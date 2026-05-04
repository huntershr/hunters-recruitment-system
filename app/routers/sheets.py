from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
import os
import requests
import logging

from .. import models, schemas
from ..database import get_db
from ..routers.candidates import run_evaluation_task
from ..services.google_sheets import get_apps_script_url

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sheets",
    tags=["Sheets Integration"]
)

@router.post("/export")
def export_to_sheets(db: Session = Depends(get_db)):
    """
    Exports all candidates and their evaluations to the Google Sheet via Webhook.
    """
    url = get_apps_script_url()
    if not url:
        raise HTTPException(status_code=500, detail="GOOGLE_APPS_SCRIPT_URL is not configured in .env.")

    try:
        candidates = db.query(models.Candidate).all()
        
        # Prepare header
        header = ["ID", "Name", "Email", "Phone", "Job ID", "Experience Years", "Expected Salary", "Education", "Skills", "CV Text", "AI Score", "AI Decision", "AI Reason", "Suggested Questions"]
        data = [header]
        
        for c in candidates:
            eval = db.query(models.Evaluation).filter(models.Evaluation.candidate_id == c.id).first()
            score = eval.score if eval else ""
            decision = eval.decision if eval else "Pending"
            reason = eval.reason if eval else ""
            questions = "\n".join(eval.suggested_interview_questions) if eval and eval.suggested_interview_questions else ""
            
            row = [
                c.id, c.name, c.email, c.phone, c.job_applied, c.experience_years, 
                c.expected_salary, c.education, c.skills, c.cv_text,
                score, decision, reason, questions
            ]
            data.append(row)
        
        payload = {
            "action": "export_candidates",
            "candidates": data
        }
        
        response = requests.post(url, json=payload)
        response.raise_for_status()
        
        return {"message": f"Successfully exported {len(candidates)} candidates to Google Sheets."}
    except Exception as e:
        logger.error(f"Failed to export to sheets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/import")
def import_from_sheets(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Imports candidates from Google Sheets via Webhook and triggers evaluation.
    """
    url = get_apps_script_url()
    if not url:
        raise HTTPException(status_code=500, detail="GOOGLE_APPS_SCRIPT_URL is not configured in .env.")

    try:
        response = requests.get(url)
        response.raise_for_status()
        
        try:
            records = response.json()
        except Exception as json_err:
            logger.error(f"Failed to parse JSON from Google Sheets: {response.text[:500]}")
            raise HTTPException(status_code=500, detail="Google Sheets returned an invalid response. Please ensure your script is deployed as 'Anyone'.")
        
        imported_count = 0
        
        # Make sure there is at least one job in the DB to assign if missing
        default_job = db.query(models.Job).first()
        if not default_job:
            raise HTTPException(status_code=400, detail="No jobs exist in the database. Please create a job first.")

        for row in records:
            # Find the email key (case insensitive or exact)
            email_key = next((k for k in row.keys() if k.lower().strip() == 'email'), None)
            if not email_key:
                continue
            email = str(row.get(email_key, ""))
            if not email:
                continue
                
            # Check if candidate already exists
            existing = db.query(models.Candidate).filter(models.Candidate.email == email).first()
            if existing:
                continue # Skip existing
                
            job_id_key = next((k for k in row.keys() if 'job' in k.lower() and 'id' in k.lower()), None)
            job_id = row.get(job_id_key) if job_id_key else default_job.id
            if not job_id:
                job_id = default_job.id
                
            # Helper to get value ignoring case
            def get_val(possible_names, default=""):
                for name in possible_names:
                    key = next((k for k in row.keys() if name.lower() in k.lower()), None)
                    if key and row.get(key):
                        return row.get(key)
                return default

            new_candidate = models.Candidate(
                name=str(get_val(["name"], "Unknown")),
                email=email,
                phone=str(get_val(["phone"], "")),
                job_applied=job_id,
                experience_years=int(get_val(["experience"], 0) or 0),
                expected_salary=str(get_val(["salary"], "")),
                education=str(get_val(["education"], "")),
                skills=str(get_val(["skills"], "")),
                cv_text=str(get_val(["cv"], ""))
            )
            
            db.add(new_candidate)
            db.commit()
            db.refresh(new_candidate)
            imported_count += 1
            
            # Trigger evaluation
            background_tasks.add_task(run_evaluation_task, new_candidate.id, Session(bind=db.get_bind()))
            
        return {"message": f"Successfully imported {imported_count} new candidates and started evaluations."}
    except Exception as e:
        logger.error(f"Failed to import from sheets: {e}")
        raise HTTPException(status_code=500, detail=str(e))
