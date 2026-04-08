"""
AM Hub — главное приложение FastAPI
"""
import os
import logging
from datetime import date, timedelta, datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from database import (
    init_db, seed_clients, get_all_clients, get_client,
    get_client_meetings, get_client_tasks, get_all_tasks,
    get_today_overview, create_meeting,
    create_tasks_bulk, update_task_status, mark_meeting_tg_sent,
    upsert_client, CHECKUP_DAYS, get_meeting,
    checkup_status,
    get_checklist, init_checklist, toggle_checklist_item,
    add_checklist_item, clear_checklist,
    create_internal_task, get_internal_tasks,
    CHECKLIST_TEMPLATES,
    get_manager_client_ids, set_manager_clients, get_all_clients_for_manager,
    get_manager_profile, save_manager_profile, get_all_manager_profiles,
)
from auth import SessionManager, verify_tg_auth
from tg import build_followup_message, send_to_tg
from merchrules import sync_meeting_to_merchrules
from sheets import get_top50_data, SHEETS_SPREADSHEET_ID, SHEETS_TOP50_GID
import tg_bot
from ai_followup import process_transcript as ai_process_transcript
from merchrules_sync import (
    sync_clients_from_merchrules, get_client_mr_data, invalidate_cache as mr_invalidate
)
from airtable_sync import sync_meeting_to_airtable, import_clients_from_airtable
from database import (
    set_planned_meeting, set_checkup_rating, get_qbr_calendar, get_upcoming_meetings
)
from ktalk import send_ktalk_notification, send_ktalk_followup, test_ktalk_connection

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("TG_BOT_USERNAME", "")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ALLOWED_IDS = set(int(x) for x in os.getenv("ALLOWED_TG_IDS", "").split(",") if x.strip())

# Railway автоматически задаёт RAILWAY_PUBLIC_DOMAIN
RAILWAY_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")

app = FastAPI(title="AM Hub")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
session_mgr = SessionManager(SECRET_KEY)

# Инициализация БД при старте
init_db()
seed_clients()


@app.on_event("startup")
async def startup_event():
    """При запуске на Railway: регистрируем webhook + стартуем планировщик."""
    if BOT_TOKEN and RAILWAY_DOMAIN:
        webhook_url = f"https://{RAILWAY_DOMAIN}/tg/webhook"
        ok = await tg_bot.set_webhook(webhook_url)
        if ok:
            logging.info("TG webhook registered: %s", webhook_url)
        else:
            logging.warning("TG webhook registration failed")

    # Запускаем планировщик (утренний план, дайджест, MR sync)
    try:
        from scheduler import start_scheduler
        start_scheduler()
    except Exception as exc:
        logging.warning("Scheduler start failed: %s", exc)


# ── Хелперы ───────────────────────────────────────────────────────────────────

# checkup_status теперь живёт в database.py — импортируем оттуда

def get_user_or_redirect(request: Request):
    user = session_mgr.get_user(request)
    if not user:
        return None
    return user


def get_tg_id(user) -> Optional[int]:
    """Извлекает tg_id из объекта сессии."""
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def get_user_mr_creds(user) -> tuple[str, str]:
    """
    Возвращает (mr_login, mr_password) для текущего пользователя.
    Приоритет: профиль в БД → env-переменные.
    """
    tg_id = get_tg_id(user)
    if tg_id:
        profile = get_manager_profile(tg_id)
        if profile.get("mr_login") and profile.get("mr_password"):
            return profile["mr_login"], profile["mr_password"]
    # Fallback — глобальные переменные окружения
    return os.getenv("MERCHRULES_LOGIN", ""), os.getenv("MERCHRULES_PASSWORD", "")


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "bot_username": BOT_USERNAME,
    })


@app.get("/auth/telegram")
async def tg_callback(request: Request, response: Response):
    """Telegram Login Widget редиректит сюда с параметрами."""
    data = dict(request.query_params)
    if BOT_TOKEN and not verify_tg_auth(dict(data), BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Неверная подпись Telegram")

    tg_id = int(data.get("id", 0))
    tg_name = data.get("first_name", "") + " " + data.get("last_name", "")
    tg_username = data.get("username", "")

    # Проверяем доступ (если список не пустой)
    if ALLOWED_IDS and tg_id not in ALLOWED_IDS:
        raise HTTPException(status_code=403, detail="Доступ закрыт")

    token = session_mgr.create_session(tg_id, tg_name.strip())
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie("session", token, max_age=86400 * 7, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("session")
    return resp


# ── Главная — трекер чекапов ─────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, segment: str = "", sort: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    # Загружаем клиентов: если у менеджера есть свой список — только его
    tg_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    clients = get_all_clients_for_manager(tg_id)

    # Добавляем статус чекапа и дополнительные поля к каждому клиенту
    for c in clients:
        c["status"] = checkup_status(c.get("last_checkup") or c.get("last_meeting"), c["segment"])
        # Считаем заблокированные задачи отдельно
        all_t = get_client_tasks(c["id"], "open")
        c["blocked_tasks"] = sum(1 for t in all_t if t.get("status") == "blocked")
        # Настроение последней встречи
        meetings = get_client_meetings(c["id"], limit=1)
        c["mood"] = meetings[0]["mood"] if meetings else "neutral"

    # Фильтр по сегменту
    if segment:
        clients = [c for c in clients if c["segment"] == segment]

    # Сортировка: сначала требующие внимания
    def attention_score(c):
        s = 0
        if c["status"]["color"] == "red":      s += 100
        if c["blocked_tasks"] > 0:             s += 50
        if c.get("mood") == "risk":            s += 40
        if c["status"]["color"] == "yellow":   s += 20
        if c.get("open_tasks", 0) > 0:         s += 5
        return -s  # отрицательный — чтобы sort() ставил высокий балл первым

    clients.sort(key=attention_score)

    segments = ["ENT", "SME+", "SME-", "SME", "SMB", "SS"]
    counts = {s: sum(1 for c in clients if c["segment"] == s) for s in segments}
    has_personal_list = bool(tg_id and get_manager_client_ids(tg_id))

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "clients": clients,
        "segment": segment,
        "sort": sort,
        "segments": segments,
        "counts": counts,
        "today": date.today().isoformat(),
        "has_personal_list": has_personal_list,
        "total_all": len(get_all_clients()),
    })


# ── Подготовка к встрече ─────────────────────────────────────────────────────

@app.get("/prep/{client_id}", response_class=HTMLResponse)
async def prep_page(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404, "Клиент не найден")

    meetings = get_client_meetings(client_id, limit=5)
    open_tasks = get_client_tasks(client_id, "open")
    status = checkup_status(client.get("last_checkup"), client["segment"])

    days = CHECKUP_DAYS.get(client["segment"], 90)
    suggested_next = (date.today() + timedelta(days=days)).isoformat()

    return templates.TemplateResponse("prep.html", {
        "request": request,
        "user": user,
        "client": client,
        "meetings": meetings,
        "open_tasks": open_tasks,
        "status": status,
        "today": date.today().isoformat(),
        "checkup_days": days,
        "suggested_next": suggested_next,
    })


