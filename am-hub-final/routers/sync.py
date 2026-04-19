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
    get_current_user,)
from error_handlers import log_error, handle_db_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

@router.post("/api/sync/extension")
async def api_sync_extension(request: Request, db: Session = Depends(get_db)):
    """
    Приём данных синхронизации от Chrome-расширения AM Hub Sync.
    Авторизация через Bearer токен (JWT) в заголовке Authorization.
    """
    # Авторизация через Bearer header (расширение не использует cookie)
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token required")

    from auth import decode_access_token
    payload_jwt = decode_access_token(token)
    if not payload_jwt:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == int(payload_jwt.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    data = await request.json()
    accounts = data.get("accounts", [])

    if not accounts:
        return {"ok": False, "error": "No accounts in payload"}

    clients_synced = 0
    tasks_synced = 0
    meetings_synced = 0

    for acc in accounts:
        site_id = str(acc.get("id", "")).strip()
        if not site_id:
            continue

        # Найти или создать клиента
        client = db.query(Client).filter(Client.merchrules_account_id == site_id).first()
        if not client:
            client = Client(
                merchrules_account_id=site_id,
                name=acc.get("name") or f"Site {site_id}",
                manager_email=user.email,
                segment=acc.get("segment") or "SMB",
                domain=acc.get("domain"),
            )
            db.add(client)
            db.flush()
        else:
            # Обновляем имя и сегмент если пришли
            if acc.get("name"):
                client.name = acc["name"]
            if acc.get("segment"):
                client.segment = acc["segment"]
            if acc.get("domain"):
                client.domain = acc["domain"]
            if acc.get("health_score") is not None:
                client.health_score = float(acc["health_score"])

        # Гарантируем привязку к менеджеру
        if not client.manager_email:
            client.manager_email = user.email

        clients_synced += 1

        # Задачи
        for t in acc.get("tasks", []):
            mr_task_id = str(t.get("id", "")).strip()
            if not mr_task_id:
                continue
            existing = db.query(Task).filter(Task.merchrules_task_id == mr_task_id).first()
            if existing:
                # Обновляем статус
                existing.status = t.get("status", existing.status)
                existing.priority = t.get("priority", existing.priority)
            else:
                due = None
                if t.get("due_date"):
                    try:
                        due = datetime.fromisoformat(str(t["due_date"])[:19])
                    except Exception:
                        pass
                db.add(Task(
                    client_id=client.id,
                    merchrules_task_id=mr_task_id,
                    title=t.get("title") or "",
                    status=t.get("status") or "plan",
                    priority=t.get("priority") or "medium",
                    source="roadmap",
                    due_date=due,
                    team=t.get("team"),
                    task_type=t.get("task_type"),
                ))
                tasks_synced += 1

        # Встречи
        for m in acc.get("meetings", []):
            mr_meeting_id = str(m.get("id", "")).strip()
            if not mr_meeting_id:
                continue
            ext_id = f"mr_{mr_meeting_id}"
            existing = db.query(Meeting).filter(Meeting.external_id == ext_id).first()
            if not existing:
                meeting_date = None
                raw_date = m.get("date")
                if raw_date:
                    try:
                        meeting_date = datetime.fromisoformat(str(raw_date)[:19])
                    except Exception:
                        pass
                if meeting_date:
                    db.add(Meeting(
                        client_id=client.id,
                        date=meeting_date,
                        type=m.get("type") or "meeting",
                        title=m.get("title"),
                        summary=m.get("summary"),
                        source="merchrules",
                        external_id=ext_id,
                        followup_status="pending",
                    ))
                    meetings_synced += 1
                    # Обновляем last_meeting_date на клиенте
                    if not client.last_meeting_date or meeting_date > client.last_meeting_date:
                        client.last_meeting_date = meeting_date

        # Метрики
        metrics = acc.get("metrics")
        if metrics and isinstance(metrics, dict):
            hs = metrics.get("health_score") or metrics.get("healthScore")
            if hs is not None:
                client.health_score = float(hs)

    db.commit()

    # Логируем
    db.add(SyncLog(
        integration="extension",
        resource_type="accounts",
        action="push",
        status="success",
        records_processed=clients_synced,
        sync_data={"tasks": tasks_synced, "meetings": meetings_synced},
    ))
    db.commit()

    logger.info(f"Extension sync: {clients_synced} clients, {tasks_synced} tasks, {meetings_synced} meetings (user={user.email})")
    return {
        "ok": True,
        "clients_synced": clients_synced,
        "tasks_synced": tasks_synced,
        "meetings_synced": meetings_synced,
    }



@router.post("/api/sync/airtable")
async def api_sync_airtable(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Синхронизация клиентов из Airtable → локальная БД.
    Auth: cookie JWT / Bearer JWT / Bearer amh_* (для расширения)."""
    from routers.api_tokens import resolve_user
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401)

    from airtable_sync import sync_clients_from_airtable
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Приоритет: body → user.settings → env
    u_settings = user.settings or {}
    at_settings = u_settings.get("airtable", {})
    token = (body.get("token")
             or at_settings.get("pat") or at_settings.get("token")
             or _env("AIRTABLE_TOKEN") or _env("AIRTABLE_PAT"))
    base_id = (body.get("base_id")
               or at_settings.get("base_id")
               or _env("AIRTABLE_BASE_ID"))
    view_id = body.get("view_id") or at_settings.get("view_id", "")

    if not token:
        return {"error": "Нет токена Airtable. Укажите в Настройках → Аккаунты."}

    # reset=true → сначала обнуляем manager_email у всех клиентов,
    # привязанных к текущему пользователю. Потом sync переприсваивает
    # их по CSM-email из Airtable. Так уходят «фантомные» 4 клиента
    # (user: 'у меня 37, показывает 41 — откуда?').
    if body.get("reset"):
        n = db.query(Client).filter(Client.manager_email == user.email).update(
            {"manager_email": None}, synchronize_session=False
        )
        db.commit()
        logger.info(f"Reset manager_email for {n} clients of {user.email}")

    result = await sync_clients_from_airtable(
        db=db,
        token=token,
        base_id=base_id or None,
        view_id=view_id,
        # НЕ передаём default_manager_email — иначе клиенты без CSM в
        # Airtable снова улетят на текущего юзера.
    )
    return result



@router.post("/api/sync/merchrules")
async def api_sync_merchrules(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Синхронизация с Merchrules — пробует QA и Production.
    Авторизация: cookie JWT, Bearer JWT, или Bearer amh_* (для расширения)."""
    from routers.api_tokens import resolve_user
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401)

    try:
        body = await request.json()
    except Exception:
        body = {}
    settings = user.settings or {}
    mr = settings.get("merchrules", {})
    login = body.get("login") or mr.get("login") or _env("MERCHRULES_LOGIN")
    password = body.get("password") or mr.get("password") or _env("MERCHRULES_PASSWORD")
    site_ids_input = body.get("site_ids") or mr.get("site_ids") or settings.get("merchrules_site_ids", [])

    if not login or not password:
        return {"error": "Нужны креды Merchrules"}

    # Нет site_ids? → пытаемся подтянуть Airtable сначала, затем берём
    # merchrules_account_id всех клиентов этого менеджера.
    if not site_ids_input:
        airtable_token = (settings.get("airtable") or {}).get("pat") or (settings.get("airtable") or {}).get("token") or _env("AIRTABLE_TOKEN") or _env("AIRTABLE_PAT")
        airtable_base  = (settings.get("airtable") or {}).get("base_id") or _env("AIRTABLE_BASE_ID")
        if airtable_token:
            try:
                from airtable_sync import sync_clients_from_airtable
                await sync_clients_from_airtable(
                    db=db, token=airtable_token, base_id=airtable_base,
                    default_manager_email=user.email,
                )
            except Exception as _at:
                logger.warning(f"Pre-sync Airtable fail: {_at}")
        # Собираем site_ids из клиентов этого менеджера
        q = db.query(Client.merchrules_account_id).filter(
            Client.manager_email == user.email,
            Client.merchrules_account_id.isnot(None),
        )
        site_ids_input = [row[0] for row in q.all() if row[0]]
        logger.info(f"Auto-derived {len(site_ids_input)} site_ids from Airtable for {user.email}")

    # Сохраняем креды и site_ids
    mr["login"] = login
    mr["password"] = password
    if site_ids_input:
        settings["merchrules_site_ids"] = site_ids_input
    settings["merchrules"] = mr
    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()

    # Session-cookie auth (как legacy app.py). Merchrules НЕ отдаёт Bearer —
    # ставит Set-Cookie. httpx.AsyncClient сохраняет cookies между запросами
    # (не закрываем пока не закончили использовать).
    import httpx
    base_url = _env("MERCHRULES_API_URL", "https://merchrules.any-platform.ru").rstrip("/")
    attempts_log = []

    hx = httpx.AsyncClient(timeout=30, follow_redirects=True)
    login_url = f"{base_url}/backend-v2/auth/login"
    auth_ok = False
    resp = None
    for field_name in ("username", "email", "login"):
        for mode in ("json", "form"):
            try:
                if mode == "form":
                    resp = await hx.post(login_url, data={field_name: login, "password": password}, timeout=15)
                else:
                    resp = await hx.post(login_url, json={field_name: login, "password": password}, timeout=15)
                attempts_log.append(f"{field_name}/{mode} → HTTP {resp.status_code}")
                if resp.status_code in (200, 201, 204):
                    auth_ok = True
                    break
            except Exception as e:
                attempts_log.append(f"{field_name}/{mode} → EXC {e}")
        if auth_ok:
            break

    if not auth_ok:
        await hx.aclose()
        return {"error": f"Ошибка авторизации Merchrules. Попытки: {' | '.join(attempts_log[-4:])}"}
    if not hx.cookies:
        await hx.aclose()
        return {"error": f"Login HTTP {resp.status_code} без Set-Cookie — session не установлена. {attempts_log[-1]}"}

    logger.info(f"✅ Merchrules session cookies: {list(hx.cookies.keys())}")
    headers = {"Accept": "application/json"}  # без Bearer — сессия через cookies

    synced_clients = 0
    synced_tasks = 0

    try:
        # Если указаны site_id — используем их
        if site_ids_input:
            for sid in site_ids_input:
                sid = str(sid).strip()
                if not sid:
                    continue
                c = db.query(Client).filter(Client.merchrules_account_id == sid).first()
                if not c:
                    c = Client(merchrules_account_id=sid, name=f"Site {sid}", manager_email=user.email, segment="SMB")
                    db.add(c)
                    db.flush()

                # Tasks
                try:
                    r_tasks = await hx.get(f"{base_url}/backend-v2/tasks", params={"site_id": sid, "limit": 50}, headers=headers, timeout=15)
                    if r_tasks.status_code == 200:
                        tasks_data = r_tasks.json()
                        tasks_list = tasks_data.get("tasks") or tasks_data.get("items") or []
                        for t in tasks_list[:20]:
                            existing = db.query(Task).filter(Task.merchrules_task_id == str(t.get("id"))).first()
                            if not existing:
                                db.add(Task(client_id=c.id, merchrules_task_id=str(t.get("id")),
                                    title=t.get("title",""), status=t.get("status","plan"),
                                    priority=t.get("priority","medium"), source="roadmap"))
                                synced_tasks += 1
                except Exception as e:
                    logger.warning(f"Failed to fetch tasks for {sid}: {e}")

                # Meetings
                try:
                    r_meetings = await hx.get(f"{base_url}/backend-v2/meetings", params={"site_id": sid, "limit": 10}, headers=headers, timeout=15)
                    if r_meetings.status_code == 200:
                        meetings_data = r_meetings.json()
                        meetings_list = meetings_data.get("meetings") or meetings_data.get("items") or []
                        if meetings_list:
                            last_mtg = max(meetings_list, key=lambda m: m.get("date", ""))
                            try:
                                c.last_meeting_date = datetime.fromisoformat(last_mtg.get("date", "")[:19])
                            except Exception as e:
                                logger.debug(f"Ignored error: {e}")
                except Exception as e:
                    logger.warning(f"Failed to fetch meetings for {sid}: {e}")

                synced_clients += 1
        else:
            # Без site_ids — получаем все аккаунты менеджера
            accounts = []
            accounts_endpoint_log = []

            # Пробуем несколько возможных endpoint'ов
            for ep in [
                f"{base_url}/backend-v2/accounts",
                f"{base_url}/backend-v2/sites",
                f"{base_url}/backend-v2/accounts?limit=500",
                f"{base_url}/backend-v2/sites?limit=500",
            ]:
                try:
                    r = await hx.get(ep, headers=headers, timeout=20)
                    accounts_endpoint_log.append(f"{ep} → {r.status_code}")
                    if r.status_code == 200:
                        data = r.json()
                        # Пробуем разные ключи в ответе
                        for key in ("accounts", "sites", "items", "data", "results"):
                            if isinstance(data.get(key), list) and data[key]:
                                accounts = data[key]
                                break
                        # Если ответ сам список
                        if not accounts and isinstance(data, list):
                            accounts = data
                        if accounts:
                            logger.info(f"✅ Accounts from {ep}: {len(accounts)}")
                            break
                except Exception as e:
                    accounts_endpoint_log.append(f"{ep} → error: {e}")

            if not accounts:
                return {
                    "error": "Не удалось получить список аккаунтов. Попробуйте указать Site ID вручную.",
                    "endpoints_tried": accounts_endpoint_log,
                    "hint": "Укажите site_id через запятую в поле Site ID, например: 2262, 5335, 8049"
                }

            logger.info(f"Syncing {len(accounts)} accounts for {user.email}")

            for acc in accounts:
                aid = acc.get("id") or acc.get("site_id") or acc.get("siteId")
                if not aid:
                    continue
                site_id = str(aid)

                # Название аккаунта — пробуем разные поля
                acc_name = (
                    acc.get("name") or acc.get("title") or
                    acc.get("company") or acc.get("domain") or
                    f"Account {site_id}"
                )

                # Сегмент если есть
                acc_segment = acc.get("segment") or acc.get("tariff") or acc.get("plan") or None

                c = db.query(Client).filter(Client.merchrules_account_id == site_id).first()
                if not c:
                    c = Client(
                        merchrules_account_id=site_id,
                        name=acc_name,
                        manager_email=user.email,
                        segment=acc_segment,
                    )
                    db.add(c)
                    db.flush()
                else:
                    # Обновляем имя и менеджера
                    c.name = acc_name
                    if not c.manager_email:
                        c.manager_email = user.email
                    if acc_segment and not c.segment:
                        c.segment = acc_segment

                # Tasks
                try:
                    r_tasks = await hx.get(
                        f"{base_url}/backend-v2/tasks",
                        params={"site_id": site_id, "limit": 100},
                        headers=headers, timeout=15,
                    )
                    if r_tasks.status_code == 200:
                        td = r_tasks.json()
                        tasks_list = td.get("tasks") or td.get("items") or (td if isinstance(td, list) else [])
                        for t in tasks_list:
                            tid = str(t.get("id", ""))
                            if not tid:
                                continue
                            existing = db.query(Task).filter(Task.merchrules_task_id == tid).first()
                            if not existing:
                                db.add(Task(
                                    client_id=c.id,
                                    merchrules_task_id=tid,
                                    title=t.get("title") or t.get("name") or "",
                                    status=t.get("status", "plan"),
                                    priority=t.get("priority", "medium"),
                                    source="roadmap",
                                    team=t.get("team") or t.get("assignee") or None,
                                ))
                                synced_tasks += 1
                            else:
                                # Обновляем статус
                                existing.status = t.get("status", existing.status)
                except Exception as e:
                    logger.warning(f"Tasks fetch failed for site {site_id}: {e}")

                # Meetings — последняя дата
                try:
                    r_meetings = await hx.get(
                        f"{base_url}/backend-v2/meetings",
                        params={"site_id": site_id, "limit": 10},
                        headers=headers, timeout=15,
                    )
                    if r_meetings.status_code == 200:
                        md = r_meetings.json()
                        meetings_list = md.get("meetings") or md.get("items") or (md if isinstance(md, list) else [])
                        dates = []
                        for m in meetings_list:
                            d = m.get("date") or m.get("meeting_date") or m.get("createdAt", "")
                            if d:
                                dates.append(str(d)[:19])
                        if dates:
                            try:
                                c.last_meeting_date = datetime.fromisoformat(max(dates))
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning(f"Meetings fetch failed for site {site_id}: {e}")

                synced_clients += 1

        db.commit()
        return {
            "ok": True,
            "clients_synced": synced_clients,
            "tasks_synced": synced_tasks,
            "base_url": base_url,
            "message": f"Синхронизировано: {synced_clients} клиентов, {synced_tasks} задач",
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Merchrules sync error: {e}")
        return {"error": str(e)}
    finally:
        try: await hx.aclose()
        except Exception: pass



@router.get("/api/sync/merchrules-creds")
async def api_get_mr_creds(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить сохранённые креды Merchrules пользователя."""
    if not auth_token:
        return {}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {}
    settings = user.settings or {}
    mr = settings.get("merchrules", {})
    return {"login": mr.get("login", ""), "site_ids": settings.get("merchrules_site_ids", [])}


# ============================================================================
# API: TASK CRUD

@router.get("/api/sync/status")
async def api_sync_status(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Статус последней синхронизации по каждой интеграции."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    integrations = ["merchrules", "airtable", "meetings_slots", "system"]
    result = {}
    now = datetime.now()

    for integration in integrations:
        last = db.query(SyncLog).filter(
            SyncLog.integration == integration,
        ).order_by(SyncLog.started_at.desc()).first()

        if last:
            ago_sec = int((now - last.started_at).total_seconds()) if last.started_at else None
            if ago_sec is not None:
                if ago_sec < 60:
                    ago_str = "только что"
                elif ago_sec < 3600:
                    ago_str = f"{ago_sec // 60} мин назад"
                elif ago_sec < 86400:
                    ago_str = f"{ago_sec // 3600} ч назад"
                else:
                    ago_str = f"{ago_sec // 86400} дн назад"
            else:
                ago_str = "—"

            result[integration] = {
                "status": last.status,
                "records": last.records_processed,
                "ago": ago_str,
                "at": last.started_at.strftime("%d.%m %H:%M") if last.started_at else "—",
                "error": last.message if last.status == "error" else None,
            }
        else:
            result[integration] = {"status": "never", "ago": "никогда", "records": 0}

    # Кол-во клиентов текущего менеджера
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients_count = q.count()

    return {"integrations": result, "clients_total": clients_count}


# ============================================================================
# DIAGNOSTICS & IMPORT

@router.post("/api/sheets/update-checkup")
async def api_sheets_update_checkup(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Записать статус чекапа обратно в Google Sheets (Top-50).
    Обновляет строку клиента: дата последнего чекапа, статус.
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    data = await request.json()
    client_name = data.get("client_name", "")
    checkup_date = data.get("checkup_date", datetime.now().strftime("%Y-%m-%d"))
    status = data.get("status", "done")

    if not client_name:
        return {"error": "client_name required"}

    try:
        from sheets import write_checkup_status
        result = await write_checkup_status(client_name, checkup_date, status)
        return {"ok": result, "client": client_name}
    except Exception as e:
        return {"error": str(e), "note": "Sheets write-back requires service account credentials"}



@router.post("/api/sheets/batch-update")
async def api_sheets_batch_update(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Массовое обновление данных в Google Sheets."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    data = await request.json()
    updates = data.get("updates", [])  # [{row, col, value}]

    try:
        from sheets import batch_update_cells
        result = await batch_update_cells(updates)
        return {"ok": result, "count": len(updates)}
    except Exception as e:
        return {"error": str(e)}

# ============================================================================
# ИНТЕГРАЦИИ: тест персональных кредов

