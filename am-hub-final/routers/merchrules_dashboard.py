"""
API синков и чтения Merchrules-дашборда:
  POST /api/clients/{id}/merchrules-dashboard/sync — запустить синк (использует креды текущего юзера)
  GET  /api/clients/{id}/merchrules-dashboard       — вернуть все 4 сущности из локальной БД
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import (
    Client, User,
    ClientSynonym, ClientWhitelistEntry, ClientBlacklistEntry, ClientMerchRule,
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


def _user_creds(user: User) -> tuple:
    settings = user.settings or {}
    mr = settings.get("merchrules", {})
    login = mr.get("login") or os.environ.get("MERCHRULES_LOGIN")
    try:
        from crypto import dec as _dec
        password = _dec(mr.get("password", "")) or os.environ.get("MERCHRULES_PASSWORD")
    except Exception:
        password = mr.get("password") or os.environ.get("MERCHRULES_PASSWORD")
    return login, password


@router.post("/api/clients/{client_id}/merchrules-dashboard/sync")
async def sync_dashboard(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "client not found")
    if not c.merchrules_account_id:
        raise HTTPException(400, "client has no merchrules_account_id")

    login, password = _user_creds(user)
    if not login or not password:
        raise HTTPException(400, "Merchrules credentials not configured")

    try:
        from integrations.merchrules_dashboard import sync_client
        counts = await sync_client(db, c, login, password)
    except Exception as e:
        logger.error(f"merchrules-dashboard sync failed for client {client_id}: {e}")
        raise HTTPException(500, f"sync failed: {e}")
    return {"ok": True, "counts": counts}


@router.get("/api/clients/{client_id}/merchrules-dashboard")
async def get_dashboard(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404)

    syns = db.query(ClientSynonym).filter(ClientSynonym.client_id == client_id).all()
    wl = db.query(ClientWhitelistEntry).filter(ClientWhitelistEntry.client_id == client_id).all()
    bl = db.query(ClientBlacklistEntry).filter(ClientBlacklistEntry.client_id == client_id).all()
    rules = db.query(ClientMerchRule).filter(ClientMerchRule.client_id == client_id).all()

    return {
        "synonyms": [{
            "id": s.id, "merchrules_id": s.merchrules_id,
            "term": s.term, "synonyms": s.synonyms or [],
            "is_active": s.is_active,
            "last_synced": s.last_synced.isoformat() if s.last_synced else None,
        } for s in syns],
        "whitelist": [{
            "id": w.id, "merchrules_id": w.merchrules_id,
            "query": w.query, "product_id": w.product_id,
            "product_name": w.product_name, "position": w.position,
            "is_active": w.is_active,
            "last_synced": w.last_synced.isoformat() if w.last_synced else None,
        } for w in wl],
        "blacklist": [{
            "id": b.id, "merchrules_id": b.merchrules_id,
            "query": b.query, "product_id": b.product_id,
            "product_name": b.product_name,
            "is_active": b.is_active,
            "last_synced": b.last_synced.isoformat() if b.last_synced else None,
        } for b in bl],
        "merch_rules": [{
            "id": r.id, "merchrules_id": r.merchrules_id,
            "name": r.name, "rule_type": r.rule_type,
            "status": r.status, "priority": r.priority,
            "config": r.config or {},
            "last_synced": r.last_synced.isoformat() if r.last_synced else None,
        } for r in rules],
    }


from datetime import datetime
from fastapi import Request

@router.post("/api/clients/{client_id}/merch-rules/draft")
async def create_draft_rule(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Создаёт локальный draft ClientMerchRule из чекап-запроса.
    Не пушит в Merchrules (там настройка требует промо-креативов и таргета —
    это делается в Merchrules UI). Здесь — задача-напоминание + заготовка.

    Body: {query: str, source_checkup_result_id?: int, rule_type?: str, note?: str}
    """
    u = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404)
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query required")
    rule_type = body.get("rule_type") or "checkup_followup"
    note = body.get("note") or ""
    checkup_result_id = body.get("source_checkup_result_id")

    rule = ClientMerchRule(
        client_id=client_id,
        merchrules_id=None,  # ещё не запушено
        name=f"Правило для «{query[:80]}»",
        rule_type=rule_type,
        status="draft",
        priority=0,
        config={
            "query": query,
            "source": "checkup",
            "checkup_result_id": checkup_result_id,
            "note": note,
            "created_by": u.email,
        },
        updated_at=datetime.utcnow(),
    )
    db.add(rule)
    db.flush()

    # PartnerLog
    try:
        from routers.partner_logs import log_event
        log_event(db, client_id=client_id,
                  event_type="merch_rule_drafted",
                  title=f"Создано draft-правило: «{query[:60]}»",
                  body=f"Источник: чекап. Заметка: {note[:200]}" if note else "Источник: чекап.",
                  payload={"rule_id": rule.id, "query": query,
                           "checkup_result_id": checkup_result_id},
                  source="checkup", created_by=u.email)
    except Exception as e:
        logger.warning(f"PartnerLog merch_rule_drafted failed: {e}")

    # Task для менеджера — дойти до Merchrules и настроить
    try:
        from models import Task
        t = Task(
            client_id=client_id,
            title=f"Настроить правило в Merchrules: «{query[:80]}»",
            description=f"Создано из чекапа. {note}" if note else "Создано из чекапа.",
            status="plan", priority="medium",
            source="checkup",
            meta={"rule_id": rule.id, "query": query,
                  "checkup_result_id": checkup_result_id},
        )
        db.add(t)
    except Exception as e:
        logger.warning(f"Task create for rule failed: {e}")

    db.commit()
    db.refresh(rule)
    return {
        "ok": True,
        "rule": {
            "id": rule.id, "name": rule.name, "rule_type": rule.rule_type,
            "status": rule.status, "config": rule.config,
        },
    }
