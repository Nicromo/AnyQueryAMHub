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
    AccountPlan,
    AuditLog,
    CheckUp,
    Client,
    ClientHistory,
    ClientNote,
    FollowupTemplate,
    Meeting,
    Notification,
    QBR,
    SyncLog,
    Task,
    TaskComment,
    TelegramSubscription,
    User,
    VoiceNote,
)
from auth import decode_access_token, hash_password, verify_password, log_audit
from deps import require_user, require_admin, optional_user
from error_handlers import log_error
from models import CHECKUP_INTERVALS
from redis_cache import cache_get, cache_set, cache_del

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

@router.get("/api/notifications")
async def api_notifications(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить уведомления: пора написать, просрочки и т.д."""
    if not auth_token:
        return {"notifications": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notifications": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"notifications": []}

    # Кеш 120 сек — polling каждые 60 сек от 18 менеджеров = экономим ~540 DB запросов/мин
    ck = f"notif:{user.id}"
    cached = cache_get(ck)
    if cached:
        return cached

    notifications = []
    now = datetime.now()

    # Клиенты без активности
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last:
            days_since = (now - last).days
            if days_since > interval:
                notifications.append({
                    "type": "overdue_checkup",
                    "priority": "high",
                    "message": f"Пора написать: {c.name} (последний контакт {days_since} дн. назад)",
                    "client_id": c.id,
                    "client_name": c.name,
                })
            elif days_since > interval - 14:
                notifications.append({
                    "type": "checkup_soon",
                    "priority": "medium",
                    "message": f"Скоро чекап: {c.name} (через {interval - days_since} дн.)",
                    "client_id": c.id,
                    "client_name": c.name,
                })

    # Blocked tasks
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    blocked = task_q.filter(Task.status == "blocked").all()
    for t in blocked:
        notifications.append({
            "type": "blocked_task",
            "priority": "high",
            "message": f"Заблокирована задача: {t.title} ({t.client.name if t.client else ''})",
            "client_id": t.client_id,
        })

    result = {"notifications": notifications}
    cache_set(ck, result, ttl=120)
    return result



@router.get("/api/inbox")
async def api_inbox(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    read: Optional[str] = None,        # "0" — только непрочитанные, "1" — только прочитанные, None — все
    type: Optional[str] = None,        # info/warning/alert/success
    kind: Optional[str] = None,        # sync_fail / task_deadline / meeting_soon / ...
    client_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    Единый inbox: Notification + derived события (просроченные чекапы, blocked tasks).
    Фильтры: ?read=0&type=alert&kind=sync_fail&client_id=5&limit=20&offset=0

    Snoozed-записи (snoozed_until > now) скрываются.
    Dismissed (dismissed_at != NULL) — скрыты всегда.
    """
    if not auth_token:
        return {"items": [], "total": 0}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"items": [], "total": 0}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"items": [], "total": 0}

    now = datetime.utcnow()

    # 1. Реальные Notification (с фильтрами)
    q = db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.dismissed_at.is_(None),
    )
    q = q.filter((Notification.snoozed_until.is_(None)) | (Notification.snoozed_until < now))
    if read == "0":
        q = q.filter(Notification.is_read == False)
    elif read == "1":
        q = q.filter(Notification.is_read == True)
    if type:
        q = q.filter(Notification.type == type)
    if kind:
        q = q.filter(Notification.kind == kind)
    if client_id is not None:
        q = q.filter(
            (Notification.related_resource_type == "client") &
            (Notification.related_resource_id == client_id)
        )
    total = q.count()
    rows = q.order_by(Notification.created_at.desc()).offset(max(0, offset)).limit(max(1, min(200, limit))).all()
    items = [{
        "id": n.id,
        "source": "notification",
        "kind": n.kind or n.type,
        "type": n.type,
        "title": n.title,
        "message": n.message,
        "is_read": bool(n.is_read),
        "snoozed_until": n.snoozed_until.isoformat() if n.snoozed_until else None,
        "related_type": n.related_resource_type,
        "related_id": n.related_resource_id,
        "client_id": n.related_resource_id if n.related_resource_type == "client" else None,
        "date": n.created_at.isoformat() if n.created_at else None,
    } for n in rows]

    # 2. Derived: overdue checkups + blocked tasks (без пагинации, дешевые запросы)
    if offset == 0 and not kind and not client_id:
        client_q = db.query(Client)
        if user.role == "manager":
            client_q = client_q.filter(Client.manager_email == user.email)
        clients = client_q.all()
        for c in clients:
            interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
            last = c.last_meeting_date or c.last_checkup
            if last and (now - last).days > interval:
                items.append({
                    "id": f"overdue-{c.id}",
                    "source": "derived", "kind": "overdue_checkup",
                    "type": "alert",
                    "title": f"Просрочен чекап: {c.name}",
                    "message": f"Последний контакт {(now-last).days} дн. назад (норма: {interval})",
                    "is_read": False, "snoozed_until": None,
                    "related_type": "client", "related_id": c.id, "client_id": c.id,
                    "date": last.isoformat(),
                })

        task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
        if user.role == "manager":
            task_q = task_q.filter(Client.manager_email == user.email)
        for t in task_q.filter(Task.status == "blocked").all():
            items.append({
                "id": f"blocked-{t.id}",
                "source": "derived", "kind": "blocked_task",
                "type": "warning",
                "title": f"Заблокирована: {t.title}",
                "message": t.client.name if t.client else "",
                "is_read": False, "snoozed_until": None,
                "related_type": "task", "related_id": t.id,
                "client_id": t.client_id,
                "date": (t.created_at or now).isoformat(),
            })

    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return {"items": items[:limit] if offset == 0 else items,
            "total": total,
            "unread": db.query(Notification).filter(
                Notification.user_id == user.id,
                Notification.is_read == False,
                Notification.dismissed_at.is_(None),
            ).count()}


