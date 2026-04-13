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
from airtable_sync import sync_meeting_to_airtable
from database import (
    set_planned_meeting, set_checkup_rating, get_qbr_calendar, get_upcoming_meetings
)

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

    clients = get_all_clients()

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
    all_for_count = get_all_clients()
    counts = {s: sum(1 for c in all_for_count if c["segment"] == s) for s in segments}

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "clients": clients,
        "segment": segment,
        "sort": sort,
        "segments": segments,
        "counts": counts,
        "today": date.today().isoformat(),
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

    # Синхронизация с MerchRules
    aq_tasks_list = [t for t in tasks if t["owner"] == "anyquery"]
    cl_tasks_list = [t for t in tasks if t["owner"] == "client"]
    await sync_meeting_to_merchrules(
        client_name=client["name"],
        meeting_date=meeting_date,
        meeting_type=meeting_type,
        summary=summary,
        mood=mood,
        next_meeting=next_meeting or None,
        aq_tasks=aq_tasks_list,
        client_tasks=cl_tasks_list,
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

@app.get("/top50", response_class=HTMLResponse)
async def top50_page(request: Request, mode: str = "weekly"):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    # Получаем список имён клиентов из нашей БД
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
        "today": date.today().isoformat(),
        "sheet_url": (
            f"https://docs.google.com/spreadsheets/d/{SHEETS_SPREADSHEET_ID}"
            f"/edit#gid={SHEETS_TOP50_GID}"
        ),
    })


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
    mr_invalidate()
    clients = get_all_clients()
    data = await sync_clients_from_merchrules(clients)
    return {"ok": True, "synced_sites": len(data)}


@app.get("/api/mr/clients", response_class=JSONResponse)
async def mr_clients(request: Request):
    """
    Возвращает агрегированные MR-данные по всем клиентам.
    Формат: { client_id: { open_tasks, blocked_tasks, overdue_tasks, last_meeting } }
    """
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    clients = get_all_clients()
    mr_data = await sync_clients_from_merchrules(clients)

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

    # Пробуем загрузить в Merchrules через существующую сессию клиента
    import re as _re
    site_ids = [s.strip() for s in _re.split(r"[,\s]+", site_ids_raw) if s.strip()]

    merchrules_url = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
    mr_login = os.getenv("MERCHRULES_LOGIN", "")
    mr_password = os.getenv("MERCHRULES_PASSWORD", "")

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
                "Для загрузки в Merchrules добавь MERCHRULES_LOGIN и MERCHRULES_PASSWORD в Railway Variables."
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

    # Синхронизация с Merchrules
    mr_ok = False
    try:
        mr_result = await sync_meeting_to_merchrules(
            client_name=client["name"],
            meeting_date=meeting_date,
            meeting_type="checkup",
            summary=summary,
            mood=mood,
            next_meeting=next_meeting,
            aq_tasks=[],
            client_tasks=[],
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

    mr_login    = os.getenv("MERCHRULES_LOGIN", "")
    mr_password = os.getenv("MERCHRULES_PASSWORD", "")
    if not mr_login or not mr_password:
        return {"ok": False, "error": "Нет кредов MR (MERCHRULES_LOGIN / MERCHRULES_PASSWORD)"}

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
