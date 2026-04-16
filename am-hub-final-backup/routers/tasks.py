"""
API роутер: задачи (CRUD, confirm, push-roadmap, kanban, bulk).
"""
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import Client, Task, User

router = APIRouter(prefix="/api", tags=["tasks"])

VALID_STATUSES = ("plan", "in_progress", "review", "done", "blocked")


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


@router.post("/tasks")
async def create_task(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
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
    return {"ok": True, "id": task.id}


@router.put("/tasks/{task_id}")
async def update_task(task_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    _get_user(auth_token, db)
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)
    data = await request.json()
    for k, v in data.items():
        if hasattr(task, k):
            setattr(task, k, v)
    db.commit()
    return {"ok": True}


@router.patch("/tasks/{task_id}/status")
async def update_task_status(task_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Изменить статус задачи (для канбан drag-and-drop)."""
    user = _get_user(auth_token, db)
    data = await request.json()
    new_status = data.get("status")
    if new_status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)
    task.status = new_status
    if new_status == "done":
        task.confirmed_at = datetime.utcnow()
        task.confirmed_by = user.email
    db.commit()
    return {"ok": True}


@router.post("/tasks/{task_id}/confirm")
async def confirm_task(task_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Подтвердить выполнение задачи."""
    user = _get_user(auth_token, db)
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)
    task.status = "done"
    task.confirmed_at = datetime.now()
    task.confirmed_by = user.email
    db.commit()
    return {"ok": True}


@router.post("/tasks/{task_id}/push-roadmap")
async def push_roadmap(task_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Отправка задачи в Merchrules Roadmap."""
    user = _get_user(auth_token, db)
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)

    settings = user.settings or {}
    mr = settings.get("merchrules", {})
    login = mr.get("login") or os.environ.get("MERCHRULES_LOGIN", "")
    password = mr.get("password") or os.environ.get("MERCHRULES_PASSWORD", "")

    if not login or not password:
        return {"error": "Нужны креды Merchrules (настройки → креды)"}

    client = db.query(Client).filter(Client.id == task.client_id).first()
    if not client or not client.merchrules_account_id:
        return {"error": "У клиента нет merchrules_account_id"}

    import httpx, io
    csv_content = "title,description,status,priority,team,task_type,assignee,product,link,due_date\n"
    csv_content += f'"{task.title}","{task.description or ""}",{task.status},{task.priority},{task.team or ""},{task.task_type or ""},any,,,'
    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            token_resp = await hx.post(
                "https://merchrules-qa.any-platform.ru/backend-v2/auth/login",
                json={"username": login, "password": password},
            )
            if token_resp.status_code != 200:
                return {"error": "Ошибка авторизации Merchrules"}
            token = token_resp.json().get("token")

            files = {"file": ("task.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
            resp = await hx.post(
                "https://merchrules-qa.any-platform.ru/backend-v2/import/tasks/csv",
                data={"site_id": client.merchrules_account_id},
                files=files,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 200:
            task.pushed_to_roadmap = True
            task.roadmap_pushed_at = datetime.now()
            db.commit()
            return {"ok": True, "roadmap": resp.json()}
        return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


@router.patch("/tasks/bulk")
async def bulk_edit_tasks(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Массовое редактирование задач."""
    _get_user(auth_token, db)
    data = await request.json()
    task_ids = data.get("task_ids", [])
    updates = {k: v for k in ["status", "priority", "due_date", "team", "task_type"] if (v := data.get(k))}
    if not task_ids or not updates:
        return {"error": "Need task_ids and updates"}
    updated = db.query(Task).filter(Task.id.in_(task_ids)).update(updates, synchronize_session=False)
    db.commit()
    return {"ok": True, "updated": updated}


@router.get("/kanban")
async def kanban(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Задачи в формате канбан (группировка по статусам)."""
    user = _get_user(auth_token, db)
    q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    tasks = q.order_by(Task.due_date.asc()).all()

    columns = {s: [] for s in VALID_STATUSES}
    for t in tasks:
        col = t.status if t.status in columns else "plan"
        columns[col].append({
            "id": t.id, "title": t.title, "priority": t.priority,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "client_name": t.client.name if t.client else "—",
            "client_id": t.client_id, "team": t.team,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    return columns


@router.get("/meetings/today")
async def meetings_today(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Встречи сегодня с ссылками."""
    from datetime import timezone, timedelta
    from models import Meeting
    user = _get_user(auth_token, db)
    MSK = timezone(timedelta(hours=3))
    now_msk = datetime.now(MSK)
    today = now_msk.date()

    q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    meetings = q.filter(
        Meeting.date >= datetime.combine(today, datetime.min.time()),
        Meeting.date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
    ).all()

    return {"meetings": [{
        "id": m.id, "title": m.title or m.type,
        "time": m.date.strftime("%H:%M") if m.date else "—",
        "client": m.client.name if m.client else "—",
        "client_id": m.client_id,
        "link": m.recording_url or f"/client/{m.client_id}",
        "type": m.type,
    } for m in meetings]}
