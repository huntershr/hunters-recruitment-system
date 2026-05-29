import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

# Fallback to SQLite if DATABASE_URL is not set
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL") or os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL or "sqlite" in SQLALCHEMY_DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set to PostgreSQL. SQLite is not allowed.")

if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
