from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
import secrets

from .. import models, database
from ..routers.auth import get_current_user
from ..services.voice_engine import VoiceEngine

router = APIRouter(prefix="/api/voice-screening", tags=["Voice Screening"])


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _require_company_or_admin(current_user: models.User):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Company or admin access required")


def _screening_dict(vs: models.VoiceScreening) -> dict:
    return {
        "id": vs.id,
        "candidate_id": vs.candidate_id,
        "application_id": vs.application_id,
        "job_id": vs.job_id,
        "triggered_by": vs.triggered_by,
        "attempt_number": vs.attempt_number,
        "status": vs.status,
        "experience_response": vs.experience_response,
        "availability_response": vs.availability_response,
        "job_type_suitable": vs.job_type_suitable,
        "interview_confirmed": vs.interview_confirmed,
        "expected_salary": vs.expected_salary,
        "candidate_questions": vs.candidate_questions,
        "has_candidate_questions": vs.has_candidate_questions,
        "english_level": vs.english_level,
        "fluency_assessment": vs.fluency_assessment,
        "clarity_assessment": vs.clarity_assessment,
        "experience_match": vs.experience_match,
        "language_notes": vs.language_notes,
        "ai_summary": vs.ai_summary,
        "full_transcript": vs.full_transcript,
        "created_at": vs.created_at.isoformat() if vs.created_at else None,
        "completed_at": vs.completed_at.isoformat() if vs.completed_at else None,
        "job_title_at_time": vs.job_title_at_time,
        "job_type_at_time": vs.job_type_at_time,
        "interview_date_at_time": vs.interview_date_at_time,
        "interview_time_at_time": vs.interview_time_at_time,
    }


# ── Helpers ───────────────────────────────────────────────────────────────

def _build_questions(job_title, job_type, interview_date, interview_time):
    q4 = (
        f"Your interview is scheduled for {interview_date} at {interview_time} — can you confirm your attendance?"
        if interview_date and interview_time
        else "Would you be available for an interview this week or next week?"
    )
    return [
        f"Please tell us about your experience relevant to the {job_title} role.",
        "When are you available to start the job?",
        f"This role is {job_type} — is that suitable for you?",
        q4,
        "What is your expected monthly salary?",
        "Do you have any questions for us? If yes, please go ahead after the beep.",
    ]


def _lookup_by_token(token: str, db: Session) -> models.VoiceScreening:
    vs = db.query(models.VoiceScreening).filter(
        models.VoiceScreening.screening_token == token
    ).first()
    if not vs:
        raise HTTPException(status_code=404, detail="Invalid or expired screening link")
    return vs


# ── Request schemas ────────────────────────────────────────────────────────

class StartScreeningIn(BaseModel):
    candidate_id: Optional[int] = None
    application_id: Optional[int] = None
    job_id: Optional[int] = None


class SaveAnswerIn(BaseModel):
    question_number: int   # 1-6
    transcript: str


# ── Routes ────────────────────────────────────────────────────────────────

