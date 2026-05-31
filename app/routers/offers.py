from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from pydantic import BaseModel
from datetime import datetime
import io
import base64
import os
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


def _hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _set_cell_bg(cell, hex_color):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    for shd in tcPr.findall(qn('w:shd')):
        tcPr.remove(shd)
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color.lstrip('#'))
    tcPr.append(shd)


def _set_cell_borders(cell, color='1B2A4A', size='4'):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    for side in ['top', 'left', 'bottom', 'right']:
        b = OxmlElement(f'w:{side}')
        b.set(qn('w:val'), 'single')
        b.set(qn('w:sz'), size)
        b.set(qn('w:color'), color)
        tcBorders.append(b)
    tcPr.append(tcBorders)


def _set_no_borders(cell):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    for side in ['top', 'left', 'bottom', 'right']:
        b = OxmlElement(f'w:{side}')
        b.set(qn('w:val'), 'none')
        b.set(qn('w:sz'), '0')
        b.set(qn('w:color'), 'FFFFFF')
        tcBorders.append(b)
    tcPr.append(tcBorders)


def _add_para_border(para, side, color, size='12'):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    pPr = para._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    b = OxmlElement(f'w:{side}')
    b.set(qn('w:val'), 'single')
    b.set(qn('w:sz'), size)
    b.set(qn('w:color'), color)
    b.set(qn('w:space'), '1')
    pBdr.append(b)
    pPr.append(pBdr)


def _cell_para(cell, text, bold=False, color='000000', size=9.5, align=None):
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    para = cell.paragraphs[0]
    para.clear()
    if align is not None:
        para.alignment = align
    run = para.add_run(text)
    run.font.bold = bold
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor(*_hex_to_rgb(color))
    run.font.name = 'Calibri'
    return para


def generate_offer_docx(offer, candidate_name, job_title, company_name, company_logo_url=None):
    import requests as req_lib
    from docx import Document
    from docx.shared import Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn

    template_path = os.path.join(os.path.dirname(__file__), '..', 'offer_master_template.docx')
    doc = Document(template_path)

    # Fix header row to exact height so logo doesn't push content onto a second page
    from docx.oxml import OxmlElement
    header_row = doc.tables[0].rows[0]
    trPr = header_row._tr.get_or_add_trPr()
    trHeight = OxmlElement('w:trHeight')
    trHeight.set(qn('w:val'), '1400')
    trHeight.set(qn('w:hRule'), 'exact')
    trPr.append(trHeight)

    created_date = offer.created_at.strftime('%B %d, %Y') if offer.created_at else ''
    hours = f'{offer.working_hours_from or ""} to {offer.working_hours_to or ""}'
    salary = f'{offer.net_salary or ""} EGP — Net (monthly by direct transfer to payroll bank account)'

    # Paragraph-level replacements
    replacements = {
        'Date: May 30, 2026': f'Date: {created_date}',
        'Dear Ms / Mr Basant,': f'Dear Ms / Mr {candidate_name},',
        'It is with great pleasure that we are offering you the position of English Teacher - KS 3 with Elite Generation International School. Your experience and enthusiasm will be a valuable asset to our organization.':
            f'It is with great pleasure that we are offering you the position of {job_title} with {company_name}. Your experience and enthusiasm will be a valuable asset to our organization.',
    }

    for para in doc.paragraphs:
        for old, new in replacements.items():
            if old in para.text:
                # Try replacing within individual runs first
                for run in para.runs:
                    if old in run.text:
                        run.text = run.text.replace(old, new)
                        break
                else:
                    # Text is split across runs — rewrite into first run
                    full = para.text
                    for run in para.runs:
                        run.text = ''
                    if para.runs:
                        para.runs[0].text = full.replace(old, new)

    # Table cell replacements
    table_replacements = {
        'Basant': candidate_name or '',
        'English Teacher - KS 3': job_title or '',
        'English Department': offer.department or '',
        '2026-06-04': str(offer.start_date) if offer.start_date else '',
        '9:00 to 5:00': hours,
        '32000 EGP — Net (monthly by direct transfer to payroll bank account)': salary,
        'None': offer.exceptions or 'None',
        'English HOD': offer.reporting_to or '',
        'Elite Generation International School\nJob Offer Letter': f'{company_name}\nJob Offer Letter',
    }

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for old, new in table_replacements.items():
                    if old in cell.text:
                        for para in cell.paragraphs:
                            for run in para.runs:
                                if old in run.text:
                                    run.text = run.text.replace(old, new)

    # Replace logo image in first cell of first table
    if company_logo_url:
        try:
            if company_logo_url.startswith('data:image'):
                _header, b64data = company_logo_url.split(',', 1)
                img_bytes = base64.b64decode(b64data)
            else:
                r = req_lib.get(company_logo_url, timeout=5)
                img_bytes = r.content

            logo_cell = doc.tables[0].cell(0, 0)
            for para in logo_cell.paragraphs:
                para.clear()
            logo_para = logo_cell.paragraphs[0]
            logo_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            logo_para.add_run().add_picture(io.BytesIO(img_bytes), width=Cm(1.8))
        except Exception:
            pass  # Keep existing logo/text on failure

    return doc


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
    logo_url = None
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
        company = app.job.owner.company
        company_name = company.company_name
        logo_url = company.logo_url if company else None

    doc = generate_offer_docx(offer, offer.candidate_name, offer.job_title, company_name, logo_url)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    safe_name = (offer.candidate_name or "Offer").replace(" ", "_")
    filename = f"JobOffer_{safe_name}.docx"

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
