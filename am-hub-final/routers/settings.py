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

def _parse_site_ids(raw) -> list:
    """Нормализует ввод site_ids: поддерживает строку с запятыми / переводами строк /
    точками с запятой / пробелами, либо уже готовый массив."""
    import re as _re
    if raw is None:
        return []
    if isinstance(raw, list):
        parts = [str(x) for x in raw]
    else:
        parts = _re.split(r"[\s,;]+", str(raw))
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    # дедупликация с сохранением порядка
    seen = set()
    result = []
    for p in out:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


@router.post("/api/me/merchrules/my-sites")
async def api_save_my_site_ids(request: Request, db: Session = Depends(get_db),
                                auth_token: Optional[str] = Cookie(None)):
    """Менеджер вводит свои Merchrules site_ids (через запятую, с новой строки, etc)."""
    if not auth_token:
        raise HTTPException(401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(401)
    body = await request.json()
    ids = _parse_site_ids(body.get("site_ids"))
    settings = dict(user.settings or {})
    mr = dict(settings.get("merchrules") or {})
    mr["my_site_ids"] = ids
    settings["merchrules"] = mr
    user.settings = settings
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True, "site_ids": ids, "count": len(ids)}


@router.get("/api/me/merchrules/my-sites-table")
async def api_my_sites_table(db: Session = Depends(get_db),
                              auth_token: Optional[str] = Cookie(None)):
    """Табличный preview: для каждого site_id из user.settings.merchrules.my_site_ids
    возвращаем name/url/segment/payment/products (из локальной БД, Client + ClientProduct).
    """
    if not auth_token:
        raise HTTPException(401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(401)

    ids_raw = (user.settings or {}).get("merchrules", {}).get("my_site_ids") or []
    if isinstance(ids_raw, str):
        ids = _parse_site_ids(ids_raw)
    else:
        ids = [str(x) for x in ids_raw]

    from models import Client
    rows = []
    for sid in ids:
        c = (db.query(Client)
               .filter((Client.merchrules_account_id == sid) |
                       (Client.airtable_site_id == sid))
               .first())
        products = []
        if c:
            try:
                from models import ClientProduct
                prods = db.query(ClientProduct).filter(ClientProduct.client_id == c.id).all()
                products = [{"code": p.code, "name": p.name, "status": p.status} for p in prods]
            except Exception:
                products = []
        rows.append({
            "site_id": sid,
            "name": c.name if c else None,
            "client_id": c.id if c else None,
            "domain": (c.domain if c else None) or (f"site-{sid}"),
            "url": (f"https://{c.domain}" if c and c.domain else None),
            "segment": c.segment if c else None,
            "payment_status": (c.payment_status if c else None),
            "payment_amount": (c.payment_amount if c else None),
            "payment_due_date": (c.payment_due_date.isoformat() if c and c.payment_due_date else None),
            "mrr": (c.mrr if c else None),
            "products": products,
            "health": (c.health_score if c else None),
            "last_meeting": (c.last_meeting_date.isoformat() if c and c.last_meeting_date else None),
            "resolved": c is not None,
        })
    return {"items": rows, "count": len(rows)}


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
    # Динамический endpoint — всегда собирает ZIP из текущей папки.
    # Fallback /static/amhub-ext.zip остаётся на диске для совместимости
    # со старыми версиями расширения.
    download_url = f"{base_url}/api/extension/download"
    install_url = f"{base_url}/settings/extension"

    # Changelog можно задать через ENV или файл
    changelog = os.getenv("EXT_CHANGELOG", "Обновлённая версия расширения AM Hub")

    return {
        "version": version,
        "download_url": download_url,
        "install_url": install_url,
        "changelog": changelog,
    }


@router.get("/api/extension/download")
async def api_extension_download():
    """
    Динамически собирает ZIP расширения из текущей папки static/amhub-ext/.
    Всегда актуальная версия — не нужно коммитить bin-файл.

    Используется всеми кнопками «⬇ Скачать .zip» и как download_url в
    /api/extension/version → extension update notification.
    """
    import pathlib, io, zipfile, json as _json
    from fastapi.responses import StreamingResponse

    ext_dir = pathlib.Path(__file__).resolve().parent.parent / "static" / "amhub-ext"
    if not ext_dir.exists():
        raise HTTPException(500, "extension source dir not found")

    # Читаем version из manifest для имени файла
    version = "unknown"
    try:
        manifest = _json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
        version = manifest.get("version", "unknown")
    except Exception:
        pass

    # Обновляем build-info.json on-the-fly (не трогаем оригинал на диске)
    from datetime import datetime as _dt
    build_info = {
        "version": version,
        "built_at": _dt.utcnow().isoformat() + "Z",
        "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA",
                                  os.environ.get("GIT_SHA", "live")),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Live build-info с текущим временем и commit SHA
        zf.writestr("build-info.json", _json.dumps(build_info, indent=2))
        for p in ext_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(ext_dir).as_posix()
            if rel in ("build-info.json",):  # уже записан выше
                continue
            if rel.endswith(".DS_Store") or rel.endswith(".map"):
                continue
            zf.write(p, arcname=rel)
    buf.seek(0)

    filename = f"amhub-ext-{version}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",  # не кешируем — всегда свежий
        },
    )


