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
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import text
from pydantic import BaseModel

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

@router.post("/api/settings/creds")
async def api_save_creds(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить персональные креды пользователя для ВСЕХ сервисов."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    data = await request.json()
    settings = user.settings or {}

    # Сохраняем все сервисы: merchrules, telegram, ktalk, tbank_time, airtable, sheets, groq
    for service in ["merchrules", "telegram", "ktalk", "tbank_time", "airtable", "sheets", "groq"]:
        if service in data:
            if service not in settings:
                settings[service] = {}
            settings[service].update(data[service])

    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}



@router.post("/api/settings/rules")
async def api_save_rules(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить правила работы менеджера."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    data = await request.json()
    settings = dict(user.settings or {})
    settings["rules"] = {**(settings.get("rules") or {}), **data}
    user.settings = settings
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}



@router.post("/api/settings/prefs")
async def api_save_prefs(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить предпочтения (тема, уведомления и т.д.)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    data = await request.json()
    settings = dict(user.settings or {})
    settings["preferences"] = {**(settings.get("preferences") or {}), **data}
    user.settings = settings
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}





@router.get("/api/extension/version")
async def api_extension_version(request: Request, auth_token: Optional[str] = Cookie(None)):
    """
    Возвращает актуальную версию расширения.
    Расширение опрашивает этот endpoint каждые 6 часов.
    Если версия выше установленной — фоновый скрипт показывает уведомление.

    URL всегда абсолютный: HUB_URL из env (если задан) или request.base_url.
    Без этого Chrome резолвит /static/... относительно chrome-extension://<id>/
    и получает ERR_FILE_NOT_FOUND при клике «Обновить».
    """
    # Читаем version из manifest.json расширения
    import pathlib
    manifest_path = pathlib.Path(__file__).resolve().parent.parent / "static" / "amhub-ext" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = manifest.get("version", "1.0.0")
    except Exception:
        version = "1.0.0"

    # Абсолютный base_url: env HUB_URL > Origin header > request.base_url
    base_url = (
        os.getenv("HUB_URL", "").rstrip("/")
        or (request.headers.get("origin") or "").rstrip("/")
        or str(request.base_url).rstrip("/")
    )
    download_url = f"{base_url}/static/amhub-ext.zip"
    install_url = f"{base_url}/settings/extension"

    # Changelog можно задать через ENV или файл
    changelog = os.getenv("EXT_CHANGELOG", "Обновлённая версия расширения AM Hub")

    return {
        "version": version,
        "download_url": download_url,
        "install_url": install_url,
        "changelog": changelog,
    }


@router.get("/settings/extension", response_class=HTMLResponse)
async def settings_extension_page(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Страница установки Chrome-расширения AM Hub Sync."""
    user = None
    if auth_token:
        payload = decode_access_token(auth_token)
        if payload:
            user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "extension_install.html",
        {"request": request, "user": user, "hub_token": auth_token},
    )


@router.get("/api/settings/my-clients")
async def api_my_clients(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Список клиентов текущего менеджера."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.order_by(Client.name).all()
    return {"clients": [{"id": c.id, "name": c.name, "segment": c.segment} for c in clients]}


# ─────────────────────────────────────────────────────────
# Sidebar prefs (persisted in User.settings.sidebar)
# ─────────────────────────────────────────────────────────

class SidebarCategory(BaseModel):
    name: str
    items: List[str] = []


class SidebarPrefs(BaseModel):
    hidden: List[str] = []
    collapsed: List[str] = []
    custom_categories: List[SidebarCategory] = []
    order: Optional[List[str]] = None


def _sidebar_get_user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user


_EMPTY_SIDEBAR = {"hidden": [], "collapsed": [], "custom_categories": [], "order": None}


@router.get("/api/settings/sidebar")
async def get_sidebar_prefs(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Получить пользовательские настройки sidebar (скрытые / порядок / кастомные категории)."""
    user = _sidebar_get_user(auth_token, db)
    settings = user.settings or {}
    sidebar = settings.get("sidebar") or {}
    # Гарантируем все ключи
    result = dict(_EMPTY_SIDEBAR)
    result.update({
        "hidden": list(sidebar.get("hidden") or []),
        "collapsed": list(sidebar.get("collapsed") or []),
        "custom_categories": list(sidebar.get("custom_categories") or []),
        "order": sidebar.get("order"),
    })
    return result


@router.post("/api/settings/sidebar")
async def save_sidebar_prefs(
    prefs: SidebarPrefs,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Сохранить пользовательские настройки sidebar в User.settings.sidebar."""
    user = _sidebar_get_user(auth_token, db)
    settings = dict(user.settings or {})
    settings["sidebar"] = {
        "hidden": prefs.hidden or [],
        "collapsed": prefs.collapsed or [],
        "custom_categories": [c.dict() for c in (prefs.custom_categories or [])],
        "order": prefs.order,
    }
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}



