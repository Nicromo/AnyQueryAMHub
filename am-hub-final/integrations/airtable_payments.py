"""
airtable_payments.py — оплаты клиентов из Airtable.

Тянет записи из таблицы «просроченные оплаты» в том же base'е что и клиенты:
  base  = appEAS1rPKpevoIel   (env AIRTABLE_BASE_ID)
  table = tblLEQYWypaYtAcp6   (env AIRTABLE_PAYMENTS_TABLE_ID)
  view  = viw977k6GUNrkeRRy   (env AIRTABLE_PAYMENTS_VIEW_ID)

Поскольку точная схема полей таблицы отличается у разных аккаунтов,
pipeline делает fuzzy-маппинг: ищем поля с именами типа «клиент», «сумма»,
«дата», «статус», «менеджер» и нормализуем к единому формату.

Фильтрация по менеджеру происходит на стороне клиента (AM Hub) — берём
ответ из Airtable и оставляем только те записи, где manager_email или
manager_name совпадает с текущим пользователем.
"""
from __future__ import annotations
import logging
import os
import re
from datetime import date, datetime
from typing import List, Optional, Any, Dict

import httpx

logger = logging.getLogger(__name__)

AIRTABLE_TOKEN              = os.getenv("AIRTABLE_TOKEN", "") or os.getenv("AIRTABLE_PAT", "")
AIRTABLE_BASE_ID            = os.getenv("AIRTABLE_BASE_ID", "appEAS1rPKpevoIel")
AIRTABLE_PAYMENTS_TABLE_ID  = os.getenv("AIRTABLE_PAYMENTS_TABLE_ID", "tblLEQYWypaYtAcp6")
AIRTABLE_PAYMENTS_VIEW_ID   = os.getenv("AIRTABLE_PAYMENTS_VIEW_ID", "viw977k6GUNrkeRRy")

BASE_URL = "https://api.airtable.com/v0"


def _headers():
    if not AIRTABLE_TOKEN:
        return None
    return {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}


_CLIENT_KEYS  = ("клиент", "client", "партнёр", "партнер", "партнёра", "partner", "сайт", "домен", "site")
_AMOUNT_KEYS  = ("сумма", "сумм", "amount", "total", "долг", "к оплате", "debt")
_DATE_KEYS    = ("дата", "дедлайн", "due", "оплати", "срок", "date", "deadline")
_STATUS_KEYS  = ("статус", "status", "состоян")
_MANAGER_KEYS = ("менеджер", "manager", "am", "акаунт", "account", "ответствен")
_INVOICE_KEYS = ("счёт", "счет", "invoice", "№", "номер")
_COMMENT_KEYS = ("коммент", "comment", "описан", "description", "заметк", "note")


def _pick(fields: Dict[str, Any], keys: tuple) -> Optional[str]:
    """Находит значение первого поля, чьё имя содержит одно из keys (case-insensitive)."""
    for k, v in fields.items():
        lk = k.lower()
        if any(kw in lk for kw in keys):
            return k
    return None


def _val(fields: Dict[str, Any], key: Optional[str]) -> Any:
    if not key:
        return None
    v = fields.get(key)
    # Airtable linked records — список dict/str
    if isinstance(v, list):
        if not v:
            return None
        first = v[0]
        if isinstance(first, dict):
            return first.get("name") or first.get("email") or first.get("id") or str(first)
        return str(first)
    return v


def _parse_amount(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    s = re.sub(r"[^\d,\.\-]", "", s).replace(",", ".")
    try:
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    s = str(v)
    # Airtable обычно ISO "2026-04-21" или datetime "2026-04-21T10:00:00.000Z"
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                 "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 5], fmt).date()
        except Exception:
            continue
    return None


async def fetch_payments_raw() -> List[Dict[str, Any]]:
    """Загружает все записи из таблицы оплат. Возвращает raw-dict Airtable API."""
    if not AIRTABLE_TOKEN:
        return []
    url = f"{BASE_URL}/{AIRTABLE_BASE_ID}/{AIRTABLE_PAYMENTS_TABLE_ID}"
    params = {"pageSize": 100}
    if AIRTABLE_PAYMENTS_VIEW_ID:
        params["view"] = AIRTABLE_PAYMENTS_VIEW_ID
    out: List[Dict[str, Any]] = []
    offset: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=20) as hx:
            while True:
                p = dict(params)
                if offset:
                    p["offset"] = offset
                r = await hx.get(url, headers=_headers(), params=p)
                if r.status_code != 200:
                    logger.warning(f"airtable payments HTTP {r.status_code}: {r.text[:200]}")
                    break
                data = r.json()
                out.extend(data.get("records") or [])
                offset = data.get("offset")
                if not offset:
                    break
    except Exception as e:
        logger.warning(f"airtable payments fetch failed: {e}")
    return out


