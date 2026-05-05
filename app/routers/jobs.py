from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from sqlalchemy.orm import Session
from typing import List
from .. import models, schemas
from ..database import get_db
from ..services.file_processor import process_excel_jobs, extract_text_from_pdf
from .auth import get_current_user

router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"]
)

@router.post("", response_model=schemas.JobResponse)
def create_job(job: schemas.JobCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    db_job = models.Job(**job.model_dump(), owner_id=current_user.id)
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
    return db.query(models.Job).filter(models.Job.owner_id == current_user.id).offset(skip).limit(limit).all()

@router.get("/{job_id}", response_model=schemas.JobResponse)
def read_job(job_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    job = db.query(models.Job).filter(models.Job.id == job_id, models.Job.owner_id == current_user.id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@router.put("/{job_id}", response_model=schemas.JobResponse)
def update_job(job_id: int, updated_job: schemas.JobCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    db_job = db.query(models.Job).filter(models.Job.id == job_id, models.Job.owner_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    for key, value in updated_job.model_dump().items():
        setattr(db_job, key, value)
    
    db.commit()
    db.refresh(db_job)
    return db_job
@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    db_job = db.query(models.Job).filter(models.Job.id == job_id, models.Job.owner_id == current_user.id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    db.delete(db_job)
    db.commit()
    return {"message": "Job deleted successfully"}

@router.get("/{job_id}/candidates", response_model=List[schemas.CandidateResponse])
def get_job_candidates(job_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Get all candidates for a specific job"""
    job = db.query(models.Job).filter(models.Job.id == job_id, models.Job.owner_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    candidates = db.query(models.Candidate).filter(models.Candidate.applied_job_id == job_id).all()
    return candidates

# Admin endpoints for job approval
@router.get("/admin/all")
def get_all_jobs(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Get all jobs (Admin only)"""
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    all_jobs = db.query(models.Job).all()
    return all_jobs

@router.get("/admin/pending")
def get_pending_jobs(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Get all jobs pending approval (Admin only)"""
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    pending = db.query(models.Job).filter(models.Job.is_approved == False).all()
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
    
    db.delete(job)
    db.commit()
    
    return {"message": f"Job rejected and deleted", "reason": rejection_data.rejection_reason}

