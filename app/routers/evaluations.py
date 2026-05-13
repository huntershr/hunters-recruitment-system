from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import logging
import json

from .. import models, schemas
from ..database import get_db
from ..services.ai_evaluator import evaluate_candidate
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Evaluations"]
)

@router.get("/results", response_model=List[schemas.EvaluationResponse])
def list_evaluations(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if current_user.is_admin:
        evals = db.query(models.Evaluation).offset(skip).limit(limit).all()
    else:
        evals = db.query(models.Evaluation).join(models.Candidate).filter(models.Candidate.owner_id == current_user.id).offset(skip).limit(limit).all()
    
    # Process results to handle JSON strings in DB for list fields
    import json
    processed = []
    for e in evals:
        e_dict = {col.name: getattr(e, col.name) for col in e.__table__.columns}
        if isinstance(e_dict.get("suggested_interview_questions"), str):
            try:
                e_dict["suggested_interview_questions"] = json.loads(e_dict["suggested_interview_questions"])
            except:
                e_dict["suggested_interview_questions"] = []
        processed.append(e_dict)
    return processed

@router.post("/evaluate/{candidate_id}", response_model=schemas.EvaluationResponse)
def trigger_evaluation_manually(candidate_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    Manually triggers evaluation for a candidate. Used if the automatic evaluation failed.
    """
    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id, models.Candidate.owner_id == current_user.id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    # Check if already evaluated
    existing_eval = db.query(models.Evaluation).filter(models.Evaluation.candidate_id == candidate_id).first()
    if existing_eval:
        raise HTTPException(status_code=400, detail="Candidate already evaluated. Please delete existing evaluation to re-evaluate.")

    job = db.query(models.Job).filter(models.Job.id == candidate.job_applied).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job associated with candidate not found")

    logger.info(f"Running manual evaluation for candidate {candidate_id}")
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
    db.refresh(db_eval)
    
    # Parse back for the response model
    db_eval_dict = {col.name: getattr(db_eval, col.name) for col in db_eval.__table__.columns}
    db_eval_dict["suggested_interview_questions"] = json.loads(db_eval_dict["suggested_interview_questions"])
    return db_eval_dict

@router.post("/re-evaluate/{candidate_id}", response_model=schemas.EvaluationResponse)
def re_evaluate_candidate(candidate_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Delete the existing evaluation and run a fresh AI evaluation for this candidate."""
    if current_user.is_admin:
        candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    else:
        candidate = db.query(models.Candidate).filter(
            models.Candidate.id == candidate_id,
            models.Candidate.owner_id == current_user.id
        ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    job = db.query(models.Job).filter(models.Job.id == candidate.job_applied).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Delete stale evaluation
    db.query(models.Evaluation).filter(models.Evaluation.candidate_id == candidate_id).delete()
    db.commit()

    logger.info(f"Re-evaluating candidate {candidate_id}")
    eval_result = evaluate_candidate(job, candidate)

    def list_to_str(val):
        if isinstance(val, list):
            return "\n".join([f"- {i}" for i in val])
        return str(val)

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
    db.refresh(db_eval)

    db_eval_dict = {col.name: getattr(db_eval, col.name) for col in db_eval.__table__.columns}
    try:
        db_eval_dict["suggested_interview_questions"] = json.loads(db_eval_dict["suggested_interview_questions"])
    except Exception:
        db_eval_dict["suggested_interview_questions"] = []
    return db_eval_dict

@router.put("/update/{evaluation_id}", response_model=schemas.EvaluationResponse)
def update_evaluation(evaluation_id: int, updated_eval: schemas.EvaluationBase, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    Allows a recruiter to manually moderate/override an AI evaluation.
    """
    db_eval = db.query(models.Evaluation).join(models.Candidate).filter(models.Evaluation.id == evaluation_id, models.Candidate.owner_id == current_user.id).first()
    if not db_eval:
        raise HTTPException(status_code=404, detail="Evaluation not found or access denied")
    
    import json
    db_eval.score = updated_eval.score
    db_eval.decision = updated_eval.decision
    db_eval.reason = updated_eval.reason
    db_eval.strengths = updated_eval.strengths
    db_eval.weaknesses = updated_eval.weaknesses
    if updated_eval.suggested_interview_questions:
        db_eval.suggested_interview_questions = json.dumps(updated_eval.suggested_interview_questions)
        
    db.commit()
    db.refresh(db_eval)
    
    # Parse back for response
    e_dict = {col.name: getattr(db_eval, col.name) for col in db_eval.__table__.columns}
    if isinstance(e_dict.get("suggested_interview_questions"), str):
        e_dict["suggested_interview_questions"] = json.loads(e_dict["suggested_interview_questions"])
    return e_dict
