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


# ── Новый режим: ingest тикетов в БД с связью по ID клиента ───────────────────

import re

# Регексы для поиска client_id в тексте поста Time/Workflow.
# Основной формат (Workflow-бот):
#   Какой Site ID (Customer ID)?:
#   5860
# Также поддерживаем: #5860, ID: 5860, account_id=5860 и т.п.
_CLIENT_ID_PATTERNS = [
    re.compile(
        r"(?:Site\s*ID|Customer\s*ID|Client\s*ID|Account\s*ID|Сайт\s*ID|Сайт[\s_-]?айди)"
        r"[^\d\n\r]{0,60}(\d{3,10})",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![\w])#(\d{3,10})\b"),
    re.compile(r"\b(?:ID|АЙДИ|Айди|айди)\s*[:№#=\-]\s*(\d{3,10})", re.IGNORECASE),
    re.compile(
        r"(?:account|client|акк[а-я]*|клиент[а-я]*)[_\s-]?id"
        r"[^\d\n\r]{0,20}(\d{3,10})",
        re.IGNORECASE,
    ),
]


def parse_client_id(text: str) -> Optional[str]:
    """Извлекает ID клиента из текста поста."""
    if not text:
        return None
    for pat in _CLIENT_ID_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


_FIELD_QUESTION_RE = re.compile(
    r"(?:Опишите\s+кратко\s+суть\s+вопроса|Суть\s+вопроса|Тема|Вопрос)\s*\??\s*:\s*",
    re.IGNORECASE,
)

_SKIP_LABELS = (
    "к кому вопрос", "какой site id", "какой customer id",
    "клиент из списка", "примеры кейсов", "обращение в саппорт",
    "опишите", "от пользователя",
)


def extract_title(text: str) -> str:
    """Заголовок тикета: первая содержательная строка после 'Опишите кратко суть вопроса:'.
    Фолбек — первая непустая строка, не похожая на служебный лейбл Workflow."""
    if not text:
        return "Без темы"

    m = _FIELD_QUESTION_RE.search(text)
    if m:
        tail = text[m.end():].strip()
        if tail:
            first = next((l.strip() for l in tail.split("\n") if l.strip()), "")
            if first:
                return first[:200]

    for line in (l.strip() for l in text.split("\n")):
        if not line:
            continue
        low = line.lower()
        if any(low.startswith(lab) for lab in _SKIP_LABELS):
            continue
        return line[:200]
    return "Без темы"


async def get_users_map(token: str, user_ids: List[str]) -> Dict[str, Dict]:
    """Резолв user_id → {username, email, nickname} через /api/v4/users/ids."""
    if not user_ids:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post(
                f"{TIME_BASE_URL}/api/v4/users/ids",
                headers=_headers(token),
                json=list(set(user_ids)),
            )
            if resp.status_code == 200:
                return {u["id"]: u for u in resp.json() if u.get("id")}
    except Exception as e:
        logger.error(f"get_users_map error: {e}")
    return {}


async def get_post_thread(token: str, post_id: str) -> Dict:
    """Получить тред (root + все реплаи)."""
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.get(
                f"{TIME_BASE_URL}/api/v4/posts/{post_id}/thread",
                headers=_headers(token),
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"get_post_thread error: {e}")
    return {}


def _detect_status(message: str, props: dict | None = None) -> str:
    m = (message or "").lower()
    if any(k in m for k in ["закрыт", "решён", "решен", "resolved", "closed", "✅", "🟢"]):
        return "resolved"
    if any(k in m for k in ["в работе", "обрабатывается", "in progress", "🟡"]):
        return "in_progress"
    return "open"


