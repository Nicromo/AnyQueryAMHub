"""
Роутер HTML-страниц.
Все страницы, отдающие HTMLResponse, вынесены сюда из main.py.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
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
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": request.query_params.get("error"),
        "bot_username": os.getenv("TG_BOT_USERNAME", ""),
    })


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="auth_token")
    return response


# ============================================================================
# TELEGRAM LOGIN — HMAC validation per core.telegram.org/widgets/login
# ============================================================================

@router.get("/auth/telegram")
async def auth_telegram(request: Request, db: Session = Depends(get_db)):
    """Callback от Telegram Login Widget.

    Ожидаемые query params: id, first_name, last_name, username, photo_url, auth_date, hash
    Валидирует HMAC-SHA256 подпись с помощью SHA256(BOT_TOKEN) как ключа.
    Создаёт/обновляет пользователя, ставит cookie auth_token, редиректит на /dashboard.
    """
    from auth import verify_tg_auth, create_access_token, hash_password

    bot_token = os.getenv("TG_BOT_TOKEN", "")
    if not bot_token:
        return RedirectResponse(url="/login?error=TG_BOT_TOKEN не настроен в .env", status_code=303)

    # Собираем data dict из query params
    qp = dict(request.query_params)
    tg_id_raw = qp.get("id")
    if not tg_id_raw:
        return RedirectResponse(url="/login?error=Нет id в callback от Telegram", status_code=303)

    # Whitelist из ALLOWED_TG_IDS
    allowed_raw = os.getenv("ALLOWED_TG_IDS", "").strip()
    if allowed_raw:
        allowed_set = {x.strip() for x in allowed_raw.split(",") if x.strip()}
        if tg_id_raw not in allowed_set:
            return RedirectResponse(
                url="/login?error=Ваш Telegram не в whitelist. Обратитесь к администратору.",
                status_code=303,
            )

    # verify_tg_auth мутирует dict (удаляет hash), поэтому передаём копию
    data_for_check = {k: v for k, v in qp.items()}
    if not verify_tg_auth(data_for_check, bot_token):
        return RedirectResponse(
            url="/login?error=Неверная подпись Telegram или устарело (&gt;1 часа)",
            status_code=303,
        )

    # Найти или создать пользователя по telegram_id
    tg_id = str(tg_id_raw)
    first_name = qp.get("first_name", "")
    last_name  = qp.get("last_name", "")
    username   = qp.get("username", "")

    user = db.query(User).filter(User.telegram_id == tg_id).first()
    if not user:
        # Создаём нового. Email подставляем синтетический — меняется в профиле.
        fallback_email = f"tg{tg_id}@amhub.local"
        # если такой email уже есть (редкий случай) — делаем уникальный
        while db.query(User).filter(User.email == fallback_email).first():
            fallback_email = f"tg{tg_id}-{int(datetime.now().timestamp())}@amhub.local"

        # Пароль — случайный хэш, пользоваться через email не будет
        import secrets
        random_pw = secrets.token_urlsafe(16)

        user = User(
            email=fallback_email,
            first_name=first_name or None,
            last_name=last_name or None,
            role="manager",  # default
            is_active=True,
            telegram_id=tg_id,
            hashed_password=hash_password(random_pw),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Обновляем имя/username если пришли
        changed = False
        if first_name and user.first_name != first_name:
            user.first_name = first_name; changed = True
        if last_name and user.last_name != last_name:
            user.last_name = last_name; changed = True
        if changed:
            db.commit()

    if not user.is_active:
        return RedirectResponse(
            url="/login?error=Аккаунт деактивирован. Обратитесь к администратору.",
            status_code=303,
        )

    # Выдаём токен и редирект
    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("ENV", "development") == "production",
        max_age=86400 * 30,
    )
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


def _classify_stream(client, recent_meetings, recent_tasks, now):
    """Определить на каком стриме сейчас клиент.

    Порядок проверок важен — более поздний стрим перекрывает ранний.
    """
    has_upcoming = any(m.date and m.date > now for m in recent_meetings)
    dated_past = [m for m in recent_meetings if m.date and m.date <= now]
    last_meeting = max(dated_past, key=lambda m: m.date) if dated_past else None
    open_tasks = [t for t in recent_tasks if t.status in ("plan", "in_progress")]

    # 5. Изменения — есть заметное число открытых задач команд
    if len(open_tasks) >= 3:
        return "changes"

    # 3. Встреча — встреча сегодня (по дате) или запланирована на сегодня
    if last_meeting and last_meeting.date and last_meeting.date.date() == now.date():
        return "meeting"
    for m in recent_meetings:
        if m.date and m.date.date() == now.date():
            return "meeting"

    # 4. Фолоуап — встреча была <= 3 дней назад
    if last_meeting and (now - last_meeting.date).days <= 3:
        return "followup"

    # 2. Подготовка — встреча в ближайшие 7 дней
    if has_upcoming:
        return "prep"

    # 1. Чек-ап — по дефолту
    return "checkup"


def _stream_hint(stream, client, recent_meetings, recent_tasks, now):
    """Короткий hint для карточки клиента на его стриме."""
    open_tasks = [t for t in recent_tasks if t.status in ("plan", "in_progress")]
    dated_past = [m for m in recent_meetings if m.date and m.date <= now]
    dated_future = [m for m in recent_meetings if m.date and m.date > now]
    last_meeting = max(dated_past, key=lambda m: m.date) if dated_past else None
    next_meeting = min(dated_future, key=lambda m: m.date) if dated_future else None

    if stream == "checkup":
        if client.last_meeting_date:
            days = (now - client.last_meeting_date).days
            return f"Молчит {days} дн."
        return "Нужен чек-ап"
    if stream == "prep":
        if next_meeting and next_meeting.date:
            delta = (next_meeting.date - now).days
            if delta == 0:
                hours = max(1, int((next_meeting.date - now).total_seconds() // 3600))
                return f"Встреча через {hours} ч."
            return f"Встреча через {delta} дн."
        return "Готовимся к встрече"
    if stream == "meeting":
        return "Встреча сегодня"
    if stream == "followup":
        if last_meeting and last_meeting.date:
            days = (now - last_meeting.date).days
            if days == 0:
                return "Саммари сегодня"
            return f"Саммари {days} дн. назад"
        return "Нужен фолоуап"
    if stream == "changes":
        return f"{len(open_tasks)} открытых задач"
    return ""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()

    settings = user.settings or {}
    # Онбординг пока отключён по запросу: пускаем сразу в дашборд.
    # Чтобы вернуть — раскомментировать следующие 2 строки.
    # if not settings.get("onboarding_complete"):
    #     return RedirectResponse(url="/onboarding", status_code=303)

    query = db.query(Client)
    if user.role == "manager":
        query = query.filter(Client.manager_email == user.email)
    clients = query.all()

    now = datetime.now()
    counts = {"ENT": 0, "SME+": 0, "SME-": 0, "SME": 0, "SMB": 0, "SS": 0}
    healthy = warning = overdue = total_open = total_tasks = 0

    # Батч-загрузка встреч и задач для всех клиентов (за 30 дней по встречам)
    client_ids = [c.id for c in clients]
    meetings_by_client: dict = {}
    tasks_by_client: dict = {}
    if client_ids:
        cutoff = now - timedelta(days=30)
        meetings_q = db.query(Meeting).filter(
            Meeting.client_id.in_(client_ids),
            Meeting.date >= cutoff,
        ).all()
        for m in meetings_q:
            meetings_by_client.setdefault(m.client_id, []).append(m)
        tasks_q = db.query(Task).filter(Task.client_id.in_(client_ids)).all()
        for t in tasks_q:
            tasks_by_client.setdefault(t.client_id, []).append(t)

    streams = {"checkup": [], "prep": [], "meeting": [], "followup": [], "changes": []}

    for c in clients:
        seg = c.segment or ""
        if seg in counts:
            counts[seg] += 1

        client_tasks = tasks_by_client.get(c.id, [])
        client_meetings = meetings_by_client.get(c.id, [])

        open_tasks = sum(1 for t in client_tasks if t.status in ("plan", "in_progress"))
        blocked_tasks = sum(1 for t in client_tasks if t.status == "blocked")
        total_client_tasks = len(client_tasks)
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

        stream_key = _classify_stream(c, client_meetings, client_tasks, now)
        c.stream = stream_key
        c.stream_hint = _stream_hint(stream_key, c, client_meetings, client_tasks, now)
        streams[stream_key].append(c)

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
        "streams": streams,
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

    # Последняя встреча с pending followup (или просто последняя)
    meeting = (
        db.query(Meeting)
        .filter(Meeting.client_id == client_id, Meeting.followup_status == "pending")
        .order_by(Meeting.date.desc())
        .first()
    ) or (meetings[0] if meetings else None)

    return templates.TemplateResponse("followup.html", {
        "request": request, "user": user, "client": client,
        "tasks": tasks, "meetings": meetings, "followup_text": followup_text,
        "meeting": meeting,
        "now": datetime.now(),
    })


# ============================================================================
# FOLLOWUP TEMPLATES PAGE
# ============================================================================

@router.get("/followup-templates", response_class=HTMLResponse)
async def followup_templates_page(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    user = _get_user(auth_token, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse("followup_templates.html", {"request": request, "user": user})


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
    u_settings = user.settings or {}
    # Развёрнутые под-блоки: шаблон ожидает rules/mr/at/tm/kt/gs/prefs как объекты с .get()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user":  user,
        "user_settings": u_settings,
        "rules": u_settings.get("rules", {}),
        "mr":    u_settings.get("mr",    {}),
        "at":    u_settings.get("at",    {}),
        "tm":    u_settings.get("tm",    {}),
        "kt":    u_settings.get("kt",    {}),
        "gs":    u_settings.get("gs",    {}),
        "prefs": u_settings.get("prefs", {}),
    })


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

