"""
Интеграция с MerchRules (merchrules.any-platform.ru).
Синхронизирует встречи и задачи из AM Hub → MerchRules после сохранения фолоуапа.
"""
import os
import httpx
from typing import Optional


MERCHRULES_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
MERCHRULES_KEY = os.getenv("MERCHRULES_API_KEY", "")

MEETING_TYPE_MAP = {
    "checkup": "checkup",
    "qbr": "qbr",
    "urgent": "urgent",
}

MOOD_MAP = {
    "positive": "positive",
    "neutral": "neutral",
    "risk": "risk",
}


async def sync_meeting_to_merchrules(
    client_name: str,
    meeting_date: str,
    meeting_type: str,
    summary: str,
    mood: str,
    next_meeting: Optional[str],
    aq_tasks: list[dict],
    client_tasks: list[dict],
) -> dict:
    """
    Отправляет данные встречи в MerchRules API.
    Возвращает {"ok": True, "meeting_id": "..."} или {"ok": False, "error": "..."}

    MerchRules ожидает POST /api/meetings с JSON-телом.
    Если API-ключ не задан — пропускаем синхронизацию тихо.
    """
    if not MERCHRULES_KEY:
        return {"ok": False, "error": "MERCHRULES_API_KEY не задан — синхронизация пропущена"}

    payload = {
        "client_name": client_name,
        "meeting_date": meeting_date,
        "meeting_type": MEETING_TYPE_MAP.get(meeting_type, meeting_type),
        "summary": summary,
        "mood": MOOD_MAP.get(mood, mood),
        "next_meeting": next_meeting,
        "tasks": [
            {
                "owner": t["owner"],
                "text": t["text"],
                "due_date": t.get("due_date"),
                "status": "open",
            }
            for t in (aq_tasks + client_tasks)
        ],
    }

    headers = {
        "Authorization": f"Bearer {MERCHRULES_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{MERCHRULES_URL}/api/meetings",
                json=payload,
                headers=headers,
            )
        if resp.status_code in (200, 201):
            data = resp.json()
            return {"ok": True, "meeting_id": data.get("id", "")}
        else:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def sync_roadmap_task(
    client_name: str,
    task_text: str,
    due_date: Optional[str],
    owner: str,
) -> dict:
    """
    Создаёт задачу в роадмапе MerchRules.
    POST /api/roadmap/tasks
    """
    if not MERCHRULES_KEY:
        return {"ok": False, "error": "нет ключа"}

    payload = {
        "client_name": client_name,
        "title": task_text,
        "due_date": due_date,
        "owner": owner,
        "status": "plan",
    }

    headers = {
        "Authorization": f"Bearer {MERCHRULES_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{MERCHRULES_URL}/api/roadmap/tasks",
                json=payload,
                headers=headers,
            )
        return {"ok": resp.status_code in (200, 201)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
