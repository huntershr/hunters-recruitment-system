from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from .database import engine, Base, SessionLocal
from .routers import jobs, candidates, evaluations, sheets, auth, public, companies
from .routers.auth import get_current_user
from . import models, auth_utils
import logging
import os
import json
from pathlib import Path
import google.generativeai as genai
import time
import random

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

@app.post("/api/ai/generate-job")
async def generate_job_ai(
    request: GenerateJobRequest,
    current_user: models.User = Depends(get_current_user)
):
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="AI service not configured")

    job_title = request.job_title
    industry_background = request.industry_background
    additional_context = request.additional_context or "None"

    prompt = f"""You are a senior HR specialist at Hunters for HR Transformation & Execution.

Generate professional job posting details based on the following inputs:
- Job Title: {job_title}
- Industry Background: {industry_background}
- Additional Context: {additional_context}

Return ONLY a valid JSON object with no markdown, no extra text, no code blocks:
{{
  "job_brief": "A professional 3-4 sentence job description that describes the role, responsibilities, and what makes it exciting. Tailored specifically to the {industry_background} industry.",
  "required_skills": "comma separated list of 6-8 must-have technical and professional skills specific to {job_title} in {industry_background}",
  "nice_to_have": "comma separated list of 4-5 additional skills that would be a bonus for {job_title}",
  "behavioral_skills": "comma separated list of 4-5 behavioral competencies important for {job_title} such as communication, teamwork, adaptability"
}}

Be specific and realistic. Base skills on actual industry standards for {industry_background}."""

    model_name = os.getenv("GEMINI_MODEL", "models/gemini-1.5-flash")
    max_retries = 3

    for attempt in range(max_retries):
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name,
                system_instruction="You are a senior HR specialist. You must output only valid JSON without any markdown blocks or explanations."
            )
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.4,
                    response_mime_type="application/json",
                )
            )

            raw = response.text.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]

            result = json.loads(raw.strip())
            return JSONResponse(content={
                "job_brief":        result.get("job_brief", ""),
                "required_skills":  result.get("required_skills", ""),
                "nice_to_have":     result.get("nice_to_have", ""),
                "behavioral_skills": result.get("behavioral_skills", "")
            })

        except Exception as e:
            logging.error(f"AI job generation failed (attempt {attempt + 1}): {e}")
            if "429" in str(e) and attempt < max_retries - 1:
                wait = (2 ** attempt) * 2 + random.random()
                time.sleep(wait)
                continue
            if attempt == max_retries - 1:
                raise HTTPException(status_code=500, detail="AI generation failed. Please try again.")

    raise HTTPException(status_code=500, detail="AI generation failed after retries.")

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
