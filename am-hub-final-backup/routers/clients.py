"""
API роутер: клиенты (notes, timeline, QBR, plan, export, churn, checkups, bulk).
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import (
    Client, Task, Meeting, User, QBR, AccountPlan,
    ClientNote, CheckUp, Notification, VoiceNote
)

router = APIRouter(prefix="/api", tags=["clients"])

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
# NOTES
# ============================================================================

@router.post("/clients/{client_id}/notes")
async def create_note(client_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()
    note = ClientNote(client_id=client_id, user_id=user.id, content=data.get("content", ""), is_pinned=data.get("pinned", False))
    db.add(note)
    db.commit()
    return {"ok": True, "id": note.id}


@router.get("/clients/{client_id}/notes")
async def get_notes(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"notes": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notes": []}
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).order_by(
        ClientNote.is_pinned.desc(), ClientNote.updated_at.desc()
    ).all()
    return {"notes": [{
        "id": n.id, "content": n.content, "pinned": n.is_pinned,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "user_id": n.user_id,
    } for n in notes]}


@router.put("/clients/notes/{note_id}")
async def update_note(note_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    note = db.query(ClientNote).filter(ClientNote.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404)
    data = await request.json()
    if "content" in data:
        note.content = data["content"]
    if "pinned" in data:
        note.is_pinned = data["pinned"]
    note.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/clients/notes/{note_id}")
async def delete_note(note_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    note = db.query(ClientNote).filter(ClientNote.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404)
    db.delete(note)
    db.commit()
    return {"ok": True}


# ============================================================================
# TIMELINE
# ============================================================================

@router.get("/clients/{client_id}/timeline")
async def client_timeline(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    events = []

    for m in db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(20).all():
        events.append({
            "date": m.date.strftime("%d.%m.%Y") if m.date else "—",
            "icon": "📅", "title": m.title or m.type,
            "desc": (m.summary or "")[:100] + ("..." if m.summary and len(m.summary) > 100 else ""),
        })

    for t in db.query(Task).filter(Task.client_id == client_id).order_by(Task.created_at.desc()).limit(20).all():
        events.append({
            "date": t.created_at.strftime("%d.%m.%Y") if t.created_at else "—",
            "icon": {"plan": "📝", "in_progress": "🔄", "done": "✅", "blocked": "🔴", "review": "👀"}.get(t.status, "📋"),
            "title": t.title,
            "desc": f"Статус: {t.status}" + (f" · {t.priority}" if t.priority else ""),
        })

    for n in db.query(ClientNote).filter(ClientNote.client_id == client_id).order_by(ClientNote.updated_at.desc()).limit(10).all():
        events.append({
            "date": n.updated_at.strftime("%d.%m.%Y") if n.updated_at else "—",
            "icon": "📌" if n.is_pinned else "📝",
            "title": "Заметка" + (" (закреплена)" if n.is_pinned else ""),
            "desc": n.content[:100] + ("..." if len(n.content) > 100 else ""),
        })

    events.sort(key=lambda e: e.get("date", ""), reverse=True)
    return {"events": events[:50]}


# ============================================================================
# QBR
# ============================================================================

@router.get("/clients/{client_id}/qbr")
async def get_qbr(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    qbr = db.query(QBR).filter(QBR.client_id == client_id).order_by(QBR.date.desc()).first()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id, Meeting.is_qbr == True).order_by(Meeting.date.desc()).limit(5).all()
    tasks = db.query(Task).filter(Task.client_id == client_id, Task.status == "done").order_by(Task.confirmed_at.desc()).limit(20).all()

    return {
        "client": {"id": client.id, "name": client.name, "segment": client.segment},
        "current_qbr": {
            "id": qbr.id, "quarter": qbr.quarter, "status": qbr.status,
            "metrics": qbr.metrics, "summary": qbr.summary,
            "achievements": qbr.achievements, "issues": qbr.issues,
            "next_goals": qbr.next_quarter_goals,
        } if qbr else None,
        "qbr_meetings": [{"id": m.id, "date": m.date.isoformat() if m.date else None, "title": m.title} for m in meetings],
        "completed_tasks": [{"id": t.id, "title": t.title, "confirmed_at": t.confirmed_at.isoformat() if t.confirmed_at else None} for t in tasks],
        "last_qbr_date": client.last_qbr_date.isoformat() if client.last_qbr_date else None,
        "next_qbr_date": client.next_qbr_date.isoformat() if client.next_qbr_date else None,
    }


@router.post("/clients/{client_id}/qbr")
async def create_or_update_qbr(client_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    data = await request.json()

    qbr = db.query(QBR).filter(QBR.client_id == client_id).order_by(QBR.date.desc()).first()
    if not qbr:
        now = datetime.now()
        qbr = QBR(client_id=client_id, year=now.year, quarter=f"{now.year}-Q{(now.month-1)//3+1}")
        db.add(qbr)

    for field in ["status", "metrics", "summary", "achievements", "issues", "next_quarter_goals", "key_insights", "future_work", "presentation_url", "executive_summary"]:
        if field in data:
            setattr(qbr, field, data[field])
    if data.get("date"):
        qbr.date = datetime.fromisoformat(data["date"])

    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        client.last_qbr_date = qbr.date
        client.next_qbr_date = qbr.date + timedelta(days=90) if qbr.date else None

    db.commit()
    return {"ok": True, "qbr_id": qbr.id}


# ============================================================================
# ACCOUNT PLAN
# ============================================================================

@router.get("/clients/{client_id}/plan")
async def get_plan(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    plan = db.query(AccountPlan).filter(AccountPlan.client_id == client_id).first()
    if not plan:
        plan = AccountPlan(client_id=client_id)
        db.add(plan)
        db.commit()
    return {
        "quarterly_goals": plan.quarterly_goals or [],
        "action_items": plan.action_items or [],
        "notes": plan.notes,
        "strategy": plan.strategy,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        "updated_by": plan.updated_by,
    }


@router.post("/clients/{client_id}/plan")
async def save_plan(client_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()

    plan = db.query(AccountPlan).filter(AccountPlan.client_id == client_id).first()
    if not plan:
        plan = AccountPlan(client_id=client_id)
        db.add(plan)

    plan.quarterly_goals = data.get("quarterly_goals", plan.quarterly_goals or [])
    plan.action_items = data.get("action_items", plan.action_items or [])
    plan.notes = data.get("notes", plan.notes)
    plan.strategy = data.get("strategy", plan.strategy)
    plan.updated_at = datetime.now()
    plan.updated_by = user.email
    db.commit()
    return {"ok": True}


# ============================================================================
# EXPORT
# ============================================================================

@router.get("/export/client/{client_id}")
async def export_client(client_id: int, fmt: str = "json", db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).all()
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).all()

    data = {
        "client": {"id": client.id, "name": client.name, "segment": client.segment, "health_score": client.health_score, "manager_email": client.manager_email},
        "tasks": [{"id": t.id, "title": t.title, "status": t.status, "priority": t.priority, "due_date": t.due_date.isoformat() if t.due_date else None} for t in tasks],
        "meetings": [{"id": m.id, "title": m.title or m.type, "date": m.date.isoformat() if m.date else None, "type": m.type} for m in meetings],
        "notes": [{"id": n.id, "content": n.content, "pinned": n.is_pinned} for n in notes],
        "exported_at": datetime.utcnow().isoformat(),
    }

    if fmt == "csv":
        import io, csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["type", "id", "title", "date", "details"])
        for t in tasks:
            writer.writerow(["task", t.id, t.title, t.due_date.isoformat() if t.due_date else "", t.status])
        for m in meetings:
            writer.writerow(["meeting", m.id, m.title or m.type, m.date.isoformat() if m.date else "", m.type])
        for n in notes:
            writer.writerow(["note", n.id, n.content[:50], "", "pinned" if n.is_pinned else ""])
        return PlainTextResponse(content=output.getvalue(), headers={"Content-Disposition": f"attachment; filename=client_{client_id}.csv"})

    return data


# ============================================================================
# CHECKUPS
# ============================================================================

@router.get("/checkups")
async def get_checkups(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"overdue": [], "due_soon": [], "upcoming": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"overdue": [], "due_soon": [], "upcoming": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"overdue": [], "due_soon": [], "upcoming": []}

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    now = datetime.now()
    overdue, due_soon, upcoming = [], [], []

    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        days_since = (now - last).days if last else 999
        days_until = interval - days_since

        info = {
            "id": c.id, "name": c.name, "segment": c.segment,
            "days_since": days_since, "days_until": days_until,
            "interval": interval, "last_date": last.isoformat() if last else None,
        }

        if days_until < 0:
            overdue.append(info)
        elif days_until <= 14:
            due_soon.append(info)
        elif days_until <= 30:
            upcoming.append(info)

    overdue.sort(key=lambda x: x["days_until"])
    due_soon.sort(key=lambda x: x["days_until"])
    upcoming.sort(key=lambda x: x["days_until"])
    return {"overdue": overdue, "due_soon": due_soon, "upcoming": upcoming}


@router.post("/checkups/assign")
async def assign_checkup(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    data = await request.json()
    client_id = data.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400)
    meeting_date = datetime.fromisoformat(data["date"]) if data.get("date") else datetime.now()
    meeting = Meeting(client_id=client_id, date=meeting_date, type="checkup", source="internal", title="Чекап")
    db.add(meeting)
    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        client.last_meeting_date = meeting_date
        client.needs_checkup = False
    db.commit()
    return {"ok": True, "meeting_id": meeting.id}


# ============================================================================
# BULK ACTIONS
# ============================================================================

@router.post("/bulk/checkups")
async def bulk_checkups(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    data = await request.json()
    meeting_date = datetime.fromisoformat(data["date"]) if data.get("date") else datetime.now()
    created = 0
    for cid in data.get("client_ids", []):
        client = db.query(Client).filter(Client.id == cid).first()
        if client:
            db.add(Meeting(client_id=cid, date=meeting_date, type="checkup", source="internal", title="Чекап"))
            client.last_meeting_date = meeting_date
            client.needs_checkup = False
            created += 1
    db.commit()
    return {"ok": True, "created": created}


@router.post("/bulk/segment")
async def bulk_segment(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    data = await request.json()
    updated = 0
    for cid in data.get("client_ids", []):
        client = db.query(Client).filter(Client.id == cid).first()
        if client:
            client.segment = data.get("segment", "")
            updated += 1
    db.commit()
    return {"ok": True, "updated": updated}


# ============================================================================
# CHURN RISK
# ============================================================================

@router.get("/churn/risk")
async def churn_risk(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"clients": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"clients": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"clients": []}

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()
    now = datetime.now()
    results = []

    for c in clients:
        score = 0
        reasons = []
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup

        if last and (now - last).days > interval * 2:
            score += 40
            reasons.append(f"Нет контакта {(now-last).days} дн. (норма: {interval})")
        if c.health_score and c.health_score < 0.3:
            score += 30
            reasons.append(f"Низкий health score: {c.health_score:.0%}")
        blocked = db.query(Task).filter(Task.client_id == c.id, Task.status == "blocked").count()
        if blocked > 0:
            score += 15
            reasons.append(f"{blocked} заблокированных задач")
        if db.query(Task).filter(Task.client_id == c.id).count() == 0:
            score += 15
            reasons.append("Нет задач")

        risk = "critical" if score >= 60 else ("medium" if score >= 30 else "low")
        results.append({"id": c.id, "name": c.name, "segment": c.segment, "risk": risk, "score": score, "reasons": reasons})

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"clients": results}


# ============================================================================
# NOTIFICATIONS & INBOX
# ============================================================================

@router.get("/notifications")
async def get_notifications(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"notifications": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notifications": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"notifications": []}

    notifications = []
    now = datetime.now()
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)

    for c in q.all():
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last:
            days_since = (now - last).days
            if days_since > interval:
                notifications.append({"type": "overdue_checkup", "priority": "high", "message": f"Пора написать: {c.name} (последний контакт {days_since} дн. назад)", "client_id": c.id, "client_name": c.name})
            elif days_since > interval - 14:
                notifications.append({"type": "checkup_soon", "priority": "medium", "message": f"Скоро чекап: {c.name} (через {interval - days_since} дн.)", "client_id": c.id, "client_name": c.name})

    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    for t in task_q.filter(Task.status == "blocked").all():
        notifications.append({"type": "blocked_task", "priority": "high", "message": f"Заблокирована задача: {t.title} ({t.client.name if t.client else ''})", "client_id": t.client_id})

    return {"notifications": notifications}


@router.get("/inbox")
async def inbox(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"items": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"items": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"items": []}

    items = []
    now = datetime.now()

    for n in db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).order_by(Notification.created_at.desc()).limit(20).all():
        items.append({"type": "notification", "title": n.title, "message": n.message, "date": n.created_at.isoformat() if n.created_at else None, "priority": n.type})

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    for c in q.all():
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last and (now - last).days > interval:
            items.append({"type": "overdue", "title": f"Просрочен чекап: {c.name}", "message": f"Последний контакт {(now-last).days} дн. назад (норма: {interval})", "date": last.isoformat(), "priority": "high", "client_id": c.id})

    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    for t in task_q.filter(Task.status == "blocked").all():
        items.append({"type": "blocked", "title": f"Заблокирована: {t.title}", "message": t.client.name if t.client else "", "priority": "high", "client_id": t.client_id})

    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return {"items": items[:50]}


@router.post("/inbox/mark-read")
async def mark_read(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).update({"is_read": True})
    db.commit()
    return {"ok": True}


# ============================================================================
# VOICE NOTES
# ============================================================================

@router.post("/voice-notes")
async def create_voice_note(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    data = await request.json()
    vn = VoiceNote(
        client_id=data.get("client_id"), meeting_id=data.get("meeting_id"),
        user_id=user.id, transcription=data.get("text", ""),
        duration_seconds=data.get("duration", 0),
    )
    db.add(vn)
    if data.get("create_task"):
        db.add(Task(client_id=data.get("client_id"), title=f"🎤 {data.get('text', '')[:80]}", description=data.get("text", ""), status="plan", priority="medium", source="voice_note"))
    db.commit()
    return {"ok": True, "id": vn.id}


@router.get("/voice-notes")
async def get_voice_notes(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return {"notes": []}
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notes": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    notes = db.query(VoiceNote).filter(VoiceNote.user_id == user.id).order_by(VoiceNote.created_at.desc()).limit(50).all()
    return {"notes": [{"id": n.id, "text": n.transcription, "duration": n.duration_seconds, "client_id": n.client_id, "created_at": n.created_at.isoformat() if n.created_at else None} for n in notes]}
