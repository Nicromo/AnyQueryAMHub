"""Auto-split from misc.py"""
from typing import Optional, List
from datetime import datetime, timedelta
import os, json, logging

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote, ClientHistory, CHECKUP_INTERVALS,
)
from auth import decode_access_token, hash_password, verify_password, log_audit, get_current_user
from deps import require_user, require_admin, optional_user
from error_handlers import log_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _checkup_auth(auth_token: Optional[str], db, request=None):
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

@router.get("/api/cabinet/my-clients")
async def api_cabinet_my_clients(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Клиенты текущего менеджера (назначены через assignment или manager_email)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    # Объединяем: manager_email ИЛИ user_client_assignment
    from sqlalchemy import or_
    assigned_ids = [a.client_id for a in
                    db.query(UserClientAssignment).filter(UserClientAssignment.user_id == user.id).all()]
    clients = db.query(Client).filter(
        or_(Client.manager_email == user.email, Client.id.in_(assigned_ids))
    ).order_by(Client.name).all()

    now = datetime.now()
    result = []
    for c in clients:
        meta = c.integration_metadata or {}
        digi = meta.get("diginetica", {})
        products = [p for p in ("sort", "autocomplete", "recommendations") if digi.get(p, {}).get("api_key")]
        if not products and meta.get("diginetica_api_key"):
            products = ["sort"]
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        days_since = (now - last).days if last else 999
        result.append({
            "id": c.id, "name": c.name, "segment": c.segment,
            "health_score": c.health_score,
            "domain": c.domain or meta.get("site_url", ""),
            "products": products,
            "has_api_keys": len(products) > 0,
            "days_since_checkup": days_since,
            "checkup_overdue": days_since > interval,
            "merchrules_id": c.merchrules_account_id,
            "last_meeting": c.last_meeting_date.isoformat() if c.last_meeting_date else None,
        })
    return {"clients": result, "total": len(result)}




@router.get("/api/cabinet/available-clients")
async def api_cabinet_available_clients(
    search: str = "",
    segment: str = "",
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Клиенты без менеджера или со свободными слотами — для добавления в свой кабинет."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    from sqlalchemy import or_
    # Уже назначенные у этого менеджера
    my_ids = {a.client_id for a in
              db.query(UserClientAssignment).filter(UserClientAssignment.user_id == user.id).all()}
    my_emails = {user.email}

    q = db.query(Client)
    # Исключаем уже своих
    if my_ids:
        q = q.filter(~Client.id.in_(my_ids))
    q = q.filter(or_(Client.manager_email != user.email, Client.manager_email.is_(None)))

    if search:
        q = q.filter(Client.name.ilike(f"%{search}%"))
    if segment:
        q = q.filter(Client.segment == segment)

    clients = q.order_by(Client.name).limit(100).all()
    return {
        "clients": [
            {
                "id": c.id, "name": c.name, "segment": c.segment,
                "health_score": c.health_score,
                "manager_email": c.manager_email or "—",
            }
            for c in clients
        ]
    }




@router.post("/api/cabinet/assign/{client_id}")
async def api_cabinet_assign(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Добавить клиента в свой кабинет."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    # Проверяем нет ли уже
    existing = db.query(UserClientAssignment).filter(
        UserClientAssignment.user_id == user.id,
        UserClientAssignment.client_id == client_id,
    ).first()
    if not existing:
        db.add(UserClientAssignment(user_id=user.id, client_id=client_id))
        # Устанавливаем manager_email если он пустой
        if not client.manager_email:
            client.manager_email = user.email
        db.commit()
    return {"ok": True}




@router.delete("/api/cabinet/assign/{client_id}")
async def api_cabinet_unassign(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Убрать клиента из своего кабинета."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    db.query(UserClientAssignment).filter(
        UserClientAssignment.user_id == user.id,
        UserClientAssignment.client_id == client_id,
    ).delete()
    db.commit()
    return {"ok": True}


@router.get("/api/cabinets/{cabinet_id}")
async def api_get_cabinet(
    cabinet_id: str,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Возвращает данные кабинета для расширения Search Quality Checkup.
    cabinet_id = client.id или client.merchrules_account_id
    """
    user = _checkup_auth(auth_token, db, request)

    # Ищем клиента по id или по merchrules_account_id
    client = None
    if cabinet_id.isdigit():
        client = db.query(Client).filter(Client.id == int(cabinet_id)).first()
    if not client:
        client = db.query(Client).filter(
            Client.merchrules_account_id == cabinet_id
        ).first()
    if not client:
        raise HTTPException(status_code=404, detail=f"Кабинет {cabinet_id} не найден")

    meta = client.integration_metadata or {}
    api_key = (
        meta.get("diginetica_api_key")
        or meta.get("search_api_key")
        or meta.get("apiKey")
        or ""
    )
    site_url = client.domain or meta.get("site_url") or ""
    if site_url and not site_url.startswith("http"):
        site_url = "https://" + site_url

    # Fallback: тянем apiKey из Merchrules /api/site/all — этот endpoint
    # отдаёт все сайты с их apiKey, удобно чтобы менеджер не вводил вручную.
    # Пробуем по merchrules_account_id и по всем site_ids из JSONB.
    fetched_from_mr = False
    if not api_key and (client.merchrules_account_id or client.site_ids):
        try:
            from merchrules_sync import fetch_site_api_keys
            mr_settings = (user.settings or {}).get("merchrules", {}) or {}
            login = mr_settings.get("login") or ""
            try:
                from crypto import dec as _dec
                password = _dec(mr_settings.get("password", "")) or ""
            except Exception:
                password = mr_settings.get("password") or ""
            site_keys = await fetch_site_api_keys(login=login, password=password)
            candidates = []
            if client.merchrules_account_id:
                candidates.append(str(client.merchrules_account_id))
            if isinstance(client.site_ids, list):
                candidates.extend([str(s) for s in client.site_ids])
            for sid in candidates:
                info = site_keys.get(sid)
                if info and info.get("apiKey"):
                    api_key = info["apiKey"]
                    fetched_from_mr = True
                    # Сохраняем в meta чтобы в следующий раз не ходить
                    new_meta = dict(meta)
                    new_meta["diginetica_api_key"] = api_key
                    if info.get("domain") and not site_url:
                        site_url = "https://" + info["domain"] if not info["domain"].startswith("http") else info["domain"]
                    client.integration_metadata = new_meta
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(client, "integration_metadata")
                    db.commit()
                    break
        except Exception as _e:
            logger.warning(f"fetch_site_api_keys fallback failed: {_e}")

    digi = meta.get("diginetica", {})

    products = {}
    for product in ("sort", "autocomplete", "recommendations"):
        p = digi.get(product, {})
        if p.get("api_key"):
            products[product] = {
                "apiKey": p["api_key"],
                "url":    p.get("url", ""),
            }
    # Legacy fallback — если нет структурированных, используем diginetica_api_key как sort
    if not products and api_key:
        products["sort"] = {"apiKey": api_key, "url": "https://sort.diginetica.net/search"}

    return {
        "ok": True,
        "cabinetId": str(client.id),
        "clientName": client.name,
        # Основной ключ (Sort или первый доступный) — для обратной совместимости с расширением
        "apiKey": (products.get("sort") or next(iter(products.values()), {})).get("apiKey", ""),
        "siteUrl": site_url,
        "segment": client.segment,
        "healthScore": client.health_score,
        # Все продукты — расширение использует для выбора типа чекапа
        "products": products,
        "hasSort": "sort" in products,
        "hasAutocomplete": "autocomplete" in products,
        "hasRecommendations": "recommendations" in products,
    }




@router.get("/api/cabinets/{cabinet_id}/merch-rules")
async def api_merch_rules(
    cabinet_id: str,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Мерч-правила клиента из integration_metadata."""
    user = _checkup_auth(auth_token, db, request)

    client = None
    if cabinet_id.isdigit():
        client = db.query(Client).filter(Client.id == int(cabinet_id)).first()
    if not client:
        client = db.query(Client).filter(
            Client.merchrules_account_id == cabinet_id
        ).first()
    if not client:
        return []

    meta = client.integration_metadata or {}
    return meta.get("merch_rules", [])




@router.post("/api/onboarding/complete")
async def api_complete_onboarding(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Отметить что онбординг пройден."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    settings = user.settings or {}
    settings["onboarding_complete"] = True
    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}




@router.get("/api/onboarding/status")
async def api_onboarding_status(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Проверить, пройден ли онбординг."""
    if not auth_token:
        return {"onboarding_complete": True}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"onboarding_complete": True}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"onboarding_complete": True}
    settings = user.settings or {}
    return {"onboarding_complete": settings.get("onboarding_complete", False)}



@router.get("/api/onboarding/progress")
async def api_onboarding_progress(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models import OnboardingProgress
    prog = db.query(OnboardingProgress).filter(OnboardingProgress.user_id == user.id).first()
    completed = prog.completed if prog else []
    steps = [
        {"id": "hub_url",      "title": "Настройте AM Hub URL",      "done": "hub_url" in completed},
        {"id": "mr_connect",   "title": "Подключите Merchrules",      "done": "mr_connect" in completed},
        {"id": "add_clients",  "title": "Добавьте первых клиентов",   "done": "add_clients" in completed},
        {"id": "first_task",   "title": "Создайте первую задачу",     "done": "first_task" in completed},
        {"id": "tg_connect",   "title": "Подключите Telegram",        "done": "tg_connect" in completed},
        {"id": "first_checkup","title": "Запустите первый чекап",     "done": "first_checkup" in completed},
    ]
    done_count = sum(1 for s in steps if s["done"])
    return {"steps": steps, "done": done_count, "total": len(steps), "completed": done_count == len(steps)}




@router.post("/api/onboarding/complete-step")
async def api_onboarding_complete_step(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    body = await request.json()
    step = body.get("step", "")
    from models import OnboardingProgress
    from sqlalchemy.orm.attributes import flag_modified
    prog = db.query(OnboardingProgress).filter(OnboardingProgress.user_id == user.id).first()
    if not prog:
        prog = OnboardingProgress(user_id=user.id, completed=[])
        db.add(prog)
    completed = list(prog.completed or [])
    if step and step not in completed:
        completed.append(step)
        prog.completed = completed
        flag_modified(prog, "completed")
    db.commit()
    return {"ok": True, "completed": completed}


@router.post("/api/onboarding/skip")
async def api_onboarding_skip(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Пропустить онбординг."""
    from models import OnboardingProgress
    from sqlalchemy.orm.attributes import flag_modified
    prog = db.query(OnboardingProgress).filter(OnboardingProgress.user_id == user.id).first()
    if not prog:
        prog = OnboardingProgress(user_id=user.id, completed=[], skipped=True)
        db.add(prog)
    else:
        prog.skipped = True
        flag_modified(prog, "completed")
    db.commit()
    return {"ok": True}


