"""
app.py
------
FastAPI entrypoint for the AI Report Generation System.
"""

from __future__ import annotations

import os
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import Session, select

# --- Internal Imports ---
from config import settings
from database import create_db_and_tables, get_session, engine
from auth import verify_password, get_password_hash, create_access_token, get_current_user
from models import (
    User, UserRegister, UserLogin, Token, Report,
    ErrorResponse, FileOutputs, GenerateReportResponse, HealthResponse,
    UserRequest, WorkflowStatus,
)
from report_generator import generate_report_files
from utils import get_logger, log_error, log_event, new_request_id, setup_logging
from workflow import WorkflowRunResult, run_report_workflow

# -----------------------------------------------------------------------------
# Logging / lifespan
# -----------------------------------------------------------------------------

setup_logging(settings.log_level)
logger = get_logger("ai_report_generator.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    create_db_and_tables()
    
    # --- AUTO-CREATE DEFAULT ADMIN USER ---
    try:
        with Session(engine) as db:
            admin_exists = db.exec(select(User).where(User.email == "admin@admin.com")).first()
            if not admin_exists:
                hashed_pw = get_password_hash("admin123")
                admin_user = User(username="admin", email="admin@admin.com", hashed_password=hashed_pw)
                db.add(admin_user)
                db.commit()
                log_event("default_admin_created", username="admin", email="admin@admin.com")
    except Exception as exc:
        log_error("admin_creation_failed", exc)

    settings.ensure_output_dir()
    log_event(
        "app_startup",
        provider=settings.llm_provider.value,
        model=settings.active_model_name,
        output_dir=settings.output_dir,
        max_validation_retries=settings.max_validation_retries,
    )
    yield
    log_event("app_shutdown")


app = FastAPI(
    title="AI Report Generation System",
    description=(
        "Generate professional reports from a topic/query using a multi-agent "
        "Microsoft Agent Framework workflow (Analyze -> Plan -> Research -> "
        "Analyze -> Validate -> Write -> Export)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# --- CORS: Allow Frontend & Swagger ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Auth Schemas
# -----------------------------------------------------------------------------

class ResetPassword(BaseModel):
    email: str
    new_password: str

class ProfileUpdate(BaseModel):
    profile_picture_base64: Optional[str] = None

# -----------------------------------------------------------------------------
# Auth Routes
# -----------------------------------------------------------------------------

@app.post("/auth/register", response_model=Token, tags=["auth"])
def register(user_data: UserRegister, db: Session = Depends(get_session)):
    """Register a new user."""
    existing_user = db.exec(select(User).where(User.email == user_data.email)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    existing_username = db.exec(select(User).where(User.username == user_data.username)).first()
    if existing_username:
        raise HTTPException(status_code=400, detail="Username already taken")

    hashed_password = get_password_hash(user_data.password)
    db_user = User(username=user_data.username, email=user_data.email, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    access_token = create_access_token(data={"sub": str(db_user.id)})
    return Token(access_token=access_token, token_type="bearer")

@app.post("/auth/login", response_model=Token, tags=["auth"])
def login(user_data: UserLogin, db: Session = Depends(get_session)):
    """Login and get JWT token."""
    user = db.exec(select(User).where(User.email == user_data.email)).first()
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    access_token = create_access_token(data={"sub": str(user.id)})
    return Token(access_token=access_token, token_type="bearer")

@app.get("/auth/me", tags=["auth"])
def read_users_me(current_user: User = Depends(get_current_user)):
    """Get current logged in user."""
    return {
        "id": current_user.id, 
        "username": current_user.username, 
        "email": current_user.email,
        "profile_picture_base64": current_user.profile_picture_base64
    }

@app.post("/auth/reset-password", tags=["auth"])
def reset_password(data: ResetPassword, db: Session = Depends(get_session)):
    """Reset password using email."""
    user = db.exec(select(User).where(User.email == data.email)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.hashed_password = get_password_hash(data.new_password)
    db.add(user)
    db.commit()
    return {"message": "Password reset successfully"}

@app.put("/auth/profile", tags=["auth"])
def update_profile(data: ProfileUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    """Update user profile picture."""
    if data.profile_picture_base64 is not None:
        if data.profile_picture_base64 == "":
            current_user.profile_picture_base64 = None
        else:
            if len(data.profile_picture_base64) > 5 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Image too large (max 5MB)")
            current_user.profile_picture_base64 = data.profile_picture_base64
        
        db.add(current_user)
        db.commit()
        db.refresh(current_user)
        
    return {"message": "Profile updated", "profile_picture_base64": current_user.profile_picture_base64}

# -----------------------------------------------------------------------------
# Report Routes (Protected)
# -----------------------------------------------------------------------------

@app.post("/generate-report", tags=["reports"])
async def generate_report(
    body: UserRequest, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_session)
):
    """Generate and save report for logged in user."""
    request_id = new_request_id()
    log_event("request_received", request_id=request_id, user_id=current_user.id, query_preview=body.query[:50])

    if not body.query or not body.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    formats = body.output_formats or ["md"]

    try:
        result = await run_report_workflow(body, output_formats=formats, request_id=request_id)
    except Exception as exc:
        log_error("request_workflow_exception", exc, request_id=request_id)
        raise HTTPException(status_code=500, detail=str(exc))

    if not result.success or not result.document:
        raise HTTPException(status_code=400, detail=result.error_message or "Report generation failed")

    files = generate_report_files(document=result.document, formats=formats)

    db_report = Report(
        user_id=current_user.id,
        title=result.document.title,
        query=body.query,
        markdown_content=result.document.content_markdown,
        pdf_path=files.pdf if files else None,
        docx_path=files.docx if files else None
    )
    db.add(db_report)
    db.commit()
    db.refresh(db_report)

    pdf_name = os.path.basename(files.pdf) if files and files.pdf else None
    docx_name = os.path.basename(files.docx) if files and files.docx else None
    md_name = os.path.basename(files.markdown) if files and files.markdown else None

    return {
        "id": db_report.id,
        "title": db_report.title,
        "markdown": db_report.markdown_content,
        "files": {"pdf": pdf_name, "docx": docx_name, "md": md_name}
    }

@app.get("/reports", tags=["reports"])
def get_user_reports(current_user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    """Get all reports for the logged in user."""
    reports = db.exec(select(Report).where(Report.user_id == current_user.id).order_by(Report.created_at.desc())).all()
    return [{"id": r.id, "title": r.title, "query": r.query, "created_at": r.created_at.isoformat()} for r in reports]

@app.get("/reports/{report_id}", tags=["reports"])
def get_report_detail(report_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    """Get single report detail. Admins can view any user's report."""
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.user_id != current_user.id and current_user.username != "admin":
        raise HTTPException(status_code=403, detail="You do not have access to this report.")

    return {
        "id": report.id,
        "title": report.title,
        "markdown": report.markdown_content,
        "query": report.query,
        "files": {
            "pdf": os.path.basename(report.pdf_path) if report.pdf_path else None,
            "docx": os.path.basename(report.docx_path) if report.docx_path else None,
        },
    }
@app.get("/download/{filename}", tags=["reports"])
async def download_file(filename: str, current_user: User = Depends(get_current_user)):
    """Download a generated report file."""
    file_path = os.path.join(settings.output_dir, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=file_path, filename=filename, media_type='application/octet-stream')

# -----------------------------------------------------------------------------
# Admin Routes
# -----------------------------------------------------------------------------

@app.get("/admin/reports", tags=["admin"])
def get_all_reports_admin(current_user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    """Admin observability: Get all reports from all users."""
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    
    reports = db.exec(select(Report).order_by(Report.created_at.desc())).all()
    result = []
    for r in reports:
        user = db.get(User, r.user_id)
        result.append({
            "id": r.id, 
            "title": r.title, 
            "username": user.username if user else "Unknown", 
            "created_at": r.created_at.isoformat()
        })
    return result

@app.get("/admin/users", tags=["admin"])
def get_all_users_admin(current_user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    """Admin: get all registered users."""
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

    users = db.exec(select(User)).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_admin": (u.username == "admin"),
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]
# -----------------------------------------------------------------------------
# System & Health Routes
# -----------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness / basic config probe (does not call the LLM)."""
    return HealthResponse(
        status="ok",
        provider=settings.llm_provider.value,
        model=settings.active_model_name,
    )

# -----------------------------------------------------------------------------
# Frontend Serving (Must be at the bottom)
# -----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/", tags=["frontend"])
def serve_frontend():
    return FileResponse(FRONTEND_DIR / "index.html")

# -----------------------------------------------------------------------------
# Local dev entry
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )