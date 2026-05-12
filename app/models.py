from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, JSON, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, index=True, unique=True)
    company_email = Column(String, index=True, unique=True)
    company_website = Column(String, nullable=True)
    registration_number = Column(String, nullable=True)
    is_approved = Column(Boolean, default=False)
    approval_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    users = relationship("User", back_populates="company")

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)

    company = relationship("Company", back_populates="users")
    jobs = relationship("Job", back_populates="owner")
    candidates = relationship("Candidate", back_populates="owner")

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_title = Column(String, index=True)
    job_description = Column(Text, nullable=True)
    job_location = Column(String, nullable=True)
    min_experience = Column(Integer)
    required_skills = Column(Text)
    nice_to_have_skills = Column(Text)
    education_level = Column(String)
    salary_range = Column(String, nullable=True)
    behavioral_skills = Column(Text, nullable=True)
    industry_experience = Column(Text, nullable=True)
    weight_experience = Column(Float, default=0.3)
    weight_skills = Column(Float, default=0.4)
    weight_education = Column(Float, default=0.1)
    weight_behavioral = Column(Float, default=0.2)
    owner_id = Column(Integer, ForeignKey("users.id"))
    is_approved = Column(Boolean, default=False)
    approval_date = Column(DateTime, nullable=True)
    approval_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="jobs")
    candidates = relationship("Candidate", back_populates="job")


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, index=True)
    phone = Column(String)
    job_applied = Column(Integer, ForeignKey("jobs.id"))
    experience_years = Column(Integer)
    expected_salary = Column(String, nullable=True)
    education = Column(String)
    skills = Column(Text)
    cv_text = Column(Text)
    last_title = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"))

    owner = relationship("User", back_populates="candidates")
    job = relationship("Job", back_populates="candidates")
    evaluation = relationship("Evaluation", back_populates="candidate", uselist=False)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), unique=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    score = Column(Float)
    decision = Column(String) # Accept / Maybe / Reject
    reason = Column(Text)
    strengths = Column(Text, nullable=True) # Bonus feature
    weaknesses = Column(Text, nullable=True) # Bonus feature
    suggested_interview_questions = Column(JSON, nullable=True)

    candidate = relationship("Candidate", back_populates="evaluation")
