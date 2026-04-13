"""
Ktalk/Tbank Integration
Получение данных о встречах, транскрипций и записей

https://tbank.ktalk.ru - встречи
https://tbank.ktalk.ru/content/artifacts - записи

Требуется: Tbank API token (если есть API) или парсинг веб-интерфейса
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import httpx

logger = logging.getLogger(__name__)

# Configuration
KTALK_BASE_URL = os.getenv("KTALK_BASE_URL", "https://tbank.ktalk.ru")
KTALK_API_TOKEN = os.getenv("KTALK_API_TOKEN", "")

CACHE_TTL_SECONDS = 3600  # 1 час

# Cache
_meetings_cache: Dict[str, Any] = {}
_artifacts_cache: Dict[str, Any] = {}


def _headers() -> dict:
    """Ktalk API headers"""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AM Hub Client",
    }
    if KTALK_API_TOKEN:
        headers["Authorization"] = f"Bearer {KTALK_API_TOKEN}"
    return headers


# ============================================================================
# PLACEHOLDER: Функции для интеграции Ktalk
# ============================================================================


async def get_meetings(
    account_id: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """
    Получить список встреч из Ktalk
    
    Args:
        account_id: Фильтр по аккаунту (optional)
        date_from: Начало периода (optional)
        date_to: Конец периода (optional)
        use_cache: Использовать кэш (default: True)
    
    Returns:
        List: Встречи с полями:
            {
                "id": str,
                "title": str,
                "date": datetime,
                "duration": int,  # минуты
                "attendees": list,
                "recording_url": str,  # если есть
                "status": str,  # scheduled/in_progress/completed/cancelled
            }
    """
    cache_key = f"meetings_{account_id or 'all'}"

    if use_cache and cache_key in _meetings_cache:
        cached = _meetings_cache[cache_key]
        if datetime.now() - cached["timestamp"] < timedelta(seconds=CACHE_TTL_SECONDS):
            return cached["data"]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Попробовать получить встречи через API
            params = {}
            if date_from:
                params["date_from"] = date_from.isoformat()
            if date_to:
                params["date_to"] = date_to.isoformat()
            if account_id:
                params["account_id"] = account_id

            response = await client.get(
                f"{KTALK_BASE_URL}/api/v1/meetings",
                headers=_headers(),
                params=params,
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()
                meetings = data.get("meetings", [])
                
                # Нормализовать данные
                normalized = []
                for m in meetings:
                    normalized.append({
                        "id": m.get("id"),
                        "title": m.get("title", ""),
                        "date": datetime.fromisoformat(m["date"]) if "date" in m else None,
                        "duration": m.get("duration", 0),
                        "attendees": m.get("attendees", []),
                        "recording_url": m.get("recording_url"),
                        "status": m.get("status", "completed"),
                    })
                
                _meetings_cache[cache_key] = {
                    "data": normalized,
                    "timestamp": datetime.now(),
                }
                
                logger.info(f"✅ Loaded {len(normalized)} meetings from Ktalk")
                return normalized
            else:
                logger.warning(f"Ktalk API error: {response.status_code}")
    
    except Exception as e:
        logger.error(f"❌ Failed to fetch Ktalk meetings: {e}")

    # Fallback: возвращаем пустой список
    return []


async def get_meeting_recording(meeting_id: str) -> Optional[str]:
    """
    Получить URL записи встречи
    
    Args:
        meeting_id: ID встречи
    
    Returns:
        str: URL на запись или None
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{KTALK_BASE_URL}/api/v1/meetings/{meeting_id}/recording",
                headers=_headers(),
                timeout=10,
            )
            
            if response.status_code == 200:
                data = response.json()
                recording_url = data.get("url")
                logger.info(f"✅ Got recording for meeting {meeting_id}")
                return recording_url
            else:
                logger.warning(f"No recording for meeting {meeting_id}")
    
    except Exception as e:
        logger.error(f"❌ Failed to fetch recording: {e}")
    
    return None


