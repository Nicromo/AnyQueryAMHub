"""
integrations/outlook.py — Microsoft Outlook/Exchange через Graph API.

Переменные окружения:
  OUTLOOK_CLIENT_ID      — Azure App Registration client_id
  OUTLOOK_CLIENT_SECRET  — client secret
  OUTLOOK_TENANT_ID      — tenant_id (или "common" для мультитенант)
  OUTLOOK_USER_EMAIL     — UPN пользователя, чей календарь читаем
                           (для делегированного доступа — пусто, берём /me)

Scope: Calendars.Read (application) или Calendars.Read (delegated).
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CLIENT_ID = os.getenv("OUTLOOK_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("OUTLOOK_CLIENT_SECRET", "")
TENANT_ID = os.getenv("OUTLOOK_TENANT_ID", "common")
USER_EMAIL = os.getenv("OUTLOOK_USER_EMAIL", "")  # пусто → /me

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

_token_cache: dict = {}


async def _get_token() -> Optional[str]:
    """Получить access_token через client_credentials flow."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return None

    now = datetime.now(timezone.utc)
    cached = _token_cache.get("token")
    expires_at = _token_cache.get("expires_at")
    if cached and expires_at and now < expires_at:
        return cached

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(TOKEN_URL, data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
            })
        if resp.status_code == 200:
            data = resp.json()
            token = data["access_token"]
            expires_in = int(data.get("expires_in", 3600))
            _token_cache["token"] = token
            _token_cache["expires_at"] = now + timedelta(seconds=expires_in - 60)
            logger.info("✅ Outlook token refreshed")
            return token
        else:
            logger.error(f"❌ Outlook token error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Outlook token exception: {e}")

    return None


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _calendar_base() -> str:
    """URL основы для запросов к календарю."""
    if USER_EMAIL:
        return f"{GRAPH_BASE}/users/{USER_EMAIL}"
    return f"{GRAPH_BASE}/me"


def _normalize_event(e: dict) -> dict:
    """Привести событие Graph API к единому формату."""
    start_raw = e.get("start", {}).get("dateTime", "")
    end_raw = e.get("end", {}).get("dateTime", "")

    def parse_dt(s: str) -> Optional[datetime]:
        if not s:
            return None
        try:
            # Graph возвращает без таймзоны, но в UTC
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None)  # храним naive UTC
        except Exception:
            return None

    start = parse_dt(start_raw)
    end = parse_dt(end_raw)

    attendees = []
    for a in e.get("attendees", []):
        em = a.get("emailAddress", {})
        attendees.append({
            "name": em.get("name", ""),
            "email": em.get("address", ""),
            "response": a.get("status", {}).get("response", ""),
        })

    organizer_em = e.get("organizer", {}).get("emailAddress", {})

    # Определяем тип встречи по теме/категориям
    subject = (e.get("subject") or "").lower()
    categories = [c.lower() for c in e.get("categories", [])]

    meeting_type = "meeting"
    if any(k in subject for k in ["qbr", "квартальный", "quarterly"]):
        meeting_type = "qbr"
    elif any(k in subject for k in ["онбординг", "onboarding", "кикофф", "kickoff"]):
        meeting_type = "onboarding"
    elif any(k in subject for k in ["чекап", "checkup", "check-up"]):
        meeting_type = "checkup"
    elif any(k in subject for k in ["апсейл", "upsell", "upsell"]):
        meeting_type = "upsell"
    elif any(k in subject for k in ["дауnsейл", "downsell", "downgrade"]):
        meeting_type = "downsell"
    elif any(k in subject for k in ["синк", "sync", "синхрон"]):
        meeting_type = "sync"

    return {
        "external_id": e.get("id", ""),
        "title": e.get("subject", ""),
        "start": start,
        "end": end,
        "meeting_type": meeting_type,
        "organizer_name": organizer_em.get("name", ""),
        "organizer_email": organizer_em.get("address", ""),
        "attendees": attendees,
        "location": e.get("location", {}).get("displayName", ""),
        "online_url": (
            e.get("onlineMeeting", {}) or {}
        ).get("joinUrl", ""),
        "is_cancelled": e.get("isCancelled", False),
        "source": "outlook",
    }


async def get_calendar_events(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list[dict]:
    """
    Получить события из Outlook-календаря за период.
    По умолчанию — ближайшие 14 дней.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        return []

    token = await _get_token()
    if not token:
        return []

    if date_from is None:
        date_from = datetime.utcnow()
    if date_to is None:
        date_to = date_from + timedelta(days=14)

    # Graph API calendarView — возвращает события с учётом повторов
    url = f"{_calendar_base()}/calendarView"
    params = {
        "startDateTime": date_from.strftime("%Y-%m-%dT%H:%M:%S"),
        "endDateTime": date_to.strftime("%Y-%m-%dT%H:%M:%S"),
        "$select": "id,subject,start,end,attendees,organizer,location,onlineMeeting,isCancelled,categories",
        "$orderby": "start/dateTime",
        "$top": 100,
    }

    events = []
    next_url: Optional[str] = url

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            while next_url:
                if next_url == url:
                    resp = await client.get(next_url, headers=_headers(token), params=params)
                else:
                    resp = await client.get(next_url, headers=_headers(token))

                if resp.status_code != 200:
                    logger.error(f"❌ Outlook calendar error: {resp.status_code} {resp.text[:300]}")
                    break

                data = resp.json()
                for e in data.get("value", []):
                    normalized = _normalize_event(e)
                    if not normalized["is_cancelled"]:
                        events.append(normalized)

                next_url = data.get("@odata.nextLink")

        logger.info(f"✅ Outlook: loaded {len(events)} events")
    except Exception as e:
        logger.error(f"❌ Outlook get_calendar_events exception: {e}")

    return events


async def test_connection() -> dict:
    """Проверить подключение к Outlook."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return {"ok": False, "error": "OUTLOOK_CLIENT_ID/SECRET not set"}

    token = await _get_token()
    if not token:
        return {"ok": False, "error": "Failed to get token"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            url = f"{_calendar_base()}/calendar"
            resp = await client.get(url, headers=_headers(token))

        if resp.status_code == 200:
            data = resp.json()
            return {"ok": True, "calendar": data.get("name", ""), "email": USER_EMAIL}
        else:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
