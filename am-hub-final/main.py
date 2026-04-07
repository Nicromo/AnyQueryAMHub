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
    # Новые функции
    mark_followup_sent, mark_postmit_sent, set_qbr_score, set_meeting_time,
    get_all_followups, get_followup_pending,
    calculate_health_score, update_client_health_score,
    save_health_snapshot, get_health_history,
    get_message_templates, add_message_template, delete_message_template,
    get_task_templates, add_task_template, delete_task_template,
    get_knowledge_base, add_knowledge_item, delete_knowledge_item,
    log_chat_activity, get_chat_activity, get_clients_without_recent_chat,
    get_improvements, add_improvement, update_improvement_result, delete_improvement,
    get_recurring_tasks_to_create, create_recurring_copy,
    CHAT_NORM_DAYS,
)
from auth import SessionManager, verify_tg_auth
from tg import build_followup_message, send_to_tg
from merchrules import sync_meeting_to_merchrules
from sheets import get_top50_data, SHEETS_SPREADSHEET_ID, SHEETS_TOP50_GID
import tg_bot
from ai_followup import (
    process_transcript as ai_process_transcript,
    generate_pre_meeting_brief,
    generate_followup_draft,
    extract_tasks_from_chat,
    generate_client_recommendations,
    generate_qbr_report,
)
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

    # ── Авто-планирование следующей встречи (фича #4) ────────────────────────
    if not next_meeting:
        days_norm = CHECKUP_DAYS.get(client["segment"], 90)
        auto_next = (date.fromisoformat(meeting_date) + timedelta(days=days_norm)).isoformat()
        set_planned_meeting(client_id, auto_next)
    else:
        set_planned_meeting(client_id, next_meeting)

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


# ═══════════════════════════════════════════════════════════════════════════════
# НОВЫЕ МАРШРУТЫ — Фичи #1–30
# ═══════════════════════════════════════════════════════════════════════════════

# ── #3: Отправка постмита в TG-канал клиента ────────────────────────────────

@app.post("/api/meeting/{meeting_id}/send-postmit", response_class=JSONResponse)
async def api_send_postmit(request: Request, meeting_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404)

    client = get_client(meeting["client_id"])
    if not client:
        raise HTTPException(404)

    tg_chat_id = client.get("tg_chat_id", "")
    if not tg_chat_id:
        return {"ok": False, "error": "У клиента не указан tg_chat_id. Добавь его в карточке клиента."}

    postmit = (meeting.get("summary") or "").strip()
    if not postmit:
        return {"ok": False, "error": "Постмит пустой. Сначала заполни итоги встречи."}

    from tg import send_to_tg
    ok = await send_to_tg(BOT_TOKEN, tg_chat_id, postmit)
    if ok:
        mark_postmit_sent(meeting_id)
    return {"ok": ok, "error": None if ok else "Ошибка отправки в Telegram"}


# ── #11/#12: Трекер фолоуапов ────────────────────────────────────────────────

@app.get("/followups", response_class=HTMLResponse)
async def followups_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    followups = get_all_followups(days_back=30)
    pending = get_followup_pending(days_back=14)

    return templates.TemplateResponse("followups.html", {
        "request": request,
        "user": user,
        "followups": followups,
        "pending_count": len(pending),
        "today": date.today().isoformat(),
    })


