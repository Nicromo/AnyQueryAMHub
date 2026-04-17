"""Auto-split from misc.py"""
from typing import Optional, List
from datetime import datetime, timedelta
import os, json, logging

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from database import get_db, SessionLocal
from models import (
    AccountPlan,
    AuditLog,
    CheckUp,
    Client,
    ClientAttachment,
    ClientHistory,
    ClientNote,
    FollowupTemplate,
    Meeting,
    Notification,
    QBR,
    SyncLog,
    Task,
    TaskComment,
    User,
    VoiceNote,
)
from auth import decode_access_token, hash_password, verify_password, log_audit, get_current_user
from deps import require_user, require_admin, optional_user
from error_handlers import log_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

@router.post("/api/roadmap/create")
async def api_roadmap_create(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    body = await request.json()
    task = Task(client_id=body.get("client_id"), title=body.get("title",""), 
                status="plan", priority=body.get("priority","medium"),
                created_at=datetime.utcnow())
    db.add(task); db.commit(); db.refresh(task)
    return {"ok": True, "id": task.id}


@router.get("/api/dashboard/actions")
async def api_dashboard_actions(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить карточки действий для дашборда."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    now = datetime.now()

    actions = []

    # 1. Фолоуапы pending
    pending_followups = db.query(Meeting).filter(
        Meeting.followup_status == "pending",
        Meeting.date < now,
    ).all()
    for m in pending_followups:
        client = db.query(Client).filter(Client.id == m.client_id).first()
        actions.append({
            "type": "followup",
            "priority": "high",
            "meeting_id": m.id,
            "client_name": client.name if client else "—",
            "meeting_title": m.title or m.type,
            "meeting_date": m.date.isoformat() if m.date else None,
            "days_ago": (now - m.date).days if m.date else 0,
        })

    # 2. Prep до встречи
    upcoming = db.query(Meeting).filter(
        Meeting.date >= now,
        Meeting.date < now + timedelta(days=2),
    ).all()
    for m in upcoming:
        client = db.query(Client).filter(Client.id == m.client_id).first()
        actions.append({
            "type": "prep",
            "priority": "medium",
            "meeting_id": m.id,
            "client_name": client.name if client else "—",
            "meeting_title": m.title or m.type,
            "meeting_date": m.date.isoformat() if m.date else None,
            "hours_until": int((m.date - now).total_seconds() / 3600) if m.date else 0,
        })

    # 3. Chekups overdue
    overdue_checkups = db.query(CheckUp).filter(CheckUp.status == "overdue").all()
    for c in overdue_checkups:
        client = db.query(Client).filter(Client.id == c.client_id).first()
        actions.append({
            "type": "checkup",
            "priority": "high",
            "checkup_id": c.id,
            "client_name": client.name if client else "—",
            "checkup_type": c.type,
            "scheduled_date": c.scheduled_date.isoformat() if c.scheduled_date else None,
        })

    # 4. QBR overdue
    clients_qbr = db.query(Client).filter(
        Client.next_qbr_date != None,
        Client.next_qbr_date < now,
    ).all()
    for c in clients_qbr:
        actions.append({
            "type": "qbr",
            "priority": "high",
            "client_id": c.id,
            "client_name": c.name,
            "next_qbr_date": c.next_qbr_date.isoformat() if c.next_qbr_date else None,
        })

    return {"actions": actions, "total": len(actions)}



@router.get("/api/search")
async def api_global_search(
    q: str = Query("", min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Глобальный поиск по клиентам, задачам, встречам, заметкам."""
    if not auth_token:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}

    search_pattern = f"%{q}%"

    # Клиенты
    c_q = db.query(Client)
    if user.role == "manager":
        c_q = c_q.filter(Client.manager_email == user.email)
    clients = c_q.filter(
        Client.name.ilike(search_pattern) |
        (Client.segment is not None and Client.segment.ilike(search_pattern)),
    ).limit(limit).all()

    # Задачи
    task_query = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_query = task_query.filter(Client.manager_email == user.email)
    tasks = task_query.filter(
        Task.title.ilike(search_pattern) |
        (Task.description is not None and Task.description.ilike(search_pattern)),
    ).limit(limit).all()

    # Встречи
    meeting_query = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        meeting_query = meeting_query.filter(Client.manager_email == user.email)
    meetings = meeting_query.filter(
        (Meeting.title is not None and Meeting.title.ilike(search_pattern)) |
        (Meeting.type is not None and Meeting.type.ilike(search_pattern)),
    ).order_by(Meeting.date.desc()).limit(limit).all()

    # Заметки
    note_query = db.query(ClientNote).join(Client, ClientNote.client_id == Client.id, isouter=True)
    if user.role == "manager":
        note_query = note_query.filter(Client.manager_email == user.email)
    notes = note_query.filter(ClientNote.content.ilike(search_pattern)).order_by(
        ClientNote.is_pinned.desc(), ClientNote.updated_at.desc()
    ).limit(limit).all()

    return {
        "clients": [{"id": c.id, "name": c.name, "segment": c.segment, "url": f"/client/{c.id}", "type": "client"} for c in clients],
        "tasks": [{"id": t.id, "title": t.title, "status": t.status, "client_name": t.client.name if t.client else "—", "url": f"/client/{t.client_id}", "type": "task"} for t in tasks],
        "meetings": [{"id": m.id, "title": m.title or m.type, "date": m.date.isoformat() if m.date else None, "client_name": m.client.name if m.client else "—", "url": f"/client/{m.client_id}", "type": "meeting"} for m in meetings],
        "notes": [{"id": n.id, "content": n.content[:100] + "..." if len(n.content) > 100 else n.content, "client_name": n.client.name if n.client else "—", "url": f"/client/{n.client_id}", "type": "note", "pinned": n.is_pinned} for n in notes],
    }



@router.get("/api/kanban")
async def api_kanban(
    client_id: Optional[int] = None,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Получить задачи в формате канбан (группировка по статусам)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if client_id:
        q = q.filter(Task.client_id == client_id)
    tasks = q.order_by(Task.due_date.asc()).all()

    columns = {"plan": [], "in_progress": [], "review": [], "done": [], "blocked": []}
    for t in tasks:
        status = t.status or "plan"
        if status not in columns:
            columns["plan"].append(t)
        else:
            columns[status].append(t)

    def task_dict(t):
        return {
            "id": t.id, "title": t.title, "priority": t.priority, "status": t.status or "plan",
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "client_name": t.client.name if t.client else "—",
            "client_id": t.client_id, "team": t.team,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    # Возвращаем оба формата — columns (для client_detail) и плоский (для kanban страницы)
    columns_list = [
        {"id": col, "tasks": [task_dict(t) for t in tlist]}
        for col, tlist in columns.items()
    ]
    return {
        "columns": columns_list,
        **{col: [task_dict(t) for t in tlist] for col, tlist in columns.items()}
    }




@router.get("/api/calendar/events")
async def api_calendar_events(start: str = "", end: str = "", db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить события для календаря."""
    if not auth_token:
        return {"events": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"events": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"events": []}

    q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if start and end:
        q = q.filter(Meeting.date >= datetime.fromisoformat(start), Meeting.date <= datetime.fromisoformat(end))
    meetings = q.order_by(Meeting.date).all()

    events = []
    for m in meetings:
        color = {"checkup": "#22c55e", "qbr": "#6366f1", "kickoff": "#f97316", "sync": "#3b82f6"}.get(m.type, "#64748b")
        events.append({
            "id": m.id,
            "title": f"{m.client.name + ': ' if m.client else ''}{m.title or m.type}",
            "start": m.date.isoformat() if m.date else None,
            "color": color,
            "url": f"/client/{m.client_id}",
            "type": m.type,
        })

    # Также добавляем дедлайны задач
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    task_q = task_q.filter(Task.due_date != None, Task.status != "done")
    if start and end:
        task_q = task_q.filter(Task.due_date >= datetime.fromisoformat(start), Task.due_date <= datetime.fromisoformat(end))
    tasks = task_q.all()

    for t in tasks:
        events.append({
            "id": f"task-{t.id}",
            "title": f"⏰ {t.title}",
            "start": t.due_date.isoformat() if t.due_date else None,
            "color": "#ef4444",
            "url": f"/client/{t.client_id}",
            "type": "task",
        })

    return {"events": events}



@router.get("/api/diagnostics/outbound-ip")
async def api_outbound_ip(auth_token: Optional[str] = Cookie(None)):
    """
    Возвращает внешний IP Railway-сервера.
    Этот IP нужно добавить в whitelist Merchrules.
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Используем несколько сервисов для надёжности
            for url in ["https://api.ipify.org?format=json", "https://ifconfig.me/ip"]:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        text = resp.text.strip()
                        ip = resp.json().get("ip", text) if "json" in url else text
                        return {"ip": ip, "note": "Добавьте этот IP в whitelist Merchrules"}
                except Exception:
                    continue
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Не удалось определить IP"}




@router.post("/api/diagnostics/merchrules-auth")
async def api_diag_merchrules_auth(
    request: Request,
    auth_token: Optional[str] = Cookie(None),
):
    """
    Диагностика авторизации Merchrules.
    Показывает точный HTTP-статус и ответ для каждой попытки.
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    body = await request.json()
    login = body.get("login", "")
    password = body.get("password", "")
    if not login or not password:
        return {"error": "Нужны login и password"}

    import httpx
    results = []
    urls = list(dict.fromkeys([
        _env("MERCHRULES_API_URL", "https://merchrules.any-platform.ru"),
        "https://merchrules.any-platform.ru",
        "https://merchrules-qa.any-platform.ru",
    ]))
    fields = ["email", "login", "username"]

    async with httpx.AsyncClient(timeout=15) as hx:
        for url in urls:
            for field in fields:
                try:
                    resp = await hx.post(
                        f"{url}/backend-v2/auth/login",
                        json={field: login, "password": password},
                        timeout=10,
                    )
                    body_text = resp.text[:300]
                    has_token = False
                    if resp.status_code == 200:
                        try:
                            j = resp.json()
                            has_token = bool(j.get("token") or j.get("access_token") or j.get("accessToken"))
                        except Exception:
                            pass
                    results.append({
                        "url": url,
                        "field": field,
                        "status": resp.status_code,
                        "has_token": has_token,
                        "response": body_text,
                    })
                    # Нашли рабочий — дальше не пробуем
                    if resp.status_code == 200 and has_token:
                        return {"ok": True, "working": results[-1], "all": results}
                except Exception as e:
                    results.append({
                        "url": url,
                        "field": field,
                        "status": "error",
                        "error": str(e),
                    })

    return {"ok": False, "all": results}



async def api_import_clients_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Импорт клиентов из CSV/Excel файла.

    Ожидаемые колонки (гибко — ищет по ключевым словам):
      name / название / клиент
      segment / сегмент
      manager_email / менеджер
      site_id / site_ids / merchrules_id
      health_score
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

    content = await file.read()
    filename = file.filename or ""

    # Парсим файл
    try:
        import pandas as pd, io
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            # Пробуем разные кодировки и разделители
            for enc in ("utf-8", "cp1251", "latin-1"):
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=enc, sep=None, engine="python")
                    break
                except Exception:
                    continue
            else:
                return {"error": "Не удалось прочитать файл. Поддерживаются CSV и XLSX."}
    except Exception as e:
        return {"error": f"Ошибка чтения файла: {e}"}

    # Нормализуем названия колонок
    df.columns = [str(c).strip().lower() for c in df.columns]

    def find_col(df, variants):
        for v in variants:
            for c in df.columns:
                if v in c:
                    return c
        return None

    col_name    = find_col(df, ["name", "название", "клиент", "company", "account"])
    col_segment = find_col(df, ["segment", "сегмент", "тип"])
    col_manager = find_col(df, ["manager", "менеджер", "email"])
    col_site    = find_col(df, ["site_id", "site", "merchrules", "account_id"])
    col_health  = find_col(df, ["health", "score", "хелс"])

    if not col_name:
        return {"error": f"Не найдена колонка с именем клиента. Колонки в файле: {list(df.columns)}"}

    created = updated = skipped = 0
    errors = []

    for idx, row in df.iterrows():
        name = str(row.get(col_name, "")).strip()
        if not name or name.lower() in ("nan", "none", ""):
            skipped += 1
            continue

        segment   = str(row.get(col_segment, "")).strip() if col_segment else ""
        manager   = str(row.get(col_manager, "")).strip() if col_manager else user.email
        site_id   = str(row.get(col_site, "")).strip() if col_site else ""
        health    = None
        if col_health:
            try:
                health = float(str(row.get(col_health, "")).replace(",", ".").replace("%", ""))
                if health > 1:
                    health = health / 100
            except Exception:
                pass

        # Ищем существующего клиента
        existing = None
        if site_id and site_id not in ("nan", ""):
            existing = db.query(Client).filter(Client.merchrules_account_id == site_id).first()
        if not existing:
            existing = db.query(Client).filter(Client.name == name).first()

        if existing:
            # Обновляем только непустые поля
            if segment and segment not in ("nan", ""):
                existing.segment = segment
            if manager and manager not in ("nan", "") and "@" in manager:
                existing.manager_email = manager
            if site_id and site_id not in ("nan", ""):
                existing.merchrules_account_id = site_id
            if health is not None:
                existing.health_score = health
            updated += 1
        else:
            c = Client(
                name=name,
                segment=segment if segment not in ("nan", "") else None,
                manager_email=manager if "@" in manager else user.email,
                merchrules_account_id=site_id if site_id not in ("nan", "") else None,
                health_score=health,
            )
            db.add(c)
            created += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return {"error": f"Ошибка сохранения: {e}"}

    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_rows": len(df),
        "columns_detected": {
            "name": col_name, "segment": col_segment,
            "manager": col_manager, "site_id": col_site,
        }
    }




@router.post("/api/voice-notes")
async def api_create_voice_note(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Создать голосовую заметку (текстовую транскрипцию)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()
    vn = VoiceNote(
        client_id=data.get("client_id"),
        meeting_id=data.get("meeting_id"),
        user_id=user.id,
        transcription=data.get("text", ""),
        duration_seconds=data.get("duration", 0),
    )
    db.add(vn)
    # Авто-создание задачи из заметки
    if data.get("create_task"):
        db.add(Task(
            client_id=data.get("client_id"),
            title=f"🎤 {data.get('text', '')[:80]}",
            description=data.get("text", ""),
            status="plan",
            priority="medium",
            source="voice_note",
        ))
    db.commit()
    return {"ok": True, "id": vn.id}



@router.get("/api/voice-notes")
async def api_get_voice_notes(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить голосовые заметки пользователя."""
    if not auth_token:
        return {"notes": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notes": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    notes = db.query(VoiceNote).filter(VoiceNote.user_id == user.id).order_by(VoiceNote.created_at.desc()).limit(50).all()
    return {"notes": [{"id": n.id, "text": n.transcription, "duration": n.duration_seconds, "client_id": n.client_id, "created_at": n.created_at.isoformat() if n.created_at else None} for n in notes]}



@router.get("/api/manager/kpi")
async def api_manager_kpi(
    period_days: int = 30,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """KPI менеджера за период: задачи, встречи, фолоуапы, чекапы."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    since = datetime.utcnow() - timedelta(days=period_days)
    email = user.email

    # Задачи
    tasks_closed = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Task.status == "done",
        Task.confirmed_at >= since,
    ).count()

    tasks_created = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Task.created_at >= since,
    ).count()

    tasks_overdue = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Task.due_date < datetime.utcnow(),
        Task.status.in_(["plan", "in_progress"]),
    ).count()

    # Встречи
    meetings_held = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Meeting.date >= since,
        Meeting.date <= datetime.utcnow(),
    ).count()

    # Фолоуапы отправлены
    followups_sent = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Meeting.followup_status == "sent",
        Meeting.followup_sent_at >= since,
    ).count()

    followups_pending = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Meeting.followup_status == "pending",
        Meeting.date < datetime.utcnow(),
    ).count()

    # Клиенты
    total_clients = db.query(Client).filter(Client.manager_email == email).count()

    clients_no_contact = db.query(Client).filter(
        Client.manager_email == email,
        Client.last_meeting_date < datetime.utcnow() - timedelta(days=60),
    ).count()

    # Средний health score
    from sqlalchemy import func
    avg_health = db.query(func.avg(Client.health_score)).filter(
        Client.manager_email == email,
        Client.health_score != None,
    ).scalar() or 0

    return {
        "period_days": period_days,
        "manager": user.email,
        # Плоская структура для совместимости с kpi.html
        "tasks_closed": tasks_closed,
        "tasks_created": tasks_created,
        "tasks_overdue": tasks_overdue,
        "close_rate": round(tasks_closed / max(tasks_created, 1) * 100, 1),
        "meetings_held": meetings_held,
        "followups_sent": followups_sent,
        "followups_pending": followups_pending,
        "followup_rate": round(followups_sent / max(meetings_held, 1) * 100, 1),
        "total_clients": total_clients,
        "clients_no_contact_60d": clients_no_contact,
        "avg_health_score": round(float(avg_health) * 100, 1),
        # Вложенные тоже для обратной совместимости
        "tasks": {"closed": tasks_closed, "created": tasks_created, "overdue": tasks_overdue},
        "meetings": {"held": meetings_held, "followups_sent": followups_sent, "followups_pending": followups_pending},
        "clients": {"total": total_clients, "no_contact_60d": clients_no_contact, "avg_health_score": round(float(avg_health) * 100, 1)},
    }




