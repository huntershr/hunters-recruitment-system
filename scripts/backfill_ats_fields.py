"""
Backfill ATS profile fields (last_title, last_employer, education, skills,
languages, certifications) for existing Candidate rows that have cv_text
but no last_title yet (i.e. were created before the agent extraction was
wired into the submission flow).

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
    db = Session()
    try:
        rows = db.execute(text(
            "SELECT id, cv_text FROM candidates "
            "WHERE last_title IS NULL AND cv_text IS NOT NULL AND cv_text != '' "
            "ORDER BY id"
        )).fetchall()
    finally:
        db.close()

    total = len(rows)
    log.info("Candidates qualifying for backfill: %d%s", total, " (DRY RUN)" if DRY_RUN else "")
    if total == 0:
        return

    updated = skipped = failed = 0

    for i, (cand_id, cv_text) in enumerate(rows, 1):
        log.info("[%d/%d] candidate_id=%d", i, total, cand_id)

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

        log.info("  -> fields to write: %s", {k: v for k, v in fields.items() if k != "_languages"})
        if "_languages" in fields:
            log.info("  -> languages: %s", fields["_languages"])

        if DRY_RUN:
            skipped += 1
            time.sleep(1)
            continue

        db2 = Session()
        try:
            row = db2.execute(
                text("SELECT last_title, last_employer, experience_years, education, skills, certifications, languages "
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

            if not (row.experience_years or 0) and fields.get("experience_years"):
                sets.append("experience_years = :experience_years")
                params["experience_years"] = fields["experience_years"]

            if not row.languages and "_languages" in fields:
                import json as _json
                sets.append("languages = :languages")
                params["languages"] = _json.dumps(fields["_languages"])

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
