"""
Airtable Integration Module
Получение данных по клиентам, менеджерам и синхронизация встреч

Конфигурация через переменные окружения:
  AIRTABLE_TOKEN - Personal Access Token
  AIRTABLE_BASE_ID - appEAS1rPKpevoIel
  AIRTABLE_TABLE_ID - tblIKAi1gcFayRJTn
  AIRTABLE_VIEW_ID - viwocTz78z44WlAu1 (основная view)
"""

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from functools import lru_cache
import httpx

logger = logging.getLogger(__name__)

# Configuration
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "")
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID", "")
AIRTABLE_VIEW_ID = os.getenv("AIRTABLE_VIEW_ID", "viwocTz78z44WlAu1")

AIRTABLE_API_URL = "https://api.airtable.com/v0"
CACHE_TTL_SECONDS = 900  # 15 минут

# Field mappings (по названиям полей ищем ID)
FIELD_NAMES = {
    "account_name": ["аккаунт", "account", "client", "name"],
    "account_id": ["номер аккаунта", "account id", "account number", "айди"],
    "manager": ["менеджер", "account manager", "manager", "am"],
    "segment": ["сегмент", "segment", "type"],
}

# Cache
_client_cache: dict = {}


def _headers() -> dict:
    """Airtable API headers"""
    return {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json",
    }


async def _get_schema(client: httpx.AsyncClient) -> Dict[str, str]:
    """
    Получить схему таблицы (маппинг имен полей на IDs)
    Нужно вызвать один раз при инициализации
    """
    try:
        resp = await client.get(
            f"{AIRTABLE_API_URL}/meta/bases/{AIRTABLE_BASE_ID}/tables",
            headers=_headers(),
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"Airtable schema error: {resp.status_code}")
            return {}

        tables = resp.json().get("tables", [])
        for table in tables:
            if table["id"] == AIRTABLE_TABLE_ID:
                schema = {}
                for field in table.get("fields", []):
                    field_name = field["name"].lower().strip()
                    field_id = field["id"]
                    schema[field_name] = field_id
                logger.info(f"Airtable schema loaded: {len(schema)} fields")
                return schema

        logger.warning("Airtable table not found")
        return {}
    except Exception as e:
        logger.error(f"Airtable schema fetch error: {e}")
        return {}


async def get_clients(
    account_manager: Optional[str] = None, use_cache: bool = True
) -> List[Dict[str, Any]]:
    """
    Получить список клиентов из Airtable
    
    Args:
        account_manager: Фильтр по менеджеру (optional)
        use_cache: Использовать кэш (default: True)
    
    Returns:
        List[Dict]: Список клиентов с полями:
            {
                "id": "recXXX",  # Airtable record ID
                "name": str,
                "account_id": str,
                "manager": str,
                "segment": str,
            }
    """
    cache_key = f"clients_{account_manager or 'all'}"
    
    if use_cache and cache_key in _client_cache:
        cached = _client_cache[cache_key]
        if datetime.now() - cached["timestamp"] < timedelta(seconds=CACHE_TTL_SECONDS):
            return cached["data"]

    async with httpx.AsyncClient() as client:
        try:
            # Получить schema
            schema = await _get_schema(client)
            if not schema:
                logger.warning("Cannot proceed without schema")
                return []

            # Получить данные
            params = {"pageSize": 100}
            if account_manager:
                # Фильтр по менеджеру (нужно узнать точное имя поля)
                manager_field_id = schema.get("менеджер") or schema.get("account manager")
                if manager_field_id:
                    params["filterByFormula"] = f'FIND("{account_manager}", {{{manager_field_id}}}) > 0'

            resp = await client.get(
                f"{AIRTABLE_API_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}",
                params=params,
                headers=_headers(),
                timeout=15,
            )

            if resp.status_code != 200:
                logger.warning(f"Airtable error: {resp.status_code} - {resp.text[:200]}")
                return []

            records = resp.json().get("records", [])
            clients = []

            for record in records:
                fields = record.get("fields", {})
                client_data = {
                    "id": record["id"],
                    "name": _find_field_value(fields, schema, "account_name"),
                    "account_id": _find_field_value(fields, schema, "account_id"),
                    "manager": _find_field_value(fields, schema, "manager"),
                    "segment": _find_field_value(fields, schema, "segment"),
                    "raw_fields": fields,
                }
                clients.append(client_data)

            # Кэшируем
            _client_cache[cache_key] = {
                "data": clients,
                "timestamp": datetime.now(),
            }

            logger.info(f"Loaded {len(clients)} clients from Airtable")
            return clients

        except Exception as e:
            logger.error(f"Airtable fetch error: {e}")
            return []


def _find_field_value(fields: Dict, schema: Dict, field_type: str) -> Optional[str]:
    """Найти значение поля по типу (используя FIELD_NAMES маппинг)"""
    possible_names = FIELD_NAMES.get(field_type, [])
    
    for name in possible_names:
        if name in schema and schema[name] in fields:
            value = fields[schema[name]]
            if isinstance(value, list):
                return value[0] if value else None
            return value
    
    return None


async def update_meeting_date(
    record_id: str, meeting_date: datetime, comment: str = ""
) -> bool:
    """
    Обновить дату последней встречи в Airtable
    ДОПИСЫВАЕТ комментарий в историческое поле (не перезаписывает)
    
    Args:
        record_id: Airtable record ID
        meeting_date: Дата встречи
        comment: Комментарий к встречи (для исторического поля)
    
    Returns:
        bool: Success
    """
    async with httpx.AsyncClient() as client:
        try:
            schema = await _get_schema(client)
            if not schema:
                return False

            # Найти поля для обновления
            meeting_date_field = schema.get("дата встречи") or schema.get("last meeting date")
            comment_field = schema.get("комментарий") or schema.get("comments")

            if not meeting_date_field:
                logger.warning("Cannot find meeting date field in schema")
                return False

            # Подготовить данные для обновления
            update_data = {"fields": {}}

            # Обновить дату встречи
            if meeting_date_field:
                update_data["fields"][meeting_date_field] = meeting_date.isoformat()

            # ДОПИСАТЬ комментарий (получить текущее значение, добавить новое)
            if comment_field and comment:
                # Получить текущее значение
                resp = await client.get(
                    f"{AIRTABLE_API_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
                    headers=_headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    current_comment = resp.json().get("fields", {}).get(comment_field, "")
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                    new_comment = f"{current_comment}\n[{timestamp}] {comment}".strip()
                    update_data["fields"][comment_field] = new_comment

            # Отправить обновление
            resp = await client.patch(
                f"{AIRTABLE_API_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
                json=update_data,
                headers=_headers(),
                timeout=10,
            )

            if resp.status_code == 200:
                logger.info(f"Updated meeting date for {record_id}")
                # Очистить кэш
                _client_cache.clear()
                return True
            else:
                logger.warning(f"Airtable update error: {resp.status_code}")
                return False

        except Exception as e:
            logger.error(f"Airtable update error: {e}")
            return False


async def sync_qbr_calendar() -> List[Dict[str, Any]]:
    """
    Синхронизировать QBR календарь из Airtable
    
    Returns:
        List[Dict]: QBR события с полями:
            {
                "id": str,
                "client": str,
                "date": datetime,
                "status": str,
            }
    """
    # TODO: Реализовать синхронизацию QBR календаря
    # QBR Calendar таблица: tblqQbChhRYoZoxWu (viw6JIE6SS2ub3enK)
    pass


if __name__ == "__main__":
    # Тест
    import asyncio

    async def test():
        clients = await get_clients()
        for client in clients[:3]:
            print(client)

    asyncio.run(test())
