from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, JSON, Boolean, DateTime, LargeBinary, Date, Time
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
    plan = Column(String, default='free', nullable=True)
    plan_expires_at = Column(DateTime, nullable=True)
    billing_status = Column(String, default='active', nullable=True)
    logo_url = Column(Text, nullable=True)

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
    # Candidates owned/managed by this user (employer relationship via owner_id)
    candidates = relationship("Candidate", back_populates="owner", foreign_keys="[Candidate.owner_id]")
    # The single Candidate profile row belonging to this user as a job-seeker (via user_id)
    candidate_profile = relationship("Candidate", back_populates="user", foreign_keys="[Candidate.user_id]", uselist=False)

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
    hide_salary = Column(Boolean, default=False, nullable=True)
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
    applications = relationship("Application", back_populates="job")


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, index=True)
    phone = Column(String)
    job_applied = Column(Integer, ForeignKey("jobs.id"))
    experience_years = Column(Integer)
    expected_salary = Column(String, nullable=True)
    education = Column(String)       # legacy VARCHAR — keep for existing data
    skills = Column(Text)
    cv_text = Column(Text)
    last_title = Column(String, nullable=True)
    last_employer = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"))   # employer who manages this record
    # Phase 1.1 — new columns (all nullable, additive)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # the candidate's own User account
    photo_url = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    location = Column(String, nullable=True)
    experiences = Column(JSON, nullable=True)       # [{title, company, from, to, desc}]
    education_history = Column(JSON, nullable=True) # [{degree, institution, year}]  (replaces legacy education VARCHAR in future)
    languages = Column(JSON, nullable=True)         # [{language, level}]
    cv_file_data = Column(LargeBinary, nullable=True)  # original uploaded CV file bytes
    cv_file_mime = Column(Text, nullable=True)          # MIME type of the original file

    owner = relationship("User", back_populates="candidates", foreign_keys=[owner_id])
    user = relationship("User", back_populates="candidate_profile", foreign_keys=[user_id])
    job = relationship("Job", back_populates="candidates")
    evaluations = relationship("Evaluation", back_populates="candidate")
    applications = relationship("Application", back_populates="candidate")


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=True)
    applicant_name = Column(Text, nullable=True)
    applicant_email = Column(Text, nullable=True)
    applicant_phone = Column(Text, nullable=True)
    cv_file_path = Column(Text, nullable=True)
    cv_text = Column(Text, nullable=True)
    cv_file_data = Column(LargeBinary, nullable=True)  # original uploaded CV file bytes
    cv_file_mime = Column(Text, nullable=True)          # MIME type of the original file
    expected_salary = Column(Text, nullable=True)
    stage = Column(Text, default='New')
    stage_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="applications")
    candidate = relationship("Candidate", back_populates="applications")
    evaluation = relationship("Evaluation", back_populates="application", uselist=False)
    interview = relationship("Interview", back_populates="application", uselist=False)
    offer = relationship("Offer", back_populates="application", uselist=False)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=True)
    score = Column(Float)
    score_experience = Column(Float, nullable=True)
    score_skills = Column(Float, nullable=True)
    score_education = Column(Float, nullable=True)
    score_behavioral = Column(Float, nullable=True)
    decision = Column(String) # Accept / Maybe / Reject
    reason = Column(Text)
    strengths = Column(Text, nullable=True) # Bonus feature
    weaknesses = Column(Text, nullable=True) # Bonus feature
    suggested_interview_questions = Column(JSON, nullable=True)

    candidate = relationship("Candidate", back_populates="evaluations")
    application = relationship("Application", back_populates="evaluation")


class Interview(Base):
    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    scheduled_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    interview_date = Column(Date, nullable=False)
    interview_time = Column(Time, nullable=False)
    duration_minutes = Column(Integer, default=60)
    location_type = Column(Text, nullable=False)
    location_value = Column(Text, nullable=True)
    interviewer_names = Column(Text, nullable=True)
    notes_for_candidate = Column(Text, nullable=True)
    internal_notes = Column(Text, nullable=True)
    status = Column(Text, default='scheduled')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", back_populates="interview")
    scheduler = relationship("User", foreign_keys=[scheduled_by])


class Offer(Base):
    __tablename__ = "offers"

    id = Column(Integer, primary_key=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    candidate_name = Column(String)
    job_title = Column(String)
    department = Column(String)
    start_date = Column(String)
    working_hours_from = Column(String)
    working_hours_to = Column(String)
    net_salary = Column(String)
    reporting_to = Column(String)
    exceptions = Column(String)
    status = Column(String, default="pending")  # pending / accepted / rejected
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))

    application = relationship("Application", back_populates="offer")
    creator = relationship("User", foreign_keys=[created_by])
