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
    AIRTABLE_TABLE = property(lambda self: _env("AIRTABLE_TABLE_ID"))
    AIRTABLE_QBR_TABLE = property(lambda self: _env("AIRTABLE_QBR_TABLE_ID"))
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
        # Schema is managed by Alembic — run `alembic upgrade head` before starting
        # For convenience, we still call init_db() to create tables if they don't exist
        # (useful for fresh deployments before first alembic run)
        init_db()

        # Seed default admin if no users exist
        with SessionLocal() as db:
            if db.query(User).count() == 0:
                import secrets, string
                random_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
                admin = User(
                    email="admin@company.ru",
                    first_name="Администратор",
                    role="admin",
                    hashed_password=hash_password(random_password),
                    settings={},
                )
                db.add(admin)
                db.commit()
                logger.warning(f"✅ Default admin: admin@company.ru / {random_password} — CHANGE PASSWORD!")

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

# ============================================================================
# ROUTERS
# ============================================================================
from routers import ai, tasks, meetings, clients, sync, settings, auth, integrations, analytics, misc

app.include_router(auth.router, tags=["auth"])
app.include_router(settings.router, tags=["settings"])
app.include_router(clients.router, tags=["clients"])
app.include_router(tasks.router, tags=["tasks"])
app.include_router(meetings.router, tags=["meetings"])
app.include_router(ai.router, tags=["ai"])
app.include_router(sync.router, tags=["sync"])
app.include_router(integrations.router, tags=["integrations"])
app.include_router(analytics.router, tags=["analytics"])
app.include_router(misc.router, tags=["misc"])


# ── Rate limiting ────────────────────────────────────────────────────────────
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Reusable auth dependency ─────────────────────────────────────────────────
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
) -> User:
    """Единый dependency для авторизации — используется в новых endpoints."""
    token = auth_token
    # Fallback: Bearer header (для API клиентов / расширения)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    from auth import decode_access_token
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user



# ── Sentry ────────────────────────────────────────────────────────────────────
from sentry_config import init_sentry
init_sentry()

# ── Redis cache (заменяем in-memory) ─────────────────────────────────────────
from redis_cache import cache_get, cache_set, cache_del, cache_key



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
        "ktalk_space": _env("KTALK_SPACE"),
        "time_active": bool(env.TIME_TOKEN),
    }
    return templates.TemplateResponse("integrations.html", {"request": request, "user": user, **integrations_data})


# ============================================================================
# API: INTEGRATION TESTS
# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

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


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, auth_token: Optional[str] = Cookie(None)):
    if auth_token:
        from auth import decode_access_token
        payload = decode_access_token(auth_token)
        if payload:
            return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


# ============================================================================
# WORKFLOW: MEETINGS CRUD
# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

@app.get("/followup-templates", response_class=HTMLResponse)
async def followup_templates_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("followup_templates.html", {"request": request, "user": user})


@app.get("/auto-tasks", response_class=HTMLResponse)
async def auto_tasks_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("auto_tasks.html", {"request": request, "user": user})


# ============================================================================

