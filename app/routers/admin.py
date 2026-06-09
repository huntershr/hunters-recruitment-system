from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from typing import Any, Dict, Optional
from datetime import datetime
from pydantic import BaseModel
import io
import json

from .. import models, database
from ..routers.auth import get_current_user

SUPERADMIN_EMAIL = "hr@hunters-egypt.com"
VALID_STAGES = {"applied", "screening", "shortlisted", "interview", "offered", "hired", "rejected"}


class StageUpdateRequest(BaseModel):
    stage: str


class AdminJobPayload(BaseModel):
    company_id: int
    title: str
    location: Optional[str] = None
    description: Optional[str] = None
    experience_years: int = 0
    required_skills: str = ""
    nice_to_have_skills: Optional[str] = None
    behavioral_skills: Optional[str] = None
    education_level: Optional[str] = None
    salary_range: Optional[str] = None
    hide_salary: bool = False
    industry_experience: Optional[str] = None
    weight_experience: float = 0.40
    weight_skills: float = 0.30
    weight_education: float = 0.20
    weight_behavioral: float = 0.10


class PlanUpdateRequest(BaseModel):
    plan: str
    plan_expires_at: str = None   # ISO date string or null
    billing_status: str = "active"


class InviteUserRequest(BaseModel):
    full_name: str
    email: str
    password: str


