"""
Client transfer workflow — manager → manager через AI-сводку + accept.

Flow:
  1. Текущий manager жмёт «Передать» → /api/clients/{id}/transfer {to_user_id, manual_note?}
     Система собирает контекст → AI-драфт → возвращает request_id + ai_summary.
     Менеджер может отредактировать summary → /api/transfers/{id} (PATCH)
  2. Новый manager видит pending запрос в инбоксе + баннере на клиенте.
     Accept: client.manager_email → new, открытые задачи переназначаются.
     Decline: запрос закрывается с reason, старый manager получает notification.
     Cancel: инициатор может отозвать пока pending.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_

from database import get_db
from auth import decode_access_token
from models import (
    Client, ClientTransferRequest, Meeting, QBR, Task, User,
    ClientNote, PartnerLog, SupportTicket,
)

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


def _build_client_context(db: Session, client: Client) -> str:
    """Контекст клиента для AI-сводки."""
    now = datetime.utcnow()
    lines = [
        f"Клиент: {client.name} (id={client.id})",
        f"Сегмент: {client.segment or '—'}",
        f"Домен: {client.domain or '—'}",
        f"Health score: {int((client.health_score or 0) * 100)}%",
        f"MRR: {int(client.mrr or 0)} ₽",
        f"Последний контакт: {client.last_meeting_date.strftime('%d.%m.%Y') if client.last_meeting_date else '—'}",
        f"Последний QBR: {client.last_qbr_date.strftime('%d.%m.%Y') if client.last_qbr_date else '—'}",
        f"Контракт до: {client.contract_end.isoformat() if client.contract_end else '—'}",
        f"Payment: {client.payment_status or 'active'}",
    ]

    # Топ-10 открытых задач
    open_tasks = (db.query(Task)
                    .filter(Task.client_id == client.id,
                            Task.status.in_(["plan", "in_progress", "blocked"]))
                    .order_by(Task.priority.desc(), Task.due_date)
                    .limit(10).all())
    if open_tasks:
        lines.append("\nОткрытые задачи:")
        for t in open_tasks:
            due = t.due_date.strftime("%d.%m") if t.due_date else "без срока"
            lines.append(f"- [{t.status}/{t.priority}] {t.title} (due {due})")

    # Встречи за 60 дней
    meetings = (db.query(Meeting)
                  .filter(Meeting.client_id == client.id)
                  .order_by(Meeting.date.desc()).limit(5).all())
    if meetings:
        lines.append("\nПоследние встречи:")
        for m in meetings:
            d = m.date.strftime("%d.%m.%Y") if m.date else "—"
            summary = (m.summary or m.title or m.type or "")[:120]
            lines.append(f"- {d} [{m.type or 'meeting'}] {summary}")

    # Закреплённые заметки
    notes = (db.query(ClientNote)
               .filter(ClientNote.client_id == client.id)
               .order_by(ClientNote.is_pinned.desc(), ClientNote.updated_at.desc())
               .limit(5).all())
    if notes:
        lines.append("\nЗаметки:")
        for n in notes:
            pin = "📌 " if n.is_pinned else ""
            lines.append(f"- {pin}{(n.content or '')[:150]}")

    # Открытые тикеты
    try:
        tickets = (db.query(SupportTicket)
                     .filter(SupportTicket.client_id == client.id,
                             SupportTicket.status.in_(["open", "in_progress"]))
                     .limit(5).all())
        if tickets:
            lines.append(f"\nОткрытые тикеты: {len(tickets)}")
    except Exception:
        pass

    # Последние события partner log
    try:
        logs = (db.query(PartnerLog)
                  .filter(PartnerLog.client_id == client.id)
                  .order_by(PartnerLog.created_at.desc()).limit(5).all())
        if logs:
            lines.append("\nПоследние события:")
            for l in logs:
                d = l.created_at.strftime("%d.%m") if l.created_at else "—"
                lines.append(f"- {d} [{l.event_type}] {(l.title or '')[:80]}")
    except Exception:
        pass

    return "\n".join(lines)


async def _generate_ai_summary(client: Client, context: str, db: Session) -> str:
    """AI-драфт сводки для передающего менеджера. Fallback — сам контекст."""
    try:
        from ai_assistant import _chat_sync, DOMAIN_CONTEXT
        prompt = (
            "Ты — помощник AM-менеджеров AnyQuery. Передаётся клиент новому менеджеру.\n"
            "Сформируй краткую сводку для него (3-5 абзацев, по-русски, по делу, без воды):\n"
            "1) исторический контекст и текущее состояние клиента (сегмент, MRR, health);\n"
            "2) что было сделано недавно (встречи, задачи, QBR);\n"
            "3) открытые вопросы и риски (что требует внимания);\n"
            "4) первоочередные действия на ближайшие 2 недели.\n\n"
            f"Данные клиента:\n{context}"
        )
        text = _chat_sync(DOMAIN_CONTEXT, prompt, max_tokens=900)
        if text and text.strip():
            return text.strip()
    except Exception as e:
        logger.warning(f"AI transfer summary fallback to raw context: {e}")
    return "AI-драфт недоступен. Сводка собрана из данных клиента:\n\n" + context


def _serialize(tr: ClientTransferRequest, db: Session) -> dict:
    client = db.query(Client).filter(Client.id == tr.client_id).first()
    from_u = db.query(User).filter(User.id == tr.from_user_id).first()
    to_u = db.query(User).filter(User.id == tr.to_user_id).first()
    return {
        "id": tr.id,
        "client_id": tr.client_id,
        "client_name": client.name if client else None,
        "from_user_id": tr.from_user_id,
        "from_email": from_u.email if from_u else None,
        "to_user_id": tr.to_user_id,
        "to_email": to_u.email if to_u else None,
        "ai_summary": tr.ai_summary,
        "manual_note": tr.manual_note,
        "status": tr.status,
        "decline_reason": tr.decline_reason,
        "created_at": tr.created_at.isoformat() if tr.created_at else None,
        "resolved_at": tr.resolved_at.isoformat() if tr.resolved_at else None,
    }


@router.post("/api/clients/{client_id}/transfer")
async def transfer_create(
    client_id: int, request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Инициировать передачу клиента. Генерирует AI-драфт."""
    u = _user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "client not found")
    # Только текущий manager_email или admin
    if client.manager_email != u.email and u.role != "admin":
        raise HTTPException(403, "only current manager or admin can initiate transfer")

    data = await request.json()
    to_user_id = data.get("to_user_id")
    if not to_user_id:
        raise HTTPException(400, "to_user_id required")
    to_u = db.query(User).filter(User.id == int(to_user_id),
                                  User.is_active == True).first()
    if not to_u:
        raise HTTPException(404, "target manager not found")
    if to_u.id == u.id:
        raise HTTPException(400, "cannot transfer to yourself")
    manual_note = (data.get("manual_note") or "").strip() or None

    # Блокируем повторный pending на одного и того же клиента
    existing = (db.query(ClientTransferRequest)
                  .filter(ClientTransferRequest.client_id == client_id,
                          ClientTransferRequest.status == "pending")
                  .first())
    if existing:
        raise HTTPException(409, f"already pending (id={existing.id})")

    # Контекст + AI
    context = _build_client_context(db, client)
    ai_summary = await _generate_ai_summary(client, context, db)

    tr = ClientTransferRequest(
        client_id=client_id,
        from_user_id=u.id,
        to_user_id=to_u.id,
        ai_summary=ai_summary,
        manual_note=manual_note,
        status="pending",
    )
    db.add(tr)
    db.flush()

    # Уведомление получателю
    try:
        from tg_notifications import notify_manager
        await notify_manager(db, to_u, "nps_incoming" if False else "meeting_soon",
            {}, related_type="transfer", related_id=tr.id)
    except Exception:
        pass
    # Используем Notification напрямую для кастомного текста передачи
    try:
        from models import Notification
        msg = f"Клиент: {client.name}. От: {u.email}."
        if manual_note:
            msg += f"\nЗаметка: {manual_note[:200]}"
        n = Notification(
            user_id=to_u.id,
            title="🤝 Входящий запрос на передачу клиента",
            message=msg,
            type="info",
            kind="client_transfer",
            related_resource_type="transfer",
            related_resource_id=tr.id,
            is_read=False,
            created_at=datetime.utcnow(),
        )
        db.add(n)
    except Exception as e:
        logger.warning(f"transfer notification skipped: {e}")

    db.commit()
    db.refresh(tr)
    return _serialize(tr, db)


