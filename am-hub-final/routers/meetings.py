from ai_assistant import generate_smart_followup
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
    AccountPlan,
    AuditLog,
    CheckUp,
    Client,
    ClientNote,
    FollowupTemplate,
    Meeting,
    MeetingComment,
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
    get_current_user,
)
from error_handlers import log_error, handle_db_error

from datetime import timezone as _tz
MSK = _tz(timedelta(hours=3))
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

@router.post("/api/meetings")
async def api_create_meeting(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Создать встречу вручную."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    data = await request.json()
    client_id = data.get("client_id")
    title = data.get("title", "").strip()
    meeting_type = data.get("type", "meeting")
    date_str = data.get("date")
    notes = data.get("notes", "")

    if not client_id:
        return {"error": "client_id обязателен"}

    meeting_date = None
    if date_str:
        try:
            meeting_date = datetime.fromisoformat(date_str.replace("Z", ""))
        except Exception:
            return {"error": f"Неверный формат даты: {date_str}"}

    meeting = Meeting(
        client_id=int(client_id),
        title=title or meeting_type,
        type=meeting_type,
        date=meeting_date or datetime.now(),
        source="manual",
        followup_status="pending",
        summary=notes or None,
    )
    db.add(meeting)
    db.flush()

    # Обновляем last_meeting_date у клиента
    client = db.query(Client).filter(Client.id == int(client_id)).first()
    if client and meeting_date:
        if not client.last_meeting_date or meeting_date > client.last_meeting_date:
            client.last_meeting_date = meeting_date

    # Создаём слоты prep/followup
    try:
        from meeting_slots import create_slots_for_meeting
        create_slots_for_meeting(db, meeting)
    except Exception as e:
        logger.warning(f"Slots creation failed: {e}")

    db.commit()
    return {"ok": True, "meeting_id": meeting.id, "message": f"Встреча «{meeting.title}» создана"}



@router.delete("/api/meetings/{meeting_id}")
async def api_delete_meeting(
    meeting_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Удалить встречу."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)
    db.delete(meeting)
    db.commit()
    return {"ok": True}


# ============================================================================
# WORKFLOW: FOLLOWUP

@router.post("/api/meetings/{meeting_id}/followup/generate")
async def api_generate_followup(meeting_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """AI-генерация фолоуапа для встречи."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)

    client = db.query(Client).filter(Client.id == meeting.client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client.id, Task.status.in_(["plan", "in_progress"])).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client.id).order_by(Meeting.date.desc()).limit(3).all()

    try:
        text = generate_smart_followup(client, tasks, meetings)
        meeting.followup_text = text
        meeting.followup_status = "filled"
        db.commit()
        return {"ok": True, "text": text}
    except Exception as e:
        return {"error": str(e)}



@router.post("/api/meetings/{meeting_id}/followup/send")
async def api_send_followup(meeting_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Подтверждение отправки фолоуапа → создаётся задача done."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)

    data = await request.json()
    followup_text = data.get("text", meeting.followup_text)

    meeting.followup_status = "sent"
    meeting.followup_text = followup_text
    meeting.followup_sent_at = datetime.now()

    # Создаём задачу "Фолоуап отправлен" со статусом done
    task = Task(
        client_id=meeting.client_id,
        title=f"📧 Фолоуап: {meeting.title or meeting.type}",
        description=followup_text[:500] if followup_text else "",
        status="done",
        priority="medium",
        source="followup",
        created_from_meeting_id=meeting.id,
        confirmed_at=datetime.now(),
        confirmed_by=user.email if user else None,
    )
    db.add(task)

    # Обновляем last_meeting_date у клиента
    client = db.query(Client).filter(Client.id == meeting.client_id).first()
    if client:
        client.last_meeting_date = meeting.date or datetime.now()

    db.commit()

    # Push в Ktalk — если настроен канал
    if user and followup_text:
        try:
            settings = user.settings or {}
            kt = settings.get("ktalk", {})
            channel_id = kt.get("followup_channel_id") or kt.get("channel_id")
            token = kt.get("access_token", "")
            if channel_id and token:
                from integrations.ktalk import send_followup_to_channel
                await send_followup_to_channel(
                    channel_id=channel_id,
                    client_name=client.name if client else "",
                    followup_text=followup_text,
                    meeting_date=meeting.date,
                    token=token,
                )
        except Exception as e:
            logger.warning(f"Ktalk followup push failed: {e}")

    # Push в Airtable — обновляем дату последней встречи
    if client and client.airtable_record_id:
        try:
            from airtable_sync import sync_meeting_to_airtable
            await sync_meeting_to_airtable(
                record_id=client.airtable_record_id,
                meeting_date=meeting.date or datetime.now(),
                comment=f"Фолоуап отправлен: {(followup_text or '')[:100]}",
            )
        except Exception as e:
            logger.warning(f"Airtable followup sync failed: {e}")

    # Push в Airtable — обновляем дату встречи
    if client and client.airtable_record_id:
        try:
            from integrations.airtable import update_meeting_date
            await update_meeting_date(
                record_id=client.airtable_record_id,
                meeting_date=meeting.date or datetime.now(),
                comment=f"Фолоуап: {(followup_text or '')[:200]}",
            )
        except Exception as e:
            logger.warning(f"Airtable followup push failed: {e}")

    return {"ok": True, "task_id": task.id}



@router.post("/api/meetings/{meeting_id}/followup/skip")
async def api_skip_followup(meeting_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Пропустить фолоуап → создаётся задача plan."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)

    meeting.followup_status = "skipped"
    meeting.followup_skipped = True

    # Создаём задачу "Фолоуап" со статусом plan
    task = Task(
        client_id=meeting.client_id,
        title=f"📧 Фолоуап: {meeting.title or meeting.type}",
        description="Фолоуап пропущен — требуется заполнить позже",
        status="plan",
        priority="medium",
        source="followup",
        created_from_meeting_id=meeting.id,
    )
    db.add(task)
    db.commit()
    return {"ok": True, "task_id": task.id}


# ============================================================================
# WORKFLOW: TASK CONFIRMATION

@router.get("/api/meetings/slots")
async def api_meetings_slots(
    date: Optional[str] = None,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Получить слоты дня (встречи + prep/followup задачи).
    date: ISO строка даты, по умолчанию — сегодня МСК.
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    from meeting_slots import get_day_slots
    target_date = datetime.now(MSK).replace(tzinfo=None)
    if date:
        try:
            target_date = datetime.fromisoformat(date)
        except ValueError:
            pass

    slots = get_day_slots(db, user.email, target_date)
    return {"slots": slots, "date": target_date.strftime("%Y-%m-%d")}



@router.post("/api/meetings/sync-slots")
async def api_sync_meeting_slots(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Принудительно создать слоты для всех предстоящих встреч."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    from meeting_slots import create_slots_for_meeting
    now = datetime.utcnow()
    window_end = now + timedelta(days=7)

    q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
        Meeting.date >= now,
        Meeting.date <= window_end,
    )
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)

    meetings = q.all()
    total = 0
    for m in meetings:
        created = create_slots_for_meeting(db, m)
        total += len(created)

    return {"ok": True, "slots_created": total, "meetings_processed": len(meetings)}



@router.get("/api/meetings/today")
async def api_meetings_today(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить встречи сегодня с ссылками."""
    if not auth_token:
        return {"meetings": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"meetings": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"meetings": []}

    now_msk = datetime.now(MSK)
    today = now_msk.date()
    tomorrow = today + timedelta(days=1)

    q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    meetings = q.filter(
        Meeting.date >= datetime.combine(today, datetime.min.time()),
        Meeting.date < datetime.combine(tomorrow, datetime.min.time()),
    ).all()

    return {
        "meetings": [{
            "id": m.id,
            "title": m.title or m.type,
            "time": m.date.strftime("%H:%M") if m.date else "—",
            "client": m.client.name if m.client else "—",
            "client_id": m.client_id,
            "link": m.recording_url or f"/client/{m.client_id}",
            "type": m.type,
        } for m in meetings]
    }

# ============================================================================
# MISSING ENDPOINTS (referenced from templates)

@router.post("/api/meetings/{meeting_id}/transcribe")
async def api_meeting_transcribe(
    meeting_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI Summary встречи на основе заметок + контекста."""
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting: raise HTTPException(status_code=404)

    body  = await request.json()
    notes = body.get("notes", "") or meeting.notes or ""
    if not notes:
        return {"ok": False, "error": "Нет заметок для анализа. Добавьте заметки встречи."}

    client = db.query(Client).filter(Client.id == meeting.client_id).first()

    u_settings = user.settings or {}
    groq_key   = u_settings.get("groq", {}).get("api_key") or _env("GROQ_API_KEY") or _env("API_GROQ")
    if not groq_key:
        return {"ok": False, "error": "Groq API key не настроен"}

    prompt = f"""Проанализируй заметки встречи и создай структурированное резюме.

Клиент: {client.name if client else "—"}
Дата встречи: {meeting.date.strftime("%d.%m.%Y") if meeting.date else "—"}
Тип: {meeting.meeting_type or "встреча"}

Заметки:
{notes}

Создай резюме в формате:
## Ключевые договорённости
- ...

## Следующие шаги (задачи)
- [ЗАДАЧА] Описание задачи — ответственный
- ...

## Риски и вопросы
- ...

## Краткое резюме (1-2 предложения)
...
"""

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as hx:
            r = await hx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 600, "temperature": 0.3},
            )
        if r.status_code != 200:
            return {"ok": False, "error": f"Groq error: {r.status_code}"}
        summary = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Сохраняем summary в meeting
    from sqlalchemy.orm.attributes import flag_modified
    meeting.summary = summary
    db.commit()

    # Извлекаем задачи из summary и создаём их
    import re
    task_lines = re.findall(r"\[ЗАДАЧА\] (.+)", summary)
    created_tasks = []
    for tl in task_lines[:5]:
        task = Task(client_id=meeting.client_id, title=tl.strip()[:200],
                    status="plan", priority="medium", created_at=datetime.utcnow(),
                    due_date=datetime.utcnow() + timedelta(days=3))
        db.add(task); created_tasks.append(tl.strip())
    if created_tasks: db.commit()

    return {"ok": True, "summary": summary, "tasks_created": created_tasks}


# ============================================================================
# MEETING COMMENTS
# ============================================================================

# ============================================================================
# ONBOARDING WIZARD

@router.post("/api/meetings/{meeting_id}/comments")
async def api_add_meeting_comment(
    meeting_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models import MeetingComment
    body = await request.json()
    content_text = body.get("content", "").strip()
    if not content_text:
        raise HTTPException(status_code=400, detail="Комментарий не может быть пустым")
    c = MeetingComment(meeting_id=meeting_id, user_id=user.id, content=content_text)
    db.add(c); db.commit(); db.refresh(c)
    return {"ok": True, "id": c.id, "content": c.content,
            "created_at": c.created_at.isoformat(), "user_name": user.name}



@router.get("/api/meetings/{meeting_id}/comments")
async def api_meeting_comments(
    meeting_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models import MeetingComment


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

def _get_user(auth_token, db):
    from auth import decode_access_token
    from models import User as _User
    if not auth_token: return None
    payload = decode_access_token(auth_token)
    if not payload: return None
    return db.query(_User).filter(_User.id == int(payload.get("sub", 0))).first()

def _require_user(auth_token, db):
    user = _get_user(auth_token, db)
    if not user: raise HTTPException(status_code=401)
    return user

def _require_admin(auth_token, db):
    user = _get_user(auth_token, db)
    if not user: raise HTTPException(status_code=401)
    if user.role != "admin": raise HTTPException(status_code=403)
    return user

def _checkup_auth(auth_token, db):
    return _require_user(auth_token, db)

_job_status: dict = {}

def log_job(job_id: str, status_val: str, msg: str = "") -> None:
    _job_status[job_id] = {"status": status_val, "msg": msg}

def _job_log(job_id: str) -> dict:
    return _job_status.get(job_id, {})

    comments = db.query(MeetingComment).filter(MeetingComment.meeting_id == meeting_id)                 .order_by(MeetingComment.created_at.asc()).all()
    return {"comments": [
        {"id": c.id, "content": c.content, "created_at": c.created_at.isoformat(),
         "user_name": c.user.name if c.user else "Система"}
        for c in comments
    ]}



