"""
Контур.Толк (Ktalk) Integration
Получение данных о встречах, транскрипций и записей через официальное API

Docs: https://docs.ktalk.ru/
API Key: панель администрирования → API-ключи
Space: ваш домен (например "company" для company.ktalk.ru)
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# ── Настройки ─────────────────────────────────────────────────────────────────

KTALK_SPACE = os.getenv("KTALK_SPACE", "")  # e.g. "company" для company.ktalk.ru
KTALK_API_TOKEN = os.getenv("KTALK_API_TOKEN", "")
KTALK_BASE_URL = f"https://{KTALK_SPACE}.ktalk.ru" if KTALK_SPACE else ""

CACHE_TTL_SECONDS = 3600  # 1 час

# Кэш
_events_cache: Dict[str, Any] = {}
_transcripts_cache: Dict[str, Any] = {}


def _headers() -> dict:
    """Ktalk API headers — API-ключ в X-Auth-Token."""
    headers = {"Content-Type": "application/json"}
    if KTALK_API_TOKEN:
        headers["X-Auth-Token"] = KTALK_API_TOKEN
    return headers


# ── Встречи (Events) ──────────────────────────────────────────────────────────

async def get_events(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 100,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """
    Получить список встреч (событий) из Контур.Толк.

    Returns:
        List событий:
        {
            "id": str,
            "title": str,
            "start": datetime,
            "end": datetime,
            "status": str,  # scheduled/in_progress/completed/cancelled
            "room_name": str,
            "organizer": {"name": str, "email": str},
            "participants": [{"name": str, "email": str}],
            "recording_available": bool,
        }
    """
    if not KTALK_BASE_URL or not KTALK_API_TOKEN:
        return []

    cache_key = f"events_{date_from}_{date_to}"
    if use_cache and cache_key in _events_cache:
        cached = _events_cache[cache_key]
        if datetime.now() - cached["timestamp"] < timedelta(seconds=CACHE_TTL_SECONDS):
            return cached["data"]

    try:
        params = {"limit": limit, "withCanceled": "false"}
        if date_from:
            params["dateFrom"] = date_from.isoformat()
        if date_to:
            params["dateTo"] = date_to.isoformat()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KTALK_BASE_URL}/api/v1/spaces/{KTALK_SPACE}/events",
                headers=_headers(),
                params=params,
            )

            if resp.status_code == 200:
                data = resp.json()
                events = data.get("events") or data.get("items") or []
                normalized = []

                for e in events:
                    normalized.append({
                        "id": e.get("id", ""),
                        "title": e.get("title", "") or e.get("name", ""),
                        "start": e.get("start") or e.get("startDate"),
                        "end": e.get("end") or e.get("endDate"),
                        "status": e.get("status", "scheduled"),
                        "room_name": e.get("room", {}).get("name", ""),
                        "organizer": e.get("organizer", {}),
                        "participants": e.get("participants", []),
                        "recording_available": e.get("recordingAvailable", False),
                    })

                _events_cache[cache_key] = {"data": normalized, "timestamp": datetime.now()}
                logger.info(f"✅ Loaded {len(normalized)} events from Ktalk")
                return normalized

            else:
                logger.warning(f"Ktalk events API error: {resp.status_code} {resp.text[:200]}")

    except Exception as e:
        logger.error(f"❌ Failed to fetch Ktalk events: {e}")

    return []


# ── Транскрипция ──────────────────────────────────────────────────────────────

async def get_transcript(event_id: str) -> Optional[Dict[str, Any]]:
    """
    Получить транскрипцию встречи.

    Returns:
        {
            "text": str,           # полный текст
            "segments": [          # по спикерам
                {"speaker": str, "text": str, "start_ms": int, "end_ms": int}
            ],
            "language": str,
        }
    """
    if not KTALK_BASE_URL or not KTALK_API_TOKEN:
        return None

    cache_key = f"transcript_{event_id}"
    if cache_key in _transcripts_cache:
        return _transcripts_cache[cache_key]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{KTALK_BASE_URL}/api/v1/spaces/{KTALK_SPACE}/events/{event_id}/transcript",
                headers=_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                result = {
                    "text": data.get("text", ""),
                    "segments": data.get("segments", []),
                    "language": data.get("language", "ru"),
                }
                _transcripts_cache[cache_key] = result
                logger.info(f"✅ Got transcript for event {event_id}")
                return result
            elif resp.status_code == 404:
                logger.info(f"No transcript for event {event_id}")
            else:
                logger.warning(f"Ktalk transcript API error: {resp.status_code}")

    except Exception as e:
        logger.error(f"❌ Failed to fetch transcript: {e}")

    return None


# ── Запись встречи ────────────────────────────────────────────────────────────

async def get_recording_url(event_id: str) -> Optional[str]:
    """Получить URL записи встречи."""
    if not KTALK_BASE_URL or not KTALK_API_TOKEN:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KTALK_BASE_URL}/api/v1/spaces/{KTALK_SPACE}/events/{event_id}/recordings",
                headers=_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                recordings = data.get("recordings") or data.get("items") or []
                if recordings:
                    url = recordings[0].get("url") or recordings[0].get("downloadUrl", "")
                    logger.info(f"✅ Got recording URL for event {event_id}")
                    return url

    except Exception as e:
        logger.error(f"❌ Failed to fetch recording: {e}")

    return None


# ── Комнаты ───────────────────────────────────────────────────────────────────

async def get_rooms() -> List[Dict[str, Any]]:
    """Получить список комнат (переговорок)."""
    if not KTALK_BASE_URL or not KTALK_API_TOKEN:
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KTALK_BASE_URL}/api/v1/spaces/{KTALK_SPACE}/rooms",
                headers=_headers(),
            )

            if resp.status_code == 200:
                data = resp.json()
                rooms = data.get("rooms") or data.get("items") or []
                return [{"id": r.get("id"), "name": r.get("name")} for r in rooms]

    except Exception as e:
        logger.error(f"❌ Failed to fetch rooms: {e}")

    return []


# ── Пользователи ──────────────────────────────────────────────────────────────

async def get_users() -> List[Dict[str, Any]]:
    """Получить список пользователей пространства."""
    if not KTALK_BASE_URL or not KTALK_API_TOKEN:
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KTALK_BASE_URL}/api/v1/spaces/{KTALK_SPACE}/users",
                headers=_headers(),
                params={"limit": 200},
            )

            if resp.status_code == 200:
                data = resp.json()
                users = data.get("users") or data.get("items") or []
                return [{
                    "key": u.get("key"),
                    "name": u.get("name", ""),
                    "email": u.get("email", ""),
                    "active": u.get("active", False),
                } for u in users]

    except Exception as e:
        logger.error(f"❌ Failed to fetch users: {e}")

    return []


# ── Аудит-лог ─────────────────────────────────────────────────────────────────

async def get_audit_log(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Получить аудит-лог действий пользователей.

    eventType: login, logout, userRoleChanged, updateUser, addRole, ...
    """
    if not KTALK_BASE_URL or not KTALK_API_TOKEN:
        return []

    try:
        params = {"limit": limit}
        if date_from:
            params["dateFrom"] = date_from.isoformat()
        if date_to:
            params["dateTo"] = date_to.isoformat()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KTALK_BASE_URL}/api/v1/spaces/{KTALK_SPACE}/audit-log",
                headers=_headers(),
                params=params,
            )

            if resp.status_code == 200:
                data = resp.json()
                return data.get("events") or data.get("items") or []

    except Exception as e:
        logger.error(f"❌ Failed to fetch audit log: {e}")

    return []


