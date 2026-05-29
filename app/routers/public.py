from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List
import logging
import json

from .. import models, schemas, database
from ..services.file_processor import extract_text_from_file
from ..services.ai_evaluator import extract_candidate_info, evaluate_candidate, finalize_evaluation

logger = logging.getLogger(__name__)


def run_evaluation_task_for_application(application_id: int, cv_text: str, db: Session):
    """Background evaluation for Type B (anonymous) applications — no Candidate row."""
    try:
        application = db.query(models.Application).filter(
            models.Application.id == application_id
        ).first()
        if not application:
            logger.error(f"Application {application_id} not found for evaluation")
            return

        job = db.query(models.Job).filter(models.Job.id == application.job_id).first()
        if not job:
            logger.error(f"Job {application.job_id} not found for application {application_id}")
            return

        info = extract_candidate_info(cv_text)

        import types
        applicant = types.SimpleNamespace(
            name=application.applicant_name or "",
            experience_years=int(info.get("experience_years") or 0),
            skills=str(info.get("skills") or ""),
            education=str(info.get("education") or ""),
            cv_text=cv_text,
        )

        raw = evaluate_candidate(job, applicant)
        result = finalize_evaluation(raw)

        def _list_to_str(val):
            if isinstance(val, list):
                return "\n".join(f"- {i}" for i in val)
            return str(val)

        _bd = result.get("score_breakdown") or {}
        db_eval = models.Evaluation(
            application_id=application.id,
            candidate_id=None,
            job_id=job.id,
            score=result.get("score", 0.0),
            score_experience=_bd.get("experience"),
            score_skills=_bd.get("skills"),
            score_education=_bd.get("education"),
            score_behavioral=_bd.get("behavioral"),
            decision=result.get("decision", "Reject"),
            reason=result.get("reason", "Failed to evaluate"),
            strengths=_list_to_str(result.get("strengths", "")),
            weaknesses=_list_to_str(result.get("weaknesses", "")),
            suggested_interview_questions=json.dumps(result.get("suggested_interview_questions", [])),
        )
        db.add(db_eval)
        db.commit()
        logger.info(f"Finished evaluation for application {application_id}")
    except Exception as e:
        logger.error(f"Evaluation task failed for application {application_id}: {e}")
    finally:
        db.close()

router = APIRouter(
    prefix="/public",
    tags=["Public"]
)

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/job/{job_id}")
def get_public_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    HUNTERS_LOGO = "/hunters-logo-blue.jpeg"
    company_name = None
    company_id = None
    company_logo_url = None
    if job.owner:
        if job.owner.is_admin:
            company_name = "Hunters for HR Solutions"
            company_logo_url = HUNTERS_LOGO
        elif job.owner.company_id:
            company = db.query(models.Company).filter(models.Company.id == job.owner.company_id).first()
            if company:
                company_name = company.company_name
                company_id = company.id
                company_logo_url = company.logo_url or None

    hide_salary = bool(job.hide_salary)
    return {
        "id": job.id,
        "job_title": job.job_title,
        "job_description": job.job_description or "",
        "job_location": job.job_location or "",
        "min_experience": job.min_experience,
        "required_skills": job.required_skills or "",
        "nice_to_have_skills": job.nice_to_have_skills,
        "behavioral_skills": job.behavioral_skills,
        "education_level": job.education_level or "",
        "salary_range": "" if hide_salary else (job.salary_range or ""),
        "industry_experience": job.industry_experience,
        "weight_experience": job.weight_experience,
        "weight_skills": job.weight_skills,
        "weight_education": job.weight_education,
        "weight_behavioral": job.weight_behavioral,
        "is_approved": job.is_approved,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "salary_min": None,
        "salary_max": None,
        "employment_type": job.education_level,
        "hide_salary": hide_salary,
        "company_name": company_name,
        "company_id": company_id,
        "company_logo_url": company_logo_url,
    }

