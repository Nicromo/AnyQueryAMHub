"""Auto-split from misc.py"""
from typing import Optional, List
from datetime import datetime, timedelta
import os, json, logging

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote, ClientHistory,
)
from auth import decode_access_token, hash_password, verify_password, log_audit, get_current_user
from deps import require_user, require_admin, optional_user
from error_handlers import log_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

@router.post("/api/admin/import/api-keys")
async def api_admin_import_api_keys(
    file: UploadFile,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Импорт Diginetica API keys из Excel файла.
    Формат: client_id | client_name | diginetica_api_key | site_url | checkup_queries_top
    Доступно только администраторам.
    """
    _require_admin(auth_token, db)

    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Поддерживаются только .xlsx / .csv")

    content = await file.read()

    import io, pandas as pd
    try:
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str, skiprows=1)  # skiprows=1 пропускает описания
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения файла: {e}")

    df.columns = [c.strip().lower() for c in df.columns]
    if "client_id" not in df.columns:
        raise HTTPException(status_code=400, detail="Нет колонки client_id")

    updated, skipped, errors = 0, 0, []
    from sqlalchemy.orm.attributes import flag_modified

    for _, row in df.iterrows():
        raw_id = row.get("client_id", "")
        if not raw_id or str(raw_id).strip() in ("", "nan", "None"):
            skipped += 1
            continue

        try:
            client_id = int(float(str(raw_id).strip()))
        except ValueError:
            errors.append(f"Неверный client_id: {raw_id!r}")
            continue

        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            errors.append(f"Клиент #{client_id} не найден")
            continue

        meta = dict(client.integration_metadata or {})

        # Diginetica продукты — Sort / Autocomplete / Recommendations
        digi = meta.get("diginetica", {})

        def _val(key):
            v = str(row.get(key, "") or "").strip()
            return "" if v in ("", "nan", "None") else v

        # Sort
        if _val("sort_api_key"):
            digi.setdefault("sort", {})["api_key"] = _val("sort_api_key")
        if _val("sort_url"):
            digi.setdefault("sort", {})["url"] = _val("sort_url")
        # Autocomplete
        if _val("auto_api_key"):
            digi.setdefault("autocomplete", {})["api_key"] = _val("auto_api_key")
        if _val("auto_url"):
            digi.setdefault("autocomplete", {})["url"] = _val("auto_url")
        # Recommendations
        if _val("rec_api_key"):
            digi.setdefault("recommendations", {})["api_key"] = _val("rec_api_key")
        if _val("rec_url"):
            digi.setdefault("recommendations", {})["url"] = _val("rec_url")
        # Legacy single key
        if _val("diginetica_api_key"):
            meta["diginetica_api_key"] = _val("diginetica_api_key")
            digi.setdefault("sort", {})["api_key"] = _val("diginetica_api_key")

        if digi:
            meta["diginetica"] = digi

        # site_url
        site_url = _val("site_url")
        if site_url:
            if not site_url.startswith("http"):
                site_url = "https://" + site_url
            meta["site_url"] = site_url

        # Запросы для чекапа (top)
        queries_raw = _val("checkup_queries_top")
        if queries_raw:
            queries = [q.strip() for q in queries_raw.split(",") if q.strip()]
            cq = meta.get("checkup_queries", {})
            cq["top"] = queries
            meta["checkup_queries"] = cq

        client.integration_metadata = meta
        flag_modified(client, "integration_metadata")
        updated += 1

    db.commit()
    logger.info(f"Admin import API keys: updated={updated}, skipped={skipped}, errors={len(errors)}")
    return {"ok": True, "updated": updated, "skipped": skipped, "errors": errors}




@router.get("/api/admin/clients/api-keys")
async def api_admin_list_api_keys(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Список всех клиентов с их API keys (только admin)."""
    _require_admin(auth_token, db)
    clients = db.query(Client).order_by(Client.name).all()
    return {
        "clients": [
            {
                "id": c.id,
                "name": c.name,
                "segment": c.segment,
                "has_api_key": bool((c.integration_metadata or {}).get("diginetica_api_key")),
                "api_key": (c.integration_metadata or {}).get("diginetica_api_key", ""),
                "site_url": c.domain or (c.integration_metadata or {}).get("site_url", ""),
                "has_queries": bool((c.integration_metadata or {}).get("checkup_queries", {}).get("top")),
            }
            for c in clients
        ]
    }




@router.patch("/api/admin/clients/{client_id}/api-key")
async def api_admin_set_api_key(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Установить/обновить API key конкретного клиента (только admin)."""
    _require_admin(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    body = await request.json()
    from sqlalchemy.orm.attributes import flag_modified
    meta = dict(client.integration_metadata or {})

    if "diginetica_api_key" in body:
        meta["diginetica_api_key"] = body["diginetica_api_key"]
    if "site_url" in body:
        meta["site_url"] = body["site_url"]
    if "checkup_queries" in body:
        meta["checkup_queries"] = body["checkup_queries"]

    client.integration_metadata = meta
    flag_modified(client, "integration_metadata")
    db.commit()
    return {"ok": True}




@router.post("/api/admin/reset-data")
async def api_reset_data(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Удалить все тестовые данные (только для админа)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403)

    # Удаляем в правильном порядке (из-за FK)
    db.query(Task).delete()
    db.query(Meeting).delete()
    db.query(CheckUp).delete()
    db.query(QBR).delete()
    db.query(AccountPlan).delete()
    db.query(Client).delete()
    db.commit()
    return {"ok": True, "message": "Все данные очищены"}




@router.get("/api/admin/jobs")
async def api_jobs_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != "admin": raise HTTPException(status_code=403)

    jobs = [
        {"id": "mr_sync",      "name": "Merchrules Sync",     "description": "Синхронизация клиентов и задач из Merchrules", "interval": "каждый час"},
        {"id": "checkup",      "name": "Checkup плановый",    "description": "Проверка просроченных чекапов и создание задач", "interval": "08:00 ежедневно"},
        {"id": "churn",        "name": "Churn Scoring",       "description": "Пересчёт риска оттока для всех клиентов", "interval": "воскресенье 02:00"},
        {"id": "tg_digest",    "name": "Telegram Digest",     "description": "Умный утренний дайджест менеджерам", "interval": "09:00 ежедневно"},
        {"id": "airtable_sync","name": "Airtable Sync",       "description": "Синхронизация клиентов с Airtable", "interval": "каждый час"},
        {"id": "auto_tasks",   "name": "AutoTask Rules",      "description": "Создание задач по правилам автозадач", "interval": "каждые 6 часов"},
    ]

    for j in jobs:
        st = _job_status.get(j["id"], {})
        j["status"]      = st.get("status", "pending")
        j["last_run"]    = st.get("last_run")
        j["next_run"]    = st.get("next_run")
        j["duration_ms"] = st.get("duration_ms")
        j["error"]       = st.get("error")

    return {"jobs": jobs, "log": list(reversed(_job_log[-50:]))}




@router.post("/api/admin/jobs/{job_id}/run")
async def api_job_run(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != "admin": raise HTTPException(status_code=403)

    _job_status[job_id] = {"status": "running", "last_run": datetime.utcnow().isoformat()}
    log_job(job_id, f"Запущен вручную пользователем {user.name}")

    import asyncio, time

    async def run():
        start = time.time()
        try:
            if job_id == "churn":
                from churn import recalculate_all
                n = await recalculate_all(db)
                msg = f"Пересчитано: {n} клиентов"
            elif job_id == "mr_sync":
                msg = "Запущен через API — результат в логе"
            elif job_id == "auto_tasks":
                from models import AutoTaskRule
                rules = db.query(AutoTaskRule).filter(AutoTaskRule.is_active == True).all()
                msg = f"Правил обработано: {len(rules)}"
            else:
                msg = f"Job {job_id} не имеет прямого вызова"

            ms = int((time.time() - start) * 1000)
            _job_status[job_id] = {"status": "ok", "last_run": datetime.utcnow().isoformat(), "duration_ms": ms}
            log_job(job_id, f"Завершён за {ms}мс: {msg}")
        except Exception as e:
            _job_status[job_id] = {"status": "error", "last_run": datetime.utcnow().isoformat(), "error": str(e)}
            log_job(job_id, f"Ошибка: {e}", level="error")

    asyncio.create_task(run())
    return {"ok": True}





# ── User Management ───────────────────────────────────────────────────────────

@router.get("/api/admin/users")
async def list_users(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Список всех пользователей (только admin)."""
    from deps import require_admin
    # inline check
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403)

    users = db.query(User).order_by(User.created_at).all()
    return {"users": [
        {
            "id": u.id,
            "email": u.email,
            "first_name": u.first_name or "",
            "last_name": u.last_name or "",
            "role": u.role,
            "is_active": u.is_active,
            "telegram_id": u.telegram_id,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]}


@router.post("/api/admin/users")
async def create_manager(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Создать нового менеджера (только admin).
    Body: {email, password, first_name, last_name, role}
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token, hash_password
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    admin = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not admin or admin.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    data = await request.json()
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    if not email or not password:
        raise HTTPException(status_code=422, detail="email and password required")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="User already exists")

    new_user = User(
        email=email,
        first_name=data.get("first_name", ""),
        last_name=data.get("last_name", ""),
        role=data.get("role", "manager"),
        is_active=True,
        hashed_password=hash_password(password),
        settings={"onboarding_complete": False},
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"ok": True, "id": new_user.id, "email": new_user.email}


@router.patch("/api/admin/users/{user_id}")
async def update_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Обновить пользователя: пароль, роль, активность."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token, hash_password
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    admin = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not admin or admin.role != "admin":
        raise HTTPException(status_code=403)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404)

    data = await request.json()
    if "password" in data and data["password"]:
        target.hashed_password = hash_password(data["password"])
    if "role" in data:
        target.role = data["role"]
    if "is_active" in data:
        target.is_active = bool(data["is_active"])
    if "first_name" in data:
        target.first_name = data["first_name"]
    if "telegram_id" in data:
        target.telegram_id = data["telegram_id"] or None

    db.commit()
    return {"ok": True}


@router.delete("/api/admin/users/{user_id}")
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Удалить пользователя."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    admin = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not admin or admin.role != "admin":
        raise HTTPException(status_code=403)
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404)
    db.delete(target)
    db.commit()
    return {"ok": True}
