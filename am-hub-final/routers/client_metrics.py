"""
Client metrics — полный дашборд метрик одного клиента для карточки клиента
(таб «Метрики» / внешняя страница /design/client/{id}/metrics).

Возвращает:
  - MRR история (revenue_history): [{period, mrr}]
  - Health snapshots: [{date, score}]
  - NPS история: [{date, score, comment}]
  - Tasks stats: total/open/overdue/done (за 90 дней)
  - Meetings stats: total_90d, upcoming, by_type
  - Tickets: open_count, avg_resolution_days
  - Upsell events: суммы выигранных / потерянных
  - Checkup activity: последние 10 чекапов (type, date, avg_score)
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from auth import decode_access_token
from models import (
    Client, User, Task, Meeting, CheckUp,
    RevenueEntry, HealthSnapshot, NPSEntry, UpsellEvent,
    CheckupResult,
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


@router.get("/api/clients/{client_id}/metrics")
async def client_metrics(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404)

    now = datetime.utcnow()
    since_90 = now - timedelta(days=90)

    # ── Revenue history (последние 12 мес) ────────────────────────────────
    revenue = []
    try:
        entries = (db.query(RevenueEntry)
                     .filter(RevenueEntry.client_id == client_id)
                     .order_by(RevenueEntry.period).all())
        revenue = [{"period": e.period, "mrr": float(e.mrr or 0),
                    "arr": float(e.arr or 0) or float(e.mrr or 0) * 12,
                    "note": e.note}
                   for e in entries[-12:]]
    except Exception:
        revenue = []

    # Fallback если пуст: 1 снапшот из Client.mrr на текущий месяц
    if not revenue and c.mrr:
        revenue = [{
            "period": now.strftime("%Y-%m"),
            "mrr": float(c.mrr or 0),
            "arr": float(c.mrr or 0) * 12,
            "note": "Из Client.mrr (нет RevenueEntry)",
        }]

    # ── Health история ────────────────────────────────────────────────────
    health_history = []
    try:
        snaps = (db.query(HealthSnapshot)
                   .filter(HealthSnapshot.client_id == client_id)
                   .order_by(HealthSnapshot.calculated_at.desc()).limit(30).all())
        health_history = [{
            "date": s.calculated_at.isoformat() if s.calculated_at else None,
            "score": float(s.score or 0),
        } for s in snaps[::-1]]
    except Exception:
        health_history = []
    if not health_history and c.health_score is not None:
        health_history = [{"date": now.isoformat(), "score": float(c.health_score or 0)}]

    # ── NPS история ───────────────────────────────────────────────────────
    nps_history = []
    try:
        entries = (db.query(NPSEntry)
                     .filter(NPSEntry.client_id == client_id)
                     .order_by(NPSEntry.recorded_at.desc()).limit(20).all())
        nps_history = [{
            "date": e.recorded_at.isoformat() if e.recorded_at else None,
            "score": e.score,
            "comment": (e.comment or "")[:200] if hasattr(e, "comment") else None,
        } for e in entries[::-1]]
    except Exception:
        nps_history = []
    if not nps_history and c.nps_last is not None:
        nps_history = [{
            "date": c.nps_date.isoformat() if c.nps_date else now.isoformat(),
            "score": c.nps_last,
            "comment": None,
        }]

    # ── Tasks stats ──────────────────────────────────────────────────────
    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    t_total = len(tasks)
    t_open = sum(1 for t in tasks if t.status != "done")
    t_overdue = sum(1 for t in tasks
                    if t.status != "done" and t.due_date and t.due_date < now)
    t_done_90 = sum(1 for t in tasks
                    if t.status == "done" and t.confirmed_at and t.confirmed_at >= since_90)

    # ── Meetings stats ───────────────────────────────────────────────────
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).all()
    m_total_90 = sum(1 for m in meetings if m.date and m.date >= since_90)
    m_upcoming = sum(1 for m in meetings if m.date and m.date > now)
    m_by_type: dict = {}
    for m in meetings:
        if m.date and m.date >= since_90:
            t = m.type or "other"
            m_by_type[t] = m_by_type.get(t, 0) + 1

    # ── Tickets ──────────────────────────────────────────────────────────
    open_tickets = c.open_tickets or 0
    last_ticket_days_ago = None
    if c.last_ticket_date:
        last_ticket_days_ago = (now - c.last_ticket_date).days

    # ── Upsell events ────────────────────────────────────────────────────
    upsell_won = upsell_lost = upsell_active = 0
    upsell_delta_won = 0.0
    try:
        ue = db.query(UpsellEvent).filter(UpsellEvent.client_id == client_id).all()
        for e in ue:
            if e.status == "won":
                upsell_won += 1
                upsell_delta_won += float(e.delta or 0)
            elif e.status == "lost":
                upsell_lost += 1
            elif e.status in ("identified", "in_progress", "postponed"):
                upsell_active += 1
    except Exception:
        pass

    # ── Checkup activity ─────────────────────────────────────────────────
    checkups_recent = []
    try:
        crs = (db.query(CheckupResult)
                 .filter(CheckupResult.client_id == client_id)
                 .order_by(CheckupResult.created_at.desc()).limit(10).all())
        for cr in crs:
            checkups_recent.append({
                "id": cr.id,
                "date": cr.created_at.isoformat() if cr.created_at else None,
                "type": cr.query_type,
                "avg_score": float(cr.avg_score) if cr.avg_score is not None else None,
                "total": cr.total_queries,
            })
    except Exception:
        pass

    return {
        "client": {
            "id": c.id,
            "name": c.name,
            "segment": c.segment,
            "health_score": c.health_score,
            "mrr": c.mrr,
            "gmv": c.gmv,
            "contract_end": c.contract_end.isoformat() if c.contract_end else None,
        },
        "revenue_history": revenue,
        "health_history": health_history,
        "nps_history": nps_history,
        "tasks": {
            "total": t_total, "open": t_open,
            "overdue": t_overdue, "done_90d": t_done_90,
        },
        "meetings": {
            "total_90d": m_total_90, "upcoming": m_upcoming,
            "by_type": m_by_type,
        },
        "tickets": {
            "open": open_tickets,
            "last_days_ago": last_ticket_days_ago,
        },
        "upsell": {
            "won": upsell_won, "lost": upsell_lost, "active": upsell_active,
            "delta_won": upsell_delta_won,
        },
        "checkups_recent": checkups_recent,
    }


@router.post("/api/revenue-trend/update")
async def api_trigger_revenue_trend(auth_token: Optional[str] = Cookie(None),
                                     db: Session = Depends(get_db)):
    """Ручной запуск job_revenue_trend_update — полезно после синка MR/Airtable."""
    u = _user(auth_token, db)
    if u.role not in ("admin", "grouphead"):
        raise HTTPException(403)
    try:
        from scheduler import job_revenue_trend_update
        await job_revenue_trend_update()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))
