from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List
import logging

logger = logging.getLogger(__name__)
from .. import models, schemas
from ..database import get_db
from ..services.file_processor import process_excel_jobs, extract_text_from_pdf
from .auth import get_current_user


def _payload_to_job_fields(job: schemas.JobSavePayload) -> dict:
    """Map frontend JobSavePayload fields to Job model column names."""
    weights = job.ai_weights or {}
    exp_w   = weights.get("experience", 30)
    skl_w   = weights.get("skills",     40)
    edu_w   = weights.get("education",  20)
    beh_w   = weights.get("behavioral", 10)
    aw = job.agent_weights or {}

    if job.salary_min or job.salary_max:
        salary_parts = []
        if job.salary_min:
            salary_parts.append(str(job.salary_min))
        if job.salary_max:
            salary_parts.append(str(job.salary_max))
        salary_range = " - ".join(salary_parts) + " EGP" if salary_parts else ""
    else:
        salary_range = job.salary_range or ""

    return dict(
        job_title        = job.title,
        job_description  = job.description or "",
        job_location     = job.location or "",
        min_experience   = job.experience_years,
        required_skills  = job.required_skills or "",
        nice_to_have_skills = job.nice_to_have_skills,
        behavioral_skills   = job.behavioral_skills,
        education_level  = job.education_level or job.employment_type or "Not specified",
        salary_range     = salary_range,
        hide_salary      = bool(job.hide_salary),
        industry_experience = job.industry_experience,
        department       = job.department or "Other",
        weight_experience = round(exp_w / 100, 4),
        weight_skills     = round(skl_w / 100, 4),
        weight_education  = round(edu_w / 100, 4),
        weight_behavioral = round(beh_w / 100, 4),
        agent_weight_title      = int(aw.get("title",      25)),
        agent_weight_industry   = int(aw.get("industry",   25)),
        agent_weight_experience = int(aw.get("experience", 25)),
        agent_weight_skills     = int(aw.get("skills",     25)),
        essential_skills        = job.essential_skills or [],
    )

router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"]
)

