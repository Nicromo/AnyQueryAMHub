"""
AM Hub вЂ” РіР»Р°РІРЅРѕРµ РїСЂРёР»РѕР¶РµРЅРёРµ FastAPI
"""
import os
import logging
from datetime import date, timedelta, datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, Response, HTTPException
from starlette.requests import Request
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

# Railway Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё Р·Р°РґР°С‘С‚ RAILWAY_PUBLIC_DOMAIN
RAILWAY_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")

app = FastAPI(title="AM Hub")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
session_mgr = SessionManager(SECRET_KEY)

# РРЅРёС†РёР°Р»РёР·Р°С†РёСЏ Р‘Р” РїСЂРё СЃС‚Р°СЂС‚Рµ
init_db()
seed_clients()


@app.on_event("startup")
async def startup_event():
    """РџСЂРё Р·Р°РїСѓСЃРєРµ РЅР° Railway: СЂРµРіРёСЃС‚СЂРёСЂСѓРµРј webhook + СЃС‚Р°СЂС‚СѓРµРј РїР»Р°РЅРёСЂРѕРІС‰РёРє."""
    if BOT_TOKEN and RAILWAY_DOMAIN:
        webhook_url = f"https://{RAILWAY_DOMAIN}/tg/webhook"
        ok = await tg_bot.set_webhook(webhook_url)
        if ok:
            logging.info("TG webhook registered: %s", webhook_url)
        else:
            logging.warning("TG webhook registration failed")

    # Р—Р°РїСѓСЃРєР°РµРј РїР»Р°РЅРёСЂРѕРІС‰РёРє (СѓС‚СЂРµРЅРЅРёР№ РїР»Р°РЅ, РґР°Р№РґР¶РµСЃС‚, MR sync)
    try:
        from scheduler import start_scheduler
        start_scheduler()
    except Exception as exc:
        logging.warning("Scheduler start failed: %s", exc)


# в”Ђв”Ђ РҐРµР»РїРµСЂС‹ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

# checkup_status С‚РµРїРµСЂСЊ Р¶РёРІС‘С‚ РІ database.py вЂ” РёРјРїРѕСЂС‚РёСЂСѓРµРј РѕС‚С‚СѓРґР°

def get_user_or_redirect(request: Request):
    user = session_mgr.get_user(request)
    if not user:
        return None
    return user


def get_tg_id(user) -> Optional[int]:
    """РР·РІР»РµРєР°РµС‚ tg_id РёР· РѕР±СЉРµРєС‚Р° СЃРµСЃСЃРёРё."""
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def get_user_mr_creds(user) -> tuple[str, str]:
    """
    Р’РѕР·РІСЂР°С‰Р°РµС‚ (mr_login, mr_password) РґР»СЏ С‚РµРєСѓС‰РµРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ.
    РџСЂРёРѕСЂРёС‚РµС‚: РїСЂРѕС„РёР»СЊ РІ Р‘Р” в†’ env-РїРµСЂРµРјРµРЅРЅС‹Рµ.
    """
    tg_id = get_tg_id(user)
    if tg_id:
        profile = get_manager_profile(tg_id)
        if profile.get("mr_login") and profile.get("mr_password"):
            return profile["mr_login"], profile["mr_password"]
    # Fallback вЂ” РіР»РѕР±Р°Р»СЊРЅС‹Рµ РїРµСЂРµРјРµРЅРЅС‹Рµ РѕРєСЂСѓР¶РµРЅРёСЏ
    return os.getenv("MERCHRULES_LOGIN", ""), os.getenv("MERCHRULES_PASSWORD", "")


# в”Ђв”Ђ Auth в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "bot_username": BOT_USERNAME,
    })


