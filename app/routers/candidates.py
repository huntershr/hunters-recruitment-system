from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, File, UploadFile, Form, Response
from fastapi.responses import StreamingResponse, RedirectResponse
from sqlalchemy.orm import Session, defer
from typing import List, Optional
import asyncio
import logging
import io
import csv
import json
import os
import httpx
import uuid as uuid_lib

from .. import models, schemas
from ..database import get_db, SessionLocal
from ..services.ai_evaluator import evaluate_candidate, extract_candidate_info, finalize_evaluation
from ..services.file_processor import extract_text_from_pdf, extract_text_from_docx, process_excel_candidates
from .auth import get_current_user

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = "cvs"


async def upload_cv_to_storage(file_bytes: bytes, filename: str, mime_type: str) -> "str | None":
    """Upload CV to Supabase Storage. Returns storage path (filename) or None if failed."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "pdf"
        unique_name = f"{uuid_lib.uuid4().hex}.{ext}"
        upload_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{unique_name}"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                upload_url,
                content=file_bytes,
                headers={
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": mime_type,
                    "x-upsert": "false",
                },
                timeout=30.0,
            )
        if response.status_code in (200, 201):
            return unique_name
        logger.error(f"Supabase Storage upload failed: {response.status_code} {response.text}")
        return None
    except Exception as e:
        logger.error(f"Supabase Storage upload error: {e}")
        return None


async def get_cv_signed_url(storage_path: str, expires_in: int = 3600) -> "str | None":
    """Generate a signed URL for CV download from Supabase Storage."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not storage_path:
        return None
    try:
        sign_url = f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_BUCKET}/{storage_path}"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                sign_url,
                json={"expiresIn": expires_in},
                headers={
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
        if response.status_code == 200:
            data = response.json()
            return f"{SUPABASE_URL}/storage/v1{data.get('signedURL', '')}"
        return None
    except Exception as e:
        logger.error(f"Signed URL generation error: {e}")
        return None


router = APIRouter(
    prefix="/candidates",
    tags=["Candidates"]
)

def find_best_job_id(job_title: str, db: Session) -> int:
    """
    Finds the most relevant job ID based on a job title string.
    """
    if not job_title or not job_title.strip():
        first_job = db.query(models.Job).first()
        return first_job.id if first_job else 1
    
    # 1. Try exact/case-insensitive match
    job = db.query(models.Job).filter(models.Job.job_title.ilike(job_title.strip())).first()
    if job:
        return job.id
    
    # 2. Try partial match
    job = db.query(models.Job).filter(models.Job.job_title.ilike(f"%{job_title.strip()}%")).first()
    if job:
        return job.id
    
    # 3. Default to first job if no match found
    first_job = db.query(models.Job).first()
    return first_job.id if first_job else 1

def run_evaluation_task(candidate_id: int, db: Session, application_id: Optional[int] = None):
    try:
        candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
        job = db.query(models.Job).filter(models.Job.id == candidate.job_applied).first()

        if not candidate or not job:
            logger.error(f"Evaluation failed: Candidate {candidate_id} or Job not found")
            return

        eval_result = evaluate_candidate(job, candidate)

        def list_to_str(val):
            if isinstance(val, list):
                return "\n".join([f"- {i}" for i in val])
            return str(val)

        import json
        _bd = eval_result.get("score_breakdown") or {}
        db_eval = models.Evaluation(
            candidate_id=candidate.id,
            job_id=job.id,
            application_id=application_id,
            score=eval_result.get("score", 0.0),
            score_experience=_bd.get("experience"),
            score_skills=_bd.get("skills"),
            score_education=_bd.get("education"),
            score_behavioral=_bd.get("behavioral"),
            decision=eval_result.get("decision", "Reject"),
            reason=eval_result.get("reason", "Failed to evaluate"),
            strengths=list_to_str(eval_result.get("strengths", "")),
            weaknesses=list_to_str(eval_result.get("weaknesses", "")),
            suggested_interview_questions=json.dumps(eval_result.get("suggested_interview_questions", [])),
            dimension_scores=eval_result.get("dimension_scores"),
        )

        db.add(db_eval)
        db.commit()
        logger.info(f"Finished evaluation for candidate {candidate_id}")
        
        # Send results back to Google Sheets
        from ..services.google_sheets import update_candidate_row
        update_candidate_row(candidate.email, eval_result)
    except Exception as e:
        logger.error(f"Evaluation task failed for candidate {candidate_id}: {e}")
    finally:
        db.close()

@router.post("/screen-cv")
async def screen_cv(
    file: Optional[UploadFile] = File(None),
    job_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Screen a single PDF CV against a job using real AI. Returns immediate results."""
    if file is None:
        # No file uploaded — fall back to saved CV (portal candidates only)
        if current_user.is_admin or current_user.company_id:
            raise HTTPException(status_code=400, detail="Please upload a CV file.")
        saved = db.query(models.Candidate).filter(models.Candidate.user_id == current_user.id).first()
        if not saved or not saved.cv_file_data:
            raise HTTPException(status_code=400, detail="No CV on file. Please upload your CV first.")
        if not (saved.cv_text or "").strip():
            raise HTTPException(status_code=422, detail="Saved CV could not be read. Please upload a new CV file.")
        content = saved.cv_file_data
        cv_text = saved.cv_text
        cv_mime = saved.cv_file_mime or "application/octet-stream"
    else:
        content = await file.read()
        filename = (file.filename or "").lower()

        # Resolve MIME — browser content_type can be missing or generic
        _ct = file.content_type or ""
        if _ct and _ct not in ("application/octet-stream", "binary/octet-stream"):
            cv_mime = _ct
        elif filename.endswith(".pdf"):
            cv_mime = "application/pdf"
        elif filename.endswith(".docx"):
            cv_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            cv_mime = "application/octet-stream"

        if filename.endswith(".pdf"):
            cv_text = extract_text_from_pdf(content)
        elif filename.endswith(".docx"):
            cv_text = extract_text_from_docx(content)
        else:
            raise HTTPException(status_code=400, detail="Only PDF or DOCX files are supported")

        if not cv_text or not cv_text.strip():
            raise HTTPException(
                status_code=422,
                detail="Your CV appears to be a scanned image — AI cannot read image-based PDFs. Please save your CV as a text-based PDF or upload a DOCX file instead."
            )

    # AI: extract candidate info from CV
    info = await asyncio.get_event_loop().run_in_executor(None, extract_candidate_info, cv_text)

    name  = info.get("name")  or (file.filename.rsplit(".", 1)[0].replace("_", " ").title() if file else "Candidate")
    email = info.get("email") or (f"bulk_{file.filename}@noemail.hunters" if file else f"user_{current_user.id}@noemail.hunters")
    phone = str(info.get("phone") or "")

    # Portal candidates (non-admin, non-company): always use their account email
    # and user_id so the Candidate record is reliably linked back to their User
    # record. AI extraction may return a different email or fall back to a
    # placeholder, breaking the User→Candidate lookup used by the pipeline,
    # talent pool, and GET /api/candidate/profile (which queries WHERE user_id=…).
    is_portal_candidate = not current_user.is_admin and not current_user.company_id
    if is_portal_candidate:
        email = current_user.email
        # Also fix name if AI returned nothing useful or an email string
        if not name or "@" in name:
            name = current_user.full_name or name

    if job_id is None:
        return {
            "candidate_id": None,
            "name": name,
            "email": email,
            "phone": phone,
            "score": None,
            "decision": "No job selected",
            "reason": "Select a job to get an AI evaluation score.",
        }

    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Try uploading the new file to Supabase Storage (no-op if env vars missing or no new file)
    storage_path = None
    if file is not None:
        storage_path = await upload_cv_to_storage(content, file.filename or "upload.pdf", cv_mime)

    # ── Phase 2: Candidate + Application creation ─────────────────────────────
    #
    # Portal (Type A): one Candidate per User (profile container), keyed by
    # user_id.  Subsequent applies to ANY job update the same profile row and
    # each creates a new Application row.
    #
    # Admin/company: legacy email+job lookup unchanged.  Also creates an
    # Application row so every apply is represented in the applications table.

    if is_portal_candidate:
        candidate = (
            db.query(models.Candidate)
            .filter(models.Candidate.user_id == current_user.id)
            .first()
        )
        if candidate:
            candidate.cv_text = cv_text
            candidate.cv_file_data = content
            candidate.cv_file_mime = cv_mime
            if storage_path:
                candidate.cv_url = storage_path
            candidate.name = name
            candidate.phone = phone or candidate.phone
            candidate.job_applied = job_id
            candidate.experience_years = int(info.get("experience_years") or 0) or candidate.experience_years
            candidate.education = str(info.get("education") or "") or candidate.education
            candidate.skills = str(info.get("skills") or "") or candidate.skills
            candidate.last_title = str(info.get("last_title") or "") or candidate.last_title
            candidate.last_employer = str(info.get("last_employer") or "") or candidate.last_employer
            db.commit()
            db.refresh(candidate)
        else:
            candidate = models.Candidate(
                name=name,
                email=email,
                phone=phone,
                job_applied=job_id,
                experience_years=int(info.get("experience_years") or 0),
                expected_salary="",
                education=str(info.get("education") or ""),
                skills=str(info.get("skills") or ""),
                cv_text=cv_text,
                cv_file_data=content,
                cv_file_mime=cv_mime,
                cv_url=storage_path,
                last_title=str(info.get("last_title") or ""),
                last_employer=str(info.get("last_employer") or ""),
                owner_id=job.owner_id,
                user_id=current_user.id,
            )
            db.add(candidate)
            db.commit()
            db.refresh(candidate)
    else:
        # Admin/company: upsert by email+job (legacy behavior)
        existing = (
            db.query(models.Candidate)
            .filter(models.Candidate.email == email, models.Candidate.job_applied == job_id)
            .first()
        )
        if existing:
            existing.cv_text = cv_text
            existing.cv_file_data = content
            existing.cv_file_mime = cv_mime
            if storage_path:
                existing.cv_url = storage_path
            existing.name = name
            existing.phone = phone or existing.phone
            existing.experience_years = int(info.get("experience_years") or 0) or existing.experience_years
            existing.education = str(info.get("education") or "") or existing.education
            existing.skills = str(info.get("skills") or "") or existing.skills
            existing.last_title = str(info.get("last_title") or "") or existing.last_title
            existing.last_employer = str(info.get("last_employer") or "") or existing.last_employer
            db.commit()
            db.refresh(existing)
            candidate = existing
        else:
            candidate = models.Candidate(
                name=name,
                email=email,
                phone=phone,
                job_applied=job_id,
                experience_years=int(info.get("experience_years") or 0),
                expected_salary="",
                education=str(info.get("education") or ""),
                skills=str(info.get("skills") or ""),
                cv_text=cv_text,
                cv_file_data=content,
                cv_file_mime=cv_mime,
                cv_url=storage_path,
                last_title=str(info.get("last_title") or ""),
                last_employer=str(info.get("last_employer") or ""),
                owner_id=job.owner_id,
                user_id=None,
            )
            db.add(candidate)
            db.commit()
            db.refresh(candidate)

    # Write structured JSONB fields from AI extraction (additive — preserve any existing user-edited data)
    _xp = info.get("experiences")
    _edu_h = info.get("education_history")
    _lang = info.get("languages")
    _summ = info.get("summary")
    if _xp:
        candidate.experiences = _xp
    if _edu_h:
        candidate.education_history = _edu_h
    if _lang:
        candidate.languages = _lang
    if _summ and not candidate.summary:
        candidate.summary = _summ
    db.commit()

    # Duplicate check: one Application per (candidate, job) pair
    if is_portal_candidate:
        existing_app = (
            db.query(models.Application)
            .filter(
                models.Application.candidate_id == candidate.id,
                models.Application.job_id == job_id,
            )
            .first()
        )
        if existing_app:
            # Profile was already refreshed above — just block the new Application
            raise HTTPException(
                status_code=409,
                detail="You have already applied to this job. Check My Applications for status.",
            )

    # Create a new Application row for each apply event (Phase 2)
    application = models.Application(
        job_id=job_id,
        candidate_id=candidate.id,
        stage="Applied",
        cv_file_data=candidate.cv_file_data,
        cv_file_mime=candidate.cv_file_mime,
        cv_text=candidate.cv_text,
        cv_url=storage_path or candidate.cv_url,
    )
    db.add(application)
    db.commit()
    db.refresh(application)

    # Synchronous AI evaluation — gives immediate result; fallback to pending on any failure
    result = {}
    try:
        raw = await asyncio.get_event_loop().run_in_executor(None, evaluate_candidate, job, candidate)
        result = finalize_evaluation(raw)
        _bd3 = result.get("score_breakdown") or {}
        _lstr = lambda v: "\n".join(f"- {x}" for x in v if x) if isinstance(v, list) else str(v or "")
        # Phase 2: always create a new Evaluation linked to the new Application.
        # Dual-write: candidate_id also set so existing admin UI reads (via
        # candidate_id) keep working during the transition to Phase 3-4.
        db_eval = models.Evaluation(
            application_id=application.id,
            candidate_id=candidate.id,
            job_id=job.id,
            score=result.get("score", 0.0),
            score_experience=_bd3.get("experience"),
            score_skills=_bd3.get("skills"),
            score_education=_bd3.get("education"),
            score_behavioral=_bd3.get("behavioral"),
            decision=result.get("decision", "Reject"),
            # legacy columns — populated from new bilingual fields for backward compat
            reason=result.get("summary_en") or result.get("reason", ""),
            strengths=_lstr(result.get("strengths_en") or result.get("strengths") or []),
            weaknesses=_lstr(result.get("gaps_en") or result.get("weaknesses") or []),
            suggested_interview_questions=result.get("interview_questions_en") or result.get("suggested_interview_questions") or [],
            # new bilingual columns
            summary_en=result.get("summary_en"),
            summary_ar=result.get("summary_ar"),
            strengths_ar=_lstr(result.get("strengths_ar") or []),
            gaps_en=_lstr(result.get("gaps_en") or []),
            gaps_ar=_lstr(result.get("gaps_ar") or []),
            interview_questions_ar=result.get("interview_questions_ar"),
            quick_facts=result.get("quick_facts"),
            dimension_scores=result.get("dimension_scores"),
        )
        db.add(db_eval)
        db.commit()
    except Exception as _eval_err:
        logger.error(f"AI screening failed for candidate {candidate.id}: {_eval_err}")
        try:
            db.rollback()
            _fallback = models.Evaluation(
                application_id=application.id,
                candidate_id=candidate.id,
                job_id=job.id,
                score=0.0,
                decision="pending",
                reason="AI screening could not be completed. Please re-screen manually.",
            )
            db.add(_fallback)
            db.commit()
        except Exception:
            db.rollback()
        result = {"score": 0, "decision": "pending", "reason": "AI screening failed"}

    return {
        "candidate_id": candidate.id,
        "application_id": application.id,
        "name": candidate.name,
        "email": candidate.email,
        "phone": candidate.phone,
        "score": result.get("score", 0),
        "decision": result.get("decision", "Reject"),
        "reason": result.get("reason", ""),
    }


@router.post("/", response_model=schemas.CandidateResponse)
def create_candidate(
    candidate: schemas.CandidateCreate, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Verify Job exists
    job = db.query(models.Job).filter(models.Job.id == candidate.job_applied).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    db_candidate = models.Candidate(**candidate.model_dump(), owner_id=current_user.id)
    db.add(db_candidate)
    db.commit()
    db.refresh(db_candidate)

    # Automatically trigger evaluation
    background_tasks.add_task(run_evaluation_task, db_candidate.id, SessionLocal())

    return db_candidate

@router.post("/upload")
async def upload_candidates(
    background_tasks: BackgroundTasks,
    job_id: int = 1,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    content = await file.read()
    filename = file.filename.lower()
    
    candidates_data = []
    
    if filename.endswith(".pdf") or filename.endswith(".docx"):
        text = extract_text_from_pdf(content) if filename.endswith(".pdf") else extract_text_from_docx(content)
        if text:
            from ..services.ai_evaluator import extract_candidate_info
            ai_data = extract_candidate_info(text)
            candidates_data.append({
                "name": ai_data.get("name", file.filename),
                "email": ai_data.get("email", ""),
                "phone": ai_data.get("phone", ""),
                "experience_years": ai_data.get("experience_years", 0),
                "education": ai_data.get("education", ""),
                "skills": ai_data.get("skills", ""),
                "last_title": ai_data.get("last_title", ""),
                "last_employer": ai_data.get("last_employer", ""),
                "cv_text": text
            })
        else:
            raise HTTPException(status_code=400, detail="Could not extract text from file")
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        candidates_data = process_excel_candidates(content)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format")

    imported_count = 0
    for data in candidates_data:
        # Helper to get value ignoring case and fuzzy matching
        def get_val(possible_names, default=""):
            for name in possible_names:
                # Direct check
                key = next((k for k in data.keys() if name.lower() == k.lower().strip()), None)
                if key and data.get(key) is not None and str(data.get(key)).strip() != "": 
                    return data.get(key)
                # Fuzzy check
                key = next((k for k in data.keys() if name.lower() in k.lower().strip()), None)
                if key and data.get(key) is not None and str(data.get(key)).strip() != "": 
                    return data.get(key)
            return default

        logger.info(f"Processing candidate row: {data}")
        
        # Explicitly map the user's exact headers
        name_aliases = ["full name", "name", "applicant", "candidate"]
        email_aliases = ["email adress", "email", "e-mail", "mail"]
        phone_aliases = ["phone number", "phone", "mobile", "tel"]
        pos_aliases = ["position applying for", "position", "role", "job"]
        exp_aliases = ["years of experience", "experience", "exp", "total years"]
        edu_aliases = ["education", "degree", "university", "study"]
        
        email = str(get_val(email_aliases, f"imported_{imported_count}@example.com"))
        
        # New Multi-Job Logic for Excel
        current_job_id = job_id
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            position_val = str(get_val(pos_aliases, ""))
            if position_val:
                current_job_id = find_best_job_id(position_val, db)

        def safe_int(val, default=0):
            if not val: return default
            try:
                if isinstance(val, (int, float)): return int(val)
                import re
                nums = re.findall(r'\d+', str(val))
                return int(nums[0]) if nums else default
            except:
                return default

        # Logic for name: 1. Excel mapping -> 2. Filename fallback
        extracted_name = get_val(name_aliases)
        if not extracted_name or str(extracted_name).lower() == "unknown":
            extracted_name = filename.split('.')[0].replace('_', ' ').title()

        # Handle the CV link column
        raw_cv_text = str(data.get("cv_text", ""))
        if not raw_cv_text:
            raw_cv_text = str(get_val(["submit your cv", "cv", "resume", "link"], ""))

        new_candidate = models.Candidate(
            name=str(extracted_name),
            email=email,
            phone=str(get_val(phone_aliases, "")),
            job_applied=current_job_id,
            experience_years=safe_int(get_val(exp_aliases, 0)),
            education=str(get_val(edu_aliases, "")),
            skills=str(get_val(["skills", "expertise", "technologies"], "")),
            expected_salary=str(get_val(["salary", "expected"], "")),
            cv_text=raw_cv_text,
            last_title=str(data.get("last_title", "") or ""),
            last_employer=str(data.get("last_employer", "") or ""),
            owner_id=current_user.id
        )
        db.add(new_candidate)
        db.commit()
        db.refresh(new_candidate)
        imported_count += 1
        background_tasks.add_task(run_evaluation_task, new_candidate.id, SessionLocal())

    return {"message": f"Successfully processed {imported_count} candidate(s)."}

@router.get("/", response_model=List[schemas.CandidateResponse])
def read_candidates(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    _BINARY_COLS = {"cv_file_data", "cv_file_mime"}
    if current_user.is_admin:
        cands = db.query(models.Candidate).options(
            defer(models.Candidate.cv_file_data)
        ).offset(skip).limit(limit).all()
        result = []
        for c in cands:
            d = {col.name: getattr(c, col.name) for col in c.__table__.columns
                 if col.name not in _BINARY_COLS}
            company_name = None
            if c.owner and c.owner.company:
                company_name = c.owner.company.company_name
            d["company_name"] = company_name
            result.append(d)
        return result
    else:
        candidates = db.query(models.Candidate).options(
            defer(models.Candidate.cv_file_data)
        ).filter(models.Candidate.owner_id == current_user.id).offset(skip).limit(limit).all()
        return candidates

@router.get("/talent-pool")
def get_talent_pool(
    skip: int = 0,
    limit: int = 200,
    search: str = "",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Registered candidates (user_id IS NOT NULL) visible to company users for talent browsing."""
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")
    q = db.query(models.Candidate).options(defer(models.Candidate.cv_file_data)).filter(models.Candidate.user_id.isnot(None))
    if search.strip():
        s = f"%{search.strip()}%"
        q = q.filter(
            models.Candidate.name.ilike(s) |
            models.Candidate.skills.ilike(s) |
            models.Candidate.last_title.ilike(s) |
            models.Candidate.location.ilike(s)
        )
    candidates = q.order_by(models.Candidate.id.desc()).offset(skip).limit(limit).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "last_title": c.last_title,
            "last_employer": c.last_employer,
            "experience_years": c.experience_years,
            "location": c.location,
            "skills": c.skills,
            "photo_url": c.photo_url,
            "cv_available": bool(c.cv_file_mime or c.cv_text),
        }
        for c in candidates
    ]


