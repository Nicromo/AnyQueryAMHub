"""
Синхронизация данных из Merchrules Dashboard.
Получаем список открытых задач и встреч для каждого клиента.
Кэшируем результат на 30 минут.
"""
import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Настройки ─────────────────────────────────────────────────────────────────

MERCHRULES_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
CACHE_TTL_MINUTES = 30

# Кэши: ключ = login (чтобы разные менеджеры не мешали друг другу)
_auth_cache:  dict[str, dict] = {}  # login → {token, expires_at}
_data_cache:  dict[str, dict] = {}  # login → {data, updated_at}


def _default_creds() -> tuple[str, str]:
    return os.getenv("MERCHRULES_LOGIN", ""), os.getenv("MERCHRULES_PASSWORD", "")


# ── Авторизация ────────────────────────────────────────────────────────────────

async def get_auth_token(client: httpx.AsyncClient,
                          login: str = "", password: str = "") -> Optional[str]:
    """Получаем/обновляем токен авторизации для конкретного пользователя."""
    if not login:
        login, password = _default_creds()
    if not login or not password:
        logger.debug("MR creds not set — skipping sync")
        return None

    now = datetime.now()
    cached = _auth_cache.get(login, {})
    if cached.get("token") and cached.get("expires_at") and now < cached["expires_at"]:
        return cached["token"]

    try:
        resp = await client.post(
            f"{MERCHRULES_URL}/backend-v2/auth/login",
            json={"username": login, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            body = resp.json()
            token = body.get("token") or body.get("access_token") or body.get("accessToken")
            if token:
                _auth_cache[login] = {"token": token, "expires_at": now + timedelta(hours=1)}
                logger.info("Merchrules auth OK for %s", login)
                return token
        logger.warning("Merchrules auth failed (%s): %s %s", login, resp.status_code, resp.text[:150])
    except Exception as exc:
        logger.warning("Merchrules auth error (%s): %s", login, exc)

    return None


# ── Получение данных ───────────────────────────────────────────────────────────

async def fetch_site_tasks(
    client: httpx.AsyncClient, headers: dict, site_id: str
) -> dict:
    """Получаем открытые задачи для одного site_id."""
    result = {"open_tasks": 0, "blocked_tasks": 0, "overdue_tasks": 0, "tasks": []}
    try:
        resp = await client.get(
            f"{MERCHRULES_URL}/backend-v2/tasks",
            params={"site_id": site_id, "status": "plan,in_progress,blocked", "limit": 100},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            tasks = data if isinstance(data, list) else data.get("tasks") or data.get("items") or []
            today = datetime.today().date().isoformat()

            for t in tasks:
                result["tasks"].append({
                    "id": t.get("id"),
                    "title": t.get("title") or t.get("name") or "",
                    "status": t.get("status", ""),
                    "due_date": t.get("due_date") or t.get("dueDate") or "",
                    "priority": t.get("priority", ""),
                })
                status = str(t.get("status", "")).lower()
                if status == "blocked":
                    result["blocked_tasks"] += 1
                else:
                    result["open_tasks"] += 1

                due = t.get("due_date") or t.get("dueDate") or ""
                if due and due < today:
                    result["overdue_tasks"] += 1

    except Exception as exc:
        logger.warning("fetch_site_tasks(%s) error: %s", site_id, exc)

    return result


async def fetch_site_meetings(
    client: httpx.AsyncClient, headers: dict, site_id: str
) -> dict:
    """Получаем последние встречи для site_id."""
    result = {"last_meeting": None, "meetings_count": 0}
    try:
        resp = await client.get(
            f"{MERCHRULES_URL}/backend-v2/meetings",
            params={"site_id": site_id, "limit": 5},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            meetings = data if isinstance(data, list) else data.get("meetings") or data.get("items") or []
            result["meetings_count"] = len(meetings)
            if meetings:
                # Берём самую свежую встречу
                dates = [
                    m.get("date") or m.get("meeting_date") or m.get("createdAt", "")[:10]
                    for m in meetings
                    if m.get("date") or m.get("meeting_date") or m.get("createdAt")
                ]
                if dates:
                    result["last_meeting"] = max(dates)
    except Exception as exc:
        logger.warning("fetch_site_meetings(%s) error: %s", site_id, exc)

    return result


# ── Публичный API ─────────────────────────────────────────────────────────────

async def sync_clients_from_merchrules(clients: list[dict],
                                       login: str = "", password: str = "") -> dict:
    """
    Получаем данные из Merchrules для всех клиентов.
    login/password — кредсы конкретного менеджера (или env-fallback).
    Кэшируется на CACHE_TTL_MINUTES минут per-manager.
    """
    if not login:
        login, password = _default_creds()
    if not login or not password:
        return {}

    now = datetime.now()
    cached = _data_cache.get(login, {})
    if (
        cached.get("data") is not None
        and cached.get("updated_at") is not None
        and now - cached["updated_at"] < timedelta(minutes=CACHE_TTL_MINUTES)
    ):
        return cached["data"]

    result = {}

    async with httpx.AsyncClient(timeout=30) as hx:
        token = await get_auth_token(hx, login, password)
        if not token:
            return {}

        headers = {"Authorization": f"Bearer {token}"}

        # Собираем все уникальные site_ids из переданных клиентов
        site_ids = set()
        for c in clients:
            raw = c.get("site_ids") or ""
            for sid in raw.split(","):
                sid = sid.strip()
                if sid:
                    site_ids.add(sid)

        if not site_ids:
            return {}

        async def fetch_one(site_id: str):
            tasks_data    = await fetch_site_tasks(hx, headers, site_id)
            meetings_data = await fetch_site_meetings(hx, headers, site_id)
            return site_id, {**tasks_data, **meetings_data}

        done = await asyncio.gather(*[fetch_one(sid) for sid in site_ids], return_exceptions=True)

        for item in done:
            if isinstance(item, Exception):
                logger.warning("MR sync gather error: %s", item)
                continue
            site_id, data = item
            result[site_id] = data

    _data_cache[login] = {"data": result, "updated_at": now}
    logger.info("MR sync done for %s: %d sites", login, len(result))
    return result


def get_client_mr_data(mr_data: dict, site_ids_raw: str) -> dict:
    """
    Агрегирует данные Merchrules для клиента у которого может быть несколько site_ids.
    Возвращает суммарную статистику.
    """
    if not site_ids_raw or not mr_data:
        return {"open_tasks": 0, "blocked_tasks": 0, "overdue_tasks": 0, "last_meeting": None}

    total = {"open_tasks": 0, "blocked_tasks": 0, "overdue_tasks": 0, "last_meeting": None}
    all_dates = []

    for sid in site_ids_raw.split(","):
        sid = sid.strip()
        if not sid or sid not in mr_data:
            continue
        d = mr_data[sid]
        total["open_tasks"] += d.get("open_tasks", 0)
        total["blocked_tasks"] += d.get("blocked_tasks", 0)
        total["overdue_tasks"] += d.get("overdue_tasks", 0)
        if d.get("last_meeting"):
            all_dates.append(d["last_meeting"])

    if all_dates:
        total["last_meeting"] = max(all_dates)

    return total


def invalidate_cache(login: str = ""):
    """Сбрасываем кэш принудительно. Если login — только для него, иначе всё."""
    if login:
        _data_cache.pop(login, None)
        _auth_cache.pop(login, None)
    else:
        _data_cache.clear()
        _auth_cache.clear()
