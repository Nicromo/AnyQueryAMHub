"""
Интеграция с системой поддержки Time (Tinkoff).
Получение обращений из канала any-team-support.
Основано на реверс-инжиниринге запросов браузера.
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

import httpx

logger = logging.getLogger(__name__)

TIME_URL = os.getenv("TIME_BASE_URL", "https://time.tbank.ru")
TIME_COOKIE = os.getenv("TIME_SESSION_COOKIE", "")


class TimeClient:
    """
    Клиент для работы с API Time (система поддержки).
    Использует Cookie аутентификацию (сессия браузера).
    """

    def __init__(self, cookie: Optional[str] = None):
        self.base_url = TIME_URL
        self.api_base = f"{self.base_url}/api/v4"
        self.cookie = cookie or TIME_COOKIE
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }
        if self.cookie:
            self.headers["Cookie"] = self.cookie

    async def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None) -> Dict:
        """Внутренний метод для запросов."""
        url = f"{self.api_base}{endpoint}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method, url, headers=self.headers, params=params, json=json_data, timeout=10.0
                )
                if response.status_code == 401:
                    logger.error("Unauthorized: Проверьте Cookie сессии Time.")
                    return {}
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"Time API Error: {e}")
                return {}

    async def get_my_teams(self) -> List[Dict]:
        """Получить список команд пользователя."""
        return await self._request("GET", "/users/me/teams")

    async def get_team_channels(self, team_id: str) -> List[Dict]:
        """Получить все каналы в команде."""
        return await self._request("GET", f"/users/me/teams/{team_id}/channels", params={"include_deleted": "true"})

    async def find_channel(self, team_id: str, channel_name: str) -> Optional[Dict]:
        """Найти канал по имени (частичное совпадение)."""
        channels = await self.get_team_channels(team_id)
        for ch in channels:
            name = ch.get("name", "").lower()
            display = ch.get("display_name", "").lower()
            if channel_name.lower() in name or channel_name.lower() in display:
                return ch
        return None

    async def get_channel_posts(self, channel_id: str, page: int = 0, per_page: int = 60) -> List[Dict]:
        """Получить посты из канала."""
        data = await self._request("GET", f"/channels/{channel_id}/posts", params={"page": page, "per_page": per_page})
        if isinstance(data, dict):
            return data.get("posts", [])
        return data if isinstance(data, list) else []

    async def get_support_tickets(self, client_name: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """
        Получить тикеты поддержки.
        1. Находим команду.
        2. Ищем канал 'any-team-support' или канал с именем клиента.
        3. Забираем последние посты.
        """
        teams = await self.get_my_teams()
        if not teams:
            logger.warning("No teams found in Time.")
            return []

        team_id = teams[0]["id"]
        target_channel = None

        if client_name:
            # Пробуем найти канал с именем клиента
            target_channel = await self.find_channel(team_id, client_name)
            # Если не нашли — общий саппорт
            if not target_channel:
                target_channel = await self.find_channel(team_id, "any-team-support")
        else:
            target_channel = await self.find_channel(team_id, "any-team-support")

        if not target_channel:
            logger.warning(f"Support channel not found for client: {client_name}")
            return []

        posts = await self.get_channel_posts(target_channel["id"], page=0, per_page=limit)
        
        tickets = []
        for post in posts:
            if post.get("type"):  # Пропускаем системные сообщения
                continue
            ticket = {
                "id": post.get("id"),
                "channel_id": target_channel.get("id"),
                "channel_name": target_channel.get("display_name", target_channel.get("name")),
                "message": post.get("message", ""),
                "create_at": post.get("create_at"),
                "user_id": post.get("user_id"),
                "root_id": post.get("root_id"),
                "is_thread": bool(post.get("root_id")),
                "url": f"{self.base_url}/{team_id}/channels/{target_channel.get('name')}/permalink/{post.get('id')}"
            }
            tickets.append(ticket)
        return tickets
