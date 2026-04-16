"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional, List
from datetime import datetime, timedelta
import os
import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Cookie, Form, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote,
)
from auth import (
    authenticate_user, create_user, create_access_token,
    verify_password, hash_password, log_audit, decode_access_token,
)
from error_handlers import log_error, handle_db_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

@router.get("/api/integrations/test/merchrules")
async def test_merchrules(login: str = "", password: str = ""):
    if not login or not password:
        return {"error": "Need login and password"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post(
                "https://merchrules-qa.any-platform.ru/backend-v2/auth/login",
                json={"username": login, "password": password},
            )
        if resp.status_code == 200:
            return {"ok": True}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}



@router.get("/api/integrations/test/ktalk")
async def test_ktalk(space: str = "", token: str = ""):
    if not space or not token:
        return {"error": "Need space and token"}
    import httpx
    try:
        base = f"https://{space}.ktalk.ru"
        async with httpx.AsyncClient(timeout=10) as hx:
            resp = await hx.get(f"{base}/api/v1/spaces/{space}/users",
                headers={"Content-Type": "application/json", "X-Auth-Token": token},
                params={"limit": 1})
        if resp.status_code == 200:
            return {"ok": True, "space": space}
        return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}



@router.post("/webhook/telegram")
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

@router.post("/api/ktalk/notify")
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
    webhook_url = env.KTALK_WEBHOOK
    if not webhook_url:
        return {"error": "KTALK_WEBHOOK_URL not set"}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            await hx.post(webhook_url, json={"text": data.get("text", "")})
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}



@router.post("/api/ktalk/followup")
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

    webhook_url = env.KTALK_WEBHOOK
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
# API: MERCHRULES SYNC

@router.get("/api/ktalk/calendar")
async def api_ktalk_calendar(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    days: int = 7,
):
    """Получить встречи из KTalk календаря."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    settings = user.settings or {}
    kt = settings.get("ktalk", {})
    login_id = kt.get("login", "")
    password = kt.get("password", "")
    if not login_id or not password:
        return {"error": "Укажи логин/пароль KTalk в Настройках"}

    import ktalk
    return await ktalk.get_today_meetings(login_id, password)



@router.get("/api/tbank/tickets/{client_name}")
async def api_tbank_tickets(client_name: str, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить тикеты Tbank Time для клиента."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    u_settings = (user.settings or {}) if user else {}
    tm = u_settings.get("tbank_time", {})

    # Приоритет: user.settings → env
    time_token = (tm.get("session_cookie") or tm.get("api_token")
                  or env.TIME_TOKEN)

    if not time_token:
        return {"error": "Настройте доступ к Tbank Time в Настройках → Аккаунты", "tickets": []}

    from integrations.tbank_time import sync_tickets_for_client
    try:
        result = await sync_tickets_for_client(client_name, token=time_token)
        return result
    except Exception as e:
        return {"error": str(e), "open_count": 0, "total_count": 0, "last_ticket": None}



@router.get("/api/tbank/tickets")
async def api_tbank_all_tickets(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить все открытые тикеты."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    time_token = env.TIME_TOKEN
    if not time_token:
        return {"error": "TIME_API_TOKEN не настроен", "tickets": []}

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
# ANALYTICS API

@router.post("/api/ktalk/send-dm")
async def api_ktalk_send_dm(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Отправить DM клиенту через KTalk."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    body = await request.json()
    client_id = body.get("client_id")
    message   = body.get("message", "")
    channel_id = body.get("channel_id")

    if not message:
        return {"ok": False, "error": "Нет текста сообщения"}

    u_settings = user.settings or {}
    kt = u_settings.get("ktalk", {})
    token = kt.get("access_token") or _env("KTALK_API_TOKEN")
    if not token:
        return {"ok": False, "error": "KTalk не настроен — войдите в Настройки → KTalk"}

    # Получаем channel_id если не передан
    if not channel_id and client_id:
        client = db.query(Client).filter(Client.id == client_id).first()
        if client:
            meta = client.integration_metadata or {}
            channel_id = meta.get("ktalk_channel_id") or kt.get("followup_channel_id")

    if not channel_id:
        return {"ok": False, "error": "Нет channel_id для отправки"}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as hx:
            r = await hx.post(
                "https://tbank.ktalk.ru/api/v4/posts",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"channel_id": channel_id, "message": message}
            )
        if r.status_code in (200, 201):
            return {"ok": True}
        return {"ok": False, "error": f"KTalk HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Integration tests ────────────────────────────────────────────────────────

@router.get("/api/integrations/test/airtable")
async def api_test_airtable(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    u = user.settings or {}
    at = u.get("airtable", {})
    token = at.get("pat") or at.get("token") or _env("AIRTABLE_TOKEN")
    if not token:
        return {"ok": False, "error": "Airtable токен не настроен"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as hx:
            r = await hx.get("https://api.airtable.com/v0/meta/bases",
                             headers={"Authorization": f"Bearer {token}"})
        return {"ok": r.status_code == 200, "error": f"HTTP {r.status_code}" if r.status_code != 200 else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}



@router.get("/api/integrations/test/tbank_time")

@router.get("/api/integrations/test/tbank")
async def api_test_tbank(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    u = user.settings or {}
    tm = u.get("tbank_time", {})
    token = tm.get("session_cookie") or tm.get("mmauthtoken") or tm.get("api_token") or _env("TIME_API_TOKEN")
    if not token:
        return {"ok": False, "error": "Tbank Time токен не настроен"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as hx:
            r = await hx.get("https://time.tbank.ru/api/v4/users/me",
                             headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            me = r.json()
            return {"ok": True, "username": me.get("username"), "email": me.get("email")}
        return {"ok": False, "error": f"HTTP {r.status_code} — токен истёк?"}
    except Exception as e:
        return {"ok": False, "error": str(e)}



@router.get("/api/integrations/test/{system}")
async def api_test_integration_generic(system: str, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Заглушка для остальных систем."""
    return {"ok": False, "error": f"Тест для {system} не реализован"}


