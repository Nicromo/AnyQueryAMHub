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
    AuditLog, UserClientAssignment,
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
    """Метаданные всех Chrome-расширений для страницы установки."""
    sync_dir = _EXT_BASE / "extensions" / "amhub-sync"
    hub_ext_dir = _EXT_BASE / "amhub-ext"
    checkup_dir = _EXT_BASE / "checkup-ext"

    return [
        {
            "id": "amhub-sync",
            "name": "AM Hub · Sync",
            "description": "Синхронизация Merchrules → AM Hub. Автообновление через GitHub.",
            "version": _read_manifest_version(_EXT_BASE.parent.parent / "extension" / "manifest.json"),
            "extension_id": "gcgjcpgbbliblmlhmfpcffnkoehibiep",
            "crx_url":       "/static/extensions/amhub-sync/amhub-sync.crx",
            "crx_size_kb":   _file_size_kb(sync_dir / "amhub-sync.crx"),
            "zip_url":       "/static/extensions/amhub-sync/amhub-sync.zip",
            "zip_size_kb":   _file_size_kb(sync_dir / "amhub-sync.zip"),
            "auto_update":   True,
            "primary":       True,
        },
        {
            "id": "amhub-ext",
            "name": "AM Hub · Ext",
            "description": "Многофункциональное расширение: Sync · Checkup · Tokens.",
            "version": _read_manifest_version(hub_ext_dir / "manifest.json"),
            "extension_id": None,
            "crx_url":     None,
            "crx_size_kb": 0,
            "zip_url":     "/static/amhub-ext.zip",
            "zip_size_kb": _file_size_kb(_EXT_BASE / "amhub-ext.zip"),
            "auto_update": False,
            "primary":     False,
        },
        {
            "id": "checkup-ext",
            "name": "Search Quality Checkup",
            "description": "Автоматический чекап качества поиска Diginetica.",
            "version": _read_manifest_version(checkup_dir / "manifest.json"),
            "extension_id": None,
            "crx_url":     None,
            "crx_size_kb": 0,
            "zip_url":     "/static/checkup-ext.zip",
            "zip_size_kb": _file_size_kb(_EXT_BASE / "checkup-ext.zip"),
            "auto_update": False,
            "primary":     False,
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
    }
