from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
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

class CompanyResponse(CompanyBase):
    id: int
    is_approved: bool
    created_at: datetime

    class Config:
        from_attributes = True

class CompanyApprovalResponse(CompanyResponse):
    approval_date: Optional[datetime] = None
    approval_notes: Optional[str] = None

# Auth Schemas
class Token(BaseModel):
    access_token: str
    token_type: str
    user_type: str  # "admin" or "company"

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

    class Config:
        from_attributes = True

class JobApprovalResponse(JobResponse):
    approval_date: Optional[datetime] = None
    approval_notes: Optional[str] = None

# Candidate Schemas
class CandidateBase(BaseModel):
    name: str
    email: str
    phone: str
    job_applied: int
    experience_years: int
    expected_salary: Optional[str] = None
    education: str
    skills: str
    cv_text: str

class CandidateCreate(CandidateBase):
    pass

class CandidateResponse(CandidateBase):
    id: int

    class Config:
        from_attributes = True

# Evaluation Schemas
class EvaluationBase(BaseModel):
    score: float
    decision: str
    reason: str
    strengths: Optional[str] = None
    weaknesses: Optional[str] = None
    suggested_interview_questions: Optional[List[str]] = None

class EvaluationCreate(EvaluationBase):
    candidate_id: int

class EvaluationResponse(EvaluationBase):
    id: int
    candidate_id: int

    class Config:
        from_attributes = True

# Approval/Rejection Schemas
class ApprovalData(BaseModel):
    approval_notes: Optional[str] = None

class RejectionData(BaseModel):
    rejection_reason: str
