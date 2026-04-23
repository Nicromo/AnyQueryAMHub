from typing import Optional
from datetime import datetime
import os
import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import Client, User, ClientContext, Meeting, NPSEntry, Task, CheckUp

logger = logging.getLogger(__name__)
router = APIRouter()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("API_GROQ", "")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")


def _require_user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user


def _serialize_context(ctx: ClientContext) -> dict:
    return {
        "id": ctx.id,
        "client_id": ctx.client_id,
        "summary": ctx.summary,
        "key_facts": ctx.key_facts or [],
        "pain_points": ctx.pain_points or [],
        "wins": ctx.wins or [],
        "risks": ctx.risks or [],
        "next_steps": ctx.next_steps or [],
        "last_auto_update": ctx.last_auto_update.isoformat() if ctx.last_auto_update else None,
        "last_manual_edit": ctx.last_manual_edit.isoformat() if ctx.last_manual_edit else None,
        "edited_by": ctx.edited_by,
        "sources_used": ctx.sources_used or [],
        "updated_at": ctx.updated_at.isoformat() if ctx.updated_at else None,
    }


def _call_ai(system: str, prompt: str, max_tokens: int = 2000) -> str:
    import httpx
    if GROQ_API_KEY:
        try:
            with httpx.Client(timeout=60) as hx:
                resp = hx.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.1,
                    },
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("Groq failed, trying Qwen: %s", e)

    if QWEN_API_KEY:
        try:
            with httpx.Client(timeout=60) as hx:
                resp = hx.post(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    json={
                        "model": "qwen-plus",
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.1,
                    },
                    headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
                )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("Qwen failed: %s", e)

    raise RuntimeError("No AI provider available (set GROQ_API_KEY or QWEN_API_KEY)")


@router.get("/api/clients/{client_id}/context")
async def get_client_context(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404)
    ctx = db.query(ClientContext).filter(ClientContext.client_id == client_id).first()
    if not ctx:
        return {
            "client_id": client_id,
            "summary": None,
            "key_facts": [],
            "pain_points": [],
            "wins": [],
            "risks": [],
            "next_steps": [],
            "last_auto_update": None,
            "last_manual_edit": None,
            "edited_by": None,
            "sources_used": [],
            "updated_at": None,
        }
    return _serialize_context(ctx)


@router.post("/api/clients/{client_id}/context/regenerate")
async def regenerate_client_context(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404)

    meetings = (
        db.query(Meeting)
        .filter(Meeting.client_id == client_id)
        .order_by(Meeting.date.desc())
        .limit(5)
        .all()
    )
    nps_entry = (
        db.query(NPSEntry)
        .filter(NPSEntry.client_id == client_id)
        .order_by(NPSEntry.recorded_at.desc())
        .first()
    )
    open_tasks = (
        db.query(Task)
        .filter(Task.client_id == client_id, Task.status != "done")
        .order_by(Task.due_date.asc().nullslast())
        .limit(10)
        .all()
    )
    last_checkup = (
        db.query(CheckUp)
        .filter(CheckUp.client_id == client_id)
        .order_by(CheckUp.scheduled_date.desc())
        .first()
    )

    meetings_text = "\n".join(
        f"- {m.date.strftime('%d.%m.%Y') if m.date else '?'}: {m.title or m.type} — {(m.summary or '')[:200]}"
        for m in meetings
    ) or "Нет данных"

    tasks_text = "\n".join(
        f"- [{t.priority}] {t.title} (срок: {t.due_date.strftime('%d.%m.%Y') if t.due_date else '—'})"
        for t in open_tasks
    ) or "Нет открытых задач"

    nps_text = f"NPS: {nps_entry.score} ({nps_entry.recorded_at.strftime('%d.%m.%Y') if nps_entry.recorded_at else '—'}) — {(nps_entry.comment or '')[:200]}" if nps_entry else "NPS нет данных"
    checkup_text = f"Последний чекап: {last_checkup.scheduled_date.strftime('%d.%m.%Y') if last_checkup and last_checkup.scheduled_date else '—'} ({last_checkup.status if last_checkup else '—'})"

    prompt = f"""Клиент: {c.name} | Сегмент: {c.segment or '—'} | Менеджер: {c.manager_email or '—'}
Health Score: {c.health_score or 0:.2f} | MRR: {c.mrr or 0} | GMV: {c.gmv or 0}
Churn risk: {"высокий" if (c.health_score or 0) < 0.4 else "средний" if (c.health_score or 0) < 0.7 else "низкий"}
Контракт до: {c.contract_end.isoformat() if c.contract_end else '—'}
{checkup_text}

Последние встречи (до 5):
{meetings_text}

{nps_text}

Открытые задачи:
{tasks_text}

Верни строго JSON (без обёрток ```json```) следующей структуры:
{{
  "summary": "краткая сводка 3-5 предложений",
  "key_facts": ["факт 1", "факт 2"],
  "pain_points": ["проблема 1", "проблема 2"],
  "wins": ["достижение 1"],
  "risks": ["риск 1", "риск 2"],
  "next_steps": ["следующий шаг 1", "следующий шаг 2"]
}}
Пиши по-русски, кратко, только по делу."""

    system = (
        "Ты — AI-ассистент Account Manager'а AnyQuery. "
        "Анализируй данные клиента и формируй структурированный контекст для работы AM. "
        "Отвечай строго JSON без дополнительного текста."
    )

    try:
        raw = _call_ai(system, prompt, max_tokens=1500)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
    except Exception as e:
        logger.error("Context AI generation failed for client %s: %s", client_id, e)
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

    ctx = db.query(ClientContext).filter(ClientContext.client_id == client_id).first()
    if not ctx:
        ctx = ClientContext(client_id=client_id)
        db.add(ctx)

    ctx.summary = parsed.get("summary") or ""
    ctx.key_facts = parsed.get("key_facts") or []
    ctx.pain_points = parsed.get("pain_points") or []
    ctx.wins = parsed.get("wins") or []
    ctx.risks = parsed.get("risks") or []
    ctx.next_steps = parsed.get("next_steps") or []
    ctx.last_auto_update = datetime.utcnow()
    ctx.sources_used = ["meetings", "nps", "tasks", "health_score", "checkup"]
    ctx.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(ctx)
    return {"ok": True, "context": _serialize_context(ctx)}


@router.patch("/api/clients/{client_id}/context")
async def update_client_context(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404)

    data = await request.json()
    ctx = db.query(ClientContext).filter(ClientContext.client_id == client_id).first()
    if not ctx:
        ctx = ClientContext(client_id=client_id)
        db.add(ctx)

    allowed = {"summary", "key_facts", "pain_points", "wins", "risks", "next_steps"}
    for k, v in data.items():
        if k in allowed and hasattr(ctx, k):
            setattr(ctx, k, v)

    ctx.last_manual_edit = datetime.utcnow()
    ctx.edited_by = user.email
    ctx.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(ctx)
    return {"ok": True, "context": _serialize_context(ctx)}
