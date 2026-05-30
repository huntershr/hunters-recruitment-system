from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from pydantic import BaseModel
from datetime import datetime
import io
import logging

from .. import models, database
from ..routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/offers", tags=["Offers"])


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


class OfferIn(BaseModel):
    application_id: int
    candidate_name: str
    job_title: str
    department: Optional[str] = ""
    start_date: Optional[str] = ""
    working_hours_from: Optional[str] = "9:00"
    working_hours_to: Optional[str] = "5:00"
    net_salary: Optional[str] = ""
    reporting_to: Optional[str] = ""
    exceptions: Optional[str] = ""


class OfferStatusIn(BaseModel):
    status: str  # accepted / rejected / pending


def _offer_to_dict(offer) -> dict:
    return {
        "id": offer.id,
        "application_id": offer.application_id,
        "candidate_name": offer.candidate_name,
        "job_title": offer.job_title,
        "department": offer.department,
        "start_date": offer.start_date,
        "working_hours_from": offer.working_hours_from,
        "working_hours_to": offer.working_hours_to,
        "net_salary": offer.net_salary,
        "reporting_to": offer.reporting_to,
        "exceptions": offer.exceptions,
        "status": offer.status,
        "created_at": offer.created_at.isoformat() if offer.created_at else None,
        "created_by": offer.created_by,
    }


def _check_scope(application_id: int, current_user, db: Session) -> bool:
    if current_user.is_admin:
        return True
    if not current_user.company_id:
        return False
    app = db.query(models.Application).filter(models.Application.id == application_id).first()
    if not app or not app.job:
        return False
    owner = db.query(models.User).filter(models.User.id == app.job.owner_id).first()
    return bool(owner and owner.company_id == current_user.company_id)


