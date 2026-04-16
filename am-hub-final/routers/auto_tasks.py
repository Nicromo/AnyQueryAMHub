"""Auto-split from misc.py"""
from typing import Optional, List
from datetime import datetime, timedelta
import os, json, logging

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Cookie, Form
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote, ClientHistory,
)
from auth import decode_access_token, hash_password, verify_password, log_audit
from deps import require_user, require_admin, optional_user
from error_handlers import log_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

@router.get("/api/auto-tasks/rules")
async def api_auto_task_rules_list(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    from sqlalchemy import or_
    rules = db.query(AutoTaskRule).filter(
        or_(AutoTaskRule.user_id == user.id, AutoTaskRule.user_id.is_(None))
    ).order_by(AutoTaskRule.created_at.desc()).all()
    return {"rules": [{"id":r.id,"name":r.name,"trigger":r.trigger,"trigger_config":r.trigger_config,
                        "segment_filter":r.segment_filter,"task_title":r.task_title,
                        "task_description":r.task_description,"task_priority":r.task_priority,
                        "task_due_days":r.task_due_days,"task_type":r.task_type,
                        "is_active":r.is_active,"created_at":r.created_at.isoformat() if r.created_at else None}
                       for r in rules]}




@router.post("/api/auto-tasks/rules")
async def api_auto_task_rules_create(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    body = await request.json()
    from models import AutoTaskRule
    rule = AutoTaskRule(user_id=user.id, **{k:v for k,v in body.items()
                         if k in ("name","trigger","trigger_config","segment_filter","task_title",
                                  "task_description","task_priority","task_due_days","task_type","is_active")})
    db.add(rule); db.commit(); db.refresh(rule)
    return {"ok": True, "id": rule.id}




@router.put("/api/auto-tasks/rules/{rule_id}")
async def api_auto_task_rules_update(rule_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    from sqlalchemy.orm.attributes import flag_modified
    rule = db.query(AutoTaskRule).filter(AutoTaskRule.id == rule_id).first()
    if not rule: raise HTTPException(status_code=404)
    body = await request.json()
    for k, v in body.items():
        if hasattr(rule, k): setattr(rule, k, v)
    db.commit()
    return {"ok": True}




@router.patch("/api/auto-tasks/rules/{rule_id}")
async def api_auto_task_rules_patch(rule_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    rule = db.query(AutoTaskRule).filter(AutoTaskRule.id == rule_id).first()
    if not rule: raise HTTPException(status_code=404)
    body = await request.json()
    for k, v in body.items():
        if hasattr(rule, k): setattr(rule, k, v)
    db.commit()
    return {"ok": True}




@router.delete("/api/auto-tasks/rules/{rule_id}")
async def api_auto_task_rules_delete(rule_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    rule = db.query(AutoTaskRule).filter(AutoTaskRule.id == rule_id).first()
    if rule: db.delete(rule); db.commit()
    return {"ok": True}




@router.post("/api/auto-tasks/rules/{rule_id}/test")
async def api_auto_task_rules_test(rule_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Тестовый прогон правила — создаёт задачи для подходящих клиентов."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    rule = db.query(AutoTaskRule).filter(AutoTaskRule.id == rule_id).first()
    if not rule: raise HTTPException(status_code=404)

    q = db.query(Client)
    if user.role == "manager": q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    segs = rule.segment_filter or []
    if segs: clients = [c for c in clients if c.segment in segs]

    cfg = rule.trigger_config or {}
    triggered = []
    now = datetime.utcnow()

    for c in clients:
        match = False
        if rule.trigger == "health_drop":
            threshold = cfg.get("threshold", 50)
            match = (c.health_score or 0) < threshold
        elif rule.trigger == "days_no_contact":
            days = cfg.get("days", 30)
            last = c.last_meeting_date or c.last_checkup
            match = not last or (now - last).days >= days
        elif rule.trigger == "checkup_due":
            interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
            last = c.last_meeting_date or c.last_checkup
            match = not last or (now - last).days >= interval
        if match:
            triggered.append(c)

    created = 0
    for c in triggered[:10]:  # Лимит 10 за тест
        due = now + timedelta(days=rule.task_due_days or 3)
        task = Task(
            client_id=c.id, title=rule.task_title,
            description="[Автозадача: " + rule.name + "]\n" + (rule.task_description or ""),
            status="plan", priority=rule.task_priority or "medium",
            due_date=due, created_at=now,
        )
        db.add(task)
        created += 1

    db.commit()
    return {"ok": True, "triggered": len(triggered), "created": created}


# ── Followup templates ──────────────────────────────────────────────────────



