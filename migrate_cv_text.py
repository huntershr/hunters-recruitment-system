#!/usr/bin/env python3
"""
migrate_cv_text.py — Whitespace normalization for stored cv_text fields.

Applies the same normalization used by _build_cv_pdf() to every cv_text
value stored in the database, so the rendered PDF matches what the
normalizer produces without having to re-upload the original file.

WHAT IT CHANGES
    - \r\n and bare \r  → \n
    - Non-breaking spaces, tabs, unicode whitespace  → ASCII space
    - Runs of 2+ spaces  → single space
    - Leading/trailing whitespace on every line  → stripped

WHAT IT DOES NOT CHANGE
    - NULL cv_text values (skipped)
    - cv_text where normalized == original (skipped, zero writes)
    - Any other column — only cv_text is touched

USAGE
    # Step 1 — DRY RUN (default): review before/after, no DB writes
    railway run python migrate_cv_text.py

    # Step 2 — Inspect a specific candidate by name or id
    railway run python migrate_cv_text.py --show "Sara"
    railway run python migrate_cv_text.py --show 9

    # Step 3 — Commit after you are satisfied with the dry-run output
    railway run python migrate_cv_text.py --commit

Local testing (uses SQLite fallback):
    python migrate_cv_text.py
    python migrate_cv_text.py --commit
"""

import os
import re
import sys
import argparse
import textwrap

# -- database setup (reuses app/database.py logic) ----------------------------
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./recruitment.db")
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# -- normalisation (mirrors _build_cv_pdf preprocessing) ---------------------

def normalize(raw: str) -> str:
    """Whitespace-only normalisation — identical to _build_cv_pdf's step 1."""
    if raw is None:
        return raw
    text = raw.replace('\r\n', '\n').replace('\r', '\n')
    cleaned = []
    for ln in text.split('\n'):
        ln = re.sub(r'[^\S\n]+', ' ', ln).strip()
        cleaned.append(ln)
    return '\n'.join(cleaned)

# -- helpers ------------------------------------------------------------------

def excerpt(s: str, chars: int = 300) -> str:
    """Return a readable excerpt of cv_text for display."""
    if s is None:
        return '<NULL>'
    snippet = s[:chars]
    return repr(snippet) + (f'  … ({len(s)} chars total)' if len(s) > chars else '')

def count_diff_lines(before: str, after: str) -> int:
    b = before.splitlines() if before else []
    a = after.splitlines() if after else []
    changed = sum(1 for x, y in zip(b, a) if x != y)
    changed += abs(len(b) - len(a))
    return changed

def wrap(s: str, width: int = 100) -> str:
    """Wrap long repr lines for terminal readability."""
    return '\n    '.join(textwrap.wrap(s, width))

# -- main ---------------------------------------------------------------------

