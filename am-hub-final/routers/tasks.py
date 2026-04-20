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
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote,
)
from auth import (
    authenticate_user, create_user, create_access_token,
    verify_password, hash_password, log_audit, decode_access_token,
    get_current_user,)
from error_handlers import log_error, handle_db_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

@router.post("/api/tasks")
async def api_create_task(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    data = await request.json()
    task = Task(
        client_id=data["client_id"],
        title=data["title"],
        description=data.get("description", ""),
        status=data.get("status", "plan"),
        priority=data.get("priority", "medium"),
        team=data.get("team", ""),
        task_type=data.get("task_type", ""),
        due_date=datetime.fromisoformat(data["due_date"]) if data.get("due_date") else None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    # WS real-time push
    return {"ok": True, "id": task.id}



@router.put("/api/tasks/{task_id}")
async def api_update_task(task_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
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
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)
    # Ownership: только менеджер клиента или админ могут редактировать задачу.
    if user.role != "admin" and task.client_id:
        client = db.query(Client).filter(Client.id == task.client_id).first()
        if client and client.manager_email and client.manager_email != user.email:
            raise HTTPException(status_code=403, detail="Задача принадлежит другому менеджеру")
    # Белый список полей — блокируем перезапись служебных (id, client_id, roadmap_*).
    allowed = {"title", "description", "status", "priority", "due_date",
               "team", "task_type", "source"}
    for k, v in data.items():
        if k in allowed and hasattr(task, k):
            setattr(task, k, v)
    db.commit()
    return {"ok": True}


# ============================================================================
# API: AI PROCESSING

@router.post("/api/tasks/{task_id}/confirm")
async def api_confirm_task(task_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Подтверждение выполнения задачи."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)

    task.status = "done"
    task.confirmed_at = datetime.now()
    task.confirmed_by = user.email if user else None
    db.commit()
    return {"ok": True}


# ============================================================================
# WORKFLOW: ROADMAP PUSH

@router.post("/api/tasks/{task_id}/push-roadmap")
async def api_push_roadmap(task_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Отправка задачи в Merchrules Roadmap."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    settings = (user.settings or {}) if user else {}
    mr = settings.get("merchrules", {})
    login = mr.get("login") or _env("MERCHRULES_LOGIN")
    password = mr.get("password") or _env("MERCHRULES_PASSWORD")
    base_url = _env("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")

    if not login or not password:
        return {"error": "Нужны креды Merchrules (Настройки → Креды)"}

    client = db.query(Client).filter(Client.id == task.client_id).first()
    if not client or not client.merchrules_account_id:
        return {"error": "У клиента нет merchrules_account_id — синхронизируйте клиента сначала"}

    import httpx, io
    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            # Авторизация — перебираем поля
            token = None
            for field in ("email", "login", "username"):
                try:
                    r = await hx.post(
                        f"{base_url}/backend-v2/auth/login",
                        json={field: login, "password": password},
                        timeout=10,
                    )
                    if r.status_code == 200:
                        token = r.json().get("token") or r.json().get("access_token") or r.json().get("accessToken")
                        if token:
                            break
                except Exception:
                    continue

            if not token:
                return {"error": "Ошибка авторизации Merchrules — проверьте логин/пароль"}

            headers = {"Authorization": f"Bearer {token}"}

            # Пробуем JSON API сначала
            task_payload = {
                "title": task.title,
                "description": task.description or "",
                "status": task.status,
                "priority": task.priority or "medium",
                "site_id": client.merchrules_account_id,
            }
            if task.team:
                task_payload["team"] = task.team
            if task.due_date:
                task_payload["due_date"] = task.due_date.strftime("%Y-%m-%d")

            resp = await hx.post(
                f"{base_url}/backend-v2/tasks",
                json=task_payload,
                headers=headers,
                timeout=15,
            )

            # Fallback: CSV import
            if resp.status_code not in (200, 201):
                csv_content = "title,description,status,priority,team,due_date\n"
                csv_content += f'"{task.title}","{task.description or ""}",{task.status},{task.priority or "medium"},{task.team or ""},{task.due_date.strftime("%Y-%m-%d") if task.due_date else ""}'
                files = {"file": ("task.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
                resp = await hx.post(
                    f"{base_url}/backend-v2/import/tasks/csv",
                    data={"site_id": client.merchrules_account_id},
                    files=files,
                    headers=headers,
                    timeout=15,
                )

        if resp.status_code in (200, 201):
            task.pushed_to_roadmap = True
            task.roadmap_pushed_at = datetime.now()
            from sqlalchemy.orm.attributes import flag_modified
            db.commit()
            return {"ok": True, "message": f"Задача «{task.title}» отправлена в Roadmap"}
        return {"error": f"Merchrules вернул HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# WORKFLOW: QBR

@router.get("/api/tasks")
async def api_tasks_list(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    client_id: Optional[int] = None,
    source: Optional[str] = None,
    task_type: Optional[str] = None,
):
    """Список задач с фильтрами. Используется для per-client роадмапа."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    q = db.query(Task)
    if client_id is not None:
        q = q.filter(Task.client_id == client_id)
    if source:
        q = q.filter(Task.source == source)
    if task_type:
        q = q.filter(Task.task_type == task_type)
    tasks = q.order_by(Task.created_at.desc()).limit(500).all()
    return {"tasks": [{
        "id": t.id, "title": t.title, "status": t.status,
        "priority": t.priority, "due_date": t.due_date.isoformat() if t.due_date else None,
        "client_id": t.client_id, "source": t.source, "task_type": t.task_type,
        "description": t.description,
    } for t in tasks]}


@router.get("/api/tasks/all")
async def api_tasks_all(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager": q = q.filter(Client.manager_email == user.email)
    tasks = q.order_by(Task.due_date.asc().nullslast(), Task.created_at.desc()).all()
    return {"tasks": [{"id":t.id,"title":t.title,"status":t.status,"priority":t.priority,
                        "due_date":t.due_date.isoformat() if t.due_date else None,
                        "client_id":t.client_id,"client_name":t.client.name if t.client else "—",
                        "description":t.description,"merchrules_task_id":t.merchrules_task_id}
                       for t in tasks]}


# ============================================================================
# MISSING PAGES — страницы из nav без endpoint

@router.patch("/api/tasks/{task_id}/status")
async def api_update_task_status(task_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Изменить статус задачи (для канбан drag-and-drop)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()
    new_status = data.get("status")
    if new_status not in ("plan", "in_progress", "review", "done", "blocked"):
        raise HTTPException(status_code=400, detail="Invalid status")
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)
    task.status = new_status
    if new_status == "done":
        task.confirmed_at = datetime.utcnow()
        task.confirmed_by = user.email if user else None
    db.commit()

    # Push статуса в Merchrules если задача оттуда
    if task.merchrules_task_id and user:
        try:
            settings = (user.settings or {})
            mr = settings.get("merchrules", {})
            login = mr.get("login") or _env("MERCHRULES_LOGIN")
            password = mr.get("password") or _env("MERCHRULES_PASSWORD")
            base_url = _env("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
            if login and password:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=15) as hx:
                    for field in ("email", "login", "username"):
                        r = await hx.post(
                            f"{base_url}/backend-v2/auth/login",
                            json={field: login, "password": password}, timeout=8,
                        )
                        if r.status_code == 200:
                            tok = r.json().get("token") or r.json().get("access_token") or r.json().get("accessToken")
                            if tok:
                                await hx.patch(
                                    f"{base_url}/backend-v2/tasks/{task.merchrules_task_id}",
                                    json={"status": new_status},
                                    headers={"Authorization": f"Bearer {tok}"},
                                    timeout=8,
                                )
                                break
        except Exception as e:
            logger.warning(f"Merchrules task status push failed: {e}")

    return {"ok": True}


# ============================================================================
# MY DAY: TIME TRACKING API

@router.patch("/api/tasks/bulk")
async def api_bulk_edit_tasks(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Массовое редактирование задач."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    data = await request.json()
    task_ids = data.get("task_ids", [])
    updates = {}
    for key in ["status", "priority", "due_date", "team", "task_type"]:
        if key in data and data[key]:
            updates[key] = data[key]
    if not task_ids or not updates:
        return {"error": "Need task_ids and updates"}
    updated = db.query(Task).filter(Task.id.in_(task_ids)).update(updates, synchronize_session=False)
    db.commit()
    return {"ok": True, "updated": updated}


# ============================================================================
# MEETING REMINDER API (for morning alerts)

@router.get("/api/tasks/{task_id}/comments")
async def api_get_task_comments(
    task_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    comments = db.query(TaskComment).filter(TaskComment.task_id == task_id).order_by(TaskComment.created_at.asc()).all()
    return {"comments": [{
        "id": c.id,
        "content": c.content,
        "created_at": c.created_at.strftime("%d.%m.%Y %H:%M") if c.created_at else None,
        "user_id": c.user_id,
    } for c in comments]}



@router.post("/api/tasks/{task_id}/comments")
async def api_add_task_comment(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
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
    content = (data.get("content") or "").strip()
    if not content:
        return {"error": "Пустой комментарий"}

    comment = TaskComment(task_id=task_id, user_id=user.id, content=content)
    db.add(comment)
    db.commit()
    return {"ok": True, "id": comment.id}



@router.delete("/api/tasks/{task_id}/comments/{comment_id}")
async def api_delete_task_comment(
    task_id: int,
    comment_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token:
        raise HTTPException(status_code=401)
    comment = db.query(TaskComment).filter(
        TaskComment.id == comment_id, TaskComment.task_id == task_id
    ).first()
    if not comment:
        raise HTTPException(status_code=404)
    db.delete(comment)
    db.commit()
    return {"ok": True}


# ============================================================================
# KPI МЕНЕДЖЕРА

