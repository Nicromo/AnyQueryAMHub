"""
Синхронизация данных из Merchrules Dashboard.
Авторизация через cookie-сессию (HttpOnly) — не Bearer-токен.
Один httpx.AsyncClient на сеанс, куки идут автоматически.
"""
import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

MERCHRULES_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
CACHE_TTL_MINUTES = 30

_data_cache:  dict[str, dict] = {}  # login → {data, updated_at}
_metrics_cache: dict[str, dict] = {}


def _default_creds() -> tuple[str, str]:
    return os.getenv("MERCHRULES_LOGIN", ""), os.getenv("MERCHRULES_PASSWORD", "")


async def _make_authed_client(login: str, password: str) -> tuple[Optional[httpx.AsyncClient], str]:
    """
    Авторизуется через cookie-сессию, возвращает (client, base_url).
    Вызывающий ОБЯЗАН закрыть через await client.aclose().
    """
    client = httpx.AsyncClient(timeout=30, follow_redirects=True)
    for url in ["https://merchrules-qa.any-platform.ru", MERCHRULES_URL]:
        try:
            resp = await client.post(
                f"{url}/backend-v2/auth/login",
                json={"username": login, "password": password},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("Merchrules cookie-auth OK for %s on %s", login, url)
                return client, url
        except Exception as exc:
            logger.warning("Merchrules auth error (%s) on %s: %s", login, url, exc)
    await client.aclose()
    return None, ""


async def fetch_site_tasks(client: httpx.AsyncClient, base_url: str, site_id: str) -> dict:
    result = {"open_tasks": 0, "blocked_tasks": 0, "overdue_tasks": 0, "tasks": []}
    try:
        resp = await client.get(
            f"{base_url}/backend-v2/tasks",
            params={"site_id": site_id, "status": "plan,in_progress,blocked", "limit": 100},
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


async def fetch_site_meetings(client: httpx.AsyncClient, base_url: str, site_id: str) -> dict:
    result = {"last_meeting": None, "meetings_count": 0}
    try:
        resp = await client.get(
            f"{base_url}/backend-v2/meetings",
            params={"site_id": site_id, "limit": 5},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            meetings = data if isinstance(data, list) else data.get("meetings") or data.get("items") or []
            result["meetings_count"] = len(meetings)
            if meetings:
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


async def sync_clients_from_merchrules(clients: list[dict],
                                       login: str = "", password: str = "") -> dict:
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

    client, base_url = await _make_authed_client(login, password)
    if not client:
        return {}

    result = {}
    try:
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
            tasks_data    = await fetch_site_tasks(client, base_url, site_id)
            meetings_data = await fetch_site_meetings(client, base_url, site_id)
            return site_id, {**tasks_data, **meetings_data}

        done = await asyncio.gather(*[fetch_one(sid) for sid in site_ids], return_exceptions=True)

        for item in done:
            if isinstance(item, Exception):
                logger.warning("MR sync gather error: %s", item)
                continue
            site_id, data = item
            result[site_id] = data
    finally:
        await client.aclose()

    _data_cache[login] = {"data": result, "updated_at": now}
    logger.info("MR sync done for %s: %d sites", login, len(result))
    return result


def get_client_mr_data(mr_data: dict, site_ids_raw: str) -> dict:
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
    if login:
        _data_cache.pop(login, None)
    else:
        _data_cache.clear()


async def get_client_metrics(site_id: str, login: str = "", password: str = "") -> dict:
    if not site_id:
        return {"gmv": 0, "conversion": 0.0, "search_ctr": 0.0, "orders": 0, "error": "no site_id"}

    now = datetime.now()
    cached = _metrics_cache.get(site_id, {})
    if (
        cached.get("metrics") is not None
        and cached.get("updated_at") is not None
        and now - cached["updated_at"] < timedelta(minutes=60)
    ):
        return cached["metrics"]

    if not login:
        login, password = _default_creds()

    result = {"gmv": 0, "conversion": 0.0, "search_ctr": 0.0, "orders": 0, "error": None}

    client, base_url = await _make_authed_client(login, password)
    if not client:
        result["error"] = "auth_failed"
        return result

    try:
        for endpoint in [
            f"{base_url}/backend-v2/sites/{site_id}/analytics",
            f"{base_url}/backend-v2/sites/{site_id}/stats",
        ]:
            try:
                resp = await client.get(endpoint, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        result["gmv"] = int(data.get("gmv") or data.get("revenue") or 0)
                        result["conversion"] = float(data.get("conversion") or data.get("conversion_rate") or 0.0)
                        result["search_ctr"] = float(data.get("search_ctr") or data.get("ctr") or 0.0)
                        result["orders"] = int(data.get("orders") or data.get("order_count") or 0)
                        result["error"] = None
                        break
            except Exception as e:
                logger.debug("Metrics endpoint %s failed: %s", endpoint, e)
    except Exception as exc:
        result["error"] = str(exc)[:100]
    finally:
        await client.aclose()

    _metrics_cache[site_id] = {"metrics": result, "updated_at": now}
    return result
