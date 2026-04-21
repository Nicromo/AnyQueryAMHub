"""
Оплаты — клиенты с неоплаченными / просроченными счетами.

Критерии «ещё не оплатил»:
  - payment_status != 'active' (любое: overdue/pending/unpaid/failed/trial/...)
  - ИЛИ payment_due_date < today (просрочено даже если status='active')
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_

from database import get_db
from auth import decode_access_token
from models import Client, User

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


def _bucket(days_from_today: Optional[int], status: str) -> str:
    s = (status or "").lower()
    if s in ("overdue", "unpaid", "failed"):
        return "overdue"
    if days_from_today is None:
        return "no_date"
    if days_from_today < 0:
        return "overdue"
    if days_from_today == 0:
        return "today"
    if days_from_today <= 7:
        return "week"
    return "later"


@router.get("/api/me/payments-pending")
async def payments_pending(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    am_scope: Optional[str] = Cookie(None),
):
    """Клиенты менеджера с неоплаченными / просроченными счетами.
    Scope-aware (mine/group/all). Группировка по бакетам срочности."""
    u = _user(auth_token, db)

    try:
        from scope import resolve_scope, get_manager_emails_for_scope
        active_scope = resolve_scope(u, am_scope)
        emails = get_manager_emails_for_scope(db, u, active_scope)
    except Exception:
        emails = [u.email] if u.role == "manager" else None

    today = date.today()
    today_dt = datetime.combine(today, datetime.min.time())

    q = db.query(Client).filter(
        or_(
            Client.payment_status.isnot(None) & (Client.payment_status != "active"),
            Client.payment_due_date.isnot(None) & (Client.payment_due_date < today_dt),
        )
    )
    if emails is not None:
        q = q.filter(Client.manager_email.in_(emails))
    clients = q.order_by(Client.payment_due_date.asc().nulls_last()).all()

    columns = {
        "overdue":  {"label": "Просрочено",     "tone": "critical", "items": []},
        "today":    {"label": "Сегодня",        "tone": "warn",     "items": []},
        "week":     {"label": "На неделе",      "tone": "warn",     "items": []},
        "later":    {"label": "Позже",          "tone": "info",     "items": []},
        "no_date":  {"label": "Без даты",       "tone": "neutral",  "items": []},
    }
    total_unpaid = 0.0
    for c in clients:
        pdd = c.payment_due_date.date() if isinstance(c.payment_due_date, datetime) else c.payment_due_date
        days = (pdd - today).days if pdd else None
        bucket = _bucket(days, c.payment_status)
        item = {
            "id": c.id,
            "name": c.name,
            "segment": c.segment,
            "manager_email": c.manager_email,
            "payment_status": c.payment_status or "—",
            "payment_due_date": pdd.isoformat() if pdd else None,
            "payment_amount": c.payment_amount or 0,
            "days_from_today": days,
            "mrr": c.mrr or 0,
        }
        columns[bucket]["items"].append(item)
        total_unpaid += (c.payment_amount or 0)
    totals = {k: len(v["items"]) for k, v in columns.items()}
    return {
        "columns": columns,
        "totals": totals,
        "total_clients": sum(totals.values()),
        "total_unpaid_amount": total_unpaid,
    }
