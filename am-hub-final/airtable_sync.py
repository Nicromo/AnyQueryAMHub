"""
Интеграция с Airtable.
База: appEAS1rPKpevoIel  Таблица: tblIKAi1gcFayRJTn (Клиенты)

Задачи:
  - Обновить поле "дата последней встречи" при записи встречи
  - ДОПИСАТЬ (не перезаписать) комментарий с датой в историческое поле
  - Авто-находить нужные поля по имени при первом запуске

Настройка через Railway Variables:
  AIRTABLE_TOKEN   = patGWwb2jBAKXddDI.f7f4a270...
  AIRTABLE_BASE_ID = appEAS1rPKpevoIel
  AIRTABLE_TABLE_ID = tblIKAi1gcFayRJTn
"""
import os
import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

AIRTABLE_TOKEN    = os.getenv("AIRTABLE_TOKEN", "")
AIRTABLE_BASE_ID  = os.getenv("AIRTABLE_BASE_ID", "appEAS1rPKpevoIel")
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID", "tblIKAi1gcFayRJTn")

BASE_URL = "https://api.airtable.com/v0"

# Кэш схемы таблицы
_schema_cache: dict = {}

# Приоритетные названия полей, которые ищем (рус + eng)
MEETING_DATE_NAMES = [
    "дата встречи", "дата последней встречи", "последняя встреча",
    "last meeting", "last meeting date", "meeting date", "дата чекапа",
]
COMMENT_FIELD_NAMES = [
    "комментарий", "комментарии", "заметки", "заметка", "notes",
    "comments", "история", "лог встреч", "история встреч",
]
CLIENT_NAME_NAMES = [
    "клиент", "название", "name", "имя", "client", "компания", "сайт",
]


def _headers() -> dict:
    return {"Authorization": f"Bearer {AIRTABLE_TOKEN}", "Content-Type": "application/json"}


