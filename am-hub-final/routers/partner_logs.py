"""
PartnerLog router — единая история по партнёру.

Endpoints:
  GET    /api/clients/{client_id}/logs?limit=50     — лента последних событий
  POST   /api/clients/{client_id}/logs              — ручная запись (заметка/коммуникация)
  DELETE /api/clients/{client_id}/logs/{log_id}     — удалить запись (авт. автоматические
                                                      нельзя стереть — вернёт 403)

Автоматические записи (merch_rule_*, synonym_*, whitelist_*) создаются из других
модулей через helper `log_event(db, client_id, event_type, ..., source="merchrules")`.
"""
from typing import Optional
from datetime import datetime
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models import Client, PartnerLog, User

logger = logging.getLogger(__name__)
router = APIRouter()


async def _auth(request: Request, db: Session, auth_token: Optional[str]) -> User:
    from routers.api_tokens import resolve_user
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return user


def _serialize(l: PartnerLog) -> dict:
    return {
        "id":         l.id,
        "client_id":  l.client_id,
        "event_type": l.event_type,
        "title":      l.title,
        "body":       l.body,
        "payload":    l.payload or {},
        "source":     l.source,
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "created_by": l.created_by,
    }


# ── Helper для автологирования из других модулей ──────────────────────────
def log_event(
    db: Session,
    client_id: int,
    event_type: str,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    payload: Optional[dict] = None,
    source: str = "system",
    created_by: Optional[str] = None,
) -> PartnerLog:
    """Создать запись в PartnerLog. Используется из sync.py, merchrules flow,
    auto_tasks и т.п. Commit должен сделать вызывающий код."""
    entry = PartnerLog(
        client_id=client_id,
        event_type=event_type,
        title=title,
        body=body,
        payload=payload or {},
        source=source,
        created_by=created_by,
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    db.flush()
    return entry


# ── REST endpoints ──────────────────────────────────────────────────────────
@router.get("/api/clients/{client_id}/logs")
async def list_client_logs(
    client_id: int,
    request: Request,
    limit: int = 50,
    event_type: Optional[str] = None,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = await _auth(request, db, auth_token)
    limit = max(1, min(limit, 200))

    q = db.query(PartnerLog).filter(PartnerLog.client_id == client_id)
    if event_type:
        q = q.filter(PartnerLog.event_type == event_type)
    rows = q.order_by(desc(PartnerLog.created_at)).limit(limit).all()
    return {"logs": [_serialize(r) for r in rows]}


class LogCreate(BaseModel):
    event_type: str = "note"
    title: Optional[str] = None
    body: Optional[str] = None
    source: Optional[str] = "manual"
    payload: Optional[dict] = None


@router.post("/api/clients/{client_id}/logs")
async def create_client_log(
    client_id: int,
    data: LogCreate,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = await _auth(request, db, auth_token)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Клиент не найден")

    et = (data.event_type or "note").strip() or "note"
    body = (data.body or "").strip()
    title = (data.title or "").strip() or None
    if not body and not title:
        return {"ok": False, "error": "Пустая запись"}

    entry = PartnerLog(
        client_id=client_id,
        user_id=user.id,
        event_type=et,
        title=title,
        body=body,
        payload=data.payload or {},
        source=(data.source or "manual"),
        created_by=getattr(user, "name", None) or getattr(user, "email", None),
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    try:
        db.commit()
        db.refresh(entry)
    except Exception as e:
        db.rollback()
        logger.exception("partner_log create failed")
        raise HTTPException(500, f"DB error: {e}")

    return {"ok": True, "log": _serialize(entry)}


@router.delete("/api/clients/{client_id}/logs/{log_id}")
async def delete_client_log(
    client_id: int,
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = await _auth(request, db, auth_token)

    l = db.query(PartnerLog).filter(
        PartnerLog.id == log_id,
        PartnerLog.client_id == client_id,
    ).first()
    if not l:
        raise HTTPException(404, "Запись не найдена")

    # Автоматические записи (source != manual) не удаляем, чтобы сохранить аудит.
    if (l.source or "") not in ("manual", ""):
        raise HTTPException(403, "Автоматические записи нельзя удалить")

    db.delete(l)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"DB error: {e}")

    return {"ok": True}
