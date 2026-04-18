"""
AM Hub — Enterprise Account Manager Dashboard
Реальные данные из Merchrules · Персональные дашборды · AI-ассистент
"""
# ─── Jinja2 × Python 3.14 + Starlette 1.0 compat patches ───
# Applied before any router imports so all Jinja2Templates instances get them.
try:
    from pathlib import Path as _P
    _BASE = _P(__file__).resolve().parent

    from fastapi.templating import Jinja2Templates as _J2T
    _orig_init = _J2T.__init__
    _orig_tpl  = _J2T.TemplateResponse

    def _patched_init(self, *a, **kw):
        # Rewrite bare relative "templates"/"static" → absolute under am-hub-final/
        if a and isinstance(a[0], str) and not _P(a[0]).is_absolute():
            a = (str(_BASE / a[0]),) + a[1:]
        if "directory" in kw and isinstance(kw["directory"], str) and not _P(kw["directory"]).is_absolute():
            kw["directory"] = str(_BASE / kw["directory"])
        _orig_init(self, *a, **kw)
        self.env.cache = None  # fix Py3.14 LRU-cache dict-key bug

    def _patched_tpl(self, *args, **kwargs):
        # Support old signature: TemplateResponse(name, {"request": req, ...})
        if args and isinstance(args[0], str):
            name = args[0]
            ctx = args[1] if len(args) > 1 else kwargs.pop("context", {})
            req = (ctx or {}).get("request")
            rest = list(args[2:])
            if req is not None:
                return _orig_tpl(self, req, name, ctx, *rest, **kwargs)
        return _orig_tpl(self, *args, **kwargs)

    _J2T.__init__ = _patched_init
    _J2T.TemplateResponse = _patched_tpl

    # Also patch StaticFiles for the same reason
    from fastapi.staticfiles import StaticFiles as _SF
    _orig_sf_init = _SF.__init__
    def _patched_sf_init(self, *a, **kw):
        if "directory" in kw and isinstance(kw["directory"], str) and not _P(kw["directory"]).is_absolute():
            kw["directory"] = str(_BASE / kw["directory"])
        _orig_sf_init(self, *a, **kw)
    _SF.__init__ = _patched_sf_init
except Exception as _e:
    print(f"[compat] patch skipped: {_e}")

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
    ClientNote, TaskComment, FollowupTemplate, VoiceNote, CHECKUP_INTERVALS,
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

# Absolute path so server works regardless of CWD
from pathlib import Path as _Path
BASE_DIR = _Path(__file__).resolve().parent