# ── Фолоуап ──────────────────────────────────────────────────────────────────

@app.get("/followup/{client_id}", response_class=HTMLResponse)
async def followup_page(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    last_meetings = get_client_meetings(client_id, limit=3)
    segment = client["segment"]
    days = CHECKUP_DAYS[segment]
    suggested_next = (date.today() + timedelta(days=days)).isoformat()

    return templates.TemplateResponse("followup.html", {
        "request": request,
        "user": user,
        "client": client,
        "last_meetings": last_meetings,
        "today": date.today().isoformat(),
        "suggested_next": suggested_next,
    })


@app.post("/followup/{client_id}")
async def followup_submit(
    request: Request,
    client_id: int,
    meeting_date: str = Form(...),
    meeting_type: str = Form("checkup"),
    summary: str = Form(""),
    mood: str = Form("neutral"),
    next_meeting: str = Form(""),
    send_tg: str = Form(""),
    # Задачи AnyQuery — массивы
    aq_task_text: list[str] = Form(default=[]),
    aq_task_due: list[str] = Form(default=[]),
    # Задачи клиента
    cl_task_text: list[str] = Form(default=[]),
    cl_task_due: list[str] = Form(default=[]),
):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    # Создаём встречу
    meeting_id = create_meeting(
        client_id=client_id,
        meeting_date=meeting_date,
        meeting_type=meeting_type,
        summary=summary,
        mood=mood,
        next_meeting=next_meeting or None,
    )

    # Задачи
    tasks = []
    for text, due in zip(aq_task_text, aq_task_due):
        if text.strip():
            tasks.append({"owner": "anyquery", "text": text.strip(), "due_date": due or None})
    for text, due in zip(cl_task_text, cl_task_due):
        if text.strip():
            tasks.append({"owner": "client", "text": text.strip(), "due_date": due or None})

    if tasks:
        create_tasks_bulk(meeting_id, client_id, tasks)

    # Синхронизация с MerchRules (кредсы из профиля пользователя)
    aq_tasks_list = [t for t in tasks if t["owner"] == "anyquery"]
    cl_tasks_list = [t for t in tasks if t["owner"] == "client"]
    mr_login, mr_password = get_user_mr_creds(user)
    await sync_meeting_to_merchrules(
        client_name=client["name"],
        meeting_date=meeting_date,
        meeting_type=meeting_type,
        summary=summary,
        mood=mood,
        next_meeting=next_meeting or None,
        aq_tasks=aq_tasks_list,
        client_tasks=cl_tasks_list,
        site_ids=client.get("site_ids") or "",
        login=mr_login,
        password=mr_password,
    )

    # Синхронизация с Airtable (дата + дописать комментарий)
    try:
        await sync_meeting_to_airtable(
            client_name=client["name"],
            meeting_date=meeting_date,
            meeting_type=meeting_type,
            summary=summary,
            mood=mood,
        )
    except Exception as exc:
        logging.warning("Airtable sync error (followup): %s", exc)

    # Отправка в TG
    tg_ok = False
    if send_tg and BOT_TOKEN and client.get("tg_chat_id"):
        msg = build_followup_message(
            client_name=client["name"],
            meeting_date=meeting_date,
            meeting_type=meeting_type,
            summary=summary,
            aq_tasks=aq_tasks_list,
            client_tasks=cl_tasks_list,
            next_meeting=next_meeting or None,
            mood=mood,
        )
        tg_ok = await send_to_tg(BOT_TOKEN, client["tg_chat_id"], msg)
        if tg_ok:
            mark_meeting_tg_sent(meeting_id)

    # K.Talk уведомление (параллельно TG, в канал менеджера)
    try:
        tg_id = get_tg_id(user)
        ktalk_url = None
        if tg_id:
            profile = get_manager_profile(tg_id)
            ktalk_url = profile.get("ktalk_webhook") or os.getenv("KTALK_WEBHOOK_URL", "")
        if ktalk_url:
            await send_ktalk_followup(
                client_name=client["name"],
                meeting_date=meeting_date,
                meeting_type=meeting_type,
                summary=summary,
                mood=mood,
                aq_tasks=aq_tasks_list,
                next_meeting=next_meeting or None,
                webhook_url=ktalk_url,
            )
    except Exception as exc:
        logging.warning("K.Talk followup error: %s", exc)

    return RedirectResponse(f"/prep/{client_id}?saved=1&tg={'ok' if tg_ok else 'skip'}", status_code=303)


# ── QBR ───────────────────────────────────────────────────────────────────────

@app.get("/qbr/{client_id}", response_class=HTMLResponse)
async def qbr_page(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    # Берём встречи за последние 3 месяца для автозаполнения
    meetings = get_client_meetings(client_id, limit=20)
    all_tasks = get_client_tasks(client_id, "open") + get_client_tasks(client_id, "done")

    # Фильтруем задачи последних 90 дней
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    recent_tasks = [t for t in all_tasks if (t.get("due_date") or "9999") >= cutoff]

    return templates.TemplateResponse("qbr.html", {
        "request": request,
        "user": user,
        "client": client,
        "meetings": meetings[:6],
        "recent_tasks": recent_tasks,
        "today": date.today().isoformat(),
        "quarter": f"Q{(date.today().month - 1) // 3 + 1} {date.today().year}",
    })


# ── Профиль менеджера ────────────────────────────────────────────────────────

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, saved: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    tg_id = get_tg_id(user)
    profile = get_manager_profile(tg_id or 0)
    all_managers = get_all_manager_profiles()
    other_managers = [m for m in all_managers if m["tg_id"] != tg_id]

    from airtable_sync import AIRTABLE_TOKEN, AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "profile": profile,
        "saved": bool(saved),
        "other_managers": other_managers,
        "default_airtable_token": AIRTABLE_TOKEN,
        "airtable_base": AIRTABLE_BASE_ID,
        "airtable_table": AIRTABLE_TABLE_ID,
        "today": date.today().isoformat(),
    })


