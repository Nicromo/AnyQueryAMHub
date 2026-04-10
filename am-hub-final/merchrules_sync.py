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
MR_LOGIN = os.getenv("MERCHRULES_LOGIN", "")
MR_PASSWORD = os.getenv("MERCHRULES_PASSWORD", "")

# Кэш токена авторизации
_auth_cache: dict = {"token": None, "expires_at": None}

# Кэш данных по клиентам
_data_cache: dict = {"data": None, "updated_at": None}
CACHE_TTL_MINUTES = 30


# ── Авторизация ────────────────────────────────────────────────────────────────

async def get_auth_token(client: httpx.AsyncClient) -> Optional[str]:
    """Получаем/обновляем токен авторизации."""
    now = datetime.now()

    # Используем кэш если не истёк
    if (
        _auth_cache["token"]
        and _auth_cache["expires_at"]
        and now < _auth_cache["expires_at"]
    ):
        return _auth_cache["token"]

    if not MR_LOGIN or not MR_PASSWORD:
        logger.debug("MERCHRULES_LOGIN/PASSWORD not set — skipping MR sync")
        return None

    try:
        resp = await client.post(
            f"{MERCHRULES_URL}/backend-v2/auth/login",
            json={"username": MR_LOGIN, "password": MR_PASSWORD},
            timeout=10,
        )
        if resp.status_code == 200:
            body = resp.json()
            token = body.get("token") or body.get("access_token") or body.get("accessToken")
            if token:
                _auth_cache["token"] = token
                _auth_cache["expires_at"] = now + timedelta(hours=1)
                logger.info("Merchrules auth OK")
                return token
        logger.warning("Merchrules auth failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Merchrules auth error: %s", exc)

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


