"""
routers/design.py — новый UI (вариант C: серверные данные + JSX в браузере).

URL-схема: /design/{page_id}
   page_id ∈ PAGES (см. словарь ниже).

Один шаблон design/app.html рендерит шелл (sidebar + topbar)
и монтирует нужный PageX-компонент по active_page.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from database import get_db
from auth import decode_access_token
from models import (
    Client, Task, Meeting, User,
    AuditLog, UserClientAssignment, RoadmapItem,
    FollowupTemplate,
)
import design_mappers as dm

_EXT_BASE = Path(__file__).resolve().parent.parent / "static"


def _read_manifest_version(manifest_path: Path) -> str:
    try:
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return str(data.get("version", "—"))
    except Exception:
        pass
    return "—"


def _file_size_kb(p: Path) -> int:
    try:
        return round(p.stat().st_size / 1024) if p.exists() else 0
    except Exception:
        return 0


def _list_extensions() -> List[dict]:
    """Метаданные единого Chrome-расширения AM Hub.

    Внутри одного расширения три модуля:
      • Sync — Merchrules → AM Hub (каждые 30 мин + по кнопке)
      • Checkup — качество поиска Diginetica (ручной запуск)
      • Tokens — перехват сессий time.tbank.ru и tbank.ktalk.ru
    """
    hub_ext_dir = _EXT_BASE / "amhub-ext"
    return [
        {
            "id": "amhub",
            "name": "AM Hub",
            "description": (
                "Единое расширение для менеджеров: синхронизация клиентов/задач "
                "из Merchrules, чекап качества поиска Diginetica, перехват токенов "
                "встреч и таймтрекинга."
            ),
            "version": _read_manifest_version(hub_ext_dir / "manifest.json"),
            "zip_url":     "/static/amhub-ext.zip",
            "zip_size_kb": _file_size_kb(_EXT_BASE / "amhub-ext.zip"),
            "modules": [
                {"icon": "refresh", "name": "Sync",    "desc": "Merchrules → AM Hub · каждые 30 мин"},
                {"icon": "check",   "name": "Checkup", "desc": "Качество поиска Diginetica · ручной запуск"},
                {"icon": "lock",    "name": "Tokens",  "desc": "Перехват сессий Ktalk и T-Bank Time"},
            ],
            "primary": True,
        },
    ]

router = APIRouter(prefix="/design", tags=["design"])
templates = Jinja2Templates(directory="templates")


# page_id → (Name компонента в window, breadcrumbs, заголовок)
PAGES = {
    "command":   ("PageHub",        ["am hub", "командный центр"],         "Командный центр"),
    "today":     ("PageToday",      ["am hub", "ежедневное", "сегодня"],   "Сегодня"),
    "clients":   ("PageClients",    ["am hub", "ежедневное", "клиенты"],   "Все клиенты"),
    "top50":     ("PageTop50",      ["am hub", "ежедневное", "top-50"],    "Top-50"),
    "tasks":     ("PageTasks",      ["am hub", "ежедневное", "задачи"],    "Задачи"),
    "meetings":  ("PageMeetings",   ["am hub", "ежедневное", "встречи"],   "Встречи"),
    "portfolio": ("PagePortfolio",  ["am hub", "ежедневное", "портфель"],  "Портфель"),
    "analytics": ("PageAnalytics",  ["am hub", "аналитика"],               "Аналитика"),
    "ai":        ("PageAI",         ["am hub", "аналитика", "AI"],         "AI-ассистент"),
    "kanban":    ("PageKanban",     ["am hub", "аналитика", "канбан"],     "Канбан"),
    "kpi":       ("PageKPI",        ["am hub", "аналитика", "KPI"],        "Мой KPI"),
    "qbr":       ("PageQBR",        ["am hub", "аналитика", "QBR"],        "QBR Календарь"),
    "cabinet":   ("PageCabinet",    ["am hub", "инструменты", "кабинет"],  "Мой кабинет"),
    "templates": ("PageTemplates",  ["am hub", "инструменты", "шаблоны"],  "Шаблоны"),
    "auto":      ("PageAuto",       ["am hub", "инструменты", "автозадачи"], "Автозадачи"),
    "roadmap":   ("PageRoadmap",    ["am hub", "инструменты", "роадмап"],  "Роадмап"),
    "internal":  ("PageInternal",   ["am hub", "инструменты", "внутр."],   "Внутренние задачи"),
    "help":      ("PageHelp",       ["am hub", "интеграции", "помощь"],    "Помощь"),
    "extension": ("PageExtInstall", ["am hub", "интеграции", "расширение"], "Расширение"),
    "profile":   ("PageProfile",    ["am hub", "профиль"],                 "Мой профиль"),
    "assignments":("PageAssignments",["am hub", "админ", "назначения"],    "Назначения клиентов"),
}


def _get_user(auth_token: Optional[str], db: Session) -> Optional[User]:
    if not auth_token:
        return None
    payload = decode_access_token(auth_token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload.get("sub"))).first()


def _client_ids_for_user(db: Session, user: User) -> Optional[List[int]]:
    """
    Возвращает список client_id, видимых менеджеру:
      • admin → None (= все клиенты)
      • иначе → объединение:
          - UserClientAssignment (явное назначение)
          - Client.manager_email == user.email (бэкап-матчинг)

    None означает "без фильтра".
    """
    if (user.role or "") == "admin":
        return None

    assigned = db.query(UserClientAssignment.client_id).filter(
        UserClientAssignment.user_id == user.id
    ).all()
    ids = {row[0] for row in assigned}

    if user.email:
        by_email = db.query(Client.id).filter(Client.manager_email == user.email).all()
        ids.update(row[0] for row in by_email)

    return list(ids) if ids else []


@router.get("/", response_class=HTMLResponse)
async def design_root():
    return RedirectResponse(url="/design/command", status_code=303)


# ── Roadmap admin API ────────────────────────────────────────
# Доступ только admin. Менеджеры только читают (через design-страницу).
@router.post("/api/roadmap")
async def roadmap_create(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Создать элемент роадмапа. Любой авторизованный менеджер может добавлять."""
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body = await request.json()
    item = RoadmapItem(
        column_key   = (body.get("column_key") or "backlog").lower(),
        column_title = body.get("column_title") or "Бэклог",
        tone         = body.get("tone") or "neutral",
        title        = body.get("title") or "",
        description  = body.get("description") or "",
        order_idx    = int(body.get("order_idx") or 0),
    )
    if not item.title:
        raise HTTPException(status_code=400, detail="title required")
    db.add(item); db.commit(); db.refresh(item)
    return {"ok": True, "id": item.id}


