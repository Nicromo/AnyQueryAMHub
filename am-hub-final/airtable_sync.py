"""
Интеграция с Airtable.
База: appEAS1rPKpevoIel  Таблица: tblIKAi1gcFayRJTn (Клиенты)

Задачи:
  - Обновить поле "дата последней встречи" при записи встречи
  - ДОПИСАТЬ (не перезаписать) комментарий с датой в историческое поле
  - Авто-находить нужные поля по имени при первом запуске
  - Импорт всех клиентов из CS ALL view с разбивкой по менеджерам

Настройка через Railway Variables:
  AIRTABLE_TOKEN   = patGWwb2jBAKXddDI.f7f4a270...
  AIRTABLE_BASE_ID = appEAS1rPKpevoIel
  AIRTABLE_TABLE_ID = tblIKAi1gcFayRJTn
  AIRTABLE_VIEW_ID = viwz7G1vPxxg0WvC3  (CS ALL view)
"""
import os
import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

AIRTABLE_TOKEN    = os.getenv("AIRTABLE_TOKEN", "patGWwb2jBAKXddDI.f7f4a270685a36112d6958c8299056c7930f67c798908f74db6d81a0bae69d8d")
AIRTABLE_BASE_ID  = os.getenv("AIRTABLE_BASE_ID", "appEAS1rPKpevoIel")
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID", "tblIKAi1gcFayRJTn")
AIRTABLE_VIEW_ID  = os.getenv("AIRTABLE_VIEW_ID", "viwz7G1vPxxg0WvC3")  # CS ALL

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


# ─────────────────────────────────────────────────────────────────────────────
#  Импорт всех клиентов из Airtable CS ALL view
# ─────────────────────────────────────────────────────────────────────────────

# Кандидаты на поле "имя клиента / сайт"
CLIENT_NAME_FIELD_CANDIDATES = [
    "сайт", "клиент", "название", "имя", "name", "site", "client",
    "компания", "company", "магазин", "shop",
]

# Кандидаты на поле "сегмент"
SEGMENT_FIELD_CANDIDATES = [
    "сегмент", "segment", "tier", "тариф", "plan", "тир",
]

# Кандидаты на поле "менеджер / АМ"
MANAGER_FIELD_CANDIDATES = [
    "ам", "am", "менеджер", "manager", "ответственный", "аккаунт",
    "account manager", "куратор", "csa", "cs",
]

# Кандидаты на поле "site_id / ID в Merchrules"
SITE_ID_FIELD_CANDIDATES = [
    "id", "site_id", "siteid", "мерч", "merchrules", "site id",
    "идентификатор", "shop id", "shopid",
]

# Кандидаты на поле "дата последнего чекапа"
LAST_CHECKUP_FIELD_CANDIDATES = [
    "дата встречи", "последняя встреча", "last meeting", "дата чекапа",
    "последний чекап", "last checkup", "last contact",
]

# Нормализация сегментов из Airtable → наш формат
SEGMENT_MAP = {
    "ent": "ENT", "enterprise": "ENT", "энт": "ENT",
    "sme+": "SME+", "sme plus": "SME+", "sme_plus": "SME+",
    "sme-": "SME-", "sme minus": "SME-", "sme_minus": "SME-",
    "sme": "SME",
    "smb": "SMB", "малый": "SMB", "small": "SMB",
    "ss": "SS", "стартер": "SS", "starter": "SS", "self-service": "SS",
}


def _fuzzy_find_field(fields: dict, candidates: list[str]) -> Optional[str]:
    """
    Ищет название поля среди ключей fields по списку кандидатов.
    Возвращает реальное имя поля (как оно есть в fields).
    """
    fields_lower = {k.lower(): k for k in fields.keys()}
    # Точное совпадение
    for c in candidates:
        if c in fields_lower:
            return fields_lower[c]
    # Поле содержит кандидата
    for c in candidates:
        for fl, orig in fields_lower.items():
            if c in fl:
                return orig
    # Кандидат содержится в поле
    for c in candidates:
        for fl, orig in fields_lower.items():
            if fl in c:
                return orig
    return None


def _normalize_segment(raw: str) -> str:
    """Приводит сырое значение сегмента к нашему формату."""
    if not raw:
        return "SMB"
    key = raw.strip().lower()
    return SEGMENT_MAP.get(key, raw.upper()[:4])


def _extract_field_value(fields: dict, field_name: Optional[str]) -> str:
    """Безопасно достаёт значение поля, учитывая linked records."""
    if not field_name or field_name not in fields:
        return ""
    val = fields[field_name]
    if isinstance(val, list):
        # Linked record — возвращаем первый элемент
        if val and isinstance(val[0], str):
            return val[0]
        if val and isinstance(val[0], dict):
            return val[0].get("name", "") or val[0].get("title", "") or str(val[0])
        return ""
    return str(val) if val else ""