@app.get("/hub", response_class=HTMLResponse)
async def hub_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Командный центр — редирект на dashboard."""
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=302)

@app.get("/top50", response_class=HTMLResponse)
async def top50_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("top50.html", {"request": request, "user": user})

@app.get("/roadmap", response_class=HTMLResponse)
async def roadmap_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("roadmap.html", {"request": request, "user": user})

@app.get("/internal-tasks", response_class=HTMLResponse)
async def internal_tasks_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("internal_tasks.html", {"request": request, "user": user})

@app.get("/qbr-calendar", response_class=HTMLResponse)
async def qbr_calendar_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("qbr_calendar.html", {"request": request, "user": user})


# ── Ktalk DM ────────────────────────────────────────────────────────────────
# ============================================================================

@app.get("/cabinet", response_class=HTMLResponse)
async def manager_cabinet(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Личный кабинет менеджера — его клиенты + выбор из общего пула."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("cabinet.html", {"request": request, "user": user})


# ============================================================================

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


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Панель администратора."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")

    # Статистика
    total_clients  = db.query(Client).count()
    clients_with_key = db.query(Client).filter(
        Client.integration_metadata.op("->>")('diginetica_api_key').isnot(None)
    ).count()
    total_users = db.query(User).count()
    from models import CheckupResult
    total_checkups = db.query(CheckupResult).count()

    return templates.TemplateResponse("admin.html", {
        "request": request, "user": user,
        "stats": {
            "total_clients": total_clients,
            "clients_with_key": clients_with_key,
            "total_users": total_users,
            "total_checkups": total_checkups,
        },
    })


# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

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



@app.get("/checkup/result/{result_id}", response_class=HTMLResponse)
async def checkup_result_page(
    result_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Детальная страница результата чекапа."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    from models import CheckupResult
    result = db.query(CheckupResult).filter(CheckupResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404)
    client = db.query(Client).filter(Client.id == result.client_id).first()

    return templates.TemplateResponse("checkup_result.html", {
        "request": request, "user": user,
        "result": result, "client": client,
    })

# ============================================================================

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


# ============================================================================

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


# ============================================================================

# ============================================================================

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


# ============================================================================

@app.get("/kpi", response_class=HTMLResponse)
async def kpi_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Страница KPI менеджера."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kpi.html", {"request": request, "user": user})


@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Страница всех уведомлений."""
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    notifs = db.query(Notification).filter(
        Notification.user_id == user.id
    ).order_by(Notification.created_at.desc()).limit(100).all()
    return templates.TemplateResponse("notifications.html", {
        "request": request, "user": user, "notifications": notifs
    })


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


# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

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


# ============================================================================

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

# ============================================================================
# OFFLINE / DRAFTS
# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

@app.get("/kpi", response_class=HTMLResponse)
async def kpi_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token:
        return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kpi.html", {"request": request, "user": user})

# ============================================================================
# AIRTABLE WEBHOOK (push при изменении записи)
# ============================================================================

# ============================================================================
# GOOGLE SHEETS WRITE-BACK (запись статуса чекапа обратно)
# ============================================================================

# ============================================================================
# AIRTABLE WEBHOOK — push при изменении записи в Airtable
# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

@app.get("/manifest.json")
async def pwa_manifest():
    p = "static/manifest.json"
    if _os.path.exists(p): return FileResponse(p, media_type="application/manifest+json")
    return {"name":"AM Hub","short_name":"AM Hub","start_url":"/","display":"standalone",
            "background_color":"#07090f","theme_color":"#6474ff"}

@app.get("/sw.js")
async def service_worker():
    p = "static/sw.js"
    if _os.path.exists(p): return FileResponse(p, media_type="application/javascript",
                                                  headers={"Service-Worker-Allowed": "/"})
    return FileResponse("/dev/null", media_type="application/javascript")

# ============================================================================
# WEBSOCKET — real-time обновления
# ============================================================================
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, List
import asyncio, json as _json