def _today():
    return date.today()


def _bucket(days_from_today: Optional[int], status: str) -> str:
    s = (status or "").lower()
    if any(w in s for w in ("просроч", "overdue", "unpaid", "не оплач")):
        return "overdue"
    if days_from_today is None:
        return "no_date"
    if days_from_today < 0:
        return "overdue"
    if days_from_today == 0:
        return "today"
    if days_from_today <= 7:
        return "week"
    return "later"


def normalize_payment_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Raw Airtable record → унифицированный формат, совместимый с UI /design/renewal."""
    fields = rec.get("fields") or {}
    client_key   = _pick(fields, _CLIENT_KEYS)
    amount_key   = _pick(fields, _AMOUNT_KEYS)
    date_key     = _pick(fields, _DATE_KEYS)
    status_key   = _pick(fields, _STATUS_KEYS)
    manager_key  = _pick(fields, _MANAGER_KEYS)
    invoice_key  = _pick(fields, _INVOICE_KEYS)
    comment_key  = _pick(fields, _COMMENT_KEYS)

    client_val  = _val(fields, client_key)
    manager_val = _val(fields, manager_key)
    amount      = _parse_amount(_val(fields, amount_key))
    due         = _parse_date(_val(fields, date_key))
    status      = str(_val(fields, status_key) or "").strip() or "overdue"

    today = _today()
    days = (due - today).days if due else None
    bucket = _bucket(days, status)

    return {
        "record_id": rec.get("id"),
        "client_name": str(client_val or "").strip() or "—",
        "manager_raw": str(manager_val or "").strip(),
        "payment_amount": amount,
        "payment_due_date": due.isoformat() if due else None,
        "payment_status": status,
        "days_from_today": days,
        "bucket": bucket,
        "invoice_number": str(_val(fields, invoice_key) or "").strip() or None,
        "comment": str(_val(fields, comment_key) or "").strip() or None,
    }


def manager_matches(item_manager_raw: str, user_email: str, user_name: str = "") -> bool:
    """Строка-manager из Airtable сравнивается с email/name текущего пользователя.
    Случаи:
      - ровно email ('x@y')
      - email-префикс ('name' vs 'name@y') совпал хотя бы локальной частью
      - ФИО (name содержит в строке)
    """
    s = (item_manager_raw or "").lower().strip()
    if not s:
        return False
    e = (user_email or "").lower()
    if e and (s == e or s.startswith(e) or e in s):
        return True
    local = e.split("@")[0] if "@" in e else e
    if local and (s == local or local in s):
        return True
    n = (user_name or "").lower().strip()
    if n and (s == n or n in s or all(tok in s for tok in n.split() if tok)):
        return True
    return False


async def get_payments_for_manager(user_email: str, user_name: str = "") -> Dict[str, Any]:
    """Главная функция: тянет всё из Airtable и фильтрует по менеджеру.
    Возвращает структуру, совместимую с /api/me/payments-pending."""
    if not AIRTABLE_TOKEN:
        return {"available": False, "reason": "AIRTABLE_TOKEN не задан"}

    raw = await fetch_payments_raw()
    items = [normalize_payment_record(r) for r in raw]
    # Фильтр по менеджеру — для admin/grouphead можно отключить извне.
    if user_email:
        items = [i for i in items if manager_matches(i.get("manager_raw", ""), user_email, user_name)]

    columns = {
        "overdue":  {"label": "Просрочено",     "tone": "critical", "items": []},
        "today":    {"label": "Сегодня",        "tone": "warn",     "items": []},
        "week":     {"label": "На неделе",      "tone": "warn",     "items": []},
        "later":    {"label": "Позже",          "tone": "info",     "items": []},
        "no_date":  {"label": "Без даты",       "tone": "neutral",  "items": []},
    }
    total_amount = 0.0
    for it in items:
        columns[it["bucket"]]["items"].append({
            "id": it["record_id"],
            "name": it["client_name"],
            "segment": None,
            "manager_email": user_email,
            "payment_status": it["payment_status"],
            "payment_due_date": it["payment_due_date"],
            "payment_amount": it["payment_amount"],
            "days_from_today": it["days_from_today"],
            "mrr": None,
            "invoice_number": it["invoice_number"],
            "comment": it["comment"],
        })
        total_amount += float(it["payment_amount"] or 0)

    totals = {k: len(v["items"]) for k, v in columns.items()}
    return {
        "available": True,
        "source": "airtable",
        "columns": columns,
        "totals": totals,
        "total_clients": sum(totals.values()),
        "total_unpaid_amount": total_amount,
    }
