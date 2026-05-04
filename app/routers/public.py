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

    return {"message": "Application submitted successfully", "candidate_id": new_candidate.id}

@router.get("/jobs", response_model=List[schemas.JobResponse])
def get_public_jobs(db: Session = Depends(get_db)):
    # Only show jobs belonging to the main admin (Owner ID 1)
    return db.query(models.Job).filter(models.Job.owner_id == 1).all()
