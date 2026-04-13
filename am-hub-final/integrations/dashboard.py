"""
Dashboard Integration
Двусторонняя синхронизация данных с дашбордом

Логика:
1. PULL: Получить обновления из дашборда
2. SYNC: Обновить локальную БД
3. PUSH: Отправить изменения в дашборд
"""

import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
import httpx

logger = logging.getLogger(__name__)

# Configuration
DASHBOARD_API_URL = os.getenv("DASHBOARD_API_URL", "")
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")

SUPPORTED_RESOURCES = ["clients", "tasks", "meetings", "checkups"]


def _headers() -> dict:
    """Dashboard API headers"""
    return {
        "Authorization": f"Bearer {DASHBOARD_API_KEY}",
        "Content-Type": "application/json",
    }


# ============================================================================
# PLACEHOLDER: Функции для двусторонней синхронизации
# ============================================================================


async def pull_updates(resource: str, since: Optional[datetime] = None) -> List[Dict]:
    """
    PULL: Получить обновления из дашборда
    
    Args:
        resource: Тип ресурса (clients/tasks/meetings/checkups)
        since: Получить обновления после даты (optional)
    
    Returns:
        List: Обновления с полями:
            {
                "id": str,
                "action": str,  # create/update/delete
                "data": Dict,
                "timestamp": datetime,
            }
    """
    if not DASHBOARD_API_URL or not DASHBOARD_API_KEY:
        logger.warning("Dashboard API not configured")
        return []

    if resource not in SUPPORTED_RESOURCES:
        logger.warning(f"Unsupported resource: {resource}")
        return []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            params = {}
            if since:
                params["since"] = since.isoformat()
            
            response = await client.get(
                f"{DASHBOARD_API_URL}/api/sync/{resource}",
                headers=_headers(),
                params=params,
                timeout=10,
            )
            
            if response.status_code == 200:
                data = response.json()
                updates = data.get("updates", [])
                
                # Нормализовать даты
                for update in updates:
                    if "timestamp" in update:
                        update["timestamp"] = datetime.fromisoformat(update["timestamp"])
                
                logger.info(f"✅ Pulled {len(updates)} updates from Dashboard for {resource}")
                return updates
            else:
                logger.warning(f"Dashboard API error: {response.status_code}")
    
    except Exception as e:
        logger.error(f"❌ Failed to pull updates from Dashboard: {e}")
    
    return []


async def push_updates(resource: str, updates: List[Dict]) -> bool:
    """
    PUSH: Отправить изменения в дашборд
    
    Args:
        resource: Тип ресурса (clients/tasks/meetings/checkups)
        updates: Список обновлений для отправки
            [
                {
                    "id": str,
                    "action": str,  # create/update/delete
                    "data": Dict,
                },
                ...
            ]
    
    Returns:
        bool: Success
    """
    if not DASHBOARD_API_URL or not DASHBOARD_API_KEY:
        logger.warning("Dashboard API not configured")
        return False

    if resource not in SUPPORTED_RESOURCES:
        logger.warning(f"Unsupported resource: {resource}")
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.patch(
                f"{DASHBOARD_API_URL}/api/sync/{resource}",
                headers=_headers(),
                json={"updates": updates},
                timeout=10,
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Pushed {len(updates)} updates to Dashboard for {resource}")
                return True
            else:
                logger.warning(f"Dashboard API error: {response.status_code}")
    
    except Exception as e:
        logger.error(f"❌ Failed to push updates to Dashboard: {e}")
    
    return False


async def sync_resource(resource: str, local_data: List[Dict]) -> Dict[str, Any]:
    """
    Полная синхронизация ресурса (pull + push)
    
    Алгоритм:
    1. PULL обновления из дашборда
    2. Применить локально (на приложение вызова полагается)
    3. PUSH локальные изменения которые были с последней синхронизации
    
    Args:
        resource: Тип ресурса
        local_data: Локальные данные (для конфликт-резолюшена)
    
    Returns:
        Dict: Результат синхронизации
            {
                "pulled": int,  # кол-во полученных обновлений
                "pushed": int,  # кол-во отправленных обновлений
                "conflicts": int,  # кол-во конфликтов
            }
    """
    logger.info(f"🔄 Full sync for {resource}")
    
    try:
        # Pull обновления из дашборда
        since = datetime.now() - __import__('datetime').timedelta(hours=1)
        pulled_updates = await pull_updates(resource, since=since)
        
        # Простой конфликт-резолюшен: дашборд актуальнее
        conflicts = 0
        for update in pulled_updates:
            # Здесь нужно проверить наличие конфликтов с локальными данными
            pass
        
        # Push локальные изменения
        # (в реальной системе нужно отслеживать какие обновления произошли локально)
        local_updates = []
        push_success = await push_updates(resource, local_updates)
        
        result = {
            "pulled": len(pulled_updates),
            "pushed": len(local_updates) if push_success else 0,
            "conflicts": conflicts,
        }
        
        logger.info(f"✅ Synced {resource}: {result}")
        return result
    
    except Exception as e:
        logger.error(f"❌ Failed to sync {resource}: {e}")
        return {
            "pulled": 0,
            "pushed": 0,
            "conflicts": 0,
        }


class DashboardSyncManager:
    """Менеджер для управления синхронизацией с дашбордом"""

    def __init__(self):
        self.last_sync_time: Dict[str, datetime] = {}
        self.pending_updates: Dict[str, List[Dict]] = {
            resource: [] for resource in SUPPORTED_RESOURCES
        }

    async def queue_update(self, resource: str, action: str, data: Dict) -> None:
        """Добавить обновление в очередь"""
        if resource not in SUPPORTED_RESOURCES:
            logger.warning(f"Unsupported resource: {resource}")
            return

        update = {
            "action": action,
            "data": data,
            "timestamp": datetime.now(),
        }
        self.pending_updates[resource].append(update)
        logger.info(f"Queued {action} for {resource}")

    async def flush_updates(self, resource: str) -> bool:
        """Отправить все накопленные обновления для ресурса"""
        if not self.pending_updates[resource]:
            return True

        updates = self.pending_updates[resource]
        success = await push_updates(resource, updates)

        if success:
            self.pending_updates[resource].clear()
            self.last_sync_time[resource] = datetime.now()
            logger.info(f"Flushed {len(updates)} updates for {resource}")

        return success

    async def full_sync(self) -> Dict[str, Dict]:
        """Полная синхронизация всех ресурсов"""
        results = {}

        for resource in SUPPORTED_RESOURCES:
            # TODO: Получить локальные данные из БД
            local_data = []  # placeholder

            result = await sync_resource(resource, local_data)
            results[resource] = result

        return results


# Глобальный менеджер для использования в других модулях
sync_manager = DashboardSyncManager()


if __name__ == "__main__":
    import asyncio

    async def test():
        manager = DashboardSyncManager()
        
        # Queue some test updates
        await manager.queue_update("clients", "create", {"name": "Test Client"})
        await manager.queue_update("tasks", "update", {"id": 1, "status": "done"})
        
        # Flush updates
        await manager.flush_updates("clients")
        await manager.flush_updates("tasks")
        
        # Full sync
        results = await manager.full_sync()
        print(f"Sync results: {results}")

    asyncio.run(test())
