"""
Account Dashboard — Блоки 1, 2, 4, 5

Блок 1: Финансы (MRR/ARR история, Апсейл/Даунсейл)
Блок 2: Health Score (авторасчёт, история, NPS/CSAT)
Блок 4: AI-инсайты (саммари, Next Best Action, тональность)
Блок 5: Executive Summary (PDF-ready данные)
"""
from typing import Optional, List
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import os

from fastapi import APIRouter, Cookie, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from database import get_db
from models import (
    Client, Task, Meeting, CheckUp, User,
    RevenueEntry, UpsellEvent, HealthSnapshot, NPSEntry,
)
from auth import decode_access_token

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_user(auth_token: Optional[str], db: Session) -> Optional[User]:
    if not auth_token:
        return None
    payload = decode_access_token(auth_token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload.get("sub", 0))).first()


def _require_user(auth_token: Optional[str], db: Session) -> User:
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401)
    return user


# ═══════════════════════════════════════════════════════════════════════════
# БЛОК 1 — ФИНАНСЫ
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/clients/{client_id}/revenue/history")
async def get_revenue_history(
    client_id: int,
    months: int = 12,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """История MRR за N месяцев + тренд."""
    _require_user(auth_token, db)

    entries = (
        db.query(RevenueEntry)
        .filter(RevenueEntry.client_id == client_id)
        .order_by(RevenueEntry.period)
        .limit(months)
        .all()
    )

    data = [
        {
            "period": e.period,
            "mrr": e.mrr,
            "arr": e.arr or e.mrr * 12,
            "currency": e.currency,
            "note": e.note,
        }
        for e in entries
    ]

    # Тренд: сравниваем последние 2 месяца
    trend = "stable"
    trend_pct = 0.0
    if len(data) >= 2:
        prev, curr = data[-2]["mrr"], data[-1]["mrr"]
        if prev > 0:
            trend_pct = round((curr - prev) / prev * 100, 1)
            if trend_pct > 3:
                trend = "up"
            elif trend_pct < -3:
                trend = "down"

    # Текущий MRR из последней записи
    current_mrr = data[-1]["mrr"] if data else 0.0

    return {
        "history": data,
        "current_mrr": current_mrr,
        "current_arr": current_mrr * 12,
        "trend": trend,
        "trend_pct": trend_pct,
    }


@router.post("/api/clients/{client_id}/revenue")
async def upsert_revenue(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Добавить/обновить MRR за период."""
    from fastapi import Request
    user = _require_user(auth_token, db)

    # читаем тело вручную через Request — роутер уже получил client_id
    # Используем зависимость Request напрямую через декоратор ниже
    raise HTTPException(status_code=405, detail="Use POST with body")


@router.post("/api/clients/{client_id}/revenue/entry")
async def create_revenue_entry(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    from fastapi import Request
    user = _require_user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    raise HTTPException(status_code=400, detail="Send JSON body")


# Реальный эндпоинт с Request
from fastapi import Request

@router.post("/api/revenue/entry")
async def add_revenue_entry(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """POST {client_id, period, mrr, currency?, note?}"""
    user = _require_user(auth_token, db)
    data = await request.json()

    client_id = data.get("client_id")
    period = data.get("period")  # "2026-03"
    mrr = float(data.get("mrr", 0))

    if not client_id or not period:
        raise HTTPException(status_code=422, detail="client_id and period required")

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    # Upsert по (client_id, period)
    entry = db.query(RevenueEntry).filter(
        RevenueEntry.client_id == client_id,
        RevenueEntry.period == period,
    ).first()

    if entry:
        entry.mrr = mrr
        entry.arr = data.get("arr") or mrr * 12
        entry.note = data.get("note", entry.note)
        entry.updated_by = user.email
    else:
        entry = RevenueEntry(
            client_id=client_id,
            period=period,
            mrr=mrr,
            arr=data.get("arr") or mrr * 12,
            currency=data.get("currency", "RUB"),
            note=data.get("note"),
            updated_by=user.email,
        )
        db.add(entry)

    # Обновляем быстрый доступ на клиенте
    client.mrr = mrr
    db.commit()

    return {"ok": True, "period": period, "mrr": mrr}


# ── Апсейл / Даунсейл ────────────────────────────────────────────────────────

@router.get("/api/clients/{client_id}/upsell")
async def get_upsell_events(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    events = (
        db.query(UpsellEvent)
        .filter(UpsellEvent.client_id == client_id)
        .order_by(desc(UpsellEvent.created_at))
        .all()
    )
    return {
        "events": [
            {
                "id": e.id,
                "type": e.event_type,
                "status": e.status,
                "amount_before": e.amount_before,
                "amount_after": e.amount_after,
                "delta": e.delta,
                "description": e.description,
                "owner": e.owner_email,
                "due_date": e.due_date.isoformat() if e.due_date else None,
                "closed_at": e.closed_at.isoformat() if e.closed_at else None,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]
    }


@router.post("/api/upsell/event")
async def create_upsell_event(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """POST {client_id, event_type, amount_before, amount_after, description, due_date?}"""
    user = _require_user(auth_token, db)
    data = await request.json()

    client_id = data.get("client_id")
    if not client_id:
        raise HTTPException(status_code=422, detail="client_id required")

    amount_before = float(data.get("amount_before") or 0)
    amount_after = float(data.get("amount_after") or 0)

    event = UpsellEvent(
        client_id=client_id,
        event_type=data.get("event_type", "upsell"),
        status=data.get("status", "identified"),
        amount_before=amount_before,
        amount_after=amount_after,
        delta=amount_after - amount_before,
        description=data.get("description"),
        owner_email=data.get("owner_email", user.email),
        due_date=datetime.fromisoformat(data["due_date"]) if data.get("due_date") else None,
        created_by=user.email,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    return {"ok": True, "id": event.id, "delta": event.delta}


@router.patch("/api/upsell/event/{event_id}")
async def update_upsell_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    data = await request.json()

    event = db.query(UpsellEvent).filter(UpsellEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404)

    for field in ("status", "description", "amount_after", "owner_email"):
        if field in data:
            setattr(event, field, data[field])

    if "amount_after" in data or "amount_before" in data:
        ab = data.get("amount_before", event.amount_before) or 0
        aa = data.get("amount_after", event.amount_after) or 0
        event.delta = float(aa) - float(ab)

    if data.get("status") in ("won", "lost"):
        event.closed_at = datetime.utcnow()

    db.commit()
    return {"ok": True, "delta": event.delta, "status": event.status}


# ═══════════════════════════════════════════════════════════════════════════
# БЛОК 2 — HEALTH SCORE (авторасчёт + история + NPS)
# ═══════════════════════════════════════════════════════════════════════════

def _calculate_health(client: Client, db: Session) -> dict:
    """
    Автоматический расчёт health score по 5 компонентам.
    Возвращает score (0.0–1.0) и breakdown.
    """
    now = datetime.utcnow()
    components = {}

    # 1. Активность встреч (30%)
    last_meeting = client.last_meeting_date
    if last_meeting:
        days_ago = (now - last_meeting).days
        if days_ago <= 14:
            components["meetings"] = 1.0
        elif days_ago <= 30:
            components["meetings"] = 0.75
        elif days_ago <= 60:
            components["meetings"] = 0.4
        elif days_ago <= 90:
            components["meetings"] = 0.15
        else:
            components["meetings"] = 0.0
    else:
        components["meetings"] = 0.0

    # 2. Задачи (25%): соотношение выполненных к открытым
    tasks = db.query(Task).filter(
        Task.client_id == client.id,
        Task.created_at >= now - timedelta(days=90),
    ).all()
    if tasks:
        done = sum(1 for t in tasks if t.status == "done")
        blocked = sum(1 for t in tasks if t.status == "blocked")
        ratio = done / len(tasks)
        blocked_penalty = min(0.3, blocked * 0.1)
        components["tasks"] = max(0.0, min(1.0, ratio - blocked_penalty))
    else:
        components["tasks"] = 0.5  # нейтрально если нет задач

    # 3. Тикеты поддержки (20%): меньше = лучше
    open_tickets = client.open_tickets or 0
    if open_tickets == 0:
        components["tickets"] = 1.0
    elif open_tickets <= 2:
        components["tickets"] = 0.7
    elif open_tickets <= 5:
        components["tickets"] = 0.4
    else:
        components["tickets"] = 0.1

    # 4. NPS последний (15%)
    nps = db.query(NPSEntry).filter(
        NPSEntry.client_id == client.id,
    ).order_by(desc(NPSEntry.recorded_at)).first()
    if nps:
        if nps.type == "nps":
            # NPS: -100..100 → 0..1
            components["nps"] = (nps.score + 100) / 200
        else:
            # CSAT: 1..10 → 0..1
            components["nps"] = (nps.score - 1) / 9
    else:
        components["nps"] = 0.5  # нейтрально

    # 5. Чекапы не просрочены (10%)
    overdue_checkup = db.query(CheckUp).filter(
        CheckUp.client_id == client.id,
        CheckUp.status == "overdue",
    ).count()
    components["checkups"] = 0.0 if overdue_checkup > 0 else 1.0

    # Взвешенная сумма
    weights = {
        "meetings": 0.30,
        "tasks":    0.25,
        "tickets":  0.20,
        "nps":      0.15,
        "checkups": 0.10,
    }
    score = sum(components[k] * weights[k] for k in weights)
    score = round(score, 3)

    return {"score": score, "components": components}


@router.post("/api/clients/{client_id}/health/recalc")
async def recalc_health(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Пересчитать health score и сохранить снимок."""
    _require_user(auth_token, db)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    result = _calculate_health(client, db)
    score = result["score"]
    components = result["components"]

    # Обновляем клиента
    client.health_score = score
    db.add(client)

    # Пишем снимок
    snapshot = HealthSnapshot(
        client_id=client_id,
        score=score,
        components=components,
    )
    db.add(snapshot)
    db.commit()

    return {
        "score": score,
        "score_pct": round(score * 100),
        "components": components,
        "level": (
            "critical" if score < 0.3 else
            "warning"  if score < 0.6 else
            "good"     if score < 0.8 else
            "excellent"
        ),
    }


@router.get("/api/clients/{client_id}/health/history")
async def get_health_history(
    client_id: int,
    months: int = 6,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """История health score за N месяцев."""
    _require_user(auth_token, db)

    since = datetime.utcnow() - timedelta(days=months * 30)
    snapshots = (
        db.query(HealthSnapshot)
        .filter(
            HealthSnapshot.client_id == client_id,
            HealthSnapshot.calculated_at >= since,
        )
        .order_by(HealthSnapshot.calculated_at)
        .all()
    )

    # Группируем по месяцам (берём последний снимок месяца)
    by_month: dict = {}
    for s in snapshots:
        key = s.calculated_at.strftime("%Y-%m")
        by_month[key] = s

    history = [
        {
            "period": k,
            "score": round(v.score * 100),
            "components": v.components,
            "date": v.calculated_at.isoformat(),
        }
        for k, v in sorted(by_month.items())
    ]

    return {"history": history}


# ── NPS / CSAT ────────────────────────────────────────────────────────────────

@router.get("/api/clients/{client_id}/nps")
async def get_nps_history(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)

    entries = (
        db.query(NPSEntry)
        .filter(NPSEntry.client_id == client_id)
        .order_by(desc(NPSEntry.recorded_at))
        .limit(20)
        .all()
    )

    latest = entries[0] if entries else None
    avg = sum(e.score for e in entries) / len(entries) if entries else None

    return {
        "latest": {
            "score": latest.score,
            "type": latest.type,
            "comment": latest.comment,
            "date": latest.recorded_at.isoformat(),
        } if latest else None,
        "average": round(avg, 1) if avg is not None else None,
        "count": len(entries),
        "history": [
            {
                "score": e.score,
                "type": e.type,
                "comment": e.comment,
                "date": e.recorded_at.isoformat(),
                "source": e.source,
            }
            for e in entries
        ],
    }


@router.post("/api/nps/entry")
async def add_nps_entry(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """POST {client_id, score, type?, comment?, source?}"""
    user = _require_user(auth_token, db)
    data = await request.json()

    client_id = data.get("client_id")
    score = data.get("score")
    if client_id is None or score is None:
        raise HTTPException(status_code=422, detail="client_id and score required")

    entry = NPSEntry(
        client_id=client_id,
        score=int(score),
        type=data.get("type", "nps"),
        comment=data.get("comment"),
        source=data.get("source", "manual"),
        recorded_by=user.email,
    )
    db.add(entry)

    # Обновляем быстрый кэш на клиенте
    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        client.nps_last = int(score)
        client.nps_date = datetime.utcnow()

    db.commit()
    return {"ok": True, "id": entry.id}


# ═══════════════════════════════════════════════════════════════════════════
# БЛОК 4 — AI ИНСАЙТЫ
# ═══════════════════════════════════════════════════════════════════════════

def _build_account_context(client: Client, db: Session) -> str:
    """Собирает контекст аккаунта для AI."""
    now = datetime.utcnow()

    # Задачи
    tasks = db.query(Task).filter(Task.client_id == client.id).order_by(desc(Task.created_at)).limit(15).all()
    tasks_text = "\n".join(
        f"- [{t.status}] {t.title} (приоритет: {t.priority})"
        + (f" — просрочена" if t.due_date and t.due_date < now and t.status != "done" else "")
        for t in tasks
    ) or "Нет задач"

    # Встречи
    meetings = db.query(Meeting).filter(Meeting.client_id == client.id).order_by(desc(Meeting.date)).limit(5).all()
    meetings_text = "\n".join(
        f"- {m.date.strftime('%d.%m.%Y') if m.date else '?'}: {m.title or m.type}"
        + (f" (фолоуап: {m.followup_status})" if m.followup_status != "sent" else "")
        for m in meetings
    ) or "Нет встреч"

    # Финансы
    revenue = db.query(RevenueEntry).filter(
        RevenueEntry.client_id == client.id
    ).order_by(desc(RevenueEntry.period)).limit(3).all()
    revenue_text = "\n".join(
        f"- {r.period}: MRR {r.mrr:,.0f} {r.currency}"
        for r in revenue
    ) or "Нет данных по выручке"

    # NPS
    nps = db.query(NPSEntry).filter(NPSEntry.client_id == client.id).order_by(desc(NPSEntry.recorded_at)).first()
    nps_text = f"NPS/CSAT: {nps.score} ({nps.type}), {nps.recorded_at.strftime('%d.%m.%Y')}" if nps else "NPS: нет данных"

    # Health
    health_pct = round((client.health_score or 0) * 100)

    # Апсейл/Даунсейл
    upsells = db.query(UpsellEvent).filter(
        UpsellEvent.client_id == client.id,
        UpsellEvent.status.in_(["identified", "in_progress"]),
    ).all()
    upsell_text = "\n".join(
        f"- {e.event_type}: {e.description or ''} (delta: {e.delta:+,.0f} руб.)"
        for e in upsells
    ) or "Нет активных апсейл/даунсейл"

    last_contact = client.last_meeting_date
    days_no_contact = (now - last_contact).days if last_contact else 999

    return f"""Клиент: {client.name}
Сегмент: {client.segment or '—'}
Health Score: {health_pct}%
{nps_text}
Последний контакт: {days_no_contact} дней назад
Тикеты: {client.open_tickets or 0} открытых

Последние задачи:
{tasks_text}

Последние встречи:
{meetings_text}

Финансы (последние 3 месяца):
{revenue_text}

Активные апсейл/даунсейл события:
{upsell_text}"""


@router.get("/api/clients/{client_id}/ai/summary")
async def get_ai_summary(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """AI-саммари аккаунта: что происходит + Next Best Action."""
    _require_user(auth_token, db)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    context = _build_account_context(client, db)

    system = """Ты — опытный Account Manager в B2B SaaS компании.
Твоя задача — кратко проанализировать аккаунт и дать практические рекомендации.
Отвечай строго в JSON формате без markdown-блоков."""

    prompt = f"""Проанализируй аккаунт и верни JSON:
{{
  "summary": "2-3 предложения о текущем состоянии аккаунта",
  "health_verdict": "одно слово: отличный|хороший|требует внимания|критичный",
  "risks": ["риск 1", "риск 2"],
  "next_best_actions": [
    {{"action": "что сделать", "reason": "почему", "priority": "high|medium|low", "deadline": "когда"}},
    ...
  ],
  "opportunities": ["возможность 1", "возможность 2"],
  "sentiment": "positive|neutral|negative"
}}

Данные аккаунта:
{context}"""

    try:
        from ai_assistant import _chat_sync
        raw = _chat_sync(system, prompt, max_tokens=1500)
        # Убираем возможные markdown-обёртки
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        import json
        result = json.loads(raw)
    except Exception as e:
        logger.warning(f"AI summary failed: {e}")
        # Fallback — детерминированный анализ
        result = _fallback_summary(client, db)

    return result


def _fallback_summary(client: Client, db: Session) -> dict:
    """Детерминированный fallback если AI недоступен."""
    now = datetime.utcnow()
    risks = []
    actions = []

    days_no_contact = (now - client.last_meeting_date).days if client.last_meeting_date else 999
    health_pct = round((client.health_score or 0) * 100)

    if days_no_contact > 30:
        risks.append(f"Нет контакта {days_no_contact} дней")
        actions.append({
            "action": f"Запланировать встречу с {client.name}",
            "reason": f"Последний контакт {days_no_contact} дней назад",
            "priority": "high",
            "deadline": "на этой неделе",
        })

    if (client.open_tickets or 0) > 3:
        risks.append(f"{client.open_tickets} открытых тикетов")
        actions.append({
            "action": "Разобрать открытые тикеты",
            "reason": "Накопились нерешённые обращения",
            "priority": "high",
            "deadline": "в течение 3 дней",
        })

    tasks = db.query(Task).filter(Task.client_id == client.id, Task.status == "blocked").all()
    if tasks:
        risks.append(f"{len(tasks)} заблокированных задач")
        actions.append({
            "action": "Разблокировать задачи",
            "reason": "Есть задачи в статусе blocked",
            "priority": "medium",
            "deadline": "на следующей встрече",
        })

    if health_pct < 40:
        risks.append(f"Низкий health score: {health_pct}%")

    verdict = (
        "критичный" if health_pct < 30 else
        "требует внимания" if health_pct < 60 else
        "хороший" if health_pct < 80 else
        "отличный"
    )

    return {
        "summary": (
            f"Аккаунт {client.name} ({client.segment or 'неизвестный сегмент'}). "
            f"Health score: {health_pct}%. "
            f"Последний контакт: {days_no_contact} дн. назад."
        ),
        "health_verdict": verdict,
        "risks": risks or ["Явных рисков не обнаружено"],
        "next_best_actions": actions or [{"action": "Провести плановый чекап", "reason": "Поддержание отношений", "priority": "medium", "deadline": "в течение месяца"}],
        "opportunities": [],
        "sentiment": "neutral",
    }


@router.get("/api/clients/{client_id}/ai/sentiment")
async def get_meeting_sentiment(
    client_id: int,
    limit: int = 5,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Анализ тональности последних встреч."""
    _require_user(auth_token, db)

    meetings = (
        db.query(Meeting)
        .filter(Meeting.client_id == client_id, Meeting.summary.isnot(None))
        .order_by(desc(Meeting.date))
        .limit(limit)
        .all()
    )

    results = []
    for m in meetings:
        sentiment = m.mood or "neutral"
        score = m.sentiment_score

        # Если нет AI-оценки — делаем быстрый keyword-анализ
        if score is None and m.summary:
            text = (m.summary or "").lower()
            positive_words = ["доволен", "хорошо", "отлично", "растём", "успешно", "нравится", "продлим", "расширим"]
            negative_words = ["проблем", "недоволен", "плохо", "уходим", "закрываем", "не устраивает", "жалоб"]
            pos = sum(1 for w in positive_words if w in text)
            neg = sum(1 for w in negative_words if w in text)
            if pos > neg:
                sentiment = "positive"
                score = 0.7
            elif neg > pos:
                sentiment = "negative"
                score = 0.3
            else:
                sentiment = "neutral"
                score = 0.5

        results.append({
            "meeting_id": m.id,
            "date": m.date.isoformat() if m.date else None,
            "title": m.title or m.type,
            "sentiment": sentiment,
            "score": score,
        })

    # Общий тренд
    if results:
        avg = sum(r["score"] or 0.5 for r in results) / len(results)
        trend = "positive" if avg > 0.6 else "negative" if avg < 0.4 else "neutral"
    else:
        avg, trend = 0.5, "neutral"

    return {
        "meetings": results,
        "overall_sentiment": trend,
        "avg_score": round(avg, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# БЛОК 5 — EXECUTIVE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/clients/{client_id}/executive-summary")
async def get_executive_summary(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Полный executive summary для руководства.
    Включает: финансы, health, риски, задачи, следующие шаги.
    """
    user = _require_user(auth_token, db)
    now = datetime.utcnow()

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    # Финансы
    revenue_entries = (
        db.query(RevenueEntry)
        .filter(RevenueEntry.client_id == client_id)
        .order_by(desc(RevenueEntry.period))
        .limit(3)
        .all()
    )
    current_mrr = revenue_entries[0].mrr if revenue_entries else (client.mrr or 0)
    mrr_trend = "stable"
    mrr_trend_pct = 0.0
    if len(revenue_entries) >= 2:
        prev = revenue_entries[1].mrr
        if prev > 0:
            mrr_trend_pct = round((current_mrr - prev) / prev * 100, 1)
            mrr_trend = "up" if mrr_trend_pct > 3 else "down" if mrr_trend_pct < -3 else "stable"

    # Апсейл
    active_upsells = db.query(UpsellEvent).filter(
        UpsellEvent.client_id == client_id,
        UpsellEvent.status.in_(["identified", "in_progress"]),
    ).all()
    upsell_pipeline = sum(e.delta or 0 for e in active_upsells if (e.delta or 0) > 0)
    downsell_risk = sum(abs(e.delta or 0) for e in active_upsells if (e.delta or 0) < 0)

    # Health
    health_pct = round((client.health_score or 0) * 100)
    health_history = (
        db.query(HealthSnapshot)
        .filter(HealthSnapshot.client_id == client_id)
        .order_by(desc(HealthSnapshot.calculated_at))
        .limit(6)
        .all()
    )
    health_trend = "stable"
    if len(health_history) >= 2:
        delta = health_history[0].score - health_history[-1].score
        health_trend = "improving" if delta > 0.05 else "declining" if delta < -0.05 else "stable"

    # NPS
    nps = db.query(NPSEntry).filter(NPSEntry.client_id == client_id).order_by(desc(NPSEntry.recorded_at)).first()

    # Задачи
    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    tasks_summary = {
        "total": len(tasks),
        "done": sum(1 for t in tasks if t.status == "done"),
        "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
        "blocked": sum(1 for t in tasks if t.status == "blocked"),
        "overdue": sum(1 for t in tasks if t.due_date and t.due_date < now and t.status not in ("done",)),
    }

    # Встречи
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(desc(Meeting.date)).limit(3).all()
    days_no_contact = (now - client.last_meeting_date).days if client.last_meeting_date else 999

    # QBR
    from models import QBR
    last_qbr = db.query(QBR).filter(QBR.client_id == client_id).order_by(desc(QBR.date)).first()

    # Сборка summary
    return {
        "client": {
            "id": client.id,
            "name": client.name,
            "segment": client.segment,
            "domain": client.domain,
            "manager": client.manager_email,
        },
        "generated_at": now.isoformat(),
        "generated_by": user.email,

        "finance": {
            "current_mrr": current_mrr,
            "current_arr": current_mrr * 12,
            "currency": revenue_entries[0].currency if revenue_entries else "RUB",
            "mrr_trend": mrr_trend,
            "mrr_trend_pct": mrr_trend_pct,
            "upsell_pipeline": upsell_pipeline,
            "downsell_risk": downsell_risk,
            "revenue_history": [
                {"period": r.period, "mrr": r.mrr}
                for r in reversed(revenue_entries)
            ],
            "active_upsells": [
                {
                    "type": e.event_type,
                    "description": e.description,
                    "delta": e.delta,
                    "status": e.status,
                }
                for e in active_upsells
            ],
        },

        "health": {
            "score": health_pct,
            "trend": health_trend,
            "level": (
                "critical"  if health_pct < 30 else
                "warning"   if health_pct < 60 else
                "good"      if health_pct < 80 else
                "excellent"
            ),
            "days_no_contact": days_no_contact,
            "open_tickets": client.open_tickets or 0,
            "nps": {
                "score": nps.score,
                "type": nps.type,
                "date": nps.recorded_at.isoformat(),
            } if nps else None,
        },

        "activity": {
            "tasks": tasks_summary,
            "recent_meetings": [
                {
                    "date": m.date.isoformat() if m.date else None,
                    "title": m.title or m.type,
                    "followup": m.followup_status,
                }
                for m in meetings
            ],
            "last_qbr": {
                "quarter": last_qbr.quarter,
                "date": last_qbr.date.isoformat() if last_qbr.date else None,
                "status": last_qbr.status,
            } if last_qbr else None,
        },

        "risks": _compute_risks(client, tasks, days_no_contact),
        "next_steps": _compute_next_steps(client, tasks, days_no_contact, active_upsells),
    }


def _compute_risks(client, tasks, days_no_contact) -> list:
    risks = []
    health_pct = round((client.health_score or 0) * 100)

    if days_no_contact > 45:
        risks.append({"level": "high", "text": f"Нет контакта {days_no_contact} дней"})
    elif days_no_contact > 30:
        risks.append({"level": "medium", "text": f"Редкий контакт ({days_no_contact} дней)"})

    blocked = [t for t in tasks if t.status == "blocked"]
    if blocked:
        risks.append({"level": "high", "text": f"{len(blocked)} заблокированных задач"})

    if health_pct < 40:
        risks.append({"level": "critical", "text": f"Критично низкий health score: {health_pct}%"})
    elif health_pct < 60:
        risks.append({"level": "medium", "text": f"Health score ниже нормы: {health_pct}%"})

    if (client.open_tickets or 0) > 5:
        risks.append({"level": "medium", "text": f"Много открытых тикетов: {client.open_tickets}"})

    return risks


def _compute_next_steps(client, tasks, days_no_contact, upsells) -> list:
    steps = []

    if days_no_contact > 30:
        steps.append({
            "priority": "high",
            "action": "Провести встречу с клиентом",
            "type": "meeting",
        })

    if upsells:
        steps.append({
            "priority": "medium",
            "action": f"Продвинуть {len(upsells)} апсейл/даунсейл событий",
            "type": "upsell",
        })

    blocked = [t for t in tasks if t.status == "blocked"]
    if blocked:
        steps.append({
            "priority": "high",
            "action": f"Разблокировать {len(blocked)} задач",
            "type": "tasks",
        })

    overdue = [t for t in tasks if t.due_date and t.due_date < datetime.utcnow() and t.status != "done"]
    if overdue:
        steps.append({
            "priority": "medium",
            "action": f"Закрыть {len(overdue)} просроченных задач",
            "type": "tasks",
        })

    if not steps:
        steps.append({
            "priority": "low",
            "action": "Провести плановый чекап",
            "type": "checkup",
        })

    return steps


@router.post("/api/clients/{client_id}/executive-summary/ai")
async def generate_ai_executive_summary(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Генерирует AI-текст для executive summary (для PDF)."""
    user = _require_user(auth_token, db)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    context = _build_account_context(client, db)

    system = """Ты — Account Manager, пишешь executive summary для руководства.
Стиль: деловой, лаконичный, без воды. Используй конкретные данные."""

    prompt = f"""Напиши executive summary аккаунта для руководства (3-4 абзаца):
1. Текущее состояние партнёрства
2. Финансовые результаты и динамика
3. Ключевые риски и возможности
4. Рекомендуемые следующие шаги

Данные:
{context}

Верни только текст, без заголовков и маркеров."""

    try:
        from ai_assistant import _chat_sync
        text = _chat_sync(system, prompt, max_tokens=800)
    except Exception as e:
        logger.warning(f"AI exec summary failed: {e}")
        health_pct = round((client.health_score or 0) * 100)
        text = (
            f"Аккаунт {client.name} ({client.segment}) находится в состоянии, "
            f"требующем {'внимания' if health_pct < 60 else 'стандартного сопровождения'}. "
            f"Health Score: {health_pct}%. "
            f"Открытых тикетов: {client.open_tickets or 0}."
        )

    return {"text": text, "generated_at": datetime.utcnow().isoformat()}


# ── Батч-пересчёт health для всех клиентов (для scheduler) ───────────────────

@router.post("/api/health/recalc-all")
async def recalc_all_health(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Пересчитать health score всех клиентов. Только для admin."""
    user = _require_user(auth_token, db)
    if user.role != "admin":
        raise HTTPException(status_code=403)

    clients = db.query(Client).all()
    updated = 0
    for client in clients:
        try:
            result = _calculate_health(client, db)
            client.health_score = result["score"]
            snapshot = HealthSnapshot(
                client_id=client.id,
                score=result["score"],
                components=result["components"],
            )
            db.add(snapshot)
            updated += 1
        except Exception as e:
            logger.warning(f"Health recalc failed for client {client.id}: {e}")

    db.commit()
    return {"ok": True, "updated": updated}


# ═══════════════════════════════════════════════════════════════════════════
# ПОРТФЕЛЬНЫЙ ДАШБОРД — список всех клиентов с мини-метриками
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/portfolio")
async def get_portfolio(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Все клиенты менеджера с мини-метриками для портфельного дашборда.
    Возвращает данные для мгновенного понимания состояния портфеля.
    """
    user = _require_user(auth_token, db)
    now = datetime.utcnow()

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.order_by(Client.health_score).all()  # сначала проблемные

    items = []
    for c in clients:
        # Задачи
        tasks = db.query(Task).filter(Task.client_id == c.id).all()
        open_t  = sum(1 for t in tasks if t.status in ("plan", "in_progress"))
        blocked = sum(1 for t in tasks if t.status == "blocked")
        overdue_t = sum(1 for t in tasks if t.due_date and t.due_date < now and t.status not in ("done",))

        # Дней без контакта
        days_silent = (now - c.last_meeting_date).days if c.last_meeting_date else 999

        # Health
        health_pct = round((c.health_score or 0) * 100)
        risk = (
            "critical" if health_pct < 30 or days_silent > 60 or blocked > 2
            else "warning" if health_pct < 60 or days_silent > 30 or blocked > 0
            else "good"
        )

        # Ближайшая встреча
        next_meeting = (
            db.query(Meeting)
            .filter(Meeting.client_id == c.id, Meeting.date > now)
            .order_by(Meeting.date)
            .first()
        )

        # Незакрытые фолоуапы
        pending_followups = db.query(Meeting).filter(
            Meeting.client_id == c.id,
            Meeting.followup_status == "pending",
        ).count()

        # Активный апсейл
        try:
            active_upsell = db.query(UpsellEvent).filter(
                UpsellEvent.client_id == c.id,
                UpsellEvent.status.in_(["identified", "in_progress"]),
                UpsellEvent.delta > 0,
            ).count()
        except Exception:
            active_upsell = 0

        items.append({
            "id": c.id,
            "name": c.name,
            "segment": c.segment or "—",
            "manager": c.manager_email,
            "health": health_pct,
            "risk": risk,
            "mrr": c.mrr or 0,
            "nps": c.nps_last,
            "days_silent": days_silent if days_silent < 999 else None,
            "open_tasks": open_t,
            "blocked_tasks": blocked,
            "overdue_tasks": overdue_t,
            "pending_followups": pending_followups,
            "active_upsell": active_upsell,
            "next_meeting": next_meeting.date.isoformat() if next_meeting else None,
            "open_tickets": c.open_tickets or 0,
        })

    # Сводная статистика портфеля
    total_mrr   = sum(i["mrr"] for i in items)
    critical    = sum(1 for i in items if i["risk"] == "critical")
    warning_cnt = sum(1 for i in items if i["risk"] == "warning")
    upsell_cnt  = sum(i["active_upsell"] for i in items)

    return {
        "clients": items,
        "summary": {
            "total": len(items),
            "total_mrr": total_mrr,
            "critical": critical,
            "warning": warning_cnt,
            "healthy": len(items) - critical - warning_cnt,
            "active_upsells": upsell_cnt,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# ПРОГНОЗ ПРОДЛЕНИЯ
# ═══════════════════════════════════════════════════════════════════════════

def _renewal_risk_score(client, tasks, days_silent: int, db) -> dict:
    """
    Формула риска оттока / прогноза продления.
    
    Факторы (сумма = 100 баллов риска):
      health_score     30% — чем ниже, тем выше риск
      days_silent      25% — дни без контакта
      blocked_tasks    15% — заблокированные задачи = проблемы
      open_tickets     15% — поддержка = недовольство
      nps_score        15% — прямая оценка клиента
    
    Итог: 0–100 (0 = точно продлится, 100 = точно уйдёт)
    """
    score = 0.0
    factors = {}

    # 1. Health score (30%)
    health = client.health_score or 0
    health_risk = (1 - health) * 30
    score += health_risk
    factors["health"] = {
        "value": round(health * 100),
        "risk_pts": round(health_risk, 1),
        "label": f"Health {round(health*100)}%",
    }

    # 2. Дни без контакта (25%)
    if days_silent >= 90:
        silent_risk = 25.0
    elif days_silent >= 60:
        silent_risk = 18.0
    elif days_silent >= 30:
        silent_risk = 10.0
    elif days_silent >= 14:
        silent_risk = 4.0
    else:
        silent_risk = 0.0
    score += silent_risk
    factors["days_silent"] = {
        "value": days_silent if days_silent < 999 else None,
        "risk_pts": silent_risk,
        "label": f"{days_silent} дн. без контакта" if days_silent < 999 else "Нет контакта",
    }

    # 3. Заблокированные задачи (15%)
    blocked = sum(1 for t in tasks if t.status == "blocked")
    blocked_risk = min(15.0, blocked * 5.0)
    score += blocked_risk
    factors["blocked_tasks"] = {
        "value": blocked,
        "risk_pts": blocked_risk,
        "label": f"{blocked} заблок. задач",
    }

    # 4. Открытые тикеты (15%)
    tickets = client.open_tickets or 0
    ticket_risk = min(15.0, tickets * 3.0)
    score += ticket_risk
    factors["open_tickets"] = {
        "value": tickets,
        "risk_pts": ticket_risk,
        "label": f"{tickets} откр. тикетов",
    }

    # 5. NPS (15%)
    nps = client.nps_last
    if nps is not None:
        # NPS -100..100 → риск 0..15
        nps_risk = max(0.0, (50 - nps) / 100 * 15)
    else:
        nps_risk = 7.5  # нет данных — нейтральный риск
    score += nps_risk
    factors["nps"] = {
        "value": nps,
        "risk_pts": round(nps_risk, 1),
        "label": f"NPS {nps}" if nps is not None else "NPS неизвестен",
    }

    score = min(100.0, round(score, 1))
    level = (
        "critical" if score >= 70 else
        "high"     if score >= 50 else
        "medium"   if score >= 30 else
        "low"
    )
    level_labels = {
        "critical": "Высокий риск оттока",
        "high":     "Повышенный риск",
        "medium":   "Умеренный риск",
        "low":      "Низкий риск",
    }
    renewal_prob = round(max(0, 100 - score))

    return {
        "risk_score": score,
        "level": level,
        "level_label": level_labels[level],
        "renewal_probability": renewal_prob,
        "factors": factors,
    }


@router.get("/api/clients/{client_id}/renewal-forecast")
async def get_renewal_forecast(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Прогноз продления контракта клиента.
    Возвращает: риск оттока (0-100), вероятность продления (%),
    разбивку по факторам, рекомендации.
    """
    _require_user(auth_token, db)
    now = datetime.utcnow()

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    days_silent = (now - client.last_meeting_date).days if client.last_meeting_date else 999

    forecast = _renewal_risk_score(client, tasks, days_silent, db)

    # Дата ближайшего продления из UpsellEvent или из последней MRR записи
    from models import RevenueEntry, UpsellEvent as UE
    last_rev = db.query(RevenueEntry).filter(
        RevenueEntry.client_id == client_id
    ).order_by(desc(RevenueEntry.period)).first()

    renewal_date = None
    if last_rev:
        # Предполагаем ежегодное продление — следующее через 12 месяцев от последней записи
        try:
            from datetime import date
            y, m = map(int, last_rev.period.split('-'))
            renewal_month = m
            renewal_year = y + 1
            if renewal_month > 12:
                renewal_month -= 12
                renewal_year += 1
            renewal_date = f"{renewal_year}-{renewal_month:02d}-01"
        except Exception:
            pass

    # Рекомендации на основе факторов
    recommendations = []
    f = forecast["factors"]
    if f["days_silent"]["risk_pts"] >= 10:
        recommendations.append({
            "priority": "high",
            "action": "Срочно связаться с клиентом",
            "reason": f["days_silent"]["label"],
        })
    if f["blocked_tasks"]["risk_pts"] >= 5:
        recommendations.append({
            "priority": "high",
            "action": "Разблокировать задачи",
            "reason": f["blocked_tasks"]["label"],
        })
    if f["health"]["risk_pts"] >= 15:
        recommendations.append({
            "priority": "medium",
            "action": "Провести чекап здоровья аккаунта",
            "reason": f"Health Score {f['health']['value']}%",
        })
    if f["open_tickets"]["risk_pts"] >= 9:
        recommendations.append({
            "priority": "medium",
            "action": "Закрыть открытые тикеты",
            "reason": f["open_tickets"]["label"],
        })
    if not recommendations:
        recommendations.append({
            "priority": "low",
            "action": "Плановый чекап",
            "reason": "Поддержание отношений",
        })

    return {
        **forecast,
        "renewal_date": renewal_date,
        "recommendations": recommendations,
        "client_id": client_id,
        "client_name": client.name,
        "segment": client.segment,
        "mrr": client.mrr or 0,
    }


@router.get("/api/portfolio/renewal-risks")
async def get_portfolio_renewal_risks(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Прогноз продления для всего портфеля — топ рисков."""
    user = _require_user(auth_token, db)
    now = datetime.utcnow()

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    results = []
    for c in clients:
        tasks = db.query(Task).filter(Task.client_id == c.id).all()
        days_silent = (now - c.last_meeting_date).days if c.last_meeting_date else 999
        forecast = _renewal_risk_score(c, tasks, days_silent, db)
        results.append({
            "client_id": c.id,
            "client_name": c.name,
            "segment": c.segment,
            "mrr": c.mrr or 0,
            "risk_score": forecast["risk_score"],
            "level": forecast["level"],
            "renewal_probability": forecast["renewal_probability"],
        })

    results.sort(key=lambda x: -x["risk_score"])
    
    at_risk_mrr = sum(r["mrr"] for r in results if r["level"] in ("critical", "high"))
    
    return {
        "clients": results,
        "summary": {
            "total": len(results),
            "critical": sum(1 for r in results if r["level"] == "critical"),
            "high": sum(1 for r in results if r["level"] == "high"),
            "at_risk_mrr": at_risk_mrr,
        }
    }