def _build_stage_notifications(application, stage: str, db: Session) -> list:
    """Build notification payloads for a stage transition. Returns empty list for stages with no notifications."""
    job = application.job
    job_title = job.job_title if job else "Unknown Position"

    # Resolve company name and job owner email
    company_name = "Hunters HR Solutions"
    job_owner_email = None
    if job and job.owner_id:
        owner = db.query(models.User).filter(models.User.id == job.owner_id).first()
        if owner:
            job_owner_email = owner.email
            if owner.company_id:
                company = db.query(models.Company).filter(models.Company.id == owner.company_id).first()
                if company:
                    company_name = company.company_name

    # Resolve candidate contact info
    cand = application.candidate
    cand_name  = (cand.name  if cand else application.applicant_name)  or "Candidate"
    cand_email = (cand.email if cand else application.applicant_email) or ""
    cand_phone = (cand.phone if cand else application.applicant_phone) or ""

    # Resolve score
    ev = application.evaluation
    score_str = f"{_norm_score(ev.score)}%" if ev and ev.score is not None else "N/A"

    today = datetime.utcnow().strftime("%d %B %Y")
    notifs = []

    if stage == "screening":
        if job_owner_email:
            notifs.append({
                "to": job_owner_email,
                "subject": f"New Candidate in Screening — {job_title}",
                "body": (
                    f"Hi,\n\nA candidate has been moved to Screening for {job_title}.\n\n"
                    f"Candidate: {cand_name}\nEmail: {cand_email}\nPhone: {cand_phone}\n"
                    f"AI Score: {score_str}\n\nView their profile in your Hunters HR dashboard.\n\nHunters HR"
                ),
                "type": "company",
            })

    elif stage == "shortlisted":
        if cand_email:
            notifs.append({
                "to": cand_email,
                "subject": f"Your Application Update — {job_title} at {company_name}",
                "body": (
                    f"Dear {cand_name},\n\nWe're pleased to inform you that your application for "
                    f"{job_title} at {company_name} has been shortlisted.\n\n"
                    f"Our team will be in touch with next steps.\n\n"
                    f"Best regards,\nHunters HR\n{SUPERADMIN_EMAIL}"
                ),
                "type": "candidate",
            })

    elif stage == "interview":
        if cand_email:
            notifs.append({
                "to": cand_email,
                "subject": f"Interview Invitation — {job_title} at {company_name}",
                "body": (
                    f"Dear {cand_name},\n\nCongratulations! You have been selected for an interview "
                    f"for {job_title} at {company_name}.\n\n"
                    f"Our team will contact you shortly to schedule the interview.\n\n"
                    f"Best regards,\nHunters HR\n{SUPERADMIN_EMAIL}"
                ),
                "type": "candidate",
            })
        notifs.append({
            "to": SUPERADMIN_EMAIL,
            "subject": f"Interview Stage — {cand_name} for {job_title}",
            "body": (
                f"Candidate {cand_name} ({cand_email}, {cand_phone}) has been moved to Interview stage "
                f"for {job_title} at {company_name}.\n\nAI Score: {score_str}\n\n"
                f"Action needed: Schedule interview via Phase 9."
            ),
            "type": "superadmin",
        })

    elif stage == "offered":
        if cand_email:
            notifs.append({
                "to": cand_email,
                "subject": f"Offer Extended — {job_title} at {company_name}",
                "body": (
                    f"Dear {cand_name},\n\nWe are delighted to inform you that {company_name} would like "
                    f"to extend an offer for the position of {job_title}.\n\n"
                    f"Hunters HR will be in touch with the details shortly.\n\n"
                    f"Best regards,\nHunters HR"
                ),
                "type": "candidate",
            })

    elif stage == "hired":
        if job_owner_email:
            notifs.append({
                "to": job_owner_email,
                "subject": f"Candidate Hired — {cand_name} for {job_title}",
                "body": (
                    f"Congratulations! {cand_name} has been successfully hired for {job_title}.\n\n"
                    f"Hunters HR has closed this application.\n\n"
                    f"Thank you for using Hunters HR Solutions."
                ),
                "type": "company",
            })
        notifs.append({
            "to": SUPERADMIN_EMAIL,
            "subject": f"Placement Confirmed — {cand_name} at {company_name}",
            "body": (
                f"{cand_name} has been hired for {job_title} at {company_name}.\n\n"
                f"Date: {today}\nAI Score: {score_str}\n\n"
                f"This placement has been recorded in analytics."
            ),
            "type": "superadmin",
        })

    # applied / rejected → no automatic notifications
    return notifs

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
    from sqlalchemy import text
    _admin(current_user)
    row = db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM companies) AS total_companies,
            (SELECT COUNT(*) FROM companies WHERE is_approved = false OR is_approved IS NULL) AS pending_companies,
            (SELECT COUNT(*) FROM companies WHERE is_approved = true) AS approved_companies,
            (SELECT COUNT(*) FROM jobs WHERE (status IS NULL OR status != 'rejected')) AS total_jobs,
            (SELECT COUNT(*) FROM jobs WHERE is_approved = false AND (status IS NULL OR status != 'rejected')) AS pending_jobs,
            (SELECT COUNT(*) FROM jobs WHERE is_approved = true AND (status IS NULL OR status != 'rejected')) AS approved_jobs,
            (SELECT COUNT(*) FROM candidates) AS total_candidates,
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM users WHERE is_active = true) AS active_users
    """)).fetchone()
    return {
        "total_companies":   row.total_companies,
        "pending_companies": row.pending_companies,
        "approved_companies": row.approved_companies,
        "total_jobs":        row.total_jobs,
        "pending_jobs":      row.pending_jobs,
        "approved_jobs":     row.approved_jobs,
        "total_candidates":  row.total_candidates,
        "screenings_today":  0,
        "total_users":       row.total_users,
        "active_users":      row.active_users,
    }


# ── Companies ─────────────────────────────────────────────────────────────────

@router.get("/companies/full")
def get_all_companies_full(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from sqlalchemy import text
    _admin(current_user)
    rows = db.execute(text("""
        SELECT
            c.id,
            c.company_name,
            c.company_email,
            c.company_website,
            c.registration_number,
            c.is_approved,
            c.created_at,
            c.plan,
            c.selected_plan,
            c.billing_status,
            c.plan_expires_at,
            c.logo_url,
            MIN(u.id)         AS admin_user_id,
            MIN(u.email)      AS admin_email,
            MIN(u.full_name)  AS admin_name,
            COALESCE(BOOL_OR(u.is_active), false) AS admin_is_active,
            COUNT(DISTINCT j.id) FILTER (WHERE j.status IS NULL OR j.status != 'rejected') AS job_count,
            COUNT(DISTINCT ca.id)  AS candidate_count,
            COUNT(DISTINCT ap.id)  AS application_count,
            MAX(ap.created_at)     AS last_activity
        FROM companies c
        LEFT JOIN users u  ON u.company_id = c.id
        LEFT JOIN jobs j   ON j.owner_id   = u.id
        LEFT JOIN candidates ca ON ca.owner_id = u.id
        LEFT JOIN applications ap ON ap.job_id = j.id
        GROUP BY c.id, c.company_name, c.company_email, c.company_website,
                 c.registration_number, c.is_approved, c.created_at,
                 c.plan, c.selected_plan, c.billing_status, c.plan_expires_at, c.logo_url
        ORDER BY c.created_at DESC
    """)).fetchall()
    return [
        {
            "id": r.id,
            "name": r.company_name or "",
            "email": r.company_email or "",
            "website": r.company_website or "",
            "registration_number": r.registration_number or "",
            "industry": "",
            "phone": "",
            "country": "",
            "status": "approved" if r.is_approved else "pending",
            "is_approved": r.is_approved,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "admin_user_id": r.admin_user_id,
            "admin_email": r.admin_email or "",
            "admin_name": r.admin_name or "",
            "admin_is_active": r.admin_is_active,
            "job_count": r.job_count or 0,
            "candidate_count": r.candidate_count or 0,
            "applications_count": r.application_count or 0,
            "last_activity_at": r.last_activity.isoformat() if r.last_activity else None,
            "plan": r.plan or "free",
            "plan_expires_at": r.plan_expires_at.isoformat() if r.plan_expires_at else None,
            "billing_status": r.billing_status or "active",
            "logo_url": r.logo_url or None,
        }
        for r in rows
    ]


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


@router.get("/companies/{company_id}/overview")
def get_company_overview(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Full company stats for SuperAdmin 'Enter Company' workspace."""
    _admin(current_user)
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    users = db.query(models.User).filter(models.User.company_id == company_id).all()
    admin_user = next((u for u in users if u.is_admin), users[0] if users else None)
    user_ids = [u.id for u in users]

    jobs = db.query(models.Job).filter(models.Job.owner_id.in_(user_ids), or_(models.Job.status == None, models.Job.status != 'rejected')).all() if user_ids else []
    job_ids = [j.id for j in jobs]

    apps = (
        db.query(models.Application).filter(models.Application.job_id.in_(job_ids)).all()
        if job_ids else []
    )
    app_ids = [a.id for a in apps]

    interviews_count = (
        db.query(models.Interview).filter(models.Interview.application_id.in_(app_ids)).count()
        if app_ids else 0
    )
    candidates_count = db.query(models.Candidate).filter(
        models.Candidate.owner_id.in_(user_ids)
    ).count() if user_ids else 0

    stage_counts: Dict[str, int] = {}
    for a in apps:
        s = (a.stage or "New").capitalize()
        stage_counts[s] = stage_counts.get(s, 0) + 1

    return {
        "id": company.id,
        "name": company.company_name or "",
        "email": company.company_email or "",
        "website": company.company_website or "",
        "registration_number": company.registration_number or "",
        "status": _status(company),
        "is_approved": company.is_approved,
        "created_at": company.created_at.isoformat() if company.created_at else "",
        "plan": getattr(company, "plan", None) or "free",
        "plan_expires_at": company.plan_expires_at.isoformat() if getattr(company, "plan_expires_at", None) else None,
        "billing_status": getattr(company, "billing_status", None) or "active",
        "admin_email": admin_user.email if admin_user else "",
        "admin_name": admin_user.full_name if admin_user else "",
        "admin_is_active": admin_user.is_active if admin_user else False,
        "job_count": len(jobs),
        "approved_job_count": sum(1 for j in jobs if j.is_approved),
        "candidate_count": candidates_count,
        "applications_count": len(apps),
        "interviews_count": interviews_count,
        "pipeline": stage_counts,
        "logo_url": getattr(company, "logo_url", None) or None,
        "recent_jobs": [
            {
                "id": j.id,
                "job_title": j.job_title,
                "is_approved": j.is_approved,
                "created_at": j.created_at.isoformat() if j.created_at else "",
            }
            for j in sorted(jobs, key=lambda x: x.created_at or datetime(2000, 1, 1), reverse=True)[:5]
        ],
    }


