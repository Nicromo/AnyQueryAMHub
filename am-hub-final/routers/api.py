"""
API роутеры: followup, settings, AI, integrations, search, calendar, analytics, dashboard, onboarding.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import (
    Client, Task, Meeting, User, QBR, CheckUp,
    ClientNote, FollowupTemplate, Notification
)
from ai_assistant import generate_smart_followup, detect_account_risks
from ai_followup import process_transcript as ai_process_transcript

router = APIRouter(prefix="/api", tags=["api"])

CHECKUP_INTERVALS = {"SS": 180, "SMB": 90, "SME": 60, "ENT": 30, "SME+": 60, "SME-": 60}


def _get_user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user


# ============================================================================
# FOLLOWUP WORKFLOW
# ============================================================================

@router.post("/meetings/{meeting_id}/followup/generate")
async def generate_followup(meeting_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
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


@router.post("/meetings/{meeting_id}/followup/send")
async def send_followup(meeting_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)

    data = await request.json()
    followup_text = data.get("text", meeting.followup_text)
    meeting.followup_status = "sent"
    meeting.followup_text = followup_text
    meeting.followup_sent_at = datetime.now()

    task = Task(
        client_id=meeting.client_id,
        title=f"📧 Фолоуап: {meeting.title or meeting.type}",
        description=followup_text[:500] if followup_text else "",
        status="done", priority="medium", source="followup",
        created_from_meeting_id=meeting.id,
        confirmed_at=datetime.now(), confirmed_by=user.email,
    )
    db.add(task)

    client = db.query(Client).filter(Client.id == meeting.client_id).first()
    if client:
        client.last_meeting_date = meeting.date or datetime.now()

    db.commit()
    return {"ok": True, "task_id": task.id}


@router.post("/meetings/{meeting_id}/followup/skip")
async def skip_followup(meeting_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)
    meeting.followup_status = "skipped"
    meeting.followup_skipped = True
    task = Task(
        client_id=meeting.client_id,
        title=f"📧 Фолоуап: {meeting.title or meeting.type}",
        description="Фолоуап пропущен — требуется заполнить позже",
        status="plan", priority="medium", source="followup",
        created_from_meeting_id=meeting.id,
    )
    db.add(task)
    db.commit()
    return {"ok": True, "task_id": task.id}


# ============================================================================
# SETTINGS
# ============================================================================

@router.post("/settings/creds")
async def save_creds(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()
    settings = user.settings or {}
    for key in ["merchrules", "telegram", "ktalk", "tbank_time"]:
        if key in data:
            settings[key] = data[key]
    user.settings = settings
    db.commit()
    return {"ok": True}


@router.post("/settings/rules")
async def save_rules(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()
    settings = user.settings or {}
    settings["rules"] = data
    user.settings = settings
    db.commit()
    return {"ok": True}


@router.post("/settings/prefs")
async def save_prefs(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()
    settings = user.settings or {}
    settings["preferences"] = data
    user.settings = settings
    db.commit()
    return {"ok": True}


# ============================================================================
# AI
# ============================================================================

@router.post("/ai/process-transcript")
async def process_transcript_api(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    data = await request.json()
    try:
        return ai_process_transcript(data.get("transcript", ""))
    except Exception as e:
        return {"error": str(e)}


@router.post("/ai/generate-followup")
async def ai_generate_followup(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    data = await request.json()
    client = db.query(Client).filter(Client.id == data.get("client_id")).first()
    if not client:
        return {"error": "Client not found"}
    tasks = db.query(Task).filter(Task.client_id == client.id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client.id).order_by(Meeting.date.desc()).limit(3).all()
    try:
        return {"text": generate_smart_followup(client, tasks, meetings)}
    except Exception as e:
        return {"error": str(e)}


@router.post("/ai/auto-qbr/{client_id}")
async def auto_qbr(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """AI-генерация черновика QBR из данных клиента."""
    _get_user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(10).all()

    done_tasks = [t for t in tasks if t.status == "done"]
    blocked_tasks = [t for t in tasks if t.status == "blocked"]
    risks = detect_account_risks(client, tasks, meetings)

    quarter = f"{datetime.now().year}-Q{(datetime.now().month-1)//3+1}"
    return {
        "quarter": quarter,
        "achievements": [t.title for t in done_tasks[:10]],
        "issues": [t.title for t in blocked_tasks[:5]] + risks,
        "metrics": {
            "tasks_completed": len(done_tasks),
            "tasks_open": len([t for t in tasks if t.status in ("plan", "in_progress")]),
            "meetings_count": len(meetings),
            "health_score": client.health_score,
        },
        "summary": f"QBR за {quarter} для {client.name}. Выполнено задач: {len(done_tasks)}. Встреч: {len(meetings)}.",
    }


# ============================================================================
# INTEGRATIONS
# ============================================================================

@router.get("/integrations/test/merchrules")
async def test_merchrules(login: str = "", password: str = ""):
    if not login or not password:
        return {"error": "Need login and password"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post("https://merchrules-qa.any-platform.ru/backend-v2/auth/login", json={"username": login, "password": password})
        return {"ok": True} if resp.status_code == 200 else {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/integrations/test/ktalk")
async def test_ktalk(space: str = "", token: str = ""):
    if not space or not token:
        return {"error": "Need space and token"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get(f"https://{space}.ktalk.ru/api/v1/spaces/{space}/users", headers={"Content-Type": "application/json", "X-Auth-Token": token}, params={"limit": 1})
        return {"ok": True, "space": space} if resp.status_code == 200 else {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/integrations/test/tbank")
async def test_tbank(token: str = ""):
    if not token:
        return {"error": "Need token"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get(f"{os.environ.get('TIME_BASE_URL', 'https://time.tbank.ru')}/api/v1/tickets", params={"limit": 1}, headers={"Authorization": f"Bearer {token}"})
        return {"ok": True} if resp.status_code == 200 else {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/ktalk/notify")
async def ktalk_notify(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    webhook_url = os.environ.get("KTALK_WEBHOOK_URL", "")
    if not webhook_url:
        return {"error": "KTALK_WEBHOOK_URL not set"}
    data = await request.json()
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            await hx.post(webhook_url, json={"text": data.get("text", "")})
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@router.post("/ktalk/followup")
async def ktalk_followup(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    webhook_url = os.environ.get("KTALK_WEBHOOK_URL", "")
    if not webhook_url:
        return {"error": "KTALK_WEBHOOK_URL not set"}
    data = await request.json()
    text = f"📋 **Followup: {data.get('client', '')}**\n\n{data.get('summary', '')}"
    if data.get("tasks"):
        text += "\n\n**Задачи:**\n" + "\n".join(f"• {t}" for t in data["tasks"])
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            await hx.post(webhook_url, json={"text": text})
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@router.get("/tbank/tickets/{client_name}")
async def tbank_tickets(client_name: str, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    if not os.environ.get("TIME_API_TOKEN"):
        return {"error": "TIME_API_TOKEN not set", "tickets": []}
    from integrations.tbank_time import sync_tickets_for_client
    try:
        return await sync_tickets_for_client(client_name)
    except Exception as e:
        return {"error": str(e), "open_count": 0, "total_count": 0, "last_ticket": None}


@router.get("/tbank/tickets")
async def tbank_all_tickets(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    if not os.environ.get("TIME_API_TOKEN"):
        return {"error": "TIME_API_TOKEN not set", "tickets": []}
    from integrations.tbank_time import get_support_tickets
    try:
        all_tickets = []
        for c in db.query(Client).all():
            if c.name:
                tickets = await get_support_tickets(c.name)
                for t in tickets:
                    t["client"] = c.name
                all_tickets.extend(tickets)
        return {"tickets": all_tickets, "total": len(all_tickets)}
    except Exception as e:
        return {"error": str(e), "tickets": [], "total": 0}


# ============================================================================
# GLOBAL SEARCH
# ============================================================================

@router.get("/search")
async def global_search(
    q: str = Query("", min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}

    pattern = f"%{q}%"

    c_q = db.query(Client)
    if user.role == "manager":
        c_q = c_q.filter(Client.manager_email == user.email)
    clients = c_q.filter(Client.name.ilike(pattern)).limit(limit).all()

    t_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        t_q = t_q.filter(Client.manager_email == user.email)
    tasks = t_q.filter(Task.title.ilike(pattern)).limit(limit).all()

    m_q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        m_q = m_q.filter(Client.manager_email == user.email)
    meetings = m_q.filter(Meeting.title.ilike(pattern)).order_by(Meeting.date.desc()).limit(limit).all()

    n_q = db.query(ClientNote).join(Client, ClientNote.client_id == Client.id, isouter=True)
    if user.role == "manager":
        n_q = n_q.filter(Client.manager_email == user.email)
    notes = n_q.filter(ClientNote.content.ilike(pattern)).order_by(ClientNote.is_pinned.desc(), ClientNote.updated_at.desc()).limit(limit).all()

    return {
        "clients": [{"id": c.id, "name": c.name, "segment": c.segment, "url": f"/client/{c.id}", "type": "client"} for c in clients],
        "tasks": [{"id": t.id, "title": t.title, "status": t.status, "client_name": t.client.name if t.client else "—", "url": f"/client/{t.client_id}", "type": "task"} for t in tasks],
        "meetings": [{"id": m.id, "title": m.title or m.type, "date": m.date.isoformat() if m.date else None, "client_name": m.client.name if m.client else "—", "url": f"/client/{m.client_id}", "type": "meeting"} for m in meetings],
        "notes": [{"id": n.id, "content": n.content[:100] + ("..." if len(n.content) > 100 else ""), "client_name": n.client.name if n.client else "—", "url": f"/client/{n.client_id}", "type": "note", "pinned": n.is_pinned} for n in notes],
    }


# ============================================================================
# CALENDAR
# ============================================================================

@router.get("/calendar/events")
async def calendar_events(start: str = "", end: str = "", db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"events": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"events": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"events": []}

    q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if start and end:
        q = q.filter(Meeting.date >= datetime.fromisoformat(start), Meeting.date <= datetime.fromisoformat(end))
    meetings = q.order_by(Meeting.date).all()

    color_map = {"checkup": "#22c55e", "qbr": "#6366f1", "kickoff": "#f97316", "sync": "#3b82f6"}
    events = [{"id": m.id, "title": f"{m.client.name + ': ' if m.client else ''}{m.title or m.type}", "start": m.date.isoformat() if m.date else None, "color": color_map.get(m.type, "#64748b"), "url": f"/client/{m.client_id}", "type": m.type} for m in meetings]

    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(Task.due_date != None, Task.status != "done")
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    if start and end:
        task_q = task_q.filter(Task.due_date >= datetime.fromisoformat(start), Task.due_date <= datetime.fromisoformat(end))
    for t in task_q.all():
        events.append({"id": f"task-{t.id}", "title": f"⏰ {t.title}", "start": t.due_date.isoformat() if t.due_date else None, "color": "#ef4444", "url": f"/client/{t.client_id}", "type": "task"})

    return {"events": events}


# ============================================================================
# ANALYTICS
# ============================================================================

# ============================================================================
# DASHBOARD ACTIONS
# ============================================================================

@router.get("/dashboard/actions")
async def dashboard_actions(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    now = datetime.now()
    actions = []

    for m in db.query(Meeting).filter(Meeting.followup_status == "pending", Meeting.date < now).all():
        client = db.query(Client).filter(Client.id == m.client_id).first()
        actions.append({"type": "followup", "priority": "high", "meeting_id": m.id, "client_name": client.name if client else "—", "meeting_title": m.title or m.type, "meeting_date": m.date.isoformat() if m.date else None, "days_ago": (now - m.date).days if m.date else 0})

    for m in db.query(Meeting).filter(Meeting.date >= now, Meeting.date < now + timedelta(days=2)).all():
        client = db.query(Client).filter(Client.id == m.client_id).first()
        actions.append({"type": "prep", "priority": "medium", "meeting_id": m.id, "client_name": client.name if client else "—", "meeting_title": m.title or m.type, "meeting_date": m.date.isoformat() if m.date else None, "hours_until": int((m.date - now).total_seconds() / 3600) if m.date else 0})

    for c in db.query(CheckUp).filter(CheckUp.status == "overdue").all():
        client = db.query(Client).filter(Client.id == c.client_id).first()
        actions.append({"type": "checkup", "priority": "high", "checkup_id": c.id, "client_name": client.name if client else "—", "checkup_type": c.type, "scheduled_date": c.scheduled_date.isoformat() if c.scheduled_date else None})

    for c in db.query(Client).filter(Client.next_qbr_date != None, Client.next_qbr_date < now).all():
        actions.append({"type": "qbr", "priority": "high", "client_id": c.id, "client_name": c.name, "next_qbr_date": c.next_qbr_date.isoformat() if c.next_qbr_date else None})

    return {"actions": actions, "total": len(actions)}


# ============================================================================
# ONBOARDING
# ============================================================================

@router.post("/onboarding/complete")
async def complete_onboarding(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    settings = user.settings or {}
    settings["onboarding_complete"] = True
    user.settings = settings
    db.commit()
    return {"ok": True}


@router.get("/onboarding/status")
async def onboarding_status(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"onboarding_complete": True}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"onboarding_complete": True}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    settings = (user.settings or {}) if user else {}
    return {"onboarding_complete": settings.get("onboarding_complete", False)}


# ============================================================================
# ADMIN
# ============================================================================

@router.post("/admin/reset-data")
async def reset_data(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if user.role != "admin":
        raise HTTPException(status_code=403)
    for model in [Task, Meeting, CheckUp, QBR, AccountPlan, Client]:
        db.query(model).delete()
    db.commit()
    return {"ok": True, "message": "Все данные очищены"}


# ============================================================================
# FOLLOWUP TEMPLATES
# ============================================================================

@router.get("/followup-templates")
async def get_followup_templates(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"templates": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"templates": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"templates": []}
    tpls = db.query(FollowupTemplate).filter(FollowupTemplate.user_id == user.id).order_by(FollowupTemplate.name).all()
    return {"templates": [{"id": t.id, "name": t.name, "content": t.content, "category": t.category} for t in tpls]}


@router.post("/followup-templates")
async def create_followup_template(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()
    tpl = FollowupTemplate(user_id=user.id, name=data.get("name", ""), content=data.get("content", ""), category=data.get("category", "general"))
    db.add(tpl)
    db.commit()
    return {"ok": True, "id": tpl.id}


@router.delete("/followup-templates/{tpl_id}")
async def delete_followup_template(tpl_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    tpl = db.query(FollowupTemplate).filter(FollowupTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(status_code=404)
    db.delete(tpl)
    db.commit()
    return {"ok": True}


# ============================================================================
# MY DAY SCHEDULE & DRAFTS
# ============================================================================

@router.post("/my-day/schedule")
async def save_my_day_schedule(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()
    settings = user.settings or {}
    settings["my_day_schedule"] = data.get("schedule", [])
    settings["my_day_date"] = data.get("date")
    user.settings = settings
    db.commit()
    return {"ok": True}


@router.get("/my-day/schedule")
async def get_my_day_schedule(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"schedule": [], "date": None}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"schedule": [], "date": None}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    settings = (user.settings or {}) if user else {}
    return {"schedule": settings.get("my_day_schedule", []), "date": settings.get("my_day_date")}


@router.post("/drafts")
async def save_draft(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()
    settings = user.settings or {}
    drafts = settings.get("drafts", [])
    drafts.append({**data, "saved_at": datetime.utcnow().isoformat(), "user_id": user.id})
    settings["drafts"] = drafts[-50:]
    user.settings = settings
    db.commit()
    return {"ok": True}


@router.get("/drafts")
async def get_drafts(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"drafts": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"drafts": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    settings = (user.settings or {}) if user else {}
    return {"drafts": settings.get("drafts", [])}