@app.post("/api/profile/save", response_class=JSONResponse)
async def api_profile_save(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    tg_id = get_tg_id(user)
    if not tg_id:
        raise HTTPException(400, "No tg_id in session")

    body = await request.json()
    save_manager_profile(
        tg_id=tg_id,
        display_name=body.get("display_name", ""),
        mr_login=body.get("mr_login", ""),
        mr_password=body.get("mr_password", ""),
        tg_notify_chat=body.get("tg_notify_chat", ""),
        airtable_token=body.get("airtable_token", ""),
        ktalk_webhook=body.get("ktalk_webhook", ""),
    )
    return {"ok": True}


@app.post("/api/profile/test-mr", response_class=JSONResponse)
async def api_test_mr(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    login    = (body.get("login") or "").strip()
    password = (body.get("password") or "").strip()
    if not login or not password:
        return {"ok": False, "error": "Введи логин и пароль"}

    import httpx as _httpx
    mr_url = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
    try:
        async with _httpx.AsyncClient(timeout=10) as hx:
            r = await hx.post(
                f"{mr_url}/backend-v2/auth/login",
                json={"username": login, "password": password},
            )
        if r.status_code == 200:
            data = r.json()
            email = data.get("email") or data.get("user", {}).get("email", "")
            return {"ok": True, "email": email}
        else:
            return {"ok": False, "error": f"Ошибка {r.status_code}: {r.text[:100]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Настройки менеджера: мой список клиентов ─────────────────────────────────

@app.get("/settings/my-clients", response_class=HTMLResponse)
async def my_clients_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    tg_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    all_clients = get_all_clients()
    my_ids = set(get_manager_client_ids(tg_id or 0))

    return templates.TemplateResponse("my_clients.html", {
        "request": request,
        "user": user,
        "all_clients": all_clients,
        "my_ids": my_ids,
        "today": date.today().isoformat(),
    })


@app.post("/api/settings/my-clients", response_class=JSONResponse)
async def save_my_clients(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    tg_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    if not tg_id:
        raise HTTPException(400, "No tg_id")

    body = await request.json()
    client_ids = [int(x) for x in body.get("client_ids", []) if str(x).isdigit()]
    set_manager_clients(tg_id, client_ids)
    return {"ok": True, "count": len(client_ids)}


# ── Импорт клиентов из Airtable CS ALL ───────────────────────────────────────

@app.post("/api/admin/import-airtable", response_class=JSONResponse)
async def api_import_airtable(request: Request):
    """
    Запускает импорт всех клиентов из Airtable CS ALL view.
    Авто-определяет поля, upsert клиентов, привязывает к менеджерам.
    Только для авторизованных пользователей.
    """
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    # Можно передать свой токен и view_id через body
    token = body.get("token") if isinstance(body, dict) else None
    view_id = body.get("view_id") if isinstance(body, dict) else None

    import_kwargs: dict = {}
    if token:
        import_kwargs["token"] = token
    if view_id:
        import_kwargs["view_id"] = view_id

    try:
        result = await import_clients_from_airtable(**import_kwargs)
        # unmatched_managers — set, нужно сериализовать
        if isinstance(result.get("unmatched_managers"), set):
            result["unmatched_managers"] = list(result["unmatched_managers"])
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Webhook от Merchrules ─────────────────────────────────────────────────────

@app.post("/webhook/merchrules", response_class=JSONResponse)
async def webhook_merchrules(request: Request):
    """
    Принимает события от Merchrules (задачи, статусы, комментарии).
    Секрет: заголовок X-MR-Secret или query ?secret=... должен совпадать с MR_WEBHOOK_SECRET.
    Если секрет не задан — принимаем всё (не рекомендуется в проде).

    Merchrules должен слать POST с JSON:
    {
      "event": "task.updated" | "task.created" | "task.done",
      "site_id": "1234",
      "task": { "title": "...", "status": "done", "id": 999 }
    }
    """
    mr_secret = os.getenv("MR_WEBHOOK_SECRET", "")
    if mr_secret:
        incoming = (
            request.headers.get("X-MR-Secret", "")
            or request.query_params.get("secret", "")
        )
        if incoming != mr_secret:
            raise HTTPException(403, "Invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event   = body.get("event", "")
    site_id = str(body.get("site_id", ""))
    task    = body.get("task", {})

    logging.info("MR Webhook: event=%s site_id=%s task=%s", event, site_id, task.get("title", "?"))

    if not event or not site_id:
        return {"ok": True, "note": "no action needed"}

    # Находим клиента по site_id
    try:
        from database import get_all_clients, update_task_status, get_all_tasks, create_tasks_bulk
        all_clients = get_all_clients()
        matched_client = None
        for c in all_clients:
            ids = [s.strip() for s in (c.get("site_ids") or "").split(",") if s.strip()]
            if site_id in ids:
                matched_client = c
                break

        if not matched_client:
            return {"ok": True, "note": f"client with site_id={site_id} not found"}

        client_id = matched_client["id"]

        if event in ("task.done", "task.completed"):
            # Закрываем задачу в БД по совпадению заголовка
            title = (task.get("title") or "").lower().strip()
            if title:
                open_tasks = get_all_tasks("open")
                for t in open_tasks:
                    if t["client_id"] == client_id and title in t["text"].lower():
                        update_task_status(t["id"], "done")
                        logging.info("MR webhook: closed task '%s' for client %s", t["text"], matched_client["name"])

        elif event == "task.created":
            # Создаём задачу в AM Hub если её нет
            title = task.get("title", "").strip()
            if title:
                existing = get_all_tasks()
                exists = any(
                    t["client_id"] == client_id and title.lower() in t["text"].lower()
                    for t in existing
                )
                if not exists:
                    create_tasks_bulk(
                        meeting_id=None,
                        client_id=client_id,
                        tasks=[{"owner": "anyquery", "text": title, "due_date": task.get("due_date")}]
                    )
                    logging.info("MR webhook: created task '%s' for %s", title, matched_client["name"])

        return {"ok": True, "client": matched_client["name"], "event": event}

    except Exception as exc:
        logging.error("webhook_merchrules processing error: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── K.Talk API ────────────────────────────────────────────────────────────────

@app.post("/api/profile/test-ktalk", response_class=JSONResponse)
async def api_test_ktalk(request: Request):
    """Проверить K.Talk webhook подключение."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    webhook_url = body.get("webhook_url", "").strip()
    if not webhook_url:
        return {"ok": False, "error": "Webhook URL не указан"}

    result = await test_ktalk_connection(webhook_url)
    return result


# ── /hub — Командный центр ───────────────────────────────────────────────────

@app.get("/hub", response_class=HTMLResponse)
async def hub_page(request: Request):
    """Единый Командный центр — статус всех инструментов, быстрые действия, лог активности."""
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    tg_id = get_tg_id(user)

    from database import (
        get_all_clients, get_all_tasks, get_all_manager_profiles,
        checkup_status, get_conn
    )

    # Общая статистика
    all_clients  = get_all_clients()
    all_tasks    = get_all_tasks("open")
    managers     = get_all_manager_profiles()

    overdue = sum(1 for c in all_clients
                  if checkup_status(c.get("last_checkup") or c.get("last_meeting"), c["segment"])["color"] == "red")
    warning = sum(1 for c in all_clients
                  if checkup_status(c.get("last_checkup") or c.get("last_meeting"), c["segment"])["color"] == "yellow")

    # Ближайшие встречи (planned_meeting в ближайшие 7 дней)
    from datetime import date, timedelta
    today = date.today()
    upcoming = []
    for c in all_clients:
        pm = c.get("planned_meeting")
        if pm:
            try:
                pm_date = date.fromisoformat(pm)
                days = (pm_date - today).days
                if 0 <= days <= 7:
                    upcoming.append({**c, "days_until": days, "pm_date": pm})
            except ValueError:
                pass
    upcoming.sort(key=lambda x: x["days_until"])

    # Последняя активность (последние 10 встреч)
    with get_conn() as conn:
        recent_meetings = conn.execute("""
            SELECT m.id, m.meeting_date, m.meeting_type, m.mood,
                   c.name as client_name, c.segment
            FROM meetings m
            JOIN clients c ON c.id = m.client_id
            ORDER BY m.created_at DESC LIMIT 10
        """).fetchall()

    # Статус инструментов
    airtable_token = os.getenv("AIRTABLE_TOKEN", "")
    mr_login, mr_password = get_user_mr_creds(user)
    bot_token = BOT_TOKEN

    profile = get_manager_profile(tg_id) if tg_id else {}
    ktalk_url = profile.get("ktalk_webhook") or os.getenv("KTALK_WEBHOOK_URL", "")

    tools_status = [
        {
            "name": "Airtable",
            "icon": "📋",
            "connected": bool(airtable_token),
            "detail": "Авто-синхронизация клиентов каждый час" if airtable_token else "Токен не задан",
            "url": "https://airtable.com/appEAS1rPKpevoIel",
            "action_url": None,
        },
        {
            "name": "Merchrules",
            "icon": "🔗",
            "connected": bool(mr_login),
            "detail": f"Аккаунт: {mr_login}" if mr_login else "Войди в Профиль и добавь кредсы",
            "url": "https://merchrules.any-platform.ru",
            "action_url": "/profile",
        },
        {
            "name": "Telegram Bot",
            "icon": "🤖",
            "connected": bool(bot_token),
            "detail": f"@{os.getenv('TG_BOT_USERNAME', '?')}" if bot_token else "TG_BOT_TOKEN не задан",
            "url": f"https://t.me/{os.getenv('TG_BOT_USERNAME', '')}" if os.getenv("TG_BOT_USERNAME") else "#",
            "action_url": None,
        },
        {
            "name": "K.Talk",
            "icon": "📹",
            "connected": bool(ktalk_url),
            "detail": "Webhook настроен" if ktalk_url else "Webhook не настроен — добавь в профиле",
            "url": "https://tbank.ktalk.ru/",
            "action_url": "/profile#ktalk",
        },
        {
            "name": "Google Calendar",
            "icon": "📅",
            "connected": True,  # Всегда — через URL-ссылки без OAuth
            "detail": "Создание событий через умные ссылки (без OAuth)",
            "url": "https://calendar.google.com",
            "action_url": None,
        },
    ]

    # Расписание планировщика
    scheduler_jobs = [
        {"name": "Утренний план",       "schedule": "09:00 пн-пт",       "icon": "☀️"},
        {"name": "Еженедельный дайджест","schedule": "пт 17:00",          "icon": "📊"},
        {"name": "Синхронизация MR",    "schedule": "каждый час в :00",   "icon": "🔗"},
        {"name": "Синхронизация Airtable","schedule": "каждый час в :30", "icon": "📋"},
        {"name": "Напоминания о встречах","schedule": "каждые 30 мин",    "icon": "📆"},
        {"name": "Авто-чекап задачи",   "schedule": "08:00 ежедневно",    "icon": "🔔"},
    ]

    return templates.TemplateResponse("hub.html", {
        "request": request,
        "user": user,
        "total_clients": len(all_clients),
        "open_tasks": len(all_tasks),
        "overdue": overdue,
        "warning": warning,
        "managers_count": len(managers),
        "upcoming": upcoming[:5],
        "recent_meetings": [dict(r) for r in recent_meetings],
        "tools_status": tools_status,
        "scheduler_jobs": scheduler_jobs,
        "today": today.isoformat(),
    })


# ── Admin API: ручной запуск scheduler jobs ───────────────────────────────────

@app.post("/api/admin/sync-mr", response_class=JSONResponse)
async def api_admin_sync_mr(request: Request):
    """Ручной запуск синхронизации статусов из Merchrules."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    try:
        from scheduler import job_mr_status_sync
        await job_mr_status_sync()
        return {"ok": True, "message": "Синхронизация MR запущена"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/admin/run-morning-plan", response_class=JSONResponse)
async def api_admin_morning_plan(request: Request):
    """Ручной запуск утреннего плана в TG."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    try:
        from scheduler import job_morning_plan
        await job_morning_plan()
        return {"ok": True, "message": "Утренний план отправлен"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/admin/run-digest", response_class=JSONResponse)
async def api_admin_run_digest(request: Request):
    """Ручной запуск еженедельного дайджеста."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    try:
        from scheduler import job_weekly_digest
        await job_weekly_digest()
        return {"ok": True, "message": "Дайджест отправлен"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── API — обновить TG chat_id клиента ────────────────────────────────────────

@app.post("/api/client/{client_id}/tg")
async def update_tg(request: Request, client_id: int, tg_chat_id: str = Form(...)):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    client = get_client(client_id)
    if not client:
        raise HTTPException(404)
    upsert_client(client["name"], client["segment"], tg_chat_id, client.get("notes", ""))
    return RedirectResponse(f"/prep/{client_id}", status_code=303)


# ── API — закрыть задачу ──────────────────────────────────────────────────────

@app.post("/api/task/{task_id}/done")
async def close_task(request: Request, task_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    update_task_status(task_id, "done")
    ref = request.headers.get("referer", "/")
    return RedirectResponse(ref, status_code=303)


# ── Top-50 — веб-страница ────────────────────────────────────────────────────

def _load_metrics_for_month(year_month: str) -> Optional[dict]:
    """Загружает сохранённые метрики из data/metrics_{year_month}.json."""
    from pathlib import Path as _P
    import json as _json
    p = _P("data") / f"metrics_{year_month}.json"
    if not p.exists():
        return None
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


@app.get("/top50", response_class=HTMLResponse)
async def top50_page(request: Request, mode: str = "weekly"):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    month_name_ru = ["Январь","Февраль","Март","Апрель","Май","Июнь",
                     "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]
    now = date.today()
    month_name = month_name_ru[now.month - 1]
    year_month = now.strftime("%Y-%m")

    metrics = None
    data = {}

    if mode == "metrics":
        metrics = _load_metrics_for_month(year_month)
    else:
        all_clients = get_all_clients()
        my_client_names = [c["name"] for c in all_clients]
        data = await get_top50_data(
            my_clients=my_client_names,
            spreadsheet_id=SHEETS_SPREADSHEET_ID,
            gid=SHEETS_TOP50_GID,
        )

    return templates.TemplateResponse("top50.html", {
        "request": request,
        "user": user,
        "data": data,
        "mode": mode,
        "metrics": metrics,
        "month_name": month_name,
        "today": now.isoformat(),
        "sheet_url": (
            f"https://docs.google.com/spreadsheets/d/{SHEETS_SPREADSHEET_ID}"
            f"/edit#gid={SHEETS_TOP50_GID}"
        ),
    })


# ── API: загрузить файл метрик (CSV / XLSX) ───────────────────────────────────

@app.post("/api/metrics/upload", response_class=JSONResponse)
async def api_metrics_upload(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    from fastapi import UploadFile, File
    import json as _json
    from pathlib import Path as _P

    form = await request.form()
    file: UploadFile = form.get("file")
    if not file:
        return {"ok": False, "error": "Файл не найден"}

    filename = file.filename or "metrics"
    content = await file.read()

    headers = []
    rows = []

    try:
        if filename.lower().endswith(".csv"):
            import io as _io
            import csv as _csv
            text = content.decode("utf-8-sig", errors="replace")
            reader = _csv.reader(_io.StringIO(text))
            all_rows = list(reader)
            if all_rows:
                headers = all_rows[0]
                rows = [list(r) for r in all_rows[1:] if any(c.strip() for c in r)]

        elif filename.lower().endswith((".xlsx", ".xls")):
            import openpyxl as _xl
            import io as _io
            wb = _xl.load_workbook(_io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            all_rows = [[str(cell.value) if cell.value is not None else "" for cell in row] for row in ws.iter_rows()]
            wb.close()
            if all_rows:
                headers = all_rows[0]
                rows = [r for r in all_rows[1:] if any(c.strip() for c in r)]
        else:
            return {"ok": False, "error": "Поддерживаются только CSV и XLSX"}

    except Exception as exc:
        return {"ok": False, "error": f"Ошибка разбора файла: {exc}"}

    if not headers or not rows:
        return {"ok": False, "error": "Файл пустой или не содержит данных"}

    # Вычисляем KPI-карточки: числовые колонки → сумма / среднее
    kpis = []
    for i, h in enumerate(headers):
        vals = []
        for r in rows:
            if i < len(r):
                try:
                    vals.append(float(str(r[i]).replace(",", ".").replace(" ", "")))
                except Exception:
                    pass
        if vals and len(vals) >= len(rows) * 0.5:  # >50% числовые
            total = sum(vals)
            avg = total / len(vals)
            label_lower = h.lower()
            if any(w in label_lower for w in ("gmv","выручка","оборот","сумма","руб")):
                kpis.append({"label": h, "value": f"{total:,.0f} ₽".replace(",", " ")})
            elif any(w in label_lower for w in ("заказ","order","cnt","кол-во","количество")):
                kpis.append({"label": h, "value": f"{int(total):,}".replace(",", " ")})
            elif "конвер" in label_lower or "%" in h:
                kpis.append({"label": h, "value": f"{avg:.1f}%"})
            elif len(kpis) < 6:
                kpis.append({"label": h, "value": f"{total:,.0f}".replace(",", " ")})
        if len(kpis) >= 6:
            break

    # Сохраняем
    now = date.today()
    year_month = now.strftime("%Y-%m")
    _P("data").mkdir(exist_ok=True)
    out_path = _P("data") / f"metrics_{year_month}.json"
    out_path.write_text(_json.dumps({
        "filename": filename,
        "uploaded_at": now.isoformat(),
        "headers": headers,
        "rows": rows,
        "kpis": kpis,
    }, ensure_ascii=False), encoding="utf-8")

    return {"ok": True, "rows": len(rows), "cols": len(headers)}


# ── API — статистика дашборда ────────────────────────────────────────────────

@app.get("/api/stats", response_class=JSONResponse)
async def api_stats(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    clients = get_all_clients()
    for c in clients:
        c["status"] = checkup_status(
            c.get("last_checkup") or c.get("last_meeting"), c["segment"]
        )
    overdue = sum(1 for c in clients if c["status"]["color"] == "red")
    warning = sum(1 for c in clients if c["status"]["color"] == "yellow")
    total_open_tasks = sum(c.get("open_tasks", 0) for c in clients)
    return {
        "total_clients": len(clients),
        "overdue": overdue,
        "warning": warning,
        "open_tasks": total_open_tasks,
    }


# ── Telegram Webhook ─────────────────────────────────────────────────────────

@app.post("/tg/webhook")
async def tg_webhook(request: Request):
    """Принимает Updates от Telegram."""
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(400, "Bad JSON")

    # Передаём update в обработчик бота
    async def _get_top50():
        all_clients = get_all_clients()
        my_client_names = [c["name"] for c in all_clients]
        return await get_top50_data(
            my_clients=my_client_names,
            spreadsheet_id=SHEETS_SPREADSHEET_ID,
            gid=SHEETS_TOP50_GID,
        )

    def _get_clients():
        clients = get_all_clients()
        for c in clients:
            c["status"] = checkup_status(
                c.get("last_checkup") or c.get("last_meeting"), c["segment"]
            )
        return clients

    await tg_bot.handle_update(update, _get_clients, _get_top50)
    return {"ok": True}


# ── Поиск клиентов (для быстрой навигации) ──────────────────────────────────

@app.get("/api/search", response_class=JSONResponse)
async def api_search(request: Request, q: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    if not q or len(q) < 1:
        return []
    clients = get_all_clients()
    q_lower = q.lower()
    matches = [
        {"id": c["id"], "name": c["name"], "segment": c["segment"]}
        for c in clients
        if q_lower in c["name"].lower()
    ][:10]
    return matches


# ── Сегодня — ежедневный план ────────────────────────────────────────────────

@app.get("/today", response_class=HTMLResponse)
async def today_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    clients = get_all_clients()
    for c in clients:
        c["status"] = checkup_status(
            c.get("last_checkup") or c.get("last_meeting"), c["segment"]
        )

    overdue = [c for c in clients if c["status"]["color"] == "red"]
    warning = [c for c in clients if c["status"]["color"] == "yellow"]
    overview = get_today_overview()

    return templates.TemplateResponse("today.html", {
        "request": request,
        "user": user,
        "today": date.today().isoformat(),
        "weekday": ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота","Воскресенье"][date.today().weekday()],
        "overdue": overdue,
        "warning": warning[:5],
        "urgent_tasks": overview["urgent_tasks"],
        "week_tasks": overview["week_tasks"],
    })


# ── Все задачи ───────────────────────────────────────────────────────────────

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, status: str = "open", owner: str = "", segment: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    tasks = get_all_tasks(status)

    if owner:
        tasks = [t for t in tasks if t["owner"] == owner]
    if segment:
        tasks = [t for t in tasks if t.get("segment") == segment]

    return templates.TemplateResponse("tasks.html", {
        "request": request,
        "user": user,
        "tasks": tasks,
        "status": status,
        "owner": owner,
        "segment": segment,
        "today": date.today().isoformat(),
        "total": len(get_all_tasks("open")),
    })


# ── Роадмап — создание bulk-задач ────────────────────────────────────────────

@app.get("/roadmap", response_class=HTMLResponse)
async def roadmap_page(request: Request, client_id: int = 0):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    # Если открыли из карточки клиента — подставляем site_ids
    prefill_site_ids = ""
    prefill_client_name = ""
    if client_id:
        c = get_client(client_id)
        if c:
            prefill_site_ids = c.get("site_ids", "")
            prefill_client_name = c["name"]

    clients = get_all_clients()

    return templates.TemplateResponse("roadmap.html", {
        "request": request,
        "user": user,
        "clients": clients,
        "prefill_site_ids": prefill_site_ids,
        "prefill_client_name": prefill_client_name,
        "today": date.today().isoformat(),
        "merchrules_url": os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru"),
    })


# ── Merchrules Sync API ───────────────────────────────────────────────────────

@app.post("/api/mr/sync", response_class=JSONResponse)
async def mr_sync(request: Request):
    """Принудительно сбрасывает кэш и заново тянет данные из Merchrules."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    mr_login, mr_password = get_user_mr_creds(user)
    mr_invalidate(mr_login)
    clients = get_all_clients_for_manager(get_tg_id(user))
    data = await sync_clients_from_merchrules(clients, login=mr_login, password=mr_password)
    return {"ok": True, "synced_sites": len(data)}


@app.get("/api/mr/clients", response_class=JSONResponse)
async def mr_clients(request: Request):
    """
    Возвращает агрегированные MR-данные по клиентам менеджера.
    Формат: { client_id: { open_tasks, blocked_tasks, overdue_tasks, last_meeting } }
    """
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    mr_login, mr_password = get_user_mr_creds(user)
    if not mr_login:
        return {}  # нет кредов — отдаём пустой ответ

    clients = get_all_clients_for_manager(get_tg_id(user))
    mr_data = await sync_clients_from_merchrules(clients, login=mr_login, password=mr_password)

    result = {}
    for c in clients:
        agg = get_client_mr_data(mr_data, c.get("site_ids") or "")
        if agg["open_tasks"] > 0 or agg["blocked_tasks"] > 0 or agg["last_meeting"]:
            result[str(c["id"])] = agg

    return result


# ── AI: обработка транскрипта ────────────────────────────────────────────────

@app.post("/api/ai/process-transcript", response_class=JSONResponse)
async def api_ai_process(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    transcript   = (body.get("transcript") or "").strip()
    client_name  = (body.get("client_name") or "").strip()
    meeting_date = (body.get("meeting_date") or date.today().isoformat())

    if not transcript:
        return {"error": "Транскрипт пустой"}

    result = await ai_process_transcript(transcript, client_name, meeting_date)
    return result


# ── AI: загрузить задачи в Merchrules + сохранить встречу ────────────────────

@app.post("/api/ai/upload-tasks", response_class=JSONResponse)
async def api_ai_upload(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    tasks        = body.get("tasks", [])
    site_ids_raw = (body.get("site_ids") or "").strip()
    client_id    = body.get("client_id", 0)
    summary      = body.get("summary", "")
    mood         = body.get("mood", "neutral")
    meeting_date = body.get("meeting_date", date.today().isoformat())

    if not tasks:
        return {"ok": False, "error": "Нет задач для загрузки"}
    if not site_ids_raw:
        return {"ok": False, "error": "Нет site_id"}

    # Сохраняем встречу в БД
    meeting_id = None
    if client_id:
        client = get_client(client_id)
        if client:
            meeting_id = create_meeting(
                client_id=client_id,
                meeting_date=meeting_date,
                meeting_type="checkup",
                summary=summary,
                mood=mood,
                next_meeting=None,
            )
            # Сохраняем задачи в БД
            db_tasks = []
            for t in tasks:
                owner = "anyquery" if t.get("assignee") != "partner" else "client"
                db_tasks.append({"owner": owner, "text": t["title"], "due_date": t.get("due_date")})
            if db_tasks:
                create_tasks_bulk(meeting_id, client_id, db_tasks)

    # Пробуем загрузить в Merchrules через кредсы текущего пользователя
    import re as _re
    site_ids = [s.strip() for s in _re.split(r"[,\s]+", site_ids_raw) if s.strip()]

    merchrules_url = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
    mr_login, mr_password = get_user_mr_creds(user)

    uploaded = []
    errors = []

    if mr_login and mr_password:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=30) as hx:
            # Авторизуемся
            auth_resp = await hx.post(
                f"{merchrules_url}/backend-v2/auth/login",
                json={"username": mr_login, "password": mr_password}
            )
            if auth_resp.status_code != 200:
                return {"ok": False, "error": f"Не удалось авторизоваться в Merchrules: {auth_resp.status_code}"}

            auth_token = auth_resp.json().get("token") or auth_resp.json().get("access_token", "")
            headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}

            for site_id in site_ids:
                for task in tasks:
                    if task.get("assignee") == "partner":
                        continue  # Не загружаем задачи партнёра
                    try:
                        csv_row = (
                            f"title,description,status,priority,team,task_type,assignee,product,link,due_date\n"
                            f"{task.get('title','')},{task.get('description','')},{task.get('status','plan')},"
                            f"{task.get('priority','medium')},{task.get('team','')},{task.get('task_type','')},"
                            f"any,{task.get('product','any_query_web')},{task.get('link','')},"
                            f"{task.get('due_date','')}"
                        )
                        import io as _io
                        r = await hx.post(
                            f"{merchrules_url}/backend-v2/import/tasks/csv",
                            params={"site_id": site_id},
                            files={"file": ("tasks.csv", _io.BytesIO(csv_row.encode("utf-8")), "text/csv")},
                            headers=headers,
                        )
                        if r.status_code in (200, 201):
                            uploaded.append({"site_id": site_id, "task": task["title"]})
                        else:
                            errors.append({"site_id": site_id, "task": task["title"],
                                          "error": r.text[:200]})
                    except Exception as exc:
                        errors.append({"site_id": site_id, "task": task.get("title", "?"),
                                       "error": str(exc)})
    else:
        # Нет кредов Merchrules — только сохраняем в БД
        return {
            "ok": True,
            "uploaded": [],
            "errors": [],
            "note": (
                "Встреча и задачи сохранены в AM Hub. "
                "Для загрузки в Merchrules укажи логин и пароль в разделе Профиль."
            ),
        }

    # Сбрасываем MR-кэш после загрузки задач
    if uploaded:
        mr_invalidate()

    return {
        "ok": True,
        "uploaded": uploaded,
        "errors": errors,
        "meeting_id": meeting_id,
        "mr_uploaded": len(uploaded) > 0,
        "mr_tasks_count": len(uploaded),
        "tasks_count": len(tasks),
    }

# ── Чеклист встречи ───────────────────────────────────────────────────────────

# AI-подсказки по сегменту для страницы чеклиста
AI_HINTS_BY_SEGMENT = {
    "ENT": [
        "Как изменился GMV за период?",
        "Есть ли планы на расширение?",
        "Кто принимает решения о бюджете?",
        "Как оцениваете ROI от AnyQuery?",
        "Что мешает масштабироваться?",
    ],
    "SME+": [
        "Какие метрики важнее всего для вас?",
        "Что из роадмапа самое приоритетное?",
        "Есть ли конкуренты которых отслеживаете?",
        "Как ваша команда использует дашборд?",
    ],
    "SME-": [
        "Какие функции используете чаще всего?",
        "Что было бы полезно добавить?",
        "Как оцениваете работу поиска у вас?",
        "Есть ли технические блокеры?",
    ],
    "SMB": [
        "Как идут продажи в целом?",
        "Влияет ли поиск на конверсию заметно?",
        "Планируете ли рост ассортимента?",
        "Нужна ли помощь с настройкой?",
    ],
    "SS": [
        "Всё ли работает как ожидалось?",
        "Есть ли вопросы по функционалу?",
        "Планируете ли переход на более высокий план?",
    ],
}


@app.get("/checklist/{client_id}", response_class=HTMLResponse)
async def checklist_page(request: Request, client_id: int, meeting_type: str = "checkup"):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    items = get_checklist(client_id)
    seg = client.get("segment", "SMB")
    hints = AI_HINTS_BY_SEGMENT.get(seg, AI_HINTS_BY_SEGMENT["SMB"])

    return templates.TemplateResponse("checklist.html", {
        "request": request,
        "user": user,
        "client": client,
        "items": items,
        "meeting_type": meeting_type,
        "today": date.today().isoformat(),
        "ai_hints": hints,
    })


@app.post("/api/checklist/init", response_class=JSONResponse)
async def api_checklist_init(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    client_id = int(body.get("client_id", 0))
    meeting_type = body.get("meeting_type", "checkup")
    if not client_id:
        return {"error": "no client_id"}

    open_tasks = get_client_tasks(client_id, "open")
    items = init_checklist(client_id, meeting_type, open_tasks)
    return {"items": items}


@app.post("/api/checklist/{item_id}/toggle", response_class=JSONResponse)
async def api_checklist_toggle(request: Request, item_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    toggle_checklist_item(item_id, body.get("checked", False))
    return {"ok": True}


@app.post("/api/checklist/add", response_class=JSONResponse)
async def api_checklist_add(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    client_id = int(body.get("client_id", 0))
    text = (body.get("text") or "").strip()
    if not client_id or not text:
        return {"error": "missing fields"}
    add_checklist_item(client_id, text)
    items = get_checklist(client_id)
    new_item = next((i for i in reversed(items) if i["text"] == text), None)
    return {"ok": True, "item": new_item}


@app.post("/api/checklist/clear", response_class=JSONResponse)
async def api_checklist_clear(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    clear_checklist(int(body.get("client_id", 0)))
    return {"ok": True}


# ── Внутренние задачи ─────────────────────────────────────────────────────────

@app.get("/internal-tasks", response_class=HTMLResponse)
async def internal_tasks_page(request: Request, status: str = "open"):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    tasks = get_internal_tasks(status)
    clients = get_all_clients()

    return templates.TemplateResponse("internal_tasks.html", {
        "request": request,
        "user": user,
        "tasks": tasks,
        "clients": clients,
        "status": status,
        "today": date.today().isoformat(),
    })


@app.post("/api/internal-task", response_class=JSONResponse)
async def api_create_internal_task(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    client_id = int(body.get("client_id", 0))
    text = (body.get("text") or "").strip()
    due_date = body.get("due_date") or None
    note = (body.get("internal_note") or "").strip()
    if not client_id or not text:
        return {"ok": False, "error": "client_id and text required"}
    task_id = create_internal_task(client_id, text, due_date, note)
    return {"ok": True, "task_id": task_id}


# ── Аналитика ─────────────────────────────────────────────────────────────────

@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    import sqlite3
    from pathlib import Path
    db_path = Path("data/am_hub.db")

    stats = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        today = date.today()

        # Встречи по неделям за последние 12 недель
        meetings_weekly = conn.execute("""
            SELECT strftime('%Y-W%W', meeting_date) as week,
                   COUNT(*) as cnt,
                   SUM(CASE WHEN mood='positive' THEN 1 ELSE 0 END) as positive,
                   SUM(CASE WHEN mood='risk' THEN 1 ELSE 0 END) as risk
            FROM meetings
            WHERE meeting_date >= date('now', '-84 days')
            GROUP BY week ORDER BY week
        """).fetchall()

        # Задачи по статусам
        task_stats = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status
        """).fetchall()

        # Задачи созданные vs закрытые за последние 4 недели
        task_flow = conn.execute("""
            SELECT strftime('%Y-W%W', created_at) as week,
                   COUNT(*) as created,
                   SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done
            FROM tasks
            WHERE created_at >= date('now', '-28 days')
            GROUP BY week ORDER BY week
        """).fetchall()

        # Чекапы: соответствие ритму по сегментам
        clients_data = conn.execute("""
            SELECT c.segment,
                   COUNT(*) as total,
                   SUM(CASE WHEN c.last_checkup IS NOT NULL
                       AND julianday('now') - julianday(c.last_checkup) <=
                       CASE c.segment
                           WHEN 'ENT' THEN 30 WHEN 'SME+' THEN 60 WHEN 'SME-' THEN 60
                           WHEN 'SMB' THEN 90 ELSE 90 END
                       THEN 1 ELSE 0 END) as on_time
            FROM clients c
            GROUP BY c.segment ORDER BY c.segment
        """).fetchall()

        # Топ клиентов по кол-ву встреч за квартал
        top_active = conn.execute("""
            SELECT c.name, c.segment, COUNT(m.id) as meetings
            FROM clients c
            LEFT JOIN meetings m ON m.client_id = c.id
              AND m.meeting_date >= date('now', '-90 days')
            GROUP BY c.id ORDER BY meetings DESC LIMIT 10
        """).fetchall()

        conn.close()

        stats = {
            "meetings_weekly": [dict(r) for r in meetings_weekly],
            "task_stats": {r["status"]: r["cnt"] for r in task_stats},
            "task_flow": [dict(r) for r in task_flow],
            "clients_by_segment": [dict(r) for r in clients_data],
            "top_active": [dict(r) for r in top_active],
        }
    except Exception as exc:
        logging.error("Analytics query error: %s", exc)

    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "user": user,
        "stats": stats,
        "today": date.today().isoformat(),
    })


# ── API: обновить оценку времени задачи ──────────────────────────────────────

@app.post("/api/task/{task_id}/hours", response_class=JSONResponse)
async def api_task_hours(request: Request, task_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    hours = float(body.get("hours", 0))
    import sqlite3 as _sq
    from pathlib import Path as _P
    with _sq.connect(_P("data/am_hub.db")) as conn:
        conn.execute("UPDATE tasks SET hours_estimate=? WHERE id=?", (hours, task_id))
    return {"ok": True}


# ── QBR Календарь ────────────────────────────────────────────────────────────

@app.get("/qbr-calendar", response_class=HTMLResponse)
async def qbr_calendar_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    qbr_meetings = get_qbr_calendar()
    upcoming = get_upcoming_meetings(days_ahead=30)

    # Клиенты ENT без QBR за последние 90 дней
    all_clients = get_all_clients()
    now_iso = date.today().isoformat()
    cutoff90 = (date.today() - timedelta(days=90)).isoformat()
    clients_with_recent_qbr = {
        m["client_id"] for m in qbr_meetings if (m.get("meeting_date") or "") >= cutoff90
    }
    ent_no_qbr = [
        c for c in all_clients
        if c["segment"] in ("ENT", "SME+") and c["id"] not in clients_with_recent_qbr
    ]

    return templates.TemplateResponse("qbr_calendar.html", {
        "request": request,
        "user": user,
        "qbr_meetings": qbr_meetings,
        "upcoming": upcoming,
        "ent_no_qbr": ent_no_qbr,
        "today": now_iso,
    })


# ── API: быстро закрыть чекап (с prep страницы) ──────────────────────────────

@app.post("/api/client/{client_id}/checkup-done", response_class=JSONResponse)
async def api_checkup_done(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    body = await request.json()
    mood          = body.get("mood", "neutral")
    rating        = int(body.get("rating", 0))
    summary       = (body.get("summary") or "").strip()
    next_meeting  = body.get("next_meeting") or None
    meeting_date  = date.today().isoformat()

    # Сохраняем встречу
    meeting_id = create_meeting(
        client_id=client_id,
        meeting_date=meeting_date,
        meeting_type="checkup",
        summary=summary,
        mood=mood,
        next_meeting=next_meeting,
    )

    # Оценка встречи
    if rating and 1 <= rating <= 5:
        set_checkup_rating(meeting_id, rating)

    # Планируем следующую встречу
    if next_meeting:
        set_planned_meeting(client_id, next_meeting)

    # Синхронизация с Merchrules (кредсы из профиля)
    mr_ok = False
    try:
        mr_login, mr_password = get_user_mr_creds(user)
        mr_result = await sync_meeting_to_merchrules(
            client_name=client["name"],
            meeting_date=meeting_date,
            meeting_type="checkup",
            summary=summary,
            mood=mood,
            next_meeting=next_meeting,
            aq_tasks=[],
            client_tasks=[],
            site_ids=client.get("site_ids") or "",
            login=mr_login,
            password=mr_password,
        )
        mr_ok = mr_result.get("ok", False)
    except Exception as exc:
        logging.warning("MR sync error (checkup-done): %s", exc)

    # Синхронизация с Airtable
    airtable_ok = False
    try:
        at_result = await sync_meeting_to_airtable(
            client_name=client["name"],
            meeting_date=meeting_date,
            meeting_type="checkup",
            summary=summary,
            mood=mood,
        )
        airtable_ok = at_result.get("ok", False)
    except Exception as exc:
        logging.warning("Airtable sync error (checkup-done): %s", exc)

    return {
        "ok": True,
        "meeting_id": meeting_id,
        "mr": mr_ok,
        "airtable": airtable_ok,
    }


# ── API: запланировать дату следующей встречи ─────────────────────────────────

@app.post("/api/client/{client_id}/planned-meeting", response_class=JSONResponse)
async def api_planned_meeting(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    planned_date = body.get("date") or None  # None — очищаем
    set_planned_meeting(client_id, planned_date)
    return {"ok": True}


# ── API: пробросить задачи клиента в Merchrules ───────────────────────────────

@app.post("/api/client/{client_id}/push-tasks", response_class=JSONResponse)
async def api_push_tasks(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    site_ids_raw = client.get("site_ids", "") or ""
    if not site_ids_raw.strip():
        return {"ok": False, "error": "У клиента нет site_ids"}

    open_tasks = get_client_tasks(client_id, "open")
    aq_tasks = [t for t in open_tasks if t["owner"] == "anyquery" and not t.get("is_internal")]
    if not aq_tasks:
        return {"ok": True, "count": 0, "note": "Нет открытых задач AnyQuery"}

    mr_login, mr_password = get_user_mr_creds(user)
    if not mr_login or not mr_password:
        return {"ok": False, "error": "Укажи логин и пароль Merchrules в разделе Профиль"}

    import httpx as _httpx, re as _re, io as _io
    site_ids = [s.strip() for s in _re.split(r"[,\s]+", site_ids_raw) if s.strip()]
    merchrules_url = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")

    uploaded = 0
    errors = []
    try:
        async with _httpx.AsyncClient(timeout=30) as hx:
            auth_resp = await hx.post(
                f"{merchrules_url}/backend-v2/auth/login",
                json={"username": mr_login, "password": mr_password},
            )
            if auth_resp.status_code != 200:
                return {"ok": False, "error": f"MR auth failed: {auth_resp.status_code}"}

            token = auth_resp.json().get("token") or auth_resp.json().get("access_token", "")
            headers = {"Authorization": f"Bearer {token}"} if token else {}

            for site_id in site_ids:
                for t in aq_tasks:
                    csv_row = (
                        "title,description,status,priority,team,task_type,assignee,product,link,due_date\n"
                        f"{t['text']},,open,medium,,,any,any_query_web,,{t.get('due_date') or ''}"
                    )
                    r = await hx.post(
                        f"{merchrules_url}/backend-v2/import/tasks/csv",
                        params={"site_id": site_id},
                        files={"file": ("tasks.csv", _io.BytesIO(csv_row.encode()), "text/csv")},
                        headers=headers,
                    )
                    if r.status_code in (200, 201):
                        uploaded += 1
                    else:
                        errors.append(t["text"][:50])
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if uploaded:
        mr_invalidate()

    return {"ok": True, "count": uploaded, "errors": errors}


# ── API: добавить комментарий к встрече ───────────────────────────────────────

@app.post("/api/meeting/{meeting_id}/comment", response_class=JSONResponse)
async def api_meeting_comment(request: Request, meeting_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    comment = (body.get("comment") or "").strip()
    if not comment:
        return {"ok": False, "error": "Пустой комментарий"}

    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404)

    # Дописываем к существующему summary
    existing = meeting.get("summary") or ""
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    new_summary = (existing + f"\n\n[{timestamp}] {comment}").strip()

    import sqlite3 as _sqlite3
    from pathlib import Path as _Path
    db_path = _Path("data/am_hub.db")
    with _sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE meetings SET summary=? WHERE id=?", (new_summary, meeting_id))

    return {"ok": True}