async def fetch_all_records_from_view(
    client: httpx.AsyncClient,
    view_id: str = AIRTABLE_VIEW_ID,
) -> list[dict]:
    """Скачивает ВСЕ записи из view с пагинацией (Airtable даёт по 100)."""
    records = []
    offset = None
    page = 0

    while True:
        page += 1
        params: dict = {
            "view": view_id,
            "pageSize": 100,
        }
        if offset:
            params["offset"] = offset

        try:
            resp = await client.get(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}",
                headers=_headers(),
                params=params,
                timeout=30,
            )
            if resp.status_code != 200:
                logger.error("Airtable fetch error page %d: %s", page, resp.text[:300])
                break

            data = resp.json()
            batch = data.get("records", [])
            records.extend(batch)
            logger.info("Airtable: page %d → %d records (total %d)", page, len(batch), len(records))

            offset = data.get("offset")
            if not offset:
                break  # Больше страниц нет
        except Exception as exc:
            logger.error("Airtable fetch exception: %s", exc)
            break

    return records


async def import_clients_from_airtable(
    token: Optional[str] = None,
    view_id: str = AIRTABLE_VIEW_ID,
) -> dict:
    """
    Импортирует клиентов из Airtable CS ALL view в локальную БД.

    Алгоритм:
    1. Качаем ВСЕ записи из view (с пагинацией)
    2. На первой записи авто-определяем названия полей
    3. Upsert клиентов в clients (name, segment, site_ids, last_checkup)
    4. Если есть поле менеджера → пытаемся привязать к manager_profiles
       по display_name (нечёткое совпадение)
    5. Записываем в manager_clients (tg_id → client_id)

    Возвращает: {ok, created, updated, skipped, managers_linked, errors, field_map}
    """
    from database import (
        upsert_client, get_all_manager_profiles,
        get_manager_client_ids, set_manager_clients, get_conn,
    )

    use_token = token or AIRTABLE_TOKEN
    if not use_token:
        return {"ok": False, "error": "AIRTABLE_TOKEN не задан"}

    # Временно подменяем токен если передан свой
    original_token = os.environ.get("AIRTABLE_TOKEN", "")
    if token:
        os.environ["AIRTABLE_TOKEN"] = token

    stats = {
        "ok": True, "created": 0, "updated": 0, "skipped": 0,
        "managers_linked": 0, "errors": [], "field_map": {},
        "unmatched_managers": set(),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            # ── 1. Качаем все записи ──────────────────────────────────────────
            records = await fetch_all_records_from_view(hx, view_id)
            if not records:
                return {"ok": False, "error": "Нет записей в view или ошибка доступа"}

            logger.info("Airtable import: %d records fetched", len(records))

            # ── 2. Авто-определяем поля по первым ~5 записям ─────────────────
            sample_fields: dict = {}
            for r in records[:5]:
                sample_fields.update(r.get("fields", {}))

            name_field    = _fuzzy_find_field(sample_fields, CLIENT_NAME_FIELD_CANDIDATES)
            segment_field = _fuzzy_find_field(sample_fields, SEGMENT_FIELD_CANDIDATES)
            manager_field = _fuzzy_find_field(sample_fields, MANAGER_FIELD_CANDIDATES)
            site_id_field = _fuzzy_find_field(sample_fields, SITE_ID_FIELD_CANDIDATES)
            checkup_field = _fuzzy_find_field(sample_fields, LAST_CHECKUP_FIELD_CANDIDATES)

            stats["field_map"] = {
                "name":     name_field,
                "segment":  segment_field,
                "manager":  manager_field,
                "site_id":  site_id_field,
                "checkup":  checkup_field,
                "all_fields": list(sample_fields.keys()),
            }

            if not name_field:
                return {
                    "ok": False,
                    "error": "Не удалось найти поле с именем клиента",
                    "available_fields": list(sample_fields.keys()),
                }

            logger.info(
                "Airtable field map: name=%s seg=%s mgr=%s site=%s checkup=%s",
                name_field, segment_field, manager_field, site_id_field, checkup_field,
            )

            # ── 3. Загружаем профили менеджеров для матчинга ─────────────────
            manager_profiles = get_all_manager_profiles()
            # Индексируем по нижнему регистру display_name и mr_login
            mgr_index: dict[str, int] = {}  # имя → tg_id
            for mp in manager_profiles:
                dn = (mp.get("display_name") or "").strip().lower()
                login = (mp.get("mr_login") or "").strip().lower()
                tg = mp.get("tg_id")
                if tg:
                    if dn:
                        mgr_index[dn] = tg
                        # Первое слово (имя)
                        first = dn.split()[0] if " " in dn else dn
                        mgr_index[first] = tg
                    if login:
                        mgr_index[login] = tg

            # ── 4. Обходим записи и импортируем ──────────────────────────────
            # Собираем: manager_name → список client_ids чтобы потом массово назначить
            manager_to_clients: dict[int, list[int]] = {}  # tg_id → [client_id]
            unmatched: dict[str, list[int]] = {}  # manager_name → [client_id]

            for record in records:
                fields = record.get("fields", {})

                client_name = _extract_field_value(fields, name_field).strip()
                if not client_name:
                    stats["skipped"] += 1
                    continue

                raw_segment = _extract_field_value(fields, segment_field)
                segment = _normalize_segment(raw_segment) if raw_segment else "SMB"
                # Проверяем что сегмент допустимый
                if segment not in ("ENT", "SME", "SME+", "SME-", "SMB", "SS"):
                    segment = "SMB"

                site_ids = _extract_field_value(fields, site_id_field) if site_id_field else ""
                # site_ids может быть числом
                if site_ids and not isinstance(site_ids, str):
                    site_ids = str(site_ids)
                # Убираем .0 у float
                if site_ids.endswith(".0"):
                    site_ids = site_ids[:-2]

                last_checkup_raw = _extract_field_value(fields, checkup_field) if checkup_field else ""
                # Нормализуем дату (может быть ISO или DD.MM.YYYY)
                last_checkup = None
                if last_checkup_raw:
                    try:
                        if "." in last_checkup_raw and len(last_checkup_raw) <= 10:
                            parts = last_checkup_raw.split(".")
                            if len(parts) == 3:
                                last_checkup = f"{parts[2]}-{parts[1]}-{parts[0]}"
                        elif "T" in last_checkup_raw:
                            last_checkup = last_checkup_raw[:10]
                        elif "-" in last_checkup_raw and len(last_checkup_raw) == 10:
                            last_checkup = last_checkup_raw
                    except Exception:
                        pass

                try:
                    client_id = upsert_client(
                        name=client_name,
                        segment=segment,
                        site_ids=site_ids,
                    )
                    # Обновляем last_checkup если есть
                    if last_checkup:
                        with get_conn() as conn:
                            conn.execute(
                                "UPDATE clients SET last_checkup=? WHERE id=? AND (last_checkup IS NULL OR last_checkup < ?)",
                                (last_checkup, client_id, last_checkup),
                            )

                    stats["created"] += 1

                except Exception as exc:
                    stats["errors"].append(f"{client_name}: {exc}")
                    stats["skipped"] += 1
                    continue

                # ── Менеджер ──────────────────────────────────────────────
                if manager_field:
                    mgr_raw = _extract_field_value(fields, manager_field).strip()
                    if mgr_raw:
                        mgr_key = mgr_raw.lower()
                        tg_id = mgr_index.get(mgr_key)

                        # Попробуем первое слово
                        if not tg_id:
                            first_word = mgr_key.split()[0] if " " in mgr_key else mgr_key
                            tg_id = mgr_index.get(first_word)

                        # Частичное совпадение по ключам индекса
                        if not tg_id:
                            for idx_name, idx_tg in mgr_index.items():
                                if idx_name in mgr_key or mgr_key in idx_name:
                                    tg_id = idx_tg
                                    break

                        if tg_id:
                            if tg_id not in manager_to_clients:
                                manager_to_clients[tg_id] = []
                            manager_to_clients[tg_id].append(client_id)
                        else:
                            if mgr_raw not in unmatched:
                                unmatched[mgr_raw] = []
                            unmatched[mgr_raw].append(client_id)

            # ── 5. Назначаем клиентов менеджерам ─────────────────────────────
            for tg_id, client_ids in manager_to_clients.items():
                # Получаем текущий список и добавляем новых (не перезаписываем)
                existing = set(get_manager_client_ids(tg_id))
                merged = list(existing | set(client_ids))
                set_manager_clients(tg_id, merged)
                stats["managers_linked"] += len(client_ids)
                logger.info("Manager tg_id=%d: assigned %d clients", tg_id, len(client_ids))

            stats["unmatched_managers"] = {
                name: len(ids) for name, ids in unmatched.items()
            }
            if unmatched:
                logger.warning(
                    "Airtable import: %d manager names not matched to profiles: %s",
                    len(unmatched), list(unmatched.keys()),
                )

    finally:
        if token and original_token:
            os.environ["AIRTABLE_TOKEN"] = original_token

    return stats
