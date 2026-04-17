"""
Tbank Time Integration — получение тикетов из канала any-team-support.

Time = Mattermost on-premise (time.tbank.ru).
Авторизация: SSO через TinkoffID → cookie MMAUTHTOKEN → Bearer токен.

Нужный канал: https://time.tbank.ru/tinkoff/channels/any-team-support
  team_name   = tinkoff
  channel_name = any-team-support

После авторизации (через /auth/time в хабе) токен сохраняется в
user.settings.tbank_time.mmauthtoken и channel_id в support_channel_id.

API Mattermost:
  GET /api/v4/users/me                          → данные пользователя
  GET /api/v4/teams/name/{team}/channels/name/{ch} → channel_id
  GET /api/v4/channels/{id}/posts               → посты (тикеты)
  GET /api/v4/posts/{id}/thread                 → тред поста
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

TIME_BASE_URL = "https://time.tbank.ru"
TEAM_NAME = "tinkoff"
CHANNEL_NAME = os.getenv("TIME_SUPPORT_CHANNEL", "any-team-support")

CACHE_TTL = 300  # 5 минут
_cache: Dict[str, Any] = {}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and datetime.now() < entry["expires"]:
        return entry["data"]
    return None


def _cache_set(key: str, data, ttl: int = CACHE_TTL):
    _cache[key] = {"data": data, "expires": datetime.now() + timedelta(seconds=ttl)}


# ── Получение channel_id ──────────────────────────────────────────────────────

async def get_channel_id(token: str, channel_name: str = CHANNEL_NAME) -> Optional[str]:
    """Получить channel_id по team+channel name."""
    cache_key = f"channel_id:{channel_name}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.get(
                f"{TIME_BASE_URL}/api/v4/teams/name/{TEAM_NAME}/channels/name/{channel_name}",
                headers=_headers(token),
            )
            if resp.status_code == 200:
                cid = resp.json().get("id")
                if cid:
                    _cache_set(cache_key, cid, ttl=3600)
                    return cid

            # Если 404 — ищем через поиск
            if resp.status_code == 404:
                search = await hx.post(
                    f"{TIME_BASE_URL}/api/v4/channels/search",
                    headers=_headers(token),
                    json={"term": channel_name},
                )
                if search.status_code == 200:
                    for ch in (search.json() if isinstance(search.json(), list) else []):
                        if channel_name in (ch.get("name") or ""):
                            cid = ch.get("id")
                            if cid:
                                _cache_set(cache_key, cid, ttl=3600)
                                return cid
    except Exception as e:
        logger.error(f"get_channel_id error: {e}")
    return None


# ── Получение постов (тикетов) ────────────────────────────────────────────────

async def get_channel_posts(
    token: str,
    channel_id: str,
    per_page: int = 60,
    page: int = 0,
) -> List[Dict]:
    """Получить посты из канала (обращения партнёров)."""
    try:
        async with httpx.AsyncClient(timeout=20) as hx:
            resp = await hx.get(
                f"{TIME_BASE_URL}/api/v4/channels/{channel_id}/posts",
                headers=_headers(token),
                params={"per_page": per_page, "page": page},
            )
            if resp.status_code == 200:
                data = resp.json()
                posts = data.get("posts", {})
                order = data.get("order", [])
                return [posts[pid] for pid in order if pid in posts]
    except Exception as e:
        logger.error(f"get_channel_posts error: {e}")
    return []


def _parse_post_as_ticket(post: dict, client_name: str = "") -> Optional[Dict]:
    """
    Преобразует пост Mattermost в тикет.
    Фильтрует по имени клиента если передан.
    """
    message = post.get("message", "")
    if not message:
        return None

    # Фильтруем по имени клиента
    if client_name and client_name.lower() not in message.lower():
        return None

    created_at = None
    ts = post.get("create_at")
    if ts:
        try:
            created_at = datetime.fromtimestamp(ts / 1000)
        except Exception:
            pass

    updated_at = None
    uts = post.get("update_at") or post.get("edit_at")
    if uts and uts != ts:
        try:
            updated_at = datetime.fromtimestamp(uts / 1000)
        except Exception:
            pass

    # Определяем статус по реакциям или тексту
    status = "open"
    props = post.get("props", {})
    attachments = props.get("attachments", [])
    for att in attachments:
        color = att.get("color", "")
        if color in ("#2eb886", "good"):
            status = "resolved"
        elif color in ("#daa038", "warning"):
            status = "in_progress"

    if "закрыт" in message.lower() or "resolved" in message.lower() or "решён" in message.lower():
        status = "resolved"
    elif "в работе" in message.lower() or "обрабатывается" in message.lower():
        status = "in_progress"

    # Заголовок — первая строка поста
    lines = [l.strip() for l in message.split("\n") if l.strip()]
    title = lines[0][:120] if lines else "Без темы"

    return {
        "id": post.get("id", ""),
        "title": title,
        "message": message[:500],
        "status": status,
        "priority": "normal",
        "created_at": created_at,
        "updated_at": updated_at,
        "author": post.get("user_id", ""),
        "root_id": post.get("root_id", ""),  # если это ответ в треде
        "channel_id": post.get("channel_id", ""),
        "post_url": f"{TIME_BASE_URL}/{TEAM_NAME}/channels/{CHANNEL_NAME}",
    }


# ── Публичные функции ─────────────────────────────────────────────────────────

async def get_support_tickets(
    account_name: str,
    token: str = "",
    status_filter: str = "open",
    use_cache: bool = True,
) -> List[Dict]:
    """
    Получить тикеты из канала any-team-support для конкретного клиента.
    Фильтрует посты по имени аккаунта.
    """
    if not token:
        token = os.environ.get("TIME_API_TOKEN") or os.environ.get("TIME_SESSION_COOKIE", "")
    if not token:
        logger.warning("get_support_tickets: no token")
        return []

    cache_key = f"tickets:{account_name}:{status_filter}"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    # Получаем channel_id
    channel_id = await get_channel_id(token)
    if not channel_id:
        logger.warning(f"Channel '{CHANNEL_NAME}' not found in team '{TEAM_NAME}'")
        return []

    # Получаем посты
    posts = await get_channel_posts(token, channel_id, per_page=100)

    # Преобразуем в тикеты, фильтруем по клиенту
    tickets = []
    for post in posts:
        ticket = _parse_post_as_ticket(post, client_name=account_name)
        if ticket:
            # Фильтр по статусу
            if status_filter and status_filter != "all":
                statuses = [s.strip() for s in status_filter.split(",")]
                if ticket["status"] not in statuses and status_filter != "open,in_progress":
                    continue
                if status_filter == "open,in_progress" and ticket["status"] not in ("open", "in_progress"):
                    continue
            tickets.append(ticket)

    # Сортируем по дате — свежие первыми
    tickets.sort(key=lambda t: t.get("created_at") or datetime.min, reverse=True)

    if use_cache:
        _cache_set(cache_key, tickets)

    logger.info(f"✅ Time: found {len(tickets)} tickets for '{account_name}'")
    return tickets


async def sync_tickets_for_client(
    account_name: str,
    token: str = "",
) -> Dict[str, Any]:
    """
    Синхронизировать тикеты для клиента.
    Возвращает сводку: open_count, total_count, last_ticket.
    """
    if not token:
        token = os.environ.get("TIME_API_TOKEN") or os.environ.get("TIME_SESSION_COOKIE", "")

    tickets = await get_support_tickets(account_name, token=token, status_filter="all", use_cache=False)

    open_count = sum(1 for t in tickets if t["status"] in ("open", "in_progress"))
    last_ticket = tickets[0] if tickets else None

    return {
        "ok": True,
        "tickets": tickets[:20],
        "open_count": open_count,
        "total_count": len(tickets),
        "last_ticket": {
            "title": last_ticket["title"],
            "status": last_ticket["status"],
            "created_at": last_ticket["created_at"].isoformat() if last_ticket and last_ticket.get("created_at") else None,
        } if last_ticket else None,
    }


async def get_all_tickets(token: str = "", per_page: int = 100) -> List[Dict]:
    """Получить все посты из канала (без фильтрации по клиенту)."""
    if not token:
        token = os.environ.get("TIME_API_TOKEN") or os.environ.get("TIME_SESSION_COOKIE", "")
    if not token:
        return []

    channel_id = await get_channel_id(token)
    if not channel_id:
        return []

    posts = await get_channel_posts(token, channel_id, per_page=per_page)
    tickets = []
    for post in posts:
        ticket = _parse_post_as_ticket(post)
        if ticket:
            tickets.append(ticket)

    tickets.sort(key=lambda t: t.get("created_at") or datetime.min, reverse=True)
    return tickets
