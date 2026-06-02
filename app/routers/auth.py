from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import func
from jose import JWTError, jwt
from datetime import datetime, timedelta
import os
import secrets

import logging
from .. import models, schemas, database
from ..auth_utils import verify_password, get_password_hash, create_access_token, SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

if not os.getenv("SENDGRID_API_KEY"):
    logger.warning("SENDGRID_API_KEY not set — password reset emails will not be sent")

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


def send_reset_email(to_email: str, reset_url: str):
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, From, To, Subject, HtmlContent

    api_key = os.getenv("SENDGRID_API_KEY", "")
    if not api_key:
        logger.error("SENDGRID_API_KEY not set — cannot send password reset email")
        return

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:'Segoe UI',Arial,sans-serif;background:#F5F6F8;padding:40px 0;margin:0">
      <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
        <div style="background:#1B2A4A;padding:28px 32px;text-align:center">
          <div style="color:#C9A84C;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin-bottom:6px">Hunters for HR Transformation</div>
          <div style="color:#fff;font-size:22px;font-weight:600">Reset Your Password</div>
        </div>
        <div style="padding:32px">
          <p style="color:#1B2A4A;font-size:15px;margin:0 0 16px">You requested a password reset for your Hunters account.</p>
          <p style="color:#555;font-size:14px;margin:0 0 28px">Click the button below to set a new password. This link expires in <strong>2 hours</strong>.</p>
          <div style="text-align:center;margin-bottom:28px">
            <a href="{reset_url}" style="background:#C9A84C;color:#1B2A4A;text-decoration:none;padding:14px 32px;border-radius:8px;font-weight:600;font-size:15px;display:inline-block">Reset My Password</a>
          </div>
          <p style="color:#888;font-size:12px;margin:0">If you didn't request this, ignore this email — your password won't change.</p>
        </div>
        <div style="background:#F5F6F8;padding:16px 32px;text-align:center">
          <p style="color:#aaa;font-size:11px;margin:0">Powered by Hunters HR · hr@hunters-egypt.com</p>
        </div>
      </div>
    </body>
    </html>
    """

    message = Mail(
        from_email=From("hr@hunters-egypt.com", "Hunters HR"),
        to_emails=To(to_email),
        subject=Subject("Reset Your Hunters Password"),
        html_content=HtmlContent(html_content)
    )

    sg = SendGridAPIClient(api_key)
    response = sg.send(message)
    logger.info(f"Password reset email sent to {to_email} — status {response.status_code}")


@router.post("/forgot-password")
def forgot_password(request: Request, data: dict, db: Session = Depends(get_db)):
    email = data.get("email", "").strip().lower()
    user = db.query(models.User).filter(func.lower(models.User.email) == email).first()

    if not user:
        return {"message": "If that email exists, a reset link has been sent."}

    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.utcnow() + timedelta(hours=2)
    db.commit()

    host = request.headers.get("host", "app.hunters-egypt.com")
    scheme = "https" if "hunters-egypt" in host else "http"
    reset_url = f"{scheme}://{host}/reset-password.html?token={token}"

    try:
        send_reset_email(user.email, reset_url)
        logger.info(f"Password reset email sent to {user.email}")
    except Exception as e:
        logger.error(f"Password reset email FAILED for {user.email}: {e}")

    return {"message": "If that email exists, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(data: dict, db: Session = Depends(get_db)):
    token = data.get("token", "")
    new_password = data.get("password", "")

    if not token or not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Invalid request")

    user = db.query(models.User).filter(models.User.reset_token == token).first()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    if user.reset_token_expires < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Reset link has expired. Please request a new one.")

    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    user.hashed_password = pwd_context.hash(new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    return {"message": "Password updated successfully"}
