"""
Merchrules Integration (Extended)
Получение данных о задачах, встречах, аналитике, чекапах и фиде

Важно: Требует аккаунт ID для получения данных аналитики!
https://merchrules.any-platform.ru/analytics/full?account_id=<ID>
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
import httpx

logger = logging.getLogger(__name__)

MERCHRULES_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
MERCHRULES_LOGIN = os.getenv("MERCHRULES_LOGIN", "")
MERCHRULES_PASSWORD = os.getenv("MERCHRULES_PASSWORD", "")

# Кэш авторизации
_auth_cache: Dict[str, Dict] = {}


async def get_auth_token(login: str = "", password: str = "") -> Optional[str]:
    """
    Получить/обновить токен авторизации
    
    Args:
        login: Меркрулс логин (или используется MERCHRULES_LOGIN из env)
        password: Меркрулс пароль (или используется MERCHRULES_PASSWORD из env)
    
    Returns:
        str: Auth token или None
    """
    _login = login or MERCHRULES_LOGIN
    _password = password or MERCHRULES_PASSWORD

    if not _login or not _password:
        logger.warning("Merchrules credentials not set")
        return None

    # Проверить кэш
    cache_key = _login
    if cache_key in _auth_cache:
        cached = _auth_cache[cache_key]
        if datetime.now() < cached.get("expires_at", datetime.min):
            return cached.get("token")

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{MERCHRULES_URL}/backend-v2/auth/login",
                json={"username": _login, "password": _password},
                timeout=10,
            )

            if resp.status_code == 200:
                body = resp.json()
                token = body.get("token") or body.get("access_token") or body.get("accessToken")
                if token:
                    _auth_cache[cache_key] = {
                        "token": token,
                        "expires_at": datetime.now() + timedelta(hours=1),
                    }
                    logger.info(f"Merchrules auth OK for {_login}")
                    return token

            logger.warning(f"Merchrules auth failed: {resp.status_code}")
            return None

        except Exception as e:
            logger.error(f"Merchrules auth error: {e}")
            return None


def _make_headers(token: str) -> dict:
    """Создать заголовки для Merchrules запроса"""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ============================================================================
# ПОПОЛНИТЬ: Функции для расширенной интеграции
# ============================================================================


async def fetch_account_analytics(account_id: str, token: str = "") -> Dict[str, Any]:
    """
    Получить аналитику аккаунта для Health Score
    
    GET /analytics/full?account_id=<ID>
    
    Args:
        account_id: ID аккаунта в Merchrules
        token: Auth token (или используется кэшированный)
    
    Returns:
        Dict: Аналитика аккаунта с полями:
            {
                "health_score": float,  # 0-100
                "revenue_trend": str,  # up/down/stable
                "activity_level": str,  # high/medium/low
                "open_tasks_count": int,
                ...
            }
    """
    if not token:
        token = await get_auth_token()
    if not token:
        return {}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{MERCHRULES_URL}/analytics/full",
                params={"account_id": account_id},
                headers=_make_headers(token),
                timeout=15,
            )

            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Loaded analytics for account {account_id}")
                return data
            else:
                logger.warning(f"Analytics error: {resp.status_code}")
                return {}

        except Exception as e:
            logger.error(f"Analytics fetch error: {e}")
            return {}


async def fetch_checkups(account_id: str, token: str = "") -> List[Dict[str, Any]]:
    """
    Получить список чекапов (checkups) для аккаунта
    
    GET /checkups?account_id=<ID>&status=overdue,scheduled
    
    Args:
        account_id: ID аккаунта
        token: Auth token
    
    Returns:
        List: Список чекапов с полями:
            {
                "id": str,
                "type": str,  # quarterly/annual/monthly
                "status": str,  # overdue/scheduled/completed
                "date": datetime,
                "assigned_to": str,
            }
    """
    if not token:
        token = await get_auth_token()
    if not token:
        return []

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{MERCHRULES_URL}/checkups",
                params={
                    "account_id": account_id,
                    "status": "overdue,scheduled",
                    "limit": 50,
                },
                headers=_make_headers(token),
                timeout=15,
            )

            if resp.status_code == 200:
                checkups = resp.json().get("data", [])
                logger.info(f"Loaded {len(checkups)} checkups for account {account_id}")
                return checkups
            else:
                logger.warning(f"Checkups error: {resp.status_code}")
                return []

        except Exception as e:
            logger.error(f"Checkups fetch error: {e}")
            return []


async def fetch_feed(account_id: str, token: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    """
    Получить фид активности по аккаунту
    
    GET /feeds?account_id=<ID>&limit=<N>
    
    Args:
        account_id: ID аккаунта
        token: Auth token
        limit: Кол-во записей (default 20)
    
    Returns:
        List: События активности с полями:
            {
                "id": str,
                "type": str,  # task_created/task_updated/meeting/etc
                "description": str,
                "timestamp": datetime,
            }
    """
    if not token:
        token = await get_auth_token()
    if not token:
        return []

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{MERCHRULES_URL}/feeds",
                params={"account_id": account_id, "limit": limit},
                headers=_make_headers(token),
                timeout=15,
            )

            if resp.status_code == 200:
                events = resp.json().get("data", [])
                logger.info(f"Loaded {len(events)} feed events for account {account_id}")
                return events
            else:
                logger.warning(f"Feed error: {resp.status_code}")
                return []

        except Exception as e:
            logger.error(f"Feed fetch error: {e}")
            return []


async def fetch_meetings(account_id: str, token: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """
    Получить встречи для аккаунта
    
    GET /meetings?account_id=<ID>&limit=<N>
    
    Args:
        account_id: ID аккаунта
        token: Auth token
        limit: Кол-во встреч
    
    Returns:
        List: Встречи с полями:
            {
                "id": str,
                "date": datetime,
                "type": str,  # qbr/checkup/kickoff/etc
                "attendees": list,
                "status": str,  # scheduled/completed/cancelled
            }
    """
    if not token:
        token = await get_auth_token()
    if not token:
        return []

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{MERCHRULES_URL}/meetings",
                params={"account_id": account_id, "limit": limit},
                headers=_make_headers(token),
                timeout=15,
            )

            if resp.status_code == 200:
                meetings = resp.json().get("data", [])
                logger.info(f"Loaded {len(meetings)} meetings for account {account_id}")
                return meetings
            else:
                logger.warning(f"Meetings error: {resp.status_code}")
                return []

        except Exception as e:
            logger.error(f"Meetings fetch error: {e}")
            return []


async def fetch_roadmap_tasks(
    account_id: str, token: str = "", status: str = "plan,in_progress,blocked"
) -> List[Dict[str, Any]]:
    """
    Получить задачи из роадмапа для аккаунта
    
    GET /roadmap?account_id=<ID>&status=<STATUS>
    
    Args:
        account_id: ID аккаунта
        token: Auth token
        status: Статусы для фильтра (default: план/в прогрессе/блокирован)
    
    Returns:
        List: Задачи с полями:
            {
                "id": str,
                "title": str,
                "status": str,
                "priority": str,
                "due_date": datetime,
                "assignee": str,
            }
    """
    if not token:
        token = await get_auth_token()
    if not token:
        return []

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{MERCHRULES_URL}/roadmap",
                params={"account_id": account_id, "status": status, "limit": 100},
                headers=_make_headers(token),
                timeout=15,
            )

            if resp.status_code == 200:
                tasks = resp.json().get("data", [])
                logger.info(f"Loaded {len(tasks)} roadmap tasks for account {account_id}")
                return tasks
            else:
                logger.warning(f"Roadmap error: {resp.status_code}")
                return []

        except Exception as e:
            logger.error(f"Roadmap fetch error: {e}")
            return []


async def get_search_settings(account_id: str, token: str = "") -> Dict[str, Any]:
    """
    Получить настройки поиска (фильтры) для аккаунта
    
    GET /search-settings?account_id=<ID>
    
    Args:
        account_id: ID аккаунта
        token: Auth token
    
    Returns:
        Dict: Настройки с фильтрами
            {
                "filters": [...],
                "saved_searches": [...],
            }
    """
    if not token:
        token = await get_auth_token()
    if not token:
        return {}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{MERCHRULES_URL}/search-settings",
                params={"account_id": account_id},
                headers=_make_headers(token),
                timeout=15,
            )

            if resp.status_code == 200:
                settings = resp.json()
                logger.info(f"Loaded search settings for account {account_id}")
                return settings
            else:
                logger.warning(f"Search settings error: {resp.status_code}")
                return {}

        except Exception as e:
            logger.error(f"Search settings error: {e}")
            return {}


async def fetch_feed_processing_status(
    token: str = "",
) -> Dict[str, Any]:
    """
    Получить статус обработки фида
    
    GET /feed-processing
    
    Args:
        token: Auth token
    
    Returns:
        Dict: Статус с информацией о очереди обработки
    """
    if not token:
        token = await get_auth_token()
    if not token:
        return {}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{MERCHRULES_URL}/feed-processing",
                headers=_make_headers(token),
                timeout=15,
            )

            if resp.status_code == 200:
                status = resp.json()
                logger.info("Loaded feed processing status")
                return status
            else:
                logger.warning(f"Feed processing error: {resp.status_code}")
                return {}

        except Exception as e:
            logger.error(f"Feed processing error: {e}")
            return {}


# Bulk operations 

async def sync_all_accounts_data(
    account_ids: List[str], token: str = ""
) -> Dict[str, Dict[str, Any]]:
    """
    Синхронизировать данные для всех аккаунтов (batch operation)
    
    Args:
        account_ids: Список ID аккаунтов
        token: Auth token
    
    Returns:
        Dict: Данные для всех аккаунтов
            {
                "account_id": {
                    "analytics": {...},
                    "tasks": [...],
                    "meetings": [...],
                    "checkups": [...],
                    "feed": [...],
                },
                ...
            }
    """
    if not token:
        token = await get_auth_token()
    if not token:
        return {}

    result = {}

    for account_id in account_ids:
        logger.info(f"Syncing account {account_id}...")

        # Параллельно загружаем все данные
        analytics, tasks, meetings, checkups, feed = await asyncio.gather(
            fetch_account_analytics(account_id, token),
            fetch_roadmap_tasks(account_id, token),
            fetch_meetings(account_id, token),
            fetch_checkups(account_id, token),
            fetch_feed(account_id, token),
            return_exceptions=True,
        )

        result[account_id] = {
            "analytics": analytics if not isinstance(analytics, Exception) else {},
            "tasks": tasks if not isinstance(tasks, Exception) else [],
            "meetings": meetings if not isinstance(meetings, Exception) else [],
            "checkups": checkups if not isinstance(checkups, Exception) else [],
            "feed": feed if not isinstance(feed, Exception) else [],
        }

    return result


if __name__ == "__main__":
    # Test
    import asyncio

    async def test():
        token = await get_auth_token()
        if token:
            print("✅ Auth successful")
        else:
            print("❌ Auth failed")

    asyncio.run(test())
