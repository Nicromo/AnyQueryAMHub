"""
AM Hub — главное приложение FastAPI
"""
import os
from datetime import date, timedelta, datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from database import (
    init_db, get_all_clients, get_client,
    get_client_meetings, get_client_tasks, create_meeting,
    create_tasks_bulk, update_task_status, mark_meeting_tg_sent,
    upsert_client, CHECKUP_DAYS, get_meeting
)
from auth import SessionManager, verify_tg_auth
from tg import build_followup_message, send_to_tg

load_dotenv()

BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("TG_BOT_USERNAME", "")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ALLOWED_IDS = set(int(x) for x in os.getenv("ALLOWED_TG_IDS", "").split(",") if x.strip())

app = FastAPI(title="AM Hub")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
session_mgr = SessionManager(SECRET_KEY)

# Инициализация БД при старте
init_db()


# ── Хелперы ───────────────────────────────────────────────────────────────────

def checkup_status(last_checkup: str | None, segment: str) -> dict:
    """Возвращает статус чекапа: days_left, color, next_date."""
    days = CHECKUP_DAYS.get(segment, 90)
    if not last_checkup:
        return {"days_left": None, "color": "red", "next_date": None, "label": "Нет данных"}

    last = date.fromisoformat(last_checkup)
    next_date = last + timedelta(days=days)
    today = date.today()
    diff = (next_date - today).days

    if diff < 0:
        color = "red"
        label = f"Просрочен {abs(diff)} дн."
    elif diff <= 7:
        color = "yellow"
        label = f"Через {diff} дн."
    else:
        color = "green"
        label = f"Через {diff} дн."

    return {"days_left": diff, "color": color, "next_date": next_date.isoformat(), "label": label}


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
async def index(request: Request, segment: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    clients = get_all_clients()

    # Добавляем статус чекапа к каждому клиенту
    for c in clients:
        c["status"] = checkup_status(c.get("last_checkup") or c.get("last_meeting"), c["segment"])

    # Фильтр по сегменту
    if segment:
        clients = [c for c in clients if c["segment"] == segment]

    # Сортировка: сначала просроченные
    clients.sort(key=lambda c: c["status"]["days_left"] if c["status"]["days_left"] is not None else 999)

    segments = ["ENT", "SME", "SMB", "SS"]
    counts = {s: sum(1 for c in get_all_clients() if c["segment"] == s) for s in segments}

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "clients": clients,
        "segment": segment,
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

    return templates.TemplateResponse("prep.html", {
        "request": request,
        "user": user,
        "client": client,
        "meetings": meetings,
        "open_tasks": open_tasks,
        "status": status,
        "today": date.today().isoformat(),
        "checkup_days": CHECKUP_DAYS[client["segment"]],
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

    # Отправка в TG
    tg_ok = False
    if send_tg and BOT_TOKEN and client.get("tg_chat_id"):
        aq_tasks = [t for t in tasks if t["owner"] == "anyquery"]
        cl_tasks = [t for t in tasks if t["owner"] == "client"]
        msg = build_followup_message(
            client_name=client["name"],
            meeting_date=meeting_date,
            meeting_type=meeting_type,
            summary=summary,
            aq_tasks=aq_tasks,
            client_tasks=cl_tasks,
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
