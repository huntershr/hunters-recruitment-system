from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
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
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_password = get_password_hash(user.password)
    new_user = models.User(
        email=user.email,
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

    user = db.query(models.User).filter(models.User.email == username).first()

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
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_type": "admin" if user.is_admin else "company",
    }

@router.get("/me", response_model=schemas.UserResponse)
def read_users_me(current_user: models.User = Depends(get_current_user)):
    return current_user
