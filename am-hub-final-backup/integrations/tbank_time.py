"""
Tbank Time Integration
Получение данных об обращениях в саппорт по клиентам

https://time.tbank.ru/tinkoff/channels/any-team-support

Требуется: Tbank Time API token или парсинг
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import httpx

logger = logging.getLogger(__name__)

# Configuration
TIME_BASE_URL = os.getenv("TIME_BASE_URL", "https://time.tbank.ru")
TIME_API_TOKEN = os.getenv("TIME_API_TOKEN", "")
TIME_SUPPORT_CHANNEL = os.getenv("TIME_SUPPORT_CHANNEL", "any-team-support")

CACHE_TTL_SECONDS = 1800  # 30 минут

# Cache
_tickets_cache: Dict[str, Any] = {}


def _headers() -> dict:
    """Tbank Time API headers"""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AM Hub Client",
    }
    if TIME_API_TOKEN:
        headers["Authorization"] = f"Bearer {TIME_API_TOKEN}"
    return headers


# ============================================================================
# PLACEHOLDER: Функции для интеграции Tbank Time
# ============================================================================


async def get_support_tickets(
    account_name: str,
    status: str = "open,in_progress",
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """
    Получить обращения в саппорт по названию клиента/аккаунта
    
    Args:
        account_name: Название аккаунта/клиента (для поиска в чикетах)
        status: Статусы для фильтра (default: открыто/в работе)
        use_cache: Использовать кэш (default: True)
    
    Returns:
        List: Обращения с полями:
            {
                "id": str,
                "account": str,
                "title": str,
                "description": str,
                "status": str,  # open/in_progress/waiting/resolved/closed
                "priority": str,  # low/normal/high/critical
                "created_at": datetime,
                "updated_at": datetime,
                "assigned_to": str,
                "messages_count": int,
            }
    """
    cache_key = f"tickets_{account_name}_{status}"

    if use_cache and cache_key in _tickets_cache:
        cached = _tickets_cache[cache_key]
        if datetime.now() - cached["timestamp"] < timedelta(seconds=CACHE_TTL_SECONDS):
            return cached["data"]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{TIME_BASE_URL}/api/v1/tickets",
                headers=_headers(),
                params={
                    "account": account_name,
                    "status": status,
                    "limit": 100,
                },
                timeout=10,
            )
            
            if response.status_code == 200:
                data = response.json()
                tickets = data.get("tickets", [])
                
                # Нормализовать данные
                normalized = []
                for t in tickets:
                    normalized.append({
                        "id": t.get("id"),
                        "account": t.get("account", account_name),
                        "title": t.get("title", ""),
                        "description": t.get("description", ""),
                        "status": t.get("status", "open"),
                        "priority": t.get("priority", "normal"),
                        "created_at": datetime.fromisoformat(t["created_at"]) if "created_at" in t else None,
                        "updated_at": datetime.fromisoformat(t["updated_at"]) if "updated_at" in t else None,
                        "assigned_to": t.get("assigned_to"),
                        "messages_count": t.get("messages_count", 0),
                    })
                
                _tickets_cache[cache_key] = {
                    "data": normalized,
                    "timestamp": datetime.now(),
                }
                
                logger.info(f"✅ Loaded {len(normalized)} tickets for {account_name}")
                return normalized
            else:
                logger.warning(f"Tbank Time API error: {response.status_code}")
    
    except Exception as e:
        logger.error(f"❌ Failed to fetch Tbank Time tickets: {e}")

    return []


async def get_ticket_details(ticket_id: str) -> Optional[Dict[str, Any]]:
    """
    Получить детали обращения
    
    Args:
        ticket_id: ID обращения
    
    Returns:
        Dict: Детали обращения с сообщениями
            {
                "id": str,
                "account": str,
                "title": str,
                "status": str,
                "messages": [
                    {
                        "id": str,
                        "author": str,
                        "text": str,
                        "created_at": datetime,
                        "attachments": list,
                    },
                    ...
                ],
                ...
            }
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{TIME_BASE_URL}/api/v1/tickets/{ticket_id}",
                headers=_headers(),
                timeout=10,
            )
            
            if response.status_code == 200:
                ticket = response.json()
                
                # Нормализовать сообщения
                messages = ticket.get("messages", [])
                normalized_messages = []
                for msg in messages:
                    normalized_messages.append({
                        "id": msg.get("id"),
                        "author": msg.get("author", ""),
                        "text": msg.get("text", ""),
                        "created_at": datetime.fromisoformat(msg["created_at"]) if "created_at" in msg else None,
                        "attachments": msg.get("attachments", []),
                    })
                
                ticket["messages"] = normalized_messages
                logger.info(f"✅ Loaded details for ticket {ticket_id}")
                return ticket
            else:
                logger.warning(f"Ticket not found: {ticket_id}")
    
    except Exception as e:
        logger.error(f"❌ Failed to fetch ticket details: {e}")
    
    return None


