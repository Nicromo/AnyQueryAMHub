"""
Airtable синхронизация клиентов → локальная БД.

Переменные окружения:
  AIRTABLE_TOKEN    = patXXX...
  AIRTABLE_BASE_ID  = appEAS1rPKpevoIel
  AIRTABLE_TABLE_ID = tblIKAi1gcFayRJTn
  AIRTABLE_QBR_TABLE_ID = tblqQbChhRYoZoxWu
  AIRTABLE_VIEW_ID  = (опционально)
"""

import os
import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _emit_csm_change_notification(db, client, old_email: str, new_email: str):
    """Сигнализирует что в Airtable у клиента сменился CSM.
    1) Создаёт Notification старому менеджеру: «клиент переназначен на X,
       хотите передать клиента в AM Hub?» с deeplink на приём передачи.
    2) Dedupe — не слать повторно в рамках 48 часов.
    3) Если pending ClientTransferRequest уже есть — не создаём новую запись."""
    from models import User, Notification, ClientTransferRequest
    old_email = (old_email or "").strip().lower()
    new_email = (new_email or "").strip().lower()
    if not old_email or old_email == new_email:
        return
    old_user = db.query(User).filter(User.email == old_email).first()
    new_user = db.query(User).filter(User.email == new_email).first()

    # Dedupe: не слать повторно в 48ч
    try:
        recent_cutoff = datetime.utcnow().timestamp() - 48 * 3600
        meta = client.integration_metadata or {}
        last_ts = meta.get("csm_change_notified_ts")
        if last_ts and float(last_ts) >= recent_cutoff:
            return
    except Exception:
        pass

    # 1. Notification старому менеджеру
    if old_user:
        msg_lines = [
            f"Клиент «{client.name}» переназначен в Airtable на {new_email}.",
            "Хотите создать запрос на передачу клиента в AM Hub?",
        ]
        n = Notification(
            user_id=old_user.id,
            title="🔄 CSM изменён в Airtable",
            message="\n".join(msg_lines),
            type="info",
            kind="csm_change",
            related_resource_type="client",
            related_resource_id=client.id,
            is_read=False,
            created_at=datetime.utcnow(),
        )
        db.add(n)

    # 2. Также сразу создаём черновой ClientTransferRequest (pending) если нет
    # уже открытого — чтобы новый менеджер тоже видел у себя в инбоксе
    if old_user and new_user:
        existing = (db.query(ClientTransferRequest)
                      .filter(ClientTransferRequest.client_id == client.id,
                              ClientTransferRequest.status == "pending")
                      .first())
        if not existing:
            tr = ClientTransferRequest(
                client_id=client.id,
                from_user_id=old_user.id,
                to_user_id=new_user.id,
                ai_summary=None,
                manual_note=f"Авто-создано: в Airtable CSM изменён на {new_email}.",
                status="pending",
            )
            db.add(tr)

    # Пишем timestamp в meta чтобы не дублировать уведомления
    try:
        from sqlalchemy.orm.attributes import flag_modified
        meta = dict(client.integration_metadata or {})
        meta["csm_change_notified_ts"] = datetime.utcnow().timestamp()
        meta["csm_change_last_old"] = old_email
        meta["csm_change_last_new"] = new_email
        client.integration_metadata = meta
        flag_modified(client, "integration_metadata")
    except Exception:
        pass

AIRTABLE_TOKEN        = os.getenv("AIRTABLE_TOKEN", "")
# Defaults hardcoded — user's specific base/tables, no Railway var needed
AIRTABLE_BASE_ID      = os.getenv("AIRTABLE_BASE_ID", "appEAS1rPKpevoIel")
AIRTABLE_TABLE_ID     = os.getenv("AIRTABLE_TABLE_ID", "tblIKAi1gcFayRJTn")
AIRTABLE_QBR_TABLE_ID = os.getenv("AIRTABLE_QBR_TABLE_ID", "tblqQbChhRYoZoxWu")
AIRTABLE_VIEW_ID      = os.getenv("AIRTABLE_VIEW_ID", "")

BASE_URL = "https://api.airtable.com/v0"

# ── EXACT field IDs for base appEAS1rPKpevoIel / table tblIKAi1gcFayRJTn ──────
EXACT_BASE_ID  = "appEAS1rPKpevoIel"
EXACT_TABLE_ID = "tblIKAi1gcFayRJTn"

FIELD_NAME           = "fldXeHkgIjzvr294Z"   # Клиент (name)
FIELD_SITE_ID        = "fldreqkwkEXrEGGwg"   # Айди личного кабинета
FIELD_MANAGER        = "fld0XMiWRh9xzvDy6"   # Аккаунт менеджер (bidirectional)
FIELD_DOMAIN         = "fldw7UmncgsP3OtOy"   # Сайт клиента
FIELD_MRR            = "fldtEBAWgyV35oVRK"   # МРР (bidirectional)
FIELD_SEGMENT        = "fldyvNxsQglqiQs48"   # Сегмент клиента (bidirectional)
FIELD_PRODUCTS       = "fldc5SOdHzGQD7QeI"   # Подключенные продукты (add/remove)
FIELD_CONTACTS       = "fldybBIJjTcxzB5T1"   # Контакты клиента (linked / multi-text)
FIELD_STATUS         = "fld4w5KAW9XCsOlOV"   # Статус клиента
FIELD_STATUS_COMMENT = "fldOeAhDVEnwcVpoG"   # Комментарий к статусу
FIELD_LAST_CONTACT   = "fldiOSJSuIQsXz7Z5"   # Дата последней коммуникации
# Вторая таблица для оплаты: tblLEQYWypaYtAcp6, поле "♥️Оплачено CSM"
PAYMENT_TABLE_ID     = os.getenv("AIRTABLE_PAYMENT_TABLE_ID", "tblLEQYWypaYtAcp6")
PAYMENT_STATUS_NAME  = "♥️Оплачено CSM"

# ── Heuristic candidates (fallback for other bases/tables) ────────────────────
NAME_CANDIDATES    = ["account", "клиент", "название", "name", "company", "аккаунт", "сайт"]
SEGMENT_CANDIDATES = ["customer stage", "размер клиента", "сегмент", "segment", "тариф", "tariff", "stage"]
MANAGER_CANDIDATES = ["csm", "менеджер", "manager", "am ", "account manager", "ответственный"]
SITE_ID_CANDIDATES = ["site id", "site_id", "siteid", "номер", "account id", "id аккаунта"]
CHECKUP_CANDIDATES = ["чекап", "checkup", "последняя встреча", "last meeting", "дата встречи", "дата последней коммуникации"]
HEALTH_CANDIDATES  = ["health", "хелс", "score"]
DOMAIN_CANDIDATES  = ["url", "домен", "domain", "website"]
GMV_CANDIDATES     = ["gmv", "оборот", "revenue", "выручка"]

