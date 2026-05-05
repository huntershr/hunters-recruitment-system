from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .database import engine, Base, SessionLocal
from .routers import jobs, candidates, evaluations, sheets, auth, public, companies
from . import models, auth_utils
import logging
import os
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
        # Create default admin user if none exists
        try:
            if db.query(models.User).count() == 0:
                logging.info("Creating default admin user...")
                hashed_pw = auth_utils.get_password_hash("admin123")
                admin = models.User(
                    email="admin@example.com",
                    hashed_password=hashed_pw,
                    full_name="Administrator",
                    is_admin=True,
                    is_active=True
                )
                db.add(admin)
                db.commit()
                logging.info("Default admin user created: admin@example.com / admin123")
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
