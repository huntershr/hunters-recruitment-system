from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from datetime import datetime, timedelta, date, time
from pydantic import BaseModel
import base64
import logging

from .. import models, database
from ..routers.auth import get_current_user

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/admin", tags=["Interviews"])
candidate_router = APIRouter(prefix="/api/candidate", tags=["Candidate Interviews"])


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Request schemas ────────────────────────────────────────────────────────

class ScheduleInterviewIn(BaseModel):
    application_id: int
    interview_date: str        # "YYYY-MM-DD"
    interview_time: str        # "HH:MM"
    duration_minutes: int = 60
    location_type: str         # "physical" | "online"
    location_value: Optional[str] = None
    interviewer_names: Optional[str] = None
    notes_for_candidate: Optional[str] = None
    internal_notes: Optional[str] = None


class UpdateInterviewIn(BaseModel):
    interview_date: Optional[str] = None
    interview_time: Optional[str] = None
    duration_minutes: Optional[int] = None
    location_type: Optional[str] = None
    location_value: Optional[str] = None
    interviewer_names: Optional[str] = None
    notes_for_candidate: Optional[str] = None
    internal_notes: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────

def _interview_to_dict(iv) -> dict:
    return {
        "id": iv.id,
        "application_id": iv.application_id,
        "scheduled_by": iv.scheduled_by,
        "interview_date": str(iv.interview_date),
        "interview_time": str(iv.interview_time)[:5],   # "HH:MM"
        "duration_minutes": iv.duration_minutes,
        "location_type": iv.location_type,
        "location_value": iv.location_value,
        "interviewer_names": iv.interviewer_names,
        "notes_for_candidate": iv.notes_for_candidate,
        "internal_notes": iv.internal_notes,
        "status": iv.status,
        "created_at": iv.created_at.isoformat() if iv.created_at else None,
        "updated_at": iv.updated_at.isoformat() if iv.updated_at else None,
    }


def _load_app(application_id: int, db: Session):
    return (
        db.query(models.Application)
        .options(
            joinedload(models.Application.candidate),
            joinedload(models.Application.job)
                .joinedload(models.Job.owner)
                .joinedload(models.User.company),
        )
        .filter(models.Application.id == application_id)
        .first()
    )


def _people(app, db: Session):
    """Returns (name, email, phone, job_title, company_name)."""
    if app.candidate_id and app.candidate:
        c = app.candidate
        name  = c.name  or app.applicant_name  or "Candidate"
        email = c.email or app.applicant_email or ""
        phone = c.phone or app.applicant_phone or ""
    else:
        name  = app.applicant_name  or "Candidate"
        email = app.applicant_email or ""
        phone = app.applicant_phone or ""

    job_title    = app.job.job_title if app.job else "Position"
    company_name = "Hunters HR"
    if app.job and app.job.owner and app.job.owner.company:
        company_name = app.job.owner.company.company_name

    return name, email, phone, job_title, company_name


def _check_scope(app, current_user, db: Session) -> bool:
    if current_user.is_admin:
        return True
    if not current_user.company_id:
        return False
    if not app.job:
        return False
    owner = db.query(models.User).filter(models.User.id == app.job.owner_id).first()
    return bool(owner and owner.company_id == current_user.company_id)


def _ics_esc(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _generate_ics(iv, cand_name: str, job_title: str, company_name: str,
                  for_candidate: bool = True) -> str:
    start_dt = datetime.combine(iv.interview_date, iv.interview_time)
    end_dt   = start_dt + timedelta(minutes=iv.duration_minutes or 60)
    uid      = f"hunters-{iv.id}-{iv.application_id}@hunters-egypt.com"
    loc      = iv.location_value or "TBD"

    if for_candidate:
        summary = f"Interview — {job_title} at {company_name}"
        desc = (
            f"Dear {cand_name},\n\n"
            f"Your interview for {job_title} at {company_name} has been scheduled.\n\n"
            f"Interviewer(s): {iv.interviewer_names or 'TBD'}\n"
            f"Location: {loc}\n\n"
            f"{iv.notes_for_candidate or ''}\n\n"
            f"Good luck!\nHunters HR Team"
        )
    else:
        summary = f"Interview: {cand_name} — {job_title}"
        desc = (
            f"Candidate: {cand_name}\n"
            f"Job: {job_title} at {company_name}\n"
            f"Interviewer(s): {iv.interviewer_names or 'TBD'}\n"
            f"Location: {loc}\n\n"
            f"Internal notes: {iv.internal_notes or 'None'}"
        )

    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Hunters HR//Interview Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{_ics_esc(summary)}",
        f"DESCRIPTION:{_ics_esc(desc)}",
        f"LOCATION:{_ics_esc(loc)}",
        "STATUS:CONFIRMED",
        "BEGIN:VALARM",
        "TRIGGER:-PT1H",
        "ACTION:DISPLAY",
        "DESCRIPTION:Interview reminder",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ])


