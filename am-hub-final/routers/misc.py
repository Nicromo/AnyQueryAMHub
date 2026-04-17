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

def _checkup_auth(auth_token: Optional[str], db, request=None):
    """Авторизация для checkup/cabinet endpoints. Cookie и Bearer (расширение)."""
    from auth import decode_access_token
    bearer = ""
    if request:
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    token = bearer or auth_token
    if not token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(token)
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

# ============================================================================
# SIDEBAR SETTINGS + HELP PAGE
# ============================================================================

def _page_user(auth_token: Optional[str], db) -> Optional[User]:
    """Helper: decode auth_token cookie into User (or None)."""
    if not auth_token:
        return None
    payload = decode_access_token(auth_token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload.get("sub"))).first()


@router.get("/settings/sidebar", response_class=HTMLResponse)
async def settings_sidebar_page(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _page_user(auth_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("settings_sidebar.html", {"request": request, "user": user})


@router.get("/help", response_class=HTMLResponse)
async def help_page(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _page_user(auth_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("help.html", {"request": request, "user": user})


# ============================================================================
# HELP — отправка обращения в поддержку
# ============================================================================

from pydantic import BaseModel as _PydanticBaseModel


class HelpMessage(_PydanticBaseModel):
    category: str
    message: str
    url_context: str = ""


def _md_escape(s: str) -> str:
    """Экранирование символов Markdown для Telegram (parse_mode=Markdown)."""
    if not s:
        return ""
    for ch in ("_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


@router.post("/api/help/send")
async def help_send(
    msg: HelpMessage,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Принимает обращение в поддержку и шлёт в Telegram (если настроен)."""
    user = _page_user(auth_token, db)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    category = (msg.category or "").strip()[:64] or "other"
    message_text = (msg.message or "").strip()
    if not message_text:
        return JSONResponse({"ok": False, "error": "empty_message"}, status_code=400)
    url_context = (msg.url_context or "").strip()[:500]

    tg_token = os.getenv("TG_BOT_TOKEN")
    tg_chat = os.getenv("TG_NOTIFY_CHAT_ID")

    fullname = " ".join(filter(None, [getattr(user, "first_name", None),
                                       getattr(user, "last_name", None)])).strip()
    text = (
        f"🆘 *Обращение в поддержку AM Hub*\n\n"
        f"От: {_md_escape(user.email)}"
        f"{(' (' + _md_escape(fullname) + ')') if fullname else ''}\n"
        f"Категория: *{_md_escape(category)}*\n"
        f"Контекст: {_md_escape(url_context) if url_context else '—'}\n\n"
        f"Сообщение:\n{_md_escape(message_text[:3500])}"
    )

    sent = False
    tg_error = None
    if tg_token and tg_chat:
        try:
            import httpx  # локальный импорт — не ломаем верх файла при отсутствии зависимости
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={
                        "chat_id": tg_chat,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                )
            sent = (r.status_code == 200)
            if not sent:
                tg_error = f"tg_http_{r.status_code}"
                logger.warning("Help TG send non-200: %s %s", r.status_code, r.text[:300])
        except Exception as e:
            tg_error = "tg_exception"
            logger.error("Help TG send failed: %s", e)
            sent = False
    else:
        tg_error = "tg_not_configured"

    # Сохраняем в AuditLog для истории обращений (best-effort).
    try:
        db.add(AuditLog(
            user_id=user.id,
            action="help_send",
            resource_type="support",
            resource_id=None,
            new_values={
                "category": category,
                "message": message_text[:2000],
                "url_context": url_context,
                "sent_to_telegram": sent,
                "tg_error": tg_error,
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent", "")[:500],
        ))
        db.commit()
    except Exception as e:
        logger.warning("Help audit log failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass

    return {"ok": True, "sent_to_telegram": sent}

