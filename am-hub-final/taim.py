"""
1Time / Time (time.tbank.ru) — корпоративный мессенджер Т-Банк.
Основан на Mattermost API v4.

Авторизация: POST /api/v4/users/login → Token header
Последующие запросы: Authorization: Bearer <token>

Если SSO-only (не работает прямой логин) — возвращаем понятную ошибку.
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://time.tbank.ru"
_token_cache: dict[str, dict] = {}  # login → {token, user_id, expires_at}


# ── Auth ──────────────────────────────────────────────────────────────────────

async def login(login_id: str, password: str) -> dict:
    """
    Авторизация через Mattermost API.
    Возвращает {"ok": True, "token": "...", "user_id": "...", "username": "..."}
    или {"ok": False, "error": "..."}.
    """
    if not login_id or not password:
        return {"ok": False, "error": "Нужны логин и пароль"}

    now = datetime.now()
    cached = _token_cache.get(login_id, {})
    if cached.get("token") and cached.get("expires_at") and now < cached["expires_at"]:
        return {"ok": True, **cached}

    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post(
                f"{BASE_URL}/api/v4/users/login",
                json={"login_id": login_id, "password": password},
                headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
            )

        if resp.status_code == 200:
            token = resp.headers.get("Token")
            if not token:
                return {"ok": False, "error": "Нет Token в ответе"}
            user = resp.json()
            result = {
                "ok": True,
                "token": token,
                "user_id": user.get("id", ""),
                "username": user.get("username", ""),
                "email": user.get("email", ""),
                "expires_at": now + timedelta(hours=8),
            }
            _token_cache[login_id] = result
            logger.info("1Time auth OK for %s (username=%s)", login_id, result["username"])
            return result

        body_text = resp.text[:300]
        if resp.status_code == 401:
            # Проверяем — может SSO-only
            try:
                err_body = resp.json()
                if err_body.get("id") == "api.user.login.invalid_credentials_email_username":
                    return {"ok": False, "error": "Неверный логин или пароль"}
                if "sso" in str(err_body).lower() or "saml" in str(err_body).lower():
                    return {"ok": False, "error": "Этот аккаунт использует корпоративный SSO — прямой логин не поддерживается"}
            except Exception:
                pass
            return {"ok": False, "error": f"HTTP 401: {body_text}"}

        return {"ok": False, "error": f"HTTP {resp.status_code}: {body_text}"}

    except Exception as exc:
        logger.warning("1Time login error (%s): %s", login_id, exc)
        return {"ok": False, "error": str(exc)}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }


def invalidate(login_id: str = ""):
    if login_id:
        _token_cache.pop(login_id, None)
    else:
        _token_cache.clear()


# ── API methods ───────────────────────────────────────────────────────────────

async def get_me(token: str) -> dict:
    """Получить данные текущего пользователя."""
    async with httpx.AsyncClient(timeout=10) as hx:
        r = await hx.get(f"{BASE_URL}/api/v4/users/me", headers=_headers(token))
    return r.json() if r.status_code == 200 else {}


async def get_channels(token: str, team_id: str = "") -> list[dict]:
    """Получить каналы пользователя."""
    url = f"{BASE_URL}/api/v4/users/me/channels"
    if team_id:
        url = f"{BASE_URL}/api/v4/users/me/teams/{team_id}/channels"
    async with httpx.AsyncClient(timeout=15) as hx:
        r = await hx.get(url, headers=_headers(token))
    return r.json() if r.status_code == 200 else []


async def get_teams(token: str) -> list[dict]:
    """Получить список команд пользователя."""
    async with httpx.AsyncClient(timeout=10) as hx:
        r = await hx.get(f"{BASE_URL}/api/v4/users/me/teams", headers=_headers(token))
    return r.json() if r.status_code == 200 else []


async def search_posts(token: str, terms: str, team_id: str = "") -> list[dict]:
    """Поиск сообщений."""
    async with httpx.AsyncClient(timeout=15) as hx:
        r = await hx.post(
            f"{BASE_URL}/api/v4/posts/search",
            headers=_headers(token),
            json={"terms": terms, "is_or_search": False, "page": 0, "per_page": 60},
        )
    if r.status_code == 200:
        data = r.json()
        order = data.get("order", [])
        posts = data.get("posts", {})
        return [posts[pid] for pid in order if pid in posts]
    return []


async def get_channel_posts(token: str, channel_id: str, per_page: int = 30) -> list[dict]:
    """Получить последние сообщения из канала."""
    async with httpx.AsyncClient(timeout=15) as hx:
        r = await hx.get(
            f"{BASE_URL}/api/v4/channels/{channel_id}/posts",
            headers=_headers(token),
            params={"per_page": per_page, "page": 0},
        )
    if r.status_code == 200:
        data = r.json()
        order = data.get("order", [])
        posts = data.get("posts", {})
        return [posts[pid] for pid in order if pid in posts]
    return []


async def send_message(token: str, channel_id: str, message: str, root_id: str = "") -> dict:
    """Отправить сообщение в канал."""
    payload: dict = {"channel_id": channel_id, "message": message}
    if root_id:
        payload["root_id"] = root_id
    async with httpx.AsyncClient(timeout=15) as hx:
        r = await hx.post(f"{BASE_URL}/api/v4/posts", headers=_headers(token), json=payload)
    if r.status_code in (200, 201):
        return {"ok": True, "post_id": r.json().get("id")}
    return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


async def get_user_by_email(token: str, email: str) -> dict:
    """Найти пользователя по email."""
    async with httpx.AsyncClient(timeout=10) as hx:
        r = await hx.get(f"{BASE_URL}/api/v4/users/email/{email}", headers=_headers(token))
    return r.json() if r.status_code == 200 else {}


async def create_direct_channel(token: str, my_user_id: str, other_user_id: str) -> dict:
    """Создать или получить существующий DM-канал."""
    async with httpx.AsyncClient(timeout=10) as hx:
        r = await hx.post(
            f"{BASE_URL}/api/v4/channels/direct",
            headers=_headers(token),
            json=[my_user_id, other_user_id],
        )
    return r.json() if r.status_code in (200, 201) else {}


# ── High-level helpers ────────────────────────────────────────────────────────

async def send_dm_to_email(login_id: str, password: str, recipient_email: str, message: str) -> dict:
    """
    Отправить личное сообщение пользователю по email.
    Авторизуется, ищет получателя, создаёт DM-канал, отправляет.
    """
    auth = await login(login_id, password)
    if not auth["ok"]:
        return auth

    token = auth["token"]
    my_id = auth["user_id"]

    recipient = await get_user_by_email(token, recipient_email)
    if not recipient.get("id"):
        return {"ok": False, "error": f"Пользователь {recipient_email} не найден в 1Time"}

    channel = await create_direct_channel(token, my_id, recipient["id"])
    if not channel.get("id"):
        return {"ok": False, "error": "Не удалось создать DM-канал"}

    return await send_message(token, channel["id"], message)


async def get_summary(login_id: str, password: str) -> dict:
    """
    Быстрая сводка: команды, каналы пользователя.
    """
    auth = await login(login_id, password)
    if not auth["ok"]:
        return auth

    token = auth["token"]
    teams = await get_teams(token)
    channels = await get_channels(token)

    return {
        "ok": True,
        "username": auth.get("username"),
        "teams_count": len(teams),
        "channels_count": len(channels),
        "teams": [{"id": t["id"], "name": t.get("display_name", t.get("name"))} for t in teams[:5]],
    }
