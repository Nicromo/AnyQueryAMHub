"""
Интеграция с видеосервисом Ktalk (Tinkoff).
Генерация ссылок на комнаты, поиск записей встреч.
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

KTALK_URL = os.getenv("KTALK_URL", "https://tbank.ktalk.ru")


class KtalkClient:
    """
    Клиент для работы с Ktalk (видеовстречи Tinkoff).
    """
    
    def __init__(self, base_url: str = KTALK_URL):
        self.base_url = base_url
    
    def generate_meeting_link(self, client_name: str, am_name: Optional[str] = None) -> str:
        """
        Сгенерировать ссылку на создание новой комнаты для встречи с клиентом.
        
        Args:
            client_name: Название клиента
            am_name: Имя аккаунт-менеджера
        
        Returns:
            Прямая ссылка на создание комнаты
        """
        # Формируем тему встречи
        topic = f"Встреча с {client_name}"
        if am_name:
            topic += f" | {am_name}"
        
        # Кодируем для URL
        encoded_topic = quote(topic)
        
        # Ссылка на создание новой комнаты
        return f"{self.base_url}/new?topic={encoded_topic}"
    
    def generate_client_room_url(self, client_id: str, client_name: str) -> str:
        """
        Сгенерировать постоянную ссылку на комнату клиента (если используется).
        
        Args:
            client_id: Уникальный ID клиента
            client_name: Название клиента
        
        Returns:
            Ссылка на комнату
        """
        # Если у вас используется паттерн с постоянными комнатами
        # Например: /room/{client_id} или /c/{client_name}
        encoded_name = quote(client_name.replace(" ", "_"))
        return f"{self.base_url}/c/{encoded_name}"
    
    async def search_artifacts(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int = 10,
        days_back: int = 90,
    ) -> List[Dict]:
        """
        Поиск записей встреч (артефактов) по названию клиента.
        
        Args:
            query: Поисковый запрос (название клиента)
            limit: Максимальное количество результатов
            days_back: За сколько дней искать
        
        Returns:
            Список найденных записей
        """
        from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        
        try:
            # Эндпоинт поиска артефактов
            resp = await client.get(
                f"{self.base_url}/content/artifacts/search",
                params={
                    "q": query,
                    "from": from_date,
                    "limit": limit,
                },
                timeout=15,
            )
            
            if resp.status_code == 200:
                data = resp.json()
                artifacts = data if isinstance(data, list) else data.get("artifacts") or data.get("items") or []
                
                # Фильтруем только записи встреч
                meeting_artifacts = []
                for a in artifacts:
                    artifact_type = a.get("type", "").lower()
                    if artifact_type in ["meeting", "recording", "video", "call"]:
                        meeting_artifacts.append(a)
                
                return meeting_artifacts if meeting_artifacts else artifacts
            
            logger.warning("Ktalk search error: %s", resp.status_code)
            return []
            
        except Exception as exc:
            logger.warning("search_artifacts error: %s", exc)
            return []
    
    async def get_recent_meetings(
        self,
        client: httpx.AsyncClient,
        client_name: str,
        limit: int = 5,
    ) -> List[Dict]:
        """
        Получить последние записи встреч с клиентом.
        
        Args:
            client_name: Название клиента
            limit: Максимальное количество записей
        
        Returns:
            Список последних встреч
        """
        return await self.search_artifacts(
            client,
            query=client_name,
            limit=limit,
            days_back=180,
        )
    
    async def get_artifact_details(
        self,
        client: httpx.AsyncClient,
        artifact_id: str,
    ) -> Optional[Dict]:
        """Получить детальную информацию об артефакте (записи)."""
        try:
            resp = await client.get(
                f"{self.base_url}/content/artifacts/{artifact_id}",
                timeout=10,
            )
            
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning("Get artifact details error: %s", resp.status_code)
                return None
                
        except Exception as exc:
            logger.warning("get_artifact_details(%s) error: %s", artifact_id, exc)
            return None
    
    def get_meeting_url_from_artifact(self, artifact: Dict) -> Optional[str]:
        """
        Получить прямую ссылку на встречу из артефакта.
        
        Args:
            artifact: Данные артефакта
        
        Returns:
            Ссылка на встречу/запись
        """
        # Пробуем разные поля для получения ссылки
        url = (
            artifact.get("url") or
            artifact.get("link") or
            artifact.get("meeting_url") or
            artifact.get("recording_url")
        )
        
        if url:
            # Если относительная ссылка, делаем абсолютной
            if url.startswith("/"):
                url = f"{self.base_url}{url}"
            return url
        
        # Если есть ID, формируем ссылку сами
        artifact_id = artifact.get("id")
        if artifact_id:
            return f"{self.base_url}/content/artifacts/{artifact_id}"
        
        return None


def create_ktalk_meeting_button(client_name: str, am_name: Optional[str] = None) -> Dict:
    """
    Создать данные для кнопки "Начать встречу" в Telegram или веб-интерфейсе.
    
    Returns:
        Dict с текстом и ссылкой для кнопки
    """
    ktalk = KtalkClient()
    link = ktalk.generate_meeting_link(client_name, am_name)
    
    return {
        "text": f"📹 Начать встречу с {client_name}",
        "url": link,
        "icon": "🎥",
    }


async def get_client_meetings_history(
    client_name: str,
    site_id: Optional[str] = None,
) -> Dict:
    """
    Получить историю встреч клиента из Ktalk.
    
    Returns:
        {
            "recent_meetings": [...],
            "last_meeting_date": "2026-04-10",
            "total_meetings_found": 5,
            "quick_link": "ссылка на новую встречу",
        }
    """
    ktalk = KtalkClient()
    
    async with httpx.AsyncClient(timeout=30) as hx:
        # Ищем последние встречи
        recent = await ktalk.get_recent_meetings(hx, client_name, limit=10)
        
        # Определяем дату последней встречи
        last_meeting_date = None
        if recent:
            dates = []
            for m in recent:
                created = m.get("created_at") or m.get("createdAt") or m.get("date")
                if created:
                    dates.append(created[:10])
            if dates:
                last_meeting_date = max(dates)
        
        # Генерируем ссылку для новой встречи
        quick_link = ktalk.generate_meeting_link(client_name)
        
        return {
            "recent_meetings": recent[:5],  # Последние 5
            "last_meeting_date": last_meeting_date,
            "total_meetings_found": len(recent),
            "quick_link": quick_link,
            "all_meetings": recent,
        }
