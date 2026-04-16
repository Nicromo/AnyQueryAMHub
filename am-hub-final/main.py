"""
AM Hub — Enterprise Account Manager Dashboard
Реальные данные из Merchrules · Персональные дашборды · AI-ассистент
"""
import os
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from datetime import timezone as tz
from typing import List, Optional

from fastapi import (
    FastAPI, Request, Depends, HTTPException, Query, Cookie,
    Form, status, UploadFile, File
)
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import engine, get_db, Base, init_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog, Notification, QBR, AccountPlan,
    ClientNote, TaskComment, FollowupTemplate, VoiceNote,
)
from auth import (
    get_current_user, get_current_admin,
    authenticate_user, create_user, create_access_token,
    verify_password, hash_password,
    log_audit,
)
from error_handlers import log_error, handle_db_error
from middlewares import (
    LoggingMiddleware, RateLimitMiddleware,
    ErrorHandlingMiddleware, SecurityHeadersMiddleware
)

# Merchrules sync
from merchrules_sync import get_auth_token as mr_get_auth_token

# AI
from ai_followup import process_transcript as ai_process_transcript
from ai_assistant import generate_prep_brief, generate_smart_followup, detect_account_risks

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ============================================================================
# ENV HELPERS — единый источник конфигурации
# ============================================================================

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

def _extract_sheets_id(val: str) -> str:
    """Вырезает spreadsheet ID из полного URL или возвращает как есть."""
    if not val:
        return ""
    # https://docs.google.com/spreadsheets/d/ID/edit...
    import re
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", val)
    if m:
        return m.group(1)
    return val


class Env:
    """Централизованный доступ к переменным окружения."""
    # Merchrules
    MR_LOGIN      = property(lambda self: _env("MERCHRULES_LOGIN"))
    MR_PASSWORD   = property(lambda self: _env("MERCHRULES_PASSWORD"))
    MR_URL        = property(lambda self: _env("MERCHRULES_API_URL", "https://merchrules.any-platform.ru"))
    MR_ACTIVE     = property(lambda self: bool(_env("MERCHRULES_LOGIN") and _env("MERCHRULES_PASSWORD")))
    # AI
    GROQ_KEY      = property(lambda self: _env("GROQ_API_KEY") or _env("API_GROQ"))
    QWEN_KEY      = property(lambda self: _env("QWEN_API_KEY"))
    AI_ACTIVE     = property(lambda self: bool(_env("GROQ_API_KEY") or _env("API_GROQ") or _env("QWEN_API_KEY")))
    AI_TYPE       = property(lambda self: "qwen" if _env("QWEN_API_KEY") else ("groq" if (_env("GROQ_API_KEY") or _env("API_GROQ")) else ""))
    # Telegram
    TG_TOKEN      = property(lambda self: _env("TG_BOT_TOKEN") or _env("TELEGRAM_BOT_TOKEN"))
    TG_CHAT_ID    = property(lambda self: _env("TG_NOTIFY_CHAT_ID") or _env("TELEGRAM_CHAT_ID"))
    TG_ACTIVE     = property(lambda self: bool(_env("TG_BOT_TOKEN") or _env("TELEGRAM_BOT_TOKEN")))
    # Airtable — поддерживаем оба имени: AIRTABLE_TOKEN и AIRTABLE_PAT
    AIRTABLE_PAT  = property(lambda self: _env("AIRTABLE_TOKEN") or _env("AIRTABLE_PAT"))
    AIRTABLE_BASE = property(lambda self: _env("AIRTABLE_BASE_ID"))
    AIRTABLE_TABLE = property(lambda self: _env("AIRTABLE_TABLE_ID", "tblIKAi1gcFayRJTn"))
    AIRTABLE_QBR_TABLE = property(lambda self: _env("AIRTABLE_QBR_TABLE_ID", "tblqQbChhRYoZoxWu"))
    AIRTABLE_ACTIVE = property(lambda self: bool(_env("AIRTABLE_TOKEN") or _env("AIRTABLE_PAT")))
    # Google Sheets — вырезаем ID из полного URL если передан
    SHEETS_ID     = property(lambda self: _extract_sheets_id(_env("SHEETS_SPREADSHEET_ID")))
    SHEETS_ACTIVE = property(lambda self: bool(_env("SHEETS_SPREADSHEET_ID")))
    # Ktalk
    KTALK_SPACE   = property(lambda self: _env("KTALK_SPACE"))
    KTALK_TOKEN   = property(lambda self: _env("KTALK_API_TOKEN"))
    KTALK_WEBHOOK = property(lambda self: _env("KTALK_WEBHOOK_URL"))
    KTALK_ACTIVE  = property(lambda self: bool(_env("KTALK_SPACE") and _env("KTALK_API_TOKEN")))
    # Tbank Time
    TIME_TOKEN    = property(lambda self: _env("TIME_API_TOKEN") or _env("TIME_SESSION_COOKIE"))
    TIME_ACTIVE   = property(lambda self: bool(_env("TIME_API_TOKEN") or _env("TIME_SESSION_COOKIE")))
    # Ktalk
    KTALK_SPACE   = property(lambda self: _env("KTALK_SPACE"))
    KTALK_TOKEN   = property(lambda self: _env("KTALK_API_TOKEN"))
    KTALK_ACTIVE  = property(lambda self: bool(_env("KTALK_API_TOKEN") and _env("KTALK_SPACE")))
    KTALK_WEBHOOK = property(lambda self: _env("KTALK_WEBHOOK_URL"))
    # Tbank Time
    TIME_TOKEN    = property(lambda self: _env("TIME_API_TOKEN"))
    TIME_URL      = property(lambda self: _env("TIME_BASE_URL", "https://time.tbank.ru"))
    TIME_ACTIVE   = property(lambda self: bool(_env("TIME_API_TOKEN")))
    # Airtable
    AIRTABLE_PAT  = property(lambda self: _env("AIRTABLE_PAT"))
    AIRTABLE_BASE = property(lambda self: _env("AIRTABLE_BASE_ID"))
    AIRTABLE_ACTIVE = property(lambda self: bool(_env("AIRTABLE_PAT")))
    # Google Sheets
    SHEETS_ID     = property(lambda self: _env("SHEETS_SPREADSHEET_ID"))
    SHEETS_ACTIVE = property(lambda self: bool(_env("SHEETS_SPREADSHEET_ID")))
    # App
    APP_URL       = property(lambda self: _env("RAILWAY_PUBLIC_DOMAIN") or _env("APP_URL"))
    SECRET_KEY    = property(lambda self: _env("SECRET_KEY", "your-secret-key-change-in-production"))

env = Env()

templates = Jinja2Templates(directory="templates")

MSK = tz(timedelta(hours=3))  # Moscow timezone


