"""
routers/design.py — новый UI (вариант C: серверные данные + JSX в браузере).

URL-схема: /design/{page_id}
   page_id ∈ PAGES (см. словарь ниже).

Один шаблон design/app.html рендерит шелл (sidebar + topbar)
и монтирует нужный PageX-компонент по active_page.
"""
import json
import os
from datetime import datetime, timedelta
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
    FollowupTemplate, QBR,
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
            "zip_url":     "/api/extension/download",
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

# ── Lazy data loading: map page_id → required data keys ──────
# Only the listed keys are computed; all others get empty defaults.
PAGES_DATA_MAP = {
    "command":    ["clients", "tasks", "meetings", "tools", "sidebar_stats", "gmv_spark", "day_kpi", "activity"],
    "today":      ["tasks", "meetings", "sidebar_stats", "day_kpi", "reminders"],
    "clients":    ["clients", "sidebar_stats"],
    "top50":      ["clients"],
    "tasks":      ["tasks", "sidebar_stats", "clients"],
    "meetings":   ["meetings", "sidebar_stats", "clients"],
    "portfolio":  ["clients", "sidebar_stats"],
    "analytics":  ["clients", "meetings", "heatmap", "team_response", "kpi_weekly"],
    "ai":         ["clients", "activity"],
    "kanban":     ["tasks", "clients"],
    "kpi":        ["kpi_weekly", "day_kpi", "clients", "sidebar_stats"],
    "qbr":        ["clients", "qbr_data"],
    "cabinet":    ["reminders"],
    "templates":  ["templates"],
    "auto":       ["auto_rules", "auto_stats"],
    "roadmap":    ["roadmap"],
    "internal":   ["tasks", "internal_tasks"],
    "help":       [],
    "extension":  [],
    "integrations": [],
    "profile":    [],
    "assignments": ["clients"],
    "client-groups": ["clients"],
}


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
    "integrations": ("PageIntegrations", ["am hub", "интеграции"], "Интеграции"),
    "profile":   ("PageProfile",    ["am hub", "профиль"],                 "Мой профиль"),
    "assignments":("PageAssignments",["am hub", "админ", "назначения"],    "Назначения клиентов"),
    "groups":    ("PageManagerGroups",["am hub", "админ", "группы"],       "Группы менеджеров"),
    "renewal":   ("PageRenewal",    ["am hub", "клиенты", "оплаты"],       "Оплаты"),
    "client-groups": ("PageClientGroups", ["am hub", "админ", "ГК"],       "Группы компаний"),
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


@router.patch("/api/roadmap/{item_id}")
async def roadmap_update(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """PATCH: смена колонки (DnD между Q1..Q4/backlog), title, description, order_idx."""
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401)
    it = db.query(RoadmapItem).filter(RoadmapItem.id == item_id).first()
    if not it:
        raise HTTPException(status_code=404)
    body = await request.json()
    if "column_key" in body:
        new_key = (body["column_key"] or "").lower()
        allowed = {"q1", "q2", "q3", "q4", "backlog"}
        if new_key not in allowed:
            raise HTTPException(400, f"column_key must be in {allowed}")
        tone_map = {"q1": "ok", "q2": "signal", "q3": "info", "q4": "warn", "backlog": "neutral"}
        title_map = {"q1": "Q1 · готово", "q2": "Q2 · в работе",
                     "q3": "Q3 · план", "q4": "Q4 · идеи", "backlog": "Бэклог"}
        it.column_key = new_key
        it.column_title = body.get("column_title") or title_map[new_key]
        it.tone = body.get("tone") or tone_map[new_key]
    if "title" in body:
        t = (body["title"] or "").strip()
        if not t:
            raise HTTPException(400, "title cannot be empty")
        it.title = t
    if "description" in body:
        it.description = body["description"] or ""
    if "order_idx" in body:
        try:
            it.order_idx = int(body["order_idx"])
        except Exception:
            pass
    db.commit()
    db.refresh(it)
    return {"ok": True, "id": it.id, "column_key": it.column_key,
            "title": it.title, "order_idx": it.order_idx}