@router.get("/{candidate_id}/cv")
async def download_candidate_cv(candidate_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Download the candidate's CV as a PDF file."""
    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if not current_user.is_admin:
        allowed = candidate.owner_id == current_user.id
        if not allowed and current_user.company_id:
            # Allow if candidate applied to a job owned by this company
            has_company_app = (
                db.query(models.Application)
                .join(models.Job, models.Application.job_id == models.Job.id)
                .join(models.User, models.Job.owner_id == models.User.id)
                .filter(
                    models.Application.candidate_id == candidate_id,
                    models.User.company_id == current_user.company_id,
                )
                .first()
            )
            allowed = has_company_app is not None
        # Also allow talent pool: registered candidate (user_id set) visible to any company user
        if not allowed and current_user.company_id and candidate.user_id:
            allowed = True
        if not allowed:
            raise HTTPException(status_code=403, detail="Access denied")

    safe_name = _safe("".join(c for c in (candidate.name or "Candidate") if c.isalnum() or c in " _-").strip().replace(" ", "_")) or "Candidate"

    # Storage-first: new uploads have cv_url pointing to Supabase Storage
    if candidate.cv_url:
        signed_url = await get_cv_signed_url(candidate.cv_url)
        if signed_url:
            return RedirectResponse(url=signed_url)

    # Fallback: serve from BYTEA (existing candidates before Storage migration)
    if candidate.cv_file_data:
        mime = candidate.cv_file_mime or "application/pdf"
        ext = _mime_to_ext(mime)
        return Response(
            content=bytes(candidate.cv_file_data),
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_CV{ext}"', **_NO_CACHE},
        )

    # Last fallback: regenerate PDF from stored cv_text (historical records)
    if not candidate.cv_text or not candidate.cv_text.strip():
        raise HTTPException(status_code=404, detail="No CV available for this candidate")

    pdf_bytes = _build_cv_pdf(candidate)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="CV_{safe_name}.pdf"', **_NO_CACHE},
    )