@router.patch("/api/transfers/{tid}")
async def transfer_update(
    tid: int, request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Редактирование AI-сводки (только инициатором пока pending)."""
    u = _user(auth_token, db)
    tr = db.query(ClientTransferRequest).filter(ClientTransferRequest.id == tid).first()
    if not tr:
        raise HTTPException(404)
    if tr.from_user_id != u.id:
        raise HTTPException(403, "only initiator can edit")
    if tr.status != "pending":
        raise HTTPException(400, "already resolved")
    data = await request.json()
    if "ai_summary" in data:
        tr.ai_summary = data["ai_summary"]
    if "manual_note" in data:
        tr.manual_note = data["manual_note"]
    db.commit()
    return _serialize(tr, db)


@router.get("/api/transfers")
async def transfers_list(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    filter: Optional[str] = None,   # incoming | outgoing | all (default)
    status: Optional[str] = None,   # pending | accepted | declined | cancelled
):
    u = _user(auth_token, db)
    q = db.query(ClientTransferRequest)
    if filter == "incoming":
        q = q.filter(ClientTransferRequest.to_user_id == u.id)
    elif filter == "outgoing":
        q = q.filter(ClientTransferRequest.from_user_id == u.id)
    else:
        q = q.filter(or_(
            ClientTransferRequest.from_user_id == u.id,
            ClientTransferRequest.to_user_id == u.id,
        ))
    if status:
        q = q.filter(ClientTransferRequest.status == status)
    rows = q.order_by(ClientTransferRequest.created_at.desc()).limit(200).all()
    return {"items": [_serialize(r, db) for r in rows]}


@router.get("/api/clients/{client_id}/transfer")
async def transfer_for_client(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Активный pending-запрос по клиенту (используется для баннера на карточке)."""
    _user(auth_token, db)
    tr = (db.query(ClientTransferRequest)
            .filter(ClientTransferRequest.client_id == client_id,
                    ClientTransferRequest.status == "pending")
            .first())
    return _serialize(tr, db) if tr else None


@router.post("/api/transfers/{tid}/accept")
async def transfer_accept(
    tid: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Принять передачу — меняет manager_email + переназначает открытые задачи."""
    u = _user(auth_token, db)
    tr = db.query(ClientTransferRequest).filter(ClientTransferRequest.id == tid).first()
    if not tr:
        raise HTTPException(404)
    if tr.status != "pending":
        raise HTTPException(400, "already resolved")
    if tr.to_user_id != u.id:
        raise HTTPException(403, "only target manager can accept")

    client = db.query(Client).filter(Client.id == tr.client_id).first()
    if not client:
        raise HTTPException(404, "client not found")

    old_email = client.manager_email
    client.manager_email = u.email
    tr.status = "accepted"
    tr.resolved_at = datetime.utcnow()
    tr.resolved_by = u.id

    # Переназначаем открытые задачи (плашку не меняет — только manager_email клиента,
    # task_id напрямую не связан с user; у нас manager_email хранится в клиенте)
    open_tasks = (db.query(Task)
                    .filter(Task.client_id == client.id,
                            Task.status.in_(["plan", "in_progress", "blocked"]))
                    .all())
    reassigned = len(open_tasks)

    # PartnerLog
    try:
        from routers.partner_logs import log_event
        from_u = db.query(User).filter(User.id == tr.from_user_id).first()
        log_event(db, client_id=client.id,
                  event_type="client_transferred",
                  title=f"Клиент передан: {(from_u.email if from_u else '—')} → {u.email}",
                  body=f"Переназначено задач: {reassigned}. AI-сводка сохранена в ClientTransferRequest #{tr.id}.",
                  payload={"from": from_u.email if from_u else None, "to": u.email,
                           "tasks_reassigned": reassigned, "transfer_id": tr.id},
                  source="transfer",
                  created_by=u.email)
    except Exception as e:
        logger.warning(f"PartnerLog client_transferred failed: {e}")

    # Уведомить старого менеджера
    try:
        from_u = db.query(User).filter(User.id == tr.from_user_id).first()
        if from_u:
            from models import Notification
            n = Notification(
                user_id=from_u.id,
                title="✅ Передача клиента принята",
                message=f"{u.email} принял {client.name}.",
                type="success",
                kind="client_transfer",
                related_resource_type="client",
                related_resource_id=client.id,
                is_read=False,
                created_at=datetime.utcnow(),
            )
            db.add(n)
    except Exception:
        pass

    db.commit()
    return {"ok": True, "tasks_reassigned": reassigned, "old_manager": old_email,
            "new_manager": u.email}


@router.post("/api/transfers/{tid}/decline")
async def transfer_decline(
    tid: int, request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    tr = db.query(ClientTransferRequest).filter(ClientTransferRequest.id == tid).first()
    if not tr:
        raise HTTPException(404)
    if tr.status != "pending":
        raise HTTPException(400, "already resolved")
    if tr.to_user_id != u.id:
        raise HTTPException(403)
    try:
        data = await request.json()
    except Exception:
        data = {}
    tr.status = "declined"
    tr.resolved_at = datetime.utcnow()
    tr.resolved_by = u.id
    tr.decline_reason = (data.get("reason") or "").strip() or None

    try:
        from models import Notification
        from_u = db.query(User).filter(User.id == tr.from_user_id).first()
        if from_u:
            n = Notification(
                user_id=from_u.id,
                title="❌ Передача клиента отклонена",
                message=f"{u.email}: {(tr.decline_reason or 'без причины')}",
                type="warning",
                kind="client_transfer",
                related_resource_type="transfer",
                related_resource_id=tr.id,
                is_read=False,
                created_at=datetime.utcnow(),
            )
            db.add(n)
    except Exception:
        pass
    db.commit()
    return {"ok": True}


@router.post("/api/transfers/{tid}/cancel")
async def transfer_cancel(
    tid: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Инициатор отзывает pending-запрос."""
    u = _user(auth_token, db)
    tr = db.query(ClientTransferRequest).filter(ClientTransferRequest.id == tid).first()
    if not tr:
        raise HTTPException(404)
    if tr.status != "pending":
        raise HTTPException(400, "already resolved")
    if tr.from_user_id != u.id and u.role != "admin":
        raise HTTPException(403)
    tr.status = "cancelled"
    tr.resolved_at = datetime.utcnow()
    tr.resolved_by = u.id
    db.commit()
    return {"ok": True}
