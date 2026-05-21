from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from .database import engine, Base, SessionLocal
from .routers import jobs, candidates, evaluations, sheets, auth, public, companies, admin, profile
from .routers.auth import get_current_user
from . import models, auth_utils
from .services.ai_evaluator import generate_job_details
import logging
import os
import json
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI Recruitment System",
    description="An AI-powered recruitment system using FastAPI and Google Gemini API.",
    version="1.0.0"
)

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
    """
    db = SessionLocal()
    try:
        logging.info("Application startup - initializing database")
        # Create default admin user if it doesn't exist yet.
        # NOTE: In production (e.g. Railway) the DB may already contain users,
        # so we must not rely on "users table is empty" for seeding.
        # Schema migrations for new columns (safe on existing DBs)
        try:
            from sqlalchemy import text as _text
            db.execute(_text("ALTER TABLE candidates ADD COLUMN last_title VARCHAR"))
            db.commit()
            logging.info("Migration: added last_title column to candidates")
        except Exception:
            db.rollback()

        try:
            from sqlalchemy import text as _text
            db.execute(_text("ALTER TABLE candidates ADD COLUMN last_employer VARCHAR"))
            db.commit()
            logging.info("Migration: added last_employer column to candidates")
        except Exception:
            db.rollback()

        try:
            from sqlalchemy import text as _text
            db.execute(_text("ALTER TABLE jobs ADD COLUMN hide_salary BOOLEAN DEFAULT FALSE"))
            db.commit()
            logging.info("Migration: added hide_salary column to jobs")
        except Exception as _e:
            logging.info(f"Migration hide_salary (column likely exists): {_e}")
            db.rollback()

        # Phase 1.1 — additive schema migrations
        _phase11_columns = [
            ("candidates", "user_id",           "INTEGER REFERENCES users(id)"),
            ("candidates", "photo_url",          "TEXT"),
            ("candidates", "summary",            "TEXT"),
            ("candidates", "location",           "VARCHAR"),
            ("candidates", "experiences",        "JSONB"),
            ("candidates", "education_history",  "JSONB"),
            ("candidates", "languages",          "JSONB"),
            ("evaluations", "application_id",    "INTEGER REFERENCES applications(id)"),
        ]
        for _tbl, _col, _typedef in _phase11_columns:
            try:
                from sqlalchemy import text as _text
                db.execute(_text(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS {_col} {_typedef}"))
                db.commit()
                logging.info(f"Migration: {_tbl}.{_col} ensured")
            except Exception as _e:
                logging.info(f"Migration {_tbl}.{_col} skipped: {_e}")
                db.rollback()

        # Phase 1.4 guard table
        try:
            from sqlalchemy import text as _text
            db.execute(_text(
                "CREATE TABLE IF NOT EXISTS _schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT NOW())"
            ))
            db.commit()
            logging.info("Migration: _schema_migrations table ensured")
        except Exception as _e:
            logging.info(f"Migration _schema_migrations skipped: {_e}")
            db.rollback()

        # Phase 1.3 cleanup — reset test artifact on Ahmed's summary
        try:
            from sqlalchemy import text as _text
            db.execute(_text(
                "UPDATE candidates SET summary = NULL "
                "WHERE user_id = 12 AND summary = 'Phase 1.3 test summary'"
            ))
            db.commit()
        except Exception:
            db.rollback()

        # Phase 1.2 — email backfill (guarded, runs once)
        try:
            from sqlalchemy import text as _text
            already_run = db.execute(_text(
                "SELECT 1 FROM _schema_migrations WHERE name = 'phase_1_2_email_backfill'"
            )).fetchone()
            if already_run:
                logging.info("Phase 1.2 backfill already applied — skipping")
            else:
                logging.info("Phase 1.2: running email backfill...")
                db.execute(_text("""
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
                db.execute(_text(
                    "INSERT INTO _schema_migrations (name) VALUES ('phase_1_2_email_backfill') "
                    "ON CONFLICT (name) DO NOTHING"
                ))
                db.commit()
                logging.info("Phase 1.2: backfill committed and migration marker inserted")

                # Reporting — read-only diagnostics logged to stdout
                total = db.execute(_text("SELECT COUNT(*) FROM candidates")).scalar()
                linked = db.execute(_text("SELECT COUNT(*) FROM candidates WHERE user_id IS NOT NULL")).scalar()
                unlinked = db.execute(_text("SELECT COUNT(*) FROM candidates WHERE user_id IS NULL")).scalar()
                logging.info(f"Phase 1.2 report | total candidates: {total} | type_a (user_id set): {linked} | unlinked: {unlinked}")

                dupes = db.execute(_text(
                    "SELECT email, COUNT(*) AS cnt FROM candidates "
                    "WHERE user_id IS NULL GROUP BY email HAVING COUNT(*) > 1"
                )).fetchall()
                if dupes:
                    for row in dupes:
                        logging.info(f"Phase 1.2 report | duplicate unlinked email: {row[0]} (count: {row[1]})")
                else:
                    logging.info("Phase 1.2 report | no duplicate unlinked emails")

                degraded = db.execute(_text(
                    "SELECT id, name, email FROM candidates WHERE name LIKE '%@%'"
                )).fetchall()
                if degraded:
                    for row in degraded:
                        logging.info(f"Phase 1.2 report | degraded name row: id={row[0]} name={row[1]} email={row[2]}")
                else:
                    logging.info("Phase 1.2 report | no degraded name-as-email rows found")

        except Exception as _e:
            logging.error(f"Phase 1.2 backfill failed: {_e}")
            db.rollback()

        # Phase 1.4 — data split (DESTRUCTIVE, single transaction, guarded)
        try:
            from sqlalchemy import text as _text
            already_run = db.execute(_text(
                "SELECT 1 FROM _schema_migrations WHERE name = 'phase_1_4_data_split'"
            )).fetchone()
            if already_run:
                logging.info("Phase 1.4 data split already applied — skipping")
            else:
                logging.info("Phase 1.4: starting data split — all steps in one transaction")

                # Step 1 — Application rows for Type A (user_id IS NOT NULL)
                db.execute(_text("""
                    INSERT INTO applications (job_id, candidate_id, stage, created_at)
                    SELECT job_applied, id, 'New', NOW()
                    FROM candidates
                    WHERE user_id IS NOT NULL AND job_applied IS NOT NULL
                """))
                logging.info("Phase 1.4 step 1: Type A application rows inserted")

                # Step 2 — Application rows for Type B (user_id IS NULL, anonymous)
                db.execute(_text("""
                    INSERT INTO applications
                      (job_id, candidate_id, applicant_name, applicant_email,
                       applicant_phone, expected_salary, stage, created_at)
                    SELECT job_applied, NULL, name, email, phone, expected_salary, 'New', NOW()
                    FROM candidates
                    WHERE user_id IS NULL AND job_applied IS NOT NULL
                """))
                logging.info("Phase 1.4 step 2: Type B application rows inserted")

                # Step 3 — Reparent evaluations: set application_id
                db.execute(_text("""
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
                logging.info("Phase 1.4 step 3: evaluations reparented to applications")

                # Verify step 3 — abort entire transaction if any eval couldn't be reparented
                unlinked_check = db.execute(_text(
                    "SELECT COUNT(*) FROM evaluations WHERE application_id IS NULL"
                )).scalar()
                if unlinked_check > 0:
                    raise Exception(
                        f"Phase 1.4 abort: {unlinked_check} evaluation(s) could not be "
                        f"reparented — rolling back entire transaction"
                    )

                # Step 3b — Nullify candidate_id on Type B evaluations before deletion
                # Required: evaluations.candidate_id has FK → candidates.id (NO ACTION)
                # Setting to NULL satisfies the constraint and keeps the eval linked via application_id
                db.execute(_text("""
                    UPDATE evaluations
                    SET candidate_id = NULL
                    WHERE candidate_id IN (SELECT id FROM candidates WHERE user_id IS NULL)
                """))
                logging.info("Phase 1.4 step 3b: candidate_id nullified on Type B evaluations (FK safety)")

                # Step 4 — Delete Type B Candidate rows (FK-safe now that their evals are nulled)
                db.execute(_text("DELETE FROM candidates WHERE user_id IS NULL"))
                logging.info("Phase 1.4 step 4: Type B candidate rows deleted")

                # Step 5 — Mark migration complete
                db.execute(_text(
                    "INSERT INTO _schema_migrations (name) VALUES ('phase_1_4_data_split')"
                ))

                db.commit()
                logging.info("Phase 1.4: transaction committed successfully")

                # Post-migration report (read-only, after commit)
                total_apps    = db.execute(_text("SELECT COUNT(*) FROM applications")).scalar()
                type_a_apps   = db.execute(_text("SELECT COUNT(*) FROM applications WHERE candidate_id IS NOT NULL")).scalar()
                type_b_apps   = db.execute(_text("SELECT COUNT(*) FROM applications WHERE candidate_id IS NULL")).scalar()
                reparented    = db.execute(_text("SELECT COUNT(*) FROM evaluations WHERE application_id IS NOT NULL")).scalar()
                still_unlinked = db.execute(_text("SELECT COUNT(*) FROM evaluations WHERE application_id IS NULL")).scalar()
                remaining_cands = db.execute(_text("SELECT COUNT(*) FROM candidates")).scalar()
                orphan_apps   = db.execute(_text(
                    "SELECT COUNT(*) FROM applications a "
                    "WHERE a.candidate_id IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM candidates c WHERE c.id = a.candidate_id)"
                )).scalar()

                logging.info(f"Phase 1.4 report | a) total applications: {total_apps}")
                logging.info(f"Phase 1.4 report | b) type_a (linked to candidate): {type_a_apps}")
                logging.info(f"Phase 1.4 report | c) type_b (anonymous): {type_b_apps}")
                logging.info(f"Phase 1.4 report | d) evaluations reparented: {reparented}")
                logging.info(f"Phase 1.4 report | e) evaluations still unlinked (RED FLAG if >0): {still_unlinked}")
                logging.info(f"Phase 1.4 report | f) candidates remaining: {remaining_cands}")
                logging.info(f"Phase 1.4 report | g) orphan applications: {orphan_apps}")

                if still_unlinked > 0:
                    logging.error(f"Phase 1.4 RED FLAG: {still_unlinked} evaluations are still unlinked!")
                if orphan_apps > 0:
                    logging.error(f"Phase 1.4 RED FLAG: {orphan_apps} orphan applications found!")

        except Exception as _e:
            logging.error(f"Phase 1.4 data split FAILED — rolled back: {_e}")
            db.rollback()

        # Phase 1.5 heal — backfill user_id for any Candidate rows created with NULL
        # user_id after Phase 1.4 (e.g. portal applies before the screen-cv fix landed).
        # Guarded so it runs once; "MAX(id) GROUP BY email" keeps canonical-row selection
        # consistent with Phase 1.2.
        try:
            from sqlalchemy import text as _text
            already_run = db.execute(_text(
                "SELECT 1 FROM _schema_migrations WHERE name = 'phase_1_5_heal_user_ids'"
            )).fetchone()
            if already_run:
                logging.info("Phase 1.5 heal already applied — skipping")
            else:
                logging.info("Phase 1.5: healing Candidate rows with user_id=NULL...")
                result = db.execute(_text("""
                    UPDATE candidates c
                    SET user_id = u.id
                    FROM users u
                    WHERE LOWER(c.email) = LOWER(u.email)
                      AND c.user_id IS NULL
                      AND c.id IN (SELECT MAX(id) FROM candidates GROUP BY email)
                """))
                healed = result.rowcount
                db.execute(_text(
                    "INSERT INTO _schema_migrations (name) VALUES ('phase_1_5_heal_user_ids') "
                    "ON CONFLICT (name) DO NOTHING"
                ))
                db.commit()
                logging.info(f"Phase 1.5 heal: {healed} row(s) healed and migration marker inserted")

                still_null = db.execute(_text(
                    "SELECT COUNT(*) FROM candidates WHERE user_id IS NULL"
                )).scalar()
                logging.info(f"Phase 1.5 heal report | candidates with user_id=NULL after heal: {still_null}")
                if still_null > 0:
                    logging.warning(
                        f"Phase 1.5 heal: {still_null} candidate(s) still have user_id=NULL "
                        f"— their email does not match any User row (expected 0 post-Phase-1.4)"
                    )
        except Exception as _e:
            logging.error(f"Phase 1.5 heal FAILED: {_e}")
            db.rollback()

        # Phase 2 — drop unique constraint on evaluations.candidate_id
        # Phase 2 allows multiple Evaluations per Candidate (one per Application).
        # The old unique=True enforced one-to-one but is incompatible with re-applies.
        try:
            from sqlalchemy import text as _text
            db.execute(_text(
                "ALTER TABLE evaluations DROP CONSTRAINT IF EXISTS evaluations_candidate_id_key"
            ))
            db.commit()
            logging.info("Migration: evaluations.candidate_id unique constraint dropped (Phase 2)")
        except Exception as _e:
            logging.info(f"Migration Phase 2 unique constraint drop: {_e}")
            db.rollback()

        # Phase 3 — add cv_text to applications for Type B CV storage
        try:
            from sqlalchemy import text as _text
            db.execute(_text("ALTER TABLE applications ADD COLUMN IF NOT EXISTS cv_text TEXT"))
            db.commit()
            logging.info("Migration: applications.cv_text column ensured (Phase 3)")
        except Exception as _e:
            logging.info(f"Migration applications.cv_text skipped: {_e}")
            db.rollback()

        try:
            admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
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
                logging.info("Default admin user created (email from ADMIN_EMAIL, password from ADMIN_PASSWORD).")
            else:
                # Ensure the seeded admin remains active/admin if it already exists.
                updated = False
                if not existing_admin.is_admin:
                    existing_admin.is_admin = True
                    updated = True
                if not existing_admin.is_active:
                    existing_admin.is_active = True
                    updated = True

                # Optional, controlled password reset for recovery.
                # Set ADMIN_RESET_PASSWORD=true temporarily to force reset.
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
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model_name = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
        model = genai.GenerativeModel(model_name)
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
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model_name = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
        model = genai.GenerativeModel(model_name)
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
