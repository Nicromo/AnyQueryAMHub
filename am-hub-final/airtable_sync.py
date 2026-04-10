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

AIRTABLE_TOKEN    = os.getenv("AIRTABLE_TOKEN", "patGWwb2jBAKXddDI.f7f4a270685a36112d6958c8299056c7930f67c798908f74db6d81a0bae69d8d")
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


# ── Л2: Health / Risk sync ────────────────────────────────────────────────────

HEALTH_FIELD_NAMES = [
    "health score", "health", "здоровье клиента", "оценка здоровья", "am score",
]
RISK_FIELD_NAMES = [
    "risk score", "риск", "risk", "уровень риска", "risk level",
]


async def sync_health_to_airtable(
    client_name: str,
    health_score: int,
    health_color: str,
    risk_score: int,
    risk_level: str,
) -> bool:
    """
    Л2: Обновляем health_score и risk_score клиента в Airtable.
    Возвращает True если обновление прошло успешно.
    """
    if not AIRTABLE_TOKEN:
        return False

    async with httpx.AsyncClient(timeout=15) as hx:
        schema = await get_table_schema(hx)
        if not schema:
            return False

        record_id = await find_client_record(hx, schema, client_name)
        if not record_id:
            return False

        id_to_name = {v: k for k, v in schema.items()}
        health_fid = _find_field(schema, HEALTH_FIELD_NAMES)
        risk_fid   = _find_field(schema, RISK_FIELD_NAMES)
        health_field_name = id_to_name.get(health_fid) if health_fid else None
        risk_field_name   = id_to_name.get(risk_fid)   if risk_fid   else None

        fields_to_update: dict = {}

        if health_field_name:
            fields_to_update[health_field_name] = health_score
        else:
            fields_to_update["Health Score"] = health_score

        if risk_field_name:
            fields_to_update[risk_field_name] = risk_score
        else:
            fields_to_update["Risk Score"] = risk_score

        try:
            resp = await hx.patch(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
                headers=_headers(),
                json={"fields": fields_to_update},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info(
                    "Airtable health sync OK: %s health=%d(%s) risk=%d(%s)",
                    client_name, health_score, health_color, risk_score, risk_level,
                )
                return True
            logger.warning(
                "Airtable health sync error for %s: HTTP %d %s",
                client_name, resp.status_code, resp.text[:200],
            )
            return False
        except Exception as exc:
            logger.warning("Airtable health sync exception for %s: %s", client_name, exc)
            return False


# ── AR — Дебиторская задолженность из Airtable ────────────────────────────────

AR_FIELD_NAMES = [
    "дебиторская задолженность", "дебиторка", "долг", "задолженность",
    "ar", "accounts receivable", "receivable", "debt", "оплата", "баланс",
]
AR_DAYS_FIELD_NAMES = [
    "дней просрочки", "дни просрочки", "просрочка", "ar days",
    "overdue days", "days overdue",
]


async def fetch_ar_for_client(
    hx: httpx.AsyncClient,
    schema: dict,
    record_id: str,
) -> dict:
    """Получаем AR-поля для одной записи в Airtable."""
    id_to_name = {v: k for k, v in schema.items()}
    ar_fid   = _find_field(schema, AR_FIELD_NAMES)
    days_fid = _find_field(schema, AR_DAYS_FIELD_NAMES)
    ar_name   = id_to_name.get(ar_fid)   if ar_fid   else None
    days_name = id_to_name.get(days_fid) if days_fid else None

    # Если не нашли поле через схему — пробуем напрямую
    fallback_ar_names   = ["Дебиторская задолженность", "Дебиторка", "Долг", "AR", "Задолженность"]
    fallback_days_names = ["Дней просрочки", "Просрочка", "AR Days"]

    try:
        resp = await hx.get(
            f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
            headers=_headers(), timeout=10,
        )
        if resp.status_code != 200:
            return {"amount": 0.0, "days_overdue": 0}
        fields = resp.json().get("fields", {})

        # Ищем сумму задолженности
        amount = 0.0
        for fname in ([ar_name] if ar_name else []) + fallback_ar_names:
            if fname and fname in fields:
                val = fields[fname]
                if isinstance(val, (int, float)):
                    amount = float(val)
                elif isinstance(val, str):
                    import re
                    m = re.search(r'[\d.,]+', val.replace(" ", ""))
                    if m:
                        amount = float(m.group().replace(",", "."))
                break

        # Ищем дни просрочки
        days_overdue = 0
        for fname in ([days_name] if days_name else []) + fallback_days_names:
            if fname and fname in fields:
                val = fields[fname]
                if isinstance(val, (int, float)):
                    days_overdue = int(val)
                elif isinstance(val, str) and val.strip().isdigit():
                    days_overdue = int(val.strip())
                break

        return {"amount": amount, "days_overdue": days_overdue}
    except Exception as exc:
        logger.warning("fetch_ar_for_client error: %s", exc)
        return {"amount": 0.0, "days_overdue": 0}


async def sync_ar_from_airtable(clients: list[dict]) -> list[dict]:
    """
    Л5: Получает AR для всех клиентов из Airtable.
    Возвращает список {client_id, name, amount, days_overdue}
    """
    if not AIRTABLE_TOKEN:
        return []

    results = []
    async with httpx.AsyncClient(timeout=20) as hx:
        schema = await get_table_schema(hx)
        if not schema:
            return []

        for c in clients:
            record_id = await find_client_record(hx, schema, c["name"])
            if not record_id:
                continue
            ar = await fetch_ar_for_client(hx, schema, record_id)
            if ar["amount"] > 0 or ar["days_overdue"] > 0:
                results.append({
                    "client_id":    c["id"],
                    "name":         c["name"],
                    "amount":       ar["amount"],
                    "days_overdue": ar["days_overdue"],
                })
                logger.info("AR sync: %s amount=%.0f days=%d", c["name"], ar["amount"], ar["days_overdue"])

    return results


# ── Импорт клиентов из Airtable → локальная БД ───────────────────────────────

# Поля которые ищем при импорте клиентов
SEGMENT_FIELD_NAMES = [
    "сегмент", "segment", "тип", "тариф", "план", "plan", "tier",
]
SITE_ID_FIELD_NAMES = [
    "site_id", "site id", "сайт", "домен", "domain", "сайты", "site_ids",
    "id сайта", "merchrules id", "mr id", "url",
]
AM_FIELD_NAMES = [
    "аккаунт менеджер", "am", "менеджер", "account manager",
    "ответственный", "owner", "куратор",
]
CONTRACT_DATE_NAMES = [
    "дата окончания", "конец контракта", "contract end", "end date",
    "дата договора", "срок", "renewal date", "продление",
]
MRR_FIELD_NAMES = [
    "mrr", "mрр", "выручка", "revenue", "оплата", "сумма", "платёж",
    "ежемесячная оплата", "monthly",
]
TG_CHAT_FIELD_NAMES = [
    "telegram", "tg", "tg chat", "tg_chat_id", "чат", "chat id",
    "telegram chat", "канал",
]


def _extract_field_value(fields: dict, candidates: list[str], schema: dict | None = None) -> str:
    """Ищет значение поля по списку возможных имён. Возвращает строку или ''."""
    # Прямой поиск по нижнему регистру
    for name in candidates:
        for field_name, value in fields.items():
            if field_name.lower().strip() == name.lower():
                return str(value) if value is not None else ""
    # Неточный поиск
    for name in candidates:
        for field_name, value in fields.items():
            if name in field_name.lower() or field_name.lower() in name:
                return str(value) if value is not None else ""
    return ""


def _extract_numeric(fields: dict, candidates: list[str]) -> float:
    """Ищет числовое значение поля."""
    import re
    raw = _extract_field_value(fields, candidates)
    if not raw or raw in ("None", ""):
        return 0.0
    m = re.search(r"[\d.,]+", raw.replace(" ", "").replace("\xa0", ""))
    if m:
        try:
            return float(m.group().replace(",", "."))
        except ValueError:
            pass
    return 0.0


async def _fetch_all_airtable_records(hx: httpx.AsyncClient) -> list[dict]:
    """Загружаем все записи из таблицы Airtable с пагинацией."""
    records = []
    offset = None

    while True:
        params: dict = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        try:
            resp = await hx.get(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}",
                headers=_headers(),
                params=params,
                timeout=20,
            )
            if resp.status_code != 200:
                logger.warning("Airtable fetch all error: %s %s", resp.status_code, resp.text[:200])
                break
            body = resp.json()
            records.extend(body.get("records", []))
            offset = body.get("offset")
            if not offset:
                break
        except Exception as exc:
            logger.warning("Airtable fetch_all_records error: %s", exc)
            break

    return records


