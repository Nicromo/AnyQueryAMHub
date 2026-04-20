"""OAuth2 для Time (Mattermost-совместимый).

Based on колеги's work (наработки/time_case_board/backend/oauth_time.py).
Docs: https://docs.time-messenger.ru/integrations/oauth2_service_provider/

Env:
  TIME_OAUTH_CLIENT_ID
  TIME_OAUTH_CLIENT_SECRET
  TIME_OAUTH_REDIRECT_URI
  TIME_BASE_URL (default https://time.tbank.ru)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

TIME_BASE_URL = os.getenv("TIME_BASE_URL", "https://time.tbank.ru").rstrip("/")


def _cfg():
    return {
        "client_id": os.getenv("TIME_OAUTH_CLIENT_ID", ""),
        "client_secret": os.getenv("TIME_OAUTH_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv(
            "TIME_OAUTH_REDIRECT_URI",
            "https://anyqueryamhub-production-9654.up.railway.app/auth/time/callback",
        ),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["client_id"] and c["client_secret"])


def authorize_url(state: str) -> str:
    c = _cfg()
    from urllib.parse import urlencode
    params = {
        "client_id": c["client_id"],
        "response_type": "code",
        "redirect_uri": c["redirect_uri"],
        "state": state,
    }
    return f"{TIME_BASE_URL}/oauth/authorize?{urlencode(params)}"


async def exchange_code(code: str) -> Dict[str, Any]:
    """Обменять authorization_code на access_token + refresh_token."""
    c = _cfg()
    data = {
        "grant_type": "authorization_code",
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "code": code,
        "redirect_uri": c["redirect_uri"],
    }
    async with httpx.AsyncClient(timeout=30) as hx:
        r = await hx.post(
            f"{TIME_BASE_URL}/oauth/access_token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Time OAuth exchange HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


async def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Обновить access_token через refresh_token."""
    c = _cfg()
    data = {
        "grant_type": "refresh_token",
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=30) as hx:
        r = await hx.post(
            f"{TIME_BASE_URL}/oauth/access_token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Time OAuth refresh HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


async def get_me(access_token: str) -> Dict[str, Any]:
    """Получить данные пользователя Time."""
    async with httpx.AsyncClient(timeout=15) as hx:
        r = await hx.get(
            f"{TIME_BASE_URL}/api/v4/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Time users/me HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


async def ensure_fresh_token(user_settings: dict) -> Optional[str]:
    """Проверяет, не истёк ли access_token, при нужде рефрешит и возвращает актуальный.

    Формат хранения в user.settings.tbank_time:
      {
        "access_token": "...",
        "refresh_token": "...",
        "token_type": "bearer",
        "expires_at": 1713600000,     # unix-seconds, когда access_token истекает
        "username": "...", "email": "...", "user_id": "...",
        "support_channel_id": "...",
      }

    Возвращает:
      - новый (или прежний валидный) access_token, либо
      - None если нет ни refresh_token'а, ни действующего access_token.

    Мутирует user_settings in-place при рефреше — вызывающий должен сохранить
    user.settings в БД после вызова.
    """
    tm = user_settings.get("tbank_time", {}) or {}
    access = tm.get("access_token") or tm.get("mmauthtoken") or tm.get("session_cookie")
    exp = tm.get("expires_at") or 0
    now = int(time.time())

    # Если access ещё свежий (более 60с до истечения) — используем
    if access and (exp == 0 or exp - now > 60):
        return access

    # Нужен рефреш
    refresh = tm.get("refresh_token")
    if not refresh:
        return access  # старый токен; пусть вызывающий сам разбирается с 401
    try:
        tok = await refresh_access_token(refresh)
    except Exception as e:
        logger.warning("Time OAuth refresh failed: %s", e)
        return access
    new_access = tok.get("access_token")
    if not new_access:
        return access
    tm["access_token"] = new_access
    if tok.get("refresh_token"):
        tm["refresh_token"] = tok["refresh_token"]
    if tok.get("expires_in"):
        tm["expires_at"] = now + int(tok["expires_in"]) - 30
    tm["token_type"] = tok.get("token_type", "bearer")
    user_settings["tbank_time"] = tm
    return new_access
