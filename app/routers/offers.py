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


def generate_offer_docx(offer, candidate_name, job_title, company_name, company_logo_url=None):
    import requests
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()

    NAVY = RGBColor(0x1B, 0x2A, 0x4A)
    GOLD = RGBColor(0xC9, 0xA8, 0x4C)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)

    for section in doc.sections:
        section.top_margin = Cm(1.5)
        section.bottom_margin = Cm(1.5)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    def set_cell_bg(cell, hex_color):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), hex_color)
        tcPr.append(shd)

    # ── HEADER TABLE (logo left, company name right) ──
    header_table = doc.add_table(rows=1, cols=2)
    header_table.autofit = False
    header_table.columns[0].width = Cm(6)
    header_table.columns[1].width = Cm(11.5)

    logo_cell = header_table.cell(0, 0)
    set_cell_bg(logo_cell, '1B2A4A')
    logo_para = logo_cell.paragraphs[0]
    logo_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if company_logo_url:
        try:
            r = requests.get(company_logo_url, timeout=5)
            img_stream = io.BytesIO(r.content)
            run = logo_para.add_run()
            run.add_picture(img_stream, width=Cm(4))
        except Exception:
            run = logo_para.add_run(company_name or '')
            run.font.color.rgb = WHITE
            run.font.size = Pt(12)
            run.font.bold = True
    else:
        run = logo_para.add_run(company_name or '')
        run.font.color.rgb = WHITE
        run.font.size = Pt(12)
        run.font.bold = True

    name_cell = header_table.cell(0, 1)
    set_cell_bg(name_cell, '1B2A4A')
    name_para = name_cell.paragraphs[0]
    name_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    name_para.paragraph_format.space_before = Pt(8)
    run = name_para.add_run(company_name or '')
    run.font.color.rgb = WHITE
    run.font.size = Pt(13)
    run.font.bold = True
    sub_para = name_cell.add_paragraph()
    sub_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    sub_run = sub_para.add_run('Job Offer Letter')
    sub_run.font.color.rgb = GOLD
    sub_run.font.size = Pt(10)
    sub_run.font.italic = True

    # ── GOLD DIVIDER ──
    div_para = doc.add_paragraph()
    div_para.paragraph_format.space_before = Pt(0)
    div_para.paragraph_format.space_after = Pt(0)
    pPr = div_para._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '12')
    bottom.set(qn('w:color'), 'C9A84C')
    pBdr.append(bottom)
    pPr.append(pBdr)

    # ── DATE ──
    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    date_para.paragraph_format.space_before = Pt(8)
    date_run = date_para.add_run(f'Date: {offer.created_at.strftime("%B %d, %Y") if offer.created_at else ""}')
    date_run.font.size = Pt(9)
    date_run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    # ── GREETING ──
    greet = doc.add_paragraph()
    greet.paragraph_format.space_before = Pt(12)
    greet.paragraph_format.space_after = Pt(6)
    greet_run = greet.add_run(f'Dear Ms / Mr {candidate_name},')
    greet_run.font.size = Pt(11)
    greet_run.font.color.rgb = NAVY

    # ── INTRO ──
    intro = doc.add_paragraph()
    intro.paragraph_format.space_after = Pt(10)
    intro_run = intro.add_run(
        f'It is with great pleasure that we are offering you the position of '
        f'{job_title} with {company_name}. '
        f'Your experience and enthusiasm will be a valuable asset to our organization.\n\n'
        f'Please review the offer details below, sign where indicated, and return a copy.'
    )
    intro_run.font.size = Pt(10.5)
    intro_run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)

    # ── OFFER DETAILS TABLE ──
    rows_data = [
        ('Candidate Name', candidate_name or ''),
        ('Job Title', job_title or ''),
        ('Department / Division / Site', offer.department or ''),
        ('Proposed Starting Date', str(offer.start_date) if offer.start_date else ''),
        ('Working Days', 'Sunday to Thursday'),
        ('Working Hours', f'{offer.working_hours_from or ""} AM to {offer.working_hours_to or ""} PM'),
        ('Package Details / Net Salary', f'{offer.net_salary or ""} EGP - Net (monthly by direct transfer to payroll bank account)'),
        ('Exceptions and Permissions', offer.exceptions or 'None'),
        ('Reporting To', offer.reporting_to or ''),
    ]

    table = doc.add_table(rows=len(rows_data) + 1, cols=2)
    table.style = 'Table Grid'
    table.autofit = False
    table.columns[0].width = Cm(7)
    table.columns[1].width = Cm(10.5)

    hdr_cells = table.rows[0].cells
    for cell in hdr_cells:
        set_cell_bg(cell, '1B2A4A')
    hdr_cells[0].paragraphs[0].add_run('Field').font.color.rgb = WHITE
    hdr_cells[0].paragraphs[0].runs[0].font.bold = True
    hdr_cells[0].paragraphs[0].runs[0].font.size = Pt(10)
    hdr_cells[1].paragraphs[0].add_run('Details').font.color.rgb = WHITE
    hdr_cells[1].paragraphs[0].runs[0].font.bold = True
    hdr_cells[1].paragraphs[0].runs[0].font.size = Pt(10)

    for i, (label, value) in enumerate(rows_data):
        row = table.rows[i + 1]
        bg = 'F5F7FA' if i % 2 == 0 else 'FFFFFF'
        set_cell_bg(row.cells[0], bg)
        set_cell_bg(row.cells[1], bg)
        label_run = row.cells[0].paragraphs[0].add_run(label)
        label_run.font.bold = True
        label_run.font.size = Pt(9.5)
        label_run.font.color.rgb = NAVY
        val_run = row.cells[1].paragraphs[0].add_run(value)
        val_run.font.size = Pt(9.5)
        val_run.font.color.rgb = RGBColor(0x22, 0x22, 0x22)

    # ── REQUIRED DOCUMENTS ──
    doc.add_paragraph()
    docs_heading = doc.add_paragraph()
    docs_run = docs_heading.add_run('Required Hiring Documents:')
    docs_run.font.bold = True
    docs_run.font.size = Pt(10.5)
    docs_run.font.color.rgb = NAVY

    arabic_docs = [
        'أصل شهادة الميلاد',
        'أصل شهادة المؤهل الدراسي',
        'أصل شهادة الخدمة العسكرية للذكور',
        'كعب عمل',
        'صورة بطاقة الرقم القومي',
        'عدد 6 صور شخصية حديثة',
        'صحيفة الحالة الجنائية',
        'بيان بآخر راتب / مفردات مرتب',
        'نموذج 111 خاص بالتأمين الصحي',
    ]
    for doc_item in arabic_docs:
        p = doc.add_paragraph(style='List Bullet')
        run = p.add_run(doc_item)
        run.font.size = Pt(9.5)
        run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)

    # ── OFFERED BY SECTION ──
    doc.add_paragraph()
    offered_table = doc.add_table(rows=1, cols=2)
    offered_table.autofit = False
    offered_table.columns[0].width = Cm(9)
    offered_table.columns[1].width = Cm(8.5)

    left_cell = offered_table.cell(0, 0)
    p = left_cell.paragraphs[0]
    run = p.add_run('Candidate Acceptance')
    run.font.bold = True
    run.font.size = Pt(9.5)
    run.font.color.rgb = NAVY
    left_cell.add_paragraph()
    for line in ('Name: ________________________________', 'Signature: ___________________________', 'Date: ________________________________'):
        sig_p = left_cell.add_paragraph()
        sig_r = sig_p.add_run(line)
        sig_r.font.size = Pt(9)
        sig_r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
        left_cell.add_paragraph()

    right_cell = offered_table.cell(0, 1)
    p2 = right_cell.paragraphs[0]
    run2 = p2.add_run('Offered By')
    run2.font.bold = True
    run2.font.size = Pt(9.5)
    run2.font.color.rgb = NAVY
    right_cell.add_paragraph()
    for line in ('Name: ________________________________', 'Signature: ___________________________', 'Date: ________________________________'):
        auth_p = right_cell.add_paragraph()
        auth_r = auth_p.add_run(line)
        auth_r.font.size = Pt(9)
        auth_r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
        right_cell.add_paragraph()

    # ── GOLD FOOTER LINE ──
    footer_div = doc.add_paragraph()
    footer_div.paragraph_format.space_before = Pt(12)
    pPr2 = footer_div._p.get_or_add_pPr()
    pBdr2 = OxmlElement('w:pBdr')
    top2 = OxmlElement('w:top')
    top2.set(qn('w:val'), 'single')
    top2.set(qn('w:sz'), '6')
    top2.set(qn('w:color'), 'C9A84C')
    pBdr2.append(top2)
    pPr2.append(pBdr2)

    # ── POWERED BY ──
    powered = doc.add_paragraph()
    powered.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pw_run = powered.add_run('Powered by Hunters HR  |  hunters-egypt.com')
    pw_run.font.size = Pt(7.5)
    pw_run.font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
    pw_run.font.italic = True

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
