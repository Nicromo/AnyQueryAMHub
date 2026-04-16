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

@router.post("/api/import/clients-csv")
async def api_import_clients_csv(
    file: UploadFile,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Импорт клиентов из CSV файла."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    import io, pandas as pd
    content_bytes = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(content_bytes), dtype=str)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения CSV: {e}")

    created = updated = skipped = 0
    from sqlalchemy.orm.attributes import flag_modified
    for _, row in df.iterrows():
        name = str(row.get("name") or row.get("название") or row.get("client_name") or "").strip()
        if not name or name == "nan":
            skipped += 1
            continue
        client = db.query(Client).filter(Client.name == name).first()
        if client:
            for field, col in [("segment","segment"),("domain","domain"),("health_score","health_score")]:
                v = str(row.get(col) or "").strip()
                if v and v != "nan":
                    if field == "health_score":
                        try: setattr(client, field, float(v.replace("%","")))
                        except: pass
                    else: setattr(client, field, v)
            updated += 1
        else:
            seg = str(row.get("segment") or "").strip()
            domain = str(row.get("domain") or "").strip()
            client = Client(name=name, segment=seg or None, domain=domain or None,
                           manager_email=user.email)
            db.add(client)
            created += 1
    db.commit()
    return {"ok": True, "created": created, "updated": updated, "skipped": skipped}


# ── Roadmap create ───────────────────────────────────────────────────────────


@router.post("/api/my-day/schedule")
async def api_my_day_schedule(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить расписание задач на день."""
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
    settings = user.settings or {}
    settings["my_day_schedule"] = data.get("schedule", [])
    settings["my_day_date"] = data.get("date")
    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}




@router.get("/api/my-day/schedule")
async def api_get_my_day_schedule(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить расписание задач на день."""
    if not auth_token:
        return {"schedule": [], "date": None}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"schedule": [], "date": None}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"schedule": [], "date": None}
    settings = user.settings or {}
    return {"schedule": settings.get("my_day_schedule", []), "date": settings.get("my_day_date")}