# ============================================================================
# LIFESPAN
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup"""
    try:
        init_db()
        # Добавляем отсутствующие колонки и таблицы
        with SessionLocal() as db:
            try:
                cols = db.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
                ).fetchall()
                col_names = {row[0] for row in cols}
                if "settings" not in col_names:
                    db.execute(text("ALTER TABLE users ADD COLUMN settings JSONB"))
                    db.commit()
                    logger.info("✅ Added users.settings column")
            except Exception as e:
                logger.warning(f"Migration users.settings: {e}")

            # Добавляем колонки Meeting
            try:
                mcols = db.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'meetings'")
                ).fetchall()
                mcol_names = {row[0] for row in mcols}
                for col, default in [("followup_status", "'pending'"), ("followup_text", "NULL"),
                                      ("followup_sent_at", "NULL"), ("followup_skipped", "FALSE"),
                                      ("is_qbr", "FALSE")]:
                    if col not in mcol_names:
                        db.execute(text(f"ALTER TABLE meetings ADD COLUMN {col} VARCHAR DEFAULT {default}" if col != "followup_skipped" and col != "is_qbr" else f"ALTER TABLE meetings ADD COLUMN {col} BOOLEAN DEFAULT {default}"))
                        db.commit()
                        logger.info(f"✅ Added meetings.{col}")
            except Exception as e:
                logger.warning(f"Migration meetings: {e}")

            # Добавляем колонки Task
            try:
                tcols = db.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tasks'")
                ).fetchall()
                tcol_names = {row[0] for row in tcols}
                for col, col_type in [
                    ("confirmed_at", "TIMESTAMP"), ("confirmed_by", "VARCHAR"),
                    ("pushed_to_roadmap", "BOOLEAN DEFAULT FALSE"), ("roadmap_pushed_at", "TIMESTAMP"),
                    ("team", "VARCHAR"), ("task_type", "VARCHAR"), ("source", "VARCHAR DEFAULT 'manual'"),
                ]:
                    if col not in tcol_names:
                        db.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {col_type}"))
                        db.commit()
                        logger.info(f"✅ Added tasks.{col}")
            except Exception as e:
                logger.warning(f"Migration tasks: {e}")

            # Добавляем колонки Client
            try:
                ccols = db.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'clients'")
                ).fetchall()
                ccol_names = {row[0] for row in ccols}
                for col, col_type in [("last_qbr_date", "TIMESTAMP"), ("next_qbr_date", "TIMESTAMP"),
                                       ("account_plan", "JSONB")]:
                    if col not in ccol_names:
                        db.execute(text(f"ALTER TABLE clients ADD COLUMN {col} {col_type}"))
                        db.commit()
                        logger.info(f"✅ Added clients.{col}")
            except Exception as e:
                logger.warning(f"Migration clients: {e}")

            # Создаём новые таблицы
            try:
                db.execute(text("""
                    CREATE TABLE IF NOT EXISTS qbrs (
                        id SERIAL PRIMARY KEY,
                        client_id INTEGER REFERENCES clients(id),
                        quarter VARCHAR NOT NULL,
                        year INTEGER NOT NULL,
                        date TIMESTAMP,
                        status VARCHAR DEFAULT 'draft',
                        metrics JSONB DEFAULT '{}',
                        summary TEXT,
                        achievements JSONB DEFAULT '[]',
                        issues JSONB DEFAULT '[]',
                        next_quarter_goals JSONB DEFAULT '[]',
                        meeting_id INTEGER REFERENCES meetings(id)
                    )
                """))
                db.commit()
                logger.info("✅ Created qbrs table")

                # Добавляем новые колонки в qbrs если их нет
                try:
                    qcols = db.execute(
                        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'qbrs'")
                    ).fetchall()
                    qcol_names = {row[0] for row in qcols}
                    for col, col_type in [("presentation_url", "VARCHAR"), ("executive_summary", "TEXT"),
                                           ("future_work", "JSONB DEFAULT '[]'"), ("key_insights", "JSONB DEFAULT '[]'")]:
                        if col not in qcol_names:
                            db.execute(text(f"ALTER TABLE qbrs ADD COLUMN {col} {col_type}"))
                            db.commit()
                            logger.info(f"✅ Added qbrs.{col}")
                except Exception as e:
                    logger.warning(f"Migration qbrs columns: {e}")
            except Exception as e:
                logger.warning(f"Migration qbrs: {e}")

            try:
                db.execute(text("""
                    CREATE TABLE IF NOT EXISTS account_plans (
                        id SERIAL PRIMARY KEY,
                        client_id INTEGER REFERENCES clients(id) UNIQUE,
                        quarterly_goals JSONB DEFAULT '[]',
                        action_items JSONB DEFAULT '[]',
                        notes TEXT,
                        strategy TEXT,
                        updated_at TIMESTAMP DEFAULT NOW(),
                        updated_by VARCHAR
                    )
                """))
                db.commit()
                logger.info("✅ Created account_plans table")
            except Exception as e:
                logger.warning(f"Migration account_plans: {e}")

            # Новые таблицы v3
            for table_name, table_sql in [
                ("client_notes", """CREATE TABLE IF NOT EXISTS client_notes (
                    id SERIAL PRIMARY KEY,
                    client_id INTEGER REFERENCES clients(id),
                    user_id INTEGER REFERENCES users(id),
                    content TEXT NOT NULL,
                    is_pinned BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )"""),
                ("task_comments", """CREATE TABLE IF NOT EXISTS task_comments (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER REFERENCES tasks(id),
                    user_id INTEGER REFERENCES users(id),
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )"""),
                ("followup_templates", """CREATE TABLE IF NOT EXISTS followup_templates (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    name VARCHAR NOT NULL,
                    content TEXT NOT NULL,
                    category VARCHAR DEFAULT 'general',
                    created_at TIMESTAMP DEFAULT NOW()
                )"""),
                ("voice_notes", """CREATE TABLE IF NOT EXISTS voice_notes (
                    id SERIAL PRIMARY KEY,
                    meeting_id INTEGER REFERENCES meetings(id),
                    client_id INTEGER REFERENCES clients(id),
                    user_id INTEGER REFERENCES users(id),
                    audio_url VARCHAR,
                    transcription TEXT,
                    duration_seconds INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )"""),
            ]:
                try:
                    db.execute(text(table_sql))
                    db.commit()
                    logger.info(f"✅ Created {table_name} table")
                except Exception as e:
                    logger.warning(f"Migration {table_name}: {e}")

            if db.query(User).count() == 0:
                admin = User(
                    email="admin@company.ru",
                    first_name="Администратор",
                    role="admin",
                    hashed_password=hash_password("admin123"),
                    settings={},
                )
                db.add(admin)
                db.commit()
                logger.info("✅ Default admin created")
        logger.info("✅ Database ready")

        # Start scheduler
        try:
            from scheduler import start_scheduler
            start_scheduler()
            logger.info("✅ Scheduler started")
        except Exception as e:
            logger.warning(f"Scheduler: {e}")

        # Register Telegram webhook
        try:
            from tg_bot import set_webhook, BOT_TOKEN as TG_TOKEN
            if TG_TOKEN:
                domain = env.APP_URL
                if domain:
                    await set_webhook(f"{domain}/webhook/telegram")
                    logger.info(f"✅ TG webhook: {domain}/webhook/telegram")
        except Exception as e:
            logger.warning(f"TG webhook: {e}")
    except Exception as e:
        logger.error(f"Startup error: {e}")
    yield


# ============================================================================
# APP SETUP
# ============================================================================

app = FastAPI(title="AM Hub", version="2.0.0", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_minute=100)
app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ============================================================================
# MERCHRULES HELPERS
# ============================================================================

def _get_user_cred(user: User) -> tuple:
    """Получить Merchrules логин/пароль: сначала из user.settings, потом из env."""
    settings = (user.settings or {}) if user else {}
    mr = settings.get("merchrules", {})
    login = mr.get("login") or env.MR_LOGIN
    password = mr.get("password") or env.MR_PASSWORD
    return login, password


# ============================================================================
# AUTH PAGES
# ============================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, email, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный email или пароль"})

    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="auth_token", value=token, httponly=True, samesite="lax", max_age=86400 * 30)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="auth_token")
    return response


# ============================================================================
# DASHBOARD
# ============================================================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)

    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Проверка онбординга
    settings = user.settings or {}
    if not settings.get("onboarding_complete"):
        return RedirectResponse(url="/onboarding", status_code=303)

    # Для админа — все клиенты, для менеджера — только его
    query = db.query(Client)
    if user.role == "manager":
        query = query.filter(Client.manager_email == user.email)
    clients = query.all()

    # Обогащаем данными из Merchrules если есть креды
    mr_login, mr_password = _get_user_cred(user)
    has_mr = bool(mr_login and mr_password)

    # Статистика
    now = datetime.now()
    counts = {"ENT": 0, "SME+": 0, "SME-": 0, "SME": 0, "SMB": 0, "SS": 0}
    healthy = warning = overdue = total_open = total_tasks = 0

    for c in clients:
        seg = c.segment or ""
        if seg in counts:
            counts[seg] += 1

        open_tasks = db.query(Task).filter(Task.client_id == c.id, Task.status.in_(["plan", "in_progress"])).count()
        blocked_tasks = db.query(Task).filter(Task.client_id == c.id, Task.status == "blocked").count()
        total_client_tasks = db.query(Task).filter(Task.client_id == c.id).count()
        total_open += open_tasks
        total_tasks += total_client_tasks

        is_overdue = c.needs_checkup and (not c.last_meeting_date or (now - c.last_meeting_date).days > 30)
        is_warning = c.needs_checkup and c.last_meeting_date and 14 < (now - c.last_meeting_date).days <= 30

        if is_overdue:
            overdue += 1
        elif is_warning:
            warning += 1
        else:
            healthy += 1

        c.open_tasks = open_tasks
        c.blocked_tasks = blocked_tasks
        c.total_tasks = total_client_tasks
        c.status = {"color": "red" if is_overdue else ("yellow" if is_warning else "green")}

    # Задачи на сегодня
    today = now.date()
    today_tasks = db.query(Task).filter(
        Task.due_date >= datetime.combine(today, datetime.min.time()),
        Task.due_date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
        Task.status.in_(["plan", "in_progress"]),
    ).all()

    if user.role == "manager":
        today_tasks = [t for t in today_tasks if t.client and t.client.manager_email == user.email]

    # Встречи на сегодня
    today_meetings = db.query(Meeting).filter(
        Meeting.date >= datetime.combine(today, datetime.min.time()),
        Meeting.date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
    ).all()

    return templates.TemplateResponse(
        "dashboard.html", {
            "request": request,
            "user": user,
            "clients": clients,
            "counts": counts,
            "healthy_count": healthy,
            "warning_count": warning,
            "overdue_count": overdue,
            "total_open_tasks": total_open,
            "total_tasks": total_tasks,
            "today_tasks": today_tasks,
            "today_meetings": today_meetings,
            "now": now,
            "has_mr": has_mr,
        },
    )


# ============================================================================
# MY DAY
# ============================================================================

@app.get("/today", response_class=HTMLResponse)
async def my_day(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    today = datetime.now().date()
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today + timedelta(days=1), datetime.min.time())

    # Задачи на сегодня
    q = db.query(Task).filter(Task.due_date >= start, Task.due_date < end)
    if user.role == "manager":
        q = q.join(Client).filter(Client.manager_email == user.email)
    today_tasks = q.all()

    # Встречи на сегодня
    q2 = db.query(Meeting).filter(Meeting.date >= start, Meeting.date < end)
    if user.role == "manager":
        q2 = q2.join(Client).filter(Client.manager_email == user.email)
    today_meetings = q2.all()

    # Просроченные задачи
    q3 = db.query(Task).filter(Task.due_date < start, Task.status.in_(["plan", "in_progress"]))
    if user.role == "manager":
        q3 = q3.join(Client).filter(Client.manager_email == user.email)
    overdue_tasks = q3.all()

    # Общая статистика
    total_open = db.query(Task).filter(Task.status.in_(["plan", "in_progress"])).count()
    if user.role == "manager":
        total_open = db.query(Task).join(Client).filter(
            Task.status.in_(["plan", "in_progress"]),
            Client.manager_email == user.email
        ).count()

    return templates.TemplateResponse("today.html", {
        "request": request, "user": user,
        "today_tasks": today_tasks,
        "today_meetings": today_meetings,
        "overdue_tasks": overdue_tasks,
        "total_open": total_open,
        "now": datetime.now(),
    })


# ============================================================================
# CLIENTS LIST
# ============================================================================

@app.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    segment = request.query_params.get("segment")
    now = datetime.now()
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if segment:
        q = q.filter(Client.segment == segment)
    clients = q.all()

    counts = {"ENT": 0, "SME+": 0, "SME-": 0, "SME": 0, "SMB": 0, "SS": 0}
    for c in clients:
        seg = c.segment or ""
        if seg in counts:
            counts[seg] += 1

    for c in clients:
        open_tasks = db.query(Task).filter(Task.client_id == c.id, Task.status.in_(["plan", "in_progress"])).count()
        blocked_tasks = db.query(Task).filter(Task.client_id == c.id, Task.status == "blocked").count()
        is_overdue = c.needs_checkup and (not c.last_meeting_date or (now - c.last_meeting_date).days > 30)
        is_warning = c.needs_checkup and c.last_meeting_date and 14 < (now - c.last_meeting_date).days <= 30
        c.open_tasks = open_tasks
        c.blocked_tasks = blocked_tasks
        c.status = {"color": "red" if is_overdue else ("yellow" if is_warning else "green")}

    return templates.TemplateResponse("clients.html", {
        "request": request, "user": user, "clients": clients,
        "counts": counts, "segment": segment, "now": now,
    })


# ============================================================================
# CLIENT DETAIL + PREP
# ============================================================================

@app.get("/client/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)

    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    tasks = db.query(Task).filter(Task.client_id == client_id).order_by(Task.due_date.desc()).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).all()

    return templates.TemplateResponse("client_detail.html", {
        "request": request, "user": user, "client": client,
        "tasks": tasks, "meetings": meetings, "now": datetime.now(),
    })


@app.get("/prep/{client_id}", response_class=HTMLResponse)
async def prep_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)

    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    tasks = db.query(Task).filter(Task.client_id == client_id, Task.status.in_(["plan", "in_progress"])).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(5).all()

    # AI-подготовка
    try:
        prep_text = generate_prep_brief(client, tasks, meetings)
    except Exception as e:
        prep_text = f"AI недоступен: {e}"

    return templates.TemplateResponse("prep.html", {
        "request": request, "user": user, "client": client,
        "tasks": tasks, "meetings": meetings, "prep_text": prep_text,
        "now": datetime.now(),
    })


# ============================================================================
# FOLLOWUP
# ============================================================================

@app.get("/followup/{client_id}", response_class=HTMLResponse)
async def followup_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)

    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(3).all()

    # Последняя встреча с pending followup (или просто последняя)
    meeting = db.query(Meeting).filter(
        Meeting.client_id == client_id,
        Meeting.followup_status == "pending"
    ).order_by(Meeting.date.desc()).first()
    if not meeting:
        meeting = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).first()

    try:
        followup_text = generate_smart_followup(client, tasks, meetings)
    except Exception as e:
        followup_text = f"AI недоступен: {e}"

    return templates.TemplateResponse("followup.html", {
        "request": request, "user": user, "client": client,
        "tasks": tasks, "meetings": meetings, "followup_text": followup_text,
        "meeting": meeting, "now": datetime.now(),
    })


# ============================================================================
# ONBOARDING PARTNER
# ============================================================================

@app.get("/onboarding/{client_id}", response_class=HTMLResponse)
async def onboarding_partner_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)

    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    return templates.TemplateResponse("onboarding_partner.html", {
        "request": request, "user": user, "client": client,
        "now": datetime.now(),
    })


# ============================================================================
# TASKS
# ============================================================================

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)

    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    status_filter = request.query_params.get("status")
    q = db.query(Task)
    if user.role == "manager":
        q = q.join(Client).filter(Client.manager_email == user.email)
    if status_filter and status_filter != "all":
        q = q.filter(Task.status == status_filter)
    tasks = q.order_by(Task.due_date.desc()).limit(100).all()

    return templates.TemplateResponse("tasks.html", {
        "request": request, "user": user, "tasks": tasks,
        "status_filter": status_filter or "all", "now": datetime.now(),
    })


@app.get("/kanban", response_class=HTMLResponse)
async def kanban_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Kanban-доска задач."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kanban.html", {"request": request, "user": user})


# ============================================================================
# SYNC
# ============================================================================

@app.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    mr_login_val, _ = _get_user_cred(user)
    return templates.TemplateResponse("sync.html", {"request": request, "user": user, "mr_login": mr_login_val})


# ============================================================================
# PLAN & QBR PAGES
# ============================================================================

@app.get("/client/{client_id}/plan", response_class=HTMLResponse)
async def plan_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    plan = db.query(AccountPlan).filter(AccountPlan.client_id == client_id).first()
    if not plan:
        plan = AccountPlan(client_id=client_id)

    return templates.TemplateResponse("plan.html", {
        "request": request, "user": user, "client": client, "plan": plan,
    })


@app.get("/client/{client_id}/qbr", response_class=HTMLResponse)
async def qbr_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    qbr = db.query(QBR).filter(QBR.client_id == client_id).order_by(QBR.date.desc()).first()

    return templates.TemplateResponse("qbr.html", {
        "request": request, "user": user, "client": client, "qbr": qbr,
    })


# ============================================================================
# INTEGRATIONS
# ============================================================================

@app.get("/integrations", response_class=HTMLResponse)
async def integrations_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    mr_login_val, mr_password_val = _get_user_cred(user)
    u_settings = user.settings or {}
    ktalk_s = u_settings.get("ktalk", {})
    airtable_s = u_settings.get("airtable", {})
    sheets_s = u_settings.get("sheets", {})
    groq_s = u_settings.get("groq", {})
    tbank_s = u_settings.get("tbank_time", {})
    tg_s = u_settings.get("telegram", {})
    airtable_active = bool(airtable_s.get("pat") or env.AIRTABLE_PAT)
    sheets_active = bool(sheets_s.get("spreadsheet_id") or env.SHEETS_ID)
    ai_active = bool(groq_s.get("api_key") or env.AI_ACTIVE)
    ai_type = "qwen" if env.QWEN_KEY else ("groq" if (groq_s.get("api_key") or env.GROQ_KEY) else "")
    integrations_data = {
        "mr_active": bool(mr_login_val and mr_password_val),
        "mr_login": mr_login_val,
        "airtable_active": airtable_active,
        "sheets_active": sheets_active,
        "sheets_id": env.SHEETS_ID,
        "tg_active": bool(env.TG_TOKEN),
        "ai_active": ai_active,
        "ai_type": ai_type,
        "ktalk_active": bool(env.KTALK_TOKEN and env.KTALK_SPACE),
        "ktalk_space": os.environ.get("KTALK_SPACE", ""),
        "time_active": bool(env.TIME_TOKEN),
    }
    return templates.TemplateResponse("integrations.html", {"request": request, "user": user, **integrations_data})


# ============================================================================
# API: INTEGRATION TESTS
# ============================================================================

@app.get("/api/integrations/test/merchrules")
async def test_merchrules(login: str = "", password: str = ""):
    if not login or not password:
        return {"error": "Need login and password"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post(
                "https://merchrules-qa.any-platform.ru/backend-v2/auth/login",
                json={"username": login, "password": password},
            )
        if resp.status_code == 200:
            return {"ok": True}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/integrations/test/ktalk")
async def test_ktalk(space: str = "", token: str = ""):
    if not space or not token:
        return {"error": "Need space and token"}
    import httpx
    try:
        base = f"https://{space}.ktalk.ru"
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get(f"{base}/api/v1/spaces/{space}/users",
                headers={"Content-Type": "application/json", "X-Auth-Token": token},
                params={"limit": 1})
        if resp.status_code == 200:
            return {"ok": True, "space": space}
        return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/integrations/test/tbank")
async def test_tbank(token: str = ""):
    if not token:
        return {"error": "Need token"}
    time_url = env.TIME_URL
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get(f"{time_url}/api/v1/tickets",
                params={"limit": 1},
                headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            return {"ok": True}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle incoming Telegram updates"""
    from tg_bot import handle_update, send_message
    from sheets import get_top50_data

    update = await request.json()
    user_id = (update.get("message", {}) or {}).get("from", {}).get("id", 0)

    # Get clients for this user
    def get_clients_fn():
        # For now, return all clients (can filter by user later)
        return [{"id": c.id, "name": c.name, "segment": c.segment or "",
                 "last_checkup": c.last_checkup, "last_meeting": c.last_meeting_date}
                for c in db.query(Client).all()]

    async def get_top50_fn():
        my_clients = [c.name for c in db.query(Client).all()]
        return await get_top50_data(my_clients)

    try:
        await handle_update(update, get_clients_fn, get_top50_fn)
    except Exception as e:
        logger.error(f"TG webhook error: {e}")
        try:
            chat_id = (update.get("message", {}) or {}).get("chat", {}).get("id")
            if chat_id:
                await send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")
        except Exception:
            pass

    return {"ok": True}


