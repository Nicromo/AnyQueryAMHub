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

@router.get("/api/followup/templates")
async def api_followup_templates_list(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    tmpls = db.query(FollowupTemplate).filter(FollowupTemplate.user_id == user.id).order_by(FollowupTemplate.created_at.desc()).all()
    return {"templates": [{"id":t.id,"name":t.name,"content":t.content,"category":t.category,"created_at":t.created_at.isoformat() if t.created_at else None} for t in tmpls]}




@router.post("/api/followup/templates")
async def api_followup_templates_create(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    body = await request.json()
    t = FollowupTemplate(user_id=user.id, name=body["name"], content=body["content"], category=body.get("category","general"))
    db.add(t); db.commit(); db.refresh(t)
    return {"ok": True, "id": t.id}




@router.put("/api/followup/templates/{tmpl_id}")
async def api_followup_templates_update(tmpl_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    t = db.query(FollowupTemplate).filter(FollowupTemplate.id == tmpl_id, FollowupTemplate.user_id == user.id).first()
    if not t: raise HTTPException(status_code=404)
    body = await request.json()
    for k in ("name","content","category"):
        if k in body: setattr(t, k, body[k])
    db.commit()
    return {"ok": True}




@router.delete("/api/followup/templates/{tmpl_id}")
async def api_followup_templates_delete(tmpl_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    t = db.query(FollowupTemplate).filter(FollowupTemplate.id == tmpl_id, FollowupTemplate.user_id == user.id).first()
    if t: db.delete(t); db.commit()
    return {"ok": True}




@router.get("/api/followup-templates")
async def api_get_followup_templates(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить шаблоны фолоуапов пользователя."""
    if not auth_token:
        return {"templates": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"templates": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"templates": []}
    templates_list = db.query(FollowupTemplate).filter(FollowupTemplate.user_id == user.id).order_by(FollowupTemplate.name).all()
    return {"templates": [{"id": t.id, "name": t.name, "content": t.content, "category": t.category} for t in templates_list]}




@router.post("/api/followup-templates")
async def api_create_followup_template(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Создать шаблон фолоуапа."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()
    tpl = FollowupTemplate(user_id=user.id, name=data.get("name", ""), content=data.get("content", ""), category=data.get("category", "general"))
    db.add(tpl)
    db.commit()
    return {"ok": True, "id": tpl.id}




@router.delete("/api/followup-templates/{tpl_id}")
async def api_delete_followup_template(tpl_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Удалить шаблон."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    tpl = db.query(FollowupTemplate).filter(FollowupTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(status_code=404)
    # Ownership: только автор или админ.
    if user.role != "admin" and tpl.user_id != user.id:
        raise HTTPException(status_code=403, detail="Шаблон принадлежит другому пользователю")
    db.delete(tpl)
    db.commit()
    return {"ok": True}



@router.post("/api/drafts")
async def api_save_draft(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить черновик (фолоуап, заметка) для офлайн-синхронизации."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()
    settings = user.settings or {}
    drafts = settings.get("drafts", [])
    drafts.append({**data, "saved_at": datetime.utcnow().isoformat(), "user_id": user.id})
    settings["drafts"] = drafts[-50:]  # keep last 50
    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}




@router.get("/api/drafts")
async def api_get_drafts(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить черновики."""
    if not auth_token:
        return {"drafts": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"drafts": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"drafts": []}
    settings = user.settings or {}
    return {"drafts": settings.get("drafts", [])}