async def get_table_schema(client: httpx.AsyncClient) -> dict:
    """Загружаем схему таблицы один раз, кэшируем."""
    if _schema_cache:
        return _schema_cache

    try:
        resp = await client.get(
            f"{BASE_URL}/meta/bases/{AIRTABLE_BASE_ID}/tables",
            headers=_headers(), timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("Airtable meta error: %s", resp.text[:200])
            return {}

        tables = resp.json().get("tables", [])
        for t in tables:
            if t["id"] == AIRTABLE_TABLE_ID or "лиент" in t.get("name", "").lower():
                fields = {f["name"].lower(): f["id"] for f in t.get("fields", [])}
                _schema_cache.update(fields)
                logger.info("Airtable schema loaded: %d fields", len(fields))
                break
    except Exception as exc:
        logger.warning("Airtable schema fetch error: %s", exc)

    return _schema_cache


def _find_field(schema: dict, candidates: list[str]) -> Optional[str]:
    """Ищем ID поля по списку возможных названий."""
    for name in candidates:
        if name in schema:
            return schema[name]
    # Неточное совпадение
    for key, fid in schema.items():
        for name in candidates:
            if name in key or key in name:
                return fid
    return None


async def find_client_record(client: httpx.AsyncClient, schema: dict, client_name: str) -> Optional[str]:
    """Найти запись клиента в Airtable по имени. Возвращает record ID."""
    name_field_id = _find_field(schema, CLIENT_NAME_NAMES)
    if not name_field_id:
        # Пробуем поиск напрямую
        pass

    # Ищем по имени через filterByFormula
    formula = f'SEARCH(LOWER("{client_name}"), LOWER({{Name}}))'
    # Попробуем несколько форматов имён поля
    for name_field in ["Name", "Клиент", "Название", "Сайт", "Компания"]:
        try:
            resp = await client.get(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}",
                headers=_headers(),
                params={"filterByFormula": f'SEARCH(LOWER("{client_name.lower()}"), LOWER({{{name_field}}}))',
                        "maxRecords": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                records = resp.json().get("records", [])
                if records:
                    logger.info("Airtable: found client %s as record %s", client_name, records[0]["id"])
                    return records[0]["id"]
        except Exception:
            continue

    logger.warning("Airtable: client '%s' not found", client_name)
    return None


async def get_existing_comment(client: httpx.AsyncClient, record_id: str, comment_field_name: str) -> str:
    """Получить текущее значение поля с комментарием."""
    try:
        resp = await client.get(
            f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
            headers=_headers(), timeout=10,
        )
        if resp.status_code == 200:
            fields = resp.json().get("fields", {})
            return fields.get(comment_field_name, "") or ""
    except Exception as exc:
        logger.warning("Airtable get_existing_comment error: %s", exc)
    return ""


async def sync_meeting_to_airtable(
    client_name: str,
    meeting_date: str,
    meeting_type: str,
    summary: str,
    mood: str,
) -> dict:
    """
    Обновляем запись клиента в Airtable:
    1. Ставим дату последней встречи
    2. ДОПИСЫВАЕМ новый комментарий (старые не трогаем)
    """
    if not AIRTABLE_TOKEN:
        return {"ok": False, "error": "AIRTABLE_TOKEN не задан"}

    async with httpx.AsyncClient(timeout=15) as hx:
        schema = await get_table_schema(hx)
        if not schema:
            return {"ok": False, "error": "Не удалось загрузить схему Airtable"}

        record_id = await find_client_record(hx, schema, client_name)
        if not record_id:
            return {"ok": False, "error": f"Клиент '{client_name}' не найден в Airtable"}

        # Определяем поля для обновления
        date_field_id   = _find_field(schema, MEETING_DATE_NAMES)
        comment_field_id = _find_field(schema, COMMENT_FIELD_NAMES)

        # Имена полей для обновления (API принимает имена, не ID)
        # Находим имя поля по ID
        id_to_name = {v: k for k, v in schema.items()}
        date_field_name    = id_to_name.get(date_field_id) if date_field_id else None
        comment_field_name = id_to_name.get(comment_field_id) if comment_field_id else None

        mood_emoji = {"positive": "🟢", "neutral": "🟡", "risk": "🔴"}.get(mood, "🟡")
        type_label = {"checkup": "Чекап", "qbr": "QBR", "urgent": "Срочная"}.get(meeting_type, meeting_type)
        timestamp  = datetime.now().strftime("%d.%m.%Y")

        new_entry = f"\n---\n📅 {timestamp} [{type_label}] {mood_emoji}"
        if summary:
            new_entry += f"\n{summary}"

        fields_to_update: dict = {}

        # Обновляем дату встречи
        if date_field_name:
            fields_to_update[date_field_name] = meeting_date  # ISO date
        else:
            # Пробуем популярные варианты напрямую
            for fname in ["Дата встречи", "Последняя встреча", "Last meeting", "Дата чекапа"]:
                fields_to_update[fname] = meeting_date
                break

        # Дописываем комментарий
        if comment_field_name:
            existing = await get_existing_comment(hx, record_id, comment_field_name)
            fields_to_update[comment_field_name] = (existing + new_entry).strip()
        else:
            # Пробуем популярные варианты
            for fname in ["Комментарий", "Заметки", "Notes", "История встреч"]:
                try:
                    resp_check = await hx.get(
                        f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
                        headers=_headers(), timeout=10,
                    )
                    if resp_check.status_code == 200:
                        existing = resp_check.json().get("fields", {}).get(fname, "") or ""
                        fields_to_update[fname] = (existing + new_entry).strip()
                        break
                except Exception:
                    continue

        if not fields_to_update:
            return {"ok": False, "error": "Не удалось определить поля для обновления"}

        # Патчим запись
        try:
            resp = await hx.patch(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
                headers=_headers(),
                json={"fields": fields_to_update},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("Airtable updated: %s (%s)", client_name, meeting_date)
                return {"ok": True, "record_id": record_id}
            else:
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# ── Импорт клиентов из Airtable ───────────────────────────────────────────────

# Точные ID полей из таблицы клиентов
CLIENTS_TABLE_ID  = "tblIKAi1gcFayRJTn"
CLIENTS_VIEW_ID   = "viwocTz78z44WlAu1"
FIELD_ACCOUNT_NAME = "fldXeHkgIjzvr294Z"   # Название аккаунта
FIELD_MANAGER      = "fld0XMiWRh9xzvDy6"   # Аккаунт-менеджер
FIELD_SITE_ID      = "fldreqkwkEXrEGGwg"   # Номер аккаунта (site_id)
FIELD_SEGMENT      = "fldyvNxsQglqiQs48"   # Сегмент (ENT / SME+ / SME- / SMB / SS)

# Нормализация значений сегмента из Airtable
SEGMENT_MAP = {
    "ent": "ENT", "enterprise": "ENT",
    "sme+": "SME+", "sme plus": "SME+", "sme_plus": "SME+",
    "sme-": "SME-", "sme minus": "SME-", "sme_minus": "SME-", "sme": "SME-",
    "smb": "SMB", "small": "SMB",
    "ss": "SS", "self-service": "SS", "self service": "SS",
}

# QBR-календарь
QBR_TABLE_ID = "tblqQbChhRYoZoxWu"
QBR_VIEW_ID  = "viw6JIE6SS2ub3enK"


async def import_clients_from_airtable() -> list[dict]:
    """
    Загружает список клиентов из Airtable.
    Возвращает список dict с ключами: name, site_id, manager
    """
    if not AIRTABLE_TOKEN:
        logger.warning("AIRTABLE_TOKEN не задан — пропускаем импорт клиентов")
        return []

    records = []
    offset = None

    async with httpx.AsyncClient(timeout=30) as hx:
        while True:
            params: dict = {
                "view": CLIENTS_VIEW_ID,
                "fields[]": [FIELD_ACCOUNT_NAME, FIELD_MANAGER, FIELD_SITE_ID, FIELD_SEGMENT],
                "pageSize": 100,
            }
            if offset:
                params["offset"] = offset

            try:
                resp = await hx.get(
                    f"{BASE_URL}/{AIRTABLE_BASE_ID}/{CLIENTS_TABLE_ID}",
                    headers=_headers(),
                    params=params,
                    timeout=20,
                )
                if resp.status_code != 200:
                    logger.warning("Airtable import_clients error: %s %s", resp.status_code, resp.text[:200])
                    break

                body = resp.json()
                for rec in body.get("records", []):
                    fields = rec.get("fields", {})
                    name = fields.get(FIELD_ACCOUNT_NAME)
                    site_id = fields.get(FIELD_SITE_ID)
                    manager_raw = fields.get(FIELD_MANAGER)
                    segment_raw = fields.get(FIELD_SEGMENT)

                    if not name:
                        continue

                    # Manager: текст / collaborator dict / linked record list
                    if isinstance(manager_raw, dict):
                        # Collaborator field: {"id": "usr...", "name": "...", "email": "..."}
                        manager = manager_raw.get("name") or manager_raw.get("email") or ""
                    elif isinstance(manager_raw, list):
                        # Linked record или multi-collaborator
                        first = manager_raw[0] if manager_raw else ""
                        if isinstance(first, dict):
                            manager = first.get("name") or first.get("email") or ""
                        else:
                            manager = str(first)
                    else:
                        manager = str(manager_raw or "").strip()

                    # Нормализуем сегмент
                    if isinstance(segment_raw, list):
                        segment_raw = segment_raw[0] if segment_raw else ""
                    seg_key = str(segment_raw or "").strip().lower()
                    segment = SEGMENT_MAP.get(seg_key, "SMB")

                    records.append({
                        "name": str(name).strip(),
                        "site_id": str(site_id).strip() if site_id else "",
                        "manager": str(manager).strip(),
                        "segment": segment,
                        "airtable_id": rec.get("id", ""),
                    })

                offset = body.get("offset")
                if not offset:
                    break

            except Exception as exc:
                logger.warning("import_clients_from_airtable error: %s", exc)
                break

    logger.info("Airtable: imported %d clients", len(records))
    return records


async def get_qbr_calendar_from_airtable() -> list[dict]:
    """
    Загружает QBR-события из Airtable-таблицы QBR-календаря.
    Возвращает список dict с ключами: name, date, status, description
    """
    if not AIRTABLE_TOKEN:
        return []

    records = []
    offset = None

    async with httpx.AsyncClient(timeout=30) as hx:
        while True:
            params: dict = {
                "view": QBR_VIEW_ID,
                "pageSize": 100,
            }
            if offset:
                params["offset"] = offset

            try:
                resp = await hx.get(
                    f"{BASE_URL}/{AIRTABLE_BASE_ID}/{QBR_TABLE_ID}",
                    headers=_headers(),
                    params=params,
                    timeout=20,
                )
                if resp.status_code != 200:
                    logger.warning("Airtable QBR calendar error: %s", resp.status_code)
                    break

                body = resp.json()
                for rec in body.get("records", []):
                    fields = rec.get("fields", {})
                    records.append({
                        "airtable_id": rec.get("id", ""),
                        "fields": fields,
                    })

                offset = body.get("offset")
                if not offset:
                    break

            except Exception as exc:
                logger.warning("get_qbr_calendar_from_airtable error: %s", exc)
                break

    logger.info("Airtable QBR calendar: %d records", len(records))
    return records