@router.post("/api/roadmap/reorder")
async def roadmap_reorder(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Batch update order_idx (+опционально column_key) для сортировки внутри колонки
    и перемещения между колонками за один запрос.
    Body: {items: [{id, order_idx, column_key?}]}
    """
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401)
    body = await request.json()
    items = body.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(400, "items must be a list")
    allowed_cols = {"q1", "q2", "q3", "q4", "backlog"}
    tone_map = {"q1": "ok", "q2": "signal", "q3": "info", "q4": "warn", "backlog": "neutral"}
    title_map = {"q1": "Q1 · готово", "q2": "Q2 · в работе",
                 "q3": "Q3 · план", "q4": "Q4 · идеи", "backlog": "Бэклог"}
    updated = 0
    for raw in items:
        try:
            iid = int(raw.get("id"))
        except Exception:
            continue
        it = db.query(RoadmapItem).filter(RoadmapItem.id == iid).first()
        if not it:
            continue
        if "order_idx" in raw:
            try: it.order_idx = int(raw["order_idx"])
            except Exception: pass
        if "column_key" in raw:
            k = (raw["column_key"] or "").lower()
            if k in allowed_cols:
                it.column_key = k
                it.column_title = title_map[k]
                it.tone = tone_map[k]
        updated += 1
    db.commit()
    return {"ok": True, "updated": updated}


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
        "first_name": user.first_name,
        "last_name":  user.last_name,
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
    # Email — проверка уникальности + умный merge фантомных учёток.
    # Если такой email уже занят, но у него нет telegram_id и роль не admin
    # — это auto-created фантом (env seed / airtable sync / etc).
    # Безопасно сливаем: переносим его клиентов на текущего юзера и удаляем.
    if "email" in body and isinstance(body["email"], str):
        new_email = body["email"].strip().lower()
        if new_email and new_email != (user.email or "").lower():
            existing = db.query(User).filter(User.email == new_email, User.id != user.id).first()
            if existing:
                is_phantom = (not existing.telegram_id) and existing.role != "admin"
                if not is_phantom:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Email '{new_email}' занят активным пользователем (role={existing.role}, tg={'да' if existing.telegram_id else 'нет'}). Обратись к админу."
                    )
                # Merge: забираем клиентов + удаляем фантома.
                try:
                    db.query(Client).filter(Client.manager_email == existing.email).update(
                        {"manager_email": new_email}, synchronize_session=False)
                    from models import UserClientAssignment
                    db.query(UserClientAssignment).filter(UserClientAssignment.user_id == existing.id).update(
                        {"user_id": user.id}, synchronize_session=False)
                    db.delete(existing)
                    db.flush()
                except Exception as e:
                    db.rollback()
                    raise HTTPException(status_code=500, detail=f"Не удалось слить аккаунты: {e}")
            old_email = user.email
            user.email = new_email
            if old_email and old_email != new_email:
                db.query(Client).filter(Client.manager_email == old_email).update(
                    {"manager_email": new_email}, synchronize_session=False)
    if "settings" in body and isinstance(body["settings"], dict):
        cur = dict(user.settings or {})
        cur.update(body["settings"])
        user.settings = cur
    db.commit()
    return {"ok": True, "email": user.email}


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


# ── Reminders ────────────────────────────────────────────────
@router.post("/api/reminders")
async def reminder_create(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    from models import Reminder
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body = await request.json()
    from datetime import datetime as _dt
    r = Reminder(
        user_id=user.id,
        text=body.get("text", ""),
        remind_at=_dt.fromisoformat(body.get("remind_at", _dt.utcnow().isoformat())),
    )
    db.add(r); db.commit(); db.refresh(r)
    return {"ok": True, "id": r.id}


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


# ── QBR Calendar API ─────────────────────────────────────────
@router.get("/api/qbr")
async def qbr_list(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """List QBRs for current user (grouped by manager)."""
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    visible_ids = _client_ids_for_user(db, user)
    q = db.query(QBR).join(Client, QBR.client_id == Client.id, isouter=True)
    if visible_ids is not None:
        if not visible_ids:
            return {"qbrs": []}
        q = q.filter(QBR.client_id.in_(visible_ids))
    rows = q.order_by(QBR.date.asc()).limit(500).all()
    result = []
    for qbr in rows:
        client_name = qbr.client.name if qbr.client else None
        result.append({
            "id": qbr.id,
            "client_id": qbr.client_id,
            "client_name": client_name,
            "quarter": qbr.quarter,
            "year": qbr.year,
            "date": qbr.date.strftime("%Y-%m-%d") if qbr.date else None,
            "status": qbr.status,
            "manager_email": getattr(qbr, "manager_email", None) or (qbr.client.manager_email if qbr.client else None),
        })
    return {"qbrs": result}


@router.post("/api/qbr")
async def qbr_create(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Create or update a QBR entry."""
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body = await request.json()
    client_id = body.get("client_id")
    quarter = (body.get("quarter") or "").strip()
    if not client_id or not quarter:
        raise HTTPException(status_code=400, detail="client_id and quarter required")

    # Verify access
    visible_ids = _client_ids_for_user(db, user)
    if visible_ids is not None and client_id not in visible_ids:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Upsert
    existing = db.query(QBR).filter(QBR.client_id == client_id, QBR.quarter == quarter).first()
    date_str = body.get("date")
    date_val = None
    if date_str:
        try:
            from datetime import datetime as _dt
            date_val = _dt.fromisoformat(date_str[:10])
        except Exception:
            pass

    # Fetch client for name lookup + Airtable record ID
    client_obj = db.query(Client).filter(Client.id == client_id).first()
    client_name = client_obj.name if client_obj else str(client_id)

    if existing:
        if date_val:
            existing.date = date_val
        if body.get("status"):
            existing.status = body["status"]
        if body.get("summary"):
            existing.summary = body["summary"]
        db.commit()
        qbr_obj = existing
        action = "updated"
    else:
        year = int(quarter.split("-")[0]) if "-" in quarter else datetime.utcnow().year
        qbr_obj = QBR(
            client_id=client_id,
            quarter=quarter,
            year=year,
            date=date_val,
            status=body.get("status", "scheduled"),
            summary=body.get("summary"),
            manager_email=body.get("manager_email") or (client_obj.manager_email if client_obj else None),
        )
        db.add(qbr_obj)
        db.commit()
        db.refresh(qbr_obj)
        action = "created"

    # Push to Airtable QBR table asynchronously (fire-and-forget)
    try:
        from airtable_sync import push_qbr_to_airtable
        import asyncio
        asyncio.create_task(push_qbr_to_airtable(
            client_name=client_name,
            quarter=quarter,
            summary=qbr_obj.summary or "",
            achievements=qbr_obj.achievements or [],
            date=date_val,
            manager_email=qbr_obj.manager_email or "",
        ))
    except Exception:
        pass  # Non-blocking — don't fail the request if Airtable is unavailable

    return {"ok": True, "id": qbr_obj.id, "action": action}


@router.post("/api/qbr/sync-airtable")
async def qbr_sync_airtable(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Pull QBR data from Airtable tblqQbChhRYoZoxWu."""
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    from airtable_sync import sync_qbr_from_airtable
    result = await sync_qbr_from_airtable(db)
    return result


# ── Sheets sync API ──────────────────────────────────────────
@router.post("/api/sync-sheets")
async def sync_sheets(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Trigger Google Sheets sync (Churn/Downsell + Top-50)."""
    user = _get_user(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        from sheets_sync import sync_churn_sheet, sync_top50_sheet
        churn_result = await sync_churn_sheet(db)
        top50_result = await sync_top50_sheet(db)
        return {"ok": True, "churn": churn_result, "top50": top50_result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    """Общая подготовка данных для обоих роутов — чтобы не дублировать.

    Lazy loading: only compute data keys required by the current page
    (see PAGES_DATA_MAP). All other keys get empty/default values.
    """
    visible_ids = _client_ids_for_user(db, user)

    # Determine which data keys this page actually needs
    needed = set(PAGES_DATA_MAP.get(page_id, []))

    # ── Base query builders (shared filters) ──────────────────
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

    # ── Clients ───────────────────────────────────────────────
    if "clients" in needed:
        clients_total = clients_base.count()
        # На странице «clients» используем пагинацию, на остальных (meetings, qbr,
        # roadmap …) грузим ВСЕХ — для дропдаунов в модалках и полного списка.
        if page_id == "clients":
            offset = (page - 1) * per_page
            clients_q = clients_base.order_by(Client.id.desc()).offset(offset).limit(per_page).all()
        else:
            clients_q = clients_base.order_by(Client.id.desc()).limit(1000).all()
    else:
        clients_total = 0
        clients_q = []

    # ── Tasks ─────────────────────────────────────────────────
    if "tasks" in needed:
        # Split task streams:
        #   /design/tasks     — только клиентские задачи (Task.client_id IS NOT NULL),
        #                       ограниченные visible_ids менеджера.
        #   /design/internal  — только внутренние: Task.client_id IS NULL ИЛИ source='internal'.
        #                       visible_ids не применяем — внутренние задачи принадлежат команде,
        #                       а не конкретному клиенту (иначе IN-list отфильтрует NULL-записи).
        if page_id == "internal":
            tasks_q_base = db.query(Task).options(joinedload(Task.client)) \
                             .filter(Task.status.in_(["plan", "in_progress", "blocked"]))
            try:
                tasks_q_base = tasks_q_base.filter(
                    (Task.client_id.is_(None)) | (Task.source == "internal")
                )
            except Exception:
                tasks_q_base = tasks_q_base.filter(Task.client_id.is_(None))
        else:
            tasks_q_base = tasks_base.filter(Task.status.in_(["plan", "in_progress", "blocked"]))
            if page_id == "tasks":
                tasks_q_base = tasks_q_base.filter(Task.client_id.isnot(None))
        tasks_q = tasks_q_base.order_by(Task.due_date.asc()).limit(200).all()
    else:
        tasks_q = []

    # ── Meetings ──────────────────────────────────────────────
    if "meetings" in needed:
        upcoming_q = meetings_base.filter(Meeting.date >= now) \
                                  .order_by(Meeting.date.asc()).limit(100).all()
        past_cutoff = now - timedelta(days=60)
        past_q = meetings_base.filter(Meeting.date < now, Meeting.date >= past_cutoff) \
                              .order_by(Meeting.date.desc()).limit(100).all()
        meetings_q = list(upcoming_q) + list(past_q)
    else:
        meetings_q = []

    # ── Ktalk events (если настроена интеграция) ─────────────
    ktalk_meetings_design: list = []
    if "meetings" in needed:
        try:
            import asyncio as _asyncio
            import concurrent.futures as _futures
            from integrations import ktalk as _ktalk
            if _ktalk.KTALK_BASE_URL and _ktalk.KTALK_API_TOKEN:
                _coro = _ktalk.get_events(
                    date_from=now - timedelta(days=7),
                    date_to=now + timedelta(days=30),
                    limit=200,
                    use_cache=True,
                )
                # Безопасно: всегда отдельный тред + свой loop, чтобы не ломать главный event loop FastAPI
                with _futures.ThreadPoolExecutor(max_workers=1) as _ex:
                    _events = _ex.submit(_asyncio.run, _coro).result(timeout=20)
                # Карта клиентов по нормализованному имени/домену для резолва
                _cmap: dict = {}
                _cq = db.query(Client)
                if visible_ids is not None:
                    if not visible_ids:
                        _cq = _cq.filter(Client.id == -1)
                    else:
                        _cq = _cq.filter(Client.id.in_(visible_ids))
                for _c in _cq.all():
                    for k in filter(None, [_c.name, _c.domain]):
                        _cmap[k.lower().strip()] = _c

                def _resolve(title: str):
                    t = (title or "").lower()
                    for k, c in _cmap.items():
                        if k and k in t:
                            return c
                    return None

                for _e in _events:
                    _start = _e.get("start")
                    if isinstance(_start, str):
                        try:
                            _sdt = datetime.fromisoformat(_start.replace("Z", "+00:00"))
                            if _sdt.tzinfo is not None:
                                _sdt = _sdt.replace(tzinfo=None)
                        except Exception:
                            continue
                    elif isinstance(_start, datetime):
                        _sdt = _start
                    else:
                        continue
                    if _sdt < now:
                        continue
                    _client_obj = _resolve(_e.get("title", ""))
                    _mood = "risk" if "churn" in (_e.get("title") or "").lower() else "ok"

                    # Формат под meeting_to_design — он читает .date, .client, .type, .mood
                    class _Proxy:
                        pass
                    _p = _Proxy()
                    _p.date = _sdt
                    _p.type = "sync"
                    _p.mood = _mood
                    _p.sentiment_score = None
                    _p.client = _client_obj

                    # Используем обычный mapper, затем перезаписываем name на заголовок Ktalk
                    _design = dm.meeting_to_design(_p, now)
                    _design["client"] = _e.get("title") or _design.get("client") or "—"
                    _design["source"] = "ktalk"
                    ktalk_meetings_design.append(_design)
        except Exception as _e:
            logger.exception("ktalk meetings fetch failed: %s", _e)

    # Pagination info (always computed, uses cached clients_total)
    total_pages = max(1, (clients_total + per_page - 1) // per_page) if clients_total else 1
    pagination = {
        "page":         page,
        "per_page":     per_page,
        "total":        clients_total,
        "total_pages":  total_pages,
        "has_prev":     page > 1,
        "has_next":     page < total_pages,
    }

    # ── Next meetings prefetch (only if clients or meetings are loaded) ──
    if clients_q or "meetings" in needed:
        next_meetings = dm.prefetch_next_meetings(db, now, visible_ids=visible_ids)
    else:
        next_meetings = {}

    # ── Activity feed ─────────────────────────────────────────
    if "activity" in needed:
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
    else:
        activity = []

    # ── Sidebar stats ─────────────────────────────────────────
    sidebar_stats = dm.compute_sidebar_stats(db, user, visible_ids, now) \
        if "sidebar_stats" in needed else {}

    # ── Tools / Jobs ──────────────────────────────────────────
    tools = dm.tools_from_sync_logs(db, now) if "tools" in needed else []
    jobs  = dm.jobs_from_sync_logs(db, now, limit=8) if "tools" in needed else []

    # ── Extended data (lazy) ──────────────────────────────────
    templates_data   = dm.templates_to_design(db, user)          if "templates"      in needed else []
    auto_rules_data  = dm.auto_rules_to_design(db, user)         if "auto_rules"     in needed else []
    auto_stats_data  = dm.auto_stats(db, user, now)              if "auto_stats"     in needed else {}
    internal_tasks   = dm.internal_tasks_to_design(db, user)     if "internal_tasks" in needed else []
    kpi_weekly_data  = dm.kpi_weekly(db, user, now)              if "kpi_weekly"     in needed else []
    heatmap_data     = dm.heatmap_activity(db, user, now, visible_ids) if "heatmap"  in needed else {"rows": [], "weeks": [], "matrix": []}
    team_resp_data   = dm.team_response(db, now)                 if "team_response"  in needed else []
    recent_files     = dm.recent_files(db, user)                 if "recent_files"   in needed else []
    roadmap_data     = dm.roadmap_data(db)                       if "roadmap"        in needed else []
    gmv_spark_data   = dm.gmv_spark(db, user, now)               if "gmv_spark"      in needed else []
    day_kpi_data     = dm.day_kpi(db, user, now)                 if "day_kpi"        in needed else {}
    reminders_data   = dm.reminders_for_user(db, user, now)      if "reminders"      in needed else []
    qbr_data         = json.dumps(dm.qbr_calendar(db, user, now)) if "qbr_data"      in needed else "[]"

    # Bundle cache-busting: use file mtime as version hash
    _bundle_path = Path(__file__).parent.parent / "static" / "design" / "dist" / "bundle.js"
    try:
        _bundle_hash = str(int(_bundle_path.stat().st_mtime))
    except Exception:
        _bundle_hash = "1"

    return {
        "request":        request,
        "user":           user,
        "active_page":    page_id,
        "bundle_hash":    _bundle_hash,
        "component_name": component,
        "breadcrumbs":    breadcrumbs,
        "page_title":     title,
        "clients":  [dm.client_to_design(c, now, next_meetings) for c in clients_q],
        "tasks":    [dm.task_to_design(t, now) for t in tasks_q],
        "meetings": ([dm.meeting_to_design(m, now) for m in meetings_q] + ktalk_meetings_design),
        "activity":        activity,
        "tools":           tools,
        "jobs":            jobs,
        "sidebar_stats":   sidebar_stats,
        "current_client":  None,  # подставится в design_client_detail
        "pagination":      pagination,
        "extensions":      _list_extensions(),
        "hub_url":         os.getenv("APP_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or "",
        # Расширенные данные для design-страниц (см. design_mappers)
        "templates":       templates_data,
        "auto_rules":      auto_rules_data,
        "auto_stats":      auto_stats_data,
        "internal_tasks":  internal_tasks,
        "kpi_weekly":      kpi_weekly_data,
        "heatmap":         heatmap_data,
        "team_response":   team_resp_data,
        "recent_files":    recent_files,
        "roadmap":         roadmap_data,
        "gmv_spark":       gmv_spark_data,
        "day_kpi":         day_kpi_data,
        "reminders":       reminders_data,
        "qbr_data":        qbr_data,
    }