VALID_SEGMENTS = {"ENT", "SME", "SME+", "SME-", "SMB", "SS"}


def _headers(token: str = "") -> dict:
    return {"Authorization": f"Bearer {token or AIRTABLE_TOKEN}", "Content-Type": "application/json"}


def _find_field(fields: dict, candidates: list) -> Optional[str]:
    keys_lower = {k.lower().strip(): k for k in fields}
    for cand in candidates:
        cand_l = cand.lower()
        for key_l, key_orig in keys_lower.items():
            if cand_l in key_l or key_l in cand_l:
                return key_orig
    return None


def _val(fields: dict, field_name: Optional[str]) -> str:
    if not field_name or field_name not in fields:
        return ""
    v = fields[field_name]
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("name") or v.get("text") or str(v)
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _val_list(fields: dict, field_name: Optional[str]) -> list:
    """Return field value as a list (for multi-select fields)."""
    if not field_name or field_name not in fields:
        return []
    v = fields[field_name]
    if isinstance(v, list):
        return [str(item) if not isinstance(item, dict) else (item.get("name") or item.get("text") or str(item)) for item in v]
    if v is None:
        return []
    return [str(v).strip()]


def _extract_manager_email(fields: dict, field_id: str) -> Optional[str]:
    """Extract manager email from a field that may be linked record, text or email."""
    if field_id not in fields:
        return None
    v = fields[field_id]
    # Linked record array: [{"id": "recXXX", "fields": {"email": "..."}}]
    if isinstance(v, list) and v:
        first = v[0]
        if isinstance(first, dict):
            # Check for email in nested fields
            nested = first.get("fields", {})
            for key in nested:
                val = nested[key]
                if isinstance(val, str) and "@" in val:
                    return val.lower()
            # Check direct email key
            if "email" in first:
                return first["email"].lower()
            # Sometimes it's just a string in the list
            if isinstance(first, str) and "@" in first:
                return first.lower()
        elif isinstance(first, str) and "@" in first:
            return first.lower()
    # Direct string value
    if isinstance(v, str) and "@" in v:
        return v.lower()
    # Dict with email key
    if isinstance(v, dict):
        for key in v:
            val = v[key]
            if isinstance(val, str) and "@" in val:
                return val.lower()
    return None


def _normalize_segment(raw: str) -> str:
    if not raw:
        return "SMB"
    r = raw.strip().upper()
    # прямые совпадения из Airtable Customer Stage / Размер клиента
    direct = {
        "ENT": "ENT", "ENTERPRISE": "ENT",
        "SME": "SME", "SME+": "SME+", "SME-": "SME-",
        "SMB": "SMB", "SS": "SS", "SELF-SERVICE": "SS",
    }
    if r in direct:
        return direct[r]
    # подстрока — приоритет длинным токенам
    for seg in ("SME+", "SME-", "ENT", "SME", "SMB", "SS"):
        if seg in r:
            return seg
    return "SMB"


def _parse_number(raw: str) -> Optional[float]:
    if not raw:
        return None
    s = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".").replace("%", "")
    try:
        return float(s)
    except Exception:
        return None


