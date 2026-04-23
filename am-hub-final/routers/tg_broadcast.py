"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional, List
from datetime import datetime
import os
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Client, User, TgBroadcast, TgBroadcastLog
from auth import decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _require_user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user


def _broadcast_dict(b: TgBroadcast) -> dict:
    return {
        "id": b.id,
        "name": b.name,
        "message_text": b.message_text,
        "target_type": b.target_type,
        "target_filter": b.target_filter,
        "schedule_type": b.schedule_type,
        "schedule_cron": b.schedule_cron,
        "is_active": b.is_active,
        "send_count": b.send_count,
        "next_run_at": b.next_run_at.isoformat() if b.next_run_at else None,
        "last_run_at": b.last_run_at.isoformat() if b.last_run_at else None,
        "created_by": b.created_by,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@router.get("/api/broadcasts")
async def api_list_broadcasts(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    q = db.query(TgBroadcast)
    if user.role != "admin":
        q = q.filter(TgBroadcast.created_by == user.email)
    broadcasts = q.order_by(TgBroadcast.created_at.desc()).all()
    return {"broadcasts": [_broadcast_dict(b) for b in broadcasts]}


@router.post("/api/broadcasts")
async def api_create_broadcast(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    data = await request.json()
    b = TgBroadcast(
        created_by=user.email,
        name=data["name"],
        message_text=data["message_text"],
        target_type=data.get("target_type", "manual"),
        target_filter=data.get("target_filter") or {},
        schedule_type=data.get("schedule_type", "once"),
        schedule_cron=data.get("schedule_cron"),
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return {"ok": True, "id": b.id}


@router.patch("/api/broadcasts/{broadcast_id}")
async def api_update_broadcast(
    broadcast_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    b = db.query(TgBroadcast).filter(TgBroadcast.id == broadcast_id).first()
    if not b:
        raise HTTPException(status_code=404)
    if user.role != "admin" and b.created_by != user.email:
        raise HTTPException(status_code=403)
    data = await request.json()
    allowed = {"name", "message_text", "target_type", "target_filter", "schedule_type", "schedule_cron", "is_active", "next_run_at"}
    for k, v in data.items():
        if k in allowed and hasattr(b, k):
            setattr(b, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/api/broadcasts/{broadcast_id}")
async def api_delete_broadcast(
    broadcast_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    b = db.query(TgBroadcast).filter(TgBroadcast.id == broadcast_id).first()
    if not b:
        raise HTTPException(status_code=404)
    if user.role != "admin" and b.created_by != user.email:
        raise HTTPException(status_code=403)
    db.delete(b)
    db.commit()
    return {"ok": True}


@router.post("/api/broadcasts/{broadcast_id}/send")
async def api_send_broadcast(
    broadcast_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    b = db.query(TgBroadcast).filter(TgBroadcast.id == broadcast_id).first()
    if not b:
        raise HTTPException(status_code=404)
    if user.role != "admin" and b.created_by != user.email:
        raise HTTPException(status_code=403)

    tg_token = _env("TG_BOT_TOKEN")
    notify_chat_id = _env("TG_NOTIFY_CHAT_ID")
    if not tg_token or not notify_chat_id:
        return {"error": "TG_BOT_TOKEN or TG_NOTIFY_CHAT_ID not configured"}

    q = db.query(Client)
    tf = b.target_filter or {}
    if b.target_type == "segment" and tf.get("segment"):
        q = q.filter(Client.segment == tf["segment"])
    elif b.target_type == "health_risk" and tf.get("health_max") is not None:
        q = q.filter(Client.health_score <= float(tf["health_max"]))
    clients = q.filter(Client.is_active == True).all()

    client_names = [c.name for c in clients]
    header = f"📢 *{b.name}*\n\n{b.message_text}"
    if client_names:
        header += "\n\n*Клиенты:*\n" + "\n".join(f"• {n}" for n in client_names)

    import httpx
    sent = 0
    failed = 0
    error_msg = None
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={
                    "chat_id": notify_chat_id,
                    "text": header,
                    "parse_mode": "Markdown",
                },
            )
            if resp.status_code == 200:
                sent += 1
                status_val = "sent"
            else:
                failed += 1
                status_val = "failed"
                error_msg = resp.text[:300]
    except Exception as e:
        failed += 1
        status_val = "failed"
        error_msg = str(e)

    log = TgBroadcastLog(
        broadcast_id=b.id,
        tg_chat_id=notify_chat_id,
        status=status_val,
        error=error_msg,
    )
    db.add(log)
    b.send_count = (b.send_count or 0) + 1
    b.last_run_at = datetime.utcnow()
    db.commit()
    return {"ok": status_val == "sent", "sent": sent, "failed": failed, "client_count": len(clients)}


@router.get("/api/broadcasts/{broadcast_id}/logs")
async def api_broadcast_logs(
    broadcast_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    b = db.query(TgBroadcast).filter(TgBroadcast.id == broadcast_id).first()
    if not b:
        raise HTTPException(status_code=404)
    if user.role != "admin" and b.created_by != user.email:
        raise HTTPException(status_code=403)
    logs = (
        db.query(TgBroadcastLog)
        .filter(TgBroadcastLog.broadcast_id == broadcast_id)
        .order_by(TgBroadcastLog.sent_at.desc())
        .limit(200)
        .all()
    )
    return {"logs": [{
        "id": l.id,
        "client_id": l.client_id,
        "tg_chat_id": l.tg_chat_id,
        "status": l.status,
        "error": l.error,
        "sent_at": l.sent_at.isoformat() if l.sent_at else None,
    } for l in logs]}