@router.post("/apply/{job_id}")
async def public_apply(
    job_id: int,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    expected_salary: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    content = await file.read()
    cv_text = extract_text_from_file(file.filename, content)

    # Resolve MIME type for original file storage
    _fname = (file.filename or "").lower()
    _ct = file.content_type or ""
    if _ct and _ct not in ("application/octet-stream", "binary/octet-stream"):
        cv_mime = _ct
    elif _fname.endswith(".pdf"):
        cv_mime = "application/pdf"
    elif _fname.endswith(".docx"):
        cv_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        cv_mime = "application/octet-stream"

    # Duplicate check: one Type B Application per (email, job) pair
    existing_app = (
        db.query(models.Application)
        .filter(
            models.Application.applicant_email.ilike(email),
            models.Application.job_id == job_id,
            models.Application.candidate_id.is_(None),
        )
        .first()
    )
    if existing_app:
        raise HTTPException(
            status_code=409,
            detail="An application with this email already exists for this job.",
        )

    # Phase 2 Type B: create Application directly — no Candidate row.
    # Email match to an existing User does NOT auto-link (T5); explicit
    # auth is required for Type A.
    application = models.Application(
        job_id=job_id,
        candidate_id=None,
        applicant_name=name,
        applicant_email=email,
        applicant_phone=phone,
        expected_salary=expected_salary,
        stage="Applied",
        cv_text=cv_text,
        cv_file_data=content,
        cv_file_mime=cv_mime,
    )
    db.add(application)
    db.commit()
    db.refresh(application)

    from ..database import SessionLocal
    background_tasks.add_task(
        run_evaluation_task_for_application,
        application.id, cv_text, SessionLocal()
    )

    return {
        "message": "Application submitted successfully",
        "application_id": application.id,
        "job_id": job_id,
    }

@router.get("/evaluation/{candidate_id}")
def get_candidate_evaluation(candidate_id: int, db: Session = Depends(get_db)):
    """
    Public endpoint to fetch candidate evaluation score.
    Called by candidates to see their results after applying.
    """
    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    evaluation = db.query(models.Evaluation).filter(models.Evaluation.candidate_id == candidate_id).first()
    if not evaluation:
        return {
            "candidate_id": candidate_id,
            "name": candidate.name,
            "status": "pending",
            "message": "Your application is being evaluated. Please check back shortly."
        }
    
    pct = float(evaluation.score or 0)
    return {
        "candidate_id": candidate_id,
        "name": candidate.name,
        "status": "completed",
        "score": pct,
        "overall_score": int(round(min(100.0, max(0.0, pct)))),
        "decision": evaluation.decision,
        "reason": evaluation.reason,
        "strengths": evaluation.strengths,
        "weaknesses": evaluation.weaknesses,
    }

@router.get("/company/{company_id}", response_model=schemas.CompanyResponse)
def get_public_company(company_id: int, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(
        models.Company.id == company_id,
        models.Company.is_approved == True
    ).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found or not approved")
    return company

@router.get("/company/{company_id}/jobs", response_model=List[schemas.JobResponse])
def get_public_company_jobs(company_id: int, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(
        models.Company.id == company_id,
        models.Company.is_approved == True
    ).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found or not approved")
    users = db.query(models.User).filter(models.User.company_id == company_id).all()
    user_ids = [u.id for u in users]
    jobs = db.query(models.Job).filter(
        models.Job.owner_id.in_(user_ids),
        models.Job.is_approved == True
    ).all()
    return jobs

@router.get("/jobs")
def get_public_jobs(db: Session = Depends(get_db)):
    """
    Get all approved public job postings with company info.
    Only shows jobs from approved companies.
    """
    import traceback as _tb
    print("=== PUBLIC JOBS ENDPOINT CALLED ===")
    try:
        approved_companies = db.query(models.Company).filter(
            models.Company.is_approved == True
        ).all()
        approved_company_ids = [c.id for c in approved_companies]
        company_map = {c.id: c.company_name for c in approved_companies}
        logo_map    = {c.id: c.logo_url for c in approved_companies}
        print(f"=== APPROVED COMPANIES: {len(approved_company_ids)} ===")

        # Build filter: approved jobs from admin users OR approved companies
        filters = [models.Job.is_approved == True]
        if approved_company_ids:
            filters.append(or_(
                models.User.is_admin == True,
                models.User.company_id.in_(approved_company_ids)
            ))
        else:
            filters.append(models.User.is_admin == True)

        try:
            jobs = db.query(models.Job).join(
                models.User, models.Job.owner_id == models.User.id
            ).filter(*filters).all()
            print(f"=== JOBS QUERY OK: {len(jobs)} rows ===")
        except Exception as qe:
            print(f"=== JOBS QUERY FAILED: {qe} ===")
            print(_tb.format_exc())
            raise

        result = []
        for job in jobs:
            try:
                owner = job.owner
                company_id = owner.company_id if owner else None
                HUNTERS_LOGO = "/hunters-logo-blue.jpeg"
                if owner and owner.is_admin:
                    company_name = "Hunters for HR Solutions"
                    company_id = None
                else:
                    company_name = company_map.get(company_id, "Unknown Company") if company_id else "Unknown Company"
                hide_salary = bool(getattr(job, 'hide_salary', False))
                result.append({
                    "id": job.id,
                    "job_title": job.job_title,
                    "job_description": job.job_description or "",
                    "job_location": job.job_location or "",
                    "min_experience": job.min_experience,
                    "required_skills": job.required_skills or "",
                    "nice_to_have_skills": job.nice_to_have_skills,
                    "behavioral_skills": job.behavioral_skills,
                    "education_level": job.education_level or "",
                    "salary_range": "" if hide_salary else (job.salary_range or ""),
                    "weight_experience": job.weight_experience,
                    "weight_skills": job.weight_skills,
                    "weight_education": job.weight_education,
                    "weight_behavioral": job.weight_behavioral,
                    "is_approved": job.is_approved,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "company_name": company_name,
                    "company_id": company_id,
                    "company_logo_url": HUNTERS_LOGO if (owner and owner.is_admin) else (logo_map.get(company_id) if company_id else None),
                    "hide_salary": hide_salary,
                    "salary_min": None,
                    "salary_max": None,
                    "employment_type": job.education_level,
                })
            except Exception as job_err:
                print(f"=== ERROR SERIALIZING JOB {job.id}: {job_err} ===")
                logger.error(f"Error serializing job {job.id}: {job_err}")
                continue
        print(f"=== RETURNING {len(result)} JOBS ===")
        return result
    except Exception as e:
        print(f"=== PUBLIC JOBS 500 ERROR: {e} ===")
        print(_tb.format_exc())
        logger.error(f"get_public_jobs error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load jobs: {str(e)}")
