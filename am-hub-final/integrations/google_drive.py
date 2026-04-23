"""
Google Drive Integration
OAuth2-авторизация и работа с файлами через Google Drive API v3

Конфигурация через переменные окружения:
  GOOGLE_CLIENT_ID     - OAuth2 client ID
  GOOGLE_CLIENT_SECRET - OAuth2 client secret
  GOOGLE_REDIRECT_URI  - URI для редиректа после авторизации
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE_API = "https://www.googleapis.com/drive/v3"

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)


# ── OAuth2 flow ───────────────────────────────────────────────────────────────

def build_auth_url(state: str = "") -> Optional[str]:
    """Сформировать URL для OAuth2-авторизации пользователя."""
    if not is_configured():
        logger.warning("Google Drive not configured: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI missing")
        return None

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state

    return f"{_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> Optional[Dict[str, Any]]:
    """
    Обменять authorization code на токены.

    Returns:
        {"access_token": str, "refresh_token": str, "token_expiry": str (ISO)}
        или None при ошибке.
    """
    if not is_configured():
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )

        if resp.status_code != 200:
            logger.warning(f"Google token exchange error: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        expiry = _expiry_from_seconds(data.get("expires_in", 3600))
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "token_expiry": expiry,
        }

    except Exception as e:
        logger.error(f"Google exchange_code error: {e}")
        return None


async def refresh_access_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """
    Обновить access token по refresh token.

    Returns:
        {"access_token": str, "token_expiry": str} или None.
    """
    if not is_configured() or not refresh_token:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                },
            )

        if resp.status_code != 200:
            logger.warning(f"Google token refresh error: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        return {
            "access_token": data.get("access_token", ""),
            "token_expiry": _expiry_from_seconds(data.get("expires_in", 3600)),
        }

    except Exception as e:
        logger.error(f"Google refresh_access_token error: {e}")
        return None


def store_tokens(user_settings: Dict[str, Any], tokens: Dict[str, Any]) -> None:
    """Сохранить токены в user.settings["google"]."""
    if "google" not in user_settings:
        user_settings["google"] = {}

    if "access_token" in tokens:
        user_settings["google"]["access_token"] = tokens["access_token"]
    if "refresh_token" in tokens:
        user_settings["google"]["refresh_token"] = tokens["refresh_token"]
    if "token_expiry" in tokens:
        user_settings["google"]["token_expiry"] = tokens["token_expiry"]


# ── Token management ──────────────────────────────────────────────────────────

def _expiry_from_seconds(expires_in: int) -> str:
    from datetime import timedelta
    expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in - 60)
    return expiry.isoformat()


def _is_token_expired(token_expiry: Optional[str]) -> bool:
    if not token_expiry:
        return True
    try:
        expiry = datetime.fromisoformat(token_expiry)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return datetime.now(tz=timezone.utc) >= expiry
    except Exception:
        return True


async def _get_valid_token(user_settings: Dict[str, Any]) -> Optional[str]:
    """
    Вернуть действующий access token, при необходимости обновив через refresh token.
    Обновлённые токены записываются обратно в user_settings.
    """
    google = user_settings.get("google", {})
    access_token = google.get("access_token", "")
    refresh_token = google.get("refresh_token", "")
    token_expiry = google.get("token_expiry")

    if access_token and not _is_token_expired(token_expiry):
        return access_token

    if not refresh_token:
        logger.warning("Google Drive: no refresh token, re-authorization required")
        return None

    refreshed = await refresh_access_token(refresh_token)
    if not refreshed:
        return None

    store_tokens(user_settings, refreshed)
    return refreshed.get("access_token")


def _auth_headers(access_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


# ── Drive API calls ───────────────────────────────────────────────────────────

async def list_files(
    user_settings: Dict[str, Any],
    folder_id: Optional[str] = None,
    query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Получить список файлов из Google Drive.

    Args:
        user_settings: Настройки пользователя (содержат google.access_token и т.д.)
        folder_id:     ID папки для фильтрации (optional)
        query:         Дополнительный q-фильтр Drive API (optional)

    Returns:
        List файлов: [{"id", "name", "mimeType", "modifiedTime", "size", "webViewLink"}]
    """
    token = await _get_valid_token(user_settings)
    if not token:
        return []

    parts = ["trashed = false"]
    if folder_id:
        parts.append(f"'{folder_id}' in parents")
    if query:
        parts.append(query)

    params = {
        "q": " and ".join(parts),
        "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink)",
        "pageSize": 100,
        "orderBy": "modifiedTime desc",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_DRIVE_API}/files",
                headers=_auth_headers(token),
                params=params,
            )

        if resp.status_code == 401:
            logger.warning("Google Drive: token rejected, re-authorization needed")
            return []

        if resp.status_code != 200:
            logger.warning(f"Google Drive list_files error: {resp.status_code} {resp.text[:200]}")
            return []

        return resp.json().get("files", [])

    except Exception as e:
        logger.error(f"Google Drive list_files error: {e}")
        return []


async def search_files(
    user_settings: Dict[str, Any],
    query: str,
) -> List[Dict[str, Any]]:
    """
    Полнотекстовый поиск по Drive.

    Args:
        user_settings: Настройки пользователя
        query:         Строка поиска (fullText contains 'query')

    Returns:
        List файлов: [{"id", "name", "mimeType", "modifiedTime", "size", "webViewLink"}]
    """
    safe_query = query.replace("'", "\\'")
    drive_query = f"fullText contains '{safe_query}' and trashed = false"
    return await list_files(user_settings, query=drive_query)


async def get_file_metadata(
    user_settings: Dict[str, Any],
    file_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Получить метаданные конкретного файла.

    Returns:
        {"id", "name", "mimeType", "modifiedTime", "createdTime", "size",
         "webViewLink", "owners", "parents"} или None.
    """
    token = await _get_valid_token(user_settings)
    if not token:
        return None

    fields = "id,name,mimeType,modifiedTime,createdTime,size,webViewLink,owners,parents"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_DRIVE_API}/files/{file_id}",
                headers=_auth_headers(token),
                params={"fields": fields},
            )

        if resp.status_code == 404:
            logger.info(f"Google Drive: file {file_id} not found")
            return None

        if resp.status_code != 200:
            logger.warning(f"Google Drive get_file_metadata error: {resp.status_code} {resp.text[:200]}")
            return None

        return resp.json()

    except Exception as e:
        logger.error(f"Google Drive get_file_metadata error: {e}")
        return None


async def create_file_link(
    user_settings: Dict[str, Any],
    file_id: str,
    client_id: Optional[str] = None,
) -> Optional[str]:
    """
    Получить публично доступную ссылку на файл (webViewLink).
    Для внутреннего использования возвращает существующую ссылку без изменения permissions.

    Args:
        user_settings: Настройки пользователя
        file_id:       ID файла в Drive
        client_id:     ID клиента (для логирования, не используется в запросе)

    Returns:
        URL-строка или None.
    """
    meta = await get_file_metadata(user_settings, file_id)
    if not meta:
        return None

    link = meta.get("webViewLink")
    if link:
        logger.info(f"Google Drive link for file {file_id}" + (f" (client {client_id})" if client_id else ""))
    return link