@router.patch("/api/inbox/{notif_id}/read")
async def api_inbox_read_one(
    notif_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token:
        raise HTTPException(401)
    from auth import decode_access_token
    p = decode_access_token(auth_token)
    if not p:
        raise HTTPException(401)
    user = db.query(User).filter(User.id == int(p.get("sub"))).first()
    n = db.query(Notification).filter(
        Notification.id == notif_id, Notification.user_id == user.id
    ).first()
    if not n:
        raise HTTPException(404)
    n.is_read = True
    n.read_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/api/inbox/{notif_id}/snooze")
async def api_inbox_snooze(
    notif_id: int, request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Скрыть нотификацию до snoozed_until = now + hours (default 24)."""
    if not auth_token:
        raise HTTPException(401)
    from auth import decode_access_token
    p = decode_access_token(auth_token)
    if not p:
        raise HTTPException(401)
    user = db.query(User).filter(User.id == int(p.get("sub"))).first()
    try:
        body = await request.json()
    except Exception:
        body = {}
    hours = int(body.get("hours", 24))
    if hours < 1 or hours > 24 * 30:
        raise HTTPException(400, "hours must be 1..720")
    n = db.query(Notification).filter(
        Notification.id == notif_id, Notification.user_id == user.id
    ).first()
    if not n:
        raise HTTPException(404)
    n.snoozed_until = datetime.utcnow() + timedelta(hours=hours)
    db.commit()
    return {"ok": True, "snoozed_until": n.snoozed_until.isoformat()}


@router.post("/api/inbox/{notif_id}/dismiss")
async def api_inbox_dismiss(
    notif_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token:
        raise HTTPException(401)
    from auth import decode_access_token
    p = decode_access_token(auth_token)
    if not p:
        raise HTTPException(401)
    user = db.query(User).filter(User.id == int(p.get("sub"))).first()
    n = db.query(Notification).filter(
        Notification.id == notif_id, Notification.user_id == user.id
    ).first()
    if not n:
        raise HTTPException(404)
    n.dismissed_at = datetime.utcnow()
    n.is_read = True
    db.commit()
    return {"ok": True}




@router.post("/api/inbox/mark-read")
async def api_inbox_mark_read(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Отметить уведомления прочитанными."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).update({"is_read": True})
    db.commit()
    return {"ok": True}




@router.get("/api/inbox/items")
async def api_inbox_items(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Алиас /api/inbox для совместимости с base.html."""
    return await api_inbox(db=db, auth_token=auth_token)



@router.get("/api/telegram/status")
async def api_tg_status(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import TelegramSubscription
    sub = db.query(TelegramSubscription).filter(TelegramSubscription.user_id == user.id).first()
    return {"connected": bool(sub and sub.chat_id), "chat_id": sub.chat_id if sub else None,
            "settings": {k: getattr(sub, k) for k in ("notify_overdue","notify_health_drop","notify_tasks","notify_daily")} if sub else {}}




@router.post("/api/telegram/connect")
async def api_tg_connect(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    body = await request.json()
    chat_id = str(body.get("chat_id", "")).strip()
    if not chat_id: return {"ok": False, "error": "chat_id обязателен"}

    # Проверяем что можем отправить сообщение
    from telegram_bot import send_message
    hub_url = str(request.base_url).rstrip("/")
    ok = await send_message(chat_id, f"✅ <b>AM Hub подключён!</b>\nМенеджер: {user.name}\n<a href='{hub_url}'>Открыть хаб →</a>")
    if not ok: return {"ok": False, "error": "Не удалось отправить сообщение. Проверьте chat_id и что бот запущен."}

    from models import TelegramSubscription
    sub = db.query(TelegramSubscription).filter(TelegramSubscription.user_id == user.id).first()
    if sub:
        sub.chat_id = chat_id; sub.is_active = True
    else:
        sub = TelegramSubscription(user_id=user.id, chat_id=chat_id)
        db.add(sub)
    db.commit()
    return {"ok": True}




@router.patch("/api/telegram/settings")
async def api_tg_settings(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    body = await request.json()
    from models import TelegramSubscription
    sub = db.query(TelegramSubscription).filter(TelegramSubscription.user_id == user.id).first()
    if not sub: return {"ok": False, "error": "Сначала подключите Telegram"}
    for k in ("notify_overdue","notify_health_drop","notify_tasks","notify_daily"):
        if k in body: setattr(sub, k, body[k])
    db.commit()
    return {"ok": True}



