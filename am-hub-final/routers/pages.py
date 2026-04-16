"""
Роутер HTML-страниц.
Все страницы, отдающие HTMLResponse, вынесены сюда из main.py.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import (
    Client, Task, Meeting, User, QBR, AccountPlan, CheckUp
)
from ai_assistant import generate_prep_brief, generate_smart_followup

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _get_user(auth_token: Optional[str], db: Session) -> Optional[User]:
    """Достать пользователя из cookie-токена."""
    if not auth_token:
        return None
    payload = decode_access_token(auth_token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload.get("sub"))).first()


def _login_redirect():
    return RedirectResponse(url="/login", status_code=303)


def _enrich_clients(clients, db: Session, now: datetime):
    """Добавляет open_tasks, blocked_tasks, status к каждому клиенту."""
    for c in clients:
        open_tasks = db.query(Task).filter(
            Task.client_id == c.id,
            Task.status.in_(["plan", "in_progress"])
        ).count()
        blocked_tasks = db.query(Task).filter(
            Task.client_id == c.id, Task.status == "blocked"
        ).count()
        is_overdue = c.needs_checkup and (
            not c.last_meeting_date or (now - c.last_meeting_date).days > 30
        )
        is_warning = (
            c.needs_checkup
            and c.last_meeting_date
            and 14 < (now - c.last_meeting_date).days <= 30
        )
        c.open_tasks = open_tasks
        c.blocked_tasks = blocked_tasks
        c.status = {"color": "red" if is_overdue else ("yellow" if is_warning else "green")}
    return clients


# ============================================================================
# AUTH
# ============================================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="auth_token")
    return response


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("onboarding.html", {"request": request, "user": user})


# ============================================================================
# DASHBOARD
# ============================================================================

@router.get("/", response_class=HTMLResponse)
async def index(auth_token: Optional[str] = Cookie(None)):
    if auth_token:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    settings = user.settings or {}
    if not settings.get("onboarding_complete"):
        return RedirectResponse(url="/onboarding", status_code=303)

    query = db.query(Client)
    if user.role == "manager":
        query = query.filter(Client.manager_email == user.email)
    clients = query.all()

    now = datetime.now()
    counts = {"ENT": 0, "SME+": 0, "SME-": 0, "SME": 0, "SMB": 0, "SS": 0}
    healthy = warning = overdue = total_open = total_tasks = 0

    for c in clients:
        seg = c.segment or ""
        if seg in counts:
            counts[seg] += 1

        open_tasks = db.query(Task).filter(
            Task.client_id == c.id, Task.status.in_(["plan", "in_progress"])
        ).count()
        blocked_tasks = db.query(Task).filter(
            Task.client_id == c.id, Task.status == "blocked"
        ).count()
        total_client_tasks = db.query(Task).filter(Task.client_id == c.id).count()
        total_open += open_tasks
        total_tasks += total_client_tasks

        is_overdue = c.needs_checkup and (
            not c.last_meeting_date or (now - c.last_meeting_date).days > 30
        )
        is_warning = (
            c.needs_checkup
            and c.last_meeting_date
            and 14 < (now - c.last_meeting_date).days <= 30
        )

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

    today = now.date()
    today_tasks = db.query(Task).filter(
        Task.due_date >= datetime.combine(today, datetime.min.time()),
        Task.due_date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
        Task.status.in_(["plan", "in_progress"]),
    ).all()
    if user.role == "manager":
        today_tasks = [t for t in today_tasks if t.client and t.client.manager_email == user.email]

    today_meetings = db.query(Meeting).filter(
        Meeting.date >= datetime.combine(today, datetime.min.time()),
        Meeting.date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
    ).all()

    has_mr = bool(
        os.environ.get("MERCHRULES_LOGIN") and os.environ.get("MERCHRULES_PASSWORD")
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "clients": clients,
        "counts": counts, "healthy_count": healthy,
        "warning_count": warning, "overdue_count": overdue,
        "total_open_tasks": total_open, "total_tasks": total_tasks,
        "today_tasks": today_tasks, "today_meetings": today_meetings,
        "now": now, "has_mr": has_mr,
    })


# ============================================================================
# MY DAY
# ============================================================================

@router.get("/today", response_class=HTMLResponse)
async def my_day(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    today = datetime.now().date()
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today + timedelta(days=1), datetime.min.time())

    q = db.query(Task).filter(Task.due_date >= start, Task.due_date < end)
    if user.role == "manager":
        q = q.join(Client).filter(Client.manager_email == user.email)
    today_tasks = q.all()

    q2 = db.query(Meeting).filter(Meeting.date >= start, Meeting.date < end)
    if user.role == "manager":
        q2 = q2.join(Client).filter(Client.manager_email == user.email)
    today_meetings = q2.all()

    q3 = db.query(Task).filter(Task.due_date < start, Task.status.in_(["plan", "in_progress"]))
    if user.role == "manager":
        q3 = q3.join(Client).filter(Client.manager_email == user.email)
    overdue_tasks = q3.all()

    if user.role == "manager":
        total_open = db.query(Task).join(Client).filter(
            Task.status.in_(["plan", "in_progress"]),
            Client.manager_email == user.email
        ).count()
    else:
        total_open = db.query(Task).filter(Task.status.in_(["plan", "in_progress"])).count()

    return templates.TemplateResponse("today.html", {
        "request": request, "user": user,
        "today_tasks": today_tasks, "today_meetings": today_meetings,
        "overdue_tasks": overdue_tasks, "total_open": total_open,
        "now": datetime.now(),
    })


# ============================================================================
# CLIENTS
# ============================================================================

@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Портфельный дашборд — все клиенты с мини-метриками."""
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("portfolio.html", {"request": request, "user": user})


