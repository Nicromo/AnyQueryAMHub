"""
AM Hub — Enterprise Account Manager Dashboard
Реальные данные из Merchrules · Персональные дашборды · AI-ассистент
"""
import os
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import (
    FastAPI, Request, Depends, HTTPException, Query, Cookie,
    Form, status
)
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import engine, get_db, Base, init_db, SessionLocal
from models import Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog
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

templates = Jinja2Templates(directory="templates")


# ============================================================================
# LIFESPAN
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup"""
    try:
        init_db()
        with SessionLocal() as db:
            if db.query(User).count() == 0:
                admin = User(
                    email="admin@company.ru",
                    first_name="Администратор",
                    role="admin",
                    hashed_password=hash_password("admin123"),
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
                domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", os.environ.get("APP_URL", ""))
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
    """Получить креды пользователя из env"""
    return os.environ.get("MERCHRULES_LOGIN", ""), os.environ.get("MERCHRULES_PASSWORD", "")


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

    try:
        followup_text = generate_smart_followup(client, tasks, meetings)
    except Exception as e:
        followup_text = f"AI недоступен: {e}"

    return templates.TemplateResponse("followup.html", {
        "request": request, "user": user, "client": client,
        "tasks": tasks, "meetings": meetings, "followup_text": followup_text,
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
    return templates.TemplateResponse("sync.html", {"request": request, "user": user, "mr_login": os.environ.get("MERCHRULES_LOGIN", "")})


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

    mr_login = os.environ.get("MERCHRULES_LOGIN", "")
    integrations_data = {
        "mr_active": bool(os.environ.get("MERCHRULES_LOGIN") and os.environ.get("MERCHRULES_PASSWORD")),
        "mr_login": mr_login,
        "airtable_active": bool(os.environ.get("AIRTABLE_PAT")),
        "sheets_active": bool(os.environ.get("SHEETS_SPREADSHEET_ID")),
        "sheets_id": os.environ.get("SHEETS_SPREADSHEET_ID", ""),
        "tg_active": bool(os.environ.get("TG_BOT_TOKEN")),
        "ai_active": bool(os.environ.get("GROQ_API_KEY") or os.environ.get("API_GROQ")),
        "email_active": bool(os.environ.get("SENDGRID_API_KEY")),
        "ktalk_active": bool(os.environ.get("KTALK_WEBHOOK_URL")),
        "time_active": bool(os.environ.get("TIME_API_TOKEN")),
    }
    return templates.TemplateResponse("integrations.html", {"request": request, "user": user, **integrations_data})


# ============================================================================
# API: INTEGRATION TESTS
# ============================================================================

@app.get("/api/integrations/test/airtable")
async def test_airtable(token: str = ""):
    if not token:
        return {"error": "No token"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get("https://api.airtable.com/v0/meta/bases", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            return {"ok": True, "bases": len(resp.json().get("bases", []))}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/integrations/test/sheets")
async def test_sheets(spreadsheet_id: str = ""):
    if not spreadsheet_id:
        return {"error": "No spreadsheet ID"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get(f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv", follow_redirects=True)
        if resp.status_code == 200:
            lines = resp.text.count('\n')
            return {"ok": True, "rows": lines}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/integrations/test/telegram")
async def test_telegram(token: str = ""):
    if not token:
        return {"error": "No token"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get(f"https://api.telegram.org/bot{token}/getMe")
        data = resp.json()
        if data.get("ok"):
            return {"ok": True, "bot": data["result"].get("first_name")}
        return {"error": data.get("description")}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/integrations/test/ai")
async def test_ai(key: str = ""):
    if not key:
        return {"error": "No key"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post("https://api.groq.com/openai/v1/chat/completions",
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        if resp.status_code == 200:
            return {"ok": True, "model": resp.json().get("model")}
        return {"error": f"HTTP {resp.status_code}: {resp.text[:100]}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/integrations/test/email")
async def test_email(key: str = ""):
    if not key:
        return {"error": "No key"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get("https://api.sendgrid.com/v3/user/profile", headers={"Authorization": f"Bearer {key}"})
        if resp.status_code == 200:
            return {"ok": True, "user": resp.json().get("username")}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/integrations/test/ktalk")
async def test_ktalk(url: str = ""):
    if not url:
        return {"error": "No URL"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.post(url, json={"text": "🔔 AM Hub: тест подключения"})
        if resp.status_code in (200, 201, 204):
            return {"ok": True}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/integrations/test/tbank")
async def test_tbank(token: str = ""):
    if not token:
        return {"error": "No token"}
    import httpx
    try:
        time_url = os.environ.get("TIME_BASE_URL", "https://time.tbank.ru")
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get(f"{time_url}/api/v1/tickets", params={"limit": 1}, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            return {"ok": True}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# TELEGRAM WEBHOOK
# ============================================================================

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
    webhook_url = os.environ.get("KTALK_WEBHOOK_URL", "")
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

    webhook_url = os.environ.get("KTALK_WEBHOOK_URL", "")
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
# API: TBANK TIME (tickets)
# ============================================================================

@app.get("/api/tbank/tickets/{client_name}")
async def api_tbank_tickets(
    client_name: str, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Get support tickets for a client from Tbank Time"""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    time_token = os.environ.get("TIME_API_TOKEN", "")
    if not time_token:
        return {"error": "TIME_API_TOKEN not set", "tickets": []}

    from integrations.tbank_time import sync_tickets_for_client
    try:
        result = await sync_tickets_for_client(client_name)
        return result
    except Exception as e:
        return {"error": str(e), "open_count": 0, "total_count": 0, "last_ticket": None}