def _build_payload(iv, db: Session, mode: str = "schedule") -> dict:
    """mode: 'schedule' | 'reschedule' | 'cancel'"""
    app = _load_app(iv.application_id, db)
    if not app:
        return {"interview": _interview_to_dict(iv), "notifications": [], "ics_files": []}

    cand_name, cand_email, cand_phone, job_title, company_name = _people(app, db)

    dt_date   = iv.interview_date
    dt_time   = iv.interview_time
    date_str  = dt_date.strftime("%A, %d %B %Y") if hasattr(dt_date, "strftime") else str(dt_date)
    time_str  = dt_time.strftime("%I:%M %p").lstrip("0") if hasattr(dt_time, "strftime") else str(dt_time)[:5]

    loc_label = "Location" if iv.location_type == "physical" else "Meeting Link"
    loc_val   = iv.location_value or "TBD"

    scheduler = db.query(models.User).filter(models.User.id == iv.scheduled_by).first()
    sched_info = (
        f"{scheduler.full_name or scheduler.email} ({scheduler.email})"
        if scheduler else "System"
    )

    # Dynamic signature based on who scheduled
    if scheduler and scheduler.is_admin:
        signature = "Hunters HR Team\nhr@hunters-egypt.com"
    elif scheduler and scheduler.company_id:
        sched_company = db.query(models.Company).filter(
            models.Company.id == scheduler.company_id
        ).first()
        co_email = (sched_company.company_email if sched_company else None) or scheduler.email
        co_name  = sched_company.company_name if sched_company else "Company"
        signature = f"{co_name} — via Hunters HR\n{co_email}"
    else:
        signature = "Hunters HR Team\nhr@hunters-egypt.com"

    if mode == "cancel":
        cand_subj = f"Interview Cancelled — {job_title} at {company_name}"
        cand_body = (
            f"Dear {cand_name},\n\n"
            f"We regret to inform you that your scheduled interview for the position of "
            f"{job_title} at {company_name} has been cancelled.\n\n"
            f"We apologise for any inconvenience this may have caused. "
            f"We will be in touch to reschedule at a suitable time.\n\n"
            f"If you have any questions, please contact us at hr@hunters-egypt.com\n\n"
            f"Best regards,\n{signature}"
        )
        adm_subj = f"Interview Cancelled — {cand_name} for {job_title}"
        adm_body = (
            f"Interview cancelled.\n\n"
            f"Candidate: {cand_name} ({cand_email}, {cand_phone})\n"
            f"Job: {job_title} at {company_name}\n"
            f"Originally: {date_str} at {time_str}\n\n"
            f"Cancelled by: {sched_info}"
        )
        return {
            "interview": _interview_to_dict(iv),
            "notifications": [
                {"to": cand_email, "subject": cand_subj, "body": cand_body, "type": "candidate"},
                {"to": "hr@hunters-egypt.com", "subject": adm_subj, "body": adm_body, "type": "superadmin"},
            ],
            "ics_files": [],
        }

    verb     = "rescheduled" if mode == "reschedule" else "scheduled"
    verb_cap = "Rescheduled" if mode == "reschedule" else "Invitation"

    cand_subj  = f"Interview {verb_cap} — {job_title} at {company_name}"
    body_lines = [
        f"Dear {cand_name},",
        "",
        f"Congratulations! We are pleased to invite you for an interview "
        f"for the position of {job_title} at {company_name}.",
        "",
        "Interview Details:",
        f"Date: {date_str}",
        f"Time: {time_str}",
        f"Duration: {iv.duration_minutes or 60} minutes",
        f"{loc_label}: {loc_val}",
        f"Interviewer(s): {iv.interviewer_names or 'TBD'}",
    ]
    if iv.notes_for_candidate:
        body_lines += ["", iv.notes_for_candidate]
    body_lines += [
        "",
        "Please confirm your attendance by replying to this email.",
        "If you need to reschedule, contact us at hr@hunters-egypt.com",
        "",
        "Best regards,",
        signature,
    ]
    cand_body = "\n".join(body_lines)

    adm_subj = f"Interview {verb_cap} — {cand_name} for {job_title}"
    adm_body = (
        f"Interview {verb}.\n\n"
        f"Candidate: {cand_name} ({cand_email}, {cand_phone})\n"
        f"Job: {job_title} at {company_name}\n"
        f"Date: {date_str} at {time_str}\n"
        f"Duration: {iv.duration_minutes or 60} minutes\n"
        f"{loc_label}: {loc_val}\n"
        f"Interviewer(s): {iv.interviewer_names or 'TBD'}\n"
        f"Internal notes: {iv.internal_notes or 'None'}\n\n"
        f"Scheduled by: {sched_info}"
    )

    ics_cand  = _generate_ics(iv, cand_name, job_title, company_name, for_candidate=True)
    ics_admin = _generate_ics(iv, cand_name, job_title, company_name, for_candidate=False)
    safe_name = (cand_name or "candidate").replace(" ", "-").lower()
    date_tag  = str(iv.interview_date).replace("-", "")

    return {
        "interview": _interview_to_dict(iv),
        "notifications": [
            {"to": cand_email, "subject": cand_subj, "body": cand_body, "type": "candidate"},
            {"to": "hr@hunters-egypt.com", "subject": adm_subj, "body": adm_body, "type": "superadmin"},
        ],
        "ics_files": [
            {
                "filename": f"interview-{safe_name}-{date_tag}.ics",
                "content": base64.b64encode(ics_cand.encode()).decode(),
                "for": "candidate",
            },
            {
                "filename": f"interview-{safe_name}-{date_tag}-admin.ics",
                "content": base64.b64encode(ics_admin.encode()).decode(),
                "for": "admin",
            },
        ],
    }


