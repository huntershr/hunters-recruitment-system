from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .database import engine, Base, SessionLocal
from .routers import jobs, candidates, evaluations, sheets, auth, public
from . import models, auth_utils
import logging

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
    db = SessionLocal()
    try:
        # Create default user if none exists
        if db.query(models.User).count() == 0:
            logging.info("Creating default admin user...")
            hashed_pw = auth_utils.get_password_hash("admin123")
            admin = models.User(
                email="admin@example.com",
                hashed_password=hashed_pw,
                full_name="Administrator"
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()

app.include_router(auth.router)
app.include_router(public.router)

app.include_router(jobs.router)
app.include_router(candidates.router)
app.include_router(evaluations.router)
app.include_router(sheets.router)

@app.get("/health")
def health_check():
    return {"status": "healthy"}

from pathlib import Path

# Mount the frontend directory to serve HTML/JS/CSS
frontend_path = Path(__file__).parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