_MIME_TO_EXT = {
    # Documents (CV-valid types)
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    # Images — map honestly so a JPEG doesn't download disguised as .pdf
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    # Other common uploads
    "text/plain": ".txt",
    "application/rtf": ".rtf",
    "application/vnd.oasis.opendocument.text": ".odt",
}

_NO_CACHE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
    "Content-Encoding": "identity",
}


def _mime_to_ext(mime: str) -> str:
    return _MIME_TO_EXT.get(mime or "", ".pdf")


def _safe(text: str) -> str:
    """Encode text to latin-1, replacing unsupported characters."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _build_cv_pdf(candidate) -> bytes:
    import textwrap
    from fpdf import FPDF

    import re
    raw_text = candidate.cv_text or ""
    # Normalise all whitespace: two-column PDFs extract with \r, \xa0, unicode spaces,
    # or runs of ASCII spaces as column separators. Collapse ALL to single-column flow.
    raw_text = raw_text.replace('\r\n', '\n').replace('\r', '\n')
    cleaned_lines = []
    for ln in raw_text.split("\n"):
        ln = re.sub(r'[^\S\n]+', ' ', ln).strip()  # collapse all non-newline whitespace
        cleaned_lines.append(ln)
    raw_text = "\n".join(cleaned_lines)

    cv_text = "\n".join(
        textwrap.fill(line, width=80, break_long_words=True, break_on_hyphens=True)
        if len(line) > 80 else line
        for line in raw_text.split("\n")
    )

    # ── cosmetic fixes (before latin-1 encoding) ──────────────────────────────
    # 1. Bullet characters → dash  (•, private-use , filled-circle ●)
    for bullet in ('•', '●', '', '‣', '⁃'):
        cv_text = cv_text.replace(bullet, '-')

    # 2. Extraction artifact: "Proble -\nsolving" or "Proble - solving" → "Problem-solving"
    cv_text = re.sub(r'(\w+) -\s*\n(\w+)', r'\1\2\n', cv_text)
    cv_text = re.sub(r'(\w+) - (\w)', r'\1-\2', cv_text)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    effective_w = pdf.w - pdf.l_margin - pdf.r_margin  # 210 - 15 - 15 = 180 mm

    pdf.set_fill_color(27, 42, 74)
    pdf.rect(0, 0, 210, 10, "F")
    pdf.ln(10)

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(27, 42, 74)
    pdf.multi_cell(effective_w, 10, _safe(candidate.name or "Candidate"))

    pdf.set_draw_color(201, 168, 76)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(107, 114, 128)
    meta_parts = []
    if candidate.email:         meta_parts.append(candidate.email)
    if candidate.phone:         meta_parts.append(candidate.phone)
    if candidate.last_title:    meta_parts.append(candidate.last_title)
    if candidate.last_employer: meta_parts.append(candidate.last_employer)
    if candidate.experience_years is not None:
        meta_parts.append(f"{candidate.experience_years} yrs exp")
    if meta_parts:
        pdf.multi_cell(effective_w, 5, _safe("   |   ".join(meta_parts)))
    pdf.ln(3)

    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)

    cv_text = cv_text.encode("latin-1", errors="replace").decode("latin-1")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(27, 42, 74)
    for line in cv_text.split("\n"):
        stripped = line.strip()  # strip() — remove BOTH leading and trailing whitespace
        if not stripped:
            pdf.set_x(pdf.l_margin)
            pdf.ln(2)
            continue
        safe_line = _safe(stripped)
        is_header = (stripped.isupper() and len(stripped) < 60) or stripped.endswith(":")
        pdf.set_x(pdf.l_margin)  # defensive cursor reset before every cell
        if is_header:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(27, 42, 74)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(effective_w, 5, safe_line, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(27, 42, 74)
        else:
            pdf.multi_cell(effective_w, 5, safe_line, new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(-12)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(156, 163, 175)
    pdf.cell(effective_w, 5, "Generated by Hunters AI Recruitment System  |  hunters-egypt.com", align="C")

    return bytes(pdf.output())

@router.get("/{candidate_id}", response_model=schemas.CandidateResponse)
def read_candidate(candidate_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id, models.Candidate.owner_id == current_user.id).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate

@router.delete("/{candidate_id}")
def delete_candidate(candidate_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id, models.Candidate.owner_id == current_user.id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    # Also delete associated evaluations
    db.query(models.Evaluation).filter(models.Evaluation.candidate_id == candidate_id).delete()
    
    db.delete(candidate)
    db.commit()
    return {"message": "Candidate deleted successfully"}

@router.delete("/bulk/all")
def delete_all_candidates(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    Deletes all candidates and all evaluations for the current user.
    """
    # Delete evaluations for candidates owned by current user
    candidate_ids = [c.id for c in db.query(models.Candidate).filter(models.Candidate.owner_id == current_user.id).all()]
    db.query(models.Evaluation).filter(models.Evaluation.candidate_id.in_(candidate_ids)).delete(synchronize_session=False)
    db.query(models.Candidate).filter(models.Candidate.owner_id == current_user.id).delete(synchronize_session=False)
    db.commit()
    return {"message": "All your candidates and evaluations deleted successfully"}