async def import_clients_from_airtable() -> dict:
    """
    Главная функция: тянет всех клиентов из Airtable → создаёт/обновляет в локальной БД.
    Возвращает {"created": N, "updated": N, "skipped": N, "errors": [...]}
    """
    if not AIRTABLE_TOKEN:
        return {"ok": False, "error": "AIRTABLE_TOKEN не задан"}

    from database import upsert_client_from_airtable

    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

    async with httpx.AsyncClient(timeout=30) as hx:
        # Загружаем схему для понимания структуры
        schema = await get_table_schema(hx)
        id_to_name = {v: k for k, v in schema.items()} if schema else {}

        records = await _fetch_all_airtable_records(hx)
        logger.info("Airtable import: fetched %d records", len(records))

        for rec in records:
            fields = rec.get("fields", {})
            record_id = rec.get("id", "")

            # Имя клиента — обязательное поле
            name = ""
            for fname in ["Name", "Клиент", "Название", "Сайт", "Компания", "Имя"]:
                if fname in fields and fields[fname]:
                    name = str(fields[fname]).strip()
                    break
            if not name:
                # Пробуем первое непустое текстовое поле
                for v in fields.values():
                    if isinstance(v, str) and len(v) > 2:
                        name = v.strip()
                        break

            if not name or len(name) < 2:
                stats["skipped"] += 1
                continue

            # Сегмент
            segment_raw = _extract_field_value(fields, SEGMENT_FIELD_NAMES)

            # Site IDs
            site_ids_raw = _extract_field_value(fields, SITE_ID_FIELD_NAMES)
            # Убираем лишние символы
            import re as _re
            site_ids = ",".join(
                s.strip() for s in _re.split(r"[,;\s]+", site_ids_raw) if s.strip().isdigit()
            )

            # AM
            am_name = _extract_field_value(fields, AM_FIELD_NAMES)

            # TG chat
            tg_chat_id = _extract_field_value(fields, TG_CHAT_FIELD_NAMES)
            # TG chat_id должен быть числом (или пустым)
            if tg_chat_id and not tg_chat_id.lstrip("-").isdigit():
                tg_chat_id = ""

            # Контракт и финансы
            contract_end = _extract_field_value(fields, CONTRACT_DATE_NAMES)
            # Нормализуем дату
            if contract_end:
                for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
                    try:
                        from datetime import datetime as _dt
                        contract_end = _dt.strptime(contract_end[:10], fmt).strftime("%Y-%m-%d")
                        break
                    except (ValueError, TypeError):
                        pass
                else:
                    contract_end = ""

            mrr = _extract_numeric(fields, MRR_FIELD_NAMES)

            # AR
            ar_amount    = _extract_numeric(fields, AR_FIELD_NAMES)
            ar_days_raw  = _extract_field_value(fields, AR_DAYS_FIELD_NAMES)
            ar_days_overdue = 0
            if ar_days_raw and ar_days_raw.strip().isdigit():
                ar_days_overdue = int(ar_days_raw.strip())

            try:
                upsert_client_from_airtable(
                    name=name,
                    segment=segment_raw,
                    site_ids=site_ids,
                    tg_chat_id=tg_chat_id,
                    am_name=am_name,
                    ar_amount=ar_amount,
                    ar_days_overdue=ar_days_overdue,
                    contract_end_date=contract_end,
                    mrr=mrr,
                    airtable_record_id=record_id,
                )
                stats["updated"] += 1
            except Exception as exc:
                logger.warning("Airtable import error for %s: %s", name, exc)
                stats["errors"].append({"name": name, "error": str(exc)})

    stats["ok"] = True
    logger.info("Airtable client import done: %s", stats)
    return stats