@router.post("/start")
def start_screening(
    data: StartScreeningIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    import traceback
    try:
        _require_company_or_admin(current_user)

        # Resolve candidate and job
        candidate = None
        if data.candidate_id:
            candidate = db.query(models.Candidate).filter(models.Candidate.id == data.candidate_id).first()

        application = None
        if data.application_id:
            application = db.query(models.Application).filter(models.Application.id == data.application_id).first()
            if application and not candidate and application.candidate_id:
                candidate = db.query(models.Candidate).filter(models.Candidate.id == application.candidate_id).first()

        job_id = data.job_id or (application.job_id if application else None) or (candidate.job_applied if candidate else None)
        job = db.query(models.Job).filter(models.Job.id == job_id).first() if job_id else None

        cand_name  = (candidate.name if candidate else None) or (application.applicant_name if application else "Candidate")
        job_title  = job.job_title if job else "the role"
        job_type   = job.education_level or "Full-time"   # reuse field; update if job_type column added

        # Interview date/time for Q4
        interview_date = interview_time = None
        if application:
            iv = (
                db.query(models.Interview)
                .filter(
                    models.Interview.application_id == application.id,
                    models.Interview.status != "cancelled",
                )
                .order_by(models.Interview.created_at.desc())
                .first()
            )
            if iv:
                interview_date = str(iv.interview_date)
                interview_time = str(iv.interview_time)[:5]

        # Increment attempt number
        last = (
            db.query(models.VoiceScreening)
            .filter(models.VoiceScreening.application_id == data.application_id)
            .order_by(desc(models.VoiceScreening.attempt_number))
            .first()
        ) if data.application_id else None
        attempt = (last.attempt_number + 1) if last else 1

        token = secrets.token_urlsafe(32)

        vs = models.VoiceScreening(
            candidate_id=candidate.id if candidate else None,
            application_id=data.application_id,
            job_id=job_id,
            triggered_by=current_user.id,
            attempt_number=attempt,
            status="pending",
            screening_token=token,
            token_used=False,
            job_title_at_time=job_title,
            job_type_at_time=job_type,
            interview_date_at_time=interview_date,
            interview_time_at_time=interview_time,
        )
        db.add(vs)
        db.commit()
        db.refresh(vs)

        questions = _build_questions(job_title, job_type, interview_date, interview_time)

        return {
            "screening_id": vs.id,
            "token": token,
            "candidate_name": cand_name,
            "job_title": job_title,
            "job_type": job_type,
            "interview_date": interview_date,
            "interview_time": interview_time,
            "attempt_number": attempt,
            "questions": questions,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Screening start failed: {str(e)} | {traceback.format_exc()}",
        )


@router.post("/{screening_id}/save-answer")
def save_answer(
    screening_id: int,
    data: SaveAnswerIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_company_or_admin(current_user)
    vs = db.query(models.VoiceScreening).filter(models.VoiceScreening.id == screening_id).first()
    if not vs:
        raise HTTPException(status_code=404, detail="Screening not found")

    field_map = {
        1: "experience_response",
        2: "availability_response",
        3: "job_type_suitable",
        4: "interview_confirmed",
        5: "expected_salary",
        6: "candidate_questions",
    }
    field = field_map.get(data.question_number)
    if not field:
        raise HTTPException(status_code=400, detail="question_number must be 1-6")

    setattr(vs, field, data.transcript)
    if data.question_number == 6 and data.transcript.strip():
        vs.has_candidate_questions = True
    vs.status = "in_progress"
    db.commit()
    return {"success": True}


@router.post("/{screening_id}/complete")
def complete_screening(
    screening_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_company_or_admin(current_user)
    vs = db.query(models.VoiceScreening).filter(models.VoiceScreening.id == screening_id).first()
    if not vs:
        raise HTTPException(status_code=404, detail="Screening not found")

    # Assemble full transcript
    parts = []
    if vs.experience_response:   parts.append(f"Q1 (Experience): {vs.experience_response}")
    if vs.availability_response: parts.append(f"Q2 (Availability): {vs.availability_response}")
    if vs.job_type_suitable:     parts.append(f"Q3 (Job Type): {vs.job_type_suitable}")
    if vs.interview_confirmed:   parts.append(f"Q4 (Interview): {vs.interview_confirmed}")
    if vs.expected_salary:       parts.append(f"Q5 (Salary): {vs.expected_salary}")
    if vs.candidate_questions:   parts.append(f"Q6 (Questions): {vs.candidate_questions}")
    full_transcript = "\n".join(parts)
    vs.full_transcript = full_transcript

    analysis = VoiceEngine.analyze_with_gemini(full_transcript, vs.job_title_at_time or "the role")

    vs.english_level      = analysis.get("english_level")
    vs.fluency_assessment = analysis.get("fluency_assessment")
    vs.clarity_assessment = analysis.get("clarity_assessment")
    vs.experience_match   = analysis.get("experience_match")
    vs.language_notes     = analysis.get("language_notes")
    vs.ai_summary         = analysis.get("ai_summary")
    vs.status             = "completed"
    vs.completed_at       = datetime.utcnow()

    db.commit()
    db.refresh(vs)
    return _screening_dict(vs)


@router.post("/{screening_id}/no-answer")
def no_answer(
    screening_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_company_or_admin(current_user)
    vs = db.query(models.VoiceScreening).filter(models.VoiceScreening.id == screening_id).first()
    if not vs:
        raise HTTPException(status_code=404, detail="Screening not found")
    vs.status = "no_answer"
    db.commit()
    return {"success": True}


@router.get("/candidate/{candidate_id}")
def get_candidate_screenings(
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_company_or_admin(current_user)
    rows = (
        db.query(models.VoiceScreening)
        .filter(models.VoiceScreening.candidate_id == candidate_id)
        .order_by(desc(models.VoiceScreening.attempt_number))
        .all()
    )
    return [_screening_dict(r) for r in rows]


@router.get("/application/{application_id}")
def get_application_screenings(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_company_or_admin(current_user)
    rows = (
        db.query(models.VoiceScreening)
        .filter(models.VoiceScreening.application_id == application_id)
        .order_by(desc(models.VoiceScreening.attempt_number))
        .all()
    )
    return [_screening_dict(r) for r in rows]


@router.get("/all")
def get_all_screenings(
    status: Optional[str] = Query(None),
    job_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="SuperAdmin only")

    q = db.query(models.VoiceScreening)
    if status:
        q = q.filter(models.VoiceScreening.status == status)
    if job_id:
        q = q.filter(models.VoiceScreening.job_id == job_id)
    if date_from:
        try:
            q = q.filter(models.VoiceScreening.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(models.VoiceScreening.created_at <= datetime.fromisoformat(date_to))
        except ValueError:
            pass

    total = q.count()
    rows = q.order_by(desc(models.VoiceScreening.created_at)).offset(skip).limit(limit).all()

    result = []
    for vs in rows:
        d = _screening_dict(vs)
        cand = vs.candidate
        app  = vs.application
        d["candidate_name"]  = (cand.name if cand else None) or (app.applicant_name if app else "—")
        d["candidate_email"] = (cand.email if cand else None) or (app.applicant_email if app else "")
        result.append(d)

    return {"total": total, "screenings": result}


# ── Recruiter polling ──────────────────────────────────────────────────────

@router.get("/{screening_id}")
def get_screening(
    screening_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_company_or_admin(current_user)
    vs = db.query(models.VoiceScreening).filter(models.VoiceScreening.id == screening_id).first()
    if not vs:
        raise HTTPException(status_code=404, detail="Screening not found")
    return _screening_dict(vs)


# ── Public token-based routes (no auth — candidate uses these) ────────────

@router.get("/session/{token}")
def get_session(token: str, db: Session = Depends(get_db)):
    vs = _lookup_by_token(token, db)
    if vs.token_used or vs.status == "completed":
        return {"error": "This screening session has already been completed"}
    questions = _build_questions(
        vs.job_title_at_time or "the role",
        vs.job_type_at_time or "Full-time",
        vs.interview_date_at_time,
        vs.interview_time_at_time,
    )
    cand = vs.candidate
    app  = vs.application
    cand_name = (cand.name if cand else None) or (app.applicant_name if app else "Candidate")
    return {
        "screening_id": vs.id,
        "candidate_name": cand_name,
        "job_title": vs.job_title_at_time,
        "job_type": vs.job_type_at_time,
        "interview_date": vs.interview_date_at_time,
        "interview_time": vs.interview_time_at_time,
        "questions": questions,
    }


@router.post("/session/{token}/save-answer")
def session_save_answer(token: str, data: SaveAnswerIn, db: Session = Depends(get_db)):
    vs = _lookup_by_token(token, db)
    if vs.token_used or vs.status == "completed":
        raise HTTPException(status_code=410, detail="Screening already completed")
    field_map = {
        1: "experience_response",
        2: "availability_response",
        3: "job_type_suitable",
        4: "interview_confirmed",
        5: "expected_salary",
        6: "candidate_questions",
    }
    field = field_map.get(data.question_number)
    if not field:
        raise HTTPException(status_code=400, detail="question_number must be 1-6")
    setattr(vs, field, data.transcript)
    if data.question_number == 6 and data.transcript.strip():
        vs.has_candidate_questions = True
    vs.status = "in_progress"
    db.commit()
    return {"success": True}


@router.post("/session/{token}/complete")
def session_complete(token: str, db: Session = Depends(get_db)):
    vs = _lookup_by_token(token, db)
    if vs.token_used or vs.status == "completed":
        raise HTTPException(status_code=410, detail="Screening already completed")

    parts = []
    if vs.experience_response:   parts.append(f"Q1 (Experience): {vs.experience_response}")
    if vs.availability_response: parts.append(f"Q2 (Availability): {vs.availability_response}")
    if vs.job_type_suitable:     parts.append(f"Q3 (Job Type): {vs.job_type_suitable}")
    if vs.interview_confirmed:   parts.append(f"Q4 (Interview): {vs.interview_confirmed}")
    if vs.expected_salary:       parts.append(f"Q5 (Salary): {vs.expected_salary}")
    if vs.candidate_questions:   parts.append(f"Q6 (Questions): {vs.candidate_questions}")
    vs.full_transcript = "\n".join(parts)

    analysis = VoiceEngine.analyze_with_gemini(vs.full_transcript, vs.job_title_at_time or "the role")
    vs.english_level      = analysis.get("english_level")
    vs.fluency_assessment = analysis.get("fluency_assessment")
    vs.clarity_assessment = analysis.get("clarity_assessment")
    vs.experience_match   = analysis.get("experience_match")
    vs.language_notes     = analysis.get("language_notes")
    vs.ai_summary         = analysis.get("ai_summary")
    vs.status             = "completed"
    vs.token_used         = True
    vs.completed_at       = datetime.utcnow()
    db.commit()
    return {"success": True, "screening_id": vs.id}
