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

AUTH_PATHS = (
    "/api/auth/login", "/api/login", "/api/v1/auth/login",
    "/api/v2/auth/login", "/api/auth/signin",
    "/backend-v2/auth/login", "/backend/auth/login",
    "/auth/login", "/login",
)
TOKEN_KEYS = ("token", "access_token", "accessToken", "jwt", "authToken", "auth_token", "sessionId", "session_id")


def _extract_token(body: dict) -> Optional[str]:
    """Поиск токена во многих возможных местах JSON-ответа."""
    if not isinstance(body, dict):
        return None
    for k in TOKEN_KEYS:
        if body.get(k): return body[k]
    for wrap in ("data", "result", "payload"):
        inner = body.get(wrap) or {}
        if isinstance(inner, dict):
            for k in TOKEN_KEYS:
                if inner.get(k): return inner[k]
    # JWT-подобные строки
    for k, v in body.items():
        if isinstance(v, str) and len(v) > 40 and v.startswith("eyJ"):
            return v
    return None


async def get_auth_token(client: httpx.AsyncClient,
                          login: str = "", password: str = "") -> Optional[str]:
    """Auth в Merchrules. Перебирает AUTH_PATHS × fields × JSON/form.
    Пропускает ответы text/html (SPA landing вместо API).
    Возвращает токен или None."""
    if not login:
        login, password = _default_creds()
    if not login or not password:
        logger.debug("MR creds not set — skipping sync")
        return None

    now = datetime.now()
    cached = _auth_cache.get(login, {})
    if cached.get("token") and cached.get("expires_at") and now < cached["expires_at"]:
        return cached["token"]

    attempts_log = []

    for path in AUTH_PATHS:
        for mode in ("json", "form"):
            for field in ("username", "email", "login"):
                url = f"{MERCHRULES_URL}{path}"
                try:
                    if mode == "form":
                        resp = await client.post(
                            url,
                            data={field: login, "password": password},
                            headers={"Accept": "application/json"},
                            timeout=8, follow_redirects=False,
                        )
                    else:
                        resp = await client.post(
                            url,
                            json={field: login, "password": password},
                            headers={"Accept": "application/json"},
                            timeout=8, follow_redirects=False,
                        )
                except Exception as exc:
                    attempts_log.append(f"{path}[{mode}/{field}]:EXC {exc}")
                    continue

                # 404/405 — путь мёртв, не пробуем больше fields на этом path
                if resp.status_code in (404, 405):
                    attempts_log.append(f"{path}:HTTP {resp.status_code}")
                    break

                # 3xx — редирект на HTML login — API не тут
                if 300 <= resp.status_code < 400:
                    attempts_log.append(f"{path}[{mode}/{field}]:HTTP {resp.status_code} redirect")
                    continue

                # HTML-ответ — не API
                ct = resp.headers.get("content-type", "").lower()
                if "text/html" in ct:
                    attempts_log.append(f"{path}[{mode}/{field}]:HTTP {resp.status_code} HTML")
                    continue

                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except Exception:
                        attempts_log.append(f"{path}[{mode}/{field}]:200 non-JSON")
                        continue
                    token = _extract_token(body)
                    if token:
                        _auth_cache[login] = {"token": token, "expires_at": now + timedelta(hours=1)}
                        logger.info("Merchrules auth OK for %s using %s[%s/%s]", login, path, mode, field)
                        return token
                    attempts_log.append(f"{path}[{mode}/{field}]:200 no-token keys={list(body.keys()) if isinstance(body, dict) else 'non-dict'}")
                elif resp.status_code == 401 or resp.status_code == 403:
                    attempts_log.append(f"{path}[{mode}/{field}]:HTTP {resp.status_code} (bad creds?)")
                elif resp.status_code == 422:
                    attempts_log.append(f"{path}[{mode}/{field}]:HTTP 422 {resp.text[:200]}")
                else:
                    attempts_log.append(f"{path}[{mode}/{field}]:HTTP {resp.status_code} {resp.text[:150]}")

    logger.warning("Merchrules auth FAILED for %s. Attempts (last 5):\n  %s",
                   login, "\n  ".join(attempts_log[-5:]))
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
    """Получаем встречи для site_id за последние 180 дней + возвращаем raw-список
    для последующего upsert в Meeting-таблицу."""
    result = {"last_meeting": None, "meetings_count": 0, "raw": []}
    try:
        resp = await client.get(
            f"{MERCHRULES_URL}/backend-v2/meetings",
            params={"site_id": site_id, "limit": 100},
            headers=headers,
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            meetings = data if isinstance(data, list) else data.get("meetings") or data.get("items") or []
            result["meetings_count"] = len(meetings)
            result["raw"] = meetings
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


def upsert_meeting_from_raw(db, client_id: int, raw: dict) -> bool:
    """Создаёт/обновляет Meeting из raw-dict'а Merchrules API.
    Ключи которые мы знаем: id, date/meeting_date/createdAt, type, title, summary, mood.
    Возвращает True если запись создана/обновлена."""
    from models import Meeting
    ext_id = str(raw.get("id") or raw.get("meeting_id") or raw.get("_id") or "")
    if not ext_id:
        return False
    date_raw = raw.get("date") or raw.get("meeting_date") or raw.get("createdAt") or ""
    dt = None
    try:
        if isinstance(date_raw, str):
            dt = datetime.fromisoformat(date_raw.replace("Z", "").split(".")[0])
    except Exception:
        dt = None
    if dt is None:
        return False
    existing = db.query(Meeting).filter(
        Meeting.external_id == ext_id,
        Meeting.client_id == client_id,
    ).first()
    if existing:
        # Обновляем только "мягкие" поля (не затираем followup_text если есть)
        existing.date = dt
        existing.type = raw.get("type") or existing.type or "sync"
        existing.title = raw.get("title") or existing.title
        if not existing.summary and raw.get("summary"):
            existing.summary = raw.get("summary")
        if raw.get("mood"):
            existing.mood = raw.get("mood")
        return True
    m = Meeting(
        client_id=client_id,
        external_id=ext_id,
        date=dt,
        type=raw.get("type") or "sync",
        title=raw.get("title") or "",
        summary=raw.get("summary") or "",
        source="merchrules",
        mood=raw.get("mood"),
        followup_status="pending",
    )
    db.add(m)
    return True


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


# ── Метрики клиента ────────────────────────────────────────────────────────────

_metrics_cache: dict[str, dict] = {}  # site_id → {metrics, updated_at}
METRICS_CACHE_TTL_MINUTES = 60


async def get_client_metrics(site_id: str, login: str = "", password: str = "") -> dict:
    """
    Получает ключевые метрики клиента с Merchrules.
    Пытается hit /backend-v2/sites/{site_id}/analytics или /backend-v2/sites/{site_id}/stats
    Возвращает:
    {
        "gmv": 0,
        "conversion": 0.0,
        "search_ctr": 0.0,
        "orders": 0,
        "error": None
    }
    Результаты кэшируются на METRICS_CACHE_TTL_MINUTES минут.
    """
    if not site_id:
        return {"gmv": 0, "conversion": 0.0, "search_ctr": 0.0, "orders": 0, "error": "no site_id"}

    # Проверяем кэш
    now = datetime.now()
    cached = _metrics_cache.get(site_id, {})
    if (
        cached.get("metrics") is not None
        and cached.get("updated_at") is not None
        and now - cached["updated_at"] < timedelta(minutes=METRICS_CACHE_TTL_MINUTES)
    ):
        return cached["metrics"]

    if not login:
        login, password = _default_creds()

    result = {"gmv": 0, "conversion": 0.0, "search_ctr": 0.0, "orders": 0, "error": None}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token = await get_auth_token(client, login, password)
            if not token:
                result["error"] = "auth_failed"
                return result

            headers = {"Authorization": f"Bearer {token}"}

            # Пробуем оба endpoint'а
            endpoints = [
                f"{MERCHRULES_URL}/backend-v2/sites/{site_id}/analytics",
                f"{MERCHRULES_URL}/backend-v2/sites/{site_id}/stats",
            ]

            for endpoint in endpoints:
                try:
                    resp = await client.get(endpoint, headers=headers, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()

                        # Пытаемся распарсить
                        if isinstance(data, dict):
                            result["gmv"] = int(data.get("gmv") or data.get("revenue") or 0)
                            result["conversion"] = float(data.get("conversion") or data.get("conversion_rate") or 0.0)
                            result["search_ctr"] = float(data.get("search_ctr") or data.get("ctr") or 0.0)
                            result["orders"] = int(data.get("orders") or data.get("order_count") or 0)
                            result["error"] = None
                            break
                except Exception as e:
                    logger.debug("Metrics endpoint %s failed: %s", endpoint, e)
                    continue

            # Если оба endpoint'а упали
            if result["error"] is None and result["gmv"] == 0:
                result["error"] = "no_data"

    except Exception as exc:
        logger.error("get_client_metrics error for site %s: %s", site_id, exc)
        result["error"] = str(exc)[:100]

    # Кэшируем результат
    _metrics_cache[site_id] = {"metrics": result, "updated_at": now}

    return result


# ── Checkup queries (top / random / zero / null) ─────────────────────────────
# Единый helper для всех клиентов: по site_id дёргает Merchrules analytics
# endpoints и возвращает список запросов. Результаты кэшируются per-site_id.
_queries_cache: dict = {}  # site_id+type -> {"queries":[...], "updated_at": datetime}
_QUERIES_CACHE_TTL_MIN = 30


async def fetch_checkup_queries(
    site_id: str, type_: str = "top",
    login: str = "", password: str = "",
    days: int = 30, limit: Optional[int] = None,
) -> list:
    """Тянет запросы из Merchrules analytics. Унифицированная точка для всех клиентов.

    type: 'top' | 'random' | 'zero' | 'null'
      top    → /analytics/top_queries?limit=120
      random → /analytics/top_queries?limit=30&min_count=0&volume=1000&randomizer=1
      zero   → /analytics/zero_queries?limit=90&mode=aggregated   (нулевые клики)
      null   → /analytics/null_queries?limit=90                    (пустая выдача)
    """
    t = (type_ or "top").lower()
    if t == "zeroquery":
        t = "null"
    cache_key = f"{site_id}:{t}:{days}:{limit or ''}"
    now = datetime.now()
    cached = _queries_cache.get(cache_key)
    if cached and (now - cached["updated_at"]).total_seconds() < _QUERIES_CACHE_TTL_MIN * 60:
        return cached["queries"]

    if not login:
        login, password = _default_creds()
    if not login or not password:
        return []

    try:
        from datetime import timedelta as _td
        date_to = now.date()
        date_from = date_to - _td(days=days)

        async with httpx.AsyncClient(timeout=20) as hx:
            token = None
            for field in ("email", "login", "username"):
                try:
                    r = await hx.post(
                        f"{MERCHRULES_URL}/backend-v2/auth/login",
                        json={field: login, "password": password},
                        timeout=15,
                    )
                    if r.status_code == 200:
                        body = r.json()
                        token = body.get("token") or body.get("access_token") or body.get("accessToken")
                        if token:
                            break
                except Exception:
                    continue
            if not token:
                return []
            headers = {"Authorization": f"Bearer {token}"}

            params_by_type = {
                "top":    {"limit": str(limit or 120)},
                "random": {"limit": str(limit or 30), "min_count": "0",
                           "volume": "1000", "randomizer": "1"},
                "zero":   {"limit": str(limit or 90), "mode": "aggregated"},
                "null":   {"limit": str(limit or 90)},
            }
            endpoint_by_type = {
                "top":    "top_queries",
                "random": "top_queries",
                "zero":   "zero_queries",
                "null":   "null_queries",
            }
            ep = endpoint_by_type.get(t, "top_queries")
            params = {
                "site_id": str(site_id),
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "platform": "all",
                **params_by_type.get(t, {"limit": "120"}),
            }
            r = await hx.get(
                f"{MERCHRULES_URL}/backend-v2/analytics/{ep}",
                params=params, headers=headers, timeout=20,
            )
            if r.status_code != 200:
                logger.warning("fetch_checkup_queries %s HTTP %s: %s",
                               ep, r.status_code, r.text[:200])
                return []
            data = r.json()
            items = data if isinstance(data, list) else (data.get("queries") or data.get("items") or data.get("data") or [])
            queries: list = []
            for it in items:
                if isinstance(it, str):
                    q = it.strip()
                elif isinstance(it, dict):
                    q = (it.get("query") or it.get("q") or it.get("text") or "").strip()
                else:
                    q = ""
                if q and q not in queries:
                    queries.append(q)
            _queries_cache[cache_key] = {"queries": queries, "updated_at": now}
            return queries
    except Exception as e:
        logger.warning("fetch_checkup_queries failed: %s", e)
        return []


# ── Site API keys (Diginetica) ───────────────────────────────────────────────
# Тянет apiKey клиентов из https://merchrules.any-platform.ru/api/site/all
# и возвращает {site_id: {apiKey, domain, ...}}. Используется как fallback
# когда в Client.integration_metadata нет сохранённого diginetica_api_key.
_site_keys_cache: dict = {}  # login -> {"map": {...}, "updated_at": datetime}


async def fetch_site_api_keys(login: str = "", password: str = "") -> dict:
    """Возвращает {site_id_str: {"apiKey": str, "domain": str|None, "name": str|None}}.
    Кэш 30 минут per-login."""
    if not login:
        login, password = _default_creds()
    if not login or not password:
        return {}
    now = datetime.now()
    cached = _site_keys_cache.get(login)
    if cached and (now - cached["updated_at"]).total_seconds() < CACHE_TTL_MINUTES * 60:
        return cached["map"]
    try:
        async with httpx.AsyncClient(timeout=20) as hx:
            # 1. Auth
            token = None
            for field in ("email", "login", "username"):
                try:
                    r = await hx.post(
                        f"{MERCHRULES_URL}/backend-v2/auth/login",
                        json={field: login, "password": password}, timeout=15,
                    )
                    if r.status_code == 200:
                        body = r.json()
                        token = body.get("token") or body.get("access_token") or body.get("accessToken")
                        if token: break
                except Exception:
                    continue
            if not token:
                return {}
            headers = {"Authorization": f"Bearer {token}"}
            # 2. /api/site/all (именно api, не backend-v2)
            out: dict = {}
            for ep in (f"{MERCHRULES_URL}/api/site/all",
                       f"{MERCHRULES_URL}/backend-v2/sites",
                       f"{MERCHRULES_URL}/backend-v2/accounts"):
                try:
                    r = await hx.get(ep, headers=headers, timeout=20)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    items = data if isinstance(data, list) else (data.get("sites") or data.get("accounts") or data.get("items") or [])
                    for it in items:
                        sid = it.get("id") or it.get("site_id") or it.get("siteId")
                        if not sid:
                            continue
                        sid_s = str(sid)
                        api_key = (it.get("apiKey") or it.get("api_key")
                                   or it.get("dn_api_key") or it.get("digineticaApiKey") or "")
                        out[sid_s] = {
                            "apiKey": api_key,
                            "domain": it.get("domain") or it.get("url") or "",
                            "name": it.get("name") or it.get("title") or "",
                        }
                    if out:
                        break
                except Exception:
                    continue
            _site_keys_cache[login] = {"map": out, "updated_at": now}
            return out
    except Exception as e:
        logger.warning("fetch_site_api_keys failed: %s", e)
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# Helpers для публичных Merchrules endpoints (2026-04 дамп реальных запросов
# фронта клиента). Используются:
#   • qbr_auto_collect.collect_product_metrics → per-product метрики
#   • design_mappers/dashboard → агрегаты и time-series для портфеля
#   • scheduler → периодический sync NPS и health
# ═══════════════════════════════════════════════════════════════════════════

_REPORT_METRIC_NAMES_DEFAULT = (
    "SESSIONS_TOTAL", "ORDERS_TOTAL", "REVENUE_TOTAL", "CONVERSION",
    "AOV", "RPS", "SEARCH_EVENTS_TOTAL", "ZERO_QUERIES_COUNT",
    "CORRECTION_TOTAL", "AUTOCOMPLETE_SESSIONS_TOTAL",
    "AUTOCOMPLETE_AND_SEARCH_SESSIONS_TOTAL",
)


def _fmt_merchrules_range(date_from: str, date_to: str) -> tuple[str, str]:
    """Merchrules /api/report/* принимает полуоткрытые интервалы в виде ISO.
    Нормализуем YYYY-MM-DD → YYYY-MM-DDT00:00:00 / T23:59:59."""
    f = str(date_from).strip()
    t = str(date_to).strip()
    if "T" not in f:
        f = f"{f}T00:00:00"
    if "T" not in t:
        t = f"{t}T23:59:59"
    return f, t


async def fetch_report_agg(
    site_id: str,
    date_from: str,
    date_to: str,
    names: Optional[list[str]] = None,
    login: str = "",
    password: str = "",
) -> dict:
    """GET /api/report/agg/{siteId}/global?name=<CSV>&from=...&to=...&siteId=...

    Возвращает агрегированные метрики за период (одно значение на метрику).
    Пустой dict — если авторизация/запрос упали, чтобы не роняли вызывающий код.
    """
    f, t = _fmt_merchrules_range(date_from, date_to)
    names_csv = ",".join(names or _REPORT_METRIC_NAMES_DEFAULT)
    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                return {}
            resp = await hx.get(
                f"{MERCHRULES_URL}/api/report/agg/{site_id}/global",
                params={"name": names_csv, "from": f, "to": t, "siteId": site_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                logger.warning("report/agg %s → %s", site_id, resp.status_code)
                return {}
            body = resp.json()
            if isinstance(body, dict):
                return body
            return {"items": body}
    except Exception:
        logger.exception("fetch_report_agg site=%s failed", site_id)
        return {}


async def fetch_report_daily(
    site_id: str,
    date_from: str,
    date_to: str,
    names: Optional[list[str]] = None,
    login: str = "",
    password: str = "",
) -> dict:
    """GET /api/report/daily/{siteId}/global — time-series по дням.
    Используется для sparkline GMV/sessions на дашборде."""
    f, t = _fmt_merchrules_range(date_from, date_to)
    names_csv = ",".join(names or ["SESSIONS_TOTAL", "ORDERS_TOTAL", "REVENUE_TOTAL"])
    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                return {}
            resp = await hx.get(
                f"{MERCHRULES_URL}/api/report/daily/{site_id}/global",
                params={"name": names_csv, "from": f, "to": t, "siteId": site_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                logger.warning("report/daily %s → %s", site_id, resp.status_code)
                return {}
            body = resp.json()
            if isinstance(body, dict):
                return body
            return {"items": body}
    except Exception:
        logger.exception("fetch_report_daily site=%s failed", site_id)
        return {}


async def fetch_any_products_metrics(
    site_id: str,
    date_from: str,
    date_to: str,
    platform: str = "",
    login: str = "",
    password: str = "",
) -> dict:
    """GET /backend-v2/api/v1/any-products/metrics?site_id=...&platform=...&date_from=...&date_to=...

    ЭТО per-product endpoint Merchrules, который используется в qbr_auto_collect:
    раньше мы делали эвристику через общий get_client_metrics, теперь — настоящие
    метрики в разрезе продуктов (sort/recs/autocomplete/merchandising/...)."""
    params: dict = {
        "site_id":   site_id,
        "date_from": date_from,
        "date_to":   date_to,
    }
    if platform:
        params["platform"] = platform
    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                return {}
            resp = await hx.get(
                f"{MERCHRULES_URL}/backend-v2/api/v1/any-products/metrics",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                logger.warning("any-products/metrics %s → %s", site_id, resp.status_code)
                return {}
            body = resp.json()
            return body if isinstance(body, dict) else {"items": body}
    except Exception:
        logger.exception("fetch_any_products_metrics site=%s failed", site_id)
        return {}


async def fetch_any_products_availability(
    site_id: str,
    login: str = "",
    password: str = "",
) -> dict:
    """GET /backend-v2/api/v1/any-products/availability?site_id=... — список доступных
    продуктов у клиента (sort, recs, autocomplete, merchandising, ...)."""
    try:
        async with httpx.AsyncClient(timeout=20) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                return {}
            resp = await hx.get(
                f"{MERCHRULES_URL}/backend-v2/api/v1/any-products/availability",
                params={"site_id": site_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return {}
            body = resp.json()
            return body if isinstance(body, dict) else {"items": body}
    except Exception:
        logger.exception("fetch_any_products_availability site=%s failed", site_id)
        return {}


async def fetch_nps_survey(login: str = "", password: str = "") -> dict:
    """GET /backend-v2/api/v1/nps/my-survey — NPS-опрос по текущему менеджеру.
    Ответ содержит последнюю оценку клиентов менеджера."""
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                return {}
            resp = await hx.get(
                f"{MERCHRULES_URL}/backend-v2/api/v1/nps/my-survey",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return {}
            body = resp.json()
            return body if isinstance(body, dict) else {"items": body}
    except Exception:
        logger.exception("fetch_nps_survey failed")
        return {}


async def fetch_health_dashboard(login: str = "", password: str = "") -> dict:
    """GET /backend-v2/api/v1/health/dashboard — здоровье системы Merchrules."""
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                return {}
            resp = await hx.get(
                f"{MERCHRULES_URL}/backend-v2/api/v1/health/dashboard",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return {}
            body = resp.json()
            return body if isinstance(body, dict) else {"items": body}
    except Exception:
        logger.exception("fetch_health_dashboard failed")
        return {}


async def fetch_incidents(
    page: int = 1,
    page_size: int = 50,
    login: str = "",
    password: str = "",
) -> dict:
    """GET /backend-v2/api/v1/incidents — список инцидентов Merchrules."""
    try:
        async with httpx.AsyncClient(timeout=20) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                return {}
            resp = await hx.get(
                f"{MERCHRULES_URL}/backend-v2/api/v1/incidents",
                params={"page": page, "page_size": page_size},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return {}
            body = resp.json()
            return body if isinstance(body, dict) else {"items": body}
    except Exception:
        logger.exception("fetch_incidents failed")
        return {}


async def fetch_recs_coverage(
    site_ids: list[str],
    login: str = "",
    password: str = "",
) -> dict:
    """GET /backend-v2/api/v1/recs-coverage?site_ids=1,2,3 — покрытие рекомендациями."""
    if not site_ids:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                return {}
            resp = await hx.get(
                f"{MERCHRULES_URL}/backend-v2/api/v1/recs-coverage",
                params={"site_ids": ",".join(str(s) for s in site_ids)},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return {}
            body = resp.json()
            return body if isinstance(body, dict) else {"items": body}
    except Exception:
        logger.exception("fetch_recs_coverage failed")
        return {}
