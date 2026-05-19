from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

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