@router.post("/api/metrics/upload")
async def api_metrics_upload(
    file: UploadFile,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Загрузка метрик Top-50 (CSV/Excel)."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    import io, pandas as pd
    content_bytes = await file.read()
    try:
        if file.filename and file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(content_bytes), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content_bytes), dtype=str)
        df.columns = [c.strip().lower().replace(" ","_") for c in df.columns]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка файла: {e}")

    updated = 0
    from sqlalchemy.orm.attributes import flag_modified
    for _, row in df.iterrows():
        name = str(row.get("name") or row.get("название") or row.get("client") or "").strip()
        if not name or name == "nan": continue
        client = db.query(Client).filter(Client.name.ilike(f"%{name}%")).first()
        if not client: continue
        meta = dict(client.integration_metadata or {})
        for col in df.columns:
            v = str(row.get(col) or "").strip()
            if v and v != "nan" and col not in ("name","название","client"):
                meta[f"metric_{col}"] = v
        client.integration_metadata = meta
        flag_modified(client, "integration_metadata")
        updated += 1
    db.commit()
    return {"ok": True, "updated": updated}



@router.get("/api/files/{file_path:path}")
async def api_serve_file(
    file_path: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Serve locally stored files."""
    from storage import get_file
    data = await get_file(file_path)
    if not data: raise HTTPException(status_code=404)
    from fastapi.responses import Response
    return Response(content=data, media_type="application/octet-stream")



@router.delete("/api/attachments/{att_id}")
async def api_delete_attachment(
    att_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models import ClientAttachment
    from storage import delete_file
    att = db.query(ClientAttachment).filter(ClientAttachment.id == att_id).first()
    if not att:
        raise HTTPException(status_code=404)
    await delete_file(att.file_key)
    db.delete(att); db.commit()
    return {"ok": True}




@router.get("/api/files/{file_key:path}")
async def api_serve_file(
    file_key: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Отдать файл из local storage."""
    from storage import get_file
    from fastapi.responses import Response as FR
    data = await get_file(file_key)
    if not data:
        raise HTTPException(status_code=404)
    import mimetypes
    mime, _ = mimetypes.guess_type(file_key)
    return FR(content=data, media_type=mime or "application/octet-stream")



