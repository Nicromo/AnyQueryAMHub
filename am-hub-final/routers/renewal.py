"""
Renewal pipeline — колонки по срокам contract_end.
  90+ дней / 30-90 / 14-30 / 7-14 / <7 (красная зона) / overdue.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

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


def _bucket(days: int) -> str:
    if days < 0:
        return "overdue"
    if days < 7:
        return "critical"
    if days < 14:
        return "week"
    if days < 30:
        return "month"
    if days < 90:
        return "quarter"
    return "later"


@router.get("/api/me/renewal-pipeline")
async def renewal_pipeline(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    am_scope: Optional[str] = Cookie(None),
):
    """Пайплайн по клиентам с contract_end. Группировка по срокам."""
    u = _user(auth_token, db)

    from scope import resolve_scope, get_manager_emails_for_scope
    active = resolve_scope(u, am_scope)
    emails = get_manager_emails_for_scope(db, u, active)

    q = db.query(Client).filter(Client.contract_end.isnot(None))
    if emails is not None:
        q = q.filter(Client.manager_email.in_(emails))
    clients = q.order_by(Client.contract_end).all()

    today = date.today()
    columns = {
        "overdue":  {"label": "Просрочено",          "tone": "critical", "items": []},
        "critical": {"label": "<7 дней",             "tone": "critical", "items": []},
        "week":     {"label": "7-14 дней",           "tone": "warn",     "items": []},
        "month":    {"label": "14-30 дней",          "tone": "warn",     "items": []},
        "quarter":  {"label": "30-90 дней",          "tone": "info",     "items": []},
        "later":    {"label": "90+ дней",            "tone": "ok",       "items": []},
    }
    for c in clients:
        days = (c.contract_end - today).days
        bucket = _bucket(days)
        columns[bucket]["items"].append({
            "id": c.id,
            "name": c.name,
            "segment": c.segment,
            "mrr": c.mrr,
            "health": int((c.health_score or 0) * 100),
            "contract_end": c.contract_end.isoformat(),
            "days_left": days,
            "manager_email": c.manager_email,
        })
    # Сортируем каждую колонку по days_left
    for col in columns.values():
        col["items"].sort(key=lambda x: x["days_left"])
    totals = {k: len(v["items"]) for k, v in columns.items()}
    total_mrr = sum(item["mrr"] or 0 for col in columns.values() for item in col["items"])
    return {
        "columns": columns,
        "totals": totals,
        "total_clients": sum(totals.values()),
        "total_mrr": total_mrr,
    }
