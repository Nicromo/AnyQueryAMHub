"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional, List
from datetime import datetime, timedelta
import os
import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Cookie, Form, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote,
)
from auth import (
    authenticate_user, create_user, create_access_token,
    verify_password, hash_password, log_audit, decode_access_token,
)
from error_handlers import log_error, handle_db_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

# ============================================================================
# MANAGER CABINET — личный кабинет менеджера

# ============================================================================
# SEARCH QUALITY CHECKUP — API для расширения
# ============================================================================

def _checkup_auth(auth_token: Optional[str], db):
    """Общая авторизация для checkup endpoints."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user



# ============================================================================
# ONBOARDING

# ============================================================================
# ADMIN PANEL
# ============================================================================

def _require_admin(auth_token, db):
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")
    return user



# ============================================================================
# GLOBAL SEARCH

# ============================================================================
# CLIENT NOTES API

# ============================================================================
# FOLLOWUP TEMPLATES

# ============================================================================
# CALENDAR

# ============================================================================
# PROFILE

# ============================================================================
# SYNC STATUS

# ============================================================================
# MEETING SLOTS

# ============================================================================
# FOCUS MODE (CSS toggle — client detail page with sidebar hidden)

# ============================================================================
# PERSONAL INBOX

# ============================================================================
# CHURN PREDICTION

# ============================================================================
# PWA ICONS (SVG placeholder)

# ============================================================================
# TASK MODAL API (bulk edit)

# ============================================================================
# AI CHAT

# ============================================================================
# TEAM DASHBOARD (admin only)

# ============================================================================
# FILE ATTACHMENTS

# ============================================================================
# EXCEL EXPORT (полноценный)
# ============================================================================

# ============================================================================
# MEETING TRANSCRIPTION + AI SUMMARY

# ============================================================================
# REVENUE TRACKING

# ============================================================================
# MEETING COMMENTS

