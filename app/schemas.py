from pydantic import BaseModel, Field, EmailStr, field_validator, AnyHttpUrl
from typing import Optional, List, Any, Union
from datetime import datetime

# Company Schemas
class CompanyBase(BaseModel):
    company_name: str
    company_email: EmailStr
    company_website: Optional[str] = None
    registration_number: Optional[str] = None

class CompanyRegister(CompanyBase):
    contact_person: str
    contact_email: EmailStr
    password: str
    selected_plan: Optional[str] = "free"
    billing_preference: Optional[str] = "monthly"
    contact_phone: Optional[str] = None
    preferred_contact: Optional[str] = "whatsapp"

class CompanyResponse(CompanyBase):
    id: int
    is_approved: bool
    created_at: datetime
    logo_url: Optional[str] = None
    selected_plan: Optional[str] = "free"
    billing_preference: Optional[str] = "monthly"
    contact_phone: Optional[str] = None
    preferred_contact: Optional[str] = "whatsapp"
    invitations_used_this_month: Optional[int] = 0
    invitations_reset_date: Optional[datetime] = None

    class Config:
        from_attributes = True

class CompanyApprovalResponse(CompanyResponse):
    approval_date: Optional[datetime] = None
    approval_notes: Optional[str] = None

class CompanyUpdate(BaseModel):
    company_name: Optional[str] = None
    company_website: Optional[str] = None
    registration_number: Optional[str] = None
    logo_url: Optional[str] = None

# Auth Schemas
class Token(BaseModel):
    access_token: str
    token_type: str
    user_type: str  # "admin", "company", or "candidate"
    username: Optional[str] = None
    company_id: Optional[int] = None

class TokenData(BaseModel):
    email: Optional[str] = None

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    company_id: Optional[int] = None

    class Config:
        from_attributes = True

# Job Schemas
class JobBase(BaseModel):
    job_title: str
    job_description: Optional[str] = None
    job_location: Optional[str] = None
    min_experience: int
    required_skills: str
    nice_to_have_skills: Optional[str] = None
    education_level: str
    salary_range: Optional[str] = None
    behavioral_skills: Optional[str] = None
    industry_experience: Optional[str] = None
    department: Optional[str] = "Other"
    weight_experience: float = Field(default=0.3, ge=0.0)
    weight_skills: float = Field(default=0.4, ge=0.0)
    weight_education: float = Field(default=0.1, ge=0.0)
    weight_behavioral: float = Field(default=0.2, ge=0.0)

class JobCreate(JobBase):
    pass

class JobResponse(JobBase):
    id: int
    is_approved: bool
    created_at: Optional[datetime] = None
    # Expose computed/convenience fields back to frontend
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    employment_type: Optional[str] = None
    hide_salary: bool = False

    class Config:
        from_attributes = True

class JobApprovalResponse(JobResponse):
    approval_date: Optional[datetime] = None
    approval_notes: Optional[str] = None

# Candidate Schemas
class CandidateBase(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    job_applied: Optional[int] = None
    experience_years: Optional[int] = None
    expected_salary: Optional[str] = None
    education: Optional[str] = None
    skills: Optional[str] = None
    cv_text: Optional[str] = None

class CandidateCreate(CandidateBase):
    pass

class CandidateResponse(CandidateBase):
    id: int
    last_title: Optional[str] = None
    last_employer: Optional[str] = None
    company_name: Optional[str] = None
    user_id: Optional[int] = None
    photo_url: Optional[str] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    experiences: Optional[Any] = None
    education_history: Optional[Any] = None
    languages: Optional[Any] = None

    class Config:
        from_attributes = True

# Application Schemas
class ApplicationCreate(BaseModel):
    job_id: int
    candidate_id: Optional[int] = None
    applicant_name: Optional[str] = None
    applicant_email: Optional[str] = None
    applicant_phone: Optional[str] = None
    cv_file_path: Optional[str] = None
    expected_salary: Optional[str] = None
    stage: Optional[str] = "New"

class ApplicationResponse(ApplicationCreate):
    id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# Evaluation Schemas
class EvaluationBase(BaseModel):
    score: Optional[float] = None
    decision: Optional[str] = None
    reason: Optional[str] = None
    strengths: Optional[str] = None
    weaknesses: Optional[str] = None
    suggested_interview_questions: Optional[List[str]] = None

class EvaluationCreate(EvaluationBase):
    candidate_id: int

class EvaluationResponse(EvaluationBase):
    id: int
    candidate_id: Optional[int] = None

    class Config:
        from_attributes = True

# Dashboard job save payload (matches hunters-jobs.js saveHuntersJob)
class JobSavePayload(BaseModel):
    title: str
    location: Optional[str] = None
    employment_type: Optional[str] = "Full-time"
    experience_years: int = 0
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_range: Optional[str] = None
    description: Optional[str] = None
    required_skills: str = ""
    nice_to_have_skills: Optional[str] = None
    behavioral_skills: Optional[str] = None
    education_level: Optional[str] = None
    industry_experience: Optional[str] = None
    department: Optional[str] = "Other"
    ai_weights: Optional[dict] = None
    agent_weights: Optional[dict] = None
    hide_salary: bool = False
    company_id: Optional[str] = None

# Approval/Rejection Schemas
class ApprovalData(BaseModel):
    approval_notes: Optional[str] = None

class RejectionData(BaseModel):
    rejection_reason: str

# ── Profile Schemas (Phase 1.3) ────────────────────────────────────────────────

class ExperienceItem(BaseModel):
    title: str
    employer: str
    start: str
    end: Optional[str] = None
    description: Optional[str] = None

class EducationItem(BaseModel):
    degree: str
    institution: str
    year: Optional[str] = None

class LanguageItem(BaseModel):
    language: str
    proficiency: Optional[str] = None

class ProfileResponse(BaseModel):
    id: int
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    photo_url: Optional[str] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    experiences: List[ExperienceItem] = []
    education_history: List[EducationItem] = []
    languages: List[Union[LanguageItem, str]] = []
    skills: Optional[str] = None
    education: Optional[str] = None
    last_title: Optional[str] = None
    last_employer: Optional[str] = None
    has_cv: bool = False

    class Config:
        from_attributes = True

class AdminProfileResponse(ProfileResponse):
    user_id: Optional[int] = None
    registration_date: Optional[datetime] = None

    class Config:
        from_attributes = True

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    photo_url: Optional[str] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    experiences: Optional[List[ExperienceItem]] = None
    education_history: Optional[List[EducationItem]] = None
    languages: Optional[List[Union[LanguageItem, str]]] = None
    skills: Optional[str] = None
    education: Optional[str] = None
    last_title: Optional[str] = None
    last_employer: Optional[str] = None

    @field_validator("photo_url", mode="before")
    @classmethod
    def validate_photo_url(cls, v):
        if v is None or v == "":
            return v
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("photo_url must be a valid http/https URL")
        return v
