"""
K.Talk интеграция — корпоративный мессенджер T-Bank.
https://tbank.ktalk.ru/

Авторизация:
  - OIDC password grant (если поддерживается сервером)
  - Fallback: Incoming Webhook для отправки

API после авторизации:
  - GET /api/calendar — встречи/конференции
  - GET /api/conferencesHistory/recent — история звонков
  - POST /api/metrics — (read-only, не используется)
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://tbank.ktalk.ru"
KTALK_WEBHOOK_URL = os.getenv("KTALK_WEBHOOK_URL", "")

_token_cache: dict[str, dict] = {}  # login → {access_token, expires_at}
_oidc_config_cache: Optional[dict] = None


# ── OIDC Discovery ────────────────────────────────────────────────────────────

async def _get_oidc_config() -> dict:
    """Получить OIDC конфигурацию сервера."""
    global _oidc_config_cache
    if _oidc_config_cache:
        return _oidc_config_cache
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            r = await hx.get(f"{BASE_URL}/api/authorize/oidc/.well-known/openid-configuration")
        if r.status_code == 200:
            _oidc_config_cache = r.json()
            logger.info("KTalk OIDC config loaded. grant_types: %s",
                        _oidc_config_cache.get("grant_types_supported", []))
            return _oidc_config_cache
    except Exception as exc:
        logger.warning("KTalk OIDC discovery failed: %s", exc)
    return {}


# ── Auth ──────────────────────────────────────────────────────────────────────

async def login(login_id: str, password: str) -> dict:
    """
    Авторизация через OIDC password grant.
    Возвращает {"ok": True, "token": "...", "user": {...}}
    или {"ok": False, "error": "..."}.
    """
    if not login_id or not password:
        return {"ok": False, "error": "Нужны логин и пароль"}

    now = datetime.now()
    cached = _token_cache.get(login_id, {})
    if cached.get("access_token") and cached.get("expires_at") and now < cached["expires_at"]:
        return {"ok": True, "token": cached["access_token"], "user": cached.get("user", {})}

    # 1. Получаем OIDC config для token_endpoint
    oidc_cfg = await _get_oidc_config()
    token_endpoint = oidc_cfg.get("token_endpoint", f"{BASE_URL}/api/authorize/oidc/connect/token")

    grant_types = oidc_cfg.get("grant_types_supported", [])
    if grant_types and "password" not in grant_types:
        return {
            "ok": False,
            "error": f"OIDC password grant не поддерживается. Доступные: {grant_types}. "
                     "Нужна авторизация через браузер.",
        }

    # 2. Запрашиваем токен через Resource Owner Password Credentials
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post(
                token_endpoint,
                content=urlencode({
                    "grant_type": "password",
                    "client_id": "KTalk",
                    "username": login_id,
                    "password": password,
                    "scope": "profile email allatclaims",
                }).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if resp.status_code == 200:
            body = resp.json()
            access_token = body.get("access_token")
            if not access_token:
                return {"ok": False, "error": f"Нет access_token в ответе: {body}"}

            expires_in = body.get("expires_in", 3600)
            entry = {
                "access_token": access_token,
                "expires_at": now + timedelta(seconds=expires_in - 60),
                "user": {},
            }
            # 3. Получаем данные пользователя
            user_info = await _get_user_info(access_token)
            entry["user"] = user_info
            _token_cache[login_id] = entry
            logger.info("KTalk OIDC auth OK for %s", login_id)
            return {"ok": True, "token": access_token, "user": user_info}

        body_text = resp.text[:300]
        if resp.status_code in (400, 401):
            try:
                err = resp.json()
                if err.get("error") == "invalid_grant":
                    return {"ok": False, "error": "Неверный логин или пароль"}
                if err.get("error") == "unsupported_grant_type":
                    return {"ok": False, "error": "OIDC password grant отключён на сервере — нужен браузерный вход"}
                return {"ok": False, "error": f"{err.get('error')}: {err.get('error_description', body_text)}"}
            except Exception:
                pass
        return {"ok": False, "error": f"HTTP {resp.status_code}: {body_text}"}

    except Exception as exc:
        logger.warning("KTalk login error (%s): %s", login_id, exc)
        return {"ok": False, "error": str(exc)}


async def _get_user_info(access_token: str) -> dict:
    """Получить данные пользователя из /api/context."""
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            r = await hx.get(
                f"{BASE_URL}/api/context",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if r.status_code == 200:
            return r.json().get("user", {})
    except Exception as exc:
        logger.debug("KTalk get user info error: %s", exc)
    return {}


def _cookie_headers(session_cookie: str) -> dict:
    """Заголовки для cookie-based сессии KTalk."""
    return {
        "Cookie": f"session={session_cookie}",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE_URL}/",
    }


def _auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE_URL}/",
    }


def invalidate(login_id: str = ""):
    if login_id:
        _token_cache.pop(login_id, None)
    else:
        _token_cache.clear()


# ── API methods ───────────────────────────────────────────────────────────────

async def get_calendar_with_cookie(session_cookie: str,
                                    start: Optional[str] = None,
                                    end: Optional[str] = None) -> list[dict]:
    """Получить встречи из KTalk через session cookie."""
    return await get_calendar(session_cookie, start, end, use_cookie=True)


async def get_calendar(access_token: str,
                       start: Optional[str] = None,
                       end: Optional[str] = None,
                       use_cookie: bool = False) -> list[dict]:
    """
    Получить события из корпоративного календаря.
    start/end — ISO-даты (YYYY-MM-DDTHH:mm:ss.sssZ).
    """
    if not start:
        now = datetime.utcnow()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if not end:
        now = datetime.utcnow()
        end = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S.999Z")
    try:
        headers = _cookie_headers(access_token) if use_cookie else _auth_headers(access_token)
        async with httpx.AsyncClient(timeout=15) as hx:
            r = await hx.get(
                f"{BASE_URL}/api/calendar",
                params={"start": start, "end": end},
                headers=headers,
            )
        if r.status_code == 200:
            return r.json() if isinstance(r.json(), list) else r.json().get("events") or r.json().get("items") or []
        logger.warning("KTalk calendar HTTP %s: %s", r.status_code, r.text[:150])
    except Exception as exc:
        logger.warning("KTalk get_calendar error: %s", exc)
    return []


async def get_conferences_history(access_token: str, limit: int = 20) -> list[dict]:
    """Получить историю недавних конференций."""
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            r = await hx.get(
                f"{BASE_URL}/api/conferencesHistory/recent",
                params={"limit": limit},
                headers=_auth_headers(access_token),
            )
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else data.get("items") or data.get("conferences") or []
    except Exception as exc:
        logger.warning("KTalk conferences history error: %s", exc)
    return []


async def get_favorite_rooms(access_token: str) -> list[dict]:
    """Получить список избранных комнат."""
    try:
        async with httpx.AsyncClient(timeout=10) as hx:
            r = await hx.get(
                f"{BASE_URL}/api/favoriterooms",
                headers=_auth_headers(access_token),
            )
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else data.get("rooms") or []
    except Exception as exc:
        logger.warning("KTalk favorite rooms error: %s", exc)
    return []


async def get_today_meetings(login_id: str, password: str) -> dict:
    """
    Авторизуется и получает встречи на сегодня из KTalk календаря.
    """
    auth = await login(login_id, password)
    if not auth["ok"]:
        return auth

    today = datetime.utcnow()
    start = today.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end   = today.replace(hour=23, minute=59, second=59, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S.999Z")

    events = await get_calendar(auth["token"], start, end)
    return {
        "ok": True,
        "user": auth.get("user", {}),
        "meetings_today": len(events),
        "events": events,
    }


# ── Webhook (отправка уведомлений, без авторизации) ───────────────────────────

async def send_ktalk_notification(
    text: str,
    webhook_url: Optional[str] = None,
    username: str = "AM Hub",
    icon_emoji: str = "🏢",
) -> dict:
    """Отправить уведомление в K.Talk через Incoming Webhook."""
    url = webhook_url or KTALK_WEBHOOK_URL
    if not url:
        return {"ok": False, "error": "KTALK_WEBHOOK_URL не задан"}

    payload = {"text": text, "username": username, "icon_emoji": icon_emoji}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code in (200, 201):
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def send_ktalk_followup(
    client_name: str,
    meeting_date: str,
    meeting_type: str,
    summary: str,
    mood: str,
    aq_tasks: list[dict],
    next_meeting: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> dict:
    """Отправить фолоуап встречи в K.Talk канал."""
    mood_emoji = {"positive": "🟢", "neutral": "🟡", "risk": "🔴"}.get(mood, "🟡")
    type_label = {"checkup": "Чекап", "qbr": "QBR", "urgent": "Экстренная"}.get(meeting_type, "Встреча")

    lines = [f"{mood_emoji} **{type_label} — {client_name}** · {meeting_date}"]
    if summary:
        lines += ["", summary]
    if aq_tasks:
        lines += ["", "**Задачи AnyQuery:**"]
        for t in aq_tasks:
            due = f" (до {t['due_date']})" if t.get("due_date") else ""
            lines.append(f"• {t['text']}{due}")
    if next_meeting:
        lines += ["", f"📆 Следующая встреча: {next_meeting}"]
    lines += ["", "_anyquery AM Hub_"]

    return await send_ktalk_notification(text="\n".join(lines), webhook_url=webhook_url)


async def send_ktalk_digest(stats: dict, webhook_url: Optional[str] = None) -> dict:
    """Отправить дайджест в K.Talk."""
    text = (
        f"📊 **Еженедельный дайджест AM Hub**\n\n"
        f"🔴 Просроченных чекапов: **{stats.get('overdue', 0)}**\n"
        f"🟡 Скоро чекап: **{stats.get('warning', 0)}**\n"
        f"📋 Открытых задач: **{stats.get('open_tasks', 0)}**\n"
        f"👥 Активных менеджеров: **{stats.get('managers_count', 0)}**"
    )
    return await send_ktalk_notification(text=text, webhook_url=webhook_url)


async def test_ktalk_connection(webhook_url: str) -> dict:
    """Проверить подключение к K.Talk."""
    return await send_ktalk_notification(
        text="✅ AM Hub подключён к K.Talk! Уведомления о встречах и чекапах будут приходить сюда.",
        webhook_url=webhook_url,
    )