# ── Admin endpoints ────────────────────────────────────────────────────────

@admin_router.post("/interviews")
def create_interview(
    data: ScheduleInterviewIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Company admin or super admin access required")

    app = _load_app(data.application_id, db)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if not _check_scope(app, current_user, db):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        iv_date = date.fromisoformat(data.interview_date)
        iv_time = time.fromisoformat(data.interview_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date/time: {exc}")

    iv = models.Interview(
        application_id=data.application_id,
        scheduled_by=current_user.id,
        interview_date=iv_date,
        interview_time=iv_time,
        duration_minutes=data.duration_minutes,
        location_type=data.location_type,
        location_value=data.location_value,
        interviewer_names=data.interviewer_names,
        notes_for_candidate=data.notes_for_candidate,
        internal_notes=data.internal_notes,
        status="scheduled",
    )
    db.add(iv)

    if (app.stage or "").lower() != "interview":
        app.stage = "Interview"

    db.commit()
    db.refresh(iv)
    logger.info(f"Interview {iv.id} created for application {data.application_id}")
    return _build_payload(iv, db, mode="schedule")


@admin_router.get("/interviews")
def list_interviews(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """All upcoming interviews. SuperAdmin: all companies. CompanyAdmin: scoped."""
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    q = db.query(models.Interview).filter(models.Interview.status != "cancelled")

    if not current_user.is_admin:
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
        co_app_ids = (
            db.query(models.Application.id)
            .filter(models.Application.job_id.in_(co_job_ids))
            .subquery()
        )
        q = q.filter(models.Interview.application_id.in_(co_app_ids))

    rows = q.order_by(
        models.Interview.interview_date.asc(),
        models.Interview.interview_time.asc(),
    ).all()

    result = []
    for iv in rows:
        app = _load_app(iv.application_id, db)
        if not app:
            continue
        cand_name, cand_email, _, job_title, company_name = _people(app, db)
        d = _interview_to_dict(iv)
        d["candidate_name"]  = cand_name
        d["candidate_email"] = cand_email
        d["job_title"]       = job_title
        d["company_name"]    = company_name
        result.append(d)

    return {"interviews": result}


@admin_router.get("/interviews/{application_id}")
def get_interview(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    app = _load_app(application_id, db)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if not _check_scope(app, current_user, db):
        raise HTTPException(status_code=403, detail="Access denied")

    iv = (
        db.query(models.Interview)
        .filter(
            models.Interview.application_id == application_id,
            models.Interview.status != "cancelled",
        )
        .order_by(models.Interview.created_at.desc())
        .first()
    )
    return _interview_to_dict(iv) if iv else None


@admin_router.patch("/interviews/{interview_id}")
def update_interview(
    interview_id: int,
    data: UpdateInterviewIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    iv = db.query(models.Interview).filter(models.Interview.id == interview_id).first()
    if not iv:
        raise HTTPException(status_code=404, detail="Interview not found")

    app = _load_app(iv.application_id, db)
    if not app or not _check_scope(app, current_user, db):
        raise HTTPException(status_code=403, detail="Access denied")

    if data.interview_date is not None:
        try:
            iv.interview_date = date.fromisoformat(data.interview_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format (YYYY-MM-DD)")
    if data.interview_time is not None:
        try:
            iv.interview_time = time.fromisoformat(data.interview_time)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid time format (HH:MM)")
    if data.duration_minutes is not None:
        iv.duration_minutes = data.duration_minutes
    if data.location_type is not None:
        iv.location_type = data.location_type
    if data.location_value is not None:
        iv.location_value = data.location_value
    if data.interviewer_names is not None:
        iv.interviewer_names = data.interviewer_names
    if data.notes_for_candidate is not None:
        iv.notes_for_candidate = data.notes_for_candidate
    if data.internal_notes is not None:
        iv.internal_notes = data.internal_notes

    iv.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(iv)
    logger.info(f"Interview {interview_id} updated (reschedule)")
    return _build_payload(iv, db, mode="reschedule")


@admin_router.delete("/interviews/{interview_id}")
def cancel_interview(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    iv = db.query(models.Interview).filter(models.Interview.id == interview_id).first()
    if not iv:
        raise HTTPException(status_code=404, detail="Interview not found")

    app = _load_app(iv.application_id, db)
    if not app or not _check_scope(app, current_user, db):
        raise HTTPException(status_code=403, detail="Access denied")

    iv.status = "cancelled"
    iv.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(iv)
    logger.info(f"Interview {interview_id} cancelled")
    return _build_payload(iv, db, mode="cancel")


# ── Candidate endpoint ─────────────────────────────────────────────────────

@candidate_router.get("/interview")
def get_my_interview(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Returns the next scheduled interview for the logged-in candidate."""
    candidate = (
        db.query(models.Candidate)
        .filter(models.Candidate.user_id == current_user.id)
        .first()
    )
    if not candidate:
        return None

    app_ids = [
        row[0]
        for row in db.query(models.Application.id)
        .filter(models.Application.candidate_id == candidate.id)
        .all()
    ]
    if not app_ids:
        return None

    iv = (
        db.query(models.Interview)
        .filter(
            models.Interview.application_id.in_(app_ids),
            models.Interview.status == "scheduled",
        )
        .order_by(
            models.Interview.interview_date.asc(),
            models.Interview.interview_time.asc(),
        )
        .first()
    )
    if not iv:
        return None

    d = _interview_to_dict(iv)
    d.pop("internal_notes", None)   # never expose to candidate

    app = _load_app(iv.application_id, db)
    if app:
        cand_name, _, _, job_title, company_name = _people(app, db)
        ics = _generate_ics(iv, cand_name, job_title, company_name, for_candidate=True)
        safe_name = (cand_name or "candidate").replace(" ", "-").lower()
        date_tag  = str(iv.interview_date).replace("-", "")
        d["job_title"]    = job_title
        d["company_name"] = company_name
        d["ics_file"] = {
            "filename": f"interview-{safe_name}-{date_tag}.ics",
            "content":  base64.b64encode(ics.encode()).decode(),
        }

    return d