@app.get("/auth/telegram")
async def tg_callback(request: Request, response: Response):
    """Telegram Login Widget СЂРµРґРёСЂРµРєС‚РёС‚ СЃСЋРґР° СЃ РїР°СЂР°РјРµС‚СЂР°РјРё."""
    data = dict(request.query_params)
    if BOT_TOKEN and not verify_tg_auth(dict(data), BOT_TOKEN):
        raise HTTPException(status_code=403, detail="РќРµРІРµСЂРЅР°СЏ РїРѕРґРїРёСЃСЊ Telegram")

    tg_id = int(data.get("id", 0))
    tg_name = data.get("first_name", "") + " " + data.get("last_name", "")
    tg_username = data.get("username", "")

    # РџСЂРѕРІРµСЂСЏРµРј РґРѕСЃС‚СѓРї (РµСЃР»Рё СЃРїРёСЃРѕРє РЅРµ РїСѓСЃС‚РѕР№)
    if ALLOWED_IDS and tg_id not in ALLOWED_IDS:
        raise HTTPException(status_code=403, detail="Р”РѕСЃС‚СѓРї Р·Р°РєСЂС‹С‚")

    token = session_mgr.create_session(tg_id, tg_name.strip())
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie("session", token, max_age=86400 * 7, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("session")
    return resp


# в”Ђв”Ђ Р“Р»Р°РІРЅР°СЏ вЂ” С‚СЂРµРєРµСЂ С‡РµРєР°РїРѕРІ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, segment: str = "", sort: str = ""):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    # Р—Р°РіСЂСѓР¶Р°РµРј РєР»РёРµРЅС‚РѕРІ: РµСЃР»Рё Сѓ РјРµРЅРµРґР¶РµСЂР° РµСЃС‚СЊ СЃРІРѕР№ СЃРїРёСЃРѕРє вЂ” С‚РѕР»СЊРєРѕ РµРіРѕ
    tg_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    clients = get_all_clients_for_manager(tg_id)

    # Р”РѕР±Р°РІР»СЏРµРј СЃС‚Р°С‚СѓСЃ С‡РµРєР°РїР° Рё РґРѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ РїРѕР»СЏ Рє РєР°Р¶РґРѕРјСѓ РєР»РёРµРЅС‚Сѓ
    for c in clients:
        c["status"] = checkup_status(c.get("last_checkup") or c.get("last_meeting"), c["segment"])
        # РЎС‡РёС‚Р°РµРј Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅРЅС‹Рµ Р·Р°РґР°С‡Рё РѕС‚РґРµР»СЊРЅРѕ (РѕС‚РґРµР»СЊРЅС‹Р№ Р·Р°РїСЂРѕСЃ)
        c["blocked_tasks"] = len(get_client_tasks(c["id"], "blocked"))
        # РќР°СЃС‚СЂРѕРµРЅРёРµ РїРѕСЃР»РµРґРЅРµР№ РІСЃС‚СЂРµС‡Рё
        meetings = get_client_meetings(c["id"], limit=1)
        c["mood"] = meetings[0]["mood"] if meetings else "neutral"

    # Р¤РёР»СЊС‚СЂ РїРѕ СЃРµРіРјРµРЅС‚Сѓ
    if segment:
        clients = [c for c in clients if c["segment"] == segment]

    # РЎРѕСЂС‚РёСЂРѕРІРєР°: СЃРЅР°С‡Р°Р»Р° С‚СЂРµР±СѓСЋС‰РёРµ РІРЅРёРјР°РЅРёСЏ
    def attention_score(c):
        s = 0
        if c["status"]["color"] == "red":      s += 100
        if c["blocked_tasks"] > 0:             s += 50
        if c.get("mood") == "risk":            s += 40
        if c["status"]["color"] == "yellow":   s += 20
        if c.get("open_tasks", 0) > 0:         s += 5
        return -s  # РѕС‚СЂРёС†Р°С‚РµР»СЊРЅС‹Р№ вЂ” С‡С‚РѕР±С‹ sort() СЃС‚Р°РІРёР» РІС‹СЃРѕРєРёР№ Р±Р°Р»Р» РїРµСЂРІС‹Рј

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


# в”Ђв”Ђ РџРѕРґРіРѕС‚РѕРІРєР° Рє РІСЃС‚СЂРµС‡Рµ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.get("/prep/{client_id}", response_class=HTMLResponse)
async def prep_page(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404, "РљР»РёРµРЅС‚ РЅРµ РЅР°Р№РґРµРЅ")

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


# в”Ђв”Ђ Р¤РѕР»РѕСѓР°Рї в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
    # Р—Р°РґР°С‡Рё AnyQuery вЂ” РјР°СЃСЃРёРІС‹
    aq_task_text: list[str] = Form(default=[]),
    aq_task_due: list[str] = Form(default=[]),
    # Р—Р°РґР°С‡Рё РєР»РёРµРЅС‚Р°
    cl_task_text: list[str] = Form(default=[]),
    cl_task_due: list[str] = Form(default=[]),
):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    # РЎРѕР·РґР°С‘Рј РІСЃС‚СЂРµС‡Сѓ
    meeting_id = create_meeting(
        client_id=client_id,
        meeting_date=meeting_date,
        meeting_type=meeting_type,
        summary=summary,
        mood=mood,
        next_meeting=next_meeting or None,
    )

    # Р—Р°РґР°С‡Рё
    tasks = []
    for text, due in zip(aq_task_text, aq_task_due):
        if text.strip():
            tasks.append({"owner": "anyquery", "text": text.strip(), "due_date": due or None})
    for text, due in zip(cl_task_text, cl_task_due):
        if text.strip():
            tasks.append({"owner": "client", "text": text.strip(), "due_date": due or None})

    if tasks:
        create_tasks_bulk(meeting_id, client_id, tasks)

    # РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ СЃ MerchRules (РєСЂРµРґСЃС‹ РёР· РїСЂРѕС„РёР»СЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ)
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

    # РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ СЃ Airtable (РґР°С‚Р° + РґРѕРїРёСЃР°С‚СЊ РєРѕРјРјРµРЅС‚Р°СЂРёР№)
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

    # РћС‚РїСЂР°РІРєР° РІ TG
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

    # K.Talk СѓРІРµРґРѕРјР»РµРЅРёРµ (РїР°СЂР°Р»Р»РµР»СЊРЅРѕ TG, РІ РєР°РЅР°Р» РјРµРЅРµРґР¶РµСЂР°)
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


