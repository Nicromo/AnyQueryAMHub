"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional
from datetime import datetime
import os
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Client, User, Meeting, AutoFollowup, AutoFollowupExecution
from auth import decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


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


def _rule_dict(r: AutoFollowup) -> dict:
    return {
        "id": r.id,
        "created_by": r.created_by,
        "name": r.name,
        "trigger_type": r.trigger_type,
        "trigger_days": r.trigger_days,
        "channel": r.channel,
        "message_template": r.message_template,
        "is_active": r.is_active,
        "target_segment": r.target_segment,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _render_template(template: str, client: Client, manager: Optional[User], meeting: Optional[Meeting] = None) -> str:
    last_meeting_date = ""
    next_meeting_date = ""
    if meeting:
        last_meeting_date = meeting.date.strftime("%d.%m.%Y") if hasattr(meeting, "date") and meeting.date else ""
    health_status = ""
    hs = getattr(client, "health_score", None)
    if hs is not None:
        if hs >= 0.7:
            health_status = "хорошее"
        elif hs >= 0.4:
            health_status = "среднее"
        else:
            health_status = "требует внимания"
    return template.format(
        client_name=client.name or "",
        manager_name=(manager.full_name or manager.email) if manager else "",
        last_meeting_date=last_meeting_date,
        next_meeting_date=next_meeting_date,
        health_status=health_status,
    )


@router.get("/api/auto-followups")
async def api_list_auto_followups(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    q = db.query(AutoFollowup)
    if user.role != "admin":
        q = q.filter(AutoFollowup.created_by == user.email)
    rules = q.order_by(AutoFollowup.created_at.desc()).all()
    return {"rules": [_rule_dict(r) for r in rules]}


@router.post("/api/auto-followups")
async def api_create_auto_followup(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    data = await request.json()
    rule = AutoFollowup(
        created_by=user.email,
        name=data["name"],
        trigger_type=data.get("trigger_type", "after_meeting"),
        trigger_days=int(data.get("trigger_days", 1)),
        channel=data.get("channel", "telegram"),
        message_template=data["message_template"],
        target_segment=data.get("target_segment"),
        is_active=data.get("is_active", True),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"ok": True, "id": rule.id}


@router.patch("/api/auto-followups/{rule_id}")
async def api_update_auto_followup(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    rule = db.query(AutoFollowup).filter(AutoFollowup.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404)
    if user.role != "admin" and rule.created_by != user.email:
        raise HTTPException(status_code=403)
    data = await request.json()
    allowed = {"name", "trigger_type", "trigger_days", "channel", "message_template", "target_segment", "is_active"}
    for k, v in data.items():
        if k in allowed and hasattr(rule, k):
            setattr(rule, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/api/auto-followups/{rule_id}")
async def api_delete_auto_followup(
    rule_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    rule = db.query(AutoFollowup).filter(AutoFollowup.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404)
    if user.role != "admin" and rule.created_by != user.email:
        raise HTTPException(status_code=403)
    db.delete(rule)
    db.commit()
    return {"ok": True}


@router.post("/api/auto-followups/{rule_id}/toggle")
async def api_toggle_auto_followup(
    rule_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    rule = db.query(AutoFollowup).filter(AutoFollowup.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404)
    if user.role != "admin" and rule.created_by != user.email:
        raise HTTPException(status_code=403)
    rule.is_active = not rule.is_active
    db.commit()
    return {"ok": True, "is_active": rule.is_active}


@router.post("/api/auto-followups/{rule_id}/test")
async def api_test_auto_followup(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    rule = db.query(AutoFollowup).filter(AutoFollowup.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404)
    if user.role != "admin" and rule.created_by != user.email:
        raise HTTPException(status_code=403)

    try:
        data = await request.json()
    except Exception:
        data = {}

    client_id = data.get("client_id")
    if client_id:
        client = db.query(Client).filter(Client.id == client_id).first()
    else:
        q = db.query(Client).filter(Client.is_active == True)
        if rule.target_segment:
            q = q.filter(Client.segment == rule.target_segment)
        if user.role != "admin":
            q = q.filter(Client.manager_email == user.email)
        client = q.first()

    if not client:
        return {"error": "No matching client found for preview"}

    meeting = (
        db.query(Meeting)
        .filter(Meeting.client_id == client.id)
        .order_by(Meeting.date.desc())
        .first()
    )
    manager = db.query(User).filter(User.email == client.manager_email).first() if client.manager_email else user

    try:
        rendered = _render_template(rule.message_template, client, manager, meeting)
    except KeyError as e:
        rendered = rule.message_template
        logger.warning(f"Template render error for rule {rule_id}: {e}")

    return {
        "preview": rendered,
        "client_name": client.name,
        "client_id": client.id,
        "channel": rule.channel,
    }


@router.get("/api/auto-followups/{rule_id}/logs")
async def api_auto_followup_logs(
    rule_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    rule = db.query(AutoFollowup).filter(AutoFollowup.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404)
    if user.role != "admin" and rule.created_by != user.email:
        raise HTTPException(status_code=403)
    executions = (
        db.query(AutoFollowupExecution)
        .filter(AutoFollowupExecution.rule_id == rule_id)
        .order_by(AutoFollowupExecution.executed_at.desc())
        .limit(200)
        .all()
    )
    return {"logs": [{
        "id": e.id,
        "client_id": e.client_id,
        "meeting_id": e.meeting_id,
        "status": e.status,
        "channel": e.channel,
        "message_sent": e.message_sent,
        "error": e.error,
        "executed_at": e.executed_at.isoformat() if e.executed_at else None,
    } for e in executions]}
