"""
Bulk actions на портфеле — мультивыбор клиентов → массовое действие.

Endpoints:
  POST /api/clients/bulk/mark-checkup       {client_ids[], note?}
  POST /api/clients/bulk/start-onboarding   {client_ids[]}
  POST /api/clients/bulk/transfer           {client_ids[], to_user_id}
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import Client, CheckUp, User

logger = logging.getLogger(__name__)
router = APIRouter()


def _user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(401)
    p = decode_access_token(auth_token)
    if not p:
        raise HTTPException(401)
    u = db.query(User).filter(User.id == int(p.get("sub"))).first()
    if not u:
        raise HTTPException(401)
    return u


def _accessible_clients(db: Session, user: User, ids: List[int]) -> List[Client]:
    """Фильтрует client_ids → только те, что менеджер имеет право менять.
    admin — все; остальные — только свои (manager_email == user.email)."""
    q = db.query(Client).filter(Client.id.in_(ids))
    if user.role != "admin":
        q = q.filter(Client.manager_email == user.email)
    return q.all()


@router.post("/api/clients/bulk/mark-checkup")
async def bulk_mark_checkup(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Массово отмечает «Чекап проведён сегодня» для выбранных клиентов."""
    u = _user(auth_token, db)
    data = await request.json()
    ids = data.get("client_ids") or []
    note = (data.get("note") or "").strip()
    if not ids:
        raise HTTPException(400, "client_ids required")

    clients = _accessible_clients(db, u, [int(x) for x in ids])
    now = datetime.utcnow()
    touched = 0
    for c in clients:
        c.last_checkup = now
        c.needs_checkup = False
        # CheckUp entry (completed)
        db.add(CheckUp(
            client_id=c.id,
            type="manual",
            status="completed",
            scheduled_date=now,
            completed_date=now,
        ))
        touched += 1
        # PartnerLog
        try:
            from routers.partner_logs import log_event
            log_event(db, client_id=c.id,
                      event_type="checkup_marked_bulk",
                      title="Чекап отмечен как проведённый (bulk)",
                      body=note or None,
                      payload={"marked_by": u.email},
                      source="bulk", created_by=u.email)
        except Exception as e:
            logger.warning(f"bulk checkup log failed for client={c.id}: {e}")
    db.commit()
    return {"ok": True, "touched": touched, "requested": len(ids)}


@router.post("/api/clients/bulk/start-onboarding")
async def bulk_start_onboarding(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Массово запускает онбординг-чеклист для клиентов (создаёт ClientOnboardingStatus
    если его нет). Детали этапов — в routers/onboarding."""
    u = _user(auth_token, db)
    data = await request.json()
    ids = data.get("client_ids") or []
    if not ids:
        raise HTTPException(400, "client_ids required")

    from datetime import date as _date
    from models import ClientOnboardingProgress
    clients = _accessible_clients(db, u, [int(x) for x in ids])
    started = 0
    skipped: List[int] = []
    for c in clients:
        existing = (db.query(ClientOnboardingProgress)
                      .filter(ClientOnboardingProgress.client_id == c.id,
                              ClientOnboardingProgress.completed_at.is_(None))
                      .first())
        if existing:
            skipped.append(c.id)
            continue
        prog = ClientOnboardingProgress(
            client_id=c.id,
            started_by=u.email,
            current_step=0,
            next_send_date=_date.today(),
        )
        db.add(prog)
        started += 1
        try:
            from routers.partner_logs import log_event
            log_event(db, client_id=c.id,
                      event_type="onboarding_started_bulk",
                      title="Онбординг запущен (bulk)",
                      payload={"started_by": u.email},
                      source="bulk", created_by=u.email)
        except Exception:
            pass
    db.commit()
    return {"ok": True, "started": started, "skipped": skipped,
            "requested": len(ids)}


@router.post("/api/clients/bulk/transfer")
async def bulk_transfer(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Массово создаёт запросы на передачу клиентов новому менеджеру.
    Для каждого клиента используется тот же flow что и в client_transfer — но без AI
    (manual_note только), чтобы не сжигать квоту. Новый менеджер всё равно должен accept.
    Если на клиента уже есть pending — пропускаем."""
    u = _user(auth_token, db)
    data = await request.json()
    ids = data.get("client_ids") or []
    to_user_id = data.get("to_user_id")
    manual_note = (data.get("manual_note") or "").strip() or None
    if not ids or not to_user_id:
        raise HTTPException(400, "client_ids and to_user_id required")

    to_u = db.query(User).filter(User.id == int(to_user_id),
                                  User.is_active == True).first()
    if not to_u:
        raise HTTPException(404, "target manager not found")
    if to_u.id == u.id:
        raise HTTPException(400, "cannot transfer to yourself")

    clients = _accessible_clients(db, u, [int(x) for x in ids])
    created = 0
    skipped: List[int] = []
    try:
        from models import ClientTransferRequest
    except Exception:
        raise HTTPException(500, "transfer model not available")

    for c in clients:
        existing = (db.query(ClientTransferRequest)
                      .filter(ClientTransferRequest.client_id == c.id,
                              ClientTransferRequest.status == "pending")
                      .first())
        if existing:
            skipped.append(c.id)
            continue
        tr = ClientTransferRequest(
            client_id=c.id,
            from_user_id=u.id,
            to_user_id=to_u.id,
            ai_summary=None,  # bulk — без AI
            manual_note=manual_note or "(передача в рамках массового действия)",
            status="pending",
        )
        db.add(tr)
        created += 1
    db.commit()
    return {"ok": True, "created": created, "skipped": skipped,
            "to_email": to_u.email}