# в”Ђв”Ђ QBR в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.get("/qbr/{client_id}", response_class=HTMLResponse)
async def qbr_page(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    # Р‘РµСЂС‘Рј РІСЃС‚СЂРµС‡Рё Р·Р° РїРѕСЃР»РµРґРЅРёРµ 3 РјРµСЃСЏС†Р° РґР»СЏ Р°РІС‚РѕР·Р°РїРѕР»РЅРµРЅРёСЏ
    meetings = get_client_meetings(client_id, limit=20)
    all_tasks = get_client_tasks(client_id, "open") + get_client_tasks(client_id, "done")

    # Р¤РёР»СЊС‚СЂСѓРµРј Р·Р°РґР°С‡Рё РїРѕСЃР»РµРґРЅРёС… 90 РґРЅРµР№
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


# в”Ђв”Ђ РџСЂРѕС„РёР»СЊ РјРµРЅРµРґР¶РµСЂР° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
        return {"ok": False, "error": "Р’РІРµРґРё Р»РѕРіРёРЅ Рё РїР°СЂРѕР»СЊ"}

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
            return {"ok": False, "error": f"РћС€РёР±РєР° {r.status_code}: {r.text[:100]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# в”Ђв”Ђ РќР°СЃС‚СЂРѕР№РєРё РјРµРЅРµРґР¶РµСЂР°: РјРѕР№ СЃРїРёСЃРѕРє РєР»РёРµРЅС‚РѕРІ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђ РРјРїРѕСЂС‚ РєР»РёРµРЅС‚РѕРІ РёР· Airtable CS ALL в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/api/admin/import-airtable", response_class=JSONResponse)
async def api_import_airtable(request: Request):
    """
    Р—Р°РїСѓСЃРєР°РµС‚ РёРјРїРѕСЂС‚ РІСЃРµС… РєР»РёРµРЅС‚РѕРІ РёР· Airtable CS ALL view.
    РђРІС‚Рѕ-РѕРїСЂРµРґРµР»СЏРµС‚ РїРѕР»СЏ, upsert РєР»РёРµРЅС‚РѕРІ, РїСЂРёРІСЏР·С‹РІР°РµС‚ Рє РјРµРЅРµРґР¶РµСЂР°Рј.
    РўРѕР»СЊРєРѕ РґР»СЏ Р°РІС‚РѕСЂРёР·РѕРІР°РЅРЅС‹С… РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№.
    """
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    # РњРѕР¶РЅРѕ РїРµСЂРµРґР°С‚СЊ СЃРІРѕР№ С‚РѕРєРµРЅ Рё view_id С‡РµСЂРµР· body
    token = body.get("token") if isinstance(body, dict) else None
    view_id = body.get("view_id") if isinstance(body, dict) else None

    import_kwargs: dict = {}
    if token:
        import_kwargs["token"] = token
    if view_id:
        import_kwargs["view_id"] = view_id

    try:
        result = await import_clients_from_airtable(**import_kwargs)
        # unmatched_managers вЂ” set, РЅСѓР¶РЅРѕ СЃРµСЂРёР°Р»РёР·РѕРІР°С‚СЊ
        if isinstance(result.get("unmatched_managers"), set):
            result["unmatched_managers"] = list(result["unmatched_managers"])
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# в”Ђв”Ђ Webhook РѕС‚ Merchrules в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/webhook/merchrules", response_class=JSONResponse)
async def webhook_merchrules(request: Request):
    """
    РџСЂРёРЅРёРјР°РµС‚ СЃРѕР±С‹С‚РёСЏ РѕС‚ Merchrules (Р·Р°РґР°С‡Рё, СЃС‚Р°С‚СѓСЃС‹, РєРѕРјРјРµРЅС‚Р°СЂРёРё).
    РЎРµРєСЂРµС‚: Р·Р°РіРѕР»РѕРІРѕРє X-MR-Secret РёР»Рё query ?secret=... РґРѕР»Р¶РµРЅ СЃРѕРІРїР°РґР°С‚СЊ СЃ MR_WEBHOOK_SECRET.
    Р•СЃР»Рё СЃРµРєСЂРµС‚ РЅРµ Р·Р°РґР°РЅ вЂ” РїСЂРёРЅРёРјР°РµРј РІСЃС‘ (РЅРµ СЂРµРєРѕРјРµРЅРґСѓРµС‚СЃСЏ РІ РїСЂРѕРґРµ).

    Merchrules РґРѕР»Р¶РµРЅ СЃР»Р°С‚СЊ POST СЃ JSON:
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

    # РќР°С…РѕРґРёРј РєР»РёРµРЅС‚Р° РїРѕ site_id
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
            # Р—Р°РєСЂС‹РІР°РµРј Р·Р°РґР°С‡Сѓ РІ Р‘Р” РїРѕ СЃРѕРІРїР°РґРµРЅРёСЋ Р·Р°РіРѕР»РѕРІРєР°
            title = (task.get("title") or "").lower().strip()
            if title:
                open_tasks = get_all_tasks("open")
                for t in open_tasks:
                    if t["client_id"] == client_id and title in t["text"].lower():
                        update_task_status(t["id"], "done")
                        logging.info("MR webhook: closed task '%s' for client %s", t["text"], matched_client["name"])

        elif event == "task.created":
            # РЎРѕР·РґР°С‘Рј Р·Р°РґР°С‡Сѓ РІ AM Hub РµСЃР»Рё РµС‘ РЅРµС‚
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


# в”Ђв”Ђ K.Talk API в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/api/profile/test-ktalk", response_class=JSONResponse)
async def api_test_ktalk(request: Request):
    """РџСЂРѕРІРµСЂРёС‚СЊ K.Talk webhook РїРѕРґРєР»СЋС‡РµРЅРёРµ."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    webhook_url = body.get("webhook_url", "").strip()
    if not webhook_url:
        return {"ok": False, "error": "Webhook URL РЅРµ СѓРєР°Р·Р°РЅ"}

    result = await test_ktalk_connection(webhook_url)
    return result


# в”Ђв”Ђ /hub вЂ” РљРѕРјР°РЅРґРЅС‹Р№ С†РµРЅС‚СЂ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.get("/hub", response_class=HTMLResponse)
async def hub_page(request: Request):
    """Р•РґРёРЅС‹Р№ РљРѕРјР°РЅРґРЅС‹Р№ С†РµРЅС‚СЂ вЂ” СЃС‚Р°С‚СѓСЃ РІСЃРµС… РёРЅСЃС‚СЂСѓРјРµРЅС‚РѕРІ, Р±С‹СЃС‚СЂС‹Рµ РґРµР№СЃС‚РІРёСЏ, Р»РѕРі Р°РєС‚РёРІРЅРѕСЃС‚Рё."""
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    tg_id = get_tg_id(user)

    from database import (
        get_all_clients, get_all_tasks, get_all_manager_profiles,
        checkup_status, get_conn
    )

    # РћР±С‰Р°СЏ СЃС‚Р°С‚РёСЃС‚РёРєР°
    all_clients  = get_all_clients()
    all_tasks    = get_all_tasks("open")
    managers     = get_all_manager_profiles()

    overdue = sum(1 for c in all_clients
                  if checkup_status(c.get("last_checkup") or c.get("last_meeting"), c["segment"])["color"] == "red")
    warning = sum(1 for c in all_clients
                  if checkup_status(c.get("last_checkup") or c.get("last_meeting"), c["segment"])["color"] == "yellow")

    # Р‘Р»РёР¶Р°Р№С€РёРµ РІСЃС‚СЂРµС‡Рё (planned_meeting РІ Р±Р»РёР¶Р°Р№С€РёРµ 7 РґРЅРµР№)
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

    # РџРѕСЃР»РµРґРЅСЏСЏ Р°РєС‚РёРІРЅРѕСЃС‚СЊ (РїРѕСЃР»РµРґРЅРёРµ 10 РІСЃС‚СЂРµС‡)
    with get_conn() as conn:
        recent_meetings = conn.execute("""
            SELECT m.id, m.meeting_date, m.meeting_type, m.mood,
                   c.name as client_name, c.segment
            FROM meetings m
            JOIN clients c ON c.id = m.client_id
            ORDER BY m.created_at DESC LIMIT 10
        """).fetchall()

    # РЎС‚Р°С‚СѓСЃ РёРЅСЃС‚СЂСѓРјРµРЅС‚РѕРІ
    airtable_token = os.getenv("AIRTABLE_TOKEN", "")
    mr_login, mr_password = get_user_mr_creds(user)
    bot_token = BOT_TOKEN

    profile = get_manager_profile(tg_id) if tg_id else {}
    ktalk_url = profile.get("ktalk_webhook") or os.getenv("KTALK_WEBHOOK_URL", "")

    tools_status = [
        {
            "name": "Airtable",
            "icon": "рџ“‹",
            "connected": bool(airtable_token),
            "detail": "РђРІС‚Рѕ-СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ РєР»РёРµРЅС‚РѕРІ РєР°Р¶РґС‹Р№ С‡Р°СЃ" if airtable_token else "РўРѕРєРµРЅ РЅРµ Р·Р°РґР°РЅ",
            "url": "https://airtable.com/appEAS1rPKpevoIel",
            "action_url": None,
        },
        {
            "name": "Merchrules",
            "icon": "рџ”—",
            "connected": bool(mr_login),
            "detail": f"РђРєРєР°СѓРЅС‚: {mr_login}" if mr_login else "Р’РѕР№РґРё РІ РџСЂРѕС„РёР»СЊ Рё РґРѕР±Р°РІСЊ РєСЂРµРґСЃС‹",
            "url": "https://merchrules.any-platform.ru",
            "action_url": "/profile",
        },
        {
            "name": "Telegram Bot",
            "icon": "рџ¤–",
            "connected": bool(bot_token),
            "detail": f"@{os.getenv('TG_BOT_USERNAME', '?')}" if bot_token else "TG_BOT_TOKEN РЅРµ Р·Р°РґР°РЅ",
            "url": f"https://t.me/{os.getenv('TG_BOT_USERNAME', '')}" if os.getenv("TG_BOT_USERNAME") else "#",
            "action_url": None,
        },
        {
            "name": "K.Talk",
            "icon": "рџ“№",
            "connected": bool(ktalk_url),
            "detail": "Webhook РЅР°СЃС‚СЂРѕРµРЅ" if ktalk_url else "Webhook РЅРµ РЅР°СЃС‚СЂРѕРµРЅ вЂ” РґРѕР±Р°РІСЊ РІ РїСЂРѕС„РёР»Рµ",
            "url": "https://tbank.ktalk.ru/",
            "action_url": "/profile#ktalk",
        },
        {
            "name": "Google Calendar",
            "icon": "рџ“…",
            "connected": True,  # Р’СЃРµРіРґР° вЂ” С‡РµСЂРµР· URL-СЃСЃС‹Р»РєРё Р±РµР· OAuth
            "detail": "РЎРѕР·РґР°РЅРёРµ СЃРѕР±С‹С‚РёР№ С‡РµСЂРµР· СѓРјРЅС‹Рµ СЃСЃС‹Р»РєРё (Р±РµР· OAuth)",
            "url": "https://calendar.google.com",
            "action_url": None,
        },
    ]

    # Р Р°СЃРїРёСЃР°РЅРёРµ РїР»Р°РЅРёСЂРѕРІС‰РёРєР°
    scheduler_jobs = [
        {"name": "РЈС‚СЂРµРЅРЅРёР№ РїР»Р°РЅ",       "schedule": "09:00 РїРЅ-РїС‚",       "icon": "вЂпёЏ"},
        {"name": "Р•Р¶РµРЅРµРґРµР»СЊРЅС‹Р№ РґР°Р№РґР¶РµСЃС‚","schedule": "РїС‚ 17:00",          "icon": "рџ“Љ"},
        {"name": "РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ MR",    "schedule": "РєР°Р¶РґС‹Р№ С‡Р°СЃ РІ :00",   "icon": "рџ”—"},
        {"name": "РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ Airtable","schedule": "РєР°Р¶РґС‹Р№ С‡Р°СЃ РІ :30", "icon": "рџ“‹"},
        {"name": "РќР°РїРѕРјРёРЅР°РЅРёСЏ Рѕ РІСЃС‚СЂРµС‡Р°С…","schedule": "РєР°Р¶РґС‹Рµ 30 РјРёРЅ",    "icon": "рџ“†"},
        {"name": "РђРІС‚Рѕ-С‡РµРєР°Рї Р·Р°РґР°С‡Рё",   "schedule": "08:00 РµР¶РµРґРЅРµРІРЅРѕ",    "icon": "рџ””"},
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


# в”Ђв”Ђ Admin API: СЂСѓС‡РЅРѕР№ Р·Р°РїСѓСЃРє scheduler jobs в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/api/admin/sync-mr", response_class=JSONResponse)
async def api_admin_sync_mr(request: Request):
    """Р СѓС‡РЅРѕР№ Р·Р°РїСѓСЃРє СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёРё СЃС‚Р°С‚СѓСЃРѕРІ РёР· Merchrules."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    try:
        from scheduler import job_mr_status_sync
        await job_mr_status_sync()
        return {"ok": True, "message": "РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ MR Р·Р°РїСѓС‰РµРЅР°"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/admin/run-morning-plan", response_class=JSONResponse)
async def api_admin_morning_plan(request: Request):
    """Р СѓС‡РЅРѕР№ Р·Р°РїСѓСЃРє СѓС‚СЂРµРЅРЅРµРіРѕ РїР»Р°РЅР° РІ TG."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    try:
        from scheduler import job_morning_plan
        await job_morning_plan()
        return {"ok": True, "message": "РЈС‚СЂРµРЅРЅРёР№ РїР»Р°РЅ РѕС‚РїСЂР°РІР»РµРЅ"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/admin/run-digest", response_class=JSONResponse)
async def api_admin_run_digest(request: Request):
    """Р СѓС‡РЅРѕР№ Р·Р°РїСѓСЃРє РµР¶РµРЅРµРґРµР»СЊРЅРѕРіРѕ РґР°Р№РґР¶РµСЃС‚Р°."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    try:
        from scheduler import job_weekly_digest
        await job_weekly_digest()
        return {"ok": True, "message": "Р”Р°Р№РґР¶РµСЃС‚ РѕС‚РїСЂР°РІР»РµРЅ"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# в”Ђв”Ђ API вЂ” РѕР±РЅРѕРІРёС‚СЊ TG chat_id РєР»РёРµРЅС‚Р° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђ API вЂ” Р·Р°РєСЂС‹С‚СЊ Р·Р°РґР°С‡Сѓ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/api/task/{task_id}/done")
async def close_task(request: Request, task_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)
    update_task_status(task_id, "done")
    ref = request.headers.get("referer", "/")
    return RedirectResponse(ref, status_code=303)


# в”Ђв”Ђ Top-50 вЂ” РІРµР±-СЃС‚СЂР°РЅРёС†Р° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def _load_metrics_for_month(year_month: str) -> Optional[dict]:
    """Р—Р°РіСЂСѓР¶Р°РµС‚ СЃРѕС…СЂР°РЅС‘РЅРЅС‹Рµ РјРµС‚СЂРёРєРё РёР· data/metrics_{year_month}.json."""
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

    month_name_ru = ["РЇРЅРІР°СЂСЊ","Р¤РµРІСЂР°Р»СЊ","РњР°СЂС‚","РђРїСЂРµР»СЊ","РњР°Р№","РСЋРЅСЊ",
                     "РСЋР»СЊ","РђРІРіСѓСЃС‚","РЎРµРЅС‚СЏР±СЂСЊ","РћРєС‚СЏР±СЂСЊ","РќРѕСЏР±СЂСЊ","Р”РµРєР°Р±СЂСЊ"]
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


# в”Ђв”Ђ API: Р·Р°РіСЂСѓР·РёС‚СЊ С„Р°Р№Р» РјРµС‚СЂРёРє (CSV / XLSX) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
        return {"ok": False, "error": "Р¤Р°Р№Р» РЅРµ РЅР°Р№РґРµРЅ"}

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
            return {"ok": False, "error": "РџРѕРґРґРµСЂР¶РёРІР°СЋС‚СЃСЏ С‚РѕР»СЊРєРѕ CSV Рё XLSX"}

    except Exception as exc:
        return {"ok": False, "error": f"РћС€РёР±РєР° СЂР°Р·Р±РѕСЂР° С„Р°Р№Р»Р°: {exc}"}

    if not headers or not rows:
        return {"ok": False, "error": "Р¤Р°Р№Р» РїСѓСЃС‚РѕР№ РёР»Рё РЅРµ СЃРѕРґРµСЂР¶РёС‚ РґР°РЅРЅС‹С…"}

    # Р’С‹С‡РёСЃР»СЏРµРј KPI-РєР°СЂС‚РѕС‡РєРё: С‡РёСЃР»РѕРІС‹Рµ РєРѕР»РѕРЅРєРё в†’ СЃСѓРјРјР° / СЃСЂРµРґРЅРµРµ
    kpis = []
    for i, h in enumerate(headers):
        vals = []
        for r in rows:
            if i < len(r):
                try:
                    vals.append(float(str(r[i]).replace(",", ".").replace(" ", "")))
                except Exception:
                    pass
        if vals and len(vals) >= len(rows) * 0.5:  # >50% С‡РёСЃР»РѕРІС‹Рµ
            total = sum(vals)
            avg = total / len(vals)
            label_lower = h.lower()
            if any(w in label_lower for w in ("gmv","РІС‹СЂСѓС‡РєР°","РѕР±РѕСЂРѕС‚","СЃСѓРјРјР°","СЂСѓР±")):
                kpis.append({"label": h, "value": f"{total:,.0f} в‚Ѕ".replace(",", " ")})
            elif any(w in label_lower for w in ("Р·Р°РєР°Р·","order","cnt","РєРѕР»-РІРѕ","РєРѕР»РёС‡РµСЃС‚РІРѕ")):
                kpis.append({"label": h, "value": f"{int(total):,}".replace(",", " ")})
            elif "РєРѕРЅРІРµСЂ" in label_lower or "%" in h:
                kpis.append({"label": h, "value": f"{avg:.1f}%"})
            elif len(kpis) < 6:
                kpis.append({"label": h, "value": f"{total:,.0f}".replace(",", " ")})
        if len(kpis) >= 6:
            break

    # РЎРѕС…СЂР°РЅСЏРµРј
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


# в”Ђв”Ђ API вЂ” СЃС‚Р°С‚РёСЃС‚РёРєР° РґР°С€Р±РѕСЂРґР° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђ Telegram Webhook в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/tg/webhook")
async def tg_webhook(request: Request):
    """РџСЂРёРЅРёРјР°РµС‚ Updates РѕС‚ Telegram."""
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(400, "Bad JSON")

    # РџРµСЂРµРґР°С‘Рј update РІ РѕР±СЂР°Р±РѕС‚С‡РёРє Р±РѕС‚Р°
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


# в”Ђв”Ђ РџРѕРёСЃРє РєР»РёРµРЅС‚РѕРІ (РґР»СЏ Р±С‹СЃС‚СЂРѕР№ РЅР°РІРёРіР°С†РёРё) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђ РЎРµРіРѕРґРЅСЏ вЂ” РµР¶РµРґРЅРµРІРЅС‹Р№ РїР»Р°РЅ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
        "weekday": ["РџРѕРЅРµРґРµР»СЊРЅРёРє","Р’С‚РѕСЂРЅРёРє","РЎСЂРµРґР°","Р§РµС‚РІРµСЂРі","РџСЏС‚РЅРёС†Р°","РЎСѓР±Р±РѕС‚Р°","Р’РѕСЃРєСЂРµСЃРµРЅСЊРµ"][date.today().weekday()],
        "overdue": overdue,
        "warning": warning[:5],
        "urgent_tasks": overview["urgent_tasks"],
        "week_tasks": overview["week_tasks"],
    })


# в”Ђв”Ђ Р’СЃРµ Р·Р°РґР°С‡Рё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђ Р РѕР°РґРјР°Рї вЂ” СЃРѕР·РґР°РЅРёРµ bulk-Р·Р°РґР°С‡ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.get("/roadmap", response_class=HTMLResponse)
async def roadmap_page(request: Request, client_id: int = 0):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    # Р•СЃР»Рё РѕС‚РєСЂС‹Р»Рё РёР· РєР°СЂС‚РѕС‡РєРё РєР»РёРµРЅС‚Р° вЂ” РїРѕРґСЃС‚Р°РІР»СЏРµРј site_ids
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


# в”Ђв”Ђ Merchrules Sync API в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/api/mr/sync", response_class=JSONResponse)
async def mr_sync(request: Request):
    """РџСЂРёРЅСѓРґРёС‚РµР»СЊРЅРѕ СЃР±СЂР°СЃС‹РІР°РµС‚ РєСЌС€ Рё Р·Р°РЅРѕРІРѕ С‚СЏРЅРµС‚ РґР°РЅРЅС‹Рµ РёР· Merchrules."""
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
    Р’РѕР·РІСЂР°С‰Р°РµС‚ Р°РіСЂРµРіРёСЂРѕРІР°РЅРЅС‹Рµ MR-РґР°РЅРЅС‹Рµ РїРѕ РєР»РёРµРЅС‚Р°Рј РјРµРЅРµРґР¶РµСЂР°.
    Р¤РѕСЂРјР°С‚: { client_id: { open_tasks, blocked_tasks, overdue_tasks, last_meeting } }
    """
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    mr_login, mr_password = get_user_mr_creds(user)
    if not mr_login:
        return {}  # РЅРµС‚ РєСЂРµРґРѕРІ вЂ” РѕС‚РґР°С‘Рј РїСѓСЃС‚РѕР№ РѕС‚РІРµС‚

    clients = get_all_clients_for_manager(get_tg_id(user))
    mr_data = await sync_clients_from_merchrules(clients, login=mr_login, password=mr_password)

    result = {}
    for c in clients:
        agg = get_client_mr_data(mr_data, c.get("site_ids") or "")
        if agg["open_tasks"] > 0 or agg["blocked_tasks"] > 0 or agg["last_meeting"]:
            result[str(c["id"])] = agg

    return result


# в”Ђв”Ђ AI: РѕР±СЂР°Р±РѕС‚РєР° С‚СЂР°РЅСЃРєСЂРёРїС‚Р° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
        return {"error": "РўСЂР°РЅСЃРєСЂРёРїС‚ РїСѓСЃС‚РѕР№"}

    result = await ai_process_transcript(transcript, client_name, meeting_date)
    return result


# в”Ђв”Ђ AI: Р·Р°РіСЂСѓР·РёС‚СЊ Р·Р°РґР°С‡Рё РІ Merchrules + СЃРѕС…СЂР°РЅРёС‚СЊ РІСЃС‚СЂРµС‡Сѓ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
        return {"ok": False, "error": "РќРµС‚ Р·Р°РґР°С‡ РґР»СЏ Р·Р°РіСЂСѓР·РєРё"}
    if not site_ids_raw:
        return {"ok": False, "error": "РќРµС‚ site_id"}

    # РЎРѕС…СЂР°РЅСЏРµРј РІСЃС‚СЂРµС‡Сѓ РІ Р‘Р”
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
            # РЎРѕС…СЂР°РЅСЏРµРј Р·Р°РґР°С‡Рё РІ Р‘Р”
            db_tasks = []
            for t in tasks:
                owner = "anyquery" if t.get("assignee") != "partner" else "client"
                db_tasks.append({"owner": owner, "text": t["title"], "due_date": t.get("due_date")})
            if db_tasks:
                create_tasks_bulk(meeting_id, client_id, db_tasks)

    # РџСЂРѕР±СѓРµРј Р·Р°РіСЂСѓР·РёС‚СЊ РІ Merchrules С‡РµСЂРµР· РєСЂРµРґСЃС‹ С‚РµРєСѓС‰РµРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ
    import re as _re
    site_ids = [s.strip() for s in _re.split(r"[,\s]+", site_ids_raw) if s.strip()]

    merchrules_url = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
    mr_login, mr_password = get_user_mr_creds(user)

    uploaded = []
    errors = []

    if mr_login and mr_password:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=30) as hx:
            # РђРІС‚РѕСЂРёР·СѓРµРјСЃСЏ
            auth_resp = await hx.post(
                f"{merchrules_url}/backend-v2/auth/login",
                json={"username": mr_login, "password": mr_password}
            )
            if auth_resp.status_code != 200:
                return {"ok": False, "error": f"РќРµ СѓРґР°Р»РѕСЃСЊ Р°РІС‚РѕСЂРёР·РѕРІР°С‚СЊСЃСЏ РІ Merchrules: {auth_resp.status_code}"}

            auth_token = auth_resp.json().get("token") or auth_resp.json().get("access_token", "")
            headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}

            for site_id in site_ids:
                for task in tasks:
                    if task.get("assignee") == "partner":
                        continue  # РќРµ Р·Р°РіСЂСѓР¶Р°РµРј Р·Р°РґР°С‡Рё РїР°СЂС‚РЅС‘СЂР°
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
        # РќРµС‚ РєСЂРµРґРѕРІ Merchrules вЂ” С‚РѕР»СЊРєРѕ СЃРѕС…СЂР°РЅСЏРµРј РІ Р‘Р”
        return {
            "ok": True,
            "uploaded": [],
            "errors": [],
            "note": (
                "Р’СЃС‚СЂРµС‡Р° Рё Р·Р°РґР°С‡Рё СЃРѕС…СЂР°РЅРµРЅС‹ РІ AM Hub. "
                "Р”Р»СЏ Р·Р°РіСЂСѓР·РєРё РІ Merchrules СѓРєР°Р¶Рё Р»РѕРіРёРЅ Рё РїР°СЂРѕР»СЊ РІ СЂР°Р·РґРµР»Рµ РџСЂРѕС„РёР»СЊ."
            ),
        }

    # РЎР±СЂР°СЃС‹РІР°РµРј MR-РєСЌС€ РїРѕСЃР»Рµ Р·Р°РіСЂСѓР·РєРё Р·Р°РґР°С‡
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


# в”Ђв”Ђ AI-Р°СЃСЃРёСЃС‚РµРЅС‚: С„РѕР»РѕСѓР°Рї, РїРѕРґРіРѕС‚РѕРІРєР°, СЂРёСЃРєРё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.get("/api/client/{client_id}/prep-brief", response_class=JSONResponse)
async def api_prep_brief(request: Request, client_id: int):
    """РџРѕР»СѓС‡РёС‚СЊ brieferror РґР»СЏ РїРѕРґРіРѕС‚РѕРІРєРё Рє РІСЃС‚СЂРµС‡Рµ."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    try:
        from ai_assistant import generate_prep_brief
        meetings = get_client_meetings(client_id, limit=3)
        tasks = get_client_tasks(client_id, "open")
        brief = await generate_prep_brief(client, meetings, tasks)
        return {"brief": brief, "ok": True}
    except Exception as exc:
        logging.error("prep_brief error: %s", exc)
        return {"brief": "", "ok": False, "error": str(exc)[:200]}


@app.get("/api/client/{client_id}/smart-followup", response_class=JSONResponse)
async def api_smart_followup(request: Request, client_id: int):
    """РџРѕР»СѓС‡РёС‚СЊ СЂРµРєРѕРјРµРЅРґСѓРµРјС‹Р№ С‚РµРєСЃС‚ С„РѕР»РѕСѓР°РїР°."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    try:
        from ai_assistant import generate_smart_followup
        meetings = get_client_meetings(client_id, limit=3)
        tasks = get_client_tasks(client_id, "open")
        text = await generate_smart_followup(client, meetings, tasks)
        return {"text": text, "ok": True}
    except Exception as exc:
        logging.error("smart_followup error: %s", exc)
        return {"text": "", "ok": False, "error": str(exc)[:200]}


@app.get("/api/client/{client_id}/risk", response_class=JSONResponse)
async def api_risk_detection(request: Request, client_id: int):
    """РџРѕР»СѓС‡РёС‚СЊ Р°РЅР°Р»РёР· СЂРёСЃРєРѕРІ РїРѕ Р°РєРєР°СѓРЅС‚Сѓ."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    try:
        from ai_assistant import detect_account_risks
        meetings = get_client_meetings(client_id, limit=3)
        tasks = get_client_tasks(client_id, "open")
        risk_data = await detect_account_risks(client, meetings, tasks)
        return risk_data
    except Exception as exc:
        logging.error("risk_detection error: %s", exc)
        return {"risk_level": "unknown", "flags": [], "recommendation": f"Error: {str(exc)[:100]}"}


@app.get("/api/client/{client_id}/metrics", response_class=JSONResponse)
async def api_client_metrics(request: Request, client_id: int):
    """РџРѕР»СѓС‡РёС‚СЊ РјРµС‚СЂРёРєРё РєР»РёРµРЅС‚Р° РёР· Merchrules."""
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    client = get_client(client_id)
    if not client:
        raise HTTPException(404)

    try:
        from merchrules_sync import get_client_metrics
        site_ids = (client.get("site_ids") or "").strip()
        if not site_ids:
            return {"gmv": 0, "conversion": 0.0, "search_ctr": 0.0, "orders": 0, "error": "no_site_ids"}

        # Р‘РµСЂС‘Рј РїРµСЂРІС‹Р№ site_id
        first_site = site_ids.split(",")[0].strip()
        mr_login, mr_password = get_user_mr_creds(user)
        metrics = await get_client_metrics(first_site, mr_login, mr_password)
        return metrics
    except Exception as exc:
        logging.error("client_metrics error: %s", exc)
        return {"gmv": 0, "conversion": 0.0, "search_ctr": 0.0, "orders": 0, "error": str(exc)[:100]}

# в”Ђв”Ђ Р§РµРєР»РёСЃС‚ РІСЃС‚СЂРµС‡Рё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

# AI-РїРѕРґСЃРєР°Р·РєРё РїРѕ СЃРµРіРјРµРЅС‚Сѓ РґР»СЏ СЃС‚СЂР°РЅРёС†С‹ С‡РµРєР»РёСЃС‚Р°
AI_HINTS_BY_SEGMENT = {
    "ENT": [
        "РљР°Рє РёР·РјРµРЅРёР»СЃСЏ GMV Р·Р° РїРµСЂРёРѕРґ?",
        "Р•СЃС‚СЊ Р»Рё РїР»Р°РЅС‹ РЅР° СЂР°СЃС€РёСЂРµРЅРёРµ?",
        "РљС‚Рѕ РїСЂРёРЅРёРјР°РµС‚ СЂРµС€РµРЅРёСЏ Рѕ Р±СЋРґР¶РµС‚Рµ?",
        "РљР°Рє РѕС†РµРЅРёРІР°РµС‚Рµ ROI РѕС‚ AnyQuery?",
        "Р§С‚Рѕ РјРµС€Р°РµС‚ РјР°СЃС€С‚Р°Р±РёСЂРѕРІР°С‚СЊСЃСЏ?",
    ],
    "SME+": [
        "РљР°РєРёРµ РјРµС‚СЂРёРєРё РІР°Р¶РЅРµРµ РІСЃРµРіРѕ РґР»СЏ РІР°СЃ?",
        "Р§С‚Рѕ РёР· СЂРѕР°РґРјР°РїР° СЃР°РјРѕРµ РїСЂРёРѕСЂРёС‚РµС‚РЅРѕРµ?",
        "Р•СЃС‚СЊ Р»Рё РєРѕРЅРєСѓСЂРµРЅС‚С‹ РєРѕС‚РѕСЂС‹С… РѕС‚СЃР»РµР¶РёРІР°РµС‚Рµ?",
        "РљР°Рє РІР°С€Р° РєРѕРјР°РЅРґР° РёСЃРїРѕР»СЊР·СѓРµС‚ РґР°С€Р±РѕСЂРґ?",
    ],
    "SME-": [
        "РљР°РєРёРµ С„СѓРЅРєС†РёРё РёСЃРїРѕР»СЊР·СѓРµС‚Рµ С‡Р°С‰Рµ РІСЃРµРіРѕ?",
        "Р§С‚Рѕ Р±С‹Р»Рѕ Р±С‹ РїРѕР»РµР·РЅРѕ РґРѕР±Р°РІРёС‚СЊ?",
        "РљР°Рє РѕС†РµРЅРёРІР°РµС‚Рµ СЂР°Р±РѕС‚Сѓ РїРѕРёСЃРєР° Сѓ РІР°СЃ?",
        "Р•СЃС‚СЊ Р»Рё С‚РµС…РЅРёС‡РµСЃРєРёРµ Р±Р»РѕРєРµСЂС‹?",
    ],
    "SMB": [
        "РљР°Рє РёРґСѓС‚ РїСЂРѕРґР°Р¶Рё РІ С†РµР»РѕРј?",
        "Р’Р»РёСЏРµС‚ Р»Рё РїРѕРёСЃРє РЅР° РєРѕРЅРІРµСЂСЃРёСЋ Р·Р°РјРµС‚РЅРѕ?",
        "РџР»Р°РЅРёСЂСѓРµС‚Рµ Р»Рё СЂРѕСЃС‚ Р°СЃСЃРѕСЂС‚РёРјРµРЅС‚Р°?",
        "РќСѓР¶РЅР° Р»Рё РїРѕРјРѕС‰СЊ СЃ РЅР°СЃС‚СЂРѕР№РєРѕР№?",
    ],
    "SS": [
        "Р’СЃС‘ Р»Рё СЂР°Р±РѕС‚Р°РµС‚ РєР°Рє РѕР¶РёРґР°Р»РѕСЃСЊ?",
        "Р•СЃС‚СЊ Р»Рё РІРѕРїСЂРѕСЃС‹ РїРѕ С„СѓРЅРєС†РёРѕРЅР°Р»Сѓ?",
        "РџР»Р°РЅРёСЂСѓРµС‚Рµ Р»Рё РїРµСЂРµС…РѕРґ РЅР° Р±РѕР»РµРµ РІС‹СЃРѕРєРёР№ РїР»Р°РЅ?",
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


# в”Ђв”Ђ Р’РЅСѓС‚СЂРµРЅРЅРёРµ Р·Р°РґР°С‡Рё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђ РђРЅР°Р»РёС‚РёРєР° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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

        # Р’СЃС‚СЂРµС‡Рё РїРѕ РЅРµРґРµР»СЏРј Р·Р° РїРѕСЃР»РµРґРЅРёРµ 12 РЅРµРґРµР»СЊ
        meetings_weekly = conn.execute("""
            SELECT strftime('%Y-W%W', meeting_date) as week,
                   COUNT(*) as cnt,
                   SUM(CASE WHEN mood='positive' THEN 1 ELSE 0 END) as positive,
                   SUM(CASE WHEN mood='risk' THEN 1 ELSE 0 END) as risk
            FROM meetings
            WHERE meeting_date >= date('now', '-84 days')
            GROUP BY week ORDER BY week
        """).fetchall()

        # Р—Р°РґР°С‡Рё РїРѕ СЃС‚Р°С‚СѓСЃР°Рј
        task_stats = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status
        """).fetchall()

        # Р—Р°РґР°С‡Рё СЃРѕР·РґР°РЅРЅС‹Рµ vs Р·Р°РєСЂС‹С‚С‹Рµ Р·Р° РїРѕСЃР»РµРґРЅРёРµ 4 РЅРµРґРµР»Рё
        task_flow = conn.execute("""
            SELECT strftime('%Y-W%W', created_at) as week,
                   COUNT(*) as created,
                   SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done
            FROM tasks
            WHERE created_at >= date('now', '-28 days')
            GROUP BY week ORDER BY week
        """).fetchall()

        # Р§РµРєР°РїС‹: СЃРѕРѕС‚РІРµС‚СЃС‚РІРёРµ СЂРёС‚РјСѓ РїРѕ СЃРµРіРјРµРЅС‚Р°Рј
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

        # РўРѕРї РєР»РёРµРЅС‚РѕРІ РїРѕ РєРѕР»-РІСѓ РІСЃС‚СЂРµС‡ Р·Р° РєРІР°СЂС‚Р°Р»
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