@router.get("/export/csv")
def export_evaluations_csv(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    Exports all candidates and their evaluation results to a CSV file.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        "ID", "Name", "Email", "Phone", "Job Title", 
        "Experience (Yrs)", "Score", "Decision", "Reason", "Strengths", "Weaknesses"
    ])
    
    candidates = db.query(models.Candidate).filter(models.Candidate.owner_id == current_user.id).all()
    for c in candidates:
        job = db.query(models.Job).filter(models.Job.id == c.job_applied).first()
        job_title = job.job_title if job else "Unknown"
        
        eval = db.query(models.Evaluation).filter(models.Evaluation.candidate_id == c.id).first()
        score = eval.score if eval else "N/A"
        decision = eval.decision if eval else "Pending"
        reason = eval.reason if eval else ""
        strengths = eval.strengths if eval else ""
        weaknesses = eval.weaknesses if eval else ""
        
        writer.writerow([
            c.id, c.name, c.email, c.phone, job_title, 
            c.experience_years, score, decision, reason, strengths, weaknesses
        ])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=candidate_evaluations.csv"}
    )


@router.post("/extract-jd", tags=["Candidates"])
async def extract_jd_text(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
):
    """Extract plain text from a PDF or DOCX job description file."""
    content = await file.read()
    filename = (file.filename or "").lower()
    if filename.endswith(".pdf"):
        text = extract_text_from_pdf(content)
    elif filename.endswith(".docx"):
        text = extract_text_from_docx(content)
    else:
        raise HTTPException(status_code=400, detail="Only PDF or DOCX files are supported")
    if not text or not text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from file")
    return {"text": text.strip()}