# ============================================================================
# API: KTALK
# ============================================================================

@app.post("/api/ktalk/notify")
async def api_ktalk_notify(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Send notification to Ktalk channel"""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    data = await request.json()
    webhook_url = env.KTALK_WEBHOOK
    if not webhook_url:
        return {"error": "KTALK_WEBHOOK_URL not set"}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            await hx.post(webhook_url, json={"text": data.get("text", "")})
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ktalk/followup")
async def api_ktalk_followup(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Send meeting followup to Ktalk"""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    data = await request.json()
    client_name = data.get("client", "")
    summary = data.get("summary", "")
    tasks = data.get("tasks", [])

    webhook_url = env.KTALK_WEBHOOK
    if not webhook_url:
        return {"error": "KTALK_WEBHOOK_URL not set"}

    text = f"📋 **Followup: {client_name}**\n\n{summary}"
    if tasks:
        text += "\n\n**Задачи:**\n" + "\n".join(f"• {t}" for t in tasks)

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            await hx.post(webhook_url, json={"text": text})
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# API: MERCHRULES SYNC
# ============================================================================

@app.post("/api/sync/extension")
async def api_sync_extension(request: Request, db: Session = Depends(get_db)):
    """
    Приём данных синхронизации от Chrome-расширения AM Hub Sync.
    Авторизация через Bearer токен (JWT) в заголовке Authorization.
    """
    # Авторизация через Bearer header (расширение не использует cookie)
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token required")

    from auth import decode_access_token
    payload_jwt = decode_access_token(token)
    if not payload_jwt:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == int(payload_jwt.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    data = await request.json()
    accounts = data.get("accounts", [])

    if not accounts:
        return {"ok": False, "error": "No accounts in payload"}

    clients_synced = 0
    tasks_synced = 0
    meetings_synced = 0

    for acc in accounts:
        site_id = str(acc.get("id", "")).strip()
        if not site_id:
            continue

        # Найти или создать клиента
        client = db.query(Client).filter(Client.merchrules_account_id == site_id).first()
        if not client:
            client = Client(
                merchrules_account_id=site_id,
                name=acc.get("name") or f"Site {site_id}",
                manager_email=user.email,
                segment=acc.get("segment") or "SMB",
                domain=acc.get("domain"),
            )
            db.add(client)
            db.flush()
        else:
            # Обновляем имя и сегмент если пришли
            if acc.get("name"):
                client.name = acc["name"]
            if acc.get("segment"):
                client.segment = acc["segment"]
            if acc.get("domain"):
                client.domain = acc["domain"]
            if acc.get("health_score") is not None:
                client.health_score = float(acc["health_score"])

        # Гарантируем привязку к менеджеру
        if not client.manager_email:
            client.manager_email = user.email

        clients_synced += 1

        # Задачи
        for t in acc.get("tasks", []):
            mr_task_id = str(t.get("id", "")).strip()
            if not mr_task_id:
                continue
            existing = db.query(Task).filter(Task.merchrules_task_id == mr_task_id).first()
            if existing:
                # Обновляем статус
                existing.status = t.get("status", existing.status)
                existing.priority = t.get("priority", existing.priority)
            else:
                due = None
                if t.get("due_date"):
                    try:
                        due = datetime.fromisoformat(str(t["due_date"])[:19])
                    except Exception:
                        pass
                db.add(Task(
                    client_id=client.id,
                    merchrules_task_id=mr_task_id,
                    title=t.get("title") or "",
                    status=t.get("status") or "plan",
                    priority=t.get("priority") or "medium",
                    source="roadmap",
                    due_date=due,
                    team=t.get("team"),
                    task_type=t.get("task_type"),
                ))
                tasks_synced += 1

        # Встречи
        for m in acc.get("meetings", []):
            mr_meeting_id = str(m.get("id", "")).strip()
            if not mr_meeting_id:
                continue
            ext_id = f"mr_{mr_meeting_id}"
            existing = db.query(Meeting).filter(Meeting.external_id == ext_id).first()
            if not existing:
                meeting_date = None
                raw_date = m.get("date")
                if raw_date:
                    try:
                        meeting_date = datetime.fromisoformat(str(raw_date)[:19])
                    except Exception:
                        pass
                if meeting_date:
                    db.add(Meeting(
                        client_id=client.id,
                        date=meeting_date,
                        type=m.get("type") or "meeting",
                        title=m.get("title"),
                        summary=m.get("summary"),
                        source="merchrules",
                        external_id=ext_id,
                        followup_status="pending",
                    ))
                    meetings_synced += 1
                    # Обновляем last_meeting_date на клиенте
                    if not client.last_meeting_date or meeting_date > client.last_meeting_date:
                        client.last_meeting_date = meeting_date

        # Метрики
        metrics = acc.get("metrics")
        if metrics and isinstance(metrics, dict):
            hs = metrics.get("health_score") or metrics.get("healthScore")
            if hs is not None:
                client.health_score = float(hs)

    db.commit()

    # Логируем
    db.add(SyncLog(
        integration="extension",
        resource_type="accounts",
        action="push",
        status="success",
        records_processed=clients_synced,
        sync_data={"tasks": tasks_synced, "meetings": meetings_synced},
    ))
    db.commit()

    logger.info(f"Extension sync: {clients_synced} clients, {tasks_synced} tasks, {meetings_synced} meetings (user={user.email})")
    return {
        "ok": True,
        "clients_synced": clients_synced,
        "tasks_synced": tasks_synced,
        "meetings_synced": meetings_synced,
    }


@app.get("/api/auth/token")
async def api_get_token(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Вернуть JWT токен текущего пользователя — для настройки расширения."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload_jwt = decode_access_token(auth_token)
    if not payload_jwt:
        raise HTTPException(status_code=401)
    return {"token": auth_token}


@app.post("/api/sync/merchrules")
async def api_sync_merchrules(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Синхронизация с Merchrules — пробует QA и Production."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    body = await request.json()
    settings = user.settings or {}
    mr = settings.get("merchrules", {})
    login = body.get("login") or mr.get("login") or env.MR_LOGIN
    password = body.get("password") or mr.get("password") or env.MR_PASSWORD
    site_ids_input = body.get("site_ids") or mr.get("site_ids") or settings.get("merchrules_site_ids", [])

    if not login or not password:
        return {"error": "Нужны креды Merchrules"}

    # Сохраняем креды
    mr["login"] = login
    mr["password"] = password
    if site_ids_input:
        settings["merchrules_site_ids"] = site_ids_input
    settings["merchrules"] = mr
    user.settings = settings
    db.commit()

    # Пробуем авторизацию — все URL + все варианты поля логина
    import httpx
    urls_to_try = list(dict.fromkeys([
        _env("MERCHRULES_API_URL", "https://merchrules.any-platform.ru"),
        "https://merchrules.any-platform.ru",
        "https://merchrules-qa.any-platform.ru",
    ]))
    login_fields = ["email", "login", "username"]
    base_url = None
    token = None
    last_error = ""
    attempts_log = []

    async with httpx.AsyncClient(timeout=30) as hx:
        outer_break = False
        for url in urls_to_try:
            if outer_break:
                break
            for field in login_fields:
                try:
                    resp = await hx.post(
                        f"{url}/backend-v2/auth/login",
                        json={field: login, "password": password},
                        timeout=15,
                    )
                    attempt_info = f"{url} [{field}] → {resp.status_code}"
                    if resp.status_code == 200:
                        body_resp = resp.json()
                        token = body_resp.get("token") or body_resp.get("access_token") or body_resp.get("accessToken")
                        if token:
                            base_url = url
                            logger.info(f"✅ Merchrules auth OK on {url} with field={field}")
                            outer_break = True
                            break
                        else:
                            last_error = f"Нет токена в ответе ({field}): {body_resp}"
                            attempts_log.append(attempt_info + " [no token]")
                    else:
                        last_error = f"HTTP {resp.status_code} [{field}]: {resp.text[:200]}"
                        attempts_log.append(attempt_info)
                except Exception as e:
                    last_error = str(e)
                    attempts_log.append(f"{url} [{field}] → error: {e}")

    if not token:
        detail = " | ".join(attempts_log[-4:]) if attempts_log else last_error
        logger.error(f"Merchrules auth failed. Attempts: {attempts_log}")
        return {"error": f"Ошибка авторизации Merchrules. {detail}"}

    headers = {"Authorization": f"Bearer {token}"}
    logger.info(f"Using Merchrules base URL: {base_url}")

    synced_clients = 0
    synced_tasks = 0

    try:
        # Если указаны site_id — используем их
        if site_ids_input:
            for sid in site_ids_input:
                sid = str(sid).strip()
                if not sid:
                    continue
                c = db.query(Client).filter(Client.merchrules_account_id == sid).first()
                if not c:
                    c = Client(merchrules_account_id=sid, name=f"Site {sid}", manager_email=user.email, segment="SMB")
                    db.add(c)
                    db.flush()

                # Tasks
                try:
                    r_tasks = await hx.get(f"{base_url}/backend-v2/tasks", params={"site_id": sid, "limit": 50}, headers=headers, timeout=15)
                    if r_tasks.status_code == 200:
                        tasks_data = r_tasks.json()
                        tasks_list = tasks_data.get("tasks") or tasks_data.get("items") or []
                        for t in tasks_list[:20]:
                            existing = db.query(Task).filter(Task.merchrules_task_id == str(t.get("id"))).first()
                            if not existing:
                                db.add(Task(client_id=c.id, merchrules_task_id=str(t.get("id")),
                                    title=t.get("title",""), status=t.get("status","plan"),
                                    priority=t.get("priority","medium"), source="roadmap"))
                                synced_tasks += 1
                except Exception as e:
                    logger.warning(f"Failed to fetch tasks for {sid}: {e}")

                # Meetings
                try:
                    r_meetings = await hx.get(f"{base_url}/backend-v2/meetings", params={"site_id": sid, "limit": 10}, headers=headers, timeout=15)
                    if r_meetings.status_code == 200:
                        meetings_data = r_meetings.json()
                        meetings_list = meetings_data.get("meetings") or meetings_data.get("items") or []
                        if meetings_list:
                            last_mtg = max(meetings_list, key=lambda m: m.get("date", ""))
                            try:
                                c.last_meeting_date = datetime.fromisoformat(last_mtg.get("date", "")[:19])
                            except:
                                pass
                except Exception as e:
                    logger.warning(f"Failed to fetch meetings for {sid}: {e}")

                synced_clients += 1
        else:
            # Без site_ids — получаем все аккаунты менеджера
            accounts = []
            accounts_endpoint_log = []

            # Пробуем несколько возможных endpoint'ов
            for ep in [
                f"{base_url}/backend-v2/accounts",
                f"{base_url}/backend-v2/sites",
                f"{base_url}/backend-v2/accounts?limit=500",
                f"{base_url}/backend-v2/sites?limit=500",
            ]:
                try:
                    r = await hx.get(ep, headers=headers, timeout=20)
                    accounts_endpoint_log.append(f"{ep} → {r.status_code}")
                    if r.status_code == 200:
                        data = r.json()
                        # Пробуем разные ключи в ответе
                        for key in ("accounts", "sites", "items", "data", "results"):
                            if isinstance(data.get(key), list) and data[key]:
                                accounts = data[key]
                                break
                        # Если ответ сам список
                        if not accounts and isinstance(data, list):
                            accounts = data
                        if accounts:
                            logger.info(f"✅ Accounts from {ep}: {len(accounts)}")
                            break
                except Exception as e:
                    accounts_endpoint_log.append(f"{ep} → error: {e}")

            if not accounts:
                return {
                    "error": "Не удалось получить список аккаунтов. Попробуйте указать Site ID вручную.",
                    "endpoints_tried": accounts_endpoint_log,
                    "hint": "Укажите site_id через запятую в поле Site ID, например: 2262, 5335, 8049"
                }

            logger.info(f"Syncing {len(accounts)} accounts for {user.email}")

            for acc in accounts:
                aid = acc.get("id") or acc.get("site_id") or acc.get("siteId")
                if not aid:
                    continue
                site_id = str(aid)

                # Название аккаунта — пробуем разные поля
                acc_name = (
                    acc.get("name") or acc.get("title") or
                    acc.get("company") or acc.get("domain") or
                    f"Account {site_id}"
                )

                # Сегмент если есть
                acc_segment = acc.get("segment") or acc.get("tariff") or acc.get("plan") or None

                c = db.query(Client).filter(Client.merchrules_account_id == site_id).first()
                if not c:
                    c = Client(
                        merchrules_account_id=site_id,
                        name=acc_name,
                        manager_email=user.email,
                        segment=acc_segment,
                    )
                    db.add(c)
                    db.flush()
                else:
                    # Обновляем имя и менеджера
                    c.name = acc_name
                    if not c.manager_email:
                        c.manager_email = user.email
                    if acc_segment and not c.segment:
                        c.segment = acc_segment

                # Tasks
                try:
                    r_tasks = await hx.get(
                        f"{base_url}/backend-v2/tasks",
                        params={"site_id": site_id, "limit": 100},
                        headers=headers, timeout=15,
                    )
                    if r_tasks.status_code == 200:
                        td = r_tasks.json()
                        tasks_list = td.get("tasks") or td.get("items") or (td if isinstance(td, list) else [])
                        for t in tasks_list:
                            tid = str(t.get("id", ""))
                            if not tid:
                                continue
                            existing = db.query(Task).filter(Task.merchrules_task_id == tid).first()
                            if not existing:
                                db.add(Task(
                                    client_id=c.id,
                                    merchrules_task_id=tid,
                                    title=t.get("title") or t.get("name") or "",
                                    status=t.get("status", "plan"),
                                    priority=t.get("priority", "medium"),
                                    source="roadmap",
                                    team=t.get("team") or t.get("assignee") or None,
                                ))
                                synced_tasks += 1
                            else:
                                # Обновляем статус
                                existing.status = t.get("status", existing.status)
                except Exception as e:
                    logger.warning(f"Tasks fetch failed for site {site_id}: {e}")

                # Meetings — последняя дата
                try:
                    r_meetings = await hx.get(
                        f"{base_url}/backend-v2/meetings",
                        params={"site_id": site_id, "limit": 10},
                        headers=headers, timeout=15,
                    )
                    if r_meetings.status_code == 200:
                        md = r_meetings.json()
                        meetings_list = md.get("meetings") or md.get("items") or (md if isinstance(md, list) else [])
                        dates = []
                        for m in meetings_list:
                            d = m.get("date") or m.get("meeting_date") or m.get("createdAt", "")
                            if d:
                                dates.append(str(d)[:19])
                        if dates:
                            try:
                                c.last_meeting_date = datetime.fromisoformat(max(dates))
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning(f"Meetings fetch failed for site {site_id}: {e}")

                synced_clients += 1

        db.commit()
        return {
            "ok": True,
            "clients_synced": synced_clients,
            "tasks_synced": synced_tasks,
            "base_url": base_url,
            "message": f"Синхронизировано: {synced_clients} клиентов, {synced_tasks} задач",
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Merchrules sync error: {e}")
        return {"error": str(e)}


@app.post("/api/auth/taim/test")
async def api_test_taim(request: Request, auth_token: Optional[str] = Cookie(None)):
    """Проверить авторизацию в 1Time (time.tbank.ru / Mattermost)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    body = await request.json()
    login_id = body.get("login", "")
    password = body.get("password", "")
    import taim
    result = await taim.login(login_id, password)
    if result["ok"]:
        summary = await taim.get_summary(login_id, password)
        return {**result, **summary, "password": None}
    return result


@app.post("/api/auth/ktalk/test")
async def api_test_ktalk(request: Request, auth_token: Optional[str] = Cookie(None)):
    """Проверить OIDC авторизацию в KTalk (tbank.ktalk.ru)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    body = await request.json()
    login_id = body.get("login", "")
    password = body.get("password", "")
    import ktalk
    oidc_cfg = await ktalk._get_oidc_config()
    result = await ktalk.login(login_id, password)
    return {**result, "oidc_grant_types": oidc_cfg.get("grant_types_supported", []), "password": None}


@app.get("/auth/ktalk", response_class=HTMLResponse)
async def ktalk_oauth_start(request: Request, auth_token: Optional[str] = Cookie(None)):
    """
    Запускает OIDC авторизацию через браузер.
    Редиректит пользователя на страницу входа KTalk.
    """
    if not auth_token:
        return RedirectResponse(url="/login")
    import secrets, urllib.parse
    state = secrets.token_urlsafe(16)
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/ktalk/callback"
    params = urllib.parse.urlencode({
        "client_id": "KTalk",
        "response_type": "id_token token",
        "scope": "profile email allatclaims",
        "redirect_uri": redirect_uri,
        "nonce": secrets.token_urlsafe(16),
        "state": state,
    })
    return RedirectResponse(url=f"https://tbank.ktalk.ru/api/authorize/oidc/connect/authorize?{params}")


@app.get("/auth/ktalk/callback", response_class=HTMLResponse)
async def ktalk_oauth_callback(request: Request):
    """
    Callback страница после OIDC авторизации KTalk.
    Токен приходит в URL-фрагменте (#), JS читает его и отправляет на бэкенд.
    """
    return HTMLResponse(content="""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>KTalk — авторизация</title>
<style>
  body{font-family:Inter,sans-serif;background:#0a0e1a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
  .card{background:#111827;border:1px solid #1e2a3a;border-radius:14px;padding:32px 40px;text-align:center;max-width:400px;}
  h2{font-size:1.2rem;margin-bottom:8px;}
  p{color:#64748b;font-size:.85rem;}
  .ok{color:#22c55e;} .err{color:#ef4444;}
</style></head>
<body><div class="card">
  <h2 id="title">⏳ Авторизация...</h2>
  <p id="msg">Получаем токен KTalk</p>
</div>
<script>
(async function(){
  const hash = window.location.hash.slice(1);
  const params = Object.fromEntries(new URLSearchParams(hash));
  const token = params.access_token || params.id_token;
  if(!token){
    document.getElementById('title').textContent = '❌ Ошибка';
    document.getElementById('msg').textContent = 'Токен не получен. Попробуйте ещё раз.';
    document.getElementById('msg').className = 'err';
    return;
  }
  try{
    const r = await fetch('/api/auth/ktalk/token', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({access_token: token, id_token: params.id_token})
    });
    const d = await r.json();
    if(d.ok){
      document.getElementById('title').textContent = '✅ Подключено!';
      document.getElementById('msg').innerHTML = 'KTalk: <b>' + (d.user?.firstname||'') + ' ' + (d.user?.surname||'') + '</b><br><br><a href="/settings" style="color:#6366f1">← Вернуться в настройки</a>';
      document.getElementById('msg').className = 'ok';
    } else {
      document.getElementById('title').textContent = '❌ Ошибка';
      document.getElementById('msg').textContent = d.error || 'Не удалось сохранить токен';
      document.getElementById('msg').className = 'err';
    }
  }catch(e){
    document.getElementById('title').textContent = '❌ Ошибка';
    document.getElementById('msg').textContent = e.message;
  }
})();
</script></body></html>""")


@app.post("/api/auth/ktalk/token")
async def api_ktalk_save_token(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Сохраняет OIDC access_token KTalk после browser-based авторизации."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    body = await request.json()
    access_token = body.get("access_token", "")
    if not access_token:
        return {"ok": False, "error": "Нет токена"}

    # Получаем данные пользователя чтобы подтвердить токен
    import ktalk as ktalk_mod
    user_info = await ktalk_mod._get_user_info(access_token)

    settings = user.settings or {}
    kt = settings.get("ktalk", {})
    kt["access_token"] = access_token
    kt["login"] = user_info.get("email", kt.get("login", ""))
    settings["ktalk"] = kt
    user.settings = settings
    db.commit()
    return {"ok": True, "user": user_info}


@app.get("/api/ktalk/calendar")
async def api_ktalk_calendar(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    days: int = 7,
):
    """Получить встречи из KTalk календаря."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    settings = user.settings or {}
    kt = settings.get("ktalk", {})
    login_id = kt.get("login", "")
    password = kt.get("password", "")
    if not login_id or not password:
        return {"error": "Укажи логин/пароль KTalk в Настройках"}

    import ktalk
    return await ktalk.get_today_meetings(login_id, password)


@app.get("/api/sync/merchrules-creds")
async def api_get_mr_creds(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить сохранённые креды Merchrules пользователя."""
    if not auth_token:
        return {}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {}
    settings = user.settings or {}
    mr = settings.get("merchrules", {})
    return {"login": mr.get("login", ""), "site_ids": settings.get("merchrules_site_ids", [])}


# ============================================================================
# API: TASK CRUD
# ============================================================================

@app.post("/api/tasks")
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
    return {"ok": True, "id": task.id}


@app.put("/api/tasks/{task_id}")
async def api_update_task(task_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        raise HTTPException(status_code=401)
    data = await request.json()
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404)
    for k, v in data.items():
        if hasattr(task, k):
            setattr(task, k, v)
    db.commit()
    return {"ok": True}


# ============================================================================
# API: AI PROCESSING
# ============================================================================

@app.post("/api/ai/process-transcript")
async def api_process_transcript(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        raise HTTPException(status_code=401)
    data = await request.json()
    transcript = data.get("transcript", "")
    try:
        result = ai_process_transcript(transcript)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/generate-followup")
async def api_generate_followup(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        raise HTTPException(status_code=401)
    data = await request.json()
    client_id = data.get("client_id")
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        return {"error": "Client not found"}
    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(3).all()
    try:
        text = generate_smart_followup(client, tasks, meetings)
        return {"text": text}
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# SETTINGS
# ============================================================================

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    settings = user.settings or {}
    rules = settings.get("rules", {
        "min_health_score": 0.5, "checkup_interval_days": 30, "warning_days": 14,
        "segments": ["ENT", "SME+", "SME-", "SMB", "SS"],
        "auto_create_tasks": True, "morning_plan_time": "09:00", "weekly_digest_day": "friday",
    })
    prefs = settings.get("preferences", {
        "theme": "dark", "dashboard_view": "cards",
        "notifications_email": True, "notifications_tg": True, "notifications_ktalk": False,
        "notif_overdue": True, "notif_new_tasks": True, "notif_blocked": True, "notif_morning": True,
    })
    return templates.TemplateResponse("settings.html", {
        "request": request, "user": user,
        "user_settings": settings,
        "rules": rules, "prefs": prefs,
    })


@app.post("/api/settings/creds")
async def api_save_creds(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить персональные креды пользователя для ВСЕХ сервисов."""
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

    # Сохраняем все сервисы: merchrules, telegram, ktalk, tbank_time, airtable, sheets, groq
    for service in ["merchrules", "telegram", "ktalk", "tbank_time", "airtable", "sheets", "groq"]:
        if service in data:
            if service not in settings:
                settings[service] = {}
            settings[service].update(data[service])

    user.settings = settings
    db.commit()
    return {"ok": True}


# ============================================================================
# ROOT
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request, auth_token: Optional[str] = Cookie(None)):
    if auth_token:
        from auth import decode_access_token
        payload = decode_access_token(auth_token)
        if payload:
            return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


# ============================================================================
# WORKFLOW: FOLLOWUP
# ============================================================================

@app.post("/api/meetings/{meeting_id}/followup/generate")
async def api_generate_followup(meeting_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """AI-генерация фолоуапа для встречи."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)

    client = db.query(Client).filter(Client.id == meeting.client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client.id, Task.status.in_(["plan", "in_progress"])).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client.id).order_by(Meeting.date.desc()).limit(3).all()

    try:
        text = generate_smart_followup(client, tasks, meetings)
        meeting.followup_text = text
        meeting.followup_status = "filled"
        db.commit()
        return {"ok": True, "text": text}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/meetings/{meeting_id}/followup/send")
async def api_send_followup(meeting_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Подтверждение отправки фолоуапа → создаётся задача done."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)

    data = await request.json()
    followup_text = data.get("text", meeting.followup_text)

    meeting.followup_status = "sent"
    meeting.followup_text = followup_text
    meeting.followup_sent_at = datetime.now()

    # Создаём задачу "Фолоуап отправлен" со статусом done
    task = Task(
        client_id=meeting.client_id,
        title=f"📧 Фолоуап: {meeting.title or meeting.type}",
        description=followup_text[:500] if followup_text else "",
        status="done",
        priority="medium",
        source="followup",
        created_from_meeting_id=meeting.id,
        confirmed_at=datetime.now(),
        confirmed_by=user.email if user else None,
    )
    db.add(task)

    # Обновляем last_meeting_date у клиента
    client = db.query(Client).filter(Client.id == meeting.client_id).first()
    if client:
        client.last_meeting_date = meeting.date or datetime.now()

    db.commit()
    return {"ok": True, "task_id": task.id}


@app.post("/api/meetings/{meeting_id}/followup/skip")
async def api_skip_followup(meeting_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Пропустить фолоуап → создаётся задача plan."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404)

    meeting.followup_status = "skipped"
    meeting.followup_skipped = True

    # Создаём задачу "Фолоуап" со статусом plan
    task = Task(
        client_id=meeting.client_id,
        title=f"📧 Фолоуап: {meeting.title or meeting.type}",
        description="Фолоуап пропущен — требуется заполнить позже",
        status="plan",
        priority="medium",
        source="followup",
        created_from_meeting_id=meeting.id,
    )
    db.add(task)
    db.commit()
    return {"ok": True, "task_id": task.id}


# ============================================================================
# WORKFLOW: TASK CONFIRMATION
# ============================================================================

@app.post("/api/tasks/{task_id}/confirm")
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
# ============================================================================

@app.post("/api/tasks/{task_id}/push-roadmap")
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

    # Получаем креды пользователя
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    settings = user.settings or {} if user else {}
    mr = settings.get("merchrules", {})
    login = mr.get("login") or env.MR_LOGIN
    password = mr.get("password") or env.MR_PASSWORD

    if not login or not password:
        return {"error": "Нужны креды Merchrules (настройки → креды)"}

    client = db.query(Client).filter(Client.id == task.client_id).first()
    if not client or not client.merchrules_account_id:
        return {"error": "У клиента нет merchrules_account_id"}

    # Push via CSV (one task)
    import httpx, io
    csv_content = f"title,description,status,priority,team,task_type,assignee,product,link,due_date\n"
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


# ============================================================================
# WORKFLOW: QBR
# ============================================================================

@app.get("/api/clients/{client_id}/qbr")
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


@app.post("/api/clients/{client_id}/qbr")
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
    return {"ok": True, "qbr_id": qbr.id}


# ============================================================================
# WORKFLOW: ACCOUNT PLAN
# ============================================================================

@app.get("/api/clients/{client_id}/plan")
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


@app.post("/api/clients/{client_id}/plan")
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
# ============================================================================

@app.get("/api/tbank/tickets/{client_name}")
async def api_tbank_tickets(client_name: str, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить тикеты Tbank Time для клиента."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    time_token = env.TIME_TOKEN
    if not time_token:
        return {"error": "TIME_API_TOKEN не настроен", "tickets": []}

    from integrations.tbank_time import sync_tickets_for_client
    try:
        result = await sync_tickets_for_client(client_name)
        return result
    except Exception as e:
        return {"error": str(e), "open_count": 0, "total_count": 0, "last_ticket": None}


@app.get("/api/tbank/tickets")
async def api_tbank_all_tickets(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить все открытые тикеты."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    time_token = env.TIME_TOKEN
    if not time_token:
        return {"error": "TIME_API_TOKEN не настроен", "tickets": []}

    from integrations.tbank_time import get_support_tickets
    try:
        clients = db.query(Client).all()
        all_tickets = []
        for c in clients:
            if c.name:
                tickets = await get_support_tickets(c.name)
                for t in tickets:
                    t["client"] = c.name
                all_tickets.extend(tickets)
        return {"tickets": all_tickets, "total": len(all_tickets)}
    except Exception as e:
        return {"error": str(e), "tickets": [], "total": 0}


# ============================================================================
# WORKFLOW: DASHBOARD ACTIONS
# ============================================================================

@app.get("/api/dashboard/actions")
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


# ============================================================================
# ONBOARDING
# ============================================================================

@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("onboarding.html", {"request": request})


@app.post("/api/onboarding/complete")
async def api_complete_onboarding(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Отметить что онбординг пройден."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    settings = user.settings or {}
    settings["onboarding_complete"] = True
    user.settings = settings
    db.commit()
    return {"ok": True}


@app.post("/api/admin/reset-data")
async def api_reset_data(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Удалить все тестовые данные (только для админа)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403)

    # Удаляем в правильном порядке (из-за FK)
    db.query(Task).delete()
    db.query(Meeting).delete()
    db.query(CheckUp).delete()
    db.query(QBR).delete()
    db.query(AccountPlan).delete()
    db.query(Client).delete()
    db.commit()
    return {"ok": True, "message": "Все данные очищены"}


@app.get("/api/onboarding/status")
async def api_onboarding_status(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Проверить, пройден ли онбординг."""
    if not auth_token:
        return {"onboarding_complete": True}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"onboarding_complete": True}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"onboarding_complete": True}
    settings = user.settings or {}
    return {"onboarding_complete": settings.get("onboarding_complete", False)}


# ============================================================================
# GLOBAL SEARCH
# ============================================================================

@app.get("/api/search")
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


# ============================================================================
# CLIENT NOTES API
# ============================================================================

@app.post("/api/clients/{client_id}/notes")
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


@app.get("/api/clients/{client_id}/notes")
async def api_get_notes(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить заметки клиента."""
    if not auth_token:
        return {"notes": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notes": []}
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).order_by(ClientNote.is_pinned.desc(), ClientNote.updated_at.desc()).all()
    return {"notes": [{"id": n.id, "content": n.content, "pinned": n.is_pinned, "created_at": n.created_at.isoformat() if n.created_at else None, "user_id": n.user_id} for n in notes]}


@app.put("/api/clients/notes/{note_id}")
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


@app.delete("/api/clients/notes/{note_id}")
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
# ============================================================================

@app.get("/api/kanban")
async def api_kanban(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
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
    tasks = q.order_by(Task.due_date.asc()).all()

    columns = {"plan": [], "in_progress": [], "review": [], "done": [], "blocked": []}
    for t in tasks:
        status = t.status or "plan"
        if status not in columns:
            columns["plan"].append(t)
        else:
            columns[status].append(t)

    return {
        col: [{"id": t.id, "title": t.title, "priority": t.priority, "due_date": t.due_date.isoformat() if t.due_date else None,
               "client_name": t.client.name if t.client else "—", "client_id": t.client_id, "team": t.team, "created_at": t.created_at.isoformat() if t.created_at else None}
              for t in tasks_list]
        for col, tasks_list in columns.items()
    }


@app.patch("/api/tasks/{task_id}/status")
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
    return {"ok": True}


# ============================================================================
# MY DAY: TIME TRACKING API
# ============================================================================

@app.post("/api/my-day/schedule")
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
    user.settings = settings
    db.commit()
    return {"ok": True}


@app.get("/api/my-day/schedule")
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


@app.get("/api/clients/{client_id}/timeline")
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
            "date": m.date.strftime("%d.%m.%Y") if m.date else "—",
            "icon": "📅",
            "title": m.title or m.type,
            "desc": (m.summary or "")[:100] + ("..." if m.summary and len(m.summary) > 100 else ""),
        })

    # Задачи
    tasks = db.query(Task).filter(Task.client_id == client_id).order_by(Task.created_at.desc()).limit(20).all()
    for t in tasks:
        events.append({
            "date": t.created_at.strftime("%d.%m.%Y") if t.created_at else "—",
            "icon": {"plan": "📝", "in_progress": "🔄", "done": "✅", "blocked": "🔴", "review": "👀"}.get(t.status, "📋"),
            "title": t.title,
            "desc": f"Статус: {t.status}" + (f" · {t.priority}" if t.priority else ""),
        })

    # Заметки
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).order_by(ClientNote.updated_at.desc()).limit(10).all()
    for n in notes:
        events.append({
            "date": n.updated_at.strftime("%d.%m.%Y") if n.updated_at else "—",
            "icon": "📝" if not n.is_pinned else "📌",
            "title": "Заметка" + (" (закреплена)" if n.is_pinned else ""),
            "desc": n.content[:100] + ("..." if len(n.content) > 100 else ""),
        })

    # Сортировка по дате
    events.sort(key=lambda e: e.get("date", ""), reverse=True)

    return {"events": events[:50]}


# ============================================================================
# SMART CHECKUPS
# ============================================================================

CHECKUP_INTERVALS = {"SS": 180, "SMB": 90, "SME": 60, "ENT": 30, "SME+": 60, "SME-": 60}

@app.get("/checkups", response_class=HTMLResponse)
async def checkups_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Страница умных чекапов."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("checkups.html", {"request": request, "user": user})


@app.get("/api/checkups")
async def api_checkups(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить список чекапов по сегментам с дедлайнами."""
    if not auth_token:
        return {"overdue": [], "due_soon": [], "upcoming": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"overdue": [], "due_soon": [], "upcoming": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"overdue": [], "due_soon": [], "upcoming": []}

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    now = datetime.now()
    overdue, due_soon, upcoming = [], [], []

    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last:
            days_since = (now - last).days
            days_until = interval - days_since
        else:
            days_since = 999
            days_until = -30

        info = {"id": c.id, "name": c.name, "segment": c.segment, "days_since": days_since, "days_until": days_until, "interval": interval, "last_date": last.isoformat() if last else None}

        if days_until < 0:
            overdue.append(info)
        elif days_until <= 14:
            due_soon.append(info)
        elif days_until <= 30:
            upcoming.append(info)

    overdue.sort(key=lambda x: x["days_until"])
    due_soon.sort(key=lambda x: x["days_until"])
    upcoming.sort(key=lambda x: x["days_until"])

    return {"overdue": overdue, "due_soon": due_soon, "upcoming": upcoming}


@app.post("/api/checkups/assign")
async def api_assign_checkup(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Назначить чекап клиенту (создать встречу)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    data = await request.json()
    client_id = data.get("client_id")
    date_str = data.get("date")
    if not client_id:
        raise HTTPException(status_code=400)
    meeting_date = datetime.fromisoformat(date_str) if date_str else datetime.now()
    meeting = Meeting(client_id=client_id, date=meeting_date, type="checkup", source="internal", title="Чекап")
    db.add(meeting)
    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        client.last_meeting_date = meeting_date
        client.needs_checkup = False
    db.commit()
    return {"ok": True, "meeting_id": meeting.id}


# ============================================================================
# FOLLOWUP TEMPLATES
# ============================================================================

@app.get("/api/followup-templates")
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


@app.post("/api/followup-templates")
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


@app.delete("/api/followup-templates/{tpl_id}")
async def api_delete_followup_template(tpl_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Удалить шаблон."""
    if not auth_token:
        raise HTTPException(status_code=401)
    tpl = db.query(FollowupTemplate).filter(FollowupTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(status_code=404)
    db.delete(tpl)
    db.commit()
    return {"ok": True}


# ============================================================================
# CALENDAR
# ============================================================================

@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Календарь встреч."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("calendar.html", {"request": request, "user": user})


@app.get("/api/calendar/events")
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


# ============================================================================
# DIAGNOSTICS & IMPORT
# ============================================================================

@app.get("/api/diagnostics/outbound-ip")
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


@app.post("/api/diagnostics/merchrules-auth")
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
        os.environ.get("MERCHRULES_API_URL", "https://merchrules.any-platform.ru"),
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


@app.post("/api/import/tasks-csv")
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


# ============================================================================
# MEETING SLOTS
# ============================================================================

@app.get("/meetings", response_class=HTMLResponse)
async def meetings_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Страница встреч со слотами дня."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("meetings.html", {"request": request, "user": user})


@app.get("/api/meetings/slots")
async def api_meetings_slots(
    date: Optional[str] = None,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Получить слоты дня (встречи + prep/followup задачи).
    date: ISO строка даты, по умолчанию — сегодня МСК.
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

    from meeting_slots import get_day_slots
    target_date = datetime.now(MSK).replace(tzinfo=None)
    if date:
        try:
            target_date = datetime.fromisoformat(date)
        except ValueError:
            pass

    slots = get_day_slots(db, user.email, target_date)
    return {"slots": slots, "date": target_date.strftime("%Y-%m-%d")}


@app.post("/api/meetings/sync-slots")
async def api_sync_meeting_slots(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Принудительно создать слоты для всех предстоящих встреч."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    from meeting_slots import create_slots_for_meeting
    now = datetime.utcnow()
    window_end = now + timedelta(days=7)

    q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
        Meeting.date >= now,
        Meeting.date <= window_end,
    )
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)

    meetings = q.all()
    total = 0
    for m in meetings:
        created = create_slots_for_meeting(db, m)
        total += len(created)

    return {"ok": True, "slots_created": total, "meetings_processed": len(meetings)}


@app.get("/api/integrations/test/outlook")
async def api_test_outlook():
    """Тест подключения к Outlook."""
    from integrations.outlook import test_connection
    result = await test_connection()
    return result


# ============================================================================
# ANALYTICS
# ============================================================================

@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Страница аналитики."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("analytics.html", {"request": request, "user": user})


@app.get("/api/analytics")
async def api_analytics(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Данные для аналитики."""
    if not auth_token:
        return {}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {}

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    # Segments
    seg_counts = {}
    for c in clients:
        seg = c.segment or "other"
        seg_counts[seg] = seg_counts.get(seg, 0) + 1

    # Health distribution
    health_buckets = {"0-25": 0, "25-50": 0, "50-75": 0, "75-100": 0}
    for c in clients:
        score = (c.health_score or 0) * 100
        if score < 25:
            health_buckets["0-25"] += 1
        elif score < 50:
            health_buckets["25-50"] += 1
        elif score < 75:
            health_buckets["50-75"] += 1
        else:
            health_buckets["75-100"] += 1

    # Tasks by status
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    all_tasks = task_q.all()
    task_status_counts = {}
    for t in all_tasks:
        s = t.status or "plan"
        task_status_counts[s] = task_status_counts.get(s, 0) + 1

    # Meetings per month (last 6 months)
    meetings_per_month = {}
    meeting_q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        meeting_q = meeting_q.filter(Client.manager_email == user.email)
    all_meetings = meeting_q.filter(Meeting.date != None).order_by(Meeting.date.desc()).all()
    for m in all_meetings:
        if m.date:
            key = m.date.strftime("%Y-%m")
            meetings_per_month[key] = meetings_per_month.get(key, 0) + 1

    return {
        "total_clients": len(clients),
        "segments": seg_counts,
        "health_distribution": health_buckets,
        "task_status": task_status_counts,
        "total_tasks": len(all_tasks),
        "meetings_per_month": dict(sorted(meetings_per_month.items(), reverse=True)[:6]),
        "avg_health": round(sum((c.health_score or 0) for c in clients) / max(len(clients), 1) * 100, 1),
    }


# ============================================================================
# BULK ACTIONS
# ============================================================================

@app.post("/api/bulk/checkups")
async def api_bulk_checkups(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Массовое назначение чекапов."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    data = await request.json()
    client_ids = data.get("client_ids", [])
    date_str = data.get("date")
    meeting_date = datetime.fromisoformat(date_str) if date_str else datetime.now()
    created = 0
    for cid in client_ids:
        client = db.query(Client).filter(Client.id == cid).first()
        if client:
            m = Meeting(client_id=cid, date=meeting_date, type="checkup", source="internal", title="Чекап")
            db.add(m)
            client.last_meeting_date = meeting_date
            client.needs_checkup = False
            created += 1
    db.commit()
    return {"ok": True, "created": created}


@app.post("/api/bulk/segment")
async def api_bulk_segment(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Массовое изменение сегмента."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    data = await request.json()
    client_ids = data.get("client_ids", [])
    segment = data.get("segment", "")
    updated = 0
    for cid in client_ids:
        client = db.query(Client).filter(Client.id == cid).first()
        if client:
            client.segment = segment
            updated += 1
    db.commit()
    return {"ok": True, "updated": updated}


# ============================================================================
# EXPORT
# ============================================================================

@app.get("/api/export/client/{client_id}")
async def api_export_client(client_id: int, fmt: str = "json", db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Экспорт отчёта по клиенту (JSON/CSV)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).all()
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).all()

    data = {
        "client": {"id": client.id, "name": client.name, "segment": client.segment, "health_score": client.health_score, "manager_email": client.manager_email},
        "tasks": [{"id": t.id, "title": t.title, "status": t.status, "priority": t.priority, "due_date": t.due_date.isoformat() if t.due_date else None} for t in tasks],
        "meetings": [{"id": m.id, "title": m.title or m.type, "date": m.date.isoformat() if m.date else None, "type": m.type} for m in meetings],
        "notes": [{"id": n.id, "content": n.content, "pinned": n.is_pinned} for n in notes],
        "exported_at": datetime.utcnow().isoformat(),
    }

    if fmt == "csv":
        import io, csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["type", "id", "title", "date", "details"])
        for t in tasks:
            writer.writerow(["task", t.id, t.title, t.due_date.isoformat() if t.due_date else "", t.status])
        for m in meetings:
            writer.writerow(["meeting", m.id, m.title or m.type, m.date.isoformat() if m.date else "", m.type])
        for n in notes:
            writer.writerow(["note", n.id, n.content[:50], "", "pinned" if n.is_pinned else ""])
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=output.getvalue(), headers={"Content-Disposition": f"attachment; filename=client_{client_id}.csv"})

    return data


# ============================================================================
# AI RECOMMENDATIONS & AUTO-QBR
# ============================================================================

@app.post("/api/ai/auto-qbr/{client_id}")
async def api_auto_qbr(client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """AI-генерация черновика QBR из данных клиента."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(10).all()

    tasks_done = [t for t in tasks if t.status == "done"]
    tasks_blocked = [t for t in tasks if t.status == "blocked"]

    prompt = f"""Создай черновик QBR для клиента {client.name} ({client.segment or '—'}).

Health Score: {(client.health_score or 0)*100:.0f}%
Задач выполнено: {len(tasks_done)}
Задач заблокировано: {len(tasks_blocked)}
Последние встречи:
{chr(10).join([f"- {m.title or m.type} ({m.date.strftime('%d.%m.%Y') if m.date else ''})" for m in meetings[:5]])}

Ответь JSON:
{"achievements": [...], "issues": [...], "next_quarter_goals": [...], "summary": "..."}"""

    try:
        text = await _ai_chat("", prompt, max_tokens=1500)
        import json, re
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
        else:
            data = {}
    except Exception as e:
        data = {"error": str(e)}

    return data


async def _ai_chat(system: str, user: str, max_tokens: int = 1000) -> str:
    """AI чат через Groq или Qwen."""
    groq_key = env.GROQ_KEY
    qwen_key = env.QWEN_KEY

    if groq_key:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60) as hx:
                resp = await hx.post("https://api.groq.com/openai/v1/chat/completions",
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens, "temperature": 0.1},
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"})
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except:
            pass

    if qwen_key:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60) as hx:
                resp = await hx.post("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    json={"model": "qwen-plus", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens, "temperature": 0.1},
                    headers={"Authorization": f"Bearer {qwen_key}", "Content-Type": "application/json"})
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except:
            pass

    return "AI недоступен. Настройте GROQ_API_KEY или QWEN_API_KEY."


# ============================================================================
# "TIME TO WRITE" SIGNALS
# ============================================================================

@app.get("/api/notifications")
async def api_notifications(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить уведомления: пора написать, просрочки и т.д."""
    if not auth_token:
        return {"notifications": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notifications": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"notifications": []}

    notifications = []
    now = datetime.now()

    # Клиенты без активности
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last:
            days_since = (now - last).days
            if days_since > interval:
                notifications.append({
                    "type": "overdue_checkup",
                    "priority": "high",
                    "message": f"Пора написать: {c.name} (последний контакт {days_since} дн. назад)",
                    "client_id": c.id,
                    "client_name": c.name,
                })
            elif days_since > interval - 14:
                notifications.append({
                    "type": "checkup_soon",
                    "priority": "medium",
                    "message": f"Скоро чекап: {c.name} (через {interval - days_since} дн.)",
                    "client_id": c.id,
                    "client_name": c.name,
                })

    # Blocked tasks
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    blocked = task_q.filter(Task.status == "blocked").all()
    for t in blocked:
        notifications.append({
            "type": "blocked_task",
            "priority": "high",
            "message": f"Заблокирована задача: {t.title} ({t.client.name if t.client else ''})",
            "client_id": t.client_id,
        })

    return {"notifications": notifications}


# ============================================================================
# FOCUS MODE (CSS toggle — client detail page with sidebar hidden)
# ============================================================================

@app.get("/client/{client_id}/focus", response_class=HTMLResponse)
async def client_focus_view(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Режим фокуса: клиент без сайдбара."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    tasks = db.query(Task).filter(Task.client_id == client_id).order_by(Task.due_date.desc()).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(5).all()
    notes = db.query(ClientNote).filter(ClientNote.client_id == client_id).order_by(ClientNote.is_pinned.desc(), ClientNote.updated_at.desc()).all()

    return templates.TemplateResponse("client_focus.html", {
        "request": request, "user": user, "client": client,
        "tasks": tasks, "meetings": meetings, "notes": notes,
    })


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ============================================================================
# VOICE NOTES
# ============================================================================

@app.post("/api/voice-notes")
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


# ============================================================================
# PERSONAL INBOX
# ============================================================================

@app.get("/inbox", response_class=HTMLResponse)
async def inbox_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Персональный Inbox."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("inbox.html", {"request": request, "user": user})


@app.get("/api/inbox")
async def api_inbox(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить сообщения Inbox."""
    if not auth_token:
        return {"items": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"items": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    items = []
    now = datetime.now()

    # Новые уведомления
    notifs = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).order_by(Notification.created_at.desc()).limit(20).all()
    for n in notifs:
        items.append({"type": "notification", "title": n.title, "message": n.message, "date": n.created_at.isoformat() if n.created_at else None, "priority": n.type})

    # Просроченные чекапы
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()
    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last and (now - last).days > interval:
            items.append({"type": "overdue", "title": f"Просрочен чекап: {c.name}", "message": f"Последний контакт {(now-last).days} дн. назад (норма: {interval})", "date": last.isoformat(), "priority": "high", "client_id": c.id})

    # Blocked tasks
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    blocked = task_q.filter(Task.status == "blocked").all()
    for t in blocked:
        items.append({"type": "blocked", "title": f"Заблокирована: {t.title}", "message": t.client.name if t.client else "", "priority": "high", "client_id": t.client_id})

    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return {"items": items[:50]}


@app.post("/api/inbox/mark-read")
async def api_inbox_mark_read(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Отметить уведомления прочитанными."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).update({"is_read": True})
    db.commit()
    return {"ok": True}


# ============================================================================
# CHURN PREDICTION
# ============================================================================

@app.get("/api/churn/risk")
async def api_churn_risk(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Прогнозирование оттока: rule-based scoring."""
    if not auth_token:
        return {"clients": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"clients": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"clients": []}

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()
    now = datetime.now()
    results = []

    for c in clients:
        score = 0
        reasons = []
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup

        # Фактор 1: Нет контакта > 2x интервала
        if last and (now - last).days > interval * 2:
            score += 40
            reasons.append(f"Нет контакта {(now-last).days} дн. (норма: {interval})")

        # Фактор 2: Low health score
        if c.health_score and c.health_score < 0.3:
            score += 30
            reasons.append(f"Низкий health score: {c.health_score:.0%}")

        # Фактор 3: Blocked tasks
        blocked = db.query(Task).filter(Task.client_id == c.id, Task.status == "blocked").count()
        if blocked > 0:
            score += 15
            reasons.append(f"{blocked} заблокированных задач")

        # Фактор 4: Нет задач вообще
        total_tasks = db.query(Task).filter(Task.client_id == c.id).count()
        if total_tasks == 0:
            score += 15
            reasons.append("Нет задач")

        risk = "low"
        if score >= 60:
            risk = "critical"
        elif score >= 30:
            risk = "medium"

        results.append({"id": c.id, "name": c.name, "segment": c.segment, "risk": risk, "score": score, "reasons": reasons})

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"clients": results}


# ============================================================================
# AI AUTO-QBR PAGE
# ============================================================================

@app.get("/qbr/auto/{client_id}", response_class=HTMLResponse)
async def qbr_auto_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Страница авто-QBR."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("qbr_auto.html", {"request": request, "user": user, "client": client})


@app.get("/voice-notes", response_class=HTMLResponse)
async def voice_notes_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Страница голосовых заметок."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("voice_notes.html", {"request": request, "user": user})


@app.get("/api/voice-notes")
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


# ============================================================================
# PWA ICONS (SVG placeholder)
# ============================================================================

@app.get("/static/icon-192.png")
@app.get("/static/icon-512.png")
async def pwa_icon():
    """SVG иконка для PWA (base64 PNG placeholder)."""
    from fastapi.responses import PlainTextResponse
    # Simple colored square as placeholder
    return PlainTextResponse(content="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNjN9GQAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAA0lEQVQI12P4z8BQDwAEgAF/QL9hbgAAAABJRU5ErkJggg==", headers={"Content-Type": "image/png"})


# ============================================================================
# PWA MANIFEST
# ============================================================================

@app.get("/manifest.json")
async def pwa_manifest():
    """PWA manifest для установки на телефон."""
    return JSONResponse(content={
        "name": "AM Hub — Account Manager",
        "short_name": "AM Hub",
        "description": "Система управления аккаунтами",
        "start_url": "/dashboard",
        "display": "standalone",
        "background_color": "#0a0e1a",
        "theme_color": "#6366f1",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                   {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    })


@app.get("/sw.js")
async def service_worker():
    """Service Worker для PWA и офлайн-кэширования."""
    from fastapi.responses import PlainTextResponse
    sw = """
const CACHE = 'amhub-v1';
const PRECACHE = ['/dashboard', '/today', '/clients', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request).then(resp => {
      if (resp.status === 200) {
        const clone = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return resp;
    }).catch(() => caches.match('/dashboard')))
  );
});

// Offline form submission queue
self.addEventListener('message', e => {
  if (e.data.type === 'SYNC_QUEUE') {
    // TODO: replay queued requests
  }
});
"""
    return PlainTextResponse(content=sw, headers={"Content-Type": "application/javascript"})


# ============================================================================
# OFFLINE / DRAFTS
# ============================================================================

@app.post("/api/drafts")
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
    user.settings = settings
    db.commit()
    return {"ok": True}


@app.get("/api/drafts")
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


# ============================================================================
# TASK MODAL API (bulk edit)
# ============================================================================

@app.patch("/api/tasks/bulk")
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
# ============================================================================

@app.get("/api/meetings/today")
async def api_meetings_today(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить встречи сегодня с ссылками."""
    if not auth_token:
        return {"meetings": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"meetings": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"meetings": []}

    now_msk = datetime.now(MSK)
    today = now_msk.date()
    tomorrow = today + timedelta(days=1)

    q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    meetings = q.filter(
        Meeting.date >= datetime.combine(today, datetime.min.time()),
        Meeting.date < datetime.combine(tomorrow, datetime.min.time()),
    ).all()

    return {
        "meetings": [{
            "id": m.id,
            "title": m.title or m.type,
            "time": m.date.strftime("%H:%M") if m.date else "—",
            "client": m.client.name if m.client else "—",
            "client_id": m.client_id,
            "link": m.recording_url or f"/client/{m.client_id}",
            "type": m.type,
        } for m in meetings]
    }
