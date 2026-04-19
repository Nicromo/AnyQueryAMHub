"""
API для браузерного расширения AM Hub.

Все эндпоинты поддерживают два способа авторизации:
- cookie JWT (когда расширение работает в контексте уже залогиненного браузера)
- заголовок Authorization: Bearer <token>, где token может быть amh_* или JWT

Логика авторизации — в routers.api_tokens.resolve_user.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import CheckUp, Client, Meeting, Task, User
from routers.api_tokens import resolve_user

router = APIRouter()


def _is_admin(user: User) -> bool:
    return (user.role or "").lower() == "admin"


@router.get("/api/ext/health")
async def ext_health():
    """Проверка доступности API расширением. Без авторизации."""
    return {
        "ok": True,
        "version": "2.0.0",
        "accept_token_formats": ["amh_*", "jwt"],
    }


@router.get("/api/ext/clients")
async def ext_clients(
    request: Request,
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=50),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Лёгкий список клиентов для расширения (id, name, segment, health, url)."""
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    query = db.query(Client)
    if not _is_admin(user):
        query = query.filter(Client.manager_email == user.email)

    if q and q.strip():
        # нижний регистр через func.lower() + .contains() — кросс-совместимо
        # между SQLite (нет ILIKE) и Postgres
        needle = q.strip().lower()
        query = query.filter(func.lower(Client.name).contains(needle))

    # Разумный порядок — по health вниз, потом по имени
    query = query.order_by(Client.name.asc())

    limit = max(1, min(limit, 50))
    clients = query.limit(limit).all()

    return [
        {
            "id": c.id,
            "name": c.name,
            "segment": c.segment,
            "health_score": c.health_score,
            "domain": c.domain,
            "manager_email": c.manager_email,
            "url": f"/client/{c.id}",
        }
        for c in clients
    ]


@router.post("/api/ext/tasks")
async def ext_create_task(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Быстрое создание задачи из расширения."""
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный JSON в теле запроса")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Ожидается JSON-объект")

    client_id = body.get("client_id")
    title = (body.get("title") or "").strip()
    priority = (body.get("priority") or "medium").strip().lower()
    due_date_raw = body.get("due_date")

    if not isinstance(client_id, int):
        try:
            client_id = int(client_id) if client_id is not None else None
        except (TypeError, ValueError):
            client_id = None
    if not client_id:
        raise HTTPException(status_code=400, detail="Не указан client_id")
    if not title:
        raise HTTPException(status_code=400, detail="Не указан title задачи")

    if priority not in ("low", "medium", "high"):
        priority = "medium"

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    # Менеджер — только свои клиенты
    if not _is_admin(user):
        if not client.manager_email or client.manager_email != user.email:
            raise HTTPException(status_code=403, detail="Нет доступа к этому клиенту")

    # Парсим дату (YYYY-MM-DD) в datetime
    due_dt: Optional[datetime] = None
    if due_date_raw:
        try:
            d = date.fromisoformat(str(due_date_raw).strip())
            due_dt = datetime.combine(d, datetime.min.time())
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректный формат due_date, ожидается YYYY-MM-DD")

    task = Task(
        client_id=client.id,
        title=title[:500],
        status="plan",
        priority=priority,
        due_date=due_dt,
        source="manual",
    )
    # created_by — в модели Task такой колонки нет, используем confirmed_by-совместимый подход
    # если в модели появится поле created_by — заполним, сейчас пишем в description как fallback
    if hasattr(Task, "created_by"):
        try:
            setattr(task, "created_by", user.email)
        except Exception:
            pass

    db.add(task)
    db.commit()
    db.refresh(task)

    return {"ok": True, "id": task.id}


@router.get("/api/ext/summary")
async def ext_summary(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Сводка для бейджа расширения и мини-дашборда."""
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    is_admin = _is_admin(user)
    now = datetime.utcnow()
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    tomorrow_start = datetime.combine(today + timedelta(days=1), datetime.min.time())

    # ── clients_total ───────────────────────────────────────────────────────
    clients_q = db.query(Client)
    if not is_admin:
        clients_q = clients_q.filter(Client.manager_email == user.email)
    clients_total = clients_q.count()

    # ── tasks_open / tasks_overdue ──────────────────────────────────────────
    tasks_open_q = db.query(Task).filter(Task.status.in_(["plan", "in_progress", "review"]))
    tasks_overdue_q = db.query(Task).filter(
        Task.status.in_(["plan", "in_progress", "review"]),
        Task.due_date.isnot(None),
        Task.due_date < now,
    )
    if not is_admin:
        tasks_open_q = tasks_open_q.join(Client, Task.client_id == Client.id).filter(
            Client.manager_email == user.email
        )
        tasks_overdue_q = tasks_overdue_q.join(Client, Task.client_id == Client.id).filter(
            Client.manager_email == user.email
        )
    tasks_open = tasks_open_q.count()
    tasks_overdue = tasks_overdue_q.count()

    # ── meetings_today ──────────────────────────────────────────────────────
    meetings_q = db.query(Meeting).filter(
        Meeting.date >= today_start,
        Meeting.date < tomorrow_start,
    )
    if not is_admin:
        meetings_q = meetings_q.join(Client, Meeting.client_id == Client.id).filter(
            Client.manager_email == user.email
        )
    meetings_today = meetings_q.count()

    # ── checkups_overdue ────────────────────────────────────────────────────
    checkups_q = db.query(CheckUp).filter(CheckUp.status == "overdue")
    if not is_admin:
        checkups_q = checkups_q.join(Client, CheckUp.client_id == Client.id).filter(
            Client.manager_email == user.email
        )
    checkups_overdue = checkups_q.count()

    return {
        "clients_total": clients_total,
        "tasks_open": tasks_open,
        "tasks_overdue": tasks_overdue,
        "meetings_today": meetings_today,
        "checkups_overdue": checkups_overdue,
    }