@router.post("", response_model=schemas.JobResponse)
def create_job(job: schemas.JobSavePayload, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    fields = _payload_to_job_fields(job)
    db_job = models.Job(**fields, owner_id=current_user.id)
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return db_job

@router.post("/upload")
async def upload_jobs(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    content = await file.read()
    filename = file.filename.lower()
    
    jobs_data = []
    
    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        jobs_data = process_excel_jobs(content)
    elif filename.endswith(".pdf"):
        text = extract_text_from_pdf(content)
        # Use filename as title, and full text as description
        jobs_data.append({
            "job_title": file.filename.replace(".pdf", ""),
            "job_description": text,
            "required_skills": "Skills extracted from PDF",
            "min_experience": 0,
            "education_level": "Specified in PDF",
            "salary_range": ""
        })
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Please use Excel or PDF.")

    imported_count = 0
    for data in jobs_data:
        new_job = models.Job(
            job_title=str(data.get("job_title", data.get("Title", "New Job"))),
            job_description=str(data.get("job_description", data.get("Description", ""))),
            required_skills=str(data.get("required_skills", data.get("Skills", ""))),
            min_experience=int(data.get("min_experience", data.get("Experience", 0)) or 0),
            education_level=str(data.get("education_level", data.get("Education", ""))),
            salary_range=str(data.get("salary_range", data.get("Salary Range", ""))),
            owner_id=current_user.id
        )
        db.add(new_job)
        db.commit()
        db.refresh(new_job)
        imported_count += 1

    return {"message": f"Successfully imported {imported_count} job(s)."}

@router.get("", response_model=List[schemas.JobResponse])
def read_jobs(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return db.query(models.Job).filter(
        models.Job.owner_id == current_user.id,
        or_(models.Job.status == None, models.Job.status != 'rejected')
    ).offset(skip).limit(limit).all()

@router.get("/{job_id}", response_model=schemas.JobResponse)
def read_job(job_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    job = db.query(models.Job).filter(models.Job.id == job_id, models.Job.owner_id == current_user.id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@router.put("/{job_id}", response_model=schemas.JobResponse)
def update_job(job_id: int, updated_job: schemas.JobSavePayload, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    db_job = db.query(models.Job).filter(models.Job.id == job_id, models.Job.owner_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")

    for key, value in _payload_to_job_fields(updated_job).items():
        setattr(db_job, key, value)

    db.commit()
    db.refresh(db_job)
    return db_job
@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    db_job = db.query(models.Job).filter(models.Job.id == job_id, models.Job.owner_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        app_ids = [r[0] for r in db.query(models.Application.id).filter(
            models.Application.job_id == job_id
        ).all()]
        vs_conds = [models.VoiceScreening.job_id == job_id]
        if app_ids:
            vs_conds.append(models.VoiceScreening.application_id.in_(app_ids))
        db.query(models.VoiceScreening).filter(or_(*vs_conds)).delete(synchronize_session=False)
        ev_conds = [models.Evaluation.job_id == job_id]
        if app_ids:
            ev_conds.append(models.Evaluation.application_id.in_(app_ids))
        db.query(models.Evaluation).filter(or_(*ev_conds)).delete(synchronize_session=False)
        if app_ids:
            db.query(models.Interview).filter(models.Interview.application_id.in_(app_ids)).delete(synchronize_session=False)
        if app_ids:
            db.query(models.Offer).filter(models.Offer.application_id.in_(app_ids)).delete(synchronize_session=False)
        db.query(models.Application).filter(models.Application.job_id == job_id).delete(synchronize_session=False)
        db.query(models.Candidate).filter(models.Candidate.job_applied == job_id).update({"job_applied": None}, synchronize_session=False)
        db.delete(db_job)
        db.commit()
        return {"message": "Job deleted successfully"}
    except Exception as e:
        db.rollback()
        logger.error(f"Job delete error for job_id={job_id}: {e}")
        raise HTTPException(status_code=500, detail="Delete failed")

@router.get("/{job_id}/candidates")
def get_job_candidates(job_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Get all candidates for a specific job with evaluation data included"""
    job = db.query(models.Job).filter(models.Job.id == job_id, models.Job.owner_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    candidates = db.query(models.Candidate).filter(models.Candidate.job_applied == job_id).all()

    cand_ids = [c.id for c in candidates]
    _evs = db.query(models.Evaluation).filter(
        models.Evaluation.candidate_id.in_(cand_ids)
    ).order_by(models.Evaluation.id.desc()).all()
    ev_map = {}
    for _ev in _evs:
        if _ev.candidate_id not in ev_map:
            ev_map[_ev.candidate_id] = _ev

    result = []
    for c in candidates:
        ev = ev_map.get(c.id)
        result.append({
            "id": c.id,
            "name": c.name,
            "full_name": c.name,
            "email": c.email,
            "phone": c.phone or "",
            "job_applied": c.job_applied,
            "experience_years": c.experience_years if c.experience_years is not None else 0,
            "expected_salary": c.expected_salary or "",
            "education": c.education or "",
            "skills": c.skills or "",
            "cv_text": "",
            "last_title": c.last_title or "",
            "last_employer": c.last_employer or "",
            "location": job.job_location or "",
            "has_cv": bool(c.cv_text and c.cv_text.strip()),
            "stage": "New",
            "evaluation_score": ev.score if ev else None,
            "score": ev.score if ev else None,
            "decision": ev.decision if ev else None,
            "evaluation": {
                "score": ev.score,
                "decision": ev.decision,
                "reason": ev.reason,
                "strengths": ev.strengths,
                "weaknesses": ev.weaknesses,
            } if ev else None,
        })
    return result

# Admin endpoints for job approval
@router.get("/admin/all")
def get_all_jobs(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Get all jobs (Admin only)"""
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    all_jobs = db.query(models.Job).filter(
        or_(models.Job.status == None, models.Job.status != 'rejected')
    ).all()
    return all_jobs

@router.get("/admin/pending")
def get_pending_jobs(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Get all jobs pending approval (Admin only)"""
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    pending = db.query(models.Job).filter(
        models.Job.is_approved == False,
        or_(models.Job.status == None, models.Job.status != 'rejected')
    ).all()
    return pending

@router.post("/admin/approve/{job_id}")
def approve_job(
    job_id: int,
    approval_data: schemas.ApprovalData,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Approve a job posting (Admin only)"""
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    from datetime import datetime
    job.is_approved = True
    job.approval_date = datetime.utcnow()
    job.approval_notes = approval_data.approval_notes
    db.commit()
    
    return {"message": f"Job '{job.job_title}' approved successfully"}

@router.post("/admin/reject/{job_id}")
def reject_job(
    job_id: int,
    rejection_data: schemas.RejectionData,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Reject a job posting (Admin only)"""
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = 'rejected'
    job.is_approved = False
    db.commit()

    return {"message": f"Job rejected", "reason": rejection_data.rejection_reason}