# cache_size=0 — workaround для Python 3.14 × Jinja2 LRU cache bug
# (tuple с dict в ключе делает его unhashable)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.cache = None  # disable template cache entirely

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

        # Auto-migrate: добавляем новые колонки если их нет в БД
        with SessionLocal() as db:
            try:
                from sqlalchemy import text as _text
                # Колонки добавленные в миграции 002 (finance + health + nps)
                _migrations = [
                    ("clients", "mrr",       "ALTER TABLE clients ADD COLUMN mrr FLOAT DEFAULT 0"),
                    ("clients", "nps_last",   "ALTER TABLE clients ADD COLUMN nps_last INTEGER"),
                    ("clients", "nps_date",   "ALTER TABLE clients ADD COLUMN nps_date TIMESTAMP"),
                    ("clients", "account_plan", "ALTER TABLE clients ADD COLUMN account_plan JSONB"),
                    ("clients", "last_qbr_date","ALTER TABLE clients ADD COLUMN last_qbr_date TIMESTAMP"),
                    ("clients", "next_qbr_date","ALTER TABLE clients ADD COLUMN next_qbr_date TIMESTAMP"),
                ]
                # Получаем список существующих колонок
                existing = {
                    row[0]
                    for row in db.execute(_text(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = 'clients'"
                    )).fetchall()
                }
                for table, col, sql in _migrations:
                    if col not in existing:
                        db.execute(_text(sql))
                        db.commit()
                        logger.info(f"✅ Auto-migrated: {table}.{col}")

                # Создаём новые таблицы из миграции 002 если не существуют
                _new_tables = {
                    "revenue_entries": """CREATE TABLE IF NOT EXISTS revenue_entries (
                        id SERIAL PRIMARY KEY, client_id INTEGER REFERENCES clients(id),
                        period VARCHAR NOT NULL, mrr FLOAT DEFAULT 0, arr FLOAT,
                        currency VARCHAR DEFAULT 'RUB', note TEXT,
                        created_at TIMESTAMP DEFAULT NOW(), updated_by VARCHAR)""",
                    "upsell_events": """CREATE TABLE IF NOT EXISTS upsell_events (
                        id SERIAL PRIMARY KEY, client_id INTEGER REFERENCES clients(id),
                        event_type VARCHAR NOT NULL, status VARCHAR DEFAULT 'identified',
                        amount_before FLOAT, amount_after FLOAT, delta FLOAT,
                        description TEXT, owner_email VARCHAR, due_date TIMESTAMP,
                        closed_at TIMESTAMP, created_at TIMESTAMP DEFAULT NOW(), created_by VARCHAR)""",
                    "health_snapshots": """CREATE TABLE IF NOT EXISTS health_snapshots (
                        id SERIAL PRIMARY KEY, client_id INTEGER REFERENCES clients(id),
                        score FLOAT NOT NULL, components JSONB,
                        calculated_at TIMESTAMP DEFAULT NOW())""",
                    "nps_entries": """CREATE TABLE IF NOT EXISTS nps_entries (
                        id SERIAL PRIMARY KEY, client_id INTEGER REFERENCES clients(id),
                        score INTEGER NOT NULL, type VARCHAR DEFAULT 'nps',
                        comment TEXT, source VARCHAR DEFAULT 'manual',
                        recorded_at TIMESTAMP DEFAULT NOW(), recorded_by VARCHAR)""",
                }
                for tname, tsql in _new_tables.items():
                    db.execute(_text(tsql))
                db.commit()
                logger.info("✅ Auto-migration complete")
            except Exception as _e:
                logger.warning(f"Auto-migration warning: {_e}")

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
from routers import account_dashboard

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
from routers import (
    auto_tasks, followup_mgmt, user_mgmt, misc_small,
    onboarding_mgmt, checkups_mgmt, admin as admin_router,
    inbox_notifications,
)
app.include_router(auto_tasks.router, tags=["auto-tasks"])
app.include_router(followup_mgmt.router, tags=["followup"])
app.include_router(user_mgmt.router, tags=["user"])
app.include_router(misc_small.router, tags=["misc-small"])
app.include_router(onboarding_mgmt.router, tags=["onboarding"])
app.include_router(checkups_mgmt.router, tags=["checkups"])
app.include_router(admin_router.router, tags=["admin"])
app.include_router(inbox_notifications.router, tags=["inbox"])
from routers import pdf_export
app.include_router(pdf_export.router, tags=["pdf"])
app.include_router(account_dashboard.router, tags=["account-dashboard"])

# ── Page routes (HTML) ───────────────────────────────────────────────────────
from routers import pages as pages_router
app.include_router(pages_router.router, tags=["pages"])

# ── Redesign (JSX + server data) ─────────────────────────────────────────────
from routers import design as design_router
app.include_router(design_router.router)

# ── SSE (real-time notifications) ────────────────────────────────────────────
from sse import router as sse_router
app.include_router(sse_router, tags=["sse"])


# ── Rate limiting ────────────────────────────────────────────────────────────
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    logger.info("✅ Rate limiting enabled")
except ImportError:
    logger.warning("⚠️  slowapi not installed — rate limiting disabled")

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

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

from pathlib import Path as _P
_EXT_DIR = _P(__file__).resolve().parent.parent / "extension"
if _EXT_DIR.exists():
    app.mount("/extension", StaticFiles(directory=str(_EXT_DIR)), name="extension")


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


# ============================================================================
# DASHBOARD
# ============================================================================

# ============================================================================
# MY DAY
# ============================================================================

# ============================================================================
# CLIENTS LIST
# ============================================================================

# ============================================================================
# CLIENT DETAIL + PREP
# ============================================================================