# ── Import CSV ───────────────────────────────────────────────────────────────

@router.get("/api/integrations/test/outlook")
async def api_test_outlook():
    """Тест подключения к Outlook."""
    from integrations.outlook import test_connection
    result = await test_connection()
    return result


# ============================================================================
# KPI МЕНЕДЖЕРА

@router.post("/webhook/airtable")
async def airtable_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Webhook от Airtable Automations.
    Airtable → Automations → Webhook → этот endpoint.
    При изменении записи обновляем клиента в локальной БД.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid json"}

    # Airtable присылает {record_id, fields: {...}}
    record_id = payload.get("record_id") or payload.get("id")
    fields = payload.get("fields") or payload.get("data") or {}

    if not record_id:
        return {"ok": False, "error": "no record_id"}

    client = db.query(Client).filter(Client.airtable_record_id == record_id).first()
    if not client:
        # Пробуем создать нового клиента
        name = fields.get("Название") or fields.get("Name") or fields.get("Клиент") or fields.get("Company")
        if name:
            client = Client(
                airtable_record_id=record_id,
                name=str(name),
                segment=str(fields.get("Сегмент") or fields.get("Segment") or ""),
                manager_email=str(fields.get("Менеджер") or fields.get("Manager Email") or ""),
            )
            db.add(client)
            db.commit()
            logger.info(f"✅ Airtable webhook: created client {name}")
            return {"ok": True, "action": "created", "client_id": client.id}
        return {"ok": False, "error": "client not found and no name field"}

    # Обновляем поля
    field_map = {
        "Название": "name", "Name": "name", "Клиент": "name", "Company": "name",
        "Сегмент": "segment", "Segment": "segment",
        "Домен": "domain", "Domain": "domain",
        "Менеджер": "manager_email", "Manager Email": "manager_email",
        "Health Score": "health_score",
    }
    updated = []
    for at_field, model_field in field_map.items():
        if at_field in fields and fields[at_field] is not None:
            val = fields[at_field]
            if model_field == "health_score":
                try:
                    val = float(val)
                    if val > 1:
                        val = val / 100
                except Exception:
                    continue
            setattr(client, model_field, val)
            updated.append(model_field)

    if updated:
        from sqlalchemy.orm.attributes import flag_modified
        db.commit()
        logger.info(f"✅ Airtable webhook: updated {client.name} fields={updated}")

    return {"ok": True, "action": "updated", "fields": updated}


# ============================================================================
# GOOGLE SHEETS — запись обратно