@router.get("/api/extension/config")
async def api_extension_config(request: Request, db: Session = Depends(get_db)):
    """
    Возвращает сохранённые креды пользователя для автозаполнения popup расширения.
    Нужно при переустановке / на другом устройстве: пользователь вводит только
    AM Hub токен, остальное подтягивается автоматически.

    Авторизация — через Bearer api-token (hub_token) в заголовке Authorization.
    Поддерживаем оба формата: hub-token из user.settings.api_tokens (hashed)
    и JWT (для cookie-auth fallback).

    Возвращает plaintext пароли — расшифровываем Fernet.
    """
    auth_header = request.headers.get("Authorization", "")
    bearer = auth_header.replace("Bearer ", "").strip()

    user = None
    if bearer:
        # 1) JWT?
        try:
            from auth import decode_access_token
            payload = decode_access_token(bearer)
            if payload:
                user = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
        except Exception:
            pass
        # 2) Hashed API token в user.settings.api_tokens?
        if not user:
            try:
                import hashlib
                h = hashlib.sha256(bearer.encode()).hexdigest()
                all_users = db.query(User).filter(User.is_active == True).all()
                for u in all_users:
                    tokens = (u.settings or {}).get("api_tokens", [])
                    for t in tokens:
                        if t.get("hashed") == h or t.get("hashed_token") == h:
                            user = u
                            break
                    if user: break
            except Exception:
                pass

    if not user:
        raise HTTPException(401, "invalid token")

    settings = user.settings or {}
    mr = settings.get("merchrules", {}) or {}
    kt = settings.get("ktalk", {}) or {}
    tm = settings.get("tbank_time", {}) or {}

    # Расшифровываем пароли через Fernet если ключ есть
    try:
        from crypto import dec as _dec
    except Exception:
        _dec = lambda v: v

    return {
        "user": {"email": user.email, "name": getattr(user, "name", "") or ""},
        "merchrules": {
            "login": mr.get("login") or "",
            "password": (_dec(mr.get("password", "")) or "") if mr.get("password") else "",
            "site_ids": mr.get("site_ids") or [],
        },
        "ktalk": {
            "channel_id": kt.get("followup_channel_id") or kt.get("channel_id") or "",
        },
        "tbank_time": {
            "has_token": bool(tm.get("mmauthtoken") or tm.get("session_cookie")),
            "username": tm.get("username") or "",
        },
        "groq": {
            "api_key": (settings.get("groq") or {}).get("api_key") or "",
        },
        "manager_name": getattr(user, "name", "") or user.email,
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



