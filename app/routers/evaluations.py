from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import logging
import json

from .. import models, schemas
from ..database import get_db
from ..services.ai_evaluator import call_agent_screener, evaluate_candidate, finalize_evaluation
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


@router.put("/update/{evaluation_id}", response_model=schemas.EvaluationResponse)
def update_evaluation(evaluation_id: int, updated_eval: schemas.EvaluationBase, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    Allows a recruiter to manually override an AI evaluation.
    Admin: unrestricted. Non-admin: scoped to jobs they own.
    Supports both Type A (candidate_id set) and Type B (candidate_id NULL).
    """
    if current_user.is_admin:
        db_eval = db.query(models.Evaluation).filter(models.Evaluation.id == evaluation_id).first()
    else:
        db_eval = (
            db.query(models.Evaluation)
            .join(models.Application, models.Evaluation.application_id == models.Application.id)
            .join(models.Job, models.Application.job_id == models.Job.id)
            .filter(
                models.Evaluation.id == evaluation_id,
                models.Job.owner_id == current_user.id,
            )
            .first()
        )

    if not db_eval:
        raise HTTPException(status_code=404, detail="Evaluation not found or access denied")

    db_eval.score = updated_eval.score
    db_eval.decision = updated_eval.decision
    db_eval.reason = updated_eval.reason
    db_eval.strengths = updated_eval.strengths
    db_eval.weaknesses = updated_eval.weaknesses
    if updated_eval.suggested_interview_questions:
        db_eval.suggested_interview_questions = json.dumps(updated_eval.suggested_interview_questions)

    db.commit()
    db.refresh(db_eval)

    e_dict = {col.name: getattr(db_eval, col.name) for col in db_eval.__table__.columns}
    if isinstance(e_dict.get("suggested_interview_questions"), str):
        try:
            e_dict["suggested_interview_questions"] = json.loads(e_dict["suggested_interview_questions"])
        except Exception:
            e_dict["suggested_interview_questions"] = []
    return e_dict


@router.post("/re-evaluate/{candidate_id}", response_model=schemas.EvaluationResponse)
def re_evaluate_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete the existing evaluation and run a fresh agent-first evaluation for this candidate.
    Used by the company dashboard Re-evaluate button when a candidate has no score."""
    if current_user.is_admin:
        candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    else:
        candidate = db.query(models.Candidate).filter(
            models.Candidate.id == candidate_id,
            models.Candidate.owner_id == current_user.id,
        ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    job = db.query(models.Job).filter(models.Job.id == candidate.job_applied).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Best-effort application lookup — legacy candidates may not have one
    application = (
        db.query(models.Application)
        .filter(
            models.Application.candidate_id == candidate_id,
            models.Application.job_id == candidate.job_applied,
        )
        .first()
    )

    db.query(models.Evaluation).filter(models.Evaluation.candidate_id == candidate_id).delete()
    db.commit()

    logger.info(f"Re-evaluating candidate {candidate_id} (agent-first)")

    _ar = call_agent_screener(candidate.cv_text or "", job, candidate.id)
    if _ar is not None:
        _cp = _ar.pop("_candidate_profile", None) or {}
        if _cp:
            if not candidate.name or candidate.name.lower().startswith("resume"):
                v = (_cp.get("name") or "").strip()
                if v and v.isprintable() and any(c.isalpha() for c in v) and len(v) <= 80:
                    candidate.name = v
            if not candidate.last_title:
                v = (_cp.get("current_title") or "").strip()
                if v and len(v) <= 80: candidate.last_title = v
            if not candidate.last_employer:
                v = (_cp.get("last_employer") or "").strip()
                if v: candidate.last_employer = v
            if not (candidate.experience_years or 0):
                v = _cp.get("years_experience")
                if v:
                    try:
                        yr = int(v)
                        if 1 <= yr <= 25: candidate.experience_years = yr
                    except Exception: pass
            if not candidate.education:
                v = (_cp.get("education") or "").strip()
                if v: candidate.education = v
            if not candidate.skills:
                v = _cp.get("skills")
                if isinstance(v, list): v = ", ".join(str(x) for x in v if x)
                if v: candidate.skills = str(v).strip()
            if not candidate.languages:
                v = _cp.get("languages")
                if isinstance(v, list) and v: candidate.languages = v
            if not candidate.certifications:
                v = _cp.get("certifications")
                if isinstance(v, list): v = ", ".join(str(x) for x in v if x)
                if v: candidate.certifications = str(v).strip()
            if not candidate.summary:
                v = (_cp.get("summary") or "").strip()
                if v: candidate.summary = v
            if not candidate.experiences:
                v = _cp.get("experiences")
                if isinstance(v, list) and v: candidate.experiences = v
            if not candidate.education_history:
                v = _cp.get("education_history")
                if isinstance(v, list) and v: candidate.education_history = v
            db.commit()
        result = _ar
    else:
        logger.warning(f"Agent unavailable for re-evaluate candidate {candidate_id}; falling back to Gemini")
        result = finalize_evaluation(evaluate_candidate(job, candidate))

    _lstr = lambda v: "\n".join(f"- {x}" for x in v if x) if isinstance(v, list) else str(v or "")
    _bd = result.get("score_breakdown") or {}
    db_eval = models.Evaluation(
        candidate_id=candidate.id,
        application_id=application.id if application else None,
        job_id=job.id,
        score=result.get("score", 0.0),
        score_experience=_bd.get("experience"),
        score_skills=_bd.get("skills"),
        score_education=_bd.get("education"),
        score_behavioral=_bd.get("behavioral"),
        decision=result.get("decision", "Reject"),
        reason=result.get("summary_en") or result.get("reason", ""),
        strengths=_lstr(result.get("strengths_en") or result.get("strengths") or []),
        weaknesses=_lstr(result.get("gaps_en") or result.get("weaknesses") or []),
        suggested_interview_questions=(
            result.get("interview_questions_en") or result.get("suggested_interview_questions") or []
        ),
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
    db.refresh(db_eval)

    e_dict = {col.name: getattr(db_eval, col.name) for col in db_eval.__table__.columns}
    if isinstance(e_dict.get("suggested_interview_questions"), str):
        try:
            e_dict["suggested_interview_questions"] = json.loads(e_dict["suggested_interview_questions"])
        except Exception:
            e_dict["suggested_interview_questions"] = []
    return e_dict