@router.get("/api/integrations/status")
async def api_integrations_status(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Статус всех интеграций для текущего менеджера.
    Проверяет наличие кредов в user.settings и последний синк.
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    s = user.settings or {}
    mr = s.get("merchrules", {})
    kt = s.get("ktalk", {})
    at = s.get("airtable", {})
    tg = s.get("telegram", {})
    tm = s.get("tbank_time", {})
    gs = s.get("sheets", {})

    def last_sync(integration: str) -> str:
        log = db.query(SyncLog).filter(
            SyncLog.integration == integration,
            SyncLog.status == "success",
        ).order_by(SyncLog.started_at.desc()).first()
        if not log or not log.started_at:
            return None
        ago = int((datetime.now() - log.started_at).total_seconds())
        if ago < 60: return "только что"
        if ago < 3600: return f"{ago//60} мин назад"
        if ago < 86400: return f"{ago//3600} ч назад"
        return f"{ago//86400} дн назад"

    return {
        "merchrules": {
            "configured": bool(mr.get("login") and mr.get("password")),
            "login": mr.get("login", ""),
            "last_sync": last_sync("merchrules"),
        },
        "ktalk": {
            "configured": bool(kt.get("access_token")),
            "login": kt.get("login", ""),
            "channel_id": kt.get("followup_channel_id", ""),
            "last_sync": last_sync("ktalk"),
        },
        "airtable": {
            "configured": bool(at.get("pat") or at.get("token")),
            "base_id": at.get("base_id", ""),
            "last_sync": last_sync("airtable"),
        },
        "tbank_time": {
            "configured": bool(tm.get("login") or tm.get("session_cookie")),
            "login": tm.get("login", ""),
        },
        "sheets": {
            "configured": bool(gs.get("spreadsheet_id") or _env("SHEETS_SPREADSHEET_ID")),
            "spreadsheet_id": gs.get("spreadsheet_id") or _env("SHEETS_SPREADSHEET_ID"),
        },
        "telegram": {
            "configured": bool(user.telegram_id or tg.get("chat_id")),
            "telegram_id": user.telegram_id,
        },
        "groq_ai": {
            "configured": bool(_env("GROQ_API_KEY") or _env("QWEN_API_KEY")),
        },
    }



@router.post("/api/integrations/test/{service}")
async def api_test_integration(
    service: str,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Проверить подключение конкретного сервиса с персональными кредами.
    service: merchrules | ktalk | airtable | tbank_time | sheets
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    s = user.settings or {}
    import httpx

    if service == "merchrules":
        mr = s.get("merchrules", {})
        login = mr.get("login", "") or _env("MERCHRULES_LOGIN")
        password = mr.get("password", "") or _env("MERCHRULES_PASSWORD")
        if not login or not password:
            return {"ok": False, "error": "Логин и пароль не заданы"}
        base = _env("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
        try:
            async with httpx.AsyncClient(timeout=15) as hx:
                for field in ("email", "login", "username"):
                    r = await hx.post(f"{base}/backend-v2/auth/login",
                                      json={field: login, "password": password}, timeout=10)
                    if r.status_code == 200:
                        body = r.json()
                        token = body.get("token") or body.get("access_token") or body.get("accessToken")
                        if token:
                            # Считаем клиентов
                            ra = await hx.get(f"{base}/backend-v2/accounts?limit=1",
                                              headers={"Authorization": f"Bearer {token}"}, timeout=10)
                            count = len(ra.json().get("accounts", ra.json().get("items", []))) if ra.status_code == 200 else "?"
                            return {"ok": True, "message": f"✅ Подключено ({field}={login})", "accounts": count}
            return {"ok": False, "error": "Неверный логин или пароль"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif service == "ktalk":
        kt = s.get("ktalk", {})
        token = kt.get("access_token", "")
        space = kt.get("space", "") or _env("KTALK_SPACE")
        if not token or not space:
            return {"ok": False, "error": "Нет токена — войдите через /auth/ktalk"}
        try:
            async with httpx.AsyncClient(timeout=10) as hx:
                r = await hx.get(f"https://{space}.ktalk.ru/api/v4/users/me",
                                  headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                me = r.json()
                return {"ok": True, "message": f"✅ {me.get('username', space)}", "email": me.get("email")}
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif service == "airtable":
        at = s.get("airtable", {})
        token = at.get("pat") or at.get("token") or _env("AIRTABLE_TOKEN")
        base_id = at.get("base_id") or _env("AIRTABLE_BASE_ID")
        if not token:
            return {"ok": False, "error": "Токен не задан"}
        try:
            async with httpx.AsyncClient(timeout=10) as hx:
                url = f"https://api.airtable.com/v0/meta/bases/{base_id}/tables" if base_id else "https://api.airtable.com/v0/meta/bases"
                r = await hx.get(url, headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                d = r.json()
                count = len(d.get("tables", d.get("bases", [])))
                return {"ok": True, "message": f"✅ Airtable подключён ({count} объектов)"}
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:100]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif service == "tbank_time":
        tm = s.get("tbank_time", {})
        login = tm.get("login", "")
        password = tm.get("password", "")
        session = tm.get("session_cookie", "") or _env("TIME_SESSION_COOKIE")
        if not login and not session:
            return {"ok": False, "error": "Логин или session cookie не заданы"}
        try:
            async with httpx.AsyncClient(timeout=10) as hx:
                headers = {}
                if session:
                    headers["Cookie"] = f"MMAUTH={session}"
                r = await hx.get("https://time.tbank.ru/api/v1/users/me", headers=headers)
            if r.status_code == 200:
                me = r.json()
                return {"ok": True, "message": f"✅ {me.get('username', login)}"}
            return {"ok": False, "error": f"HTTP {r.status_code} — проверьте session cookie"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif service == "sheets":
        gs = s.get("sheets", {})
        sheet_id = _extract_sheets_id(gs.get("spreadsheet_id") or _env("SHEETS_SPREADSHEET_ID"))
        if not sheet_id:
            return {"ok": False, "error": "Spreadsheet ID не задан"}
        try:
            from sheets import fetch_sheet_csv
            rows = await fetch_sheet_csv(sheet_id)
            return {"ok": bool(rows), "message": f"✅ Таблица доступна ({len(rows)} строк)" if rows else "❌ Таблица пустая или недоступна"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"Неизвестный сервис: {service}"}

# ============================================================================
# CHROME EXTENSION — push токенов Time и Ktalk

