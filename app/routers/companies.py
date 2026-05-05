from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from jose import JWTError, jwt
import logging

from .. import models, schemas, database, auth_utils
from ..auth_utils import SECRET_KEY, ALGORITHM

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/companies",
    tags=["Companies"]
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Get current authenticated user from token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        raise credentials_exception
    return user

@router.post("/register", response_model=schemas.CompanyResponse)
def register_company(
    company_data: schemas.CompanyRegister,
    db: Session = Depends(get_db)
):
    """
    Register a new company for recruitment.
    Company will be pending admin approval.
    """
    # Check if company already exists
    existing_company = db.query(models.Company).filter(
        models.Company.company_email == company_data.company_email
    ).first()
    if existing_company:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company email already registered"
        )
    
    # Check if email is already used by a user
    existing_user = db.query(models.User).filter(
        models.User.email == company_data.contact_email
    ).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use"
        )
    
    # Create company
    new_company = models.Company(
        company_name=company_data.company_name,
        company_email=company_data.company_email,
        company_website=company_data.company_website,
        registration_number=company_data.registration_number,
        is_approved=False
    )
    db.add(new_company)
    db.flush()  # Get the company ID
    
    # Create company admin user
    hashed_password = auth_utils.get_password_hash(company_data.password)
    new_user = models.User(
        email=company_data.contact_email,
        hashed_password=hashed_password,
        full_name=company_data.contact_person,
        is_admin=False,
        company_id=new_company.id,
        is_active=False  # Inactive until company is approved
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_company)
    
    logger.info(f"New company registered: {company_data.company_name} - Pending approval")
    
    return new_company

@router.get("/pending", response_model=List[schemas.CompanyApprovalResponse])
def get_pending_companies(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Get all pending company approvals (Admin only).
    """
    if not current_user or not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    pending = db.query(models.Company).filter(
        models.Company.is_approved == False
    ).all()
    
    return pending

@router.post("/approve/{company_id}")
def approve_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Approve a company registration (Admin only).
    Activates all company users.
    """
    if not current_user or not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    company = db.query(models.Company).filter(
        models.Company.id == company_id
    ).first()
    
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    # Approve company
    company.is_approved = True
    company.approval_date = datetime.utcnow()
    
    # Activate all company users
    users = db.query(models.User).filter(
        models.User.company_id == company_id
    ).all()
    for user in users:
        user.is_active = True
    
    db.commit()
    
    logger.info(f"Company approved: {company.company_name}")
    
    return {
        "message": f"Company '{company.company_name}' approved successfully",
        "company_id": company_id
    }

@router.post("/reject/{company_id}")
def reject_company(
    company_id: int,
    reason: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Reject a company registration (Admin only).
    Deletes the company and associated users.
    """
    if not current_user or not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    company = db.query(models.Company).filter(
        models.Company.id == company_id
    ).first()
    
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    # Delete associated users
    db.query(models.User).filter(
        models.User.company_id == company_id
    ).delete()
    
    # Delete company
    db.delete(company)
    db.commit()
    
    logger.info(f"Company rejected: {company.company_name} - Reason: {reason}")
    
    return {
        "message": f"Company rejected",
        "reason": reason
    }

@router.get("/", response_model=List[schemas.CompanyApprovalResponse])
def get_all_companies(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Get all companies (Admin only).
    """
    if not current_user or not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    return db.query(models.Company).all()

@router.get("/approved", response_model=List[schemas.CompanyApprovalResponse])
def get_approved_companies(db: Session = Depends(get_db)):
    """
    Get all approved companies (Public endpoint).
    Shows company count and reputation.
    """
    return db.query(models.Company).filter(
        models.Company.is_approved == True
    ).all()

@router.get("/{company_id}", response_model=schemas.CompanyResponse)
def get_company(company_id: int, db: Session = Depends(get_db)):
    """
    Get company details by ID.
    """
    company = db.query(models.Company).filter(
        models.Company.id == company_id
    ).first()
    
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    return company
