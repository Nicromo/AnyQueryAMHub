"""Auto-split from misc.py"""
from typing import Optional, List
from datetime import datetime, timedelta
import os, json, logging

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Cookie, Form
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
from auth import decode_access_token, hash_password, verify_password, log_audit
from deps import require_user, require_admin, optional_user
from error_handlers import log_error

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

    return {"notifications": notifications}



@router.get("/api/inbox")
async def api_inbox(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить сообщения Inbox."""
    if not auth_token:
        return {"items": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"items": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    items = []
    now = datetime.now()

    # Новые уведомления
    notifs = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).order_by(Notification.created_at.desc()).limit(20).all()
    for n in notifs:
        items.append({"type": "notification", "title": n.title, "message": n.message, "date": n.created_at.isoformat() if n.created_at else None, "priority": n.type})

    # Просроченные чекапы
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()
    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last and (now - last).days > interval:
            items.append({"type": "overdue", "title": f"Просрочен чекап: {c.name}", "message": f"Последний контакт {(now-last).days} дн. назад (норма: {interval})", "date": last.isoformat(), "priority": "high", "client_id": c.id})

    # Blocked tasks
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    blocked = task_q.filter(Task.status == "blocked").all()
    for t in blocked:
        items.append({"type": "blocked", "title": f"Заблокирована: {t.title}", "message": t.client.name if t.client else "", "priority": "high", "client_id": t.client_id})

    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return {"items": items[:50]}




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