# ── Sync helper ───────────────────────────────────────────────────────────────

async def sync_meetings_for_client(
    client_name: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Синхронизировать встречи Толка для конкретного клиента.
    Ищет встречи где клиент упомянут в названии или среди участников.

    Returns:
        {
            "meetings": [...],
            "total": int,
        }
    """
    events = await get_events(date_from=date_from, date_to=date_to, limit=200)

    # Фильтруем по имени клиента
    client_lower = client_name.lower()
    client_meetings = []

    for e in events:
        # Проверяем название встречи
        if client_lower in e.get("title", "").lower():
            client_meetings.append(e)
            continue

        # Проверяем участников
        for p in e.get("participants", []):
            if client_lower in p.get("name", "").lower() or client_lower in p.get("email", "").lower():
                client_meetings.append(e)
                break

    # Добавляем транскрипции для встреч с записью
    for m in client_meetings:
        if m.get("recording_available"):
            transcript = await get_transcript(m["id"])
            if transcript:
                m["transcript"] = transcript["text"]
                m["transcript_segments"] = transcript.get("segments", [])

    return {"meetings": client_meetings, "total": len(client_meetings)}


def invalidate_cache():
    """Сбросить весь кэш."""
    _events_cache.clear()
    _transcripts_cache.clear()


if __name__ == "__main__":
    import asyncio

    async def test():
        events = await get_events()
        print(f"Loaded {len(events)} events")
        if events:
            print(f"First event: {events[0].get('title')}")

    asyncio.run(test())