@router.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    segment = request.query_params.get("segment")
    now = datetime.now()
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if segment:
        q = q.filter(Client.segment == segment)
    clients = _enrich_clients(q.all(), db, now)

    counts = {"ENT": 0, "SME+": 0, "SME-": 0, "SME": 0, "SMB": 0, "SS": 0}
    for c in clients:
        seg = c.segment or ""
        if seg in counts:
            counts[seg] += 1

    return templates.TemplateResponse("clients.html", {
        "request": request, "user": user, "clients": clients,
        "counts": counts, "segment": segment, "now": now,
    })


@router.get("/client/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    tasks = db.query(Task).filter(Task.client_id == client_id).order_by(Task.due_date.desc()).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).all()

    return templates.TemplateResponse("client_detail.html", {
        "request": request, "user": user, "client": client,
        "tasks": tasks, "meetings": meetings, "now": datetime.now(),
    })


# ============================================================================
# PREP & FOLLOWUP
# ============================================================================

@router.get("/prep/{client_id}", response_class=HTMLResponse)
async def prep_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    tasks = db.query(Task).filter(Task.client_id == client_id, Task.status.in_(["plan", "in_progress"])).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).limit(5).all()

    try:
        prep_text = generate_prep_brief(client, tasks, meetings)
    except Exception as e:
        prep_text = f"AI недоступен: {e}"

    return templates.TemplateResponse("prep.html", {
        "request": request, "user": user, "client": client,
        "tasks": tasks, "meetings": meetings, "prep_text": prep_text,
        "now": datetime.now(),
    })


@router.get("/followup/{client_id}", response_class=HTMLResponse)
async def followup_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

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
# TASKS & KANBAN
# ============================================================================

@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

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


@router.get("/kanban", response_class=HTMLResponse)
async def kanban_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("kanban.html", {"request": request, "user": user})


# ============================================================================
# PLAN & QBR
# ============================================================================

@router.get("/client/{client_id}/plan", response_class=HTMLResponse)
async def plan_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    plan = db.query(AccountPlan).filter(AccountPlan.client_id == client_id).first()
    if not plan:
        plan = AccountPlan(client_id=client_id)

    return templates.TemplateResponse("plan.html", {
        "request": request, "user": user, "client": client, "plan": plan,
    })


@router.get("/client/{client_id}/qbr", response_class=HTMLResponse)
async def qbr_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    qbr = db.query(QBR).filter(QBR.client_id == client_id).order_by(QBR.date.desc()).first()

    return templates.TemplateResponse("qbr.html", {
        "request": request, "user": user, "client": client, "qbr": qbr,
    })


@router.get("/qbr/auto/{client_id}", response_class=HTMLResponse)
async def qbr_auto_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("qbr_auto.html", {"request": request, "user": user, "client": client})


# ============================================================================
# SYNC, INTEGRATIONS, SETTINGS
# ============================================================================

@router.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("sync.html", {
        "request": request, "user": user,
        "mr_login": os.environ.get("MERCHRULES_LOGIN", ""),
    })


@router.get("/integrations", response_class=HTMLResponse)
async def integrations_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    ai_active = bool(
        os.environ.get("GROQ_API_KEY") or os.environ.get("API_GROQ") or os.environ.get("QWEN_API_KEY")
    )
    ai_type = (
        "qwen" if os.environ.get("QWEN_API_KEY")
        else ("groq" if (os.environ.get("GROQ_API_KEY") or os.environ.get("API_GROQ")) else "")
    )

    return templates.TemplateResponse("integrations.html", {
        "request": request, "user": user,
        "mr_active": bool(os.environ.get("MERCHRULES_LOGIN") and os.environ.get("MERCHRULES_PASSWORD")),
        "mr_login": os.environ.get("MERCHRULES_LOGIN", ""),
        "airtable_active": bool(os.environ.get("AIRTABLE_PAT")),
        "sheets_active": bool(os.environ.get("SHEETS_SPREADSHEET_ID")),
        "sheets_id": os.environ.get("SHEETS_SPREADSHEET_ID", ""),
        "tg_active": bool(os.environ.get("TG_BOT_TOKEN")),
        "ai_active": ai_active,
        "ai_type": ai_type,
        "ktalk_active": bool(os.environ.get("KTALK_API_TOKEN") and os.environ.get("KTALK_SPACE")),
        "ktalk_space": os.environ.get("KTALK_SPACE", ""),
        "time_active": bool(os.environ.get("TIME_API_TOKEN")),
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("settings.html", {"request": request, "user": user})


# ============================================================================
# MISC PAGES
# ============================================================================

@router.get("/checkups", response_class=HTMLResponse)
async def checkups_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("checkups.html", {"request": request, "user": user})


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("calendar.html", {"request": request, "user": user})


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("analytics.html", {"request": request, "user": user})


@router.get("/inbox", response_class=HTMLResponse)
async def inbox_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("inbox.html", {"request": request, "user": user})


@router.get("/voice-notes", response_class=HTMLResponse)
async def voice_notes_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("voice_notes.html", {"request": request, "user": user})


@router.get("/client/{client_id}/focus", response_class=HTMLResponse)
async def client_focus_page(request: Request, client_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("client_focus.html", {"request": request, "user": user, "client": client})


# ============================================================================
# AUTH: KTALK OAuth
# ============================================================================