# в”Ђв”Ђ API: РѕР±РЅРѕРІРёС‚СЊ РѕС†РµРЅРєСѓ РІСЂРµРјРµРЅРё Р·Р°РґР°С‡Рё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђ QBR РљР°Р»РµРЅРґР°СЂСЊ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.get("/qbr-calendar", response_class=HTMLResponse)
async def qbr_calendar_page(request: Request):
    user = get_user_or_redirect(request)
    if not user:
        return RedirectResponse("/login")

    qbr_meetings = get_qbr_calendar()
    upcoming = get_upcoming_meetings(days_ahead=30)

    # РљР»РёРµРЅС‚С‹ ENT Р±РµР· QBR Р·Р° РїРѕСЃР»РµРґРЅРёРµ 90 РґРЅРµР№
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


# в”Ђв”Ђ API: Р±С‹СЃС‚СЂРѕ Р·Р°РєСЂС‹С‚СЊ С‡РµРєР°Рї (СЃ prep СЃС‚СЂР°РЅРёС†С‹) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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

    # РЎРѕС…СЂР°РЅСЏРµРј РІСЃС‚СЂРµС‡Сѓ
    meeting_id = create_meeting(
        client_id=client_id,
        meeting_date=meeting_date,
        meeting_type="checkup",
        summary=summary,
        mood=mood,
        next_meeting=next_meeting,
    )

    # РћС†РµРЅРєР° РІСЃС‚СЂРµС‡Рё
    if rating and 1 <= rating <= 5:
        set_checkup_rating(meeting_id, rating)

    # РџР»Р°РЅРёСЂСѓРµРј СЃР»РµРґСѓСЋС‰СѓСЋ РІСЃС‚СЂРµС‡Сѓ
    if next_meeting:
        set_planned_meeting(client_id, next_meeting)

    # РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ СЃ Merchrules (РєСЂРµРґСЃС‹ РёР· РїСЂРѕС„РёР»СЏ)
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

    # РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ СЃ Airtable
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


