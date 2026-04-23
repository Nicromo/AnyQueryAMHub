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


# ============================================================================
# Merchrules-powered endpoints — дергают внешние API с кредами менеджера
# ============================================================================
# Все 3 эндпоинта защищены cookie-JWT через _user() и требуют, чтобы у
# менеджера в user.settings.merchrules были валидные login/password.
# Если кредов нет или Merchrules недоступен — отдаём пустой ответ
# с флагом ok=false + reason, фронт показывает понятную заглушку.

def _merchrules_creds(user: User) -> tuple[str, str]:
    s = (user.settings or {}).get("merchrules", {}) or {}
    return (s.get("login") or "", s.get("password") or "")


@router.get("/api/clients/{client_id}/gmv-daily")
async def client_gmv_daily(
    client_id: int,
    days: int = 30,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Реальный дневной GMV/sessions/orders из Merchrules /api/report/daily.
    Заменяет локальный RevenueEntry-sparkline (который помесячный) на честный
    дневной timeseries — это та самая user-facing разница для sparkline GMV,
    про которую я упоминал в «что ещё осталось»."""
    user = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404)
    if not c.merchrules_account_id:
        return {"ok": False, "reason": "no_site_id", "items": []}
    login, password = _merchrules_creds(user)
    if not login or not password:
        return {"ok": False, "reason": "no_credentials", "items": []}

    from merchrules_sync import fetch_report_daily
    date_to = datetime.utcnow().date()
    date_from = date_to - timedelta(days=max(1, min(days, 180)))
    try:
        data = await fetch_report_daily(
            site_id=str(c.merchrules_account_id),
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            names=["REVENUE_TOTAL", "SESSIONS_TOTAL", "ORDERS_TOTAL"],
            login=login, password=password,
        )
    except Exception as e:
        logger.warning("fetch_report_daily failed for client %s: %s", client_id, e)
        return {"ok": False, "reason": "merchrules_error", "error": str(e), "items": []}

    # Merchrules возвращает разные формы — нормализуем в [{date, revenue, sessions, orders}]
    items_raw = data.get("items") or data.get("data") or data.get("rows") or []
    items: list = []
    if isinstance(items_raw, list):
        for row in items_raw:
            if not isinstance(row, dict):
                continue
            date = row.get("date") or row.get("day") or row.get("period") or ""
            items.append({
                "date": date,
                "revenue":  float(row.get("REVENUE_TOTAL")  or row.get("revenue")  or 0),
                "sessions": float(row.get("SESSIONS_TOTAL") or row.get("sessions") or 0),
                "orders":   float(row.get("ORDERS_TOTAL")   or row.get("orders")   or 0),
            })
    # Отсортируем по дате (если есть)
    items.sort(key=lambda x: x.get("date") or "")
    total_rev = sum(i["revenue"] for i in items)
    return {"ok": True, "items": items, "total_revenue": total_rev,
            "days": days, "site_id": c.merchrules_account_id}


@router.get("/api/clients/{client_id}/merchrules-health")
async def client_merchrules_health(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Здоровье Merchrules + активные инциденты, отфильтрованные под site_id клиента.
    Инциденты фильтруются по affected_sites (если Merchrules вернул это поле) —
    иначе показываем все инциденты менеджера (лучше false-positive, чем тишина)."""
    user = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404)
    login, password = _merchrules_creds(user)
    if not login or not password:
        return {"ok": False, "reason": "no_credentials", "health": None, "incidents": []}

    from merchrules_sync import fetch_health_dashboard, fetch_incidents
    health_data: dict = {}
    incidents_raw: list = []
    try:
        health_data = await fetch_health_dashboard(login=login, password=password) or {}
    except Exception as e:
        logger.warning("fetch_health_dashboard failed: %s", e)
    try:
        inc = await fetch_incidents(login=login, password=password) or {}
        incidents_raw = inc.get("items") or inc.get("data") or inc.get("incidents") or []
    except Exception as e:
        logger.warning("fetch_incidents failed: %s", e)

    # Нормализация health
    health_pct: Optional[float] = None
    if isinstance(health_data, dict):
        # Merchrules может возвращать {health: 0.87} или {score: 87} или {status: 'ok'}
        hv = health_data.get("health") or health_data.get("score") or health_data.get("value")
        if isinstance(hv, (int, float)):
            health_pct = float(hv) * 100 if hv <= 1 else float(hv)

    # Фильтруем инциденты: только те, где задет site_id клиента, либо глобальные
    site_id = str(c.merchrules_account_id or "")
    filtered_incidents: list = []
    if isinstance(incidents_raw, list):
        for inc in incidents_raw:
            if not isinstance(inc, dict):
                continue
            affected = inc.get("affected_sites") or inc.get("sites") or inc.get("site_ids") or []
            if isinstance(affected, str):
                affected = [affected]
            affected_strs = [str(x) for x in (affected or [])]
            # Если incident без affected — считаем глобальным (показываем всем клиентам).
            is_relevant = (not affected_strs) or (site_id and site_id in affected_strs)
            if not is_relevant:
                continue
            filtered_incidents.append({
                "id":        inc.get("id") or inc.get("uuid") or "",
                "title":     inc.get("title") or inc.get("name") or inc.get("summary") or "—",
                "severity":  inc.get("severity") or inc.get("priority") or "info",
                "status":    inc.get("status") or "open",
                "created_at": inc.get("created_at") or inc.get("started_at") or inc.get("date"),
            })

    return {
        "ok": True,
        "health": {"pct": health_pct, "raw": health_data} if health_pct is not None else None,
        "incidents": filtered_incidents[:20],
        "site_id": site_id or None,
    }