def _build_offer_docx(offer, company_name: str) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    for section in doc.sections:
        section.top_margin    = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin   = Inches(1.2)
        section.right_margin  = Inches(1.2)

    NAVY = RGBColor(27, 42, 74)
    GOLD = RGBColor(201, 168, 76)

    # ── Header ──────────────────────────────────────────────────────────────
    h = doc.add_heading("HUNTERS FOR HR TRANSFORMATION & EXECUTION", level=1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = h.runs[0]
    run.font.color.rgb = NAVY
    run.font.size = Pt(14)

    doc.add_paragraph()

    t = doc.add_heading("Job Offer", level=2)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if t.runs:
        t.runs[0].font.color.rgb = GOLD

    doc.add_paragraph()

    # ── Salutation ──────────────────────────────────────────────────────────
    doc.add_paragraph(f"Dear Ms / Mr {offer.candidate_name or 'Candidate'},")
    doc.add_paragraph()

    doc.add_paragraph(
        f"It is with great pleasure that We are offering you the position of "
        f"{offer.job_title} with {company_name}. Your experience and enthusiasm "
        f"will be an asset to our school."
    )
    doc.add_paragraph()

    doc.add_paragraph(
        "Please review offer details below outlining your salary and benefits, "
        "and sign where indicated, then reattach the job offer."
    )
    doc.add_paragraph()

    # ── Details table ───────────────────────────────────────────────────────
    rows_data = [
        ("Candidate name",                  offer.candidate_name or ""),
        ("Job title",                       offer.job_title or ""),
        ("Department / Division / Site",    offer.department or ""),
        ("Proposed starting date",          offer.start_date or ""),
        ("Working days",                    "Sunday to Thursday"),
        ("Working hours",                   f"{offer.working_hours_from} AM to {offer.working_hours_to} PM"),
        ("Package details / Net Salary",    f"{offer.net_salary} EGP - Net (as monthly net amount by direct transfer to payroll bank account)"),
        ("Exceptions and Permissions",      offer.exceptions or ""),
        ("Reporting to",                    offer.reporting_to or ""),
    ]

    table = doc.add_table(rows=len(rows_data), cols=2)
    table.style = "Table Grid"
    for i, (label, value) in enumerate(rows_data):
        row = table.rows[i]
        label_run = row.cells[0].paragraphs[0].add_run(label)
        label_run.bold = True
        label_run.font.color.rgb = NAVY
        row.cells[1].paragraphs[0].add_run(value)

    doc.add_paragraph()

    # ── Required documents ──────────────────────────────────────────────────
    dh = doc.add_heading("Required hiring documents (in Arabic):", level=3)
    if dh.runs:
        dh.runs[0].font.color.rgb = NAVY

    arabic_docs = [
        "أصل شهادة الميلاد",
        "أصل شهادة المؤهل الدراسي",
        "أصل شهادة الخدمة العسكرية للذكور",
        "كعب عمل",
        "صورة بطاقة الرقم القومي",
        "عدد 6 صور شخصية حديثة",
        "صحيفة الحالة الجنائية",
        "بيان باخر راتب / مفردات مرتب / كشف حساب",
        "نموذج 111 خاص بالتأمين الصحي خط ساخن 19806",
    ]
    for item in arabic_docs:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(item)

    doc.add_paragraph()

    # ── Signatures ──────────────────────────────────────────────────────────
    sh = doc.add_heading("Signatures", level=3)
    if sh.runs:
        sh.runs[0].font.color.rgb = NAVY

    sig = doc.add_table(rows=2, cols=2)
    sig.style = "Table Grid"
    sig.rows[0].cells[0].paragraphs[0].add_run("Name: ___________")
    sig.rows[0].cells[1].paragraphs[0].add_run("Name: ___________")
    sig.rows[1].cells[0].paragraphs[0].add_run("Signature: ___________")
    sig.rows[1].cells[1].paragraphs[0].add_run("Signature: ___________")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ── Routes ──────────────────────────────────────────────────────────────────

@router.post("/")
def create_offer(
    data: OfferIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if not _check_scope(data.application_id, current_user, db):
        raise HTTPException(status_code=403, detail="Access denied")

    existing = db.query(models.Offer).filter(models.Offer.application_id == data.application_id).first()
    if existing:
        for field in ["candidate_name", "job_title", "department", "start_date",
                      "working_hours_from", "working_hours_to", "net_salary",
                      "reporting_to", "exceptions"]:
            setattr(existing, field, getattr(data, field))
        db.commit()
        db.refresh(existing)
        logger.info(f"Offer {existing.id} updated for application {data.application_id}")
        return _offer_to_dict(existing)

    offer = models.Offer(
        application_id=data.application_id,
        candidate_name=data.candidate_name,
        job_title=data.job_title,
        department=data.department,
        start_date=data.start_date,
        working_hours_from=data.working_hours_from,
        working_hours_to=data.working_hours_to,
        net_salary=data.net_salary,
        reporting_to=data.reporting_to,
        exceptions=data.exceptions,
        status="pending",
        created_by=current_user.id,
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)
    logger.info(f"Offer {offer.id} created for application {data.application_id}")
    return _offer_to_dict(offer)


@router.get("/application/{application_id}")
def get_offer_by_application(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")
    offer = db.query(models.Offer).filter(models.Offer.application_id == application_id).first()
    if not offer:
        return None
    return _offer_to_dict(offer)


@router.patch("/{offer_id}/status")
def update_offer_status(
    offer_id: int,
    data: OfferStatusIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")
    offer = db.query(models.Offer).filter(models.Offer.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")
    if data.status not in ("accepted", "rejected", "pending"):
        raise HTTPException(status_code=400, detail="Invalid status — use accepted / rejected / pending")
    offer.status = data.status
    db.commit()
    db.refresh(offer)
    logger.info(f"Offer {offer_id} status → {data.status}")
    return _offer_to_dict(offer)


@router.get("/{offer_id}/download")
def download_offer(
    offer_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.is_admin and not current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")
    offer = db.query(models.Offer).filter(models.Offer.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    company_name = "Hunters for HR Transformation & Execution"
    app = (
        db.query(models.Application)
        .options(
            joinedload(models.Application.job)
                .joinedload(models.Job.owner)
                .joinedload(models.User.company)
        )
        .filter(models.Application.id == offer.application_id)
        .first()
    )
    if app and app.job and app.job.owner and app.job.owner.company:
        company_name = app.job.owner.company.company_name

    docx_bytes = _build_offer_docx(offer, company_name)
    safe_name = (offer.candidate_name or "Offer").replace(" ", "_")
    filename = f"JobOffer_{safe_name}.docx"

    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
