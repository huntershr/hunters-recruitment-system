from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Any, Dict

from .. import models, database
from ..routers.auth import get_current_user

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _admin(current_user: models.User):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def _status(company: models.Company) -> str:
    return "approved" if company.is_approved else "pending"


def _norm_score(raw) -> int:
    if not raw:
        return 0
    n = float(raw)
    if n <= 1:
        return round(n * 100)
    if n <= 10:
        return round(n * 10)
    return round(min(100, n))


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
def get_admin_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    return {
        "total_companies": db.query(models.Company).count(),
        "pending_companies": db.query(models.Company).filter(models.Company.is_approved == False).count(),
        "approved_companies": db.query(models.Company).filter(models.Company.is_approved == True).count(),
        "total_jobs": db.query(models.Job).count(),
        "pending_jobs": db.query(models.Job).filter(models.Job.is_approved == False).count(),
        "approved_jobs": db.query(models.Job).filter(models.Job.is_approved == True).count(),
        "total_candidates": db.query(models.Candidate).count(),
        "screenings_today": 0,
        "total_users": db.query(models.User).count(),
        "active_users": db.query(models.User).filter(models.User.is_active == True).count(),
    }


# ── Companies ─────────────────────────────────────────────────────────────────

@router.get("/companies/full")
def get_all_companies_full(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    companies = db.query(models.Company).all()
    result = []
    for c in companies:
        user = db.query(models.User).filter(models.User.company_id == c.id).first()
        user_ids = [
            u.id for u in db.query(models.User).filter(models.User.company_id == c.id).all()
        ]
        job_count = (
            db.query(models.Job).filter(models.Job.owner_id.in_(user_ids)).count()
            if user_ids else 0
        )
        candidate_count = (
            db.query(models.Candidate).filter(models.Candidate.owner_id.in_(user_ids)).count()
            if user_ids else 0
        )
        result.append({
            "id": c.id,
            "name": c.company_name or "",
            "email": c.company_email or "",
            "website": c.company_website or "",
            "registration_number": c.registration_number or "",
            "industry": "",
            "phone": "",
            "country": "",
            "status": _status(c),
            "is_approved": c.is_approved,
            "created_at": c.created_at.isoformat() if c.created_at else "",
            "admin_user_id": user.id if user else None,
            "admin_email": user.email if user else "",
            "admin_name": user.full_name if user else "",
            "admin_is_active": user.is_active if user else False,
            "job_count": job_count,
            "candidate_count": candidate_count,
        })
    return result


@router.patch("/companies/{company_id}")
def admin_update_company(
    company_id: int,
    update_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if "name" in update_data:
        company.company_name = update_data["name"]
    if "email" in update_data:
        company.company_email = update_data["email"]
    if "website" in update_data:
        company.company_website = update_data["website"]
    if "registration_number" in update_data:
        company.registration_number = update_data["registration_number"]
    if "status" in update_data:
        s = update_data["status"]
        approved = s == "approved"
        company.is_approved = approved
        for u in db.query(models.User).filter(models.User.company_id == company_id).all():
            u.is_active = approved

    user = db.query(models.User).filter(models.User.company_id == company_id).first()
    if user:
        if update_data.get("admin_email"):
            user.email = update_data["admin_email"]
        if update_data.get("admin_name"):
            user.full_name = update_data["admin_name"]
        if update_data.get("new_password"):
            from ..auth_utils import get_password_hash
            user.hashed_password = get_password_hash(update_data["new_password"])
        if "is_active" in update_data:
            user.is_active = update_data["is_active"]

    db.commit()
    return {"message": "Company updated successfully"}


@router.delete("/companies/{company_id}")
def admin_delete_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    user_ids = [
        u.id for u in db.query(models.User).filter(models.User.company_id == company_id).all()
    ]
    if user_ids:
        candidate_ids = [
            c.id for c in db.query(models.Candidate)
            .filter(models.Candidate.owner_id.in_(user_ids)).all()
        ]
        if candidate_ids:
            db.query(models.Evaluation).filter(
                models.Evaluation.candidate_id.in_(candidate_ids)
            ).delete(synchronize_session=False)
        db.query(models.Candidate).filter(
            models.Candidate.owner_id.in_(user_ids)
        ).delete(synchronize_session=False)
        db.query(models.Job).filter(
            models.Job.owner_id.in_(user_ids)
        ).delete(synchronize_session=False)
        db.query(models.User).filter(
            models.User.company_id == company_id
        ).delete(synchronize_session=False)

    db.query(models.Company).filter(models.Company.id == company_id).delete(
        synchronize_session=False
    )
    db.commit()
    return {"message": "Company deleted"}


# ── Candidates ────────────────────────────────────────────────────────────────

@router.get("/candidates/full")
def get_all_candidates_full(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    candidates = db.query(models.Candidate).all()
    result = []
    for c in candidates:
        owner = db.query(models.User).filter(models.User.id == c.owner_id).first()
        company = (
            db.query(models.Company).filter(models.Company.id == owner.company_id).first()
            if owner and owner.company_id else None
        )
        job = (
            db.query(models.Job).filter(models.Job.id == c.job_applied).first()
            if c.job_applied else None
        )
        ev = db.query(models.Evaluation).filter(models.Evaluation.candidate_id == c.id).first()
        result.append({
            "id": c.id,
            "name": c.name or "",
            "email": c.email or "",
            "phone": c.phone or "",
            "last_title": c.last_title or "",
            "last_employer": c.last_employer or "",
            "years_exp": c.experience_years or 0,
            "expected_salary": c.expected_salary or "",
            "score": _norm_score(ev.score if ev else 0),
            "decision": (ev.decision if ev else "Pending") or "Pending",
            "reason": ev.reason if ev else "",
            "strengths": ev.strengths if ev else "",
            "weaknesses": ev.weaknesses if ev else "",
            "company_id": company.id if company else None,
            "company_name": company.company_name if company else "",
            "job_title": job.job_title if job else "",
            "owner_id": c.owner_id,
        })
    return result


@router.patch("/candidates/{candidate_id}")
def admin_update_candidate(
    candidate_id: int,
    update_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    for field in ["name", "email", "phone", "last_title", "last_employer"]:
        if field in update_data:
            setattr(candidate, field, update_data[field])
    if "years_exp" in update_data:
        candidate.experience_years = update_data["years_exp"]

    ev = db.query(models.Evaluation).filter(models.Evaluation.candidate_id == candidate_id).first()
    if ev:
        if "score" in update_data:
            s = float(update_data["score"])
            ev.score = s / 100.0 if s > 1 else s
        if "decision" in update_data:
            ev.decision = update_data["decision"]

    db.commit()
    return {"message": "Candidate updated"}


@router.delete("/candidates/{candidate_id}")
def admin_delete_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    db.query(models.Evaluation).filter(
        models.Evaluation.candidate_id == candidate_id
    ).delete(synchronize_session=False)
    db.delete(candidate)
    db.commit()
    return {"message": "Candidate deleted"}


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users/full")
def get_all_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    users = db.query(models.User).all()
    result = []
    for u in users:
        company = (
            db.query(models.Company).filter(models.Company.id == u.company_id).first()
            if u.company_id else None
        )
        user_type = "admin" if u.is_admin else ("company" if u.company_id else "candidate")
        result.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name or "",
            "user_type": user_type,
            "is_active": u.is_active,
            "is_admin": u.is_admin,
            "company_id": u.company_id,
            "company_name": company.company_name if company else "",
            "password_hash_preview": (u.hashed_password or "")[:30] + "...",
        })
    return result


@router.patch("/users/{user_id}")
def admin_update_user(
    user_id: int,
    update_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if "full_name" in update_data:
        user.full_name = update_data["full_name"]
    if "email" in update_data:
        user.email = update_data["email"]
    if "is_active" in update_data:
        user.is_active = update_data["is_active"]
    if update_data.get("new_password"):
        from ..auth_utils import get_password_hash
        user.hashed_password = get_password_hash(update_data["new_password"])
    db.commit()
    return {"message": "User updated"}


@router.delete("/users/{user_id}")
def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    db.delete(user)
    db.commit()
    return {"message": "User deleted"}


# ── Candidate Users (registered portal users + their applications) ────────────

@router.get("/candidate-users")
def get_candidate_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    users = db.query(models.User).filter(
        models.User.is_admin == False,
        models.User.company_id == None,
    ).all()

    result = []
    for u in users:
        cand = (
            db.query(models.Candidate)
            .filter(models.Candidate.email == u.email)
            .order_by(models.Candidate.id.desc())
            .first()
        )
        ev = (
            db.query(models.Evaluation)
            .filter(models.Evaluation.candidate_id == cand.id)
            .first()
            if cand else None
        )
        job = (
            db.query(models.Job).filter(models.Job.id == cand.job_applied).first()
            if cand else None
        )
        result.append({
            "user_id": u.id,
            "candidate_id": cand.id if cand else None,
            "name": u.full_name or (cand.name if cand else ""),
            "email": u.email,
            "phone": cand.phone if cand else "",
            "last_title": cand.last_title if cand else "",
            "last_employer": cand.last_employer if cand else "",
            "years_exp": cand.experience_years if cand else 0,
            "skills": cand.skills if cand else "",
            "education": cand.education if cand else "",
            "has_cv": bool(cand and cand.cv_text and cand.cv_text.strip()),
            "job_applied": cand.job_applied if cand else None,
            "job_title": job.job_title if job else "",
            "score": _norm_score(ev.score) if ev else None,
            "decision": ev.decision if ev else None,
            "reason": ev.reason if ev else "",
            "strengths": ev.strengths if ev else "",
            "weaknesses": ev.weaknesses if ev else "",
        })
    return result


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/analytics")
def get_admin_analytics(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)

    by_decision = db.query(
        models.Evaluation.decision,
        func.count(models.Evaluation.id).label("count"),
    ).group_by(models.Evaluation.decision).all()

    companies = db.query(models.Company).all()
    top = []
    for c in companies:
        user_ids = [
            u.id for u in db.query(models.User).filter(models.User.company_id == c.id).all()
        ]
        if user_ids:
            jc = db.query(models.Job).filter(models.Job.owner_id.in_(user_ids)).count()
            cc = db.query(models.Candidate).filter(models.Candidate.owner_id.in_(user_ids)).count()
            cand_ids = [
                x.id for x in db.query(models.Candidate)
                .filter(models.Candidate.owner_id.in_(user_ids)).all()
            ]
            if cand_ids:
                hc = db.query(models.Evaluation).filter(
                    models.Evaluation.candidate_id.in_(cand_ids),
                    models.Evaluation.decision == "Shortlist",
                ).count()
                avg_raw = db.query(func.avg(models.Evaluation.score)).filter(
                    models.Evaluation.candidate_id.in_(cand_ids)
                ).scalar()
                avg = _norm_score(avg_raw)
            else:
                hc = avg = 0
        else:
            jc = cc = hc = avg = 0
        top.append({
            "name": c.company_name,
            "job_count": jc,
            "candidate_count": cc,
            "shortlisted_count": hc,
            "avg_score": avg,
            "status": _status(c),
        })
    top.sort(key=lambda x: x["candidate_count"], reverse=True)

    return {
        "total_companies": db.query(models.Company).count(),
        "approved_companies": db.query(models.Company).filter(models.Company.is_approved == True).count(),
        "total_jobs": db.query(models.Job).count(),
        "approved_jobs": db.query(models.Job).filter(models.Job.is_approved == True).count(),
        "total_candidates": db.query(models.Candidate).count(),
        "total_users": db.query(models.User).count(),
        "active_users": db.query(models.User).filter(models.User.is_active == True).count(),
        "candidates_by_decision": [
            {"stage": r[0] or "Pending", "count": r[1]} for r in by_decision
        ],
        "top_companies": top[:10],
    }