class ConnectionManager:
    """Менеджер WebSocket соединений. Группирует по user_id."""
    def __init__(self):
        self.active: Dict[int, List[WebSocket]] = {}

    async def connect(self, user_id: int, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(user_id, []).append(ws)

    def disconnect(self, user_id: int, ws: WebSocket):
        conns = self.active.get(user_id, [])
        if ws in conns:
            conns.remove(ws)
        if not conns:
            self.active.pop(user_id, None)

    async def send(self, user_id: int, data: dict):
        """Отправить данные всем вкладкам пользователя."""
        dead = []
        for ws in self.active.get(user_id, []):
            try:
                await ws.send_text(_json.dumps(data, ensure_ascii=False, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    async def broadcast(self, data: dict):
        """Отправить всем подключённым пользователям."""
        for uid in list(self.active.keys()):
            await self.send(uid, data)

    @property
    def connected_users(self):
        return len(self.active)


ws_manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = "",
    db: Session = Depends(get_db),
):
    """
    WebSocket endpoint для real-time обновлений.
    Клиент подключается с токеном: ws://host/ws?token=<auth_token>
    
    Сервер пушит:
      {type: "task_update", task: {...}}
      {type: "notification", ...}
      {type: "stats", overdue, warning, open_tasks}
      {type: "ping"}
    """
    # Авторизация через query param token
    if not token:
        await websocket.close(code=4001)
        return

    from auth import decode_access_token
    payload = decode_access_token(token)
    if not payload:
        await websocket.close(code=4001)
        return

    user_id = int(payload.get("sub", 0))
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        await websocket.close(code=4001)
        return

    await ws_manager.connect(user_id, websocket)
    logger.info(f"WS connected: user_id={user_id}, total={ws_manager.connected_users}")

    try:
        # Сразу отправляем текущие stats
        now = datetime.now()
        q = db.query(Client)
        if user.role == "manager":
            q = q.filter(Client.manager_email == user.email)
        clients = q.all()
        overdue = warning = 0
        for c in clients:
            last = c.last_meeting_date or c.last_checkup
            if not last: continue
            days = (now - last).days
            interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
            if days > interval: overdue += 1
            elif days > interval - 14: warning += 1
        tq = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
            Task.status.in_(["plan", "in_progress", "blocked"])
        )
        if user.role == "manager":
            tq = tq.filter(Client.manager_email == user.email)
        open_tasks = tq.count()

        await websocket.send_text(_json.dumps({
            "type": "stats",
            "overdue": overdue, "warning": warning, "open_tasks": open_tasks
        }))

        # Heartbeat loop — держим соединение живым
        while True:
            try:
                # Ждём сообщение от клиента (ping) или timeout
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = _json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(_json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Каждые 30 сек шлём ping
                await websocket.send_text(_json.dumps({"type": "ping"}))
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(user_id, websocket)
        logger.info(f"WS disconnected: user_id={user_id}, total={ws_manager.connected_users}")


async def ws_notify_user(user_id: int, event_type: str, data: dict):
    """
    Хелпер для push-уведомлений конкретному пользователю.
    Вызывается из других endpoint'ов после изменения данных.
    """
    await ws_manager.send(user_id, {"type": event_type, **data})


async def ws_invalidate_stats(user_id: int, db):
    """Инвалидирует кеш stats и пушит обновлённые данные через WS."""
    cache_del(f"stats:{user_id}")
    cache_del(f"notif:{user_id}")
    # Пушим новые stats если пользователь подключён по WS
    if user_id in ws_manager.active:
        now = datetime.now()
        user = db.query(User).filter(User.id == user_id).first()
        if not user: return
        q = db.query(Client)
        if user.role == "manager": q = q.filter(Client.manager_email == user.email)
        clients = q.all()
        overdue = warning = 0
        for c in clients:
            last = c.last_meeting_date or c.last_checkup
            if not last: continue
            days = (now - last).days
            interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
            if days > interval: overdue += 1
            elif days > interval - 14: warning += 1
        tq = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
            Task.status.in_(["plan", "in_progress", "blocked"])
        )
        if user.role == "manager": tq = tq.filter(Client.manager_email == user.email)
        open_tasks = tq.count()
        await ws_manager.send(user_id, {
            "type": "stats", "overdue": overdue,
            "warning": warning, "open_tasks": open_tasks
        })


# ============================================================================
# REMAINING MISSING ENDPOINTS
# ============================================================================

# ============================================================================

@app.get("/ai-chat", response_class=HTMLResponse)
async def ai_chat_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("ai_chat.html", {"request": request, "user": user})


# ============================================================================

# ============================================================================

# ============================================================================

@app.get("/team", response_class=HTMLResponse)
async def team_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    if user.role != "admin": raise HTTPException(status_code=403, detail="Только для администраторов")
    return templates.TemplateResponse("team_dashboard.html", {"request": request, "user": user})


# ============================================================================

# ============================================================================

# ============================================================================

@app.get("/dedup", response_class=HTMLResponse)
async def dedup_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    if user.role != "admin": raise HTTPException(status_code=403)
    return templates.TemplateResponse("dedup.html", {"request": request, "user": user})


# ============================================================================

@app.get("/admin/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request, db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user or user.role != "admin": raise HTTPException(status_code=403)
    return templates.TemplateResponse("jobs_monitor.html", {"request": request, "user": user})


# ============================================================================

# ============================================================================

# ============================================================================

@app.get("/onboarding/wizard", response_class=HTMLResponse)
async def onboarding_wizard(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token: return RedirectResponse(url="/login", status_code=303)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("onboarding_wizard.html", {"request": request, "user": user})


# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

