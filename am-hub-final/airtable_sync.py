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

AIRTABLE_TOKEN        = os.getenv("AIRTABLE_TOKEN", "")
AIRTABLE_BASE_ID      = os.getenv("AIRTABLE_BASE_ID", "")
AIRTABLE_TABLE_ID     = os.getenv("AIRTABLE_TABLE_ID", "")
AIRTABLE_QBR_TABLE_ID = os.getenv("AIRTABLE_QBR_TABLE_ID", "")
AIRTABLE_VIEW_ID      = os.getenv("AIRTABLE_VIEW_ID", "")

BASE_URL = "https://api.airtable.com/v0"

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


async def _fetch_all_records(client, table_id: str, view_id: str = "", token: str = "") -> list:
    """Скачать ВСЕ записи с пагинацией (Airtable даёт по 100 за раз)."""
    records = []
    offset = None
    page = 0
    while True:
        page += 1
        params = {"pageSize": 100}
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


async def sync_clients_from_airtable(
    db,
    token: str = "",
    base_id: Optional[str] = None,
    view_id: str = "",
    default_manager_email: str = "",
    table_id: str = "",
) -> dict:
    """Синхронизировать всех клиентов из Airtable в таблицу clients.

    Маппинг (Airtable → Client):
      Account           → name
      CSM (email)       → manager_email
      Customer Stage    → segment (ENT/SME/SMB/SS)
      Site ID           → airtable_site_id + merchrules_account_id (fallback)
      URL / Domain      → domain
      GMV               → gmv
      MRR поля          → mrr (если есть)

    Идемпотентность: upsert по airtable_record_id → airtable_site_id → name.
    """
    from models import Client

    use_token = token or AIRTABLE_TOKEN
    use_base = base_id or AIRTABLE_BASE_ID
    use_table = table_id or AIRTABLE_TABLE_ID
    if not use_token:
        return {"ok": False, "error": "AIRTABLE_TOKEN не задан", "created": 0, "updated": 0, "skipped": 0, "errors": []}
    if not use_base or not use_table:
        return {"ok": False, "error": "AIRTABLE_BASE_ID/AIRTABLE_TABLE_ID не заданы", "created": 0, "updated": 0, "skipped": 0, "errors": []}

    # Подменяем module-level переменные для _fetch_all_records (он их использует)
    global AIRTABLE_BASE_ID
    _saved_base = AIRTABLE_BASE_ID
    AIRTABLE_BASE_ID = use_base
    try:
        async with httpx.AsyncClient(timeout=60) as hx:
            records = await _fetch_all_records(hx, use_table, view_id or AIRTABLE_VIEW_ID, use_token)
    finally:
        AIRTABLE_BASE_ID = _saved_base

    if not records:
        return {"ok": False, "error": "Нет записей или ошибка доступа к Airtable",
                "created": 0, "updated": 0, "skipped": 0, "errors": []}

    # Авто-определяем поля по первым 10 записям
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

    logger.info("Airtable field map: %s", {k: v for k, v in field_map.items() if k != "all_fields"})
    created = updated = skipped = 0
    errors = []

    for record in records:
        fields = record.get("fields", {})
        airtable_id = record.get("id", "")
        name = _val(fields, f_name)
        if not name:
            skipped += 1
            continue

        segment = _normalize_segment(_val(fields, f_segment)) if f_segment else None
        manager_raw = _val(fields, f_manager)
        # CSM (email) → manager_email. Если нет "@" — fallback на default
        manager_email = manager_raw.lower() if (manager_raw and "@" in manager_raw) else (default_manager_email or None)
        site_id = _val(fields, f_site) or None
        domain  = _val(fields, f_domain) or None
        # домен: обрезаем протокол
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
                if segment: c.segment = segment
                # ВСЕГДА обновляем manager_email из CSM (источник истины — Airtable)
                if manager_email: c.manager_email = manager_email
                if site_id:
                    c.airtable_site_id = site_id
                    if not c.merchrules_account_id:
                        c.merchrules_account_id = site_id
                if domain: c.domain = domain
                if last_checkup: c.last_checkup = last_checkup
                if health is not None: c.health_score = health
                if gmv is not None: c.gmv = gmv
                if airtable_id: c.airtable_record_id = airtable_id
                c.last_sync_at = datetime.utcnow()
                updated += 1
            else:
                c = Client(
                    name=name,
                    segment=segment,
                    manager_email=manager_email,
                    airtable_site_id=site_id,
                    merchrules_account_id=site_id,
                    domain=domain,
                    last_checkup=last_checkup,
                    health_score=health or 0.0,
                    gmv=gmv or 0.0,
                    airtable_record_id=airtable_id,
                    last_sync_at=datetime.utcnow(),
                )
                db.add(c)
                created += 1
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            skipped += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": f"DB error: {exc}",
                "created": created, "updated": updated, "skipped": skipped, "errors": errors[:10]}

    # Для совместимости со scheduler (ожидает result.get("synced"))
    return {"ok": True, "total": len(records),
            "created": created, "updated": updated, "skipped": skipped,
            "synced": created + updated,
            "errors": errors[:10], "field_map": field_map}


async def sync_meeting_to_airtable(client_name: str, meeting_date: datetime, comment: str = "", token: str = "") -> bool:
    """Обновить дату встречи и дописать комментарий в Airtable."""
    use_token = token or AIRTABLE_TOKEN
    if not use_token:
        return False
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
            f_date    = _find_field(fields, CHECKUP_CANDIDATES)
            f_comment = _find_field(fields, ["комментарий", "notes", "comments", "история"])
            update: dict = {}
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
    fields — словарь {field_name: value}.
    Используется при изменении данных клиента в AM Hub.
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
                logger.info(f"✅ Airtable client {record_id} updated")
                return True
            logger.warning(f"Airtable push error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"push_client_fields_to_airtable error: {e}")
    return False


async def push_qbr_to_airtable(
    client_name: str,
    quarter: str,
    summary: str,
    achievements: list,
    token: str = "",
) -> bool:
    """
    Создать/обновить QBR запись в Airtable QBR-таблице.
    """
    qbr_table = os.getenv("AIRTABLE_QBR_TABLE_ID", "")
    use_token = token or AIRTABLE_TOKEN
    if not use_token or not AIRTABLE_BASE_ID or not qbr_table:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            fields = {
                "Клиент": client_name,
                "Квартал": quarter,
                "Итоги": summary,
                "Достижения": "\n".join(achievements) if achievements else "",
            }
            resp = await hx.post(
                f"{BASE_URL}/{AIRTABLE_BASE_ID}/{qbr_table}",
                headers=_headers(use_token),
                json={"fields": fields},
                timeout=15,
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        logger.error(f"push_qbr_to_airtable error: {e}")
    return False