async def get_all_channel_posts(token: str, channel_id: str, max_pages: int = 20) -> List[Dict]:
    """Забирает все root-посты канала (не-реплаи), до max_pages страниц по 100."""
    all_posts = []
    async with httpx.AsyncClient(timeout=30) as hx:
        for page in range(max_pages):
            try:
                resp = await hx.get(
                    f"{TIME_BASE_URL}/api/v4/channels/{channel_id}/posts",
                    headers=_headers(token),
                    params={"per_page": 100, "page": page},
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                posts = data.get("posts", {})
                order = data.get("order", [])
                if not order:
                    break
                for pid in order:
                    p = posts.get(pid)
                    if p and not p.get("root_id"):  # только root-посты
                        all_posts.append(p)
                if len(order) < 100:
                    break
            except Exception as e:
                logger.error(f"get_all_channel_posts page={page}: {e}")
                break
    return all_posts


async def ingest_tickets(db, user, limit_new: int = 100, fetch_threads: bool = True) -> Dict[str, Any]:
    """Главная функция: забирает посты из Mattermost, связывает с клиентами по ID, апсертит в БД."""
    from models import SupportTicket, TicketComment, Client
    settings = dict(user.settings or {})
    # Сначала пытаемся OAuth access_token (с автоматическим рефрешем).
    try:
        from integrations.time_oauth import ensure_fresh_token
        token = await ensure_fresh_token(settings)
    except Exception:
        token = None
    if token and settings != (user.settings or {}):
        # ensure_fresh_token мог обновить access_token — сохраняем
        from sqlalchemy.orm.attributes import flag_modified
        user.settings = settings
        flag_modified(user, "settings")
        db.commit()
    tm = settings.get("tbank_time", {})
    if not token:
        token = (tm.get("access_token") or tm.get("mmauthtoken")
                 or tm.get("session_cookie") or tm.get("api_token")
                 or os.environ.get("TIME_API_TOKEN"))
    if not token:
        return {"ok": False, "error": "no_token"}

    channel_id = tm.get("support_channel_id") or await get_channel_id(token)
    if not channel_id:
        return {"ok": False, "error": "no_channel"}

    posts = await get_all_channel_posts(token, channel_id, max_pages=max(1, limit_new // 100 + 1))
    if not posts:
        return {"ok": True, "ingested": 0, "note": "Пусто"}

    # Соберём всех авторов для резолва
    user_ids = list({p.get("user_id") for p in posts if p.get("user_id")})
    users_map = await get_users_map(token, user_ids)

    # Индекс клиентов для быстрого резолва по ID
    clients = db.query(Client).all()
    client_by_mrid = {c.merchrules_account_id: c for c in clients if c.merchrules_account_id}
    client_by_internal = {str(c.id): c for c in clients}
    client_by_site: Dict[str, Any] = {}
    for c in clients:
        for sid in (c.site_ids or []):
            client_by_site[str(sid)] = c

    def resolve_client(cid_str: Optional[str]):
        if not cid_str:
            return None
        return client_by_mrid.get(cid_str) or client_by_site.get(cid_str) or client_by_internal.get(cid_str)

    ingested = 0
    updated = 0
    unlinked = 0

    for p in posts[:limit_new]:
        ext_id = p.get("id")
        if not ext_id:
            continue
        msg = p.get("message", "") or ""
        cid_raw = parse_client_id(msg)
        client = resolve_client(cid_raw)
        if not client:
            unlinked += 1

        ts_created = p.get("create_at")
        ts_updated = p.get("update_at") or p.get("edit_at") or ts_created
        opened_at = datetime.fromtimestamp(ts_created / 1000) if ts_created else None

        author_id = p.get("user_id") or ""
        author_info = users_map.get(author_id) or {}
        author_name = author_info.get("username") or author_info.get("nickname") or author_id

        ticket = db.query(SupportTicket).filter(SupportTicket.external_id == ext_id).first()
        is_new = ticket is None
        if is_new:
            ticket = SupportTicket(external_id=ext_id, source="tbank_time")
            db.add(ticket)

        ticket.client_id           = client.id if client else None
        ticket.external_client_id  = cid_raw
        ticket.channel_id          = channel_id
        ticket.title               = extract_title(msg)
        ticket.body                = msg
        ticket.status              = _detect_status(msg, p.get("props"))
        ticket.author              = author_id
        ticket.author_name         = author_name
        ticket.opened_at           = opened_at
        ticket.external_url        = f"{TIME_BASE_URL}/{TEAM_NAME}/channels/{CHANNEL_NAME}/{ext_id}"
        ticket.raw                 = p
        if ticket.status == "resolved" and not ticket.resolved_at:
            ticket.resolved_at = datetime.fromtimestamp((ts_updated or ts_created) / 1000) if ts_updated else datetime.utcnow()

        db.flush()

        # Тред
        if fetch_threads and p.get("reply_count", 0):
            thread = await get_post_thread(token, ext_id)
            tposts = list((thread.get("posts") or {}).values()) if isinstance(thread.get("posts"), dict) else []
            reply_user_ids = list({rp.get("user_id") for rp in tposts if rp.get("user_id") and rp.get("id") != ext_id})
            reply_users = await get_users_map(token, reply_user_ids) if reply_user_ids else {}
            last_comment_at = None
            last_comment_snippet = None
            cnt = 0
            for rp in sorted(tposts, key=lambda x: x.get("create_at") or 0):
                rid = rp.get("id")
                if rid == ext_id:
                    continue
                comment = db.query(TicketComment).filter(TicketComment.external_id == rid).first()
                if not comment:
                    comment = TicketComment(ticket_id=ticket.id, external_id=rid)
                    db.add(comment)
                comment.author      = rp.get("user_id")
                comment.author_name = (reply_users.get(rp.get("user_id") or "") or {}).get("username") or rp.get("user_id")
                comment.body        = rp.get("message")
                comment.posted_at   = datetime.fromtimestamp(rp.get("create_at") / 1000) if rp.get("create_at") else None
                comment.raw         = rp
                cnt += 1
                if comment.posted_at and (not last_comment_at or comment.posted_at > last_comment_at):
                    last_comment_at = comment.posted_at
                    last_comment_snippet = (rp.get("message") or "")[:200]
            ticket.comments_count       = cnt
            ticket.last_comment_at      = last_comment_at
            ticket.last_comment_snippet = last_comment_snippet

        if is_new:
            ingested += 1
        else:
            updated += 1

    db.commit()
    return {"ok": True, "ingested": ingested, "updated": updated, "unlinked": unlinked, "total_posts": len(posts)}
