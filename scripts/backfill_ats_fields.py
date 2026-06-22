"""
One-time backfill: populate ATS fields for existing portal candidates.

Targets candidates where:
  - user_id IS NOT NULL  (self-registered via portal)
  - last_title IS NULL or empty  (ATS fields not yet populated)
  - cv_text IS NOT NULL and non-empty  (something to extract from)

For each candidate, calls extract_candidate_info(cv_text) — the same
Gemini call used in the live screening flow — then writes back:
  last_title, last_employer, skills, education, summary,
  experiences, education_history, languages, experience_years

Additive only: never overwrites a field that already has a value.
Commits after each candidate. Sleeps 2 s between Gemini calls.

Run manually once:
  python scripts/backfill_ats_fields.py

Requires the venv to be active (or full path to venv python):
  .\\venv\\Scripts\\python.exe scripts\\backfill_ats_fields.py
"""

import sys
import os
import time
import logging

# Allow imports from the app package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_ats")

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app import models
from app.services.ai_evaluator import extract_candidate_info


def _str_or_none(v) -> str | None:
    s = str(v).strip() if v else ""
    return s if s else None


def backfill():
    db = SessionLocal()
    try:
        candidates = (
            db.query(models.Candidate)
            .filter(
                models.Candidate.user_id.isnot(None),
                models.Candidate.cv_text.isnot(None),
                models.Candidate.cv_text != "",
                (
                    models.Candidate.last_title.is_(None)
                    | (models.Candidate.last_title == "")
                ),
            )
            .order_by(models.Candidate.id)
            .all()
        )

        total = len(candidates)
        logger.info(f"Candidates qualifying for backfill: {total}")
        if total == 0:
            logger.info("Nothing to do.")
            return

        written = 0
        skipped = 0
        errors = 0

        for i, cand in enumerate(candidates, 1):
            logger.info(f"[{i}/{total}] cand_id={cand.id}  name={cand.name!r}")
            try:
                info = extract_candidate_info(cand.cv_text)

                changed = False
                def _set(field, value):
                    nonlocal changed
                    if value and not getattr(cand, field):
                        setattr(cand, field, value)
                        changed = True

                _set("last_title",        _str_or_none(info.get("last_title")))
                _set("last_employer",     _str_or_none(info.get("last_employer")))
                _set("skills",            _str_or_none(info.get("skills")))
                _set("education",         _str_or_none(info.get("education")))
                _set("summary",           _str_or_none(info.get("summary")))
                _set("experiences",       info.get("experiences") or None)
                _set("education_history", info.get("education_history") or None)
                _set("languages",         info.get("languages") or None)

                # experience_years: only update if currently 0 or null
                if (not cand.experience_years or cand.experience_years == 0):
                    new_exp = int(info.get("experience_years") or 0)
                    if new_exp:
                        cand.experience_years = new_exp
                        changed = True

                if changed:
                    db.commit()
                    written += 1
                    logger.info(
                        f"  WRITTEN: last_title={cand.last_title!r}  "
                        f"last_employer={cand.last_employer!r}  "
                        f"exp_yrs={cand.experience_years}"
                    )
                else:
                    db.rollback()
                    skipped += 1
                    logger.info("  SKIPPED: no new data extracted (Gemini returned empty fields)")

            except Exception as e:
                db.rollback()
                errors += 1
                logger.error(f"  ERROR for cand_id={cand.id}: {e}")

            if i < total:
                time.sleep(2)

        logger.info(
            f"\nBackfill complete — {total} candidates processed: "
            f"{written} written, {skipped} skipped, {errors} errors."
        )

    finally:
        db.close()


if __name__ == "__main__":
    backfill()