# ============================================================================
# FOLLOWUP
# ============================================================================

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

# ============================================================================
# SYNC
# ============================================================================

# ============================================================================
# PLAN & QBR PAGES
# ============================================================================

# ============================================================================
# INTEGRATIONS
# ============================================================================

# ============================================================================
# API: INTEGRATION TESTS
# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

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
    # Загружаем данные Top-50
    try:
        from sheets import get_top50_data
        mode = request.query_params.get("mode", "weekly")
        top50_data = await get_top50_data(user_email=user.email)
        data = top50_data if top50_data and not top50_data.get("error") else None
    except Exception:
        data = None
        mode = "weekly"
    sheets_id = os.environ.get("SHEETS_SPREADSHEET_ID", "")
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheets_id}" if sheets_id else ""
    return templates.TemplateResponse("top50.html", {
        "request": request, "user": user,
        "data": data or {"rows": [], "headers": [], "fetched_at": ""},
        "mode": mode,
        "sheet_url": sheet_url,
        "month_name": datetime.now().strftime("%B %Y"),
    })

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
    from models import QBR
    from sqlalchemy import desc as _desc
    now_dt = datetime.now()
    q_clients = db.query(Client)
    if user.role == "manager":
        q_clients = q_clients.filter(Client.manager_email == user.email)
    all_clients = q_clients.all()

    upcoming = []
    needs_qbr = []
    for c in all_clients:
        if c.next_qbr_date and c.next_qbr_date > now_dt:
            upcoming.append({"client": c, "date": c.next_qbr_date})
        if not c.last_qbr_date or (now_dt - c.last_qbr_date).days > 90:
            needs_qbr.append(c)

    qbr_history = (
        db.query(QBR).join(Client)
        .filter(Client.manager_email == user.email if user.role == "manager" else True)
        .order_by(_desc(QBR.date)).limit(20).all()
    )

    return templates.TemplateResponse("qbr_calendar.html", {
        "request": request, "user": user,
        "upcoming": sorted(upcoming, key=lambda x: x["date"]),
        "needs_qbr": needs_qbr,
        "qbr_history": qbr_history,
    })


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
    # Load per-user creds for the profile page
    try:
        from creds import load_merchrules_creds, load_airtable_token, load_grok_api_key
        _mr_url, mr_login, mr_password = load_merchrules_creds()
        at_token = load_airtable_token(user.email)
        gk = load_grok_api_key(user.email)
    except Exception:
        mr_login = mr_password = at_token = gk = None

    settings = user.settings or {}
    # profile объект для шаблона — все поля, которые нужны profile.html
    profile = {
        "name":            user.name,
        "display_name":    user.name,
        "email":           user.email,
        "role":            user.role,
        "telegram_id":     user.telegram_id,
        "settings":        settings,
        "created_at":      user.created_at,
        # Integration credentials (pre-filled from creds file)
        "mr_login":        mr_login or "",
        "mr_password":     mr_password or "",
        "airtable_token":  at_token or os.getenv("AIRTABLE_TOKEN", ""),
        "tg_notify_chat":  settings.get("tg_notify_chat", ""),
        "ktalk_webhook":   settings.get("ktalk_webhook", ""),
        "groq_api_key":    gk or os.getenv("GROQ_API_KEY", ""),
    }
    return templates.TemplateResponse("profile.html", {"request": request, "user": user, "profile": profile})


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

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ============================================================================
# VOICE NOTES
# ============================================================================

# ============================================================================

# ============================================================================

# ============================================================================

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
    p = BASE_DIR / "static" / "manifest.json"
    if p.exists(): return FileResponse(str(p), media_type="application/manifest+json")
    return {"name":"AM Hub","short_name":"AM Hub","start_url":"/","display":"standalone",
            "background_color":"#07090f","theme_color":"#6474ff"}

@app.get("/sw.js")
async def service_worker():
    p = BASE_DIR / "static" / "sw.js"
    if p.exists(): return FileResponse(str(p), media_type="application/javascript",
                                                  headers={"Service-Worker-Allowed": "/"})
    return JSONResponse({}, media_type="application/javascript")

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

