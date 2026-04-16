"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional, List
from datetime import datetime, timedelta
import os
import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Cookie, Form, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db, SessionLocal
from models import (
    AIChat,
    AccountPlan,
    AuditLog,
    CheckUp,
    Client,
    ClientNote,
    FollowupTemplate,
    Meeting,
    Notification,
    QBR,
    SyncLog,
    Task,
    TaskComment,
    User,
    VoiceNote,
)
from auth import (
    authenticate_user, create_user, create_access_token,
    verify_password, hash_password, log_audit, decode_access_token,
)
from error_handlers import log_error, handle_db_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

@router.post("/api/ai/process-transcript")
async def api_process_transcript(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        raise HTTPException(status_code=401)
    data = await request.json()
    transcript = data.get("transcript", "")
    try:
        result = ai_process_transcript(transcript)
        return result
    except Exception as e:
        return {"error": str(e)}



@router.post("/api/ai/generate-followup")
async def api_generate_followup(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        raise HTTPException(status_code=401)
    data = await request.json()
    client_id = data.get("client_id")
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return {"error": "Client not found"}
    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(3).all()
    try:
        text = generate_smart_followup(client, tasks, meetings)
        return {"text": text}
    except Exception as e:
        return {"error": str(e)}



@router.post("/api/ai/generate-prep")
async def api_generate_prep(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Перегенерация AI-подготовки к встрече (вызывается по кнопке на prep-странице)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    data = await request.json()
    client_id = data.get("client_id")
    meeting_type = data.get("meeting_type", "meeting")
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return {"error": "Клиент не найден"}
    tasks = db.query(Task).filter(
        Task.client_id == client_id,
        Task.status.in_(["plan", "in_progress", "blocked"]),
    ).all()
    meetings = db.query(Meeting).filter(
        Meeting.client_id == client_id,
    ).order_by(Meeting.date.desc()).limit(5).all()
    try:
        text = generate_prep_brief(client, tasks, meetings)
        # Добавляем контекст типа встречи
        type_hints = {
            "checkup": "\n\n📋 Тип встречи: ЧЕКАП — фокус на прогрессе по задачам и здоровье аккаунта.",
            "qbr": "\n\n📊 Тип встречи: QBR — квартальный обзор, нужна аналитика и достижения.",
            "onboarding": "\n\n🚀 Тип встречи: ОНБОРДИНГ — первые шаги, знакомство с продуктом.",
            "upsell": "\n\n📈 Тип встречи: АПСЕЙЛ — выявление возможностей для расширения.",
            "sync": "\n\n🔄 Тип встречи: СИНК — текущий статус и оперативные вопросы.",
        }
        text += type_hints.get(meeting_type, "")
        return {"text": text}
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# SETTINGS

@router.post("/api/ai/auto-qbr/{client_id}")
async def api_auto_qbr(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """AI-генерация черновика QBR из данных клиента."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(10).all()

    tasks_done = [t for t in tasks if t.status == "done"]
    tasks_blocked = [t for t in tasks if t.status == "blocked"]

    prompt = f"""Создай черновик QBR для клиента {client.name} ({client.segment or '—'}).

Health Score: {(client.health_score or 0)*100:.0f}%
Задач выполнено: {len(tasks_done)}
Задач заблокировано: {len(tasks_blocked)}
Последние встречи:
{chr(10).join([f"- {m.title or m.type} ({m.date.strftime('%d.%m.%Y') if m.date else ''})" for m in meetings[:5]])}

Ответь JSON:
{"achievements": [...], "issues": [...], "next_quarter_goals": [...], "summary": "..."}"""

    try:
        text = await _ai_chat("", prompt, max_tokens=1500)
        import json, re
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
        else:
            data = {}
    except Exception as e:
        data = {"error": str(e)}

    return data


async def _ai_chat(system: str, user: str, max_tokens: int = 1000) -> str:
    """AI чат через Groq или Qwen."""
    groq_key = env.GROQ_KEY
    qwen_key = env.QWEN_KEY

    if groq_key:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60) as hx:
                resp = await hx.post("https://api.groq.com/openai/v1/chat/completions",
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens, "temperature": 0.1},
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"})
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug(f"Ignored error: {e}")

    if qwen_key:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60) as hx:
                resp = await hx.post("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    json={"model": "qwen-plus", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens, "temperature": 0.1},
                    headers={"Authorization": f"Bearer {qwen_key}", "Content-Type": "application/json"})
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug(f"Ignored error: {e}")

    return "AI недоступен. Настройте GROQ_API_KEY или QWEN_API_KEY."


# ============================================================================
# "TIME TO WRITE" SIGNALS

@router.post("/api/ai/chat")
async def api_ai_chat(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """AI чат с контекстом клиента."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    body = await request.json()
    message   = body.get("message", "").strip()
    client_id = body.get("client_id")
    history   = body.get("history", [])

    if not message:
        return {"reply": "Напишите что-нибудь."}

    # Собираем контекст клиента
    context_parts = []
    client = None
    if client_id:
        client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        open_tasks = db.query(Task).filter(Task.client_id == client.id, Task.status != "done").all()
        last_meeting = db.query(Meeting).filter(Meeting.client_id == client.id).order_by(Meeting.date.desc()).first()
        context_parts.append(f"""Данные клиента:
- Название: {client.name}
- Сегмент: {client.segment or '—'}
- Health Score: {client.health_score or 0:.0f}%
- Домен: {client.domain or '—'}
- Открытых задач: {len(open_tasks)}
- Последняя встреча: {last_meeting.date.strftime('%d.%m.%Y') if last_meeting and last_meeting.date else 'нет'}
- Топ задачи: {', '.join(t.title for t in open_tasks[:3])}""")
    else:
        # Общий контекст менеджера
        q = db.query(Client)
        if user.role == "manager": q = q.filter(Client.manager_email == user.email)
        clients = q.all()
        health_vals = [c.health_score for c in clients if c.health_score is not None]
        avg_h = sum(health_vals) / len(health_vals) if health_vals else 0
        open_t = db.query(Task).join(Client, Task.client_id == Client.id).filter(
            Task.status != "done"
        )
        if user.role == "manager": open_t = open_t.filter(Client.manager_email == user.email)
        context_parts.append(f"""Портфель менеджера {user.name}:
- Клиентов: {len(clients)}
- Средний Health Score: {avg_h:.0f}%
- Открытых задач: {open_t.count()}
- Клиенты с low health: {sum(1 for h in health_vals if h < 50)}""")

    system_prompt = f"""Ты — AI-ассистент AM Hub, помощник аккаунт-менеджера.
Ты помогаешь управлять портфелем клиентов, составлять планы, писать фолоуапы и анализировать данные.
Отвечай кратко, конкретно, на русском языке. Используй маркированные списки где уместно.

{chr(10).join(context_parts)}

Сегодня: {datetime.utcnow().strftime('%d.%m.%Y')}"""

    # Groq API
    u_settings = user.settings or {}
    groq_key = u_settings.get("groq", {}).get("api_key") or env.GROQ_KEY
    if not groq_key:
        return {"reply": "AI не настроен. Добавьте GROQ_API_KEY в Settings → AI или в Railway Variables."}

    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-8:]:  # последние 8 сообщений контекста
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"][:1000]})
    messages.append({"role": "user", "content": message})

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as hx:
            r = await hx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": messages,
                      "max_tokens": 800, "temperature": 0.7},
            )
        if r.status_code != 200:
            return {"reply": f"Groq API error {r.status_code}. Проверьте API ключ."}
        reply = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return {"reply": f"Ошибка AI: {e}"}

    # Сохраняем в историю
    from models import AIChat
    for role, text in [("user", message), ("assistant", reply)]:
        db.add(AIChat(client_id=client_id, user_id=user.id, role=role, content=text))
    db.commit()

    return {"reply": reply, "client_name": client.name if client else None}



@router.get("/api/ai/chat/history")
async def api_ai_chat_history(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    from models import AIChat
    from sqlalchemy import func
    # Группируем по session (первое сообщение user за последние 30 дней)
    msgs = (db.query(AIChat)
            .filter(AIChat.user_id == user.id, AIChat.role == "user")
            .order_by(AIChat.created_at.desc()).limit(20).all())
    return {"chats": [{"id": m.id, "first_message": m.content[:60], "created_at": m.created_at.isoformat()} for m in msgs]}


# ============================================================================
# CLIENT HISTORY (audit log)

