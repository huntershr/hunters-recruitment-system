from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload, defer
from sqlalchemy import func, or_
from typing import Any, Dict, Optional
from datetime import datetime
from pydantic import BaseModel
import io
import json
import logging

from .. import models, database
from ..routers.auth import get_current_user
from ..services.ai_evaluator import evaluate_candidate, finalize_evaluation, extract_candidate_info, call_agent_screener

_logger = logging.getLogger(__name__)

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
    department: Optional[str] = "Other"
    weight_experience: float = 0.40
    weight_skills: float = 0.30
    weight_education: float = 0.20
    weight_behavioral: float = 0.10
    agent_weight_title: int = 25
    agent_weight_industry: int = 25
    agent_weight_experience: int = 25
    agent_weight_skills: int = 25
    essential_skills: Optional[list] = None


class PlanUpdateRequest(BaseModel):
    plan: str
    plan_expires_at: str = None   # ISO date string or null
    billing_status: str = "active"
    extra_jobs_count: int = 0


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
            c.extra_jobs_count,
            c.contact_phone,
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
                 c.plan, c.selected_plan, c.billing_status, c.plan_expires_at, c.logo_url, c.extra_jobs_count, c.contact_phone
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
            "phone": r.contact_phone or "",
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
            "extra_jobs_count": r.extra_jobs_count or 0,
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

    # Aggregate stage counts and total in one query — avoids loading all Application rows
    if job_ids:
        stage_rows = (
            db.query(models.Application.stage, func.count(models.Application.id))
            .filter(models.Application.job_id.in_(job_ids))
            .group_by(models.Application.stage)
            .all()
        )
        stage_counts: Dict[str, int] = {}
        for stage_val, cnt in stage_rows:
            stage_counts[(stage_val or "New").capitalize()] = cnt
        candidates_count = sum(stage_counts.values())

        interviews_count = (
            db.query(func.count(models.Interview.id))
            .filter(
                models.Interview.application_id.in_(
                    db.query(models.Application.id)
                    .filter(models.Application.job_id.in_(job_ids))
                    .scalar_subquery()
                )
            )
            .scalar() or 0
        )
    else:
        stage_counts = {}
        candidates_count = 0
        interviews_count = 0

    return {
        "id": company.id,
        "name": company.company_name or "",
        "email": company.company_email or "",
        "website": company.company_website or "",
        "registration_number": company.registration_number or "",
        "contact_phone": company.contact_phone or "",
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
        "applications_count": candidates_count,
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
    company.extra_jobs_count = payload.extra_jobs_count or 0
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
        "extra_jobs_count": company.extra_jobs_count or 0,
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

    # Collect application IDs before deletion so we can cascade their children
    app_ids = [
        a.id for a in db.query(models.Application.id)
        .filter(models.Application.candidate_id == candidate_id).all()
    ]

    if app_ids:
        db.query(models.Evaluation).filter(
            models.Evaluation.application_id.in_(app_ids)
        ).delete(synchronize_session=False)
        db.query(models.Interview).filter(
            models.Interview.application_id.in_(app_ids)
        ).delete(synchronize_session=False)
        db.query(models.VoiceScreening).filter(
            models.VoiceScreening.application_id.in_(app_ids)
        ).delete(synchronize_session=False)
        db.query(models.Offer).filter(
            models.Offer.application_id.in_(app_ids)
        ).delete(synchronize_session=False)
        db.query(models.Application).filter(
            models.Application.candidate_id == candidate_id
        ).delete(synchronize_session=False)

    # Delete evaluations linked directly by candidate_id (no application row)
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
            joinedload(models.User.candidate_profile).options(
                defer(models.Candidate.cv_file_data)
            ),
        )
        .order_by(models.User.is_admin.desc(), models.User.company_id.isnot(None).desc(), models.User.id.asc())
        .limit(2000)
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
            "has_cv": bool(candidate and (candidate.cv_file_mime or candidate.cv_text)) if candidate else False,
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
    try:
        n_apps = n_evals = n_cands = 0
        candidates = db.query(models.Candidate).filter(
            or_(models.Candidate.user_id == user_id, models.Candidate.owner_id == user_id)
        ).all()
        for cand in candidates:
            cand_emails = {e for e in [cand.email] if e}
            ev_del = db.query(models.Evaluation).filter(
                models.Evaluation.candidate_id == cand.id
            ).delete(synchronize_session=False)
            n_evals += ev_del
            app_q = db.query(models.Application).filter(
                or_(
                    models.Application.candidate_id == cand.id,
                    models.Application.applicant_email.in_(cand_emails) if cand_emails else False,
                )
            )
            n_apps += app_q.delete(synchronize_session=False)
            db.delete(cand)
            n_cands += 1
        db.flush()
        db.delete(user)
        db.commit()
        _logger.info(
            "Deleted user %s: %d candidate(s), %d application(s), %d evaluation(s)",
            user_id, n_cands, n_apps, n_evals,
        )
    except Exception as exc:
        db.rollback()
        _logger.error("Cascade delete failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete user — rolled back")
    return {"message": "User deleted", "deleted": {"candidates": n_cands, "applications": n_apps, "evaluations": n_evals}}


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
    job_id: Optional[int] = None,
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

    # Narrow to a specific job when requested
    if job_id is not None:
        specific = models.Application.job_id == job_id
        job_id_filter = specific if job_id_filter is None else (job_id_filter & specific)

    # Total count (separate query — no joinedload inflation)
    count_q = db.query(func.count(models.Application.id))
    if job_id_filter is not None:
        count_q = count_q.filter(job_id_filter)
    total_count = count_q.scalar()

    # Fetch with eager loads to avoid N+1
    fetch_q = (
        db.query(models.Application)
        .options(
            defer(models.Application.cv_file_data),
            joinedload(models.Application.candidate).options(
                defer(models.Candidate.cv_file_data)
            ),
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
        # Agent scores are already 0-100 (finalize_evaluation normalizes them before storage).
        # Legacy Gemini scores (source=NULL) may be on a 0-10 scale and need ×10.
        score = None
        if evaluation and evaluation.score is not None:
            raw = float(evaluation.score)
            _ds = evaluation.dimension_scores if isinstance(evaluation.dimension_scores, dict) else {}
            if _ds.get('source') == 'agent':
                score = round(min(100.0, max(0.0, raw)), 1)
            elif raw <= 1:
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
            "photo_url": candidate.photo_url if candidate else None,
            "cv_available": bool(
                (candidate and (candidate.cv_file_mime or candidate.cv_text)) or
                (app.cv_file_mime or app.cv_text)
            ),
            "score": score,
            "decision": evaluation.decision if evaluation else None,
            "strengths": evaluation.strengths if evaluation else None,
            "weaknesses": evaluation.weaknesses if evaluation else None,
            "suggested_interview_questions": iq,
            "reason": evaluation.reason if evaluation else None,
            "summary_en": evaluation.summary_en if evaluation else None,
            "summary_ar": evaluation.summary_ar if evaluation else None,
            "strengths_ar": evaluation.strengths_ar if evaluation else None,
            "gaps_en": evaluation.gaps_en if evaluation else None,
            "gaps_ar": evaluation.gaps_ar if evaluation else None,
            "interview_questions_ar": evaluation.interview_questions_ar if evaluation else None,
            "quick_facts": evaluation.quick_facts if evaluation else None,
            "dimension_scores": evaluation.dimension_scores if evaluation else None,
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


@router.post("/rescreen-pending")
def rescreen_pending(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SuperAdmin only.
    Pass 1 — create missing Evaluation rows (max 10) for applications that have none.
    Pass 2 — re-run Gemini for existing evaluations whose decision is NULL or 'pending'.
    """
    if not current_user.is_admin or current_user.email != SUPERADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="SuperAdmin only")

    _lstr = lambda v: "\n".join(f"- {x}" for x in v if x) if isinstance(v, list) else str(v or "")

    def _save_result(result: dict, ev: models.Evaluation) -> None:
        """Write all evaluation fields from a Gemini result dict onto an Evaluation ORM object."""
        _bd3 = result.get("score_breakdown") or {}
        ev.score = result.get("score", 0.0)
        ev.score_experience = _bd3.get("experience")
        ev.score_skills = _bd3.get("skills")
        ev.score_education = _bd3.get("education")
        ev.score_behavioral = _bd3.get("behavioral")
        ev.decision = result.get("decision", "Reject")
        ev.reason = result.get("summary_en") or result.get("reason", "")
        ev.strengths = _lstr(result.get("strengths_en") or result.get("strengths") or [])
        ev.weaknesses = _lstr(result.get("gaps_en") or result.get("weaknesses") or [])
        ev.suggested_interview_questions = (
            result.get("interview_questions_en") or result.get("suggested_interview_questions") or []
        )
        ev.summary_en = result.get("summary_en")
        ev.summary_ar = result.get("summary_ar")
        ev.strengths_ar = _lstr(result.get("strengths_ar") or [])
        ev.gaps_en = _lstr(result.get("gaps_en") or [])
        ev.gaps_ar = _lstr(result.get("gaps_ar") or [])
        ev.interview_questions_ar = result.get("interview_questions_ar")
        ev.quick_facts = result.get("quick_facts")
        ev.dimension_scores = result.get("dimension_scores")

    # ── Pass 1: INSERT missing evaluations (max 10 per call) ─────────────────
    apps_missing = (
        db.query(models.Application)
        .outerjoin(
            models.Evaluation,
            models.Evaluation.application_id == models.Application.id,
        )
        .filter(
            models.Evaluation.id == None,
            models.Application.candidate_id != None,
        )
        .limit(10)
        .all()
    )

    created = 0
    failed = 0

    for app in apps_missing:
        try:
            candidate = db.query(models.Candidate).filter(models.Candidate.id == app.candidate_id).first()
            job = db.query(models.Job).filter(models.Job.id == app.job_id).first()

            if not candidate or not job or not (candidate.cv_text or "").strip():
                failed += 1
                continue

            _ar = call_agent_screener(candidate.cv_text or "", job, candidate.id)
            if _ar is not None:
                _cp = _ar.pop("_candidate_profile", None) or {}
                if _cp and candidate:
                    if not candidate.name or candidate.name.lower().startswith("resume"):
                        v = (_cp.get("name") or "").strip()
                        # Reject if contains non-printable chars or no letters (icon/symbol fallback)
                        if v and v.isprintable() and any(c.isalpha() for c in v) and len(v) <= 80:
                            candidate.name = v
                    if not candidate.last_title:
                        v = (_cp.get("current_title") or "").strip()
                        # Reject if longer than 80 chars — it's a paragraph, not a title
                        if v and len(v) <= 80:
                            candidate.last_title = v
                    if not candidate.last_employer:
                        v = (_cp.get("last_employer") or "").strip()
                        # Reject if starts with digits (date bleed) or too long
                        if v and len(v) <= 100 and not v[0].isdigit():
                            candidate.last_employer = v
                    if not (candidate.experience_years or 0):
                        v = _cp.get("years_experience")
                        if v:
                            try:
                                yr = int(v)
                                # Cap at 30 — anything higher is likely a birth-year calculation
                                if 1 <= yr <= 25: candidate.experience_years = yr
                            except: pass
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
                result = _ar
            else:
                result = finalize_evaluation(evaluate_candidate(job, candidate))
            db_eval = models.Evaluation(
                application_id=app.id,
                candidate_id=app.candidate_id,
                job_id=app.job_id,
            )
            _save_result(result, db_eval)
            db.add(db_eval)
            db.commit()
            created += 1
        except Exception as e:
            db.rollback()
            _logger.error(f"Create eval failed for application {app.id}: {e}")
            failed += 1

    # ── Pass 2: UPDATE evaluations with no score yet (never touches completed evals) ────
    # Guard: score IS NULL means the row exists but was never actually scored.
    # Evaluations with score > 0 are complete — do not re-screen them.
    pending_evals = (
        db.query(models.Evaluation)
        .filter(
            models.Evaluation.score.is_(None),
            or_(
                models.Evaluation.decision == None,
                models.Evaluation.decision == "pending",
                models.Evaluation.decision == "Pending Review",
            )
        )
        .all()
    )

    rescreened = 0

    for ev in pending_evals:
        try:
            candidate = db.query(models.Candidate).filter(models.Candidate.id == ev.candidate_id).first()
            job = db.query(models.Job).filter(models.Job.id == ev.job_id).first()

            if not candidate or not job or not (candidate.cv_text or "").strip():
                failed += 1
                continue

            _ar = call_agent_screener(candidate.cv_text or "", job, candidate.id)
            if _ar is not None:
                _cp = _ar.pop("_candidate_profile", None) or {}
                if _cp and candidate:
                    if not candidate.name or candidate.name.lower().startswith("resume"):
                        v = (_cp.get("name") or "").strip()
                        # Reject if contains non-printable chars or no letters (icon/symbol fallback)
                        if v and v.isprintable() and any(c.isalpha() for c in v) and len(v) <= 80:
                            candidate.name = v
                    if not candidate.last_title:
                        v = (_cp.get("current_title") or "").strip()
                        # Reject if longer than 80 chars — it's a paragraph, not a title
                        if v and len(v) <= 80:
                            candidate.last_title = v
                    if not candidate.last_employer:
                        v = (_cp.get("last_employer") or "").strip()
                        # Reject if starts with digits (date bleed) or too long
                        if v and len(v) <= 100 and not v[0].isdigit():
                            candidate.last_employer = v
                    if not (candidate.experience_years or 0):
                        v = _cp.get("years_experience")
                        if v:
                            try:
                                yr = int(v)
                                # Cap at 30 — anything higher is likely a birth-year calculation
                                if 1 <= yr <= 25: candidate.experience_years = yr
                            except: pass
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
                result = _ar
            else:
                result = finalize_evaluation(evaluate_candidate(job, candidate))
            _save_result(result, ev)
            db.commit()
            rescreened += 1
        except Exception as e:
            db.rollback()
            _logger.error(f"Rescreen failed for eval {ev.id}: {e}")
            failed += 1

    # ── Pass 3: Extract ATS profiles for candidates with empty profiles ─────────
    candidates_empty = (
        db.query(models.Candidate)
        .filter(
            models.Candidate.last_title.is_(None),
            models.Candidate.cv_text.isnot(None),
            models.Candidate.cv_text != "",
        )
        .limit(10)
        .all()
    )

    profiles_extracted = 0
    profiles_failed = 0

    for cand in candidates_empty:
        try:
            info = extract_candidate_info(cand.cv_text)
            if not (
                info.get("last_title") or info.get("skills")
                or info.get("summary") or info.get("experiences")
            ):
                _logger.warning(f"extract_candidate_info returned empty result for candidate {cand.id}")
                profiles_failed += 1
                continue
            if not cand.last_title:
                cand.last_title = info.get("last_title") or None
            if not cand.last_employer:
                cand.last_employer = info.get("last_employer") or None
            if not cand.skills:
                cand.skills = info.get("skills") or None
            if not cand.education:
                cand.education = info.get("education") or None
            if not cand.summary:
                cand.summary = info.get("summary") or None
            if not cand.experiences:
                cand.experiences = info.get("experiences") or None
            if not cand.education_history:
                cand.education_history = info.get("education_history") or None
            if not cand.languages:
                cand.languages = info.get("languages") or None
            if not cand.experience_years or cand.experience_years == 0:
                cand.experience_years = int(info.get("experience_years") or 0)
            db.commit()
            profiles_extracted += 1
            _logger.info(f"ATS profile extracted for candidate {cand.id}")
        except Exception as e:
            db.rollback()
            _logger.error(f"ATS extraction failed for candidate {cand.id}: {e}")
            profiles_failed += 1

    return {
        "created": created,
        "rescreened": rescreened,
        "failed": failed,
        "profiles_extracted": profiles_extracted,
        "profiles_failed": profiles_failed,
        "apps_missing_eval_found": len(apps_missing),
        "profiles_empty_found": len(candidates_empty),
    }


@router.post("/rescreen/{application_id}")
def rescreen_single(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SuperAdmin only. Re-run the agent screener for a single application."""
    if not current_user.is_admin or current_user.email != SUPERADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="SuperAdmin only")

    app = db.query(models.Application).filter(models.Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    candidate = db.query(models.Candidate).filter(models.Candidate.id == app.candidate_id).first()
    job = db.query(models.Job).filter(models.Job.id == app.job_id).first()

    if not candidate or not job:
        raise HTTPException(status_code=404, detail="Candidate or job not found")
    if not (candidate.cv_text or "").strip():
        raise HTTPException(status_code=422, detail="No CV text available for this candidate")

    _lstr = lambda v: "\n".join(f"- {x}" for x in v if x) if isinstance(v, list) else str(v or "")

    try:
        _ar = call_agent_screener(candidate.cv_text, job, candidate.id)
        if _ar is not None:
            _cp = _ar.pop("_candidate_profile", None) or {}
            if _cp:
                if not candidate.name or candidate.name.lower().startswith("resume"):
                    v = (_cp.get("name") or "").strip()
                    if v and v.isprintable() and any(c.isalpha() for c in v) and len(v) <= 80:
                        candidate.name = v
                if not candidate.last_title:
                    v = (_cp.get("current_title") or "").strip()
                    if v and len(v) <= 80:
                        candidate.last_title = v
                if not candidate.last_employer:
                    v = (_cp.get("last_employer") or "").strip()
                    if v and len(v) <= 100 and not v[0].isdigit():
                        candidate.last_employer = v
                if not (candidate.experience_years or 0):
                    v = _cp.get("years_experience")
                    if v:
                        try:
                            yr = int(v)
                            if 1 <= yr <= 25:
                                candidate.experience_years = yr
                        except Exception:
                            pass
            result = _ar
        else:
            result = finalize_evaluation(evaluate_candidate(job, candidate))

        ev = db.query(models.Evaluation).filter(models.Evaluation.application_id == application_id).first()
        if not ev:
            ev = models.Evaluation(
                application_id=application_id,
                candidate_id=app.candidate_id,
                job_id=app.job_id,
            )
            db.add(ev)

        _bd3 = result.get("score_breakdown") or {}
        ev.score = result.get("score", 0.0)
        ev.score_experience = _bd3.get("experience")
        ev.score_skills = _bd3.get("skills")
        ev.score_education = _bd3.get("education")
        ev.score_behavioral = _bd3.get("behavioral")
        ev.decision = result.get("decision", "Reject")
        ev.reason = result.get("summary_en") or result.get("reason", "")
        ev.strengths = _lstr(result.get("strengths_en") or result.get("strengths") or [])
        ev.weaknesses = _lstr(result.get("gaps_en") or result.get("weaknesses") or [])
        ev.suggested_interview_questions = (
            result.get("interview_questions_en") or result.get("suggested_interview_questions") or []
        )
        ev.summary_en = result.get("summary_en")
        ev.summary_ar = result.get("summary_ar")
        ev.strengths_ar = _lstr(result.get("strengths_ar") or [])
        ev.gaps_en = _lstr(result.get("gaps_en") or [])
        ev.gaps_ar = _lstr(result.get("gaps_ar") or [])
        ev.interview_questions_ar = result.get("interview_questions_ar")
        ev.quick_facts = result.get("quick_facts")
        ev.dimension_scores = result.get("dimension_scores")
        db.commit()

        return {
            "status": "rescreened",
            "application_id": application_id,
            "score": ev.score,
            "decision": ev.decision,
            "reason": ev.reason,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        _logger.error(f"rescreen_single failed for application {application_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Rescreen failed: {e}")


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
async def download_application_cv(
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
    from .candidates import _build_cv_pdf, _mime_to_ext, _NO_CACHE, _safe, get_cv_signed_url
    from fastapi import Response as _Response

    safe_name = _safe("".join(c for c in display_name if c.isalnum() or c in " _-").strip().replace(" ", "_")) or "Applicant"

    # Storage-first: new uploads have cv_url pointing to Supabase Storage
    cv_url = (candidate.cv_url if candidate else None) or application.cv_url
    if cv_url:
        signed_url = await get_cv_signed_url(cv_url)
        if signed_url:
            return RedirectResponse(url=signed_url)

    # Fallback: serve original uploaded file (BYTEA — existing candidates before Storage migration)
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


# ── Screening Report PDF ──────────────────────────────────────────────────────

def _parse_bullet_field(value) -> str:
    """Convert list/JSON/Python-repr/newline string to reportlab Paragraph-safe bullet text."""
    import json as _j, ast as _ast
    if not value:
        return '—'
    if isinstance(value, list):
        items = [str(i).strip().strip("'\"") for i in value if i]
        return '<br/>'.join(f'• {item}' for item in items if item) or '—'
    if isinstance(value, str):
        val = value.strip()
        if val.startswith('['):
            try:
                parsed = _j.loads(val)
                items = [str(i).strip() for i in parsed if i]
                return '<br/>'.join(f'• {item}' for item in items if item) or '—'
            except Exception:
                pass
            try:
                parsed = _ast.literal_eval(val)
                if isinstance(parsed, list):
                    return '<br/>'.join(f'• {str(i).strip()}' for i in parsed if i) or '—'
            except Exception:
                pass
        lines = [l.lstrip('-• ').strip() for l in val.split('\n') if l.strip()]
        if lines:
            return '<br/>'.join(f'• {l}' for l in lines)
        return val or '—'
    return str(value) or '—'


_amiri_registered = False


def _render_arabic(text: str) -> str:
    """Reshape Arabic glyphs + apply bidi visual ordering for reportlab LTR rendering."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:
        return str(text)


def generate_screening_report_pdf(candidate_data: dict, evaluation_data: dict, company_name: str = "") -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from io import BytesIO
    from datetime import datetime
    import os

    global _amiri_registered
    if not _amiri_registered:
        try:
            _font_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'fonts')
            _amiri_path = os.path.join(_font_dir, 'Amiri-Regular.ttf')
            if os.path.exists(_amiri_path):
                pdfmetrics.registerFont(TTFont('Amiri', _amiri_path))
                _amiri_registered = True
        except Exception:
            pass
    arabic_font = 'Amiri' if _amiri_registered else 'Helvetica'

    buffer = BytesIO()

    NAVY  = colors.HexColor('#1B2A4A')
    GOLD  = colors.HexColor('#C9A84C')
    LIGHT = colors.HexColor('#F5F6F8')
    GRAY  = colors.HexColor('#6B7280')
    BORDER = colors.HexColor('#E0E2E6')
    RED   = colors.HexColor('#E24B4A')
    GREEN = colors.HexColor('#16A34A')
    AMBER = colors.HexColor('#D97706')
    WHITE = colors.white

    raw = evaluation_data.get('score', 0)
    score_pct = _norm_score(raw)
    decision  = str(evaluation_data.get('decision') or '').upper()

    if score_pct >= 65:
        dec_color, score_border = GREEN, GREEN
    elif score_pct >= 40:
        dec_color, score_border = AMBER, AMBER
    else:
        dec_color, score_border = RED, RED

    doc = SimpleDocTemplate(buffer, pagesize=A4,
        rightMargin=18*mm, leftMargin=18*mm,
        topMargin=12*mm, bottomMargin=12*mm)
    W = A4[0] - 36*mm
    story = []

    # ── HEADER ──
    logo_path = '/app/frontend/hunters-logo-transparent.png'
    if not os.path.exists(logo_path):
        logo_path = '/app/frontend/hunters-logo-white.jpeg'
    if not os.path.exists(logo_path):
        logo_path = '/app/frontend/hunters-logo-blue.jpeg'

    try:
        logo = Image(logo_path, width=42*mm, height=15*mm)
    except Exception:
        logo = Paragraph('<b>HUNTERS HR</b>', ParagraphStyle('lh',
            fontName='Helvetica-Bold', fontSize=12, textColor=GOLD))

    s_title = ParagraphStyle('ht', fontName='Helvetica', fontSize=8,
        textColor=GOLD, leading=11)
    s_co    = ParagraphStyle('hc', fontName='Helvetica', fontSize=11,
        textColor=WHITE, leading=14)
    s_conf  = ParagraphStyle('hcf', fontName='Helvetica-Bold', fontSize=9,
        textColor=GOLD, alignment=TA_RIGHT)
    s_date  = ParagraphStyle('hd', fontName='Helvetica', fontSize=8,
        textColor=colors.HexColor('#9CA3AF'), alignment=TA_RIGHT)

    hdr = Table([[
        logo,
        [Paragraph('CANDIDATE SCREENING REPORT', s_title),
         Paragraph(company_name or 'Hunters HR', s_co)],
        [Paragraph('Confidential', s_conf),
         Paragraph(datetime.now().strftime('%d %b %Y'), s_date)]
    ]], colWidths=[48*mm, W - 98*mm, 50*mm])
    hdr.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), NAVY),
        ('BACKGROUND',    (0, 0), (0,  0),  NAVY),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (0,  0),  4*mm),
        ('RIGHTPADDING',  (0, 0), (0,  0),  2*mm),
        ('LEFTPADDING',   (1, 0), (1,  0),  4*mm),
        ('RIGHTPADDING',  (2, 0), (2,  0),  5*mm),
        ('TOPPADDING',    (0, 0), (-1, -1), 5*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5*mm),
    ]))
    story.append(hdr)

    # ── CANDIDATE INFO + SCORE ──
    s_name = ParagraphStyle('cn', fontName='Helvetica-Bold', fontSize=14, textColor=NAVY, spaceAfter=6)
    s_sub  = ParagraphStyle('cs', fontName='Helvetica',      fontSize=10, textColor=GRAY, spaceBefore=2)
    s_lbl  = ParagraphStyle('cl', fontName='Helvetica',      fontSize=7,
        textColor=colors.HexColor('#9CA3AF'))
    s_val  = ParagraphStyle('cv', fontName='Helvetica-Bold', fontSize=10, textColor=NAVY)

    name = candidate_data.get('name', '—')

    def info_box(lbl, val):
        t = Table([[Paragraph(lbl, s_lbl)], [Paragraph(str(val), s_val)]],
                  colWidths=[(W * 0.68) / 2 - 3*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), LIGHT),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING',   (0, 0), (-1, -1), 7),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 7),
            ('ROUNDEDCORNERS', [4]),
        ]))
        return t

    info_grid = Table([
        [info_box('PHONE',          candidate_data.get('phone', '—')),
         info_box('EXPERIENCE',     f"{candidate_data.get('experience_years', 0)} yrs")],
        [info_box('SCREENING DATE', datetime.now().strftime('%d %b %Y')),
         info_box('SOURCE',         candidate_data.get('source', '—'))],
    ], colWidths=[(W * 0.68) / 2 - 2*mm, (W * 0.68) / 2 - 2*mm], spaceBefore=4)

    s_sc_lbl = ParagraphStyle('scl', fontName='Helvetica', fontSize=7,
        textColor=colors.HexColor('#9CA3AF'), alignment=TA_CENTER)
    s_sc_num = ParagraphStyle('scn', fontName='Helvetica-Bold', fontSize=36,
        textColor=dec_color, alignment=TA_CENTER, leading=40)
    s_dec    = ParagraphStyle('scd', fontName='Helvetica-Bold', fontSize=11,
        textColor=dec_color, alignment=TA_CENTER)

    score_block = Table([
        [Paragraph('AI SCORE', s_sc_lbl)],
        [Paragraph(str(score_pct), s_sc_num)],
        [Paragraph(decision or 'N/A', s_dec)],
    ], colWidths=[W * 0.28], rowHeights=[8*mm, 16*mm, 8*mm])
    score_block.setStyle(TableStyle([
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))

    left_col = Table([
        [[Paragraph(name, s_name), Paragraph(candidate_data.get('job_title', '—'), s_sub)]],
        [info_grid],
    ], colWidths=[W * 0.68])

    info_row = Table([[left_col, score_block]], colWidths=[W * 0.70, W * 0.30])
    info_row.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 4*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4*mm),
        ('LINEBEFORE',    (1, 0), (1,  0),  0.5, BORDER),
        ('LINEBELOW',     (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    story.append(info_row)

    # ── COMPETENCY SCORING ──
    s_sec = ParagraphStyle('sec', fontName='Helvetica-Bold', fontSize=8,
        textColor=NAVY, spaceBefore=4*mm, spaceAfter=2*mm)
    s_bar_lbl = ParagraphStyle('bl', fontName='Helvetica', fontSize=9,
        textColor=colors.HexColor('#4B5563'))

    story.append(Paragraph('COMPETENCY SCORING', s_sec))

    metrics = [
        ('Experience', evaluation_data.get('weight_experience', 40), evaluation_data.get('score_experience', 0)),
        ('Skills',     evaluation_data.get('weight_skills',     30), evaluation_data.get('score_skills',     0)),
        ('Education',  evaluation_data.get('weight_education',  20), evaluation_data.get('score_education',  0)),
        ('Behavioral', evaluation_data.get('weight_behavioral', 10), evaluation_data.get('score_behavioral', 0)),
    ]

    for label, weight, raw_s in metrics:
        s_pct_val = _norm_score(raw_s)
        bar_color = GREEN if s_pct_val >= 65 else AMBER if s_pct_val >= 40 else RED
        fill_w  = max(W * 0.50 * s_pct_val / 100, 1)
        empty_w = max(W * 0.50 - fill_w, 0.5)

        filled = Table([['']], colWidths=[fill_w],  rowHeights=[5])
        filled.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), bar_color),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))

        empty = Table([['']], colWidths=[empty_w], rowHeights=[5])
        empty.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), BORDER),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))

        pct_s = ParagraphStyle('ps', fontName='Helvetica-Bold', fontSize=9,
            textColor=bar_color, alignment=TA_RIGHT)

        row = Table([[
            Paragraph(f"{label} ({weight}%)", s_bar_lbl),
            Table([[[filled, empty]]], colWidths=[W * 0.50]),
            Paragraph(f"{s_pct_val}%", pct_s),
        ]], colWidths=[W * 0.22, W * 0.54, W * 0.24])
        row.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(row)

    story.append(HRFlowable(width=W, thickness=0.5, color=BORDER, spaceAfter=2*mm, spaceBefore=3*mm))

    # ── ANALYSIS & REASONING ──
    story.append(Paragraph('ANALYSIS &amp; REASONING', s_sec))
    s_body = ParagraphStyle('body', fontName='Helvetica', fontSize=10,
        textColor=colors.HexColor('#4B5563'), leading=16)

    reasoning_tbl = Table([[
        Paragraph(evaluation_data.get('reasoning', '—'), s_body),
    ]], colWidths=[W])
    reasoning_tbl.setStyle(TableStyle([
        ('LINEBEFORE',    (0, 0), (0, -1), 3, GOLD),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(reasoning_tbl)
    story.append(HRFlowable(width=W, thickness=0.5, color=BORDER, spaceAfter=2*mm, spaceBefore=4*mm))

    # ── STRENGTHS & GAPS ──
    s_str = ParagraphStyle('str', fontName='Helvetica-Bold', fontSize=8,
        textColor=NAVY, spaceAfter=2*mm)

    sg = Table([
        [Paragraph('STRENGTHS', s_str), Paragraph('AREAS TO IMPROVE', s_str)],
        [Paragraph(evaluation_data.get('strengths', '—'), s_body),
         Paragraph(evaluation_data.get('gaps', '—'),      s_body)],
    ], colWidths=[W / 2 - 3*mm, W / 2 - 3*mm])
    sg.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LINEAFTER',    (0, 0), (0, -1),  0.5, BORDER),
        ('RIGHTPADDING', (0, 0), (0, -1),  4*mm),
        ('LEFTPADDING',  (1, 0), (1, -1),  4*mm),
    ]))
    story.append(sg)

    # ── INTERVIEW QUESTIONS ──
    iq_en = evaluation_data.get('interview_questions_en', [])
    iq_ar = evaluation_data.get('interview_questions_ar', [])

    if iq_en or iq_ar:
        story.append(HRFlowable(width=W, thickness=0.5, color=BORDER,
            spaceAfter=2*mm, spaceBefore=4*mm))
        story.append(Paragraph('SUGGESTED INTERVIEW QUESTIONS', s_sec))

        if iq_en:
            s_q = ParagraphStyle('q', fontName='Helvetica', fontSize=10,
                textColor=colors.HexColor('#4B5563'), leading=16, leftIndent=10, spaceAfter=4)
            for i, q in enumerate(iq_en if isinstance(iq_en, list) else [iq_en], 1):
                story.append(Paragraph(f"{i}. {q}", s_q))

        if iq_ar:
            story.append(Spacer(1, 3*mm))
            s_ar_hdr = ParagraphStyle('arh', fontName='Helvetica-Bold', fontSize=8,
                textColor=NAVY, spaceAfter=2*mm)
            story.append(Paragraph('INTERVIEW QUESTIONS (ARABIC)', s_ar_hdr))
            s_q_ar = ParagraphStyle('qar', fontName=arabic_font, fontSize=11,
                textColor=colors.HexColor('#4B5563'), leading=20, rightIndent=10,
                spaceAfter=6, alignment=TA_RIGHT)
            for i, q in enumerate(iq_ar if isinstance(iq_ar, list) else [iq_ar], 1):
                rendered = _render_arabic(f"{i}. {q}") if arabic_font == 'Amiri' else f"{i}. {q}"
                story.append(Paragraph(rendered, s_q_ar))

    # ── FOOTER ──
    story.append(Spacer(1, 5*mm))
    s_ft   = ParagraphStyle('ft',  fontName='Helvetica', fontSize=8,
        textColor=colors.HexColor('#9CA3AF'))
    s_ft_r = ParagraphStyle('ftr', fontName='Helvetica', fontSize=8,
        textColor=colors.HexColor('#9CA3AF'), alignment=TA_RIGHT)

    footer = Table([[
        Paragraph('Powered by Hunters HR · hr@hunters-egypt.com', s_ft),
        Paragraph('Confidential — For internal use only', s_ft_r),
    ]], colWidths=[W / 2, W / 2])
    footer.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), LIGHT),
        ('TOPPADDING',    (0, 0), (-1, -1), 3*mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3*mm),
        ('LEFTPADDING',   (0, 0), (0, -1),  4*mm),
        ('RIGHTPADDING',  (1, 0), (1, -1),  4*mm),
    ]))
    story.append(footer)

    doc.build(story)
    return buffer.getvalue()


@router.get("/applications/{application_id}/report")
async def download_screening_report(
    application_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate and download candidate screening report as PDF."""
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

    # Company admin: verify the application belongs to their company's job
    if not current_user.is_admin:
        job_chk = application.job
        allowed = False
        if job_chk:
            owner_chk = db.query(models.User).filter(
                models.User.id == job_chk.owner_id,
                models.User.company_id == current_user.company_id,
            ).first()
            allowed = owner_chk is not None
        if not allowed:
            raise HTTPException(status_code=403, detail="Access denied")

    candidate  = application.candidate
    job        = application.job
    evaluation = application.evaluation
    if not evaluation and candidate:
        evaluation = (
            db.query(models.Evaluation)
            .filter(
                models.Evaluation.candidate_id == candidate.id,
                models.Evaluation.job_id == application.job_id,
            )
            .first()
        )

    # Company name
    company_name = ""
    if job and job.owner_id:
        owner = db.query(models.User).filter(models.User.id == job.owner_id).first()
        if owner and owner.company_id:
            company = db.query(models.Company).filter(
                models.Company.id == owner.company_id
            ).first()
            if company:
                company_name = company.company_name or ""

    display_name = (
        (candidate.name if candidate else None)
        or application.applicant_name
        or "Candidate"
    )

    candidate_data = {
        'name':             display_name,
        'phone':            (candidate.phone if candidate else None) or application.applicant_phone or '—',
        'email':            (candidate.email if candidate else None) or application.applicant_email or '—',
        'job_title':        job.job_title if job else '—',
        'experience_years': candidate.experience_years if candidate else 0,
        'source':           'External Apply' if getattr(application, 'source', '') == 'external' else 'Portal Apply',
    }

    evaluation_data = {}
    if evaluation:
        import json as _json

        iq_en = evaluation.suggested_interview_questions or []
        if isinstance(iq_en, str):
            try:    iq_en = _json.loads(iq_en)
            except: iq_en = [iq_en] if iq_en else []

        iq_ar = evaluation.interview_questions_ar or []
        if isinstance(iq_ar, str):
            try:    iq_ar = _json.loads(iq_ar)
            except: iq_ar = [iq_ar] if iq_ar else []

        evaluation_data = {
            'score':                 evaluation.score or 0,
            'decision':              evaluation.decision or '—',
            'reasoning':             evaluation.reason or evaluation.summary_en or '—',
            'strengths':             _parse_bullet_field(evaluation.strengths),
            'gaps':                  _parse_bullet_field(evaluation.weaknesses or evaluation.gaps_en),
            'score_experience':      evaluation.score_experience or 0,
            'score_skills':          evaluation.score_skills     or 0,
            'score_education':       evaluation.score_education  or 0,
            'score_behavioral':      evaluation.score_behavioral or 0,
            'weight_experience':     round((job.weight_experience or 0) * 100) if job else 40,
            'weight_skills':         round((job.weight_skills     or 0) * 100) if job else 30,
            'weight_education':      round((job.weight_education  or 0) * 100) if job else 20,
            'weight_behavioral':     round((job.weight_behavioral or 0) * 100) if job else 10,
            'interview_questions_en': iq_en,
            'interview_questions_ar': iq_ar,
        }

    pdf_bytes = generate_screening_report_pdf(candidate_data, evaluation_data, company_name)
    from .candidates import _safe as _safe_fn
    safe_name = _safe_fn("".join(c for c in display_name if c.isalnum() or c in " _-").strip().replace(" ", "_")) or "Candidate"
    return Response(
        content=pdf_bytes,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="screening_report_{safe_name}.pdf"'},
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
    company_name = ""
    if j.owner and j.owner.company:
        company_name = j.owner.company.company_name or ""
    return {
        "id": j.id,
        "job_title": j.job_title or "",
        "company_name": company_name,
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
        "department": j.department or "Other",
        "is_approved": bool(j.is_approved),
        "created_at": j.created_at.isoformat() if j.created_at else "",
        "owner_id": j.owner_id,
        "agent_weight_title":      getattr(j, "agent_weight_title",      25) or 25,
        "agent_weight_industry":   getattr(j, "agent_weight_industry",   25) or 25,
        "agent_weight_experience": getattr(j, "agent_weight_experience", 25) or 25,
        "agent_weight_skills":     getattr(j, "agent_weight_skills",     25) or 25,
        "essential_skills":        getattr(j, "essential_skills",        None) or [],
    }


@router.get("/jobs")
def list_admin_jobs(
    company_id: Optional[int] = None,
    archived: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List all jobs as admin, optionally scoped to a company. Pass archived=true to list archived jobs."""
    _admin(current_user)
    q = db.query(models.Job).filter(
        or_(models.Job.status == None, models.Job.status != 'rejected'),
        models.Job.is_archived == archived,
    )
    if company_id is not None:
        co_user_ids = (
            db.query(models.User.id).filter(models.User.company_id == company_id).subquery()
        )
        q = q.filter(models.Job.owner_id.in_(co_user_ids))
    jobs = (
        q.options(
            joinedload(models.Job.owner).joinedload(models.User.company)
        )
        .order_by(models.Job.id.desc())
        .all()
    )
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
        department=payload.department or "Other",
        weight_experience=payload.weight_experience,
        weight_skills=payload.weight_skills,
        weight_education=payload.weight_education,
        weight_behavioral=payload.weight_behavioral,
        agent_weight_title=payload.agent_weight_title,
        agent_weight_industry=payload.agent_weight_industry,
        agent_weight_experience=payload.agent_weight_experience,
        agent_weight_skills=payload.agent_weight_skills,
        essential_skills=payload.essential_skills or [],
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
    job.department = payload.department or "Other"
    job.weight_experience = payload.weight_experience
    job.weight_skills = payload.weight_skills
    job.weight_education = payload.weight_education
    job.weight_behavioral = payload.weight_behavioral
    job.agent_weight_title      = payload.agent_weight_title
    job.agent_weight_industry   = payload.agent_weight_industry
    job.agent_weight_experience = payload.agent_weight_experience
    job.agent_weight_skills     = payload.agent_weight_skills
    job.essential_skills        = payload.essential_skills or []
    db.commit()
    db.refresh(job)
    return _job_to_dict(job)


# ── Shadow Screening (SuperAdmin only — isolated, never client-facing) ────────

def _cv_text_for_application(app, db: Session) -> str | None:
    """Resolve CV text for an application: candidate profile first, then inline."""
    candidate = app.candidate
    if candidate and (candidate.cv_text or "").strip():
        return candidate.cv_text.strip()
    if (app.cv_text or "").strip():
        return app.cv_text.strip()
    return None


def _upsert_agent_screening(db: Session, application_id: int, job_id: int,
                            result: dict, user_id: int) -> None:
    from sqlalchemy import text as _text
    import json as _json
    dim = result.get("dimension_scores") or {}
    db.execute(_text("""
        INSERT INTO agent_screenings
            (application_id, job_id, agent_score, agent_recommendation,
             dimension_scores, strengths, concerns, semantic_match,
             matched_skills, missed_skills,
             gate_triggered, gate_reason,
             screened_at, screened_by)
        VALUES (:app_id, :job_id, :score, :rec,
                CAST(:dims AS jsonb), CAST(:str AS jsonb), CAST(:con AS jsonb), :sem,
                CAST(:matched AS jsonb), CAST(:missed AS jsonb),
                :gate_triggered, :gate_reason,
                NOW(), :uid)
        ON CONFLICT (application_id) DO UPDATE SET
            agent_score          = EXCLUDED.agent_score,
            agent_recommendation = EXCLUDED.agent_recommendation,
            dimension_scores     = EXCLUDED.dimension_scores,
            strengths            = EXCLUDED.strengths,
            concerns             = EXCLUDED.concerns,
            semantic_match       = EXCLUDED.semantic_match,
            matched_skills       = EXCLUDED.matched_skills,
            missed_skills        = EXCLUDED.missed_skills,
            gate_triggered       = EXCLUDED.gate_triggered,
            gate_reason          = EXCLUDED.gate_reason,
            screened_at          = EXCLUDED.screened_at,
            screened_by          = EXCLUDED.screened_by
    """), {
        "app_id": application_id,
        "job_id": job_id,
        "score": result.get("overall_score"),
        "rec": result.get("recommendation"),
        "dims": _json.dumps(dim),
        "str": _json.dumps(result.get("strengths") or []),
        "con": _json.dumps(result.get("concerns") or []),
        "sem": result.get("semantic_match"),
        "matched": _json.dumps(result.get("matched_skills") or []),
        "missed":  _json.dumps(result.get("missed_skills") or []),
        "gate_triggered": bool(result.get("gate_triggered")),
        "gate_reason": result.get("gate_reason"),
        "uid": user_id,
    })
    db.commit()


@router.post("/shadow-screen/{application_id}")
def shadow_screen_single(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SuperAdmin only. Run the Node screening service against one application.
    Result is stored in agent_screenings — never in evaluations."""
    if not current_user.is_admin or current_user.email != SUPERADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="SuperAdmin only")

    from ..services.agent_screener import call_agent_screen

    app = (
        db.query(models.Application)
        .options(
            joinedload(models.Application.candidate),
            joinedload(models.Application.job),
            joinedload(models.Application.evaluation),
        )
        .filter(models.Application.id == application_id)
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if not app.job:
        raise HTTPException(status_code=400, detail="Application has no associated job")

    cv_text = _cv_text_for_application(app, db)
    if not cv_text:
        raise HTTPException(status_code=400, detail="No CV text found for this application")

    result = call_agent_screen(cv_text, app.job)
    if result is None:
        raise HTTPException(status_code=502, detail="Screening service unreachable or returned error")

    _upsert_agent_screening(db, application_id, app.job_id, result, current_user.id)

    ev = app.evaluation
    gemini_score = None
    if ev and ev.score is not None:
        gemini_score = _norm_score(ev.score)

    return {
        "application_id": application_id,
        "candidate_name": (
            (app.candidate.name if app.candidate else None)
            or app.applicant_name or "Unknown"
        ),
        "job_title": app.job.job_title,
        "gemini_score": gemini_score,
        "gemini_decision": ev.decision if ev else None,
        "agent_score": result.get("overall_score"),
        "agent_recommendation": result.get("recommendation"),
        "dimension_scores": result.get("dimension_scores"),
        "strengths": result.get("strengths"),
        "concerns": result.get("concerns"),
        "semantic_match": result.get("semantic_match"),
        "gate_triggered": bool(result.get("gate_triggered")),
        "gate_reason": result.get("gate_reason"),
    }


@router.post("/shadow-screen-bulk")
def shadow_screen_bulk(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SuperAdmin only. Shadow-screen up to 20 applications for a job.
    Results go to agent_screenings only — Gemini evaluations are untouched."""
    if not current_user.is_admin or current_user.email != SUPERADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="SuperAdmin only")

    from ..services.agent_screener import call_agent_screen

    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    apps = (
        db.query(models.Application)
        .options(
            joinedload(models.Application.candidate),
            joinedload(models.Application.evaluation),
        )
        .filter(models.Application.job_id == job_id)
        .limit(20)
        .all()
    )

    screened, skipped, failed = 0, 0, 0
    for app in apps:
        cv_text = _cv_text_for_application(app, db)
        if not cv_text:
            skipped += 1
            continue
        result = call_agent_screen(cv_text, job)
        if result is None:
            failed += 1
            continue
        try:
            _upsert_agent_screening(db, app.id, job_id, result, current_user.id)
            screened += 1
        except Exception as exc:
            _logger.error("Failed to persist shadow screening for app %d: %s", app.id, exc)
            db.rollback()
            failed += 1

    return {
        "job_id": job_id,
        "job_title": job.job_title,
        "total_apps": len(apps),
        "screened": screened,
        "skipped_no_cv": skipped,
        "failed": failed,
    }


@router.get("/shadow-screenings")
def list_shadow_screenings(
    job_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """SuperAdmin only. List shadow screening results with Gemini comparison."""
    if not current_user.is_admin or current_user.email != SUPERADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="SuperAdmin only")

    from sqlalchemy import text as _text
    import json as _json

    where = "WHERE 1=1"
    params: dict = {}
    if job_id is not None:
        where += " AND ag.job_id = :job_id"
        params["job_id"] = job_id

    rows = db.execute(_text(f"""
        SELECT
            ag.id,
            ag.application_id,
            ag.job_id,
            ag.agent_score,
            ag.agent_recommendation,
            ag.dimension_scores,
            ag.strengths,
            ag.concerns,
            ag.matched_skills,
            ag.missed_skills,
            ag.semantic_match,
            ag.gate_triggered,
            ag.gate_reason,
            ag.screened_at,
            j.job_title,
            j.required_skills AS job_required_skills,
            COALESCE(c.name, ap.applicant_name) AS candidate_name,
            ev.score   AS gemini_score_raw,
            ev.decision AS gemini_decision
        FROM agent_screenings ag
        LEFT JOIN applications ap ON ap.id = ag.application_id
        LEFT JOIN jobs         j  ON j.id  = ag.job_id
        LEFT JOIN candidates   c  ON c.id  = ap.candidate_id
        LEFT JOIN evaluations  ev ON ev.application_id = ag.application_id
        {where}
        ORDER BY ag.screened_at DESC
        LIMIT 200
    """), params).fetchall()

    def _parse_jsonb(val):
        if val is None:
            return None
        if isinstance(val, (list, dict)):
            return val
        try:
            return _json.loads(val)
        except Exception:
            return None

    result = []
    for r in rows:
        gemini_score = _norm_score(r.gemini_score_raw) if r.gemini_score_raw is not None else None
        dim = _parse_jsonb(r.dimension_scores) or {}
        result.append({
            "id": r.id,
            "application_id": r.application_id,
            "job_id": r.job_id,
            "job_title": r.job_title or "",
            "job_required_skills": r.job_required_skills or "",
            "candidate_name": r.candidate_name or "Unknown",
            "agent_score": r.agent_score,
            "agent_recommendation": r.agent_recommendation,
            "dimension_scores": dim,
            "strengths": _parse_jsonb(r.strengths) or [],
            "concerns": _parse_jsonb(r.concerns) or [],
            "matched_skills": _parse_jsonb(r.matched_skills) or [],
            "missed_skills": _parse_jsonb(r.missed_skills) or [],
            "semantic_match": r.semantic_match,
            "gate_triggered": bool(r.gate_triggered) if r.gate_triggered is not None else False,
            "gate_reason": r.gate_reason,
            "gemini_score": gemini_score,
            "gemini_decision": r.gemini_decision,
            "score_delta": (
                (r.agent_score - gemini_score)
                if r.agent_score is not None and gemini_score is not None else None
            ),
            "screened_at": r.screened_at.isoformat() if r.screened_at else None,
        })
    return result


@router.patch("/jobs/{job_id}/archive")
def admin_archive_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.is_archived = True
    db.commit()
    return {"message": "Job archived"}

@router.patch("/jobs/{job_id}/restore")
def admin_restore_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _admin(current_user)
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.is_archived = False
    db.commit()
    return {"message": "Job restored"}

@router.delete("/jobs/{job_id}")
def admin_delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete any job as admin, cascading all dependent records."""
    _admin(current_user)
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        from sqlalchemy import text as _text
        app_ids = [r[0] for r in db.query(models.Application.id).filter(
            models.Application.job_id == job_id
        ).all()]

        # 1. agent_screenings (no SQLAlchemy model — raw SQL; FK → applications.id AND jobs.id)
        if app_ids:
            db.execute(_text("DELETE FROM agent_screenings WHERE application_id = ANY(:ids)"),
                       {"ids": app_ids})
        db.execute(_text("DELETE FROM agent_screenings WHERE job_id = :jid"), {"jid": job_id})

        # 2. VoiceScreenings (FK → applications.id AND jobs.id)
        vs_conds = [models.VoiceScreening.job_id == job_id]
        if app_ids:
            vs_conds.append(models.VoiceScreening.application_id.in_(app_ids))
        db.query(models.VoiceScreening).filter(or_(*vs_conds)).delete(synchronize_session=False)

        # 3. Evaluations (FK → applications.id and job_id)
        ev_conds = [models.Evaluation.job_id == job_id]
        if app_ids:
            ev_conds.append(models.Evaluation.application_id.in_(app_ids))
        db.query(models.Evaluation).filter(or_(*ev_conds)).delete(synchronize_session=False)

        # 4. Interviews (FK → applications.id)
        if app_ids:
            db.query(models.Interview).filter(
                models.Interview.application_id.in_(app_ids)
            ).delete(synchronize_session=False)

        # 5. Offers (FK → applications.id)
        if app_ids:
            db.query(models.Offer).filter(
                models.Offer.application_id.in_(app_ids)
            ).delete(synchronize_session=False)

        # 6. Applications
        db.query(models.Application).filter(
            models.Application.job_id == job_id
        ).delete(synchronize_session=False)

        # 7. Null out job_applied on Candidates (preserve candidate records)
        db.query(models.Candidate).filter(
            models.Candidate.job_applied == job_id
        ).update({"job_applied": None}, synchronize_session=False)

        # 8. Delete the job
        db.delete(job)
        db.commit()
        return {"message": "Job deleted"}
    except Exception as exc:
        db.rollback()
        _logger.error("admin_delete_job failed for job_id=%s: %s", job_id, exc)
        raise HTTPException(status_code=500, detail="Delete failed — rolled back")
