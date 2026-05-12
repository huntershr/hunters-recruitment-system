from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, File, UploadFile, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import logging
import io
import csv
import json

from .. import models, schemas
from ..database import get_db, SessionLocal
from ..services.ai_evaluator import evaluate_candidate, extract_candidate_info, finalize_evaluation
from ..services.file_processor import extract_text_from_pdf, extract_text_from_docx, process_excel_candidates
from .auth import get_current_user

logger = logging.getLogger(__name__)

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

def run_evaluation_task(candidate_id: int, db: Session):
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
        db_eval = models.Evaluation(
            candidate_id=candidate.id,
            job_id=job.id,
            score=eval_result.get("score", 0.0),
            decision=eval_result.get("decision", "Reject"),
            reason=eval_result.get("reason", "Failed to evaluate"),
            strengths=list_to_str(eval_result.get("strengths", "")),
            weaknesses=list_to_str(eval_result.get("weaknesses", "")),
            suggested_interview_questions=json.dumps(eval_result.get("suggested_interview_questions", []))
        )
        
        db.add(db_eval)
        db.commit()
        logger.info(f"Finished evaluation for candidate {candidate_id}")
        
        # Send results back to Google Sheets
        from ..services.google_sheets import update_candidate_row
        update_candidate_row(candidate.email, eval_result)
    finally:
        db.close()

@router.post("/screen-cv")
async def screen_cv(
    file: UploadFile = File(...),
    job_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Screen a single PDF CV against a job using real AI. Returns immediate results."""
    content = await file.read()
    filename = (file.filename or "").lower()

    if filename.endswith(".pdf"):
        cv_text = extract_text_from_pdf(content)
    elif filename.endswith(".docx"):
        cv_text = extract_text_from_docx(content)
    else:
        raise HTTPException(status_code=400, detail="Only PDF or DOCX files are supported")

    if not cv_text or not cv_text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from the file")

    # AI: extract candidate info from CV
    info = extract_candidate_info(cv_text)

    name  = info.get("name")  or file.filename.rsplit(".", 1)[0].replace("_", " ").title()
    email = info.get("email") or f"bulk_{file.filename}@noemail.hunters"
    phone = str(info.get("phone") or "")

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
        owner_id=current_user.id,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)

    # Synchronous AI evaluation — gives immediate result
    raw = evaluate_candidate(job, candidate)
    result = finalize_evaluation(raw)

    db_eval = models.Evaluation(
        candidate_id=candidate.id,
        job_id=job.id,
        score=result.get("score", 0.0),
        decision=result.get("decision", "Reject"),
        reason=result.get("reason", ""),
        strengths=str(result.get("strengths", "") or ""),
        weaknesses=str(result.get("weaknesses", "") or ""),
        suggested_interview_questions=json.dumps(result.get("suggested_interview_questions", [])),
    )
    db.add(db_eval)
    db.commit()

    return {
        "candidate_id": candidate.id,
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
    candidates = db.query(models.Candidate).filter(models.Candidate.owner_id == current_user.id).offset(skip).limit(limit).all()
    return candidates

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

