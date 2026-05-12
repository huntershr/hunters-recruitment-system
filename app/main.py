from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from .database import engine, Base, SessionLocal
from .routers import jobs, candidates, evaluations, sheets, auth, public, companies
from .routers.auth import get_current_user
from . import models, auth_utils
from .services.ai_evaluator import generate_job_details
import logging
import os
import json
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI Recruitment System",
    description="An AI-powered recruitment system using FastAPI and Google Gemini API.",
    version="1.0.0"
)

# Add CORS middleware to allow the frontend to communicate with the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_populate_db():
    """
    Initialize database on startup and create default admin user.
    """
    db = SessionLocal()
    try:
        logging.info("Application startup - initializing database")
        # Create default admin user if it doesn't exist yet.
        # NOTE: In production (e.g. Railway) the DB may already contain users,
        # so we must not rely on "users table is empty" for seeding.
        try:
            admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
            admin_password = os.getenv("ADMIN_PASSWORD", "admin123")

            existing_admin = db.query(models.User).filter(models.User.email == admin_email).first()
            if existing_admin is None:
                logging.info(f"Creating default admin user: {admin_email}")
                hashed_pw = auth_utils.get_password_hash(admin_password)
                admin = models.User(
                    email=admin_email,
                    hashed_password=hashed_pw,
                    full_name="Administrator",
                    is_admin=True,
                    is_active=True,
                )
                db.add(admin)
                db.commit()
                logging.info("Default admin user created (email from ADMIN_EMAIL, password from ADMIN_PASSWORD).")
            else:
                # Ensure the seeded admin remains active/admin if it already exists.
                updated = False
                if not existing_admin.is_admin:
                    existing_admin.is_admin = True
                    updated = True
                if not existing_admin.is_active:
                    existing_admin.is_active = True
                    updated = True

                # Optional, controlled password reset for recovery.
                # Set ADMIN_RESET_PASSWORD=true temporarily to force reset.
                reset_flag = os.getenv("ADMIN_RESET_PASSWORD", "").strip().lower() in {"1", "true", "yes", "y", "on"}
                if reset_flag:
                    existing_admin.hashed_password = auth_utils.get_password_hash(admin_password)
                    updated = True
                    logging.warning(f"ADMIN_RESET_PASSWORD is enabled. Resetting password for: {admin_email}")

                if updated:
                    db.commit()
                    logging.info(f"Updated existing admin flags for: {admin_email}")
        except Exception as e:
            logging.warning(f"Could not create default admin user: {e}")
            db.rollback()
    finally:
        db.close()

app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(public.router)

app.include_router(jobs.router)
app.include_router(candidates.router)
app.include_router(evaluations.router)
app.include_router(sheets.router)

class GenerateJobRequest(BaseModel):
    job_title: str
    industry_background: str
    additional_context: str = ""

class GenerateCVRequest(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    experiences: list = []
    skills: list = []
    education: str = ""
    industry: str = ""

@app.post("/api/ai/generate-job")
async def generate_job_ai(
    request: GenerateJobRequest,
    current_user: models.User = Depends(get_current_user)
):
    if not os.getenv("GEMINI_API_KEY", ""):
        raise HTTPException(status_code=503, detail="AI service not configured")
    try:
        result = generate_job_details(
            job_title=request.job_title,
            industry_background=request.industry_background,
            additional_context=request.additional_context or "",
        )
        return JSONResponse(content={
            "job_brief":         result.get("job_brief", ""),
            "required_skills":   result.get("required_skills", ""),
            "nice_to_have":      result.get("nice_to_have", ""),
            "behavioral_skills": result.get("behavioral_skills", ""),
        })
    except Exception as e:
        logging.error("generate_job_ai endpoint error: %s", e)
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

@app.post("/api/ai/generate-cv")
async def generate_cv_ai(
    request: GenerateCVRequest,
    current_user: models.User = Depends(get_current_user)
):
    if not os.getenv("GEMINI_API_KEY", ""):
        raise HTTPException(status_code=503, detail="AI service not configured")
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""You are a professional CV writer at Hunters for HR Transformation & Execution.

Create a polished, professional CV. Return ONLY valid JSON with no markdown fences:
{{
  "name": "{request.name}",
  "headline": "professional headline based on their experience",
  "summary": "3-4 sentence professional summary",
  "experience": [{{"title":"job title","company":"employer","duration":"duration","achievements":["achievement 1","achievement 2","achievement 3"]}}],
  "skills": ["skill1","skill2","skill3"],
  "education": "education details",
  "languages": ["Arabic","English"],
  "certifications": []
}}

Candidate: {request.name}
Email: {request.email}
Phone: {request.phone}
Experiences: {json.dumps(request.experiences)}
Skills: {json.dumps(request.skills)}
Education: {request.education}
Industry: {request.industry}"""
        response = model.generate_content(prompt)
        text = response.text.strip()
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
        cv_data = json.loads(text)
        return JSONResponse(content=cv_data)
    except Exception as e:
        logging.error("generate_cv_ai error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/extract-cv")
async def extract_cv_ai(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user)
):
    if not os.getenv("GEMINI_API_KEY", ""):
        raise HTTPException(status_code=503, detail="AI service not configured")
    try:
        contents = await file.read()
        filename = file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        text = ""
        if ext == "pdf":
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(contents))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif ext == "docx":
            import io
            from docx import Document
            doc = Document(io.BytesIO(contents))
            text = "\n".join(p.text for p in doc.paragraphs)
        else:
            text = contents.decode("utf-8", errors="ignore")
        if not text.strip():
            raise HTTPException(status_code=400, detail="Could not extract text from file")
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""Extract structured profile data from this CV/resume text.
Return ONLY valid JSON with no markdown fences:
{{
  "name": "full name",
  "email": "email address or empty string",
  "phone": "phone number or empty string",
  "industry": "main industry or field",
  "education": "education summary as one string",
  "skills": ["skill1", "skill2"],
  "experiences": [
    {{"title": "job title", "company": "company name", "duration": "e.g. 2020-2023"}}
  ]
}}

CV Text:
{text[:8000]}"""
        response = model.generate_content(prompt)
        raw = response.text.strip()
        if "```" in raw:
            start = raw.find("{"); end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]
        profile_data = json.loads(raw)
        return JSONResponse(content=profile_data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI returned invalid data format")
    except Exception as e:
        logging.error("extract_cv_ai error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "healthy"}

# Mount the frontend directory to serve HTML/JS/CSS
try:
    frontend_path = Path(__file__).parent.parent / "frontend"
    if frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
        logging.info(f"Frontend mounted from: {frontend_path}")
    else:
        logging.warning(f"Frontend directory not found at: {frontend_path}")
except Exception as e:
    logging.warning(f"Failed to mount frontend: {e}")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