def _parse_date(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.000Z"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            continue
    return None


def _hub_fields_to_airtable(
    manager_email=None,
    mrr=None,
    segment=None,
    products=None,
    last_contact=None,
) -> dict:
    """Convert Hub field values to Airtable field ID dict for patch request."""
    update = {}
    if manager_email is not None:
        update[FIELD_MANAGER] = manager_email
    if mrr is not None:
        update[FIELD_MRR] = mrr
    if segment is not None:
        update[FIELD_SEGMENT] = segment
    if products is not None:
        update[FIELD_PRODUCTS] = products  # list or string
    if last_contact is not None:
        # Airtable date format: YYYY-MM-DD
        update[FIELD_LAST_CONTACT] = (
            last_contact.strftime("%Y-%m-%d")
            if hasattr(last_contact, "strftime")
            else str(last_contact)
        )
    return update


# Кеш: {(base_id, source_table_id, field_id): linked_table_id}
_LINKED_TABLE_CACHE: dict = {}


async def _get_linked_table_id(client, base_id: str, source_table_id: str,
                                field_id: str, token: str = "") -> Optional[str]:
    """Через Airtable Meta API узнать, в какую таблицу ссылается linked-поле.
    Нужно для contacts: в нашей таблице клиентов поле «Контакты клиента»
    (fldybBIJjTcxzB5T1) — linked-массив record-id из другой таблицы; без этой
    таблицы мы видим только «recXXX» вместо имён.

    Кешируется в памяти процесса (per-deploy).
    Возвращает None если schema API недоступен (напр. у токена нет scope
    schema.bases:read) — тогда синк просто оставит старое поведение.
    """
    cache_key = (base_id, source_table_id, field_id)
    if cache_key in _LINKED_TABLE_CACHE:
        return _LINKED_TABLE_CACHE[cache_key]
    try:
        resp = await client.get(
            f"{BASE_URL}/meta/bases/{base_id}/tables",
            headers=_headers(token),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.info("Airtable schema API → %d: %s (возможно у токена нет scope schema.bases:read)",
                        resp.status_code, resp.text[:150])
            _LINKED_TABLE_CACHE[cache_key] = None
            return None
        tables = resp.json().get("tables", [])
        for t in tables:
            if t.get("id") != source_table_id:
                continue
            for f in t.get("fields", []):
                if f.get("id") != field_id:
                    continue
                opts = f.get("options") or {}
                linked = opts.get("linkedTableId")
                _LINKED_TABLE_CACHE[cache_key] = linked
                return linked
        _LINKED_TABLE_CACHE[cache_key] = None
        return None
    except Exception as exc:
        logger.warning("Airtable schema fetch exception: %s", exc)
        _LINKED_TABLE_CACHE[cache_key] = None
        return None


# Типичные имена полей в Contacts-таблице — пробуем по порядку.
_CONTACT_NAME_KEYS     = ["Name", "name", "Имя", "ФИО", "Contact", "Контакт", "Full Name"]
_CONTACT_EMAIL_KEYS    = ["Email", "email", "E-mail", "Почта", "Mail"]
_CONTACT_PHONE_KEYS    = ["Phone", "phone", "Телефон", "Mobile", "Номер"]
_CONTACT_POSITION_KEYS = ["Position", "position", "Должность", "Title", "Role"]
_CONTACT_TG_KEYS       = ["Telegram", "telegram", "TG", "tg", "Телеграм"]


def _pick_first(fields: dict, keys: list) -> Optional[str]:
    """Вернуть первое непустое значение по списку возможных имён полей."""
    for k in keys:
        v = fields.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, list):
            # linked / multi-select: берём первый элемент
            if not v:
                continue
            item = v[0]
            if isinstance(item, dict):
                v = item.get("name") or item.get("email") or item.get("text") or ""
            else:
                v = str(item)
        elif isinstance(v, dict):
            v = v.get("name") or v.get("email") or v.get("text") or ""
        else:
            v = str(v)
        v = (v or "").strip()
        if v:
            return v
    return None


async def _fetch_linked_contacts(client, base_id: str, linked_table_id: str,
                                  record_ids: list, token: str = "") -> dict:
    """Забрать данные для указанных record_id из linked-таблицы контактов.
    Возвращает dict {record_id: {name, email, phone, position, telegram}}.
    Формула: OR(RECORD_ID()='rec1', RECORD_ID()='rec2', ...) — одним запросом.
    Airtable лимит на длину formula ~16 KB, это более чем достаточно для
    десятка контактов на клиента.
    """
    if not record_ids:
        return {}
    # OR(...) — до 100 ID за раз; режем на чанки на всякий случай.
    resolved: dict = {}
    for i in range(0, len(record_ids), 100):
        chunk = record_ids[i:i+100]
        formula = "OR(" + ",".join([f"RECORD_ID()='{r}'" for r in chunk]) + ")"
        try:
            resp = await client.get(
                f"{BASE_URL}/{base_id}/{linked_table_id}",
                headers=_headers(token),
                params={"filterByFormula": formula, "pageSize": 100},
                timeout=20,
            )
            if resp.status_code != 200:
                logger.warning("Airtable contacts fetch → %d: %s",
                               resp.status_code, resp.text[:200])
                continue
            for rec in resp.json().get("records", []):
                rid = rec.get("id")
                f = rec.get("fields", {}) or {}
                if not rid:
                    continue
                resolved[rid] = {
                    "name":     _pick_first(f, _CONTACT_NAME_KEYS),
                    "email":    _pick_first(f, _CONTACT_EMAIL_KEYS),
                    "phone":    _pick_first(f, _CONTACT_PHONE_KEYS),
                    "position": _pick_first(f, _CONTACT_POSITION_KEYS),
                    "telegram": _pick_first(f, _CONTACT_TG_KEYS),
                }
        except Exception as exc:
            logger.warning("Airtable contacts fetch exception: %s", exc)
    return resolved


async def _fetch_one_record(client, table_id: str, record_id: str, token: str = "") -> Optional[dict]:
    """Забрать одну запись по её airtable record_id. Вернёт dict {id, fields, ...}
    или None если записи нет / ошибка. Для per-client sync — гораздо дешевле
    полного пагинированного обхода."""
    try:
        resp = await client.get(
            f"{BASE_URL}/{AIRTABLE_BASE_ID}/{table_id}/{record_id}",
            headers=_headers(token),
            params={"returnFieldsByFieldId": "true"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("Airtable single-record fetch %s → %d: %s",
                           record_id, resp.status_code, resp.text[:200])
            return None
        return resp.json()
    except Exception as exc:
        logger.error("Airtable single-record fetch %s exception: %s", record_id, exc)
        return None


async def _fetch_all_records(client, table_id: str, view_id: str = "", token: str = "") -> list:
    """Скачать ВСЕ записи с пагинацией (Airtable даёт по 100 за раз)."""
    records = []
    offset = None
    page = 0
    while True:
        page += 1
        params = {
            "pageSize": 100,
            # Критично: без этого Airtable отдаёт ключами имена полей ("Account",
            # "CSM"), а у нас захардкожены field-id ("fld..."). Тогда все
            # lookup'ы возвращают None и sync проходит вхолостую.
            "returnFieldsByFieldId": "true",
        }
        if view_id:
            params["view"] = view_id
        if offset:
            params["offset"] = offset
        try:
            resp = await client.get(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{table_id}",
                headers=_headers(token),
                params=params,
                timeout=30,
            )
            if resp.status_code != 200:
                logger.error("Airtable page %d error %d: %s", page, resp.status_code, resp.text[:300])
                break
            data = resp.json()
            batch = data.get("records", [])
            records.extend(batch)
            logger.info("Airtable page %d: +%d (total %d)", page, len(batch), len(records))
            offset = data.get("offset")
            if not offset:
                break
        except Exception as exc:
            logger.error("Airtable fetch exception: %s", exc)
            break
    return records


def _use_exact_fields(use_base: str, use_table: str) -> bool:
    """Return True if we should use exact field IDs (specific base/table combo)."""
    return use_base == EXACT_BASE_ID and use_table == EXACT_TABLE_ID


async def sync_clients_from_airtable(
    db,
    token: str = "",
    base_id: Optional[str] = None,
    view_id: str = "",
    default_manager_email: str = "",
    table_id: str = "",
    only_record_id: Optional[str] = None,
) -> dict:
    """Синхронизировать всех клиентов из Airtable в таблицу clients.

    Если base_id == appEAS1rPKpevoIel и table_id == tblIKAi1gcFayRJTn —
    используем точные field ID (FIELD_NAME, FIELD_MANAGER, и т.д.).
    Иначе — эвристическое определение по названиям полей (fallback).

    only_record_id: если передан — синкаем только одну запись (per-client sync
    по кнопке на карточке). Гораздо дешевле: один GET к Airtable + апдейт
    одной строки в БД вместо прохода по 100+ клиентам.

    Идемпотентность: upsert по airtable_record_id → airtable_site_id → name.
    """
    from models import Client

    # Must declare global before any use of the variable in this scope
    global AIRTABLE_BASE_ID

    use_token = token or AIRTABLE_TOKEN
    use_base = base_id or AIRTABLE_BASE_ID
    use_table = table_id or AIRTABLE_TABLE_ID
    if not use_token:
        return {"ok": False, "error": "AIRTABLE_TOKEN не задан", "created": 0, "updated": 0, "skipped": 0, "errors": []}
    if not use_base or not use_table:
        return {"ok": False, "error": "AIRTABLE_BASE_ID/AIRTABLE_TABLE_ID не заданы", "created": 0, "updated": 0, "skipped": 0, "errors": []}
    _saved_base = AIRTABLE_BASE_ID
    AIRTABLE_BASE_ID = use_base
    try:
        async with httpx.AsyncClient(timeout=60) as hx:
            if only_record_id:
                one = await _fetch_one_record(hx, use_table, only_record_id, use_token)
                records = [one] if one else []
            else:
                records = await _fetch_all_records(hx, use_table, view_id or AIRTABLE_VIEW_ID, use_token)
    finally:
        AIRTABLE_BASE_ID = _saved_base

    if not records:
        return {"ok": False, "error": "Нет записей или ошибка доступа к Airtable",
                "created": 0, "updated": 0, "skipped": 0, "errors": []}

    exact = _use_exact_fields(use_base, use_table)
    logger.info("Airtable sync mode: %s (base=%s, table=%s)", "exact field IDs" if exact else "heuristic", use_base, use_table)

    if not exact:
        # ── FALLBACK: heuristic field detection ──────────────────────
        sample: dict = {}
        for r in records[:10]:
            sample.update(r.get("fields", {}))

        f_name    = _find_field(sample, NAME_CANDIDATES)
        f_segment = _find_field(sample, SEGMENT_CANDIDATES)
        f_manager = _find_field(sample, MANAGER_CANDIDATES)
        f_site    = _find_field(sample, SITE_ID_CANDIDATES)
        f_checkup = _find_field(sample, CHECKUP_CANDIDATES)
        f_health  = _find_field(sample, HEALTH_CANDIDATES)
        f_domain  = _find_field(sample, DOMAIN_CANDIDATES)
        f_gmv     = _find_field(sample, GMV_CANDIDATES)

        field_map = {"name": f_name, "segment": f_segment, "manager": f_manager,
                     "site_id": f_site, "checkup": f_checkup, "health": f_health,
                     "domain": f_domain, "gmv": f_gmv,
                     "all_fields": list(sample.keys())}

        if not f_name:
            return {"ok": False, "error": "Не найдено поле с именем клиента",
                    "created": 0, "updated": 0, "skipped": 0, "errors": [], "field_map": field_map}

        logger.info("Airtable field map (heuristic): %s", {k: v for k, v in field_map.items() if k != "all_fields"})
    else:
        field_map = {
            "name": FIELD_NAME, "segment": FIELD_SEGMENT, "manager": FIELD_MANAGER,
            "site_id": FIELD_SITE_ID, "checkup": FIELD_LAST_CONTACT, "health": None,
            "domain": FIELD_DOMAIN, "gmv": None, "mrr": FIELD_MRR,
            "products": FIELD_PRODUCTS, "status": FIELD_STATUS,
            "status_comment": FIELD_STATUS_COMMENT,
            "mode": "exact_field_ids",
        }
        logger.info("Airtable field map (exact): using hardcoded field IDs for %s/%s", use_base, use_table)

    created = updated = skipped = 0
    errors = []

    # Дедуп по нормализованному имени: строка "Yves Rocher" и "yves-rocher"
    # становятся одним ключом "yvesrocher" → один Client в БД.
    import re as _re
    def _name_key(s: str) -> str:
        return _re.sub(r"[^a-zа-я0-9]", "", (s or "").lower())

    # Если имя явно не похоже на клиента (длинное описание / пробелы посреди
    # кириллических предложений), пропускаем запись. 60 символов без
    # специальных разделителей — порог.
    def _looks_like_real_client(n: str) -> bool:
        n = (n or "").strip()
        if not n or len(n) > 80:
            return False
        # Если больше 6 пробелов ИЛИ в имени встречается характерная лексика
        # описательной записи — отбрасываем.
        if n.count(" ") > 6:
            return False
        bad_tokens = ["лучш", "стабиль", "увеличен", "не-падение", "сервис без", "обработк"]
        low = n.lower()
        return not any(t in low for t in bad_tokens)

    seen_keys = set()

    # Собираем ссылочные контакт-recId'ы для post-processing (см. после цикла).
    # {client_id: [rec_id, ...]}
    pending_linked_contacts: dict = {}

    for record in records:
        fields = record.get("fields", {})
        airtable_id = record.get("id", "")

        if exact:
            # ── EXACT field ID path ──────────────────────────────────
            name_raw = fields.get(FIELD_NAME)
            if isinstance(name_raw, list):
                name = str(name_raw[0]).strip() if name_raw else ""
            elif isinstance(name_raw, dict):
                name = (name_raw.get("name") or name_raw.get("text") or "").strip()
            else:
                name = str(name_raw).strip() if name_raw else ""

            # Мусорные имена: слишком длинные, с фразами про фичи/задачи
            if not _looks_like_real_client(name):
                skipped += 1
                continue

            # Дедуп: если такое имя уже видели в этом же sync — пропускаем
            nkey = _name_key(name)
            if nkey in seen_keys:
                skipped += 1
                continue
            seen_keys.add(nkey)

            if not name:
                skipped += 1
                continue

            # Manager email (linked record or text).
            # Airtable возвращает FIELD_MANAGER как массив record-id'шников
            # (["recXXX"]). _extract_manager_email умеет достать email только
            # если linked-запись была expanded (у нас не всегда). Поэтому
            # три шага:
            #   1. Попробовать достать email напрямую.
            #   2. Если не вышло, но в поле ЕСТЬ линк — запись закреплена за
            #      кем-то, и если пришла через вьюху текущего юзера — это он.
            #      Используем default_manager_email как fallback.
            #   3. Если поле вообще пустое — manager_email остаётся None
            #      (это «ничей» клиент, в портфель не попадёт).
            manager_email = _extract_manager_email(fields, FIELD_MANAGER)
            if not manager_email:
                raw = fields.get(FIELD_MANAGER)
                has_link = bool(raw) and (
                    (isinstance(raw, list) and len(raw) > 0)
                    or (isinstance(raw, str) and raw.strip())
                )
                if has_link and default_manager_email:
                    manager_email = default_manager_email

            # Site ID
            site_id_raw = fields.get(FIELD_SITE_ID)
            if isinstance(site_id_raw, list):
                site_id = str(site_id_raw[0]).strip() if site_id_raw else None
            elif site_id_raw:
                s = str(site_id_raw).strip()
                site_id = s[:-2] if s.endswith(".0") else s
            else:
                site_id = None

            # Domain
            domain_raw = fields.get(FIELD_DOMAIN, "")
            domain = str(domain_raw).strip() if domain_raw else None
            if domain:
                for proto in ("https://", "http://"):
                    if domain.startswith(proto):
                        domain = domain[len(proto):]
                domain = domain.rstrip("/") or None

            # Segment
            seg_raw = _val(fields, FIELD_SEGMENT)
            segment = _normalize_segment(seg_raw) if seg_raw else None

            # MRR
            mrr_raw = fields.get(FIELD_MRR)
            mrr = None
            if mrr_raw is not None:
                try:
                    mrr = float(str(mrr_raw).replace(" ", "").replace(",", "."))
                except Exception:
                    pass

            # Products (multi-select)
            products_list = _val_list(fields, FIELD_PRODUCTS)

            # Contacts (text / linked / multi-text)
            contacts_raw = fields.get(FIELD_CONTACTS)

            # Last contact date
            last_contact_raw = fields.get(FIELD_LAST_CONTACT, "")
            last_checkup = _parse_date(str(last_contact_raw)) if last_contact_raw else None

            health = None  # not in exact mapping

        else:
            # ── HEURISTIC path ───────────────────────────────────────
            name = _val(fields, f_name)
            if not name:
                skipped += 1
                continue

            segment = _normalize_segment(_val(fields, f_segment)) if f_segment else None
            manager_raw = _val(fields, f_manager)
            manager_email = manager_raw.lower() if (manager_raw and "@" in manager_raw) else (default_manager_email or None)
            site_id = _val(fields, f_site) or None
            domain  = _val(fields, f_domain) or None
            if domain:
                for proto in ("https://", "http://"):
                    if domain.startswith(proto):
                        domain = domain[len(proto):]
                domain = domain.rstrip("/")
            last_checkup = _parse_date(_val(fields, f_checkup))
            health = None
            if f_health:
                h = _parse_number(_val(fields, f_health))
                if h is not None:
                    health = h / 100 if h > 1 else h
            gmv = _parse_number(_val(fields, f_gmv)) if f_gmv else None
            mrr = None

        try:
            # Идемпотентный lookup: record_id → airtable_site_id → merchrules_account_id → name
            c = None
            if airtable_id:
                c = db.query(Client).filter(Client.airtable_record_id == airtable_id).first()
            if not c and site_id:
                c = db.query(Client).filter(Client.airtable_site_id == site_id).first()
            if not c and site_id:
                c = db.query(Client).filter(Client.merchrules_account_id == site_id).first()
            if not c:
                c = db.query(Client).filter(Client.name == name).first()

            if c:
                c.name = name
                if segment:
                    c.segment = segment
                # CSM-change detection: если в Airtable сменился менеджер,
                # уведомляем старого менеджера в AM Hub и предлагаем создать
                # ClientTransferRequest на нового. Сам manager_email пока
                # оставляем старый — пусть менеджер через accept передаст
                # клиента явно. Админ всё равно увидит смену в следующем синке.
                if manager_email and c.manager_email and \
                        c.manager_email.lower() != (manager_email or "").lower():
                    try:
                        _emit_csm_change_notification(
                            db, c, old_email=c.manager_email, new_email=manager_email
                        )
                    except Exception as _ne:
                        logger.warning(f"csm_change notify failed for {c.id}: {_ne}")
                    # Сохраняем новый manager_email в метаданных для показа
                    # «в Airtable теперь X». Сам Client.manager_email не трогаем.
                    meta = dict(c.integration_metadata or {})
                    meta["airtable_manager_email"] = manager_email
                    meta["airtable_csm_changed_at"] = datetime.utcnow().isoformat()
                    c.integration_metadata = meta
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(c, "integration_metadata")
                elif manager_email and not c.manager_email:
                    # Клиент был без менеджера → присваиваем
                    c.manager_email = manager_email
                if site_id:
                    c.airtable_site_id = site_id
                    if not c.merchrules_account_id:
                        c.merchrules_account_id = site_id
                if domain:
                    c.domain = domain
                if last_checkup:
                    c.last_checkup = last_checkup
                if health is not None:
                    c.health_score = health
                if mrr is not None:
                    c.mrr = mrr
                if not exact and gmv is not None:
                    c.gmv = gmv
                if airtable_id:
                    c.airtable_record_id = airtable_id
                c.last_sync_at = datetime.utcnow()
                updated += 1
            else:
                kwargs = dict(
                    name=name,
                    segment=segment,
                    manager_email=manager_email,
                    airtable_site_id=site_id,
                    merchrules_account_id=site_id,
                    domain=domain,
                    last_checkup=last_checkup,
                    health_score=health or 0.0,
                    airtable_record_id=airtable_id,
                    last_sync_at=datetime.utcnow(),
                )
                if mrr is not None:
                    kwargs["mrr"] = mrr
                if not exact:
                    kwargs["gmv"] = gmv or 0.0
                c = Client(**kwargs)
                db.add(c)
                db.flush()
                created += 1

            # ── Products → ClientProduct ─────────────────────────────
            if exact and c and c.id:
                try:
                    from models import ClientProduct
                    # Маппинг названий в code
                    _code_map = {
                        "поиск": "search", "search": "search",
                        "рекомендации": "recommendations", "recs": "recommendations", "anyrecs": "recommendations",
                        "баннеры": "banners", "banners": "banners",
                        "персонализация": "personalization", "personal": "personalization",
                        "email": "email", "почта": "email",
                        "push": "push",
                        "seo": "seo",
                        "отзывы": "reviews", "reviews": "reviews", "anyreviews": "reviews",
                        "фото": "images", "images": "images", "anyimages": "images",
                    }
                    def _product_code(nm: str) -> str:
                        low = (nm or "").lower().strip()
                        for k, v in _code_map.items():
                            if k in low:
                                return v
                        return "".join(ch for ch in low if ch.isalnum())[:40] or "other"

                    desired = [(_product_code(p), p) for p in (products_list or []) if p]
                    existing = {p.code: p for p in db.query(ClientProduct).filter(ClientProduct.client_id == c.id).all()}
                    desired_codes = {code for code, _ in desired}
                    # Удалить лишние
                    for code, p in existing.items():
                        if code not in desired_codes:
                            db.delete(p)
                    # Добавить / обновить
                    for code, name_p in desired:
                        if code in existing:
                            existing[code].name = name_p
                            existing[code].status = "active"
                        else:
                            db.add(ClientProduct(client_id=c.id, code=code, name=name_p, status="active"))
                except Exception as _pe:
                    logger.debug(f"products upsert failed for {c.name}: {_pe}")

            # ── Contacts → ClientContact ─────────────────────────────
            # Поле «Контакты клиента» (fldybBIJjTcxzB5T1) может быть:
            #   (a) linked-массив record-id ["recXXX", ...] — ссылается на отдельную
            #       таблицу контактов с полями Name/Email/Phone/Telegram/Position;
            #   (b) многострочный текст (имена/email в произвольном формате);
            #   (c) массив dict'ов (expanded linked records).
            # Для (a) мы собираем rec-id'ы в pending_linked_contacts и разрешаем
            # их одним batch-запросом ПОСЛЕ цикла (см. ниже). Для (b)/(c) —
            # парсим inline, как раньше.
            if exact and c and c.id and contacts_raw:
                try:
                    from models import ClientContact
                    linked_ids: list = []
                    inline_raw: list = []
                    if isinstance(contacts_raw, list):
                        for item in contacts_raw:
                            if isinstance(item, dict):
                                # Уже expanded linked record
                                inline_raw.append(item.get("name") or item.get("text") or item.get("email") or "")
                            elif isinstance(item, str) and item.startswith("rec") and len(item) >= 17:
                                linked_ids.append(item)
                            else:
                                inline_raw.append(str(item))
                    elif isinstance(contacts_raw, str):
                        for part in contacts_raw.replace(";", "\n").replace(",", "\n").split("\n"):
                            if part.strip(): inline_raw.append(part.strip())

                    if linked_ids:
                        # Копим — разрешим одним batch'ем после цикла
                        pending_linked_contacts[c.id] = linked_ids

                    if inline_raw:
                        # Получить существующие, сопоставить по name
                        existing = {(ct.name or "").strip().lower(): ct
                                    for ct in db.query(ClientContact).filter(ClientContact.client_id == c.id).all()}
                        for raw_c in inline_raw:
                            raw_c = (raw_c or "").strip()
                            if not raw_c: continue
                            email = raw_c if "@" in raw_c else None
                            name_c = raw_c
                            key = name_c.lower()
                            if key in existing:
                                if email and not existing[key].email:
                                    existing[key].email = email
                            else:
                                db.add(ClientContact(client_id=c.id, name=name_c[:200],
                                                      email=email, role=None))
                except Exception as _ce:
                    logger.debug(f"contacts upsert failed for {c.name}: {_ce}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            skipped += 1

    # ── Resolve linked contacts (batch fetch from Contacts table) ─────────────
    # Если в поле «Контакты клиента» у каких-то клиентов были recXXX —
    # ищем через schema API целевую таблицу и одним запросом получаем
    # name/email/phone/telegram/position для ВСЕХ упомянутых контактов.
    if exact and pending_linked_contacts:
        try:
            from models import ClientContact
            all_ids: list = []
            for ids in pending_linked_contacts.values():
                all_ids.extend(ids)
            all_ids = list(dict.fromkeys(all_ids))  # dedup, preserve order

            async with httpx.AsyncClient(timeout=30) as hx2:
                linked_table = await _get_linked_table_id(
                    hx2, use_base, use_table, FIELD_CONTACTS, use_token,
                )
                resolved: dict = {}
                if linked_table:
                    resolved = await _fetch_linked_contacts(
                        hx2, use_base, linked_table, all_ids, use_token,
                    )
                else:
                    logger.info("Airtable: не смог определить linked-таблицу контактов "
                                "(нужен scope schema.bases:read у PAT). "
                                "Контакты останутся как recXXX — можно добавить scope в Airtable → PAT.")

            if resolved:
                for client_id, rec_ids in pending_linked_contacts.items():
                    existing_by_airtable = {
                        ct.airtable_record_id: ct
                        for ct in db.query(ClientContact)
                          .filter(ClientContact.client_id == client_id,
                                  ClientContact.airtable_record_id.isnot(None))
                          .all()
                    }
                    for rid in rec_ids:
                        data = resolved.get(rid)
                        if not data:
                            continue
                        name = (data.get("name") or rid)[:200]
                        email = data.get("email")
                        phone = data.get("phone")
                        position = data.get("position")
                        telegram = data.get("telegram")
                        ct = existing_by_airtable.get(rid)
                        if ct:
                            ct.name = name
                            if email:    ct.email = email
                            if phone:    ct.phone = phone
                            if position: ct.position = position
                            if telegram: ct.telegram = telegram
                        else:
                            db.add(ClientContact(
                                client_id=client_id,
                                airtable_record_id=rid,
                                name=name,
                                email=email,
                                phone=phone,
                                position=position,
                                telegram=telegram,
                                role=None,
                            ))
                # Удаляем устаревшие airtable-контакты: те, которых больше нет в
                # recXXX-списке клиента (менеджер убрал в Airtable).
                for client_id, rec_ids in pending_linked_contacts.items():
                    keep = set(rec_ids)
                    stale = (db.query(ClientContact)
                               .filter(ClientContact.client_id == client_id,
                                       ClientContact.airtable_record_id.isnot(None))
                               .all())
                    for ct in stale:
                        if ct.airtable_record_id not in keep:
                            db.delete(ct)
        except Exception as exc:
            logger.warning("linked-contacts resolution failed: %s", exc)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": f"DB error: {exc}",
                "created": created, "updated": updated, "skipped": skipped, "errors": errors[:10]}

    return {"ok": True, "total": len(records),
            "created": created, "updated": updated, "skipped": skipped,
            "synced": created + updated,
            "errors": errors[:10], "field_map": field_map}


async def sync_meeting_to_airtable(client_name: str, meeting_date: datetime, comment: str = "", token: str = "") -> bool:
    """Обновить дату встречи и дописать комментарий в Airtable."""
    use_token = token or AIRTABLE_TOKEN
    if not use_token:
        return False

    exact = _use_exact_fields(AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID)

    async with httpx.AsyncClient(timeout=20) as hx:
        try:
            resp = await hx.get(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}",
                headers=_headers(use_token),
                params={"filterByFormula": f'SEARCH("{client_name}", {{Name}})', "pageSize": 5},
                timeout=15,
            )
            if resp.status_code != 200:
                return False
            records = resp.json().get("records", [])
            if not records:
                return False
            record_id = records[0]["id"]
            fields = records[0].get("fields", {})

            update: dict = {}
            if exact:
                # Use exact field IDs directly
                update[FIELD_LAST_CONTACT] = meeting_date.strftime("%Y-%m-%d")
                if comment:
                    existing = _val(fields, FIELD_STATUS_COMMENT)
                    ts = meeting_date.strftime("%d.%m.%Y")
                    update[FIELD_STATUS_COMMENT] = f"{existing}\n[{ts}] {comment}".strip() if existing else f"[{ts}] {comment}"
            else:
                # Heuristic fallback
                f_date    = _find_field(fields, CHECKUP_CANDIDATES)
                f_comment = _find_field(fields, ["комментарий", "notes", "comments", "история"])
                if f_date:
                    update[f_date] = meeting_date.strftime("%Y-%m-%d")
                if f_comment and comment:
                    existing = _val(fields, f_comment)
                    ts = meeting_date.strftime("%d.%m.%Y")
                    update[f_comment] = f"{existing}\n[{ts}] {comment}".strip() if existing else f"[{ts}] {comment}"

            if not update:
                return False
            patch = await hx.patch(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
                headers=_headers(use_token),
                json={"fields": update},
                timeout=15,
            )
            return patch.status_code == 200
        except Exception as exc:
            logger.error("sync_meeting_to_airtable: %s", exc)
            return False


async def push_client_fields_to_airtable(
    record_id: str,
    fields: dict,
    token: str = "",
) -> bool:
    """
    Обновить произвольные поля записи клиента в Airtable.
    fields — словарь {field_id_or_name: value}.

    Для базы appEAS1rPKpevoIel / tblIKAi1gcFayRJTn используйте
    _hub_fields_to_airtable() для построения словаря с точными field ID.
    """
    use_token = token or AIRTABLE_TOKEN
    if not use_token or not AIRTABLE_BASE_ID or not AIRTABLE_TABLE_ID:
        logger.warning("push_client_fields_to_airtable: missing config")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.patch(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}",
                headers=_headers(use_token),
                json={"fields": fields},
                timeout=15,
            )
            if resp.status_code == 200:
                logger.info("Airtable client %s updated", record_id)
                return True
            logger.warning("Airtable push error: %d %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("push_client_fields_to_airtable error: %s", e)
    return False


async def push_qbr_to_airtable(
    client_name: str,
    quarter: str,
    summary: str,
    achievements: list,
    date=None,
    manager_email: str = "",
    token: str = "",
) -> bool:
    """
    Создать/обновить QBR запись в Airtable QBR-таблице (tblqQbChhRYoZoxWu).

    Сначала ищем существующую запись по client_name + quarter.
    Если есть — PATCH, если нет — POST.
    """
    qbr_table = AIRTABLE_QBR_TABLE_ID or "tblqQbChhRYoZoxWu"
    use_token = token or AIRTABLE_TOKEN
    if not use_token or not AIRTABLE_BASE_ID or not qbr_table:
        logger.warning("push_qbr_to_airtable: missing Airtable config")
        return False
    try:
        fields: dict = {}
        if client_name:    fields["Проект"]   = client_name
        if quarter:        fields["Квартал"]  = quarter
        if summary:        fields["Итоги"]    = summary
        if achievements:   fields["Достижения"] = "\n".join(achievements)
        if manager_email:  fields["Менеджер"] = manager_email
        # date → write into the month column matching the quarter
        if date:
            try:
                d = date if hasattr(date, "strftime") else datetime.fromisoformat(str(date)[:10])
                # Column name format used in Airtable: "мар.26", "апр.26" etc.
                MONTH_RU = ["янв", "фев", "мар", "апр", "май", "июн",
                            "июл", "авг", "сен", "окт", "ноя", "дек"]
                col = f"{MONTH_RU[d.month - 1]}.{str(d.year)[2:]}"
                fields[col] = d.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except Exception as e:
                logger.warning("push_qbr_to_airtable: could not format date: %s", e)

        async with httpx.AsyncClient(timeout=15) as hx:
            # Try to find existing record
            search = await hx.get(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{qbr_table}",
                headers=_headers(use_token),
                params={"filterByFormula": f'AND({{Проект}}="{client_name}", {{Квартал}}="{quarter}")', "pageSize": 1},
                timeout=10,
            )
            existing_id = None
            if search.status_code == 200:
                recs = search.json().get("records", [])
                if recs:
                    existing_id = recs[0]["id"]

            if existing_id:
                resp = await hx.patch(
                    f"{BASE_URL}/{AIRTABLE_BASE_ID}/{qbr_table}/{existing_id}",
                    headers=_headers(use_token),
                    json={"fields": fields},
                    timeout=15,
                )
            else:
                resp = await hx.post(
                    f"{BASE_URL}/{AIRTABLE_BASE_ID}/{qbr_table}",
                    headers=_headers(use_token),
                    json={"fields": fields},
                    timeout=15,
                )
            ok = resp.status_code in (200, 201)
            if not ok:
                logger.warning("push_qbr_to_airtable: %d %s", resp.status_code, resp.text[:200])
            return ok
    except Exception as e:
        logger.error("push_qbr_to_airtable error: %s", e)
    return False


async def sync_qbr_from_airtable(db, token: str = "") -> dict:
    """Sync QBR calendar from Airtable tblqQbChhRYoZoxWu.

    Each record has:
      - client name (linked field)
      - manager email (from linked Clients table or separate field)
      - date columns per month: мар.26, апр.26, май.26, etc.

    Upserts into the QBR table (match by client_id + quarter).
    """
    from models import QBR, Client

    use_token = token or AIRTABLE_TOKEN
    qbr_table = AIRTABLE_QBR_TABLE_ID or "tblqQbChhRYoZoxWu"

    if not use_token:
        return {"ok": False, "error": "AIRTABLE_TOKEN не задан", "created": 0, "updated": 0, "skipped": 0}
    if not AIRTABLE_BASE_ID:
        return {"ok": False, "error": "AIRTABLE_BASE_ID не задан", "created": 0, "updated": 0, "skipped": 0}

    async with httpx.AsyncClient(timeout=60) as hx:
        records = await _fetch_all_records(hx, qbr_table, "", use_token)

    if not records:
        return {"ok": False, "error": "Нет записей QBR или ошибка доступа",
                "created": 0, "updated": 0, "skipped": 0}

    # Detect month columns dynamically by scanning all field values for YYYY-MM-DD dates
    sample: dict = {}
    for r in records[:10]:
        sample.update(r.get("fields", {}))

    # Month columns: fields whose values look like dates (YYYY-MM-DD)
    import re
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}")
    month_fields = []
    for key, val in sample.items():
        if isinstance(val, str) and date_pattern.match(val):
            month_fields.append(key)
        elif isinstance(val, list) and val and isinstance(val[0], str) and date_pattern.match(val[0]):
            month_fields.append(key)

    logger.info("QBR sync: detected %d month fields: %s", len(month_fields), month_fields)

    # Candidate fields for client name and manager
    f_client_name  = _find_field(sample, ["проект", "клиент", "client", "name", "account"])
    f_manager_email = _find_field(sample, ["email", "csm", "менеджер", "manager", "am "])

    created = updated = skipped = 0
    errors = []

    for record in records:
        fields = record.get("fields", {})
        airtable_id = record.get("id", "")

        # Extract client name
        client_name = _val(fields, f_client_name) if f_client_name else ""
        if not client_name:
            skipped += 1
            continue

        # Extract manager email
        manager_email = None
        if f_manager_email:
            raw = _val(fields, f_manager_email)
            if raw and "@" in raw:
                manager_email = raw.lower()
        if not manager_email:
            manager_email = _extract_manager_email(fields, f_manager_email or "")

        # Find client in DB
        client_obj = db.query(Client).filter(Client.name == client_name).first()
        client_id = client_obj.id if client_obj else None
        if not manager_email and client_obj and client_obj.manager_email:
            manager_email = client_obj.manager_email

        # Process each month column as a separate QBR record
        for month_field in month_fields:
            date_raw = fields.get(month_field)
            if not date_raw:
                continue
            qbr_date = _parse_date(str(date_raw))
            if not qbr_date:
                continue

            # Derive quarter from date
            quarter_num = (qbr_date.month - 1) // 3 + 1
            quarter = f"{qbr_date.year}-Q{quarter_num}"
            year = qbr_date.year

            try:
                # Upsert: match by airtable_record_id + month_field, or client_id + quarter
                qbr_obj = None
                if client_id:
                    qbr_obj = db.query(QBR).filter(
                        QBR.client_id == client_id,
                        QBR.quarter == quarter,
                    ).first()

                status = "completed" if qbr_date < datetime.utcnow() else "scheduled"

                if qbr_obj:
                    qbr_obj.date = qbr_date
                    qbr_obj.status = status
                    if manager_email and hasattr(qbr_obj, "manager_email"):
                        qbr_obj.manager_email = manager_email
                    if airtable_id and hasattr(qbr_obj, "airtable_record_id"):
                        qbr_obj.airtable_record_id = airtable_id
                    updated += 1
                else:
                    kwargs = dict(
                        client_id=client_id,
                        quarter=quarter,
                        year=year,
                        date=qbr_date,
                        status=status,
                    )
                    if hasattr(QBR, "manager_email"):
                        kwargs["manager_email"] = manager_email
                    if hasattr(QBR, "airtable_record_id"):
                        kwargs["airtable_record_id"] = airtable_id
                    qbr_obj = QBR(**kwargs)
                    db.add(qbr_obj)
                    created += 1
            except Exception as exc:
                errors.append(f"{client_name}/{quarter}: {exc}")
                skipped += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": f"DB error: {exc}",
                "created": created, "updated": updated, "skipped": skipped, "errors": errors[:10]}

    return {
        "ok": True,
        "total": len(records),
        "month_fields": month_fields,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "synced": created + updated,
        "errors": errors[:10],
    }


