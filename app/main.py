from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import google.generativeai as genai
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from .database import engine, Base, SessionLocal, get_db
from sqlalchemy.orm import Session
from .routers import jobs, candidates, evaluations, sheets, auth, public, companies, admin, profile, interviews, offers, voice_screening
from .routers.auth import get_current_user
from . import models, auth_utils
from .services.ai_evaluator import generate_job_details
import logging
import os
import json
import time
from pathlib import Path
from sqlalchemy import text

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Startup env diagnostic
_db_url = os.environ.get("DATABASE_URL", "NOT FOUND")
_db_safe = _db_url[:30] + "..." if len(_db_url) > 30 else _db_url
print(f"STARTUP DATABASE_URL = {_db_safe}", flush=True)

def wait_for_db(max_retries=5, delay=5):
    for attempt in range(max_retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print("Database connected successfully")
            return True
        except Exception as e:
            print(f"DB connection attempt {attempt+1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
    return False

if not wait_for_db():
    print("Could not connect to database after retries — starting anyway, endpoints will fail until DB recovers")

# Create database tables
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"Warning: Could not create tables at startup: {e}")
    print("App will start anyway and retry on first request")

# Bilingual evaluation columns migration (safe — ADD COLUMN IF NOT EXISTS)
try:
    with engine.connect() as _mc:
        for _col_sql in [
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS summary_en TEXT",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS summary_ar TEXT",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS strengths_ar TEXT",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS gaps_en TEXT",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS gaps_ar TEXT",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS interview_questions_ar JSONB",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS quick_facts JSONB",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS dimension_scores JSONB",
        ]:
            _mc.execute(text(_col_sql))
        _mc.commit()
    print("Evaluation bilingual columns: OK")
except Exception as _me:
    print(f"Warning: Evaluation column migration: {_me}")

# Department column migration (safe — ADD COLUMN IF NOT EXISTS)
try:
    with engine.connect() as _mc2:
        _mc2.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS department VARCHAR DEFAULT 'Other'"))
        _mc2.commit()
    print("Jobs department column: OK")
except Exception as _me2:
    print(f"Warning: Jobs department column migration: {_me2}")

# Supabase Storage cv_url columns (safe — ADD COLUMN IF NOT EXISTS)
try:
    with engine.connect() as _mc3:
        _mc3.execute(text("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS cv_url TEXT"))
        _mc3.execute(text("ALTER TABLE applications ADD COLUMN IF NOT EXISTS cv_url TEXT"))
        _mc3.commit()
    print("cv_url columns: OK")
except Exception as _me3:
    print(f"Warning: cv_url column migration: {_me3}")

app = FastAPI(
    title="AI Recruitment System",
    description="An AI-powered recruitment system using FastAPI and Google Gemini API.",
    version="1.0.0"
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

_gemini_model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash"))

# Add CORS middleware to allow the frontend to communicate with the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_populate_db():
    """
    Initialize database on startup and create default admin user.
    DDL migrations are batched into a single connection to avoid exhausting
    the Supabase connection pool at startup.
    """
    logging.info("Application startup - initializing database")

    # ── Fast guard: if all DDL already applied, skip the entire migration block ──
    _ddl_done = False
    try:
        with engine.connect() as _c:
            _ddl_done = bool(_c.execute(text(
                "SELECT 1 FROM _schema_migrations WHERE name = 'startup_schema_complete'"
            )).fetchone())
    except Exception:
        pass  # _schema_migrations doesn't exist yet on first deploy

    if not _ddl_done:
        # ── All DDL in one connection, one commit, savepoints per statement ──
        _DDL_STATEMENTS = [
            # Early columns (no IF NOT EXISTS in original — adding it here is safe)
            "ALTER TABLE candidates ADD COLUMN IF NOT EXISTS last_title VARCHAR",
            "ALTER TABLE candidates ADD COLUMN IF NOT EXISTS last_employer VARCHAR",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS hide_salary BOOLEAN DEFAULT FALSE",
            # Phase 1.1
            "ALTER TABLE candidates  ADD COLUMN IF NOT EXISTS user_id          INTEGER REFERENCES users(id)",
            "ALTER TABLE candidates  ADD COLUMN IF NOT EXISTS photo_url         TEXT",
            "ALTER TABLE candidates  ADD COLUMN IF NOT EXISTS summary           TEXT",
            "ALTER TABLE candidates  ADD COLUMN IF NOT EXISTS location          VARCHAR",
            "ALTER TABLE candidates  ADD COLUMN IF NOT EXISTS experiences       JSONB",
            "ALTER TABLE candidates  ADD COLUMN IF NOT EXISTS education_history JSONB",
            "ALTER TABLE candidates  ADD COLUMN IF NOT EXISTS languages         JSONB",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS application_id    INTEGER REFERENCES applications(id)",
            # Guard table (must exist before data-migration markers are written)
            "CREATE TABLE IF NOT EXISTS _schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT NOW())",
            # Phase 2
            "ALTER TABLE evaluations DROP CONSTRAINT IF EXISTS evaluations_candidate_id_key",
            # Phase 3
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS cv_text TEXT",
            # Phase 8
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS stage_updated_at TIMESTAMP",
            # Phase 9 — interviews table
            """CREATE TABLE IF NOT EXISTS interviews (
                id SERIAL PRIMARY KEY,
                application_id   INTEGER REFERENCES applications(id) NOT NULL,
                scheduled_by     INTEGER REFERENCES users(id) NOT NULL,
                interview_date   DATE NOT NULL,
                interview_time   TIME NOT NULL,
                duration_minutes INTEGER DEFAULT 60,
                location_type    TEXT NOT NULL,
                location_value   TEXT,
                interviewer_names      TEXT,
                notes_for_candidate    TEXT,
                internal_notes         TEXT,
                status      TEXT DEFAULT 'scheduled',
                created_at  TIMESTAMP DEFAULT NOW(),
                updated_at  TIMESTAMP DEFAULT NOW()
            )""",
            # Phase 9 — score breakdown
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS score_experience FLOAT DEFAULT NULL",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS score_skills     FLOAT DEFAULT NULL",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS score_education  FLOAT DEFAULT NULL",
            "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS score_behavioral FLOAT DEFAULT NULL",
            # Offers table
            """CREATE TABLE IF NOT EXISTS offers (
                id             SERIAL PRIMARY KEY,
                application_id INTEGER REFERENCES applications(id) NOT NULL,
                candidate_name VARCHAR,
                job_title      VARCHAR,
                department     VARCHAR,
                start_date     VARCHAR,
                working_hours_from VARCHAR,
                working_hours_to   VARCHAR,
                net_salary     VARCHAR,
                reporting_to   VARCHAR,
                exceptions     VARCHAR,
                status         VARCHAR DEFAULT 'pending',
                created_at     TIMESTAMP DEFAULT NOW(),
                created_by     INTEGER REFERENCES users(id)
            )""",
            # Plan management
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS plan            TEXT DEFAULT 'free'",
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMP",
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS billing_status  TEXT DEFAULT 'active'",
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_url        TEXT",
            # Phase 4 — file storage
            "ALTER TABLE candidates   ADD COLUMN IF NOT EXISTS cv_file_data BYTEA",
            "ALTER TABLE candidates   ADD COLUMN IF NOT EXISTS cv_file_mime TEXT",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS cv_file_data BYTEA",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS cv_file_mime TEXT",
            # Voice Screening
            """CREATE TABLE IF NOT EXISTS voice_screenings (
                id                     SERIAL PRIMARY KEY,
                candidate_id           INTEGER REFERENCES candidates(id),
                application_id         INTEGER REFERENCES applications(id),
                job_id                 INTEGER REFERENCES jobs(id),
                triggered_by           INTEGER REFERENCES users(id),
                attempt_number         INTEGER DEFAULT 1,
                status                 VARCHAR DEFAULT 'pending',
                experience_response    TEXT,
                availability_response  TEXT,
                job_type_suitable      VARCHAR,
                interview_confirmed    VARCHAR,
                expected_salary        TEXT,
                candidate_questions    TEXT,
                has_candidate_questions BOOLEAN DEFAULT FALSE,
                english_level          VARCHAR,
                fluency_assessment     VARCHAR,
                clarity_assessment     VARCHAR,
                experience_match       VARCHAR,
                language_notes         TEXT,
                ai_summary             TEXT,
                full_transcript        TEXT,
                created_at             TIMESTAMP DEFAULT NOW(),
                completed_at           TIMESTAMP,
                job_title_at_time      VARCHAR,
                job_type_at_time       VARCHAR,
                interview_date_at_time VARCHAR,
                interview_time_at_time VARCHAR,
                screening_token        VARCHAR(64) UNIQUE,
                token_used             BOOLEAN DEFAULT FALSE
            )""",
            "ALTER TABLE voice_screenings ADD COLUMN IF NOT EXISTS screening_token VARCHAR(64) UNIQUE",
            "ALTER TABLE voice_screenings ADD COLUMN IF NOT EXISTS token_used BOOLEAN DEFAULT FALSE",
            # Performance indexes
            "CREATE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id)",
            "CREATE INDEX IF NOT EXISTS idx_applications_company_owner ON applications(owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_applications_stage ON applications(stage)",
            "CREATE INDEX IF NOT EXISTS idx_candidates_owner ON candidates(owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_candidates_email ON candidates(email)",
        ]

        try:
            with engine.connect() as conn:
                for _stmt in _DDL_STATEMENTS:
                    try:
                        conn.execute(text("SAVEPOINT _mig"))
                        conn.execute(text(_stmt))
                        conn.execute(text("RELEASE SAVEPOINT _mig"))
                    except Exception as _e:
                        conn.execute(text("ROLLBACK TO SAVEPOINT _mig"))
                        logging.info(f"DDL skipped (already applied): {str(_e)[:120]}")
                # Mark the entire DDL batch complete
                conn.execute(text(
                    "INSERT INTO _schema_migrations (name) VALUES ('startup_schema_complete') "
                    "ON CONFLICT (name) DO NOTHING"
                ))
                conn.commit()
                logging.info("All DDL migrations applied in one connection")
        except Exception as _e:
            logging.error(f"DDL batch migration failed: {_e}")

        # ── Data migrations (each guarded by _schema_migrations, run once) ───
        db = SessionLocal()
        try:
            # Phase 1.3 cleanup — reset test artifact
            try:
                db.execute(text(
                    "UPDATE candidates SET summary = NULL "
                    "WHERE user_id = 12 AND summary = 'Phase 1.3 test summary'"
                ))
                db.commit()
            except Exception:
                db.rollback()

            # Phase 1.2 — email backfill (guarded, runs once)
            try:
                already_run = db.execute(text(
                    "SELECT 1 FROM _schema_migrations WHERE name = 'phase_1_2_email_backfill'"
                )).fetchone()
                if already_run:
                    logging.info("Phase 1.2 backfill already applied — skipping")
                else:
                    logging.info("Phase 1.2: running email backfill...")
                    db.execute(text("""
                        UPDATE candidates c
                        SET user_id = u.id
                        FROM users u
                        WHERE LOWER(c.email) = LOWER(u.email)
                          AND c.user_id IS NULL
                          AND c.id = (
                            SELECT MAX(id) FROM candidates c2
                            WHERE LOWER(c2.email) = LOWER(c.email)
                              AND c2.name NOT LIKE '%@%'
                          )
                    """))
                    db.execute(text(
                        "INSERT INTO _schema_migrations (name) VALUES ('phase_1_2_email_backfill') "
                        "ON CONFLICT (name) DO NOTHING"
                    ))
                    db.commit()
                    logging.info("Phase 1.2: backfill committed and migration marker inserted")
                    total    = db.execute(text("SELECT COUNT(*) FROM candidates")).scalar()
                    linked   = db.execute(text("SELECT COUNT(*) FROM candidates WHERE user_id IS NOT NULL")).scalar()
                    unlinked = db.execute(text("SELECT COUNT(*) FROM candidates WHERE user_id IS NULL")).scalar()
                    logging.info(f"Phase 1.2 report | total: {total} | linked: {linked} | unlinked: {unlinked}")
            except Exception as _e:
                logging.error(f"Phase 1.2 backfill failed: {_e}")
                db.rollback()

            # Phase 1.4 — data split (DESTRUCTIVE, single transaction, guarded)
            try:
                already_run = db.execute(text(
                    "SELECT 1 FROM _schema_migrations WHERE name = 'phase_1_4_data_split'"
                )).fetchone()
                if already_run:
                    logging.info("Phase 1.4 data split already applied — skipping")
                else:
                    logging.info("Phase 1.4: starting data split — all steps in one transaction")
                    db.execute(text("""
                        INSERT INTO applications (job_id, candidate_id, stage, created_at)
                        SELECT job_applied, id, 'New', NOW()
                        FROM candidates
                        WHERE user_id IS NOT NULL AND job_applied IS NOT NULL
                    """))
                    db.execute(text("""
                        INSERT INTO applications
                          (job_id, candidate_id, applicant_name, applicant_email,
                           applicant_phone, expected_salary, stage, created_at)
                        SELECT job_applied, NULL, name, email, phone, expected_salary, 'New', NOW()
                        FROM candidates
                        WHERE user_id IS NULL AND job_applied IS NOT NULL
                    """))
                    db.execute(text("""
                        UPDATE evaluations e
                        SET application_id = a.id
                        FROM applications a
                        WHERE (
                            a.candidate_id = e.candidate_id
                            OR (
                                a.applicant_email IS NOT NULL
                                AND LOWER(a.applicant_email) = LOWER(
                                    (SELECT email FROM candidates WHERE id = e.candidate_id)
                                )
                            )
                        )
                        AND a.job_id = e.job_id
                        AND e.application_id IS NULL
                    """))
                    unlinked_check = db.execute(text(
                        "SELECT COUNT(*) FROM evaluations WHERE application_id IS NULL"
                    )).scalar()
                    if unlinked_check > 0:
                        raise Exception(
                            f"Phase 1.4 abort: {unlinked_check} evaluation(s) could not be reparented"
                        )
                    db.execute(text("""
                        UPDATE evaluations
                        SET candidate_id = NULL
                        WHERE candidate_id IN (SELECT id FROM candidates WHERE user_id IS NULL)
                    """))
                    db.execute(text("DELETE FROM candidates WHERE user_id IS NULL"))
                    db.execute(text(
                        "INSERT INTO _schema_migrations (name) VALUES ('phase_1_4_data_split')"
                    ))
                    db.commit()
                    logging.info("Phase 1.4: transaction committed successfully")
            except Exception as _e:
                logging.error(f"Phase 1.4 data split FAILED — rolled back: {_e}")
                db.rollback()

            # Phase 1.5 — heal user_id NULLs (guarded, runs once)
            try:
                already_run = db.execute(text(
                    "SELECT 1 FROM _schema_migrations WHERE name = 'phase_1_5_heal_user_ids'"
                )).fetchone()
                if already_run:
                    logging.info("Phase 1.5 heal already applied — skipping")
                else:
                    logging.info("Phase 1.5: healing Candidate rows with user_id=NULL...")
                    result = db.execute(text("""
                        UPDATE candidates c
                        SET user_id = u.id
                        FROM users u
                        WHERE LOWER(c.email) = LOWER(u.email)
                          AND c.user_id IS NULL
                          AND c.id IN (SELECT MAX(id) FROM candidates GROUP BY email)
                    """))
                    healed = result.rowcount
                    db.execute(text(
                        "INSERT INTO _schema_migrations (name) VALUES ('phase_1_5_heal_user_ids') "
                        "ON CONFLICT (name) DO NOTHING"
                    ))
                    db.commit()
                    logging.info(f"Phase 1.5 heal: {healed} row(s) healed")
            except Exception as _e:
                logging.error(f"Phase 1.5 heal FAILED: {_e}")
                db.rollback()
        finally:
            db.close()

    # ── voice_screenings columns — always run so they apply even after startup_schema_complete ──
    try:
        with engine.connect() as conn:
            conn.execute(text("SAVEPOINT _vs1"))
            try:
                conn.execute(text("ALTER TABLE voice_screenings ADD COLUMN IF NOT EXISTS screening_token VARCHAR(64)"))
                conn.execute(text("RELEASE SAVEPOINT _vs1"))
            except Exception:
                conn.execute(text("ROLLBACK TO SAVEPOINT _vs1"))
            conn.execute(text("SAVEPOINT _vs2"))
            try:
                conn.execute(text("ALTER TABLE voice_screenings ADD COLUMN IF NOT EXISTS token_used BOOLEAN DEFAULT FALSE"))
                conn.execute(text("RELEASE SAVEPOINT _vs2"))
            except Exception:
                conn.execute(text("ROLLBACK TO SAVEPOINT _vs2"))
            conn.commit()
            logging.info("voice_screenings column migrations ensured")
    except Exception as _e:
        logging.error(f"voice_screenings column migrations failed: {_e}")

    # ── agent_screenings table — always run, isolated shadow table ──
    try:
        with engine.connect() as conn:
            conn.execute(text("SAVEPOINT _as1"))
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS agent_screenings (
                        id                  SERIAL PRIMARY KEY,
                        application_id      INTEGER REFERENCES applications(id),
                        job_id              INTEGER REFERENCES jobs(id),
                        agent_score         INTEGER,
                        agent_recommendation VARCHAR,
                        dimension_scores    JSONB,
                        strengths           JSONB,
                        concerns            JSONB,
                        semantic_match      FLOAT,
                        screened_at         TIMESTAMP DEFAULT NOW(),
                        screened_by         INTEGER REFERENCES users(id)
                    )
                """))
                conn.execute(text("RELEASE SAVEPOINT _as1"))
            except Exception:
                conn.execute(text("ROLLBACK TO SAVEPOINT _as1"))
            conn.execute(text("SAVEPOINT _as2"))
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_screenings_app "
                    "ON agent_screenings(application_id)"
                ))
                conn.execute(text("RELEASE SAVEPOINT _as2"))
            except Exception:
                conn.execute(text("ROLLBACK TO SAVEPOINT _as2"))
            conn.execute(text("SAVEPOINT _as3"))
            try:
                conn.execute(text(
                    "ALTER TABLE agent_screenings ADD COLUMN IF NOT EXISTS matched_skills JSONB"
                ))
                conn.execute(text(
                    "ALTER TABLE agent_screenings ADD COLUMN IF NOT EXISTS missed_skills JSONB"
                ))
                conn.execute(text("RELEASE SAVEPOINT _as3"))
            except Exception:
                conn.execute(text("ROLLBACK TO SAVEPOINT _as3"))
            conn.commit()
            logging.info("agent_screenings table ensured")
    except Exception as _e:
        logging.error(f"agent_screenings migration failed: {_e}")

    # ── Password reset columns (additive, runs every startup — safe no-ops after first run) ──
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMP"))
            conn.commit()
    except Exception:
        pass

    # ── Job status column (additive, runs every startup) ──
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS status VARCHAR"))
            conn.commit()
    except Exception:
        pass

    # ── Plan/invite columns (additive, runs every startup — safe no-ops after first run) ──
    try:
        with engine.connect() as conn:
            for _stmt in [
                "ALTER TABLE companies ADD COLUMN IF NOT EXISTS selected_plan VARCHAR DEFAULT 'free'",
                "ALTER TABLE companies ADD COLUMN IF NOT EXISTS billing_preference VARCHAR DEFAULT 'monthly'",
                "ALTER TABLE companies ADD COLUMN IF NOT EXISTS contact_phone VARCHAR",
                "ALTER TABLE companies ADD COLUMN IF NOT EXISTS preferred_contact VARCHAR DEFAULT 'whatsapp'",
                "ALTER TABLE companies ADD COLUMN IF NOT EXISTS invitations_used_this_month INTEGER DEFAULT 0",
                "ALTER TABLE companies ADD COLUMN IF NOT EXISTS invitations_reset_date TIMESTAMP",
            ]:
                try:
                    conn.execute(text("SAVEPOINT _mig"))
                    conn.execute(text(_stmt))
                    conn.execute(text("RELEASE SAVEPOINT _mig"))
                except Exception:
                    conn.execute(text("ROLLBACK TO SAVEPOINT _mig"))
            conn.commit()
            logging.info("Plan/invite column migrations ensured")
    except Exception as _e:
        logging.error(f"Plan/invite column migrations failed: {_e}")

    # ── CASCADE FK migration (one-time, guarded) ─────────────────────────────
    try:
        with engine.connect() as conn:
            already = conn.execute(text(
                "SELECT 1 FROM _schema_migrations WHERE name = 'cascade_fk_v1'"
            )).first()
            if not already:
                conn.execute(text("""
                DO $$
                DECLARE v TEXT;
                BEGIN
                    SELECT tc.constraint_name INTO v FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='applications' AND kcu.column_name='job_id';
                    IF v IS NOT NULL THEN EXECUTE format('ALTER TABLE applications DROP CONSTRAINT %I',v); END IF;
                    ALTER TABLE applications ADD CONSTRAINT applications_job_id_fkey FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE;

                    SELECT tc.constraint_name INTO v FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='evaluations' AND kcu.column_name='job_id';
                    IF v IS NOT NULL THEN EXECUTE format('ALTER TABLE evaluations DROP CONSTRAINT %I',v); END IF;
                    ALTER TABLE evaluations ADD CONSTRAINT evaluations_job_id_fkey FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE;

                    SELECT tc.constraint_name INTO v FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='voice_screenings' AND kcu.column_name='job_id';
                    IF v IS NOT NULL THEN EXECUTE format('ALTER TABLE voice_screenings DROP CONSTRAINT %I',v); END IF;
                    ALTER TABLE voice_screenings ADD CONSTRAINT voice_screenings_job_id_fkey FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE;

                    SELECT tc.constraint_name INTO v FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='candidates' AND kcu.column_name='job_applied';
                    IF v IS NOT NULL THEN EXECUTE format('ALTER TABLE candidates DROP CONSTRAINT %I',v); END IF;
                    ALTER TABLE candidates ADD CONSTRAINT candidates_job_applied_fkey FOREIGN KEY (job_applied) REFERENCES jobs(id) ON DELETE SET NULL;

                    SELECT tc.constraint_name INTO v FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='evaluations' AND kcu.column_name='application_id';
                    IF v IS NOT NULL THEN EXECUTE format('ALTER TABLE evaluations DROP CONSTRAINT %I',v); END IF;
                    ALTER TABLE evaluations ADD CONSTRAINT evaluations_application_id_fkey FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE;

                    SELECT tc.constraint_name INTO v FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='interviews' AND kcu.column_name='application_id';
                    IF v IS NOT NULL THEN EXECUTE format('ALTER TABLE interviews DROP CONSTRAINT %I',v); END IF;
                    ALTER TABLE interviews ADD CONSTRAINT interviews_application_id_fkey FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE;

                    SELECT tc.constraint_name INTO v FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='voice_screenings' AND kcu.column_name='application_id';
                    IF v IS NOT NULL THEN EXECUTE format('ALTER TABLE voice_screenings DROP CONSTRAINT %I',v); END IF;
                    ALTER TABLE voice_screenings ADD CONSTRAINT voice_screenings_application_id_fkey FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE;

                    SELECT tc.constraint_name INTO v FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='offers' AND kcu.column_name='application_id';
                    IF v IS NOT NULL THEN EXECUTE format('ALTER TABLE offers DROP CONSTRAINT %I',v); END IF;
                    ALTER TABLE offers ADD CONSTRAINT offers_application_id_fkey FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE;
                END$$;
                """))
                conn.execute(text("INSERT INTO _schema_migrations (name) VALUES ('cascade_fk_v1')"))
                conn.commit()
                logging.info("CASCADE FK migration applied (cascade_fk_v1)")
            else:
                logging.info("CASCADE FK migration already applied — skipping")
    except Exception as _e:
        logging.error(f"CASCADE FK migration failed: {_e}")

    # ── Admin user seed (always runs — fast single SELECT) ────────────────────
    db = SessionLocal()
    try:
        admin_email    = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123")

        existing_admin = db.query(models.User).filter(models.User.email == admin_email).first()
        if existing_admin is None:
            logging.info(f"Creating default admin user: {admin_email}")
            hashed_pw = auth_utils.get_password_hash(admin_password)
            admin = models.User(
                email=admin_email,
                hashed_password=hashed_pw,
                full_name="Administrator",
                is_admin=True,
                is_active=True,
            )
            db.add(admin)
            db.commit()
            logging.info("Default admin user created.")
        else:
            updated = False
            if not existing_admin.is_admin:
                existing_admin.is_admin = True
                updated = True
            if not existing_admin.is_active:
                existing_admin.is_active = True
                updated = True
            reset_flag = os.getenv("ADMIN_RESET_PASSWORD", "").strip().lower() in {"1", "true", "yes", "y", "on"}
            if reset_flag:
                existing_admin.hashed_password = auth_utils.get_password_hash(admin_password)
                updated = True
                logging.warning(f"ADMIN_RESET_PASSWORD is enabled. Resetting password for: {admin_email}")
            if updated:
                db.commit()
                logging.info(f"Updated existing admin flags for: {admin_email}")
    except Exception as e:
        logging.warning(f"Could not create default admin user: {e}")
        db.rollback()
    finally:
        db.close()

app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(public.router)
app.include_router(admin.router)
app.include_router(profile.router)

app.include_router(jobs.router)
app.include_router(candidates.router)
app.include_router(evaluations.router)
app.include_router(sheets.router)
app.include_router(interviews.admin_router)
app.include_router(interviews.candidate_router)
app.include_router(offers.router)
app.include_router(voice_screening.router)

class GenerateJobRequest(BaseModel):
    job_title: str
    industry_background: str
    additional_context: str = ""

class GenerateCVRequest(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    experiences: list = []
    skills: list = []
    education: str = ""
    industry: str = ""

@app.post("/api/ai/generate-job")
async def generate_job_ai(
    request: GenerateJobRequest,
    current_user: models.User = Depends(get_current_user)
):
    if not os.getenv("GEMINI_API_KEY", ""):
        raise HTTPException(status_code=503, detail="AI service not configured")
    try:
        result = generate_job_details(
            job_title=request.job_title,
            industry_background=request.industry_background,
            additional_context=request.additional_context or "",
        )
        return JSONResponse(content={
            "job_brief":         result.get("job_brief", ""),
            "required_skills":   result.get("required_skills", ""),
            "nice_to_have":      result.get("nice_to_have", ""),
            "behavioral_skills": result.get("behavioral_skills", ""),
        })
    except Exception as e:
        logging.error("generate_job_ai endpoint error: %s", e)
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

@app.post("/api/ai/generate-cv")
async def generate_cv_ai(
    request: GenerateCVRequest,
    current_user: models.User = Depends(get_current_user)
):
    if not os.getenv("GEMINI_API_KEY", ""):
        raise HTTPException(status_code=503, detail="AI service not configured")
    try:
        model = _gemini_model
        prompt = f"""You are a professional CV writer at Hunters for HR Transformation & Execution.

Create a polished, professional CV. Return ONLY valid JSON with no markdown fences:
{{
  "name": "{request.name}",
  "headline": "professional headline based on their experience",
  "summary": "3-4 sentence professional summary",
  "experience": [{{"title":"job title","company":"employer","duration":"duration","achievements":["achievement 1","achievement 2","achievement 3"]}}],
  "skills": ["skill1","skill2","skill3"],
  "education": "education details",
  "languages": ["Arabic","English"],
  "certifications": []
}}

Candidate: {request.name}
Email: {request.email}
Phone: {request.phone}
Experiences: {json.dumps(request.experiences)}
Skills: {json.dumps(request.skills)}
Education: {request.education}
Industry: {request.industry}"""
        response = model.generate_content(prompt)
        text = response.text.strip()
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
        cv_data = json.loads(text)
        return JSONResponse(content=cv_data)
    except Exception as e:
        logging.error("generate_cv_ai error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/extract-cv")
async def extract_cv_ai(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user)
):
    if not os.getenv("GEMINI_API_KEY", ""):
        raise HTTPException(status_code=503, detail="AI service not configured")
    try:
        contents = await file.read()
        filename = file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        text = ""
        if ext == "pdf":
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(contents))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif ext == "docx":
            import io
            from docx import Document
            doc = Document(io.BytesIO(contents))
            text = "\n".join(p.text for p in doc.paragraphs)
        else:
            text = contents.decode("utf-8", errors="ignore")
        if not text.strip():
            raise HTTPException(status_code=400, detail="Could not extract text from file")
        model = _gemini_model
        prompt = f"""Extract structured profile data from this CV/resume text.
Return ONLY valid JSON with no markdown fences:
{{
  "name": "full name",
  "email": "email address or empty string",
  "phone": "phone number or empty string",
  "industry": "main industry or field",
  "education": "education summary as one string",
  "skills": ["skill1", "skill2"],
  "experiences": [
    {{"title": "job title", "company": "company name", "duration": "e.g. 2020-2023"}}
  ]
}}

CV Text:
{text[:8000]}"""
        response = model.generate_content(prompt)
        raw = response.text.strip()
        if "```" in raw:
            start = raw.find("{"); end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]
        profile_data = json.loads(raw)
        return JSONResponse(content=profile_data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI returned invalid data format")
    except Exception as e:
        logging.error("extract_cv_ai error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/api/diagnostic")
def diagnostic(db: Session = Depends(get_db)):
    url = os.environ.get("DATABASE_URL", "NOT FOUND")
    counts = {}
    for table in ["users", "companies", "jobs", "candidates", "applications", "evaluations"]:
        try:
            result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            counts[table] = result.scalar()
        except Exception:
            counts[table] = "ERROR"
    return {
        "raw_database_url": url[:30] + "..." if url != "NOT FOUND" else "NOT FOUND",
        "counts": counts
    }












@app.get("/website", include_in_schema=False)
async def website():
    return FileResponse("frontend/website.html")


@app.middleware("http")
async def domain_router(request: Request, call_next):
    host = request.headers.get("host", "")
    if "www.hunters-egypt.com" in host and request.url.path == "/":
        return FileResponse("frontend/website.html")
    return await call_next(request)


# Mount the frontend directory to serve HTML/JS/CSS
try:
    frontend_path = Path(__file__).parent.parent / "frontend"
    if frontend_path.exists():
        # Serve landing.html as the root entry point
        @app.get("/")
        async def root():
            return FileResponse(str(frontend_path / "landing.html"))

        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
        logging.info(f"Frontend mounted from: {frontend_path}")
    else:
        logging.warning(f"Frontend directory not found at: {frontend_path}")
except Exception as e:
    logging.warning(f"Failed to mount frontend: {e}")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