# в”Ђв”Ђ API: Р·Р°РїР»Р°РЅРёСЂРѕРІР°С‚СЊ РґР°С‚Сѓ СЃР»РµРґСѓСЋС‰РµР№ РІСЃС‚СЂРµС‡Рё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/api/client/{client_id}/planned-meeting", response_class=JSONResponse)
async def api_planned_meeting(request: Request, client_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    planned_date = body.get("date") or None  # None вЂ” РѕС‡РёС‰Р°РµРј
    set_planned_meeting(client_id, planned_date)
    return {"ok": True}


# в”Ђв”Ђ API: РїСЂРѕР±СЂРѕСЃРёС‚СЊ Р·Р°РґР°С‡Рё РєР»РёРµРЅС‚Р° РІ Merchrules в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
        return {"ok": False, "error": "РЈ РєР»РёРµРЅС‚Р° РЅРµС‚ site_ids"}

    open_tasks = get_client_tasks(client_id, "open")
    aq_tasks = [t for t in open_tasks if t["owner"] == "anyquery" and not t.get("is_internal")]
    if not aq_tasks:
        return {"ok": True, "count": 0, "note": "РќРµС‚ РѕС‚РєСЂС‹С‚С‹С… Р·Р°РґР°С‡ AnyQuery"}

    mr_login, mr_password = get_user_mr_creds(user)
    if not mr_login or not mr_password:
        return {"ok": False, "error": "РЈРєР°Р¶Рё Р»РѕРіРёРЅ Рё РїР°СЂРѕР»СЊ Merchrules РІ СЂР°Р·РґРµР»Рµ РџСЂРѕС„РёР»СЊ"}

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


# в”Ђв”Ђ API: РґРѕР±Р°РІРёС‚СЊ РєРѕРјРјРµРЅС‚Р°СЂРёР№ Рє РІСЃС‚СЂРµС‡Рµ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@app.post("/api/meeting/{meeting_id}/comment", response_class=JSONResponse)
async def api_meeting_comment(request: Request, meeting_id: int):
    user = get_user_or_redirect(request)
    if not user:
        raise HTTPException(401)

    body = await request.json()
    comment = (body.get("comment") or "").strip()
    if not comment:
        return {"ok": False, "error": "РџСѓСЃС‚РѕР№ РєРѕРјРјРµРЅС‚Р°СЂРёР№"}

    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404)

    # Р”РѕРїРёСЃС‹РІР°РµРј Рє СЃСѓС‰РµСЃС‚РІСѓСЋС‰РµРјСѓ summary
    existing = meeting.get("summary") or ""
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    new_summary = (existing + f"\n\n[{timestamp}] {comment}").strip()

    import sqlite3 as _sqlite3
    from pathlib import Path as _Path
    db_path = _Path("data/am_hub.db")
    with _sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE meetings SET summary=? WHERE id=?", (new_summary, meeting_id))

    return {"ok": True}

@app.get("/workspace")
async def get_workspace():
    return templates.TemplateResponse("workspace.html", {"request": request})

