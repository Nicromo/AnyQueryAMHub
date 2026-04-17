"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional, List
from datetime import datetime, timedelta
import os
import json
import logging

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote,
)
from auth import (
    authenticate_user, create_user, create_access_token,
    verify_password, hash_password, log_audit, decode_access_token,
    get_current_user,
)
from error_handlers import log_error, handle_db_error
from redis_cache import cache

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

@router.get("/api/clients/{client_id}/qbr")
async def api_get_qbr(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить QBR данные клиента."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    qbr = db.query(QBR).filter(QBR.client_id == client_id).order_by(QBR.date.desc()).first()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id, Meeting.is_qbr == True).order_by(Meeting.date.desc()).limit(5).all()
    tasks = db.query(Task).filter(Task.client_id == client_id, Task.status == "done").order_by(Task.confirmed_at.desc()).limit(20).all()

    return {
        "client": {"id": client.id, "name": client.name, "segment": client.segment},
        "current_qbr": {
            "id": qbr.id if qbr else None,
            "quarter": qbr.quarter if qbr else None,
            "status": qbr.status if qbr else "draft",
            "metrics": qbr.metrics if qbr else {},
            "summary": qbr.summary if qbr else None,
            "achievements": qbr.achievements if qbr else [],
            "issues": qbr.issues if qbr else [],
            "next_goals": qbr.next_quarter_goals if qbr else [],
        } if qbr else None,
        "qbr_meetings": [{"id": m.id, "date": m.date.isoformat() if m.date else None, "title": m.title} for m in meetings],
        "completed_tasks": [{"id": t.id, "title": t.title, "confirmed_at": t.confirmed_at.isoformat() if t.confirmed_at else None} for t in tasks],
        "last_qbr_date": client.last_qbr_date.isoformat() if client.last_qbr_date else None,
        "next_qbr_date": client.next_qbr_date.isoformat() if client.next_qbr_date else None,
    }



@router.post("/api/clients/{client_id}/qbr")
async def api_create_qbr(client_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Создать/обновить QBR."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()

    qbr = db.query(QBR).filter(QBR.client_id == client_id).order_by(QBR.date.desc()).first()
    if not qbr:
        qbr = QBR(client_id=client_id, year=datetime.now().year, quarter=f"{datetime.now().year}-Q{(datetime.now().month-1)//3+1}")
        db.add(qbr)

    qbr.status = data.get("status", qbr.status)
    qbr.metrics = data.get("metrics", qbr.metrics)
    qbr.summary = data.get("summary", qbr.summary)
    qbr.achievements = data.get("achievements", qbr.achievements)
    qbr.issues = data.get("issues", qbr.issues)
    qbr.next_quarter_goals = data.get("next_quarter_goals", qbr.next_quarter_goals)
    qbr.key_insights = data.get("key_insights", qbr.key_insights or [])
    qbr.future_work = data.get("future_work", qbr.future_work or [])
    qbr.presentation_url = data.get("presentation_url", qbr.presentation_url)
    qbr.executive_summary = data.get("executive_summary", qbr.executive_summary)
    if data.get("date"):
        qbr.date = datetime.fromisoformat(data["date"])

    # Обновляем клиента
    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        client.last_qbr_date = qbr.date
        # Следующий QBR через 3 месяца
        client.next_qbr_date = qbr.date + timedelta(days=90) if qbr.date else None

    db.commit()

    # Push QBR в Airtable
    if client and client.airtable_record_id and qbr.summary:
        try:
            from airtable_sync import push_qbr_to_airtable
            await push_qbr_to_airtable(
                client_name=client.name,
                quarter=qbr.quarter or "",
                summary=qbr.summary or "",
                achievements=qbr.achievements or [],
            )
        except Exception as e:
            logger.warning(f"Airtable QBR push failed: {e}")

    return {"ok": True, "qbr_id": qbr.id}


# ============================================================================
# WORKFLOW: ACCOUNT PLAN

@router.get("/api/clients/{client_id}/plan")
async def api_get_plan(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить план работы по клиенту."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

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



@router.post("/api/clients/{client_id}/plan")
async def api_save_plan(client_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить план работы по клиенту."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
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
    plan.updated_by = user.email if user else None

    db.commit()
    return {"ok": True}


# ============================================================================
# WORKFLOW: TBANK TICKETS

@router.put("/api/clients/{client_id}/checkup-config")
async def api_save_checkup_config(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Сохраняет конфиг чекапа клиента:
    diginetica_api_key, site_url, checkup_queries (top/random/zero/zeroquery)
    """
    user = _checkup_auth(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    body = await request.json()
    meta = dict(client.integration_metadata or {})

    if "diginetica_api_key" in body:
        meta["diginetica_api_key"] = body["diginetica_api_key"]
    if "site_url" in body:
        meta["site_url"] = body["site_url"]
    if "checkup_queries" in body:
        meta["checkup_queries"] = body["checkup_queries"]
    if "merch_rules" in body:
        meta["merch_rules"] = body["merch_rules"]

    from sqlalchemy.orm.attributes import flag_modified
    client.integration_metadata = meta
    flag_modified(client, "integration_metadata")
    db.commit()
    return {"ok": True}


# ============================================================================
# WORKFLOW: DASHBOARD ACTIONS

@router.get("/api/clients/{client_id}/checkup-info")
async def api_client_checkup_info(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Публичная информация о checkup-конфиге клиента.
    API key — только маска для менеджеров, полный — для admin.
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

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    meta = client.integration_metadata or {}
    api_key = meta.get("diginetica_api_key", "")

    # Менеджер видит только маску: первые 4 и последние 4 символа
    if user.role != "admin" and api_key:
        masked = api_key[:4] + "••••••••••••••••••••" + api_key[-4:] if len(api_key) > 8 else "••••••••"
        api_key_display = masked
    else:
        api_key_display = api_key

    cq = meta.get("checkup_queries", {})
    return {
        "client_id": client_id,
        "client_name": client.name,
        "has_api_key": bool(meta.get("diginetica_api_key")),
        "api_key_display": api_key_display,
        "api_key_full": api_key if user.role == "admin" else None,
        "site_url": client.domain or meta.get("site_url", ""),
        "is_admin": user.role == "admin",
        "checkup_queries": cq,
        "queries_count": {k: len(v) for k, v in cq.items() if isinstance(v, list)},
    }


@router.post("/api/clients/{client_id}/notes")
async def api_create_note(client_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Создать заметку к клиенту."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()
    note = ClientNote(client_id=client_id, user_id=user.id, content=data.get("content", ""), is_pinned=data.get("pinned", False))
    db.add(note)
    db.commit()
    return {"ok": True, "id": note.id}



@router.get("/api/clients/{client_id}/notes")
async def api_get_notes(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить заметки клиента."""
    if not auth_token:
        return {"notes": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notes": []}
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).order_by(ClientNote.is_pinned.desc(), ClientNote.updated_at.desc()).all()
    return {"notes": [{"id": n.id, "content": n.content, "pinned": n.is_pinned, "created_at": n.created_at.strftime("%d.%m.%Y %H:%M") if n.created_at else None, "updated_at": n.updated_at.strftime("%d.%m.%Y %H:%M") if n.updated_at else None, "user_id": n.user_id} for n in notes]}



@router.put("/api/clients/notes/{note_id}")
async def api_update_note(note_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Обновить заметку."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
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



@router.delete("/api/clients/notes/{note_id}")
async def api_delete_note(note_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Удалить заметку."""
    if not auth_token:
        raise HTTPException(status_code=401)
    note = db.query(ClientNote).filter(ClientNote.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404)
    db.delete(note)
    db.commit()
    return {"ok": True}


# ============================================================================
# KANBAN API

@router.get("/api/clients/{client_id}/timeline")
async def api_client_timeline(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Таймлайн клиента: встречи, задачи, заметки."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    events = []

    # Встречи
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(20).all()
    for m in meetings:
        events.append({
            "type": "followup" if m.followup_status == "sent" else "meeting",
            "date": m.date.strftime("%d.%m.%Y") if m.date else "—",
            "iso_date": m.date.isoformat() if m.date else "",
            "icon": "📅",
            "title": m.title or m.type,
            "desc": (m.summary or "")[:100] + ("..." if m.summary and len(m.summary) > 100 else ""),
        })

    # Задачи
    tasks = db.query(Task).filter(Task.client_id == client_id).order_by(Task.created_at.desc()).limit(20).all()
    for t in tasks:
        events.append({
            "type": "task",
            "date": t.created_at.strftime("%d.%m.%Y") if t.created_at else "—",
            "iso_date": t.created_at.isoformat() if t.created_at else "",
            "icon": {"plan": "📝", "in_progress": "🔄", "done": "✅", "blocked": "🔴", "review": "👀"}.get(t.status, "📋"),
            "title": t.title,
            "desc": f"Статус: {t.status}" + (f" · {t.priority}" if t.priority else ""),
        })

    # Заметки
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).order_by(ClientNote.updated_at.desc()).limit(10).all()
    for n in notes:
        events.append({
            "type": "note",
            "date": n.updated_at.strftime("%d.%m.%Y") if n.updated_at else "—",
            "iso_date": n.updated_at.isoformat() if n.updated_at else "",
            "icon": "📌" if n.is_pinned else "📝",
            "title": "Заметка" + (" (закреплена)" if n.is_pinned else ""),
            "desc": n.content[:100] + ("..." if len(n.content) > 100 else ""),
        })

    # Фолоуапы как отдельные события
    followups = db.query(Meeting).filter(
        Meeting.client_id == client_id,
        Meeting.followup_status == "sent",
        Meeting.followup_text != None,
    ).order_by(Meeting.followup_sent_at.desc()).limit(10).all()
    for m in followups:
        events.append({
            "type": "followup",
            "date": m.followup_sent_at.strftime("%d.%m.%Y") if m.followup_sent_at else "—",
            "iso_date": m.followup_sent_at.isoformat() if m.followup_sent_at else "",
            "icon": "✍️",
            "title": f"Фолоуап: {m.title or m.type}",
            "desc": (m.followup_text or "")[:100],
        })

    # Сортировка по iso_date
    events.sort(key=lambda e: e.get("iso_date", ""), reverse=True)

    return {"events": events[:50]}



@router.get("/api/clients/{client_id}/tasks-status")
async def api_client_tasks_status(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Лёгкий polling — только статусы задач для real-time обновления."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    return {"tasks": [{"id": t.id, "status": t.status} for t in tasks]}

CHECKUP_INTERVALS = {"SS": 180, "SMB": 90, "SME": 60, "ENT": 30, "SME+": 60, "SME-": 60}


@router.get("/api/clients")
@cache(ttl=60)
async def api_clients_list(
    segment: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """API список клиентов с фильтрами."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if segment:
        q = q.filter(Client.segment == segment)
    if search:
        q = q.filter(Client.name.ilike(f"%{search}%"))
    clients = q.order_by(Client.name).all()

    return {"clients": [{
        "id": c.id, "name": c.name, "segment": c.segment,
        "health_score": c.health_score, "manager_email": c.manager_email,
        "merchrules_account_id": c.merchrules_account_id,
        "last_meeting_date": c.last_meeting_date.isoformat() if c.last_meeting_date else None,
    } for c in clients]}


# ============================================================================
# EXPORT: PDF REPORT
# ============================================================================

# ============================================================================
# EXPORT: PDF отчёт по клиенту

@router.get("/api/clients/{client_id}/export/pdf")
async def api_export_client_pdf(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Экспорт карточки клиента в PDF."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client_id).order_by(Task.created_at.desc()).limit(50).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(20).all()
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).order_by(ClientNote.updated_at.desc()).limit(10).all()
    plan = db.query(AccountPlan).filter(AccountPlan.client_id == client_id).first()
    qbr = db.query(QBR).filter(QBR.client_id == client_id).order_by(QBR.date.desc()).first()

    now_str = datetime.now().strftime("%d.%m.%Y")
    health_pct = int((client.health_score or 0) * 100)
    health_color = "#22c55e" if health_pct >= 70 else ("#eab308" if health_pct >= 40 else "#ef4444")

    # Задачи по статусам
    task_rows = ""
    for t in tasks:
        status_colors = {"plan": "#64748b", "in_progress": "#6366f1", "review": "#eab308", "done": "#22c55e", "blocked": "#ef4444"}
        color = status_colors.get(t.status or "plan", "#64748b")
        due = t.due_date.strftime("%d.%m.%Y") if t.due_date else "—"
        task_rows += f"""<tr>
            <td>{t.title or ''}</td>
            <td><span style="color:{color};font-weight:600;">{t.status or ''}</span></td>
            <td>{t.priority or ''}</td>
            <td>{t.team or '—'}</td>
            <td>{due}</td>
        </tr>"""

    # Встречи
    meeting_rows = ""
    for m in meetings:
        date_str = m.date.strftime("%d.%m.%Y %H:%M") if m.date else "—"
        followup = "✅" if m.followup_status == "sent" else ("⏳" if m.followup_status == "pending" else "—")
        meeting_rows += f"""<tr>
            <td>{date_str}</td>
            <td>{m.type or ''}</td>
            <td>{m.title or ''}</td>
            <td>{followup}</td>
        </tr>"""

    # Заметки
    notes_html = ""
    for n in notes:
        pin = "📌 " if n.is_pinned else ""
        date_str = n.updated_at.strftime("%d.%m.%Y") if n.updated_at else ""
        notes_html += f'<div style="margin-bottom:8px;padding:8px 12px;background:#f8fafc;border-left:3px solid #e2e8f0;border-radius:4px;"><div style="font-size:12px;white-space:pre-wrap;">{pin}{n.content}</div><div style="font-size:10px;color:#94a3b8;margin-top:4px;">{date_str}</div></div>'

    # Цели из плана
    goals_html = ""
    if plan and plan.quarterly_goals:
        for g in (plan.quarterly_goals or [])[:5]:
            if isinstance(g, dict):
                goals_html += f'<li>{g.get("goal", str(g))}</li>'
            else:
                goals_html += f'<li>{g}</li>'

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<style>
  @page {{ margin: 20mm 15mm; }}
  body {{ font-family: 'Arial', sans-serif; font-size: 12px; color: #1e293b; line-height: 1.5; }}
  h1 {{ font-size: 22px; font-weight: 700; color: #0f172a; margin: 0 0 4px; }}
  h2 {{ font-size: 14px; font-weight: 600; color: #1e293b; margin: 20px 0 8px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; padding-bottom: 16px; border-bottom: 2px solid #6366f1; }}
  .meta {{ font-size: 11px; color: #64748b; margin-top: 4px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; background: #ede9fe; color: #6366f1; margin-right: 4px; }}
  .health {{ font-size: 28px; font-weight: 800; color: {health_color}; }}
  .health-label {{ font-size: 10px; color: #64748b; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 11px; }}
  th {{ background: #f1f5f9; padding: 6px 8px; text-align: left; font-weight: 600; color: #475569; font-size: 10px; text-transform: uppercase; letter-spacing: 0.4px; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  .footer {{ margin-top: 24px; padding-top: 8px; border-top: 1px solid #e2e8f0; font-size: 10px; color: #94a3b8; display: flex; justify-content: space-between; }}
  .kpi-row {{ display: flex; gap: 16px; margin-bottom: 16px; }}
  .kpi {{ flex: 1; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 12px; }}
  .kpi-val {{ font-size: 20px; font-weight: 700; color: #0f172a; }}
  .kpi-label {{ font-size: 10px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.4px; margin-top: 2px; }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>{client.name}</h1>
    <div class="meta">
      <span class="badge">{client.segment or '—'}</span>
      Менеджер: {client.manager_email or '—'}
      {'· Домен: ' + client.domain if client.domain else ''}
    </div>
  </div>
  <div style="text-align:right;">
    <div class="health">{health_pct}%</div>
    <div class="health-label">Health Score</div>
    <div style="font-size:10px;color:#94a3b8;margin-top:4px;">Отчёт от {now_str}</div>
  </div>
</div>

<div class="kpi-row">
  <div class="kpi">
    <div class="kpi-val">{sum(1 for t in tasks if t.status in ('plan','in_progress','blocked'))}</div>
    <div class="kpi-label">Открытых задач</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">{sum(1 for t in tasks if t.status == 'done')}</div>
    <div class="kpi-label">Выполнено</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">{len(meetings)}</div>
    <div class="kpi-label">Встреч</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">{client.last_meeting_date.strftime('%d.%m') if client.last_meeting_date else '—'}</div>
    <div class="kpi-label">Последний контакт</div>
  </div>
</div>

{'<h2>Цели на квартал</h2><ul>' + goals_html + '</ul>' if goals_html else ''}

<h2>Задачи</h2>
{'<table><thead><tr><th>Задача</th><th>Статус</th><th>Приоритет</th><th>Команда</th><th>Дедлайн</th></tr></thead><tbody>' + task_rows + '</tbody></table>' if task_rows else '<p style="color:#94a3b8;font-size:11px;">Задач нет</p>'}

<h2>Встречи</h2>
{'<table><thead><tr><th>Дата</th><th>Тип</th><th>Тема</th><th>Фолоуап</th></tr></thead><tbody>' + meeting_rows + '</tbody></table>' if meeting_rows else '<p style="color:#94a3b8;font-size:11px;">Встреч нет</p>'}

{'<h2>Заметки</h2>' + notes_html if notes_html else ''}

{'<h2>QBR · ' + (qbr.quarter or '') + '</h2><p>' + (qbr.summary or '') + '</p>' if qbr and qbr.summary else ''}

<div class="footer">
  <span>AM Hub · {client.name}</span>
  <span>{now_str}</span>
</div>
</body>
</html>"""

    try:
        import weasyprint
        pdf_bytes = weasyprint.HTML(string=html).write_pdf()
        from fastapi.responses import Response
        fname = f"{client.name.replace(' ', '_')}_{now_str.replace('.', '-')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    except Exception as e:
        logger.error(f"PDF export error: {e}")
        return {"error": str(e)}

# ============================================================================
# КЛИЕНТ: редактирование карточки

@router.patch("/api/clients/{client_id}")
async def api_update_client(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Обновить поля клиента (сегмент, имя, домен, health_score)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    data = await request.json()
    allowed = ("name", "segment", "domain", "health_score", "manager_email", "activity_level")
    changed = {}
    for field in allowed:
        if field in data:
            old_val = getattr(client, field)
            new_val = data[field]
            if old_val != new_val:
                setattr(client, field, new_val)
                changed[field] = {"old": old_val, "new": new_val}

    if changed:
        db.commit()
        # Push изменений в Airtable если есть record_id
        if client.airtable_record_id:
            try:
                from airtable_sync import push_client_fields_to_airtable
                # Маппинг полей хаба → поля Airtable (подстраивается под реальную структуру)
                at_fields = {}
                if "segment" in changed:
                    at_fields["Сегмент"] = data["segment"]
                if "health_score" in changed:
                    at_fields["Health Score"] = data["health_score"]
                if "domain" in changed:
                    at_fields["Домен"] = data["domain"]
                if at_fields:
                    await push_client_fields_to_airtable(client.airtable_record_id, at_fields)
            except Exception as e:
                logger.warning(f"Airtable push on client update failed: {e}")

    return {"ok": True, "changed": changed}


# ============================================================================
# ЗАДАЧИ: комментарии

@router.get("/api/clients/{client_id}/onboarding")
async def api_get_onboarding(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Получить чеклист онбординга клиента."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    # Читаем прогресс из account_plan
    plan_data = client.account_plan or {}
    onboarding_progress = plan_data.get("onboarding_progress", {})

    checklist = []
    for item in ONBOARDING_CHECKLIST:
        checklist.append({
            **item,
            "done": onboarding_progress.get(str(item["id"]), False),
        })

    return {"checklist": checklist, "client_id": client_id}



@router.patch("/api/clients/{client_id}/onboarding/{item_id}")
async def api_update_onboarding_item(
    client_id: int,
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Отметить шаг онбординга выполненным."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    data = await request.json()
    done = bool(data.get("done", True))

    plan_data = dict(client.account_plan or {})
    if "onboarding_progress" not in plan_data:
        plan_data["onboarding_progress"] = {}
    plan_data["onboarding_progress"][str(item_id)] = done

    client.account_plan = plan_data
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(client, "account_plan")
    db.commit()

    # Если отмечаем касание в Ktalk — отправляем авто-сообщение
    item = next((i for i in ONBOARDING_CHECKLIST if i["id"] == item_id), None)
    if done and item and item["type"] == "ktalk":
        try:
            from auth import decode_access_token
            user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
            if user:
                settings = user.settings or {}
                kt = settings.get("ktalk", {})
                channel_id = kt.get("followup_channel_id") or kt.get("channel_id")
                token = kt.get("access_token", "")
                if channel_id and token:
                    from integrations.ktalk import send_message
                    await send_message(
                        channel_id,
                        f"📋 Онбординг {client.name}: выполнено — {item['title']}",
                        token,
                    )
        except Exception as e:
            logger.warning(f"Ktalk onboarding notify failed: {e}")

    return {"ok": True, "done": done}


# ============================================================================
# KPI PAGE

@router.get("/api/clients/{client_id}/history")
async def api_client_history(
    client_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    from models import ClientHistory
    history = (db.query(ClientHistory)
               .filter(ClientHistory.client_id == client_id)
               .order_by(ClientHistory.created_at.desc())
               .limit(limit).all())
    return {"history": [
        {"id": h.id, "field": h.field, "old_value": h.old_value, "new_value": h.new_value,
         "event_type": h.event_type, "comment": h.comment,
         "user_name": h.user.name if h.user else "Система",
         "created_at": h.created_at.isoformat()}
        for h in history
    ]}


def log_client_change(db, client_id: int, user_id: Optional[int],
                       field: str, old_val, new_val, event_type: str = "update", comment: str = None):
    """Хелпер для записи истории изменений."""
    from models import ClientHistory
    if str(old_val) == str(new_val):
        return  # нечего логировать
    entry = ClientHistory(
        client_id=client_id, user_id=user_id, field=field,
        old_value=str(old_val) if old_val is not None else None,
        new_value=str(new_val) if new_val is not None else None,
        event_type=event_type, comment=comment,
    )
    db.add(entry)


# ============================================================================
# TELEGRAM SETTINGS + SMART NOTIFICATIONS

@router.get("/api/clients/{client_id}/churn")
async def api_client_churn(
    client_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models import ChurnScore
    cs = db.query(ChurnScore).filter(ChurnScore.client_id == client_id).first()
    if not cs:
        # Считаем на лету
        from churn import calculate_churn_score
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client: raise HTTPException(status_code=404)
        tasks = [{"due_date": t.due_date.isoformat() if t.due_date else None, "status": t.status}
                 for t in db.query(Task).filter(Task.client_id == client_id).all()]
        meetings = [{"date": m.date.isoformat() if m.date else None}
                    for m in db.query(Meeting).filter(Meeting.client_id == client_id).all()]
        result = calculate_churn_score(client, tasks, meetings)
        return {**result, "client_id": client_id, "fresh": True}
    return {
        "score": cs.score, "risk_level": cs.risk_level,
        "factors": cs.factors, "calculated_at": cs.calculated_at.isoformat(),
        "client_id": client_id,
    }



@router.get("/api/clients/duplicates")
async def api_clients_duplicates(
    threshold: int = 75,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Находит похожие названия клиентов через простой алгоритм."""
    if user.role != "admin": raise HTTPException(status_code=403)

    clients = db.query(Client).order_by(Client.name).all()

    def similarity(a: str, b: str) -> int:
        """Простое сходство строк через общие биграммы."""
        a, b = a.lower().strip(), b.lower().strip()
        if a == b: return 100
        def bigrams(s):
            return set(s[i:i+2] for i in range(len(s)-1))
        ba, bb = bigrams(a), bigrams(b)
        if not ba or not bb: return 0
        return int(2 * len(ba & bb) / (len(ba) + len(bb)) * 100)

    groups = []
    used   = set()
    for i, c1 in enumerate(clients):
        if c1.id in used: continue
        group = [c1]
        for c2 in clients[i+1:]:
            if c2.id in used: continue
            sim = similarity(c1.name, c2.name)
            if sim >= threshold:
                group.append(c2)
                used.add(c2.id)
        if len(group) > 1:
            used.add(c1.id)
            t_counts = {c.id: db.query(Task).filter(Task.client_id == c.id).count() for c in group}
            m_counts = {c.id: db.query(Meeting).filter(Meeting.client_id == c.id).count() for c in group}
            sim_score = max(similarity(group[0].name, c.name) for c in group[1:])
            groups.append({
                "similarity": sim_score,
                "clients": [
                    {"id": c.id, "name": c.name, "segment": c.segment, "domain": c.domain,
                     "tasks_count": t_counts[c.id], "meetings_count": m_counts[c.id]}
                    for c in sorted(group, key=lambda x: -(t_counts[x.id] + m_counts[x.id]))
                ]
            })

    groups.sort(key=lambda x: -x["similarity"])
    return {"groups": groups[:50], "total": len(groups)}



@router.post("/api/clients/merge")
async def api_clients_merge(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Слияние двух клиентов. master_id — остаётся, dup_id — удаляется."""
    if user.role != "admin": raise HTTPException(status_code=403)
    body      = await request.json()
    master_id = int(body.get("masterId", 0))
    dup_id    = int(body.get("dupId", 0))
    if not master_id or not dup_id or master_id == dup_id:
        raise HTTPException(status_code=400, detail="Некорректные ID")

    master = db.query(Client).filter(Client.id == master_id).first()
    dup    = db.query(Client).filter(Client.id == dup_id).first()
    if not master or not dup: raise HTTPException(status_code=404)

    # Переносим все связанные данные
    for model, col in [
        (Task,    "client_id"),
        (Meeting, "client_id"),
    ]:
        db.query(model).filter(getattr(model, col) == dup_id).update(
            {col: master_id}, synchronize_session=False
        )

    # Логируем событие
    from models import ClientHistory
    db.add(ClientHistory(
        client_id=master_id, user_id=user.id,
        field="merge", old_value=None,
        new_value=f"Слит с: {dup.name} (id={dup_id})",
        event_type="merge",
    ))
    db.delete(dup)
    db.commit()

    logger.info(f"Merged client {dup_id} ({dup.name}) into {master_id} ({master.name})")
    return {"ok": True, "master_id": master_id}


# ============================================================================
# DATA ENRICHMENT
# ============================================================================

# ============================================================================
# DATA VALIDATION

@router.get("/api/clients/validation")
async def api_clients_validation(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Клиенты с неполными данными."""
    q = db.query(Client)
    if user.role == "manager": q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    issues = []
    for c in clients:
        client_issues = []
        if not c.domain:         client_issues.append("нет домена")
        if not c.segment:        client_issues.append("нет сегмента")
        if not c.manager_email:  client_issues.append("нет менеджера")
        if c.health_score is None: client_issues.append("нет health score")
        if not c.merchrules_account_id: client_issues.append("нет MR ID")
        if client_issues:
            issues.append({"id": c.id, "name": c.name, "issues": client_issues, "count": len(client_issues)})

    issues.sort(key=lambda x: -x["count"])
    return {"total_with_issues": len(issues), "clients": issues[:100]}


# ============================================================================
# JOBS MONITORING
# ============================================================================

_job_log: list = []   # circular buffer for job log
_job_status: dict = {}


def log_job(job_id: str, message: str, level: str = "info"):
    import time
    _job_log.append({"ts": datetime.utcnow().isoformat(), "job": job_id, "message": message, "level": level})
    if len(_job_log) > 200:
        _job_log.pop(0)



@router.patch("/api/clients/{client_id}/revenue")
async def api_set_revenue(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Установить MRR/ARR клиента."""
    body  = await request.json()
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    from sqlalchemy.orm.attributes import flag_modified
    meta = dict(client.integration_metadata or {})
    if "mrr" in body:
        meta["mrr"] = float(body["mrr"])
    if "arr" in body:
        meta["arr"] = float(body.get("arr", meta.get("mrr", 0) * 12))
    if "currency" in body:
        meta["currency"] = body["currency"]
    client.integration_metadata = meta
    flag_modified(client, "integration_metadata")
    db.commit()
    return {"ok": True}



@router.get("/api/clients/validation/issues")
async def api_validation_issues(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Клиенты с неполными/проблемными данными."""
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.limit(500).all()

    issues = []
    for c in clients:
        client_issues = []
        if not c.domain:
            client_issues.append({"field": "domain", "msg": "Нет домена сайта"})
        if not c.segment:
            client_issues.append({"field": "segment", "msg": "Не указан сегмент"})
        if not c.manager_email:
            client_issues.append({"field": "manager", "msg": "Нет ответственного менеджера"})
        meta = c.integration_metadata or {}
        if not meta.get("mrr") and not meta.get("arr"):
            client_issues.append({"field": "revenue", "msg": "Нет данных о выручке"})
        digi = meta.get("diginetica", {})
        if not any(digi.get(p, {}).get("api_key") for p in ("sort", "autocomplete", "recommendations")):
            client_issues.append({"field": "diginetica", "msg": "Нет Diginetica API ключей"})
        if client_issues:
            issues.append({
                "id": c.id, "name": c.name, "segment": c.segment,
                "issues": client_issues, "issues_count": len(client_issues),
            })

    issues.sort(key=lambda x: x["issues_count"], reverse=True)
    return {
        "total_issues": len(issues),
        "total_clients": len(clients),
        "clean_pct": round((len(clients) - len(issues)) / len(clients) * 100, 1) if clients else 0,
        "clients": issues[:100],
    }


# ============================================================================
# DATA ENRICHMENT — обогащение по домену

@router.post("/api/clients/{client_id}/enrich")
async def api_enrich_client(
    client_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Обогащение данных клиента по домену.
    Использует открытые источники: Clearbit Reveal (free tier) / whois / robots.txt
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    domain = client.domain
    if not domain:
        return {"ok": False, "error": "У клиента не указан домен"}

    # Нормализуем домен
    import re as _re
    domain = _re.sub(r'^https?://', '', domain).strip('/').split('/')[0]

    enriched = {}
    errors   = []

    # 1. Clearbit Logo API (бесплатно, без ключа)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as hx:
            logo_url = f"https://logo.clearbit.com/{domain}"
            r = await hx.head(logo_url)
            if r.status_code == 200:
                enriched["logo_url"] = logo_url
    except Exception:
        pass

    # 2. Публичный Clearbit Autocomplete (company name)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as hx:
            r = await hx.get(
                f"https://autocomplete.clearbit.com/v1/companies/suggest?query={domain}",
                headers={"Accept": "application/json"},
            )
            if r.status_code == 200:
                companies = r.json()
                if companies:
                    co = companies[0]
                    enriched["company_name"] = co.get("name")
                    enriched["company_domain"] = co.get("domain")
                    if not enriched.get("logo_url"):
                        enriched["logo_url"] = co.get("logo")
    except Exception as e:
        errors.append(f"clearbit: {e}")

    # 3. Сохраняем в integration_metadata
    if enriched:
        from sqlalchemy.orm.attributes import flag_modified
        meta = dict(client.integration_metadata or {})
        meta["enriched"] = enriched
        meta["enriched_at"] = datetime.utcnow().isoformat()
        if enriched.get("company_name") and not client.name:
            client.name = enriched["company_name"]
        if enriched.get("logo_url"):
            meta["logo_url"] = enriched["logo_url"]
        client.integration_metadata = meta
        flag_modified(client, "integration_metadata")
        db.commit()

    return {"ok": bool(enriched), "enriched": enriched, "errors": errors}



@router.post("/api/clients/enrich-bulk")
async def api_enrich_bulk(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Обогатить всех клиентов с доменом но без лого."""
    q = db.query(Client).filter(Client.domain.isnot(None))
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.limit(50).all()

    enriched = skipped = failed = 0
    import httpx, asyncio
    async with httpx.AsyncClient(timeout=8) as hx:
        for client in clients:
            meta = client.integration_metadata or {}
            if meta.get("enriched_at"):
                skipped += 1
                continue
            domain = client.domain.replace("https://","").replace("http://","").strip("/").split("/")[0]
            try:
                r = await hx.get(f"https://autocomplete.clearbit.com/v1/companies/suggest?query={domain}")
                if r.status_code == 200 and r.json():
                    co = r.json()[0]
                    from sqlalchemy.orm.attributes import flag_modified
                    m = dict(meta)
                    m["enriched"] = {"company_name": co.get("name"), "logo_url": co.get("logo")}
                    m["enriched_at"] = datetime.utcnow().isoformat()
                    if co.get("logo"): m["logo_url"] = co["logo"]
                    client.integration_metadata = m
                    flag_modified(client, "integration_metadata")
                    enriched += 1
                await asyncio.sleep(0.3)
            except Exception:
                failed += 1
    db.commit()
    return {"ok": True, "enriched": enriched, "skipped": skipped, "failed": failed}


# ============================================================================
# CHURN SCORING ENDPOINTS

@router.get("/api/clients/churn-scores")
async def api_churn_scores(
    risk_level: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Список клиентов с churn score."""
    from models import ChurnScore
    q = (db.query(Client, ChurnScore)
         .outerjoin(ChurnScore, Client.id == ChurnScore.client_id))
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if risk_level:
        q = q.filter(ChurnScore.risk_level == risk_level)
    rows = q.order_by(ChurnScore.score.desc().nullslast()).limit(200).all()
    return {"clients": [
        {"id": c.id, "name": c.name, "segment": c.segment,
         "health_score": c.health_score,
         "churn_score": cs.score if cs else None,
         "risk_level":  cs.risk_level if cs else "unknown",
         "factors":     cs.factors if cs else {},
         "calculated_at": cs.calculated_at.isoformat() if cs and cs.calculated_at else None}
        for c, cs in rows
    ]}



@router.post("/api/clients/{client_id}/churn-recalc")
async def api_churn_recalc_single(
    client_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Пересчитать churn score одного клиента."""
    from churn import calculate_churn_score
    from models import ChurnScore
    from sqlalchemy.orm.attributes import flag_modified
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    tasks    = [{"due_date": t.due_date.isoformat() if t.due_date else None, "status": t.status}
                for t in db.query(Task).filter(Task.client_id == client_id).all()]
    meetings = [{"date": m.date.isoformat() if m.date else None}
                for m in db.query(Meeting).filter(Meeting.client_id == client_id).all()]
    result = calculate_churn_score(client, tasks, meetings)
    cs = db.query(ChurnScore).filter(ChurnScore.client_id == client_id).first()
    if cs:
        cs.score = result["score"]; cs.risk_level = result["risk_level"]
        cs.factors = result["factors"]; cs.calculated_at = datetime.utcnow()
        flag_modified(cs, "factors")
    else:
        db.add(ChurnScore(client_id=client_id, **result))
    db.commit()
    return {"ok": True, **result}


# ============================================================================
# EXCEL EXPORT — полноценный с форматированием

@router.post("/api/clients/{client_id}/attachments")
async def api_upload_attachment(
    client_id: int,
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Загрузить файл к клиенту."""
    from storage import upload_file, ALLOWED_MIME
    from models import ClientAttachment
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    file_bytes = await file.read()
    try:
        result = await upload_file(file_bytes, file.filename, client_id, file.content_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    att = ClientAttachment(
        client_id=client_id, user_id=user.id,
        filename=file.filename, file_key=result["key"],
        file_size=result["size"], mime_type=result.get("mime_type"),
    )
    db.add(att); db.commit(); db.refresh(att)
    return {"ok": True, "id": att.id, "filename": att.filename,
            "url": result["url"], "size": att.file_size}



@router.get("/api/clients/{client_id}/attachments")
async def api_list_attachments(
    client_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models import ClientAttachment
    from storage import get_signed_url
    atts = db.query(ClientAttachment).filter(ClientAttachment.client_id == client_id)              .order_by(ClientAttachment.created_at.desc()).all()
    return {"attachments": [
        {"id": a.id, "filename": a.filename,
         "url": get_signed_url(a.file_key),
         "size": a.file_size, "mime_type": a.mime_type,
         "created_at": a.created_at.isoformat(),
         "uploaded_by": a.user.name if a.user else "—"}
        for a in atts
    ]}



