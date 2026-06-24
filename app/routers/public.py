from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from typing import List
import logging
import json
import secrets
import string
import time

from .. import models, schemas, database
from ..services.file_processor import extract_text_from_file
from ..services.ai_evaluator import extract_candidate_info, evaluate_candidate, finalize_evaluation
from ..services.agent_screener import call_agent_screen
from ..auth_utils import get_password_hash

logger = logging.getLogger(__name__)


def generate_temp_password(length: int = 12) -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def send_candidate_welcome_email(to_email: str, name: str, temp_password: str):
    """Best-effort welcome email for auto-created candidate accounts. Never raises."""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, From, To, Subject, HtmlContent
        import os

        api_key = os.getenv("SENDGRID_API_KEY", "")
        if not api_key:
            logger.warning("SENDGRID_API_KEY not set — skipping candidate welcome email")
            return

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family:'Segoe UI',Arial,sans-serif;background:#F5F6F8;padding:40px 0;margin:0">
          <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
            <div style="background:#1B2A4A;padding:24px 32px;text-align:center">
              <div style="color:#C9A84C;font-size:11px;letter-spacing:3px;text-transform:uppercase">Hunters for HR Transformation</div>
              <div style="color:#fff;font-size:20px;font-weight:600;margin-top:6px">Application Received!</div>
            </div>
            <div style="padding:32px">
              <p style="color:#1B2A4A;font-size:15px;margin:0 0 16px">Dear {name},</p>
              <p style="color:#555;font-size:14px">Your application has been submitted successfully. We have created an account for you to track your application status.</p>
              <div style="background:#F5F6F8;border-radius:8px;padding:16px;margin:20px 0">
                <p style="margin:0;font-size:13px;color:#1B2A4A"><strong>Login Email:</strong> {to_email}</p>
                <p style="margin:8px 0 0;font-size:13px;color:#1B2A4A"><strong>Temporary Password:</strong> {temp_password}</p>
              </div>
              <p style="color:#555;font-size:13px">Use these credentials to log in and track your application status at any time.</p>
              <div style="text-align:center;margin-top:24px">
                <a href="https://app.hunters-egypt.com" style="background:#C9A84C;color:#1B2A4A;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;font-size:14px">Track My Application</a>
              </div>
              <p style="color:#888;font-size:12px;margin-top:20px">Please change your password after first login for security.</p>
            </div>
            <div style="background:#F5F6F8;padding:14px 32px;text-align:center">
              <p style="color:#aaa;font-size:11px;margin:0">Powered by Hunters HR · hr@hunters-egypt.com</p>
            </div>
          </div>
        </body>
        </html>
        """

        message = Mail(
            from_email=From("hr@hunters-egypt.com", "Hunters HR"),
            to_emails=To(to_email),
            subject=Subject("Your Application is Submitted — Hunters HR"),
            html_content=HtmlContent(html_content)
        )

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        logger.info(f"Candidate welcome email sent to {to_email} — status {response.status_code}")
    except Exception as e:
        logger.error(f"Candidate welcome email failed for {to_email}: {e}")


def run_evaluation_task_for_application(application_id: int, cv_text: str, db: Session):
    """Background evaluation for Type B (anonymous) applications — no Candidate row."""
    try:
        application = db.query(models.Application).filter(
            models.Application.id == application_id
        ).first()
        if not application:
            logger.error(f"Application {application_id} not found for evaluation")
            return

        job = db.query(models.Job).filter(models.Job.id == application.job_id).first()
        if not job:
            logger.error(f"Job {application.job_id} not found for application {application_id}")
            return

        info = extract_candidate_info(cv_text)

        # Write extracted ATS fields back to the Candidate row (additive — never overwrites populated values)
        if application.candidate_id:
            try:
                cand = db.query(models.Candidate).filter(
                    models.Candidate.id == application.candidate_id
                ).first()
                if cand:
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
                    logger.info(f"ATS fields written to candidate {application.candidate_id}")
            except Exception as _ats_err:
                logger.error(f"ATS field write-back failed for candidate {application.candidate_id}: {_ats_err}")
                db.rollback()

            # ── Agent candidate_profile save (additive, best-effort) ──────────────
            try:
                agent_resp = call_agent_screen(cv_text, job)
                cp = (agent_resp or {}).get("candidate_profile") or {}
                if cp:
                    _cand = db.query(models.Candidate).filter(
                        models.Candidate.id == application.candidate_id
                    ).first()
                    if _cand:
                        if not _cand.last_title:
                            v = (cp.get("current_title") or "").strip()
                            if v: _cand.last_title = v
                        if not _cand.last_employer:
                            v = (cp.get("last_employer") or "").strip()
                            if v: _cand.last_employer = v
                        if not (_cand.experience_years or 0):
                            v = cp.get("years_experience")
                            if v: _cand.experience_years = int(v)
                        if not _cand.education:
                            v = (cp.get("education") or "").strip()
                            if v: _cand.education = v
                        if not _cand.skills:
                            v = cp.get("skills")
                            if isinstance(v, list): v = ", ".join(str(x) for x in v if x)
                            if v: _cand.skills = str(v).strip()
                        if not _cand.languages:
                            v = cp.get("languages")
                            if isinstance(v, list) and v: _cand.languages = v
                        if not _cand.certifications:
                            v = cp.get("certifications")
                            if isinstance(v, list): v = ", ".join(str(x) for x in v if x)
                            if v: _cand.certifications = str(v).strip()
                        db.commit()
                        logger.info(f"Agent candidate_profile saved to candidate {application.candidate_id}")
            except Exception as _ap_err:
                logger.error(f"Agent profile save failed for candidate {application.candidate_id}: {_ap_err}")
                db.rollback()

        import types
        applicant = types.SimpleNamespace(
            name=application.applicant_name or "",
            experience_years=int(info.get("experience_years") or 0),
            skills=str(info.get("skills") or ""),
            education=str(info.get("education") or ""),
            cv_text=cv_text,
        )

        raw = None
        for _attempt in range(3):
            try:
                raw = evaluate_candidate(job, applicant)
                break
            except Exception as _ev_err:
                _es = str(_ev_err)
                if ("429" in _es or "timeout" in _es.lower() or "deadline" in _es.lower()) and _attempt < 2:
                    _wait = (_attempt + 1) * 10
                    logger.warning(f"Gemini eval error (attempt {_attempt+1}/3), retrying in {_wait}s: {_ev_err}")
                    time.sleep(_wait)
                else:
                    logger.error(f"Gemini eval non-retriable error (attempt {_attempt+1}/3): {_ev_err}")
                    break
        result = finalize_evaluation(raw or {})

        _lstr = lambda v: "\n".join(f"- {x}" for x in v if x) if isinstance(v, list) else str(v or "")

        _bd = result.get("score_breakdown") or {}
        db_eval = models.Evaluation(
            application_id=application.id,
            candidate_id=application.candidate_id,
            job_id=job.id,
            score=result.get("score", 0.0),
            score_experience=_bd.get("experience"),
            score_skills=_bd.get("skills"),
            score_education=_bd.get("education"),
            score_behavioral=_bd.get("behavioral"),
            decision=result.get("decision", "Reject"),
            # legacy columns — populated from bilingual fields for backward compat
            reason=result.get("summary_en") or result.get("reason", "Failed to evaluate"),
            strengths=_lstr(result.get("strengths_en") or result.get("strengths") or []),
            weaknesses=_lstr(result.get("gaps_en") or result.get("weaknesses") or []),
            suggested_interview_questions=result.get("interview_questions_en") or result.get("suggested_interview_questions") or [],
            # new bilingual columns
            summary_en=result.get("summary_en"),
            summary_ar=result.get("summary_ar"),
            strengths_ar=_lstr(result.get("strengths_ar") or []),
            gaps_en=_lstr(result.get("gaps_en") or []),
            gaps_ar=_lstr(result.get("gaps_ar") or []),
            interview_questions_ar=result.get("interview_questions_ar"),
            quick_facts=result.get("quick_facts"),
        )
        db.add(db_eval)
        db.commit()
        logger.info(f"Finished evaluation for application {application_id}")
    except Exception as e:
        logger.error(f"Evaluation task failed for application {application_id}: {e}")
        try:
            db.rollback()
            _app_fb = db.query(models.Application).filter(models.Application.id == application_id).first()
            if _app_fb and not db.query(models.Evaluation).filter(models.Evaluation.application_id == application_id).first():
                db.add(models.Evaluation(
                    application_id=application_id,
                    candidate_id=_app_fb.candidate_id,
                    job_id=_app_fb.job_id,
                    score=0.0,
                    decision="Pending Review",
                    reason="AI screening could not be completed automatically. Please screen this candidate manually.",
                    strengths="",
                    weaknesses="- Automatic screening failed — manual review required",
                ))
                db.commit()
                logger.info(f"Fallback evaluation created for application {application_id}")
        except Exception as _fb_err:
            logger.error(f"Fallback eval creation failed for application {application_id}: {_fb_err}")
            db.rollback()
    finally:
        db.close()

router = APIRouter(
    prefix="/public",
    tags=["Public"]
)

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/job/{job_id}")
def get_public_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == 'rejected' or job.is_archived:
        raise HTTPException(status_code=404, detail="Job not found")

    HUNTERS_LOGO = "/hunters-logo-blue.jpeg"
    company_name = None
    company_id = None
    company_logo_url = None
    if job.owner:
        if job.owner.is_admin:
            company_name = "Hunters for HR Solutions"
            company_logo_url = HUNTERS_LOGO
        elif job.owner.company_id:
            company = db.query(models.Company).filter(models.Company.id == job.owner.company_id).first()
            if company:
                company_name = company.company_name
                company_id = company.id
                company_logo_url = company.logo_url or None

    hide_salary = bool(job.hide_salary)
    return {
        "id": job.id,
        "job_title": job.job_title,
        "job_description": job.job_description or "",
        "job_location": job.job_location or "",
        "min_experience": job.min_experience,
        "required_skills": job.required_skills or "",
        "nice_to_have_skills": job.nice_to_have_skills,
        "behavioral_skills": job.behavioral_skills,
        "education_level": job.education_level or "",
        "salary_range": "" if hide_salary else (job.salary_range or ""),
        "industry_experience": job.industry_experience,
        "weight_experience": job.weight_experience,
        "weight_skills": job.weight_skills,
        "weight_education": job.weight_education,
        "weight_behavioral": job.weight_behavioral,
        "is_approved": job.is_approved,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "salary_min": None,
        "salary_max": None,
        "employment_type": job.education_level,
        "hide_salary": hide_salary,
        "department": job.department or "Other",
        "company_name": company_name,
        "company_id": company_id,
        "company_logo_url": company_logo_url,
    }

@router.post("/apply/{job_id}")
async def public_apply(
    job_id: int,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    expected_salary: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    fname = (file.filename or "").lower()
    if not (fname.endswith(".pdf") or fname.endswith(".docx") or fname.endswith(".doc")):
        raise HTTPException(
            status_code=400,
            detail="Please upload your CV as a PDF or Word document (.pdf or .docx). Images and other file types are not accepted.",
        )
    content = await file.read()
    cv_text = extract_text_from_file(file.filename, content)
    if not cv_text or not cv_text.strip():
        raise HTTPException(
            status_code=422,
            detail="Your CV could not be read — it may be a scanned image or a protected file. Please save it as a text-based PDF or upload a .docx file.",
        )

    # Resolve MIME type for original file storage
    _fname = (file.filename or "").lower()
    _ct = file.content_type or ""
    if _ct and _ct not in ("application/octet-stream", "binary/octet-stream"):
        cv_mime = _ct
    elif _fname.endswith(".pdf"):
        cv_mime = "application/pdf"
    elif _fname.endswith(".docx"):
        cv_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        cv_mime = "application/octet-stream"

    # Duplicate check: one Type B Application per (email, job) pair
    existing_app = (
        db.query(models.Application)
        .filter(
            models.Application.applicant_email.ilike(email),
            models.Application.job_id == job_id,
            models.Application.candidate_id.is_(None),
        )
        .first()
    )
    if existing_app:
        raise HTTPException(
            status_code=409,
            detail="An application with this email already exists for this job.",
        )

    # Phase 2 Type B: create Application directly — no Candidate row.
    # Email match to an existing User does NOT auto-link (T5); explicit
    # auth is required for Type A.
    application = models.Application(
        job_id=job_id,
        candidate_id=None,
        applicant_name=name,
        applicant_email=email,
        applicant_phone=phone,
        expected_salary=expected_salary,
        stage="Applied",
        cv_text=cv_text,
        cv_file_data=content,
        cv_file_mime=cv_mime,
    )
    db.add(application)
    db.commit()
    db.refresh(application)

    # Talent pool upsert: ensure every external applicant has a Candidate row
    # so they appear in the pipeline even after the job closes.
    existing_candidate = (
        db.query(models.Candidate)
        .filter(models.Candidate.email.ilike(email))
        .first()
    )
    if existing_candidate:
        application.candidate_id = existing_candidate.id
        db.commit()
    else:
        new_candidate = models.Candidate(
            name=name,
            email=email,
            phone=phone,
            job_applied=job_id,
            experience_years=0,
            expected_salary=expected_salary,
            education="",
            skills="",
            cv_text=cv_text,
            cv_file_data=content,
            cv_file_mime=cv_mime,
            owner_id=job.owner_id,
            user_id=None,
        )
        db.add(new_candidate)
        db.commit()
        db.refresh(new_candidate)
        application.candidate_id = new_candidate.id
        db.commit()

    # Auto-create a candidate login account so applicants can track their
    # application status. Best-effort only — never blocks the application.
    try:
        candidate_row = existing_candidate if existing_candidate else new_candidate
        email_lower = (email or "").strip().lower()
        existing_user = (
            db.query(models.User)
            .filter(
                func.lower(models.User.email) == email_lower,
                models.User.is_admin == False,
                models.User.company_id.is_(None),
            )
            .first()
        )
        if not existing_user:
            temp_password = generate_temp_password()
            new_user = models.User(
                email=email_lower,
                hashed_password=get_password_hash(temp_password),
                full_name=name,
                is_active=True,
                is_admin=False,
                company_id=None,
            )
            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            candidate_row.user_id = new_user.id
            db.commit()

            background_tasks.add_task(send_candidate_welcome_email, email, name, temp_password)
        elif candidate_row.user_id is None:
            candidate_row.user_id = existing_user.id
            db.commit()
    except Exception as e:
        logger.error(f"Auto-create candidate account failed for {email}: {e}")

    from ..database import SessionLocal
    background_tasks.add_task(
        run_evaluation_task_for_application,
        application.id, cv_text, SessionLocal()
    )

    return {
        "message": "Application submitted successfully",
        "application_id": application.id,
        "job_id": job_id,
    }

@router.get("/evaluation/{candidate_id}")
def get_candidate_evaluation(candidate_id: int, db: Session = Depends(get_db)):
    """
    Public endpoint to fetch candidate evaluation score.
    Called by candidates to see their results after applying.
    """
    candidate = db.query(models.Candidate).filter(models.Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    
    evaluation = db.query(models.Evaluation).filter(models.Evaluation.candidate_id == candidate_id).first()
    if not evaluation:
        return {
            "candidate_id": candidate_id,
            "name": candidate.name,
            "status": "pending",
            "message": "Your application is being evaluated. Please check back shortly."
        }
    
    pct = float(evaluation.score or 0)
    return {
        "candidate_id": candidate_id,
        "name": candidate.name,
        "status": "completed",
        "score": pct,
        "overall_score": int(round(min(100.0, max(0.0, pct)))),
        "decision": evaluation.decision,
        "reason": evaluation.reason,
        "strengths": evaluation.strengths,
        "weaknesses": evaluation.weaknesses,
    }

@router.get("/company/{company_id}", response_model=schemas.CompanyResponse)
def get_public_company(company_id: int, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(
        models.Company.id == company_id,
        models.Company.is_approved == True
    ).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found or not approved")
    return company

@router.get("/company/{company_id}/jobs", response_model=List[schemas.JobResponse])
def get_public_company_jobs(company_id: int, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(
        models.Company.id == company_id,
        models.Company.is_approved == True
    ).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found or not approved")
    users = db.query(models.User).filter(models.User.company_id == company_id).all()
    user_ids = [u.id for u in users]
    jobs = db.query(models.Job).filter(
        models.Job.owner_id.in_(user_ids),
        models.Job.is_approved == True
    ).all()
    return jobs

@router.get("/jobs")
def get_public_jobs(department: str = None, db: Session = Depends(get_db)):
    """
    Get all approved public job postings with company info.
    Only shows jobs from approved companies.
    """
    import traceback as _tb
    print("=== PUBLIC JOBS ENDPOINT CALLED ===")
    try:
        approved_companies = db.query(models.Company).filter(
            models.Company.is_approved == True
        ).all()
        approved_company_ids = [c.id for c in approved_companies]
        company_map = {c.id: c.company_name for c in approved_companies}
        logo_map    = {c.id: c.logo_url for c in approved_companies}
        print(f"=== APPROVED COMPANIES: {len(approved_company_ids)} ===")

        # Build filter: approved, non-archived jobs from admin users OR approved companies
        filters = [models.Job.is_approved == True, models.Job.is_archived == False]
        if approved_company_ids:
            filters.append(or_(
                models.User.is_admin == True,
                models.User.company_id.in_(approved_company_ids)
            ))
        else:
            filters.append(models.User.is_admin == True)

        if department:
            filters.append(models.Job.department == department)

        try:
            jobs = db.query(models.Job).join(
                models.User, models.Job.owner_id == models.User.id
            ).filter(*filters).all()
            print(f"=== JOBS QUERY OK: {len(jobs)} rows ===")
        except Exception as qe:
            print(f"=== JOBS QUERY FAILED: {qe} ===")
            print(_tb.format_exc())
            raise

        result = []
        for job in jobs:
            try:
                owner = job.owner
                company_id = owner.company_id if owner else None
                HUNTERS_LOGO = "/hunters-logo-blue.jpeg"
                if owner and owner.is_admin:
                    company_name = "Hunters for HR Solutions"
                    company_id = None
                else:
                    company_name = company_map.get(company_id, "Unknown Company") if company_id else "Unknown Company"
                hide_salary = bool(getattr(job, 'hide_salary', False))
                result.append({
                    "id": job.id,
                    "job_title": job.job_title,
                    "job_description": job.job_description or "",
                    "job_location": job.job_location or "",
                    "min_experience": job.min_experience,
                    "required_skills": job.required_skills or "",
                    "nice_to_have_skills": job.nice_to_have_skills,
                    "behavioral_skills": job.behavioral_skills,
                    "education_level": job.education_level or "",
                    "salary_range": "" if hide_salary else (job.salary_range or ""),
                    "weight_experience": job.weight_experience,
                    "weight_skills": job.weight_skills,
                    "weight_education": job.weight_education,
                    "weight_behavioral": job.weight_behavioral,
                    "is_approved": job.is_approved,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "company_name": company_name,
                    "company_id": company_id,
                    "company_logo_url": HUNTERS_LOGO if (owner and owner.is_admin) else (logo_map.get(company_id) if company_id else None),
                    "hide_salary": hide_salary,
                    "salary_min": None,
                    "salary_max": None,
                    "employment_type": job.education_level,
                    "department": job.department or "Other",
                })
            except Exception as job_err:
                print(f"=== ERROR SERIALIZING JOB {job.id}: {job_err} ===")
                logger.error(f"Error serializing job {job.id}: {job_err}")
                continue
        print(f"=== RETURNING {len(result)} JOBS ===")
        return result
    except Exception as e:
        print(f"=== PUBLIC JOBS 500 ERROR: {e} ===")
        print(_tb.format_exc())
        logger.error(f"get_public_jobs error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load jobs: {str(e)}")