async def get_ticket_history(
    account_name: str,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    Получить историю обращений за период
    
    Args:
        account_name: Название аккаунта
        days: Период в днях (default: 30 дней)
    
    Returns:
        List: Все обращения (открытые + закрытые) за период
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{TIME_BASE_URL}/api/v1/tickets/history",
                headers=_headers(),
                params={
                    "account": account_name,
                    "days": days,
                    "limit": 1000,
                },
                timeout=10,
            )
            
            if response.status_code == 200:
                data = response.json()
                tickets = data.get("tickets", [])
                
                logger.info(f"✅ Loaded {len(tickets)} tickets from history for {account_name}")
                return tickets
            else:
                logger.warning(f"Failed to load ticket history")
    
    except Exception as e:
        logger.error(f"❌ Failed to fetch ticket history: {e}")
    
    return []


async def count_open_tickets(account_name: str) -> int:
    """
    Получить кол-во открытых обращений
    
    Args:
        account_name: Название аккаунта
    
    Returns:
        int: Кол-во открытых обращений
    """
    tickets = await get_support_tickets(account_name, status="open,in_progress")
    return len(tickets)


async def sync_tickets_for_client(account_name: str) -> Dict[str, Any]:
    """
    Синхронизировать обращения для клиента в БД
    
    Логика:
    1. Получить все обращения по аккаунту
    2. Обновить count в таблице Client
    3. Сохранить список в БД (можно в JSONB)
    
    Args:
        account_name: Название аккаунта
    
    Returns:
        Dict: Результат синхронизации
            {
                "open_count": int,
                "total_count": int,
                "last_ticket": Dict,
            }
    """
    logger.info(f"🔄 Syncing Tbank Time tickets for {account_name}")
    
    try:
        # Получить открытые обращения
        open_tickets = await get_support_tickets(
            account_name,
            status="open,in_progress",
            use_cache=False,
        )
        
        # Получить историю за 30 дней
        history_tickets = await get_ticket_history(account_name, days=30)
        
        # Получить детали для последнего (если есть)
        last_ticket = None
        if open_tickets:
            last_ticket = await get_ticket_details(open_tickets[0]["id"])
        
        result = {
            "open_count": len(open_tickets),
            "total_count": len(history_tickets),
            "last_ticket": last_ticket,
        }
        
        logger.info(f"✅ Synced Tbank Time tickets: {result}")
        return result
    
    except Exception as e:
        logger.error(f"❌ Failed to sync Tbank Time tickets: {e}")
        return {
            "open_count": 0,
            "total_count": 0,
            "last_ticket": None,
        }


class TimeChannelMonitor:
    """Мониторинг канала any-team-support в Tbank Time"""

    async def get_channel_messages(
        self, channel: str = "any-team-support", limit: int = 50
    ) -> List[Dict]:
        """
        Получить сообщения из канала (может помочь в обработке обращений)
        
        Args:
            channel: Название канала
            limit: Кол-во сообщений
        
        Returns:
            List: Сообщения для анализа
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{TIME_BASE_URL}/api/v1/channels/{channel}/messages",
                    headers=_headers(),
                    params={"limit": limit},
                    timeout=10,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    messages = data.get("messages", [])
                    logger.info(f"✅ Loaded {len(messages)} messages from {channel}")
                    return messages
        
        except Exception as e:
            logger.error(f"❌ Failed to fetch channel messages: {e}")
        
        return []

    async def parse_tickets_from_channel(self) -> List[Dict]:
        """
        Парсить обращения из сообщений канала
        
        Returns:
            List: Выделенные обращения
        """
        try:
            messages = await self.get_channel_messages()
            
            # Простой парсинг: ищем сообщения с структурой вида "Ticket: ..."
            tickets = []
            for msg in messages:
                text = msg.get("text", "")
                if "ticket" in text.lower() or "обращение" in text.lower():
                    tickets.append({
                        "source": "channel",
                        "message": text,
                        "author": msg.get("author"),
                        "created_at": msg.get("created_at"),
                    })
            
            logger.info(f"📊 Parsed {len(tickets)} tickets from channel")
            return tickets
        
        except Exception as e:
            logger.error(f"❌ Failed to parse channel tickets: {e}")
        
        return []


if __name__ == "__main__":
    import asyncio

    async def test():
        tickets = await get_support_tickets("Test Account")
        print(f"Loaded {len(tickets)} tickets")

    asyncio.run(test())
