from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import ast

from .. import models, schemas, database
from ..routers.auth import get_current_user

router = APIRouter(tags=["Profile"])


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _require_admin(current_user: models.User):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def _build_profile(candidate: models.Candidate) -> dict:
    """Coerce JSONB lists to empty list when null."""
    return {
        "id": candidate.id,
        "name": candidate.name,
        "email": candidate.email,
        "phone": candidate.phone,
        "photo_url": candidate.photo_url,
        "summary": candidate.summary,
        "location": candidate.location,
        "experiences": candidate.experiences or [],
        "education_history": candidate.education_history or [],
        "languages": candidate.languages or [],
        "skills": candidate.skills,
        "education": candidate.education,
        "last_title": candidate.last_title,
        "last_employer": candidate.last_employer,
    }


# ── GET /api/candidate/profile ─────────────────────────────────────────────────

@router.get("/api/candidate/profile", response_model=schemas.ProfileResponse)
def get_candidate_profile(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    candidate = (
        db.query(models.Candidate)
        .filter(models.Candidate.user_id == current_user.id)
        .first()
    )
    if not candidate:
        raise HTTPException(
            status_code=404,
            detail="Profile not found. Apply to a job to create one.",
        )
    return _build_profile(candidate)


# ── PUT /api/candidate/profile ─────────────────────────────────────────────────

@router.put("/api/candidate/profile", response_model=schemas.ProfileResponse)
def update_candidate_profile(
    payload: schemas.ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    candidate = (
        db.query(models.Candidate)
        .filter(models.Candidate.user_id == current_user.id)
        .first()
    )
    if not candidate:
        raise HTTPException(
            status_code=404,
            detail="Profile not found. Apply to a job to create one.",
        )

    update_data = payload.model_dump(exclude_unset=True)

    # Reject attempts to mutate immutable fields
    for immutable in ("id", "email", "user_id"):
        if immutable in update_data:
            raise HTTPException(
                status_code=400,
                detail=f"Field '{immutable}' cannot be changed via this endpoint.",
            )

    for field, value in update_data.items():
        # Serialize Pydantic sub-objects to plain dicts for JSONB storage
        if isinstance(value, list):
            value = [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in value
            ]
        setattr(candidate, field, value)

    db.commit()
    db.refresh(candidate)
    return _build_profile(candidate)


# ── GET /api/admin/candidate/{candidate_id}/profile ────────────────────────────

@router.get(
    "/api/admin/candidate/{candidate_id}/profile",
    response_model=schemas.AdminProfileResponse,
)
def get_admin_candidate_profile(
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_admin(current_user)
    # TODO: company-scoped admin check (Phase 2+)

    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found.")

    profile = _build_profile(candidate)
    profile["user_id"] = candidate.user_id
    # candidates table has no created_at column — surface as null for now
    profile["registration_date"] = getattr(candidate, "created_at", None)
    return profile


# ── GET /api/candidate/has-applied/{job_id} ───────────────────────────────────

@router.get("/api/candidate/has-applied/{job_id}")
def has_applied(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    candidate = (
        db.query(models.Candidate)
        .filter(models.Candidate.user_id == current_user.id)
        .first()
    )
    if not candidate:
        return {"has_applied": False, "application_id": None}
    app = (
        db.query(models.Application)
        .filter(
            models.Application.candidate_id == candidate.id,
            models.Application.job_id == job_id,
        )
        .first()
    )
    return {
        "has_applied": app is not None,
        "application_id": app.id if app else None,
    }


# ── GET /api/candidate/application/{application_id}/summary ───────────────────

def _parse_list_field(raw) -> list:
    """Parse strengths/weaknesses TEXT column into a Python list.

    The column may contain a Python list literal ("['a', 'b']"),
    newline/bullet-separated text, or a plain string.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(i).strip() for i in raw if str(i).strip()]
    s = raw.strip()
    try:
        parsed = ast.literal_eval(s)
        if isinstance(parsed, list):
            return [str(i).strip() for i in parsed if str(i).strip()]
    except Exception:
        pass
    lines = [ln.lstrip("-• ").strip() for ln in s.split("\n") if ln.strip()]
    return lines if lines else [s]


def _reframe_weakness(text: str) -> str:
    negative = ("lacks", "missing", "does not", "no mention of")
    if any(text.lower().startswith(p) for p in negative):
        return f"Strengthen this area: {text}"
    return f"Consider building: {text}"


def _match_label_tier(score: float):
    if score >= 80:
        return "Strong Match", "strong"
    if score >= 60:
        return "Good Match", "good"
    return "Some Gaps", "gaps"


@router.get("/api/candidate/application/{application_id}/summary")
def get_application_summary(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    application = db.query(models.Application).filter(
        models.Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found.")

    # Authorization: must belong to this user's candidate profile
    if application.candidate_id is None:
        raise HTTPException(status_code=403, detail="You can only view your own applications.")
    candidate = db.query(models.Candidate).filter(
        models.Candidate.id == application.candidate_id
    ).first()
    if not candidate or candidate.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only view your own applications.")

    evaluation = db.query(models.Evaluation).filter(
        models.Evaluation.application_id == application_id
    ).first()
    if not evaluation:
        return {
            "status": "processing",
            "application_id": application_id,
            "message": "AI is still reviewing your application...",
        }

    # Job + company name
    job = db.query(models.Job).filter(models.Job.id == application.job_id).first()
    job_title = job.job_title if job else "Unknown Job"
    company_name = "Hunters HR Solutions"
    if job:
        owner = db.query(models.User).filter(models.User.id == job.owner_id).first()
        if owner and owner.company_id:
            company = db.query(models.Company).filter(
                models.Company.id == owner.company_id
            ).first()
            if company:
                company_name = company.company_name

    strengths_raw = _parse_list_field(evaluation.strengths)[:3]
    weaknesses_raw = _parse_list_field(evaluation.weaknesses)[:2]

    strengths = [s[:200] for s in strengths_raw]
    improvement_areas = [_reframe_weakness(w[:200]) for w in weaknesses_raw]

    score = float(evaluation.score or 0)
    match_label, match_tier = _match_label_tier(score)

    return {
        "status": "ready",
        "application_id": application_id,
        "job_title": job_title,
        "company_name": company_name,
        "match_label": match_label,
        "match_tier": match_tier,
        "strengths": strengths,
        "improvement_areas": improvement_areas,
        "stage": application.stage or "Applied",
        "submitted_at": application.created_at.isoformat() if application.created_at else None,
    }