@router.get("/companies/{company_id}/users")
def list_company_users(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List all users belonging to a company."""
    _admin(current_user)
    users = db.query(models.User).filter(
        models.User.company_id == company_id,
        models.User.is_admin == False,
    ).order_by(models.User.id).all()
    return [
        {
            "id": u.id,
            "full_name": u.full_name or "",
            "email": u.email,
            "is_active": u.is_active,
            "created_at": None,
        }
        for u in users
    ]


@router.post("/companies/{company_id}/invite-user")
def invite_company_user(
    company_id: int,
    payload: InviteUserRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Add a second user to a company (max 2 per company)."""
    _admin(current_user)
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    existing_count = db.query(models.User).filter(
        models.User.company_id == company_id,
        models.User.is_admin == False,
    ).count()
    if existing_count >= 2:
        raise HTTPException(status_code=400, detail="Maximum 2 users allowed per company")

    email = payload.email.strip().lower()
    if db.query(models.User).filter(func.lower(models.User.email) == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    from ..auth_utils import get_password_hash
    new_user = models.User(
        email=email,
        full_name=payload.full_name.strip(),
        hashed_password=get_password_hash(payload.password),
        company_id=company_id,
        is_active=True,
        is_admin=False,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Send welcome email via SendGrid if configured
    import os
    sg_key = os.getenv("SENDGRID_API_KEY", "")
    if sg_key:
        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail, From, To, Subject, HtmlContent
            html = f"""
            <div style="font-family:'Segoe UI',Arial,sans-serif;background:#F5F6F8;padding:40px 0;margin:0">
              <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
                <div style="background:#1B2A4A;padding:28px 32px;text-align:center">
                  <div style="color:#C9A84C;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin-bottom:6px">Hunters HR</div>
                  <div style="color:#fff;font-size:22px;font-weight:600">Welcome to {company.company_name}</div>
                </div>
                <div style="padding:32px">
                  <p style="color:#1B2A4A;font-size:15px;margin:0 0 16px">Hi {payload.full_name},</p>
                  <p style="color:#555;font-size:14px;margin:0 0 16px">You have been added as a user for <strong>{company.company_name}</strong> on the Hunters HR platform.</p>
                  <p style="color:#555;font-size:14px;margin:0 0 8px"><strong>Email:</strong> {email}</p>
                  <p style="color:#555;font-size:14px;margin:0 0 28px"><strong>Password:</strong> {payload.password}</p>
                  <div style="text-align:center;margin-bottom:28px">
                    <a href="https://app.hunters-egypt.com" style="background:#C9A84C;color:#1B2A4A;text-decoration:none;padding:14px 32px;border-radius:8px;font-weight:600;font-size:15px;display:inline-block">Login to Dashboard</a>
                  </div>
                </div>
              </div>
            </div>"""
            msg = Mail(
                from_email=From("hr@hunters-egypt.com", "Hunters HR"),
                to_emails=To(email),
                subject=Subject(f"Welcome to {company.company_name} on Hunters HR"),
                html_content=HtmlContent(html)
            )
            SendGridAPIClient(sg_key).send(msg)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Welcome email failed for {email}: {e}")

    return {"id": new_user.id, "email": new_user.email, "full_name": new_user.full_name, "is_active": True}


@router.delete("/companies/{company_id}/users/{user_id}")
def deactivate_company_user(
    company_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Deactivate (not delete) a company user."""
    _admin(current_user)
    user = db.query(models.User).filter(
        models.User.id == user_id,
        models.User.company_id == company_id,
        models.User.is_admin == False,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()
    return {"message": f"User {user.email} deactivated"}


@router.patch("/companies/{company_id}/plan")
def update_company_plan(
    company_id: int,
    payload: PlanUpdateRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SuperAdmin-only: update a company's plan and billing status."""
    _admin(current_user)
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    company.plan = payload.plan
    company.billing_status = payload.billing_status
    if payload.plan_expires_at:
        try:
            company.plan_expires_at = datetime.fromisoformat(payload.plan_expires_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid plan_expires_at format")
    else:
        company.plan_expires_at = None

    db.commit()
    return {
        "message": "Plan updated",
        "plan": company.plan,
        "billing_status": company.billing_status,
        "plan_expires_at": company.plan_expires_at.isoformat() if company.plan_expires_at else None,
    }


# ── Candidates ────────────────────────────────────────────────────────────────

@router.get("/candidates/full")
def get_all_candidates_full(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    candidates = (
        db.query(models.Candidate)
        .options(
            joinedload(models.Candidate.owner).joinedload(models.User.company),
            joinedload(models.Candidate.job),
        )
        .limit(200)
        .all()
    )
    candidate_ids = [c.id for c in candidates]
    eval_map = {
        e.candidate_id: e
        for e in db.query(models.Evaluation)
        .filter(models.Evaluation.candidate_id.in_(candidate_ids))
        .all()
    }
    result = []
    for c in candidates:
        owner   = c.owner
        company = owner.company if owner else None
        job     = c.job
        ev      = eval_map.get(c.id)
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
    users = (
        db.query(models.User)
        .options(
            joinedload(models.User.company),
            joinedload(models.User.candidate_profile),
        )
        .limit(200)
        .all()
    )
    result = []
    for u in users:
        company   = u.company
        user_type = "admin" if u.is_admin else ("company" if u.company_id else "candidate")
        candidate = u.candidate_profile if user_type == "candidate" else None
        result.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name or "",
            "user_type": user_type,
            "is_active": u.is_active,
            "is_admin": u.is_admin,
            "company_id": u.company_id,
            "company_name": company.company_name if company else "",
            "candidate_id": candidate.id if candidate else None,
            "has_cv": bool(candidate and ((candidate.cv_file_data) or (candidate.cv_text and candidate.cv_text.strip()))) if candidate else False,
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
        # Prefer Candidate linked by user_id (portal profile); fall back to email match
        cand = (
            db.query(models.Candidate)
            .filter(models.Candidate.user_id == u.id)
            .order_by(models.Candidate.id.desc())
            .first()
        ) or (
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


# ── Talent Pool (Type A candidates — accessible to all admin roles) ──────────

@router.get("/talent-pool")
def get_talent_pool(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    All registered (Type A) candidates — user_id IS NOT NULL.
    Accessible to both SuperAdmin and Company Admin.
    No company scoping: the talent pool is intentionally shared.
    """
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    candidates = (
        db.query(models.Candidate)
        .filter(models.Candidate.user_id.isnot(None))
        .order_by(models.Candidate.id.desc())
        .all()
    )

    result = []
    for cand in candidates:
        user = (
            db.query(models.User).filter(models.User.id == cand.user_id).first()
            if cand.user_id else None
        )
        result.append({
            "user_id": cand.user_id,
            "candidate_id": cand.id,
            "name": cand.name or (user.full_name if user else "") or "",
            "email": cand.email or (user.email if user else "") or "",
            "phone": cand.phone or "",
            "last_title": cand.last_title or "",
            "last_employer": cand.last_employer or "",
            "years_exp": cand.experience_years,
            "skills": cand.skills or "",
            "photo_url": cand.photo_url or "",
            "summary": cand.summary or "",
            "location": cand.location or "",
        })
    return result


# ── Applications (Phase 3 — unified view: Type A + Type B) ───────────────────

@router.get("/applications")
def list_admin_applications(
    skip: int = 0,
    limit: int = 50,
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Unified application list for admin UI.
    SuperAdmin: all applications, or scoped to ?company_id=X for workspace view.
    CompanyAdmin: applications for jobs owned by users in their company.
    Returns both Type A (candidate_id set) and Type B (applicant_* fields).
    """
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Company admin or super admin access required")

    # Build a job_id filter subquery for company scoping
    if current_user.is_admin:
        if company_id is not None:
            co_user_ids = (
                db.query(models.User.id)
                .filter(models.User.company_id == company_id)
                .subquery()
            )
            co_job_ids = (
                db.query(models.Job.id)
                .filter(models.Job.owner_id.in_(co_user_ids))
                .subquery()
            )
            job_id_filter = models.Application.job_id.in_(co_job_ids)
        else:
            job_id_filter = None
    else:
        co_user_ids = (
            db.query(models.User.id)
            .filter(models.User.company_id == current_user.company_id)
            .subquery()
        )
        co_job_ids = (
            db.query(models.Job.id)
            .filter(models.Job.owner_id.in_(co_user_ids))
            .subquery()
        )
        job_id_filter = models.Application.job_id.in_(co_job_ids)

    # Total count (separate query — no joinedload inflation)
    count_q = db.query(func.count(models.Application.id))
    if job_id_filter is not None:
        count_q = count_q.filter(job_id_filter)
    total_count = count_q.scalar()

    # Fetch with eager loads to avoid N+1
    fetch_q = (
        db.query(models.Application)
        .options(
            joinedload(models.Application.candidate),
            joinedload(models.Application.evaluation),
            joinedload(models.Application.job)
            .joinedload(models.Job.owner)
            .joinedload(models.User.company),
        )
        .order_by(models.Application.id.desc())
    )
    if job_id_filter is not None:
        fetch_q = fetch_q.filter(job_id_filter)

    applications = fetch_q.offset(skip).limit(limit).all()

    # Bulk-fetch most recent active interview per application for Schedule/Reschedule button state
    _app_ids = [a.id for a in applications]
    _iv_map = {}
    _vs_map = {}
    if _app_ids:
        _ivs = (
            db.query(models.Interview)
            .filter(
                models.Interview.application_id.in_(_app_ids),
                models.Interview.status != 'cancelled',
            )
            .order_by(models.Interview.application_id, models.Interview.created_at.desc())
            .all()
        )
        for _iv in _ivs:
            if _iv.application_id not in _iv_map:
                _iv_map[_iv.application_id] = _iv

        # Bulk-fetch most recent voice screening per application
        try:
            _vss = (
                db.query(models.VoiceScreening)
                .filter(models.VoiceScreening.application_id.in_(_app_ids))
                .order_by(models.VoiceScreening.application_id, models.VoiceScreening.attempt_number.desc())
                .all()
            )
            for _vs in _vss:
                if _vs.application_id not in _vs_map:
                    _vs_map[_vs.application_id] = _vs
        except Exception:
            pass

    result = []
    for app in applications:
        job = app.job
        candidate = app.candidate      # None for Type B
        evaluation = app.evaluation    # None if still processing

        # Company name via job → owner → company
        company_name = "Hunters HR Solutions"
        if job and job.owner and job.owner.company:
            company_name = job.owner.company.company_name

        # Name / email / phone: prefer candidate profile, fall back to applicant_* fields
        name  = (candidate.name  if candidate else None) or app.applicant_name  or ""
        email = (candidate.email if candidate else None) or app.applicant_email or ""
        phone = (candidate.phone if candidate else None) or app.applicant_phone or ""

        # Normalize score → 0-100 float or None
        score = None
        if evaluation and evaluation.score is not None:
            raw = float(evaluation.score)
            if raw <= 1:
                score = round(raw * 100, 1)
            elif raw <= 10:
                score = round(raw * 10, 1)
            else:
                score = round(min(100.0, raw), 1)

        # Interview questions: stored as JSON string or already a list
        iq = None
        if evaluation and evaluation.suggested_interview_questions is not None:
            iq_raw = evaluation.suggested_interview_questions
            if isinstance(iq_raw, str):
                try:
                    iq = json.loads(iq_raw)
                except Exception:
                    iq = []
            elif isinstance(iq_raw, list):
                iq = iq_raw
            else:
                iq = []

        result.append({
            "application_id": app.id,
            "job_id": app.job_id,
            "job_title": job.job_title if job else "",
            "company_name": company_name,
            "candidate_id": candidate.id if candidate else None,
            "candidate_type": "registered" if (candidate and candidate.user_id) else "external",
            "name": name,
            "email": email,
            "phone": phone,
            "skills": candidate.skills if candidate else None,
            "experience_years": candidate.experience_years if candidate else None,
            "last_title": candidate.last_title if candidate else None,
            "cv_available": bool(
                (candidate and candidate.cv_file_data) or
                app.cv_file_data or
                (candidate and candidate.cv_text and candidate.cv_text.strip()) or
                (app.cv_text and app.cv_text.strip())
            ),
            "score": score,
            "decision": evaluation.decision if evaluation else None,
            "strengths": evaluation.strengths if evaluation else None,
            "weaknesses": evaluation.weaknesses if evaluation else None,
            "suggested_interview_questions": iq,
            "reason": evaluation.reason if evaluation else None,
            "stage": app.stage or "Applied",
            "applied_at": app.created_at.isoformat() if app.created_at else None,
            "evaluation_id": evaluation.id if evaluation else None,
            "weight_experience": round((job.weight_experience or 0) * 100) if job else None,
            "weight_skills":     round((job.weight_skills or 0) * 100)     if job else None,
            "weight_education":  round((job.weight_education or 0) * 100)  if job else None,
            "weight_behavioral": round((job.weight_behavioral or 0) * 100) if job else None,
            "score_experience": round(evaluation.score_experience) if evaluation and evaluation.score_experience is not None else None,
            "score_skills":     round(evaluation.score_skills)     if evaluation and evaluation.score_skills     is not None else None,
            "score_education":  round(evaluation.score_education)  if evaluation and evaluation.score_education  is not None else None,
            "score_behavioral": round(evaluation.score_behavioral) if evaluation and evaluation.score_behavioral is not None else None,
            "interview": (lambda _iv: {
                "id": _iv.id,
                "interview_date": str(_iv.interview_date),
                "interview_time": str(_iv.interview_time)[:5],
                "duration_minutes": _iv.duration_minutes,
                "location_type": _iv.location_type,
                "location_value": _iv.location_value,
                "interviewer_names": _iv.interviewer_names,
                "notes_for_candidate": _iv.notes_for_candidate,
                "internal_notes": _iv.internal_notes,
                "status": _iv.status,
            } if _iv else None)(_iv_map.get(app.id)),
            "voice_screening": (lambda _vs: {
                "id": _vs.id,
                "status": _vs.status,
                "attempt_number": _vs.attempt_number,
                "english_level": _vs.english_level,
                "ai_summary": _vs.ai_summary,
                "completed_at": _vs.completed_at.isoformat() if _vs.completed_at else None,
                "fluency_assessment": _vs.fluency_assessment,
                "clarity_assessment": _vs.clarity_assessment,
                "experience_match": _vs.experience_match,
                "language_notes": _vs.language_notes,
                "availability_response": _vs.availability_response,
                "job_type_suitable": _vs.job_type_suitable,
                "interview_confirmed": _vs.interview_confirmed,
                "expected_salary": _vs.expected_salary,
                "has_candidate_questions": _vs.has_candidate_questions,
                "full_transcript": _vs.full_transcript,
            } if _vs else None)(_vs_map.get(app.id)),
        })

    return {"total_count": total_count, "applications": result}


@router.get("/candidate/{candidate_id}/profile")
def get_candidate_ats_profile(
    candidate_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Full ATS profile for a registered candidate, including JSONB fields and application history."""
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    apps = (
        db.query(models.Application)
        .options(joinedload(models.Application.job), joinedload(models.Application.evaluation))
        .filter(models.Application.candidate_id == candidate_id)
        .order_by(models.Application.created_at.desc())
        .all()
    )
    app_list = [
        {
            "application_id": app.id,
            "job_title": app.job.job_title if app.job else None,
            "stage": app.stage,
            "decision": app.evaluation.decision if app.evaluation else None,
            "score": app.evaluation.score if app.evaluation else None,
            "applied_at": app.created_at.isoformat() if app.created_at else None,
        }
        for app in apps
    ]

    return {
        "id": candidate.id,
        "name": candidate.name,
        "email": candidate.email,
        "phone": candidate.phone,
        "photo_url": candidate.photo_url,
        "summary": candidate.summary,
        "location": candidate.location,
        "last_title": candidate.last_title,
        "last_employer": candidate.last_employer,
        "experience_years": candidate.experience_years,
        "expected_salary": candidate.expected_salary,
        "skills": candidate.skills,
        "education": candidate.education,
        "experiences": candidate.experiences or [],
        "education_history": candidate.education_history or [],
        "languages": candidate.languages or [],
        "applications": app_list,
    }


@router.patch("/applications/{application_id}/stage")
def update_application_stage(
    application_id: int,
    payload: StageUpdateRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Move an application to a new pipeline stage and return notification payloads."""
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    stage_lower = payload.stage.lower().strip()
    if stage_lower not in VALID_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage. Must be one of: {', '.join(sorted(VALID_STAGES))}",
        )

    application = (
        db.query(models.Application)
        .options(
            joinedload(models.Application.job),
            joinedload(models.Application.candidate),
            joinedload(models.Application.evaluation),
        )
        .filter(models.Application.id == application_id)
        .first()
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    # Company admin: scoped to their company's jobs only
    if not current_user.is_admin:
        job = application.job
        if not job or not job.owner_id:
            raise HTTPException(status_code=403, detail="Access denied")
        owner = db.query(models.User).filter(models.User.id == job.owner_id).first()
        if not owner or owner.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Access denied")

    application.stage = stage_lower.capitalize()
    if hasattr(models.Application, "stage_updated_at"):
        application.stage_updated_at = datetime.utcnow()
    db.commit()
    db.refresh(application)

    notifications = _build_stage_notifications(application, stage_lower, db)

    return {
        "application_id": application.id,
        "stage": application.stage,
        "notifications": notifications,
    }


@router.get("/applications/{application_id}/cv")
def download_application_cv(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Download CV for any application — Type A (from candidate.cv_text) or
    Type B (from application.cv_text stored at submit time).
    Auth: SuperAdmin always allowed; CompanyAdmin scoped to their company's jobs.
    """
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    application = (
        db.query(models.Application)
        .options(joinedload(models.Application.candidate), joinedload(models.Application.job))
        .filter(models.Application.id == application_id)
        .first()
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    # Company admin: verify the application belongs to one of their company's jobs
    if not current_user.is_admin:
        job = application.job
        allowed = False
        if job:
            owner = db.query(models.User).filter(
                models.User.id == job.owner_id,
                models.User.company_id == current_user.company_id,
            ).first()
            allowed = owner is not None
        if not allowed:
            raise HTTPException(status_code=403, detail="Access denied")

    # Try to get cv_text and display name from candidate (Type A) first
    candidate = application.candidate
    display_name = (
        (candidate.name if candidate else None)
        or application.applicant_name
        or "Applicant"
    )
    safe_name = "".join(c for c in display_name if c.isalnum() or c in " _-").strip().replace(" ", "_")

    from .candidates import _build_cv_pdf, _mime_to_ext, _NO_CACHE
    from fastapi import Response as _Response

    # Serve original uploaded file if available (BYTEA)
    cv_file_data = (candidate.cv_file_data if candidate else None) or application.cv_file_data
    cv_file_mime_str = (candidate.cv_file_mime if candidate else None) or application.cv_file_mime
    if cv_file_data:
        ext = _mime_to_ext(cv_file_mime_str or "application/pdf")
        return _Response(
            content=bytes(cv_file_data),
            media_type=cv_file_mime_str or "application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_CV{ext}"', **_NO_CACHE},
        )

    # Fall back to rebuilding PDF from cv_text
    cv_text = (candidate.cv_text if candidate else None) or application.cv_text
    if not cv_text or not cv_text.strip():
        raise HTTPException(
            status_code=404,
            detail="CV not available for this application — text was not stored at submission time",
        )

    from types import SimpleNamespace
    cv_obj = SimpleNamespace(
        name=display_name,
        email=(candidate.email if candidate else None) or application.applicant_email or "",
        phone=(candidate.phone if candidate else None) or application.applicant_phone or "",
        last_title=candidate.last_title if candidate else None,
        last_employer=candidate.last_employer if candidate else None,
        experience_years=candidate.experience_years if candidate else None,
        cv_text=cv_text,
    )
    try:
        pdf_bytes = _build_cv_pdf(cv_obj)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="CV_{safe_name}.pdf"', **_NO_CACHE},
    )


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


# ── Admin Job Management ───────────────────────────────────────────────────────

def _job_to_dict(j: models.Job) -> dict:
    return {
        "id": j.id,
        "job_title": j.job_title or "",
        "job_description": j.job_description or "",
        "job_location": j.job_location or "",
        "min_experience": j.min_experience or 0,
        "required_skills": j.required_skills or "",
        "nice_to_have_skills": j.nice_to_have_skills or "",
        "behavioral_skills": j.behavioral_skills or "",
        "education_level": j.education_level or "",
        "salary_range": j.salary_range or "",
        "hide_salary": bool(j.hide_salary),
        "industry_experience": j.industry_experience or "",
        "is_approved": bool(j.is_approved),
        "created_at": j.created_at.isoformat() if j.created_at else "",
        "owner_id": j.owner_id,
    }


@router.get("/jobs")
def list_admin_jobs(
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List all jobs as admin, optionally scoped to a company."""
    _admin(current_user)
    q = db.query(models.Job).filter(
        or_(models.Job.status == None, models.Job.status != 'rejected')
    )
    if company_id is not None:
        co_user_ids = (
            db.query(models.User.id).filter(models.User.company_id == company_id).subquery()
        )
        q = q.filter(models.Job.owner_id.in_(co_user_ids))
    jobs = q.order_by(models.Job.id.desc()).all()
    return [_job_to_dict(j) for j in jobs]


@router.post("/jobs")
def admin_create_job(
    payload: AdminJobPayload,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a job as admin on behalf of a company (auto-approved)."""
    _admin(current_user)
    owner = db.query(models.User).filter(models.User.company_id == payload.company_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="No user found for this company")
    job = models.Job(
        owner_id=owner.id,
        job_title=payload.title,
        job_description=payload.description or "",
        job_location=payload.location or "",
        min_experience=payload.experience_years,
        required_skills=payload.required_skills,
        nice_to_have_skills=payload.nice_to_have_skills,
        behavioral_skills=payload.behavioral_skills,
        education_level=payload.education_level or "Not specified",
        salary_range=payload.salary_range or "",
        hide_salary=payload.hide_salary,
        industry_experience=payload.industry_experience,
        weight_experience=payload.weight_experience,
        weight_skills=payload.weight_skills,
        weight_education=payload.weight_education,
        weight_behavioral=payload.weight_behavioral,
        is_approved=True,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_to_dict(job)


@router.put("/jobs/{job_id}")
def admin_update_job(
    job_id: int,
    payload: AdminJobPayload,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Update any job as admin."""
    _admin(current_user)
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.job_title = payload.title
    job.job_description = payload.description or ""
    job.job_location = payload.location or ""
    job.min_experience = payload.experience_years
    job.required_skills = payload.required_skills
    job.nice_to_have_skills = payload.nice_to_have_skills
    job.behavioral_skills = payload.behavioral_skills
    job.education_level = payload.education_level or "Not specified"
    job.salary_range = payload.salary_range or ""
    job.hide_salary = payload.hide_salary
    job.industry_experience = payload.industry_experience
    job.weight_experience = payload.weight_experience
    job.weight_skills = payload.weight_skills
    job.weight_education = payload.weight_education
    job.weight_behavioral = payload.weight_behavioral
    db.commit()
    db.refresh(job)
    return _job_to_dict(job)


@router.delete("/jobs/{job_id}")
def admin_delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete any job as admin, cascading to applications and evaluations."""
    _admin(current_user)
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    app_ids = [
        a.id for a in db.query(models.Application.id).filter(models.Application.job_id == job_id).all()
    ]
    if app_ids:
        db.query(models.Evaluation).filter(
            models.Evaluation.application_id.in_(app_ids)
        ).delete(synchronize_session=False)
        db.query(models.Application).filter(
            models.Application.job_id == job_id
        ).delete(synchronize_session=False)

    db.query(models.Candidate).filter(
        models.Candidate.job_applied == job_id
    ).delete(synchronize_session=False)

    db.query(models.Job).filter(models.Job.id == job_id).delete(synchronize_session=False)
    db.commit()
    return {"message": "Job deleted"}