@app.get("/api/tbank/tickets")
async def api_tbank_all_tickets(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Get all open support tickets"""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    time_token = os.environ.get("TIME_API_TOKEN", "")
    if not time_token:
        return {"error": "TIME_API_TOKEN not set", "tickets": []}

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
# API: MERCHRULES SYNC
# ============================================================================

@app.post("/api/sync/merchrules")
async def api_sync_merchrules(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    body = await request.json()
    login = body.get("login") or os.environ.get("MERCHRULES_LOGIN", "")
    password = body.get("password") or os.environ.get("MERCHRULES_PASSWORD", "")

    if not login or not password:
        return {"error": "Нужны креды Merchrules"}

    import httpx
    import asyncio
    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            from merchrules_sync import get_auth_token as mr_auth, fetch_account_analytics, fetch_checkups, fetch_roadmap_tasks
            token = await mr_auth(hx, login, password)
            if not token:
                return {"error": "Ошибка авторизации Merchrules"}
            # Fetch accounts
            r = await hx.get(
                "https://merchrules-qa.any-platform.ru/backend-v2/accounts",
                headers={"Authorization": f"Bearer {token}"},
            )
            accounts = r.json().get("accounts", []) if r.status_code == 200 else []
            # For each account, fetch data and save
            synced = 0
            for acc in accounts[:20]:  # limit
                aid = acc.get("id")
                if not aid:
                    continue
                # Check if client exists
                existing = db.query(Client).filter(Client.merchrules_account_id == str(aid)).first()
                if existing:
                    existing.name = acc.get("name") or existing.name
                    synced += 1
                    continue
                # Create new client
                analytics = await fetch_account_analytics(hx, token, str(aid))
                client = Client(
                    name=acc.get("name", f"Account {aid}"),
                    merchrules_account_id=str(aid),
                    health_score=float(analytics.get("health_score", 0) if analytics else 0),
                    revenue_trend=analytics.get("revenue_trend") if analytics else None,
                    activity_level=analytics.get("activity_level") if analytics else None,
                    manager_email=acc.get("manager_email") or login,
                    segment="SMB",
                )
                db.add(client)
                db.flush()
                # Fetch and create tasks
                tasks_data = await fetch_roadmap_tasks(hx, token, str(aid))
                for t in (tasks_data or [])[:10]:
                    db.add(Task(
                        client_id=client.id,
                        title=t.get("title", ""),
                        description=t.get("description", ""),
                        status=t.get("status", "plan"),
                        priority=t.get("priority", "medium"),
                        team=t.get("team", ""),
                        task_type=t.get("task_type", ""),
                    ))
                synced += 1
            db.commit()
        return {"ok": True, "clients_synced": synced}
    except Exception as e:
        db.rollback()
        return {"error": str(e)}


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


@app.post("/api/settings/integrations")
async def api_save_integrations(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
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
    for service in ["merchrules", "airtable", "sheets", "telegram", "groq", "ktalk", "tbank_time"]:
        if service in data:
            settings[service] = data[service]
    user.settings = settings
    db.commit()
    return {"ok": True}


@app.post("/api/settings/rules")
async def api_save_rules(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
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
    settings["rules"] = data
    user.settings = settings
    db.commit()
    return {"ok": True}


@app.post("/api/settings/prefs")
async def api_save_prefs(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
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
    if "preferences" not in settings:
        settings["preferences"] = {}
    settings["preferences"].update(data)
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
# HEALTH
# ============================================================================

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}