async def get_meeting_transcript(meeting_id: str) -> Optional[Dict[str, Any]]:
    """
    Получить транскрипцию встречи
    
    Args:
        meeting_id: ID встречи
    
    Returns:
        Dict: Транскрипция с полями:
            {
                "id": str,
                "text": str,  # полный текст
                "segments": [  # по спикерам
                    {
                        "speaker": str,
                        "text": str,
                        "timestamp": int,  # секунды
                    },
                    ...
                ],
                "language": str,
                "duration": int,  # секунды
            }
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{KTALK_BASE_URL}/api/v1/meetings/{meeting_id}/transcript",
                headers=_headers(),
                timeout=10,
            )
            
            if response.status_code == 200:
                transcript = response.json()
                logger.info(f"✅ Got transcript for meeting {meeting_id}")
                return transcript
            else:
                logger.warning(f"No transcript for meeting {meeting_id}")
    
    except Exception as e:
        logger.error(f"❌ Failed to fetch transcript: {e}")
    
    return None


async def sync_meetings_for_client(
    account_id: str, merchrules_meetings: List[Dict] = None
) -> List[Dict[str, Any]]:
    """
    Синхронизировать встречи Ktalk с Merchrules встречами
    
    Логика:
    1. Получить встречи из Ktalk
    2. Получить встречи из Merchrules (параметр)
    3. Маппировать и обновить встречи в БД
    4. Загрузить транскрипции для новых встреч
    
    Args:
        account_id: ID аккаунта
        merchrules_meetings: Встречи из Merchrules (для маппирования)
    
    Returns:
        List: Синхронизированные встречи
    """
    logger.info(f"🔄 Syncing Ktalk meetings for account {account_id}")
    
    try:
        # Получить встречи из Ktalk за последние 90 дней
        date_from = datetime.now() - timedelta(days=90)
        ktalk_meetings = await get_meetings(
            account_id=account_id,
            date_from=date_from,
            use_cache=False,
        )
        
        # Получить транскрипции для встреч без них
        for meeting in ktalk_meetings:
            if not meeting.get("transcript"):
                transcript = await get_meeting_transcript(meeting["id"])
                if transcript:
                    meeting["transcript"] = transcript["text"]
                    meeting["transcript_segments"] = transcript.get("segments", [])
            
            if not meeting.get("recording_url"):
                recording_url = await get_meeting_recording(meeting["id"])
                if recording_url:
                    meeting["recording_url"] = recording_url
        
        logger.info(f"✅ Synced {len(ktalk_meetings)} meetings from Ktalk")
        return ktalk_meetings
    
    except Exception as e:
        logger.error(f"❌ Failed to sync Ktalk meetings: {e}")
        return []


class KtalkWebScraper:
    """Парсер веб-интерфейса Ktalk (если API недоступен)"""

    BASE_URL = "https://tbank.ktalk.ru"

    async def login(self, username: str, password: str) -> bool:
        """Авторизоваться в Ktalk"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.BASE_URL}/login",
                    data={
                        "username": username,
                        "password": password,
                    },
                    timeout=10,
                )
                
                if response.status_code == 200:
                    logger.info("✅ Ktalk login successful")
                    return True
                else:
                    logger.warning("❌ Ktalk login failed")
                    return False
        
        except Exception as e:
            logger.error(f"❌ Ktalk login error: {e}")
            return False

    async def get_meetings_page(self, page: int = 1) -> List[Dict]:
        """Получить встречи со страницы (парсинг HTML)"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/meetings",
                    params={"page": page},
                    timeout=10,
                )
                
                if response.status_code == 200:
                    # Здесь нужно парсить HTML с использованием BeautifulSoup
                    # Для примера возвращаем пустой список
                    logger.info(f"📄 Fetched meetings page {page}")
                    return []
        
        except Exception as e:
            logger.error(f"❌ Failed to fetch meetings page: {e}")
        
        return []

    async def get_meeting_details(self, meeting_id: str) -> Dict:
        """Получить детали встречи (парсинг HTML)"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/meetings/{meeting_id}",
                    timeout=10,
                )
                
                if response.status_code == 200:
                    # Здесь нужно парсить HTML
                    logger.info(f"📋 Fetched details for meeting {meeting_id}")
                    return {}
        
        except Exception as e:
            logger.error(f"❌ Failed to fetch meeting details: {e}")
        
        return {}

    async def download_recording(self, meeting_id: str, output_path: str) -> bool:
        """Скачать запись встречи"""
        try:
            recording_url = await get_meeting_recording(meeting_id)
            
            if not recording_url:
                logger.warning(f"No recording URL for meeting {meeting_id}")
                return False
            
            async with httpx.AsyncClient() as client:
                response = await client.get(recording_url, timeout=30)
                
                if response.status_code == 200:
                    with open(output_path, "wb") as f:
                        f.write(response.content)
                    logger.info(f"✅ Downloaded recording to {output_path}")
                    return True
        
        except Exception as e:
            logger.error(f"❌ Failed to download recording: {e}")
        
        return False


if __name__ == "__main__":
    import asyncio

    async def test():
        meetings = await get_meetings()
        print(f"Loaded {len(meetings)} meetings")

    asyncio.run(test())
