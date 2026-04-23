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


QBR_CALENDAR_TABLE_ID = os.getenv("AIRTABLE_QBR_TABLE_ID", "tblqQbChhRYoZoxWu")
QBR_CALENDAR_VIEW_ID = os.getenv("AIRTABLE_QBR_VIEW_ID", "viw6JIE6SS2ub3enK")


def _pick_field(fields: Dict[str, Any], candidates: List[str]) -> Any:
    """Вытащить значение поля по любому из имён-кандидатов (case-insensitive)."""
    lower = {k.lower().strip(): v for k, v in fields.items()}
    for c in candidates:
        v = lower.get(c.lower().strip())
        if v not in (None, "", []):
            return v
    return None


def _parse_airtable_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


async def sync_qbr_calendar() -> List[Dict[str, Any]]:
    """Синхронизировать QBR календарь из Airtable.

    Таблица: tblqQbChhRYoZoxWu (view viw6JIE6SS2ub3enK).
    Возвращает список событий: ``{id, client, date, status, raw}``.
    """
    if not AIRTABLE_TOKEN or not AIRTABLE_BASE_ID:
        logger.warning("sync_qbr_calendar: Airtable not configured")
        return []

    events: List[Dict[str, Any]] = []
    offset: Optional[str] = None

    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            params: Dict[str, Any] = {"pageSize": 100}
            if QBR_CALENDAR_VIEW_ID:
                params["view"] = QBR_CALENDAR_VIEW_ID
            if offset:
                params["offset"] = offset

            try:
                resp = await http.get(
                    f"{AIRTABLE_API_URL}/{AIRTABLE_BASE_ID}/{QBR_CALENDAR_TABLE_ID}",
                    headers=_headers(),
                    params=params,
                )
            except Exception as e:
                logger.error(f"sync_qbr_calendar: HTTP error: {e}")
                break

            if resp.status_code != 200:
                logger.warning(
                    f"sync_qbr_calendar: Airtable API error {resp.status_code}: {resp.text[:200]}"
                )
                break

            data = resp.json()
            for record in data.get("records", []):
                fields = record.get("fields", {}) or {}
                raw_date = _pick_field(
                    fields,
                    ["qbr date", "дата qbr", "date", "дата", "planned date", "when"],
                )
                date_parsed = _parse_airtable_date(raw_date)

                client_name = _pick_field(
                    fields,
                    ["client", "клиент", "account", "аккаунт", "name"],
                )
                if isinstance(client_name, list) and client_name:
                    client_name = client_name[0]
                status = _pick_field(
                    fields, ["status", "статус", "state"],
                ) or "planned"

                events.append({
                    "id":     record.get("id"),
                    "client": str(client_name or "").strip(),
                    "date":   date_parsed,
                    "status": str(status).strip().lower(),
                    "raw":    fields,
                })

            offset = data.get("offset")
            if not offset:
                break

    logger.info(f"sync_qbr_calendar: pulled {len(events)} QBR events from Airtable")
    return events


if __name__ == "__main__":
    # Тест
    import asyncio

    async def test():
        clients = await get_clients()
        for client in clients[:3]:
            print(client)

    asyncio.run(test())
