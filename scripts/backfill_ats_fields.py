"""
Backfill ATS profile fields (last_title, last_employer, education, skills,
languages, certifications, summary, experiences, education_history) for
existing Candidate rows that have cv_text but are missing these fields.

Usage:
    DATABASE_URL=postgresql://... SCREEN_SERVICE_URL=https://... SCREEN_API_KEY=... \
        python scripts/backfill_ats_fields.py

Dry-run (read-only, prints what would be written):
    DRY_RUN=1 DATABASE_URL=... python scripts/backfill_ats_fields.py
"""

import os
import sys
import time
import logging
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL env var not set")

SCREEN_URL = os.environ.get(
    "SCREEN_SERVICE_URL",
    "https://hunters-screening-service-production.up.railway.app",
)
SCREEN_KEY = os.environ.get("SCREEN_API_KEY", "")
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
if not SCREEN_KEY and not DRY_RUN:
    sys.exit("ERROR: SCREEN_API_KEY env var not set")

_endpoint = (
    SCREEN_URL
    if SCREEN_URL.rstrip("/").endswith("/api/screen")
    else f"{SCREEN_URL.rstrip('/')}/api/screen"
)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def call_agent(cv_text: str) -> dict | None:
    payload = {
        "cv_text": cv_text,
        "job": {
            "title": "General",
            "description": "",
            "required_skills": [],
            "required_experience": 0,
            "required_industry": "",
            "min_education": "",
            "weights": {"title": 25, "industry": 25, "experience": 25, "skills": 25},
            "essential_skills": [],
        },
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(_endpoint, json=payload, headers={"X-API-Key": SCREEN_KEY})
        if resp.status_code == 200:
            return resp.json()
        log.warning("Agent returned HTTP %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.error("Agent request failed: %s", exc)
    return None


def main():
    # Pass 1 — fetch IDs only (no large text blobs) to avoid statement timeout
    db = Session()
    try:
        id_rows = db.execute(text(
            "SELECT id FROM candidates "
            "WHERE (last_title IS NULL OR summary IS NULL OR experiences IS NULL) "
            "AND cv_text IS NOT NULL AND cv_text != '' "
            "ORDER BY id"
        )).fetchall()
    finally:
        db.close()

    cand_ids = [r[0] for r in id_rows]
    total = len(cand_ids)
    log.info("Candidates qualifying for backfill: %d%s", total, " (DRY RUN)" if DRY_RUN else "")
    if total == 0:
        return

    updated = skipped = failed = 0

    for i, cand_id in enumerate(cand_ids, 1):
        log.info("[%d/%d] candidate_id=%d", i, total, cand_id)

        # Pass 2 — fetch cv_text for this single candidate
        db2 = Session()
        try:
            row = db2.execute(
                text("SELECT cv_text FROM candidates WHERE id = :id"),
                {"id": cand_id}
            ).fetchone()
            cv_text = row[0] if row else ""
        finally:
            db2.close()

        if not cv_text:
            log.warning("  -> cv_text empty for candidate %d; skipping", cand_id)
            skipped += 1
            continue

        resp = call_agent(cv_text)
        cp = (resp or {}).get("candidate_profile") or {}
        if not cp:
            log.warning("  -> no candidate_profile returned; skipping")
            skipped += 1
            time.sleep(1)
            continue

        fields = {}
        v = (cp.get("current_title") or "").strip()
        if v:
            fields["last_title"] = v
        v = (cp.get("last_employer") or "").strip()
        if v:
            fields["last_employer"] = v
        v = cp.get("years_experience")
        if v:
            fields["experience_years"] = int(v)
        v = (cp.get("education") or "").strip()
        if v:
            fields["education"] = v
        v = cp.get("skills")
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x)
        if v:
            fields["skills"] = str(v).strip()
        v = cp.get("certifications")
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x)
        if v:
            fields["certifications"] = str(v).strip()
        langs = cp.get("languages")
        if isinstance(langs, list) and langs:
            fields["_languages"] = langs
        v = (cp.get("summary") or "").strip()
        if v:
            fields["summary"] = v
        v = cp.get("experiences")
        if isinstance(v, list) and v:
            fields["_experiences"] = v
        v = cp.get("education_history")
        if isinstance(v, list) and v:
            fields["_education_history"] = v

        log.info(
            "  -> fields to write: %s | summary=%s experiences=%s education_history=%s",
            {k: v for k, v in fields.items() if k not in ("_languages", "_experiences", "_education_history")},
            bool(fields.get("summary")),
            "yes(%d)" % len(fields["_experiences"]) if "_experiences" in fields else "no",
            "yes(%d)" % len(fields["_education_history"]) if "_education_history" in fields else "no",
        )
        if "_languages" in fields:
            log.info("  -> languages: %s", fields["_languages"])

        if DRY_RUN:
            skipped += 1
            time.sleep(1)
            continue

        db2 = Session()
        try:
            row = db2.execute(
                text("SELECT last_title, last_employer, experience_years, education, skills, certifications, languages, summary, experiences, education_history "
                     "FROM candidates WHERE id = :id"),
                {"id": cand_id}
            ).fetchone()
            if not row:
                log.warning("  -> candidate %d not found in DB; skipping", cand_id)
                skipped += 1
                db2.close()
                time.sleep(1)
                continue

            sets = []
            params = {"id": cand_id}

            def _guard(col, val, current):
                if val and not current:
                    sets.append(f"{col} = :{col}")
                    params[col] = val

            _guard("last_title",      fields.get("last_title"),      row.last_title)
            _guard("last_employer",   fields.get("last_employer"),   row.last_employer)
            _guard("education",       fields.get("education"),       row.education)
            _guard("skills",          fields.get("skills"),          row.skills)
            _guard("certifications",  fields.get("certifications"),  row.certifications)
            _guard("summary",         fields.get("summary"),         row.summary)

            if not (row.experience_years or 0) and fields.get("experience_years"):
                sets.append("experience_years = :experience_years")
                params["experience_years"] = fields["experience_years"]

            if not row.languages and "_languages" in fields:
                import json as _json
                sets.append("languages = :languages")
                params["languages"] = _json.dumps(fields["_languages"])
            if not row.experiences and "_experiences" in fields:
                import json as _json
                sets.append("experiences = :experiences")
                params["experiences"] = _json.dumps(fields["_experiences"])
            if not row.education_history and "_education_history" in fields:
                import json as _json
                sets.append("education_history = :education_history")
                params["education_history"] = _json.dumps(fields["_education_history"])

            if sets:
                db2.execute(
                    text(f"UPDATE candidates SET {', '.join(sets)} WHERE id = :id"),
                    params
                )
                db2.commit()
                log.info("  -> saved %d field(s) to candidate %d", len(sets), cand_id)
                updated += 1
            else:
                log.info("  -> all fields already populated; skipping")
                skipped += 1
        except Exception as exc:
            log.error("  -> DB write failed for candidate %d: %s", cand_id, exc)
            db2.rollback()
            failed += 1
        finally:
            db2.close()

        time.sleep(1)

    log.info(
        "\n=== Backfill complete ===\n"
        "  Total qualifying : %d\n"
        "  Updated          : %d\n"
        "  Skipped          : %d\n"
        "  Failed           : %d",
        total, updated, skipped, failed,
    )


if __name__ == "__main__":
    main()