async def sync_payment_status_from_airtable(db, token: str = "", base_id: str = "", table_id: str = "") -> dict:
    """Тянет поле 'Оплачено CSM' из payment-таблицы Airtable (tblLEQYWypaYtAcp6)
    и обновляет Client.payment_status.

    Логика значений:
      truthy (✓ / True / "да" / "оплачено") → "active"
      falsy / пустой                        → "overdue"
    """
    use_token = token or AIRTABLE_TOKEN
    use_base  = base_id or AIRTABLE_BASE_ID
    use_table = table_id or PAYMENT_TABLE_ID
    if not use_token or not use_base or not use_table:
        return {"ok": False, "error": "AIRTABLE не настроен"}

    records = []
    offset = None
    async with httpx.AsyncClient(timeout=30) as hx:
        for _ in range(50):  # макс 50 страниц × 100 = 5000 строк
            params = {"pageSize": 100}
            if offset:
                params["offset"] = offset
            resp = await hx.get(
                f"{BASE_URL}/{use_base}/{use_table}",
                headers=_headers(use_token),
                params=params,
            )
            if resp.status_code != 200:
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            data = resp.json()
            records.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset:
                break

    def _pay_value(f: dict):
        for k, v in (f or {}).items():
            if PAYMENT_STATUS_NAME.lower() in (k or "").lower() or "оплачен" in (k or "").lower():
                return v
        return None

    def _truthy(v) -> bool:
        if v is None: return False
        if isinstance(v, bool): return v
        if isinstance(v, (int, float)): return v > 0
        if isinstance(v, list): return any(_truthy(x) for x in v)
        s = str(v).strip().lower()
        return s in {"да", "yes", "true", "1", "✓", "✅", "оплачено", "paid", "active"}

    from models import Client
    updated = 0
    skipped = 0
    for r in records:
        f = r.get("fields", {})
        pay = _pay_value(f)
        # Попробуем резолвить клиента: по name / айди сайта / айтейбл record_id
        name = _val(f, FIELD_NAME) or _val(f, "Клиент") or _val(f, "Name")
        site_id = _val(f, FIELD_SITE_ID) or _val(f, "Site ID") or _val(f, "ID")
        c = None
        if site_id:
            c = db.query(Client).filter(
                (Client.airtable_site_id == site_id) | (Client.merchrules_account_id == site_id)
            ).first()
        if not c and name:
            c = db.query(Client).filter(Client.name == name).first()
        if not c:
            skipped += 1
            continue
        new_status = "active" if _truthy(pay) else "overdue"
        if c.payment_status != new_status:
            c.payment_status = new_status
            updated += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": f"DB error: {exc}"}

    return {"ok": True, "updated": updated, "skipped": skipped, "total": len(records)}
