from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
import logging

from .. import models, schemas, database
from ..services.file_processor import extract_text_from_file
from ..services.ai_evaluator import extract_candidate_info
from .candidates import run_evaluation_task

logger = logging.getLogger(__name__)

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

@router.get("/job/{job_id}", response_model=schemas.JobResponse)
def get_public_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

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

    # 1. Process File
    content = await file.read()
    cv_text = extract_text_from_file(file.filename, content)
    
    # 2. Extract Info if needed (optional since we have form data)
    # But we want to store the full CV text
    
    # 3. Create Candidate
    new_candidate = models.Candidate(
        name=name,
        email=email,
        phone=phone,
        expected_salary=expected_salary,
        job_applied=job_id,
        experience_years=0, # Will be updated by AI if we want, or extracted
        education="",
        skills="",
        cv_text=cv_text,
        owner_id=job.owner_id # Map to the job owner
    )
    
    db.add(new_candidate)
    db.commit()
    db.refresh(new_candidate)
    
    # 4. Trigger AI Screening in background
    from .candidates import SessionLocal # Import here to avoid circular
    background_tasks.add_task(run_evaluation_task, new_candidate.id, SessionLocal())

    return {
        "message": "Application submitted successfully",
        "candidate_id": new_candidate.id,
        "job_id": job_id
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

@router.get("/jobs", response_model=List[schemas.JobResponse])
def get_public_jobs(db: Session = Depends(get_db)):
    """
    Get all approved public job postings.
    Only shows jobs from approved companies.
    """
    # Get approved companies
    approved_companies = db.query(models.Company).filter(
        models.Company.is_approved == True
    ).all()
    approved_company_ids = [c.id for c in approved_companies]
    
    # Get approved jobs from approved companies
    jobs = db.query(models.Job).join(
        models.User, models.Job.owner_id == models.User.id
    ).filter(
        models.Job.is_approved == True,
        models.User.company_id.in_(approved_company_ids)
    ).all()
    
    return jobs