@router.delete("/api/roadmap/{item_id}")
async def roadmap_delete(
    item_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Удалить элемент роадмапа. Admin — любой; manager — только свои (через TODO author_id).
    Пока все авторизованные могут удалять — добавим author_id и фильтр позже."""
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    it = db.query(RoadmapItem).filter(RoadmapItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(it); db.commit()
    return {"ok": True}


@router.post("/api/roadmap/from-meeting/{meeting_id}")
async def roadmap_from_meeting(
    meeting_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Авто-добавить пункт в бэклог по завершённой встрече (из follow-up)."""
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Meeting not found")
    # Определим следующий квартал
    from datetime import datetime
    now = datetime.utcnow()
    q = (now.month - 1) // 3 + 1
    next_q = q + 1 if q < 4 else 1
    col_key = f"q{next_q}"
    col_title = f"Q{next_q} · план"
    client_name = m.client.name if m.client else "клиент"
    title = f"Follow-up по встрече {m.type or 'meeting'} · {client_name}"
    item = RoadmapItem(
        column_key=col_key, column_title=col_title, tone="info",
        title=title,
        description=f"Создано авто из meeting_id={meeting_id}",
    )
    db.add(item); db.commit(); db.refresh(item)
    return {"ok": True, "id": item.id}


# ── Templates CRUD ───────────────────────────────────────────
@router.post("/api/templates")
async def template_create(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body = await request.json()
    t = FollowupTemplate(
        user_id=user.id,
        name=(body.get("name") or "").strip(),
        category=(body.get("category") or "general").strip(),
        content=(body.get("body") or body.get("content") or "").strip(),
    )
    if not t.name or not t.content:
        raise HTTPException(status_code=400, detail="name and body required")
    db.add(t); db.commit(); db.refresh(t)
    return {"ok": True, "id": t.id}


@router.delete("/api/templates/{tid}")
async def template_delete(
    tid: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    t = db.query(FollowupTemplate).filter(FollowupTemplate.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    if t.user_id != user.id and (user.role or "") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    db.delete(t); db.commit()
    return {"ok": True}


# ── Internal tasks quick create ──────────────────────────────
@router.post("/api/internal-tasks")
async def internal_task_create(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    t = Task(
        client_id=None,
        title=title,
        description=body.get("description") or "",
        priority=body.get("priority") or "medium",
        status="plan",
        team=body.get("owner") or (user.email or ""),
        source="internal",
    )
    # due_date — если пришёл как "YYYY-MM-DD" или "N дней"
    due = body.get("due")
    if due:
        try:
            from datetime import datetime as _dt, timedelta as _td
            if isinstance(due, str) and due.isdigit():
                t.due_date = _dt.utcnow() + _td(days=int(due))
            else:
                t.due_date = _dt.fromisoformat(due)
        except Exception:
            pass
    db.add(t); db.commit(); db.refresh(t)
    return {"ok": True, "id": t.id}


# ── Profile ──────────────────────────────────────────────────
@router.get("/api/profile")
async def profile_get(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Скоуп клиентов
    my_clients = db.query(Client).filter(Client.manager_email == user.email).count() if user.email else 0
    assigned = db.query(UserClientAssignment).filter(UserClientAssignment.user_id == user.id).count()
    return {
        "id":         user.id,
        "email":      user.email,
        "name":       user.name,
        "role":       user.role,
        "is_active":  user.is_active,
        "telegram_id": user.telegram_id,
        "settings":   user.settings or {},
        "clients_by_email": my_clients,
        "clients_assigned": assigned,
    }


@router.put("/api/profile")
async def profile_update(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body = await request.json()
    for field in ("first_name", "last_name", "telegram_id"):
        if field in body:
            setattr(user, field, body[field])
    if "settings" in body and isinstance(body["settings"], dict):
        cur = dict(user.settings or {})
        cur.update(body["settings"])
        user.settings = cur
    db.commit()
    return {"ok": True}


# ── Users list (for assignments) ─────────────────────────────
@router.get("/api/users")
async def users_list(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    rows = db.query(User).filter(User.is_active == True).order_by(User.email).all()
    return {"users": [{
        "id": u.id, "email": u.email, "name": u.name,
        "role": u.role, "is_active": u.is_active,
    } for u in rows]}


# ── Client assignment (admin only) ───────────────────────────
@router.post("/api/assign-client")
async def assign_client(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Передать клиента другому менеджеру: переустанавливает Client.manager_email
    и пересоздаёт UserClientAssignment. Admin only."""
    user = _get_user(auth_token, db)
    if not user or (user.role or "") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    body = await request.json()
    client_id = int(body.get("client_id") or 0)
    target_email = (body.get("manager_email") or "").strip().lower()
    if not client_id or not target_email:
        raise HTTPException(status_code=400, detail="client_id and manager_email required")
    client = db.query(Client).filter(Client.id == client_id).first()
    target = db.query(User).filter(User.email == target_email).first()
    if not client or not target:
        raise HTTPException(status_code=404, detail="client or target user not found")
    old_email = client.manager_email
    client.manager_email = target_email
    # Чистим старые assignments и создаём новый
    db.query(UserClientAssignment).filter(UserClientAssignment.client_id == client.id).delete()
    db.add(UserClientAssignment(user_id=target.id, client_id=client.id))
    db.commit()
    return {"ok": True, "from": old_email, "to": target_email}


# ── Seed CSMs ────────────────────────────────────────────────
# Идемпотентно; доступно admin либо при пустой таблице users (bootstrap).
@router.post("/api/seed-csms")
async def seed_csms_endpoint(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _get_user(auth_token, db)
    users_count = db.query(User).count()
    # Разрешаем: admin OR bootstrap (пустая таблица)
    if users_count > 0 and (not user or (user.role or "") != "admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    from seed_csms import seed
    report = seed(dry_run=False)
    return report


@router.get("/{page_id}", response_class=HTMLResponse)
async def design_page(
    page_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _get_user(auth_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if page_id not in PAGES:
        return RedirectResponse(url="/design/command", status_code=303)

    component, breadcrumbs, title = PAGES[page_id]
    now = datetime.utcnow()

    ctx = _build_context(
        db, user, request, now,
        page_id=page_id,
        component=component,
        breadcrumbs=breadcrumbs,
        title=title,
        page=page,
        per_page=per_page,
    )
    return templates.TemplateResponse("design/app.html", ctx)


@router.get("/client/{client_id}", response_class=HTMLResponse)
async def design_client_detail(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Детальная страница клиента (монтирует window.PageClient)."""
    user = _get_user(auth_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    visible_ids = _client_ids_for_user(db, user)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    # Проверка доступа: admin или клиент в визибле-списке
    if visible_ids is not None and client.id not in visible_ids:
        raise HTTPException(status_code=403, detail="Forbidden")

    now = datetime.utcnow()
    next_meetings = dm.prefetch_next_meetings(db, now, visible_ids=[client.id])

    ctx = _build_context(
        db, user, request, now,
        page_id="clients",
        component="PageClient",
        breadcrumbs=["am hub", "клиенты", client.name or f"#{client.id}"],
        title=client.name or f"Client #{client.id}",
    )
    ctx["current_client"] = dm.client_to_design(client, now, next_meetings)
    return templates.TemplateResponse("design/app.html", ctx)


def _build_context(db, user, request, now, *, page_id, component, breadcrumbs, title, page=1, per_page=50):
    """Общая подготовка данных для обоих роутов — чтобы не дублировать."""
    visible_ids = _client_ids_for_user(db, user)

    clients_base  = db.query(Client)
    tasks_base    = db.query(Task).options(joinedload(Task.client))
    meetings_base = db.query(Meeting).options(joinedload(Meeting.client))

    if visible_ids is not None:
        if not visible_ids:
            clients_base  = clients_base.filter(Client.id == -1)
            tasks_base    = tasks_base.filter(Task.client_id == -1)
            meetings_base = meetings_base.filter(Meeting.client_id == -1)
        else:
            clients_base  = clients_base.filter(Client.id.in_(visible_ids))
            tasks_base    = tasks_base.filter(Task.client_id.in_(visible_ids))
            meetings_base = meetings_base.filter(Meeting.client_id.in_(visible_ids))

    # Pagination: работает на любой странице, но реально видна только на /design/clients
    clients_total = clients_base.count()
    offset = (page - 1) * per_page
    clients_q = clients_base.order_by(Client.id.desc()).offset(offset).limit(per_page).all()

    tasks_q    = tasks_base.filter(Task.status.in_(["plan", "in_progress", "blocked"])) \
                           .order_by(Task.due_date.asc()).limit(200).all()
    meetings_q = meetings_base.filter(Meeting.date >= now) \
                              .order_by(Meeting.date.asc()).limit(100).all()

    total_pages = max(1, (clients_total + per_page - 1) // per_page)
    pagination = {
        "page":         page,
        "per_page":     per_page,
        "total":        clients_total,
        "total_pages":  total_pages,
        "has_prev":     page > 1,
        "has_next":     page < total_pages,
    }

    # Префетчим встречи ОДНИМ запросом (убираем N+1)
    next_meetings = dm.prefetch_next_meetings(db, now, visible_ids=visible_ids)

    # ACTIVITY
    audit_rows = (
        db.query(AuditLog)
          .order_by(AuditLog.created_at.desc())
          .limit(20)
          .all()
    )
    user_ids = {a.user_id for a in audit_rows if a.user_id}
    users_map = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            users_map[u.id] = u.name

    ref_client_ids = {a.resource_id for a in audit_rows
                      if a.resource_type == "client" and a.resource_id}
    obj_map = {}
    if ref_client_ids:
        for c in db.query(Client.id, Client.name).filter(Client.id.in_(ref_client_ids)).all():
            obj_map[("client", c.id)] = c.name

    activity = [dm.activity_to_design(a, users_map, obj_map, now) for a in audit_rows]

    # Sidebar stats — живые вместо хардкода в shell.jsx
    sidebar_stats = dm.compute_sidebar_stats(db, user, visible_ids, now)

    return {
        "request":        request,
        "user":           user,
        "active_page":    page_id,
        "component_name": component,
        "breadcrumbs":    breadcrumbs,
        "page_title":     title,
        "clients":  [dm.client_to_design(c, now, next_meetings) for c in clients_q],
        "tasks":    [dm.task_to_design(t, now) for t in tasks_q],
        "meetings": [dm.meeting_to_design(m, now) for m in meetings_q],
        "activity":        activity,
        "tools":           dm.tools_from_sync_logs(db, now),
        "jobs":            dm.jobs_from_sync_logs(db, now, limit=8),
        "sidebar_stats":   sidebar_stats,
        "current_client":  None,  # подставится в design_client_detail
        "pagination":      pagination,
        "extensions":      _list_extensions(),
        "hub_url":         os.getenv("APP_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or "",
        # Расширенные данные для design-страниц (см. design_mappers)
        "templates":       dm.templates_to_design(db, user),
        "auto_rules":      dm.auto_rules_to_design(db, user),
        "auto_stats":      dm.auto_stats(db, user, now),
        "internal_tasks":  dm.internal_tasks_to_design(db, user),
        "kpi_weekly":      dm.kpi_weekly(db, user, now),
        "heatmap":         dm.heatmap_activity(db, user, now, visible_ids),
        "team_response":   dm.team_response(db, now),
        "recent_files":    dm.recent_files(db, user),
        "roadmap":         dm.roadmap_data(db),
        "gmv_spark":       dm.gmv_spark(db, user, now),
        "day_kpi":         dm.day_kpi(db, user, now),
        "reminders":       dm.reminders_for_user(db, user, now),
    }