@router.post("/api/profile/update")
async def api_profile_update(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Обновить имя/фамилию/telegram_id."""
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
    if "first_name" in data:
        user.first_name = data["first_name"].strip()
    if "last_name" in data:
        user.last_name = data["last_name"].strip()
    if "telegram_id" in data:
        tg = data["telegram_id"].strip()
        # Проверяем что такой TG ID не занят другим юзером
        if tg and db.query(User).filter(User.telegram_id == tg, User.id != user.id).first():
            return {"ok": False, "error": "Этот Telegram ID уже привязан к другому аккаунту"}
        user.telegram_id = tg or None
    db.commit()
    return {"ok": True}




@router.post("/api/profile/password")
async def api_change_password(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сменить пароль."""
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
    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    confirm = data.get("confirm_password", "")

    if not new_pw or len(new_pw) < 8:
        return {"ok": False, "error": "Новый пароль должен быть не менее 8 символов"}
    if new_pw != confirm:
        return {"ok": False, "error": "Пароли не совпадают"}
    if user.hashed_password and not verify_password(current, user.hashed_password):
        return {"ok": False, "error": "Неверный текущий пароль"}

    user.hashed_password = hash_password(new_pw)
    db.commit()
    return {"ok": True}




@router.post("/api/import/tasks-csv")
async def api_import_tasks_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Импорт задач из CSV/Excel файла.

    Ожидаемые колонки:
      title / название / задача
      client / клиент / account
      status / статус
      priority / приоритет
      due_date / дедлайн / срок
      team / команда
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

    try:
        import pandas as pd, io
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            for enc in ("utf-8", "cp1251", "latin-1"):
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=enc, sep=None, engine="python")
                    break
                except Exception:
                    continue
            else:
                return {"error": "Не удалось прочитать файл"}
    except Exception as e:
        return {"error": f"Ошибка чтения файла: {e}"}

    df.columns = [str(c).strip().lower() for c in df.columns]

    def find_col(df, variants):
        for v in variants:
            for c in df.columns:
                if v in c:
                    return c
        return None

    col_title    = find_col(df, ["title", "название", "задача", "task", "name"])
    col_client   = find_col(df, ["client", "клиент", "account", "аккаунт", "site"])
    col_status   = find_col(df, ["status", "статус"])
    col_priority = find_col(df, ["priority", "приоритет"])
    col_due      = find_col(df, ["due", "дедлайн", "срок", "date"])
    col_team     = find_col(df, ["team", "команда"])
    col_mr_id    = find_col(df, ["merchrules_task", "task_id", "mr_id", "id"])

    if not col_title:
        return {"error": f"Не найдена колонка с названием задачи. Колонки: {list(df.columns)}"}

    STATUS_MAP = {
        "plan": "plan", "в работе": "in_progress", "in_progress": "in_progress",
        "review": "review", "done": "done", "готово": "done",
        "blocked": "blocked", "заблок": "blocked",
    }

    created = skipped = 0
    # Кешируем клиентов для поиска
    all_clients = {c.name.lower(): c for c in db.query(Client).all()}

    for idx, row in df.iterrows():
        title = str(row.get(col_title, "")).strip()
        if not title or title.lower() in ("nan", "none", ""):
            skipped += 1
            continue

        # Ищем клиента
        client_id = None
        if col_client:
            client_name = str(row.get(col_client, "")).strip().lower()
            if client_name and client_name not in ("nan", ""):
                # Точное совпадение
                c = all_clients.get(client_name)
                if not c:
                    # Частичное совпадение
                    for cname, cobj in all_clients.items():
                        if client_name in cname or cname in client_name:
                            c = cobj
                            break
                if c:
                    client_id = c.id

        # Статус
        raw_status = str(row.get(col_status, "plan")).strip().lower() if col_status else "plan"
        status_val = STATUS_MAP.get(raw_status, "plan")

        # Дедлайн
        due_date = None
        if col_due:
            raw_due = str(row.get(col_due, "")).strip()
            if raw_due and raw_due not in ("nan", ""):
                for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
                    try:
                        due_date = datetime.strptime(raw_due[:10], fmt)
                        break
                    except Exception:
                        continue

        # Проверяем дубль по merchrules_task_id
        mr_id = str(row.get(col_mr_id, "")).strip() if col_mr_id else ""
        if mr_id and mr_id not in ("nan", ""):
            existing = db.query(Task).filter(Task.merchrules_task_id == mr_id).first()
            if existing:
                skipped += 1
                continue

        task = Task(
            client_id=client_id,
            title=title,
            status=status_val,
            priority=str(row.get(col_priority, "medium")).strip().lower() if col_priority else "medium",
            due_date=due_date,
            team=str(row.get(col_team, "")).strip() if col_team else None,
            source="import",
            merchrules_task_id=mr_id if mr_id not in ("nan", "") else None,
        )
        db.add(task)
        created += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return {"error": f"Ошибка сохранения: {e}"}

    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "total_rows": len(df),
    }



@router.post("/api/internal-task")


@router.get("/api/internal-task")
async def api_internal_task(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Внутренние задачи менеджера (не привязаны к клиенту)."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    if request.method == "GET":
        tasks = db.query(Task).filter(
            Task.client_id.is_(None),
            Task.created_at >= datetime.utcnow() - timedelta(days=90)
        ).order_by(Task.created_at.desc()).limit(50).all()
        return {"tasks": [{"id":t.id,"title":t.title,"status":t.status,"priority":t.priority,
                           "due_date":t.due_date.isoformat() if t.due_date else None} for t in tasks]}

    body = await request.json()
    task = Task(
        client_id=None, title=body.get("title","Задача"),
        status=body.get("status","plan"), priority=body.get("priority","medium"),
        description=body.get("description",""),
        due_date=datetime.fromisoformat(body["due_date"]) if body.get("due_date") else None,
        created_at=datetime.utcnow(),
    )
    db.add(task); db.commit(); db.refresh(task)
    return {"ok": True, "id": task.id}