async def fetch_site_checkups(
    client: httpx.AsyncClient, headers: dict, site_id: str
) -> dict:
    """Получаем данные чекапов для site_id."""
    result = {"next_checkup": None, "last_checkup_mr": None, "checkup_type": None}
    for endpoint in [
        f"{MERCHRULES_URL}/backend-v2/checkups",
        f"{MERCHRULES_URL}/backend-v2/meetings?type=checkup",
    ]:
        try:
            resp = await client.get(
                endpoint,
                params={"site_id": site_id, "limit": 5},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else data.get("checkups") or data.get("items") or []
                if items:
                    dates = [
                        i.get("date") or i.get("meeting_date") or i.get("scheduled_at", "")[:10]
                        for i in items if i.get("date") or i.get("meeting_date") or i.get("scheduled_at")
                    ]
                    if dates:
                        result["last_checkup_mr"] = max(d for d in dates if d)
                break
        except Exception as exc:
            logger.debug("fetch_site_checkups(%s) %s: %s", site_id, endpoint, exc)
    return result


async def fetch_site_feeds(
    client: httpx.AsyncClient, headers: dict, site_id: str
) -> dict:
    """Получаем данные индекса/фидов для site_id."""
    result = {"feed_index_size": 0, "feed_index_limit": 0, "feed_status": "", "feed_count": 0}
    for endpoint in [
        f"{MERCHRULES_URL}/backend-v2/feeds",
        f"{MERCHRULES_URL}/backend-v2/sites/{site_id}/feeds",
        f"{MERCHRULES_URL}/backend-v2/index",
    ]:
        try:
            resp = await client.get(
                endpoint,
                params={"site_id": site_id},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else data.get("feeds") or data.get("items") or [data]
                total_size  = 0
                total_limit = 0
                statuses    = []
                for feed in items:
                    total_size  += int(feed.get("index_size")  or feed.get("size")  or feed.get("indexed", 0))
                    total_limit += int(feed.get("index_limit") or feed.get("limit") or feed.get("max", 0))
                    st = feed.get("status") or feed.get("state") or ""
                    if st:
                        statuses.append(str(st))
                result["feed_index_size"]  = total_size
                result["feed_index_limit"] = total_limit
                result["feed_status"]      = statuses[0] if statuses else ""
                result["feed_count"]       = len(items)
                break
        except Exception as exc:
            logger.debug("fetch_site_feeds(%s) %s: %s", site_id, endpoint, exc)
    return result


async def fetch_site_roadmap(
    client: httpx.AsyncClient, headers: dict, site_id: str
) -> list[dict]:
    """Получаем roadmap-задачи для site_id."""
    for endpoint in [
        f"{MERCHRULES_URL}/backend-v2/roadmap",
        f"{MERCHRULES_URL}/backend-v2/improvements",
        f"{MERCHRULES_URL}/backend-v2/projects",
    ]:
        try:
            resp = await client.get(
                endpoint,
                params={"site_id": site_id, "limit": 50},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else (
                    data.get("roadmap") or data.get("improvements")
                    or data.get("items") or []
                )
                return [
                    {
                        "id":       i.get("id"),
                        "title":    i.get("title") or i.get("name") or "",
                        "status":   i.get("status") or "plan",
                        "priority": i.get("priority") or "medium",
                        "due_date": i.get("due_date") or i.get("dueDate") or "",
                        "overdue":  bool(
                            i.get("due_date") and i["due_date"] < datetime.today().date().isoformat()
                        ),
                    }
                    for i in items
                ]
        except Exception as exc:
            logger.debug("fetch_site_roadmap(%s) %s: %s", site_id, endpoint, exc)
    return []


async def fetch_all_mr_sites(
    client: httpx.AsyncClient, headers: dict
) -> list[dict]:
    """
    Получаем список всех сайтов/клиентов из Merchrules.
    Используется для импорта клиентов в БД.
    """
    for endpoint in [
        f"{MERCHRULES_URL}/backend-v2/sites",
        f"{MERCHRULES_URL}/backend-v2/clients",
        f"{MERCHRULES_URL}/backend-v2/accounts",
    ]:
        try:
            resp = await client.get(
                endpoint,
                params={"limit": 500},
                headers=headers,
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else (
                    data.get("sites") or data.get("clients")
                    or data.get("accounts") or data.get("items") or []
                )
                logger.info("MR sites fetched: %d from %s", len(items), endpoint)
                return [
                    {
                        "site_id":  str(i.get("id") or i.get("site_id") or ""),
                        "name":     i.get("name") or i.get("title") or i.get("domain") or "",
                        "domain":   i.get("domain") or i.get("url") or "",
                        "segment":  i.get("segment") or i.get("plan") or i.get("tier") or "",
                        "am_name":  i.get("am") or i.get("account_manager") or i.get("manager") or "",
                    }
                    for i in items if i.get("name") or i.get("domain")
                ]
        except Exception as exc:
            logger.debug("fetch_all_mr_sites %s: %s", endpoint, exc)
    return []


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

async def sync_clients_from_merchrules(clients: list[dict]) -> dict:
    """
    Получаем данные из Merchrules для всех клиентов.

    Возвращает dict: { site_id: { open_tasks, blocked_tasks, overdue_tasks, last_meeting, ... } }
    Кэшируется на CACHE_TTL_MINUTES минут.
    """
    now = datetime.now()

    # Возвращаем кэш если свежий
    if (
        _data_cache["data"] is not None
        and _data_cache["updated_at"] is not None
        and now - _data_cache["updated_at"] < timedelta(minutes=CACHE_TTL_MINUTES)
    ):
        return _data_cache["data"]

    if not MR_LOGIN or not MR_PASSWORD:
        return {}

    result = {}

    async with httpx.AsyncClient(timeout=30) as hx:
        token = await get_auth_token(hx)
        if not token:
            return {}

        headers = {"Authorization": f"Bearer {token}"}

        # Собираем все уникальные site_ids
        site_ids = set()
        for c in clients:
            raw = c.get("site_ids") or ""
            for sid in raw.split(","):
                sid = sid.strip()
                if sid:
                    site_ids.add(sid)

        if not site_ids:
            return {}

        # Параллельно запрашиваем задачи, встречи, чекапы и фиды по каждому site_id
        async def fetch_one(site_id: str):
            tasks_data    = await fetch_site_tasks(hx, headers, site_id)
            meetings_data = await fetch_site_meetings(hx, headers, site_id)
            checkups_data = await fetch_site_checkups(hx, headers, site_id)
            feeds_data    = await fetch_site_feeds(hx, headers, site_id)
            return site_id, {**tasks_data, **meetings_data, **checkups_data, **feeds_data}

        fetch_tasks = [fetch_one(sid) for sid in site_ids]
        done = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        for item in done:
            if isinstance(item, Exception):
                logger.warning("MR sync gather error: %s", item)
                continue
            site_id, data = item
            result[site_id] = data

    _data_cache["data"] = result
    _data_cache["updated_at"] = now
    logger.info("Merchrules sync done: %d sites", len(result))

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


def invalidate_cache():
    """Сбрасываем кэш принудительно (например после загрузки задач)."""
    _data_cache["data"] = None
    _data_cache["updated_at"] = None
    _auth_cache["token"] = None
    _auth_cache["expires_at"] = None