@router.get("/api/clients/{client_id}/recs-coverage")
async def client_recs_coverage(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Покрытие рекомендациями для site_id клиента. Используется в чекапе
    качества: если покрытие < 70% — подсвечиваем warning."""
    user = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404)
    if not c.merchrules_account_id:
        return {"ok": False, "reason": "no_site_id"}
    login, password = _merchrules_creds(user)
    if not login or not password:
        return {"ok": False, "reason": "no_credentials"}

    from merchrules_sync import fetch_recs_coverage
    try:
        data = await fetch_recs_coverage(
            site_ids=[str(c.merchrules_account_id)],
            login=login, password=password,
        ) or {}
    except Exception as e:
        logger.warning("fetch_recs_coverage failed: %s", e)
        return {"ok": False, "reason": "merchrules_error", "error": str(e)}

    # Merchrules возвращает {items: [{site_id, coverage, missing_products, ...}]}
    items = data.get("items") or data.get("data") or []
    site_id = str(c.merchrules_account_id)
    target: Optional[dict] = None
    if isinstance(items, list):
        for row in items:
            if isinstance(row, dict) and str(row.get("site_id") or row.get("siteId") or "") == site_id:
                target = row
                break
        if target is None and items:
            # Если single-site запрос — берём первый элемент.
            t = items[0]
            if isinstance(t, dict):
                target = t

    if not target:
        return {"ok": True, "coverage_pct": None, "missing_count": 0}

    cov = target.get("coverage") or target.get("coverage_pct") or target.get("pct")
    cov_pct: Optional[float] = None
    if isinstance(cov, (int, float)):
        cov_pct = float(cov) * 100 if cov <= 1 else float(cov)

    missing = target.get("missing_products") or target.get("missing") or []
    missing_count = len(missing) if isinstance(missing, list) else int(missing or 0)

    return {
        "ok": True,
        "coverage_pct": cov_pct,
        "missing_count": missing_count,
        "missing_sample": missing[:10] if isinstance(missing, list) else [],
        "site_id": site_id,
        "warning": (cov_pct is not None and cov_pct < 70),
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
