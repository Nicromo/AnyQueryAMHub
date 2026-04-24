"""Quick-actions API: meeting prep brief, instant followup, transfer briefing."""
import os
import logging
from typing import Optional
from datetime import datetime, date

from fastapi import APIRouter, Depends, Cookie, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import (
    Client, Task, Meeting, ClientContact, CheckUp, NPSEntry, User,
    ClientContext,
)

logger = logging.getLogger(__name__)
router = APIRouter()

from env_helpers import tg_bot_token, tg_notify_chat_id, groq_api_key, qwen_api_key

TG_BOT_TOKEN    = tg_bot_token() or ""
TG_NOTIFY_CHAT  = tg_notify_chat_id() or ""
GROQ_API_KEY    = groq_api_key() or ""
QWEN_API_KEY    = qwen_api_key() or ""


def _user(auth_token: Optional[str]) -> Optional[dict]:
    if not auth_token:
        return None
    payload = decode_access_token(auth_token)
    return payload if payload else None


# ── Prep brief ────────────────────────────────────────────────────────────────

@router.get("/api/clients/{client_id}/prep-brief")
def get_prep_brief(
    client_id: int,
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    u = _user(auth_token)
    if not u:
        raise HTTPException(status_code=401, detail="Unauthorized")

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Last 5 meetings
    meetings_q = (
        db.query(Meeting)
        .filter(Meeting.client_id == client_id)
        .order_by(Meeting.date.desc())
        .limit(5)
        .all()
    )
    meetings = []
    for m in meetings_q:
        meetings.append({
            "id": m.id,
            "date": str(m.date) if m.date else None,
            "type": m.meeting_type or "meeting",
            "summary": m.summary or "",
            "mood": m.mood or "neutral",
        })

    # Open tasks
    tasks_q = (
        db.query(Task)
        .filter(Task.client_id == client_id, Task.status.in_(["plan", "in_progress", "blocked"]))
        .order_by(Task.due_date.asc().nullslast())
        .limit(10)
        .all()
    )
    tasks = []
    for t in tasks_q:
        due_str = str(t.due_date) if t.due_date else None
        overdue = False
        if t.due_date and isinstance(t.due_date, date):
            overdue = t.due_date < date.today()
        tasks.append({
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority or "medium",
            "due_date": due_str,
            "overdue": overdue,
        })

    # Client context
    ctx = db.query(ClientContext).filter(ClientContext.client_id == client_id).first()
    context = None
    if ctx:
        context = {
            "key_facts":   ctx.key_facts or [],
            "pain_points": ctx.pain_points or [],
            "wins":        ctx.wins or [],
            "risks":       ctx.risks or [],
            "next_steps":  ctx.next_steps or [],
            "notes":       ctx.summary or "",
        }

    # Latest NPS
    nps_row = (
        db.query(NPSEntry)
        .filter(NPSEntry.client_id == client_id)
        .order_by(NPSEntry.recorded_at.desc())
        .first()
    )
    nps = None
    if nps_row:
        nps = {
            "score":   nps_row.score,
            "comment": nps_row.comment or "",
            "date":    str(nps_row.recorded_at) if nps_row.recorded_at else None,
        }

    # Contacts
    contacts_q = db.query(ClientContact).filter(ClientContact.client_id == client_id).limit(5).all()
    contacts = []
    for c in contacts_q:
        contacts.append({
            "name":  c.name or "",
            "role":  c.role or "",
            "email": c.email or "",
            "phone": c.phone or "",
        })

    # Health info
    renewal_days = None
    if client.renewal_date:
        delta = (client.renewal_date - date.today()).days
        renewal_days = delta

    return {
        "client": {
            "id":            client.id,
            "name":          client.name,
            "segment":       client.segment or "",
            "status":        client.status or "ok",
            "health_score":  float(client.health_score) if client.health_score else None,
            "churn_risk":    float(client.churn_risk) if client.churn_risk else None,
            "manager_email": client.manager_email or "",
            "renewal_date":  str(client.renewal_date) if client.renewal_date else None,
            "renewal_days":  renewal_days,
        },
        "meetings": meetings,
        "tasks":    tasks,
        "context":  context,
        "nps":      nps,
        "contacts": contacts,
    }


# ── Quick followup ────────────────────────────────────────────────────────────

@router.post("/api/clients/{client_id}/quick-followup")
async def quick_followup(
    client_id: int,
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    u = _user(auth_token)
    if not u:
        raise HTTPException(status_code=401, detail="Unauthorized")

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if not TG_BOT_TOKEN or not TG_NOTIFY_CHAT:
        return JSONResponse({"ok": False, "error": "TG_BOT_TOKEN / TG_NOTIFY_CHAT_ID не настроены"})

    import httpx
    manager_name = u.get("name") or u.get("sub") or "AM"
    msg = (
        f"📋 *Фолоуап по клиенту {client.name}*\n\n"
        f"Менеджер: {manager_name}\n"
        f"Сегмент: {client.segment or '—'}\n"
        f"Health: {round(float(client.health_score) * 100) if client.health_score else '—'}%\n\n"
        f"_Отправлено из AM Hub_"
    )
    try:
        async with httpx.AsyncClient(timeout=8) as hx:
            r = await hx.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": TG_NOTIFY_CHAT, "text": msg, "parse_mode": "Markdown"},
            )
        if r.status_code == 200:
            return {"ok": True}
        return JSONResponse({"ok": False, "error": f"TG error {r.status_code}: {r.text[:200]}"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ── Transfer briefing ─────────────────────────────────────────────────────────

@router.get("/api/clients/{client_id}/transfer-brief")
async def get_transfer_brief(
    client_id: int,
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    u = _user(auth_token)
    if not u:
        raise HTTPException(status_code=401, detail="Unauthorized")

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Collect data for the briefing
    meetings = (
        db.query(Meeting).filter(Meeting.client_id == client_id)
        .order_by(Meeting.date.desc()).limit(5).all()
    )
    tasks = (
        db.query(Task).filter(Task.client_id == client_id, Task.status != "done")
        .order_by(Task.due_date.asc().nullslast()).limit(10).all()
    )
    ctx = db.query(ClientContext).filter(ClientContext.client_id == client_id).first()

    meetings_text = "\n".join(
        f"- {m.date}: {m.meeting_type} — {(m.summary or '')[:120]}"
        for m in meetings
    ) or "нет данных"

    tasks_text = "\n".join(
        f"- [{t.status}] {t.title} (due: {t.due_date or '—'})"
        for t in tasks
    ) or "нет открытых задач"

    context_text = ""
    if ctx:
        if ctx.key_facts:    context_text += f"\nКлючевые факты: {'; '.join(ctx.key_facts[:3])}"
        if ctx.pain_points:  context_text += f"\nПроблемы: {'; '.join(ctx.pain_points[:3])}"
        if ctx.risks:        context_text += f"\nРиски: {'; '.join(ctx.risks[:3])}"
        if ctx.next_steps:   context_text += f"\nСледующие шаги: {'; '.join(ctx.next_steps[:3])}"

    prompt = f"""Напиши краткий брифинг для передачи клиента новому аккаунт-менеджеру.

Клиент: {client.name}
Сегмент: {client.segment or '—'}
Health score: {round(float(client.health_score) * 100) if client.health_score else '—'}%
Дата продления: {client.renewal_date or '—'}

Последние встречи:
{meetings_text}

Открытые задачи:
{tasks_text}
{context_text}

Напиши брифинг в формате: краткое резюме (2-3 предложения) + 3-5 ключевых пунктов для нового менеджера + критические риски/дедлайны + следующий обязательный шаг. Без лишних слов, по делу."""

    text = ""
    if GROQ_API_KEY:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=25) as hx:
                r = await hx.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json={"model": "llama3-8b-8192", "messages": [{"role": "user", "content": prompt}], "max_tokens": 600},
                )
                if r.status_code == 200:
                    text = r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("transfer-brief AI error: %s", e)

    if not text:
        # Fallback: structured text without AI
        health_pct = round(float(client.health_score) * 100) if client.health_score else None
        lines = [
            f"**{client.name}** ({client.segment or 'сегмент не указан'})",
            f"Health score: {health_pct}%" if health_pct else "",
            f"Продление: {client.renewal_date}" if client.renewal_date else "",
            "",
            "**Открытые задачи:**",
        ]
        for t in tasks[:5]:
            lines.append(f"• [{t.status}] {t.title}")
        lines.append("\n**Последние встречи:**")
        for m in meetings[:3]:
            lines.append(f"• {m.date}: {m.meeting_type}")
        text = "\n".join(l for l in lines if l is not None)

    return {"text": text, "client_name": client.name}


# ── Focus list for today dashboard ───────────────────────────────────────────

@router.get("/api/today/focus")
def get_today_focus(
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    u = _user(auth_token)
    if not u:
        raise HTTPException(status_code=401, detail="Unauthorized")

    email = u.get("sub") or u.get("email") or ""
    role  = u.get("role") or "manager"

    q = db.query(Client)
    if role not in ("admin", "head"):
        q = q.filter(Client.manager_email == email)

    clients = q.filter(Client.status != "churned").all()

    today = date.today()
    focus = []
    for c in clients:
        reasons = []
        priority = 0

        if c.status == "risk":
            reasons.append("🔴 Статус: риск")
            priority = max(priority, 100)
        elif c.status == "warn":
            reasons.append("🟡 Статус: внимание")
            priority = max(priority, 60)

        if c.health_score and float(c.health_score) < 0.55:
            reasons.append(f"⚠ Health {round(float(c.health_score)*100)}%")
            priority = max(priority, 80)

        if c.renewal_date:
            days = (c.renewal_date - today).days
            if 0 <= days <= 30:
                reasons.append(f"📅 Продление через {days}д")
                priority = max(priority, 70)
            elif days < 0:
                reasons.append("‼ Продление просрочено")
                priority = max(priority, 90)

        # Last meeting check
        last_m = (
            db.query(Meeting)
            .filter(Meeting.client_id == c.id)
            .order_by(Meeting.date.desc())
            .first()
        )
        if last_m and last_m.date:
            m_date = last_m.date if isinstance(last_m.date, date) else last_m.date.date()
            days_ago = (today - m_date).days
            if days_ago > 21:
                reasons.append(f"📵 Нет встреч {days_ago}д")
                priority = max(priority, 50)

        # Overdue tasks
        overdue_count = (
            db.query(Task)
            .filter(
                Task.client_id == c.id,
                Task.status.in_(["plan", "in_progress"]),
                Task.due_date < today,
            )
            .count()
        )
        if overdue_count:
            reasons.append(f"⏰ {overdue_count} просроч. задач")
            priority = max(priority, 55)

        if reasons:
            focus.append({
                "id":           c.id,
                "name":         c.name,
                "segment":      c.segment or "",
                "status":       c.status or "ok",
                "health_score": float(c.health_score) if c.health_score else None,
                "reasons":      reasons,
                "priority":     priority,
                "last_meeting": str(last_m.date) if last_m and last_m.date else None,
            })

    focus.sort(key=lambda x: -x["priority"])
    return {"items": focus[:15]}
