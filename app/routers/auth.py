from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import func
from jose import JWTError, jwt
from datetime import timedelta
import os

from .. import models, schemas, database
from ..auth_utils import verify_password, get_password_hash, create_access_token, SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
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
        token_data = schemas.TokenData(email=email)
    except JWTError:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.email == token_data.email).first()
    if user is None:
        raise credentials_exception
    return user

@router.post("/register", response_model=schemas.UserResponse)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    email = (user.email or "").strip().lower()
    db_user = db.query(models.User).filter(func.lower(models.User.email) == email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = get_password_hash(user.password)
    new_user = models.User(
        email=email,
        hashed_password=hashed_password,
        full_name=user.full_name
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    username = (form_data.username or "").strip().lower()
    password = form_data.password or ""

    # Admin recovery/bootstrapping:
    # If ADMIN_EMAIL/ADMIN_PASSWORD are configured, we can (optionally) ensure that admin can log in
    # even when the database already contains users (common in hosted DBs).
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    bootstrap_flag = os.getenv("ADMIN_BOOTSTRAP_LOGIN", "").strip().lower() in {"1", "true", "yes", "y", "on"}

    user = db.query(models.User).filter(func.lower(models.User.email) == username).first()

    # If attempting to log in as the configured admin, allow a controlled one-time bootstrap/reset.
    if bootstrap_flag and admin_email and admin_password and username == admin_email:
        if user is None:
            user = models.User(
                email=admin_email,
                hashed_password=get_password_hash(admin_password),
                full_name="Administrator",
                is_admin=True,
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            # Ensure flags and (re)sync password for recovery
            changed = False
            if not user.is_admin:
                user.is_admin = True
                changed = True
            if not user.is_active:
                user.is_active = True
                changed = True
            if not verify_password(password, user.hashed_password):
                user.hashed_password = get_password_hash(admin_password)
                changed = True
            if changed:
                db.commit()
                db.refresh(user)

    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if user is active (for company users, must wait for approval)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending approval from an administrator. Please check back soon!",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    if user.is_admin:
        user_type = "admin"
    elif user.company_id:
        user_type = "company"
    else:
        user_type = "candidate"
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_type": user_type,
        "username": user.full_name or user.email,
        "company_id": user.company_id,
    }

@router.get("/me", response_model=schemas.UserResponse)
def read_users_me(current_user: models.User = Depends(get_current_user)):
    return current_user

@router.get("/candidate-users")
def list_candidate_users(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    users = db.query(models.User).filter(
        models.User.is_admin == False,
        models.User.company_id == None
    ).order_by(models.User.id.desc()).all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "is_active": u.is_active} for u in users]

@router.delete("/candidate-users/{user_id}")
def delete_candidate_user(user_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot delete admin accounts")
    db.delete(user)
    db.commit()
    return {"detail": "Deleted"}

@router.post("/candidate-users/{user_id}/reset-password")
def reset_candidate_password(user_id: int, body: dict, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    new_password = (body.get("new_password") or "").strip()
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or user.is_admin:
        raise HTTPException(status_code=404, detail="User not found")
    user.hashed_password = get_password_hash(new_password)
    db.commit()
    return {"detail": "Password reset"}