@app.post("/api/meeting/{meeting_id}/mark-followup", response_class=JSONResponse)
async def api_mark_followup(request: Request, meeting_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    mark_followup_sent(meeting_id)
    return {"ok": True}


# ── #13: AI-черновик фолоуапа ────────────────────────────────────────────────

@app.get("/api/meeting/{meeting_id}/followup-draft", response_class=JSONResponse)
async def api_followup_draft(request: Request, meeting_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404)

    client = get_client(meeting["client_id"])
    if not client:
        raise HTTPException(404)

    aq_tasks = get_client_tasks(client["id"], "open")
    aq_tasks = [t for t in aq_tasks if t["owner"] == "anyquery" and t.get("meeting_id") == meeting_id]
    cl_tasks = [t for t in get_client_tasks(client["id"], "open") if t["owner"] == "client" and t.get("meeting_id") == meeting_id]

    next_meeting = client.get("planned_meeting") or ""
    draft = await generate_followup_draft(client, meeting, aq_tasks, cl_tasks, next_meeting)
    return {"ok": True, "draft": draft}


# ── #10: Kanban-доска задач ──────────────────────────────────────────────────

@app.get("/tasks/kanban", response_class=HTMLResponse)
async def kanban_page(request: Request, client_id: int = 0, segment: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    clients = get_all_clients()
    all_statuses = ["open", "done", "blocked"]

    columns = {}
    for status in ["open", "blocked", "done"]:
        tasks = get_all_tasks(status)
        if client_id:
            tasks = [t for t in tasks if t["client_id"] == client_id]
        if segment:
            tasks = [t for t in tasks if t.get("segment") == segment]
        columns[status] = tasks

    filter_client = get_client(client_id) if client_id else None

    return templates.TemplateResponse("kanban.html", {
        "request": request,
        "user": user,
        "columns": columns,
        "clients": clients,
        "filter_client": filter_client,
        "filter_segment": segment,
        "today": date.today().isoformat(),
    })


@app.post("/api/task/{task_id}/status", response_class=JSONResponse)
async def api_task_status(request: Request, task_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    status = body.get("status", "open")
    if status not in ("open", "done", "blocked"):
        return {"ok": False, "error": "Invalid status"}
    update_task_status(task_id, status)
    return {"ok": True}


@app.post("/api/task/{task_id}/recurring", response_class=JSONResponse)
async def api_task_recurring(request: Request, task_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    days = int(body.get("days", 0))
    import sqlite3 as _sq
    from pathlib import Path as _P
    with _sq.connect(_P("data/am_hub.db")) as conn:
        conn.execute(
            "UPDATE tasks SET recurring=?, recurring_days=? WHERE id=?",
            (1 if days > 0 else 0, days, task_id)
        )
    return {"ok": True}


# ── #8: Шаблоны наборов задач ────────────────────────────────────────────────

@app.get("/task-templates", response_class=HTMLResponse)
async def task_templates_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    templates_list = get_task_templates()
    clients = get_all_clients()

    return templates.TemplateResponse("task_templates.html", {
        "request": request,
        "user": user,
        "task_templates": templates_list,
        "clients": clients,
        "today": date.today().isoformat(),
    })


@app.post("/api/task-templates", response_class=JSONResponse)
async def api_add_task_template(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    name = (body.get("name") or "").strip()
    category = body.get("category", "general")
    tasks = body.get("tasks", [])
    if not name or not tasks:
        return {"ok": False, "error": "name and tasks required"}
    template_id = add_task_template(name, category, tasks)
    return {"ok": True, "id": template_id}


@app.delete("/api/task-templates/{template_id}", response_class=JSONResponse)
async def api_delete_task_template(request: Request, template_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    delete_task_template(template_id)
    return {"ok": True}


@app.post("/api/client/{client_id}/apply-template/{template_id}", response_class=JSONResponse)
async def api_apply_template(request: Request, client_id: int, template_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    templates_list = get_task_templates()
    tmpl = next((t for t in templates_list if t["id"] == template_id), None)
    if not tmpl:
        raise HTTPException(404, "Шаблон не найден")

    today = date.today()
    tasks_to_create = []
    for t in tmpl.get("tasks", []):
        days = int(t.get("days", 7))
        due = (today + timedelta(days=days)).isoformat()
        tasks_to_create.append({
            "owner": t.get("owner", "anyquery"),
            "text": t.get("text", ""),
            "due_date": due,
        })

    if tasks_to_create:
        create_tasks_bulk(None, client_id, tasks_to_create)

    return {"ok": True, "created": len(tasks_to_create)}


# ── #22: Шаблоны сообщений ───────────────────────────────────────────────────

@app.get("/message-templates", response_class=HTMLResponse)
async def message_templates_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    msg_templates = get_message_templates()
    categories = sorted(set(t["category"] for t in msg_templates))

    return templates.TemplateResponse("message_templates.html", {
        "request": request,
        "user": user,
        "msg_templates": msg_templates,
        "categories": categories,
        "today": date.today().isoformat(),
    })


@app.post("/api/message-templates", response_class=JSONResponse)
async def api_add_message_template(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    name = (body.get("name") or "").strip()
    category = body.get("category", "general")
    template_text = (body.get("template_text") or "").strip()
    if not name or not template_text:
        return {"ok": False, "error": "name and template_text required"}
    tmpl_id = add_message_template(name, category, template_text)
    return {"ok": True, "id": tmpl_id}


@app.delete("/api/message-templates/{tmpl_id}", response_class=JSONResponse)
async def api_delete_message_template(request: Request, tmpl_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    delete_message_template(tmpl_id)
    return {"ok": True}


# ── #23: База знаний ─────────────────────────────────────────────────────────

@app.get("/knowledge-base", response_class=HTMLResponse)
async def knowledge_base_page(request: Request, category: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    items = get_knowledge_base(category or None)
    categories = sorted(set(i["category"] for i in get_knowledge_base()))

    return templates.TemplateResponse("knowledge_base.html", {
        "request": request,
        "user": user,
        "items": items,
        "categories": categories,
        "filter_category": category,
        "today": date.today().isoformat(),
    })


@app.post("/api/knowledge-base", response_class=JSONResponse)
async def api_add_knowledge(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title required"}
    item_id = add_knowledge_item(
        category=body.get("category", "search"),
        title=title,
        description=body.get("description", ""),
        metric_name=body.get("metric_name", ""),
        metric_result=body.get("metric_result", ""),
        applies_to=body.get("applies_to", ""),
    )
    return {"ok": True, "id": item_id}


@app.delete("/api/knowledge-base/{item_id}", response_class=JSONResponse)
async def api_delete_knowledge(request: Request, item_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    delete_knowledge_item(item_id)
    return {"ok": True}


# ── #24: AI-рекомендации клиенту ─────────────────────────────────────────────

@app.get("/api/client/{client_id}/recommendations", response_class=JSONResponse)
async def api_client_recommendations(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    meetings = get_client_meetings(client_id, limit=10)
    open_tasks = get_client_tasks(client_id, "open")
    kb = get_knowledge_base()

    recs = await generate_client_recommendations(client, meetings, open_tasks, kb)
    return {"ok": True, "recommendations": recs}


# ── #19: Health Score история ─────────────────────────────────────────────────

@app.get("/api/client/{client_id}/health-history", response_class=JSONResponse)
async def api_health_history(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    history = get_health_history(client_id, months=6)
    current = calculate_health_score(client_id)
    return {"ok": True, "history": history, "current": current}


# ── #25: Трекер A/B улучшений ────────────────────────────────────────────────

@app.get("/improvements", response_class=HTMLResponse)
async def improvements_page(request: Request, client_id: int = 0):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    items = get_improvements(client_id or None)
    clients = get_all_clients()
    filter_client = get_client(client_id) if client_id else None

    return templates.TemplateResponse("improvements.html", {
        "request": request,
        "user": user,
        "items": items,
        "clients": clients,
        "filter_client": filter_client,
        "today": date.today().isoformat(),
    })


@app.post("/api/improvements", response_class=JSONResponse)
async def api_add_improvement(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    client_id = int(body.get("client_id", 0))
    title = (body.get("title") or "").strip()
    if not client_id or not title:
        return {"ok": False, "error": "client_id and title required"}
    item_id = add_improvement(
        client_id=client_id,
        title=title,
        metric_name=body.get("metric_name", ""),
        metric_before=body.get("metric_before", ""),
        launched_at=body.get("launched_at") or None,
        notes=body.get("notes", ""),
        task_id=body.get("task_id") or None,
    )
    return {"ok": True, "id": item_id}


@app.post("/api/improvements/{item_id}/result", response_class=JSONResponse)
async def api_improvement_result(request: Request, item_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    update_improvement_result(
        improvement_id=item_id,
        metric_after=body.get("metric_after", ""),
        result_at=body.get("result_at") or None,
        status=body.get("status", "success"),
        notes=body.get("notes", ""),
    )
    return {"ok": True}


@app.delete("/api/improvements/{item_id}", response_class=JSONResponse)
async def api_delete_improvement(request: Request, item_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    delete_improvement(item_id)
    return {"ok": True}


# ── #26/#27: Генерация QBR + QBR Score ──────────────────────────────────────

@app.get("/api/qbr/{client_id}/generate", response_class=JSONResponse)
async def api_qbr_generate(request: Request, client_id: int, quarter: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    if not quarter:
        d = date.today()
        quarter = f"Q{(d.month - 1) // 3 + 1} {d.year}"

    # Встречи за квартал (примерно 90 дней)
    all_meetings = get_client_meetings(client_id, limit=20)
    cutoff = (date.today() - timedelta(days=95)).isoformat()
    quarter_meetings = [m for m in all_meetings if (m.get("meeting_date") or "") >= cutoff]

    done_tasks = get_client_tasks(client_id, "done")
    open_tasks = get_client_tasks(client_id, "open")

    result = await generate_qbr_report(client, quarter_meetings, done_tasks, open_tasks, quarter)
    return result


@app.post("/api/meeting/{meeting_id}/qbr-score", response_class=JSONResponse)
async def api_qbr_score(request: Request, meeting_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    score = int(body.get("score", 0))
    if not 1 <= score <= 5:
        return {"ok": False, "error": "Score must be 1-5"}
    set_qbr_score(meeting_id, score)
    return {"ok": True}


# ── #20: Трекер активности в чатах ───────────────────────────────────────────

@app.get("/chat-activity", response_class=HTMLResponse)
async def chat_activity_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    clients = get_clients_without_recent_chat()
    overdue = [c for c in clients if c.get("chat_overdue")]
    ok_clients = [c for c in clients if not c.get("chat_overdue")]

    return templates.TemplateResponse("chat_activity.html", {
        "request": request,
        "user": user,
        "overdue": overdue,
        "ok_clients": ok_clients,
        "today": date.today().isoformat(),
        "chat_norm": CHAT_NORM_DAYS,
    })


@app.post("/api/client/{client_id}/chat-log", response_class=JSONResponse)
async def api_chat_log(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    direction = body.get("direction", "am")
    note = (body.get("note") or "").strip()
    record_id = log_chat_activity(client_id, direction, note)
    return {"ok": True, "id": record_id}


@app.get("/api/client/{client_id}/chat-log", response_class=JSONResponse)
async def api_get_chat_log(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    activity = get_chat_activity(client_id, limit=20)
    return {"ok": True, "activity": activity}


# ── #21: Извлечение задач из переписки ───────────────────────────────────────

@app.post("/api/extract-tasks", response_class=JSONResponse)
async def api_extract_tasks(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    text = (body.get("text") or "").strip()
    client_id = int(body.get("client_id", 0))
    client_name = body.get("client_name", "")

    if not text:
        return {"ok": False, "error": "Текст переписки пустой"}

    if client_id and not client_name:
        c = get_client(client_id)
        if c:
            client_name = c["name"]

    tasks = await extract_tasks_from_chat(text, client_name)
    return {"ok": True, "tasks": tasks}


@app.post("/api/client/{client_id}/create-from-chat", response_class=JSONResponse)
async def api_create_from_chat(request: Request, client_id: int):
    """Создать задачи извлечённые из переписки."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    tasks = body.get("tasks", [])
    if not tasks:
        return {"ok": False, "error": "Нет задач"}
    create_tasks_bulk(None, client_id, tasks)
    return {"ok": True, "created": len(tasks)}


# ── #16: Health Score API ─────────────────────────────────────────────────────

@app.post("/api/client/{client_id}/recalc-health", response_class=JSONResponse)
async def api_recalc_health(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    result = update_client_health_score(client_id)
    save_health_snapshot(client_id, result["score"], result["color"])
    return {"ok": True, "score": result["score"], "color": result["color"]}


# ── #18: Чеклист здоровья платформы ─────────────────────────────────────────

PRODUCT_HEALTH_CHECKLIST = {
    "search": [
        {"id": "s1", "text": "Нулевые результаты < 3%?", "hint": "Скачать топ-100 запросов без результатов"},
        {"id": "s2", "text": "Синонимы настроены для топ-категорий?", "hint": "Проверить покрытие по частотным запросам"},
        {"id": "s3", "text": "Стоп-слова актуальны?", "hint": "Проверить нет ли лишних / устаревших"},
        {"id": "s4", "text": "Бустинг настроен для сезонных товаров?", "hint": "Учесть текущие акции и сезон"},
        {"id": "s5", "text": "Конверсия поиска в норме (vs. прошлый период)?", "hint": "Сравнить с прошлым месяцем"},
        {"id": "s6", "text": "CTR поисковой выдачи в норме?", "hint": "Норма: > 30% для ENT"},
    ],
    "recommendations": [
        {"id": "r1", "text": "CTR блока рекомендаций 7–10%?", "hint": "Проверить в дашборде Merchrules"},
        {"id": "r2", "text": "Алгоритм ротации настроен правильно?", "hint": "Проверить приоритеты и весовые коэффициенты"},
        {"id": "r3", "text": "Нет устаревших / снятых с продажи товаров в выдаче?", "hint": "Проверить топ-10 рекомендуемых"},
    ],
    "reviews": [
        {"id": "rv1", "text": "Средний рейтинг > 4.0?", "hint": "Посмотреть общий рейтинг за период"},
        {"id": "rv2", "text": "Все негативные отзывы получили ответ?", "hint": "Проверить отзывы 1-2 звезды"},
        {"id": "rv3", "text": "Количество отзывов за период растёт?", "hint": "Сравнить с прошлым периодом"},
    ],
}


@app.get("/api/product-health-checklist", response_class=JSONResponse)
async def api_product_health_checklist(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    return {"ok": True, "checklist": PRODUCT_HEALTH_CHECKLIST}


# ── #1: Ручной запуск AI-брифа ───────────────────────────────────────────────

@app.get("/api/client/{client_id}/pre-meeting-brief", response_class=JSONResponse)
async def api_pre_meeting_brief(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    meetings = get_client_meetings(client_id, limit=3)
    open_tasks = get_client_tasks(client_id, "open")
    health = calculate_health_score(client_id)

    brief = await generate_pre_meeting_brief(client, meetings, open_tasks, health)
    return {"ok": True, "brief": brief, "health": health}


# ── Обновить планирование в api_checkup_done (авто) ─────────────────────────

# api_checkup_done уже есть выше, просто добавляем авто-планирование через patch

@app.get("/qbr/{client_id}/generate", response_class=HTMLResponse)
async def qbr_generate_page(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    d = date.today()
    quarter = f"Q{(d.month - 1) // 3 + 1} {d.year}"
    cutoff = (d - timedelta(days=95)).isoformat()
    all_meetings = get_client_meetings(client_id, limit=20)
    quarter_meetings = [m for m in all_meetings if (m.get("meeting_date") or "") >= cutoff]
    done_tasks = get_client_tasks(client_id, "done")
    open_tasks = get_client_tasks(client_id, "open")
    health = calculate_health_score(client_id)
    client["health_score"] = health["score"]

    return templates.TemplateResponse("qbr_generate.html", {
        "request": request,
        "user": user,
        "client": client,
        "quarter": quarter,
        "quarter_meetings": quarter_meetings,
        "done_tasks": done_tasks,
        "open_tasks": open_tasks,
        "today": d.isoformat(),
    })


@app.post("/api/meeting/{meeting_id}/set-time", response_class=JSONResponse)
async def api_meeting_set_time(request: Request, meeting_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    body = await request.json()
    meeting_time = (body.get("time") or "").strip()
    set_meeting_time(meeting_id, meeting_time)
    return {"ok": True}