def run(commit: bool, show_candidate: str | None):
    with engine.connect() as conn:

        # -- show specific candidate ------------------------------------------
        if show_candidate is not None:
            try:
                cid = int(show_candidate)
                where = "WHERE id = :val"
            except ValueError:
                where = "WHERE name ILIKE :val"
                show_candidate = f"%{show_candidate}%"

            rows = conn.execute(
                text(f"SELECT id, name, cv_text FROM candidates {where} LIMIT 5"),
                {"val": show_candidate}
            ).fetchall()

            if not rows:
                # Also check applications table
                try:
                    cid = int(show_candidate.strip('%'))
                    app_where = "WHERE id = :val"
                except (ValueError, AttributeError):
                    app_where = "WHERE applicant_name ILIKE :val"
                rows2 = conn.execute(
                    text(f"SELECT id, applicant_name, cv_text FROM applications {app_where} LIMIT 5"),
                    {"val": show_candidate}
                ).fetchall()
                if rows2:
                    print("Found in applications table:\n")
                    for r in rows2:
                        print(f"  application id={r[0]}  name={r[1]}")
                        print(f"  STORED cv_text (first 400 chars):")
                        print(f"    {wrap(excerpt(r[2], 400))}\n")
                else:
                    print("No matching candidate or application found.")
                return

            for r in rows:
                before = r[2]
                after  = normalize(before)
                print(f"\n{'='*70}")
                print(f"candidates id={r[0]}  name={r[1]}")
                print(f"{'='*70}")
                print(f"\nSTORED cv_text (first 400 chars):\n    {wrap(excerpt(before, 400))}")
                if before == after:
                    print("\nOK Already clean — normalization would make no change.")
                else:
                    print(f"\nAFTER normalization (first 400 chars):\n    {wrap(excerpt(after, 400))}")
                    print(f"\nLines that would change: {count_diff_lines(before, after)}")
            return

        # -- load all candidates with cv_text ---------------------------------
        print("Loading candidates with cv_text …")
        cand_rows = conn.execute(
            text("SELECT id, name, cv_text FROM candidates WHERE cv_text IS NOT NULL AND cv_text != ''")
        ).fetchall()

        print("Loading applications with cv_text …")
        try:
            app_rows = conn.execute(
                text("SELECT id, applicant_name, cv_text FROM applications WHERE cv_text IS NOT NULL AND cv_text != ''")
            ).fetchall()
        except Exception:
            print("  (applications.cv_text column not present — skipping applications table)")
            app_rows = []

        # -- compute which rows would change ----------------------------------
        cand_changes = [(r[0], r[1], r[2], normalize(r[2])) for r in cand_rows if normalize(r[2]) != r[2]]
        app_changes  = [(r[0], r[1], r[2], normalize(r[2])) for r in app_rows  if normalize(r[2]) != r[2]]

        total_cands = len(cand_rows)
        total_apps  = len(app_rows)

        print(f"\n{'-'*70}")
        print(f"  candidates:  {total_cands} with cv_text,  {len(cand_changes)} would change")
        print(f"  applications:{total_apps} with cv_text,  {len(app_changes)} would change")
        print(f"{'-'*70}\n")

        if not cand_changes and not app_changes:
            print("OK Nothing to do — all cv_text values are already normalized.")
            return

        # -- dry-run preview: first 5 of each table ----------------------------
        def show_preview(label, changes, limit=5):
            print(f"\n{'='*70}")
            print(f"  DRY-RUN PREVIEW — {label}  (showing {min(limit, len(changes))} of {len(changes)})")
            print(f"{'='*70}")
            for row in changes[:limit]:
                rid, name, before, after = row
                print(f"\n  id={rid}  name={name}")
                print(f"  BEFORE (first 300 chars):\n    {wrap(excerpt(before, 300))}")
                print(f"  AFTER  (first 300 chars):\n    {wrap(excerpt(after,  300))}")
                print(f"  Lines changed: {count_diff_lines(before, after)}")
                print()

        show_preview("candidates", cand_changes)
        show_preview("applications", app_changes)

        if not commit:
            print(f"\n{'-'*70}")
            print("  DRY RUN — no changes written.")
            print("  Review the output above, then run with --commit to apply.")
            print(f"{'-'*70}\n")
            return

        # -- commit mode -------------------------------------------------------
        print(f"\n{'!'*70}")
        print("  COMMIT MODE — about to write to the database.")
        ans = input("  Type YES to proceed, anything else to abort: ").strip()
        if ans != "YES":
            print("  Aborted.")
            return

        trans = conn.begin()
        try:
            cand_updated = 0
            for rid, name, before, after in cand_changes:
                conn.execute(
                    text("UPDATE candidates SET cv_text = :after WHERE id = :id"),
                    {"after": after, "id": rid}
                )
                cand_updated += 1

            app_updated = 0
            for rid, name, before, after in app_changes:
                conn.execute(
                    text("UPDATE applications SET cv_text = :after WHERE id = :id"),
                    {"after": after, "id": rid}
                )
                app_updated += 1

            trans.commit()
            print(f"\nOK Committed: {cand_updated} candidates, {app_updated} applications updated.")

        except Exception as exc:
            trans.rollback()
            print(f"\nFAIL Error — transaction rolled back: {exc}")
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normalize cv_text whitespace in the database.")
    parser.add_argument("--commit", action="store_true",
                        help="Write changes to DB (interactive confirmation required). Default is dry-run.")
    parser.add_argument("--show", metavar="NAME_OR_ID",
                        help="Show raw cv_text for a specific candidate (by name substring or id). No DB writes.")
    args = parser.parse_args()

    run(commit=args.commit, show_candidate=args.show)
