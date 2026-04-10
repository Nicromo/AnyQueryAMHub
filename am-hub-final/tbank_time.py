"""
TBank Time — интеграция с тикет-системой.
URL: time.tbank.ru/tinkoff/channels/any-team-support

Задачи:
  - Получить открытые тикеты по клиентам AnyQuery
  - Матчить тикеты к клиентам из БД по названию
  - Если тикет открыт > 3 дней → создать задачу AM
"""
import os
import logging
from datetime import date, datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TIME_BASE_URL = os.getenv("TBANK_TIME_URL", "https://time.tbank.ru")
TIME_TOKEN    = os.getenv("TBANK_TIME_TOKEN", "")
TIME_CHANNEL  = os.getenv("TBANK_TIME_CHANNEL", "any-team-support")
TIME_ORG      = os.getenv("TBANK_TIME_ORG", "tinkoff")


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if TIME_TOKEN:
        h["Authorization"] = f"Bearer {TIME_TOKEN}"
    return h


def _match_client(subject: str, clients: list[dict]) -> Optional[dict]:
    """Матчим тикет к клиенту по названию в теме тикета."""
    subject_lower = subject.lower()
    # Точное совпадение имени
    for c in clients:
        if c["name"].lower() in subject_lower:
            return c
    # Совпадение по site_ids
    for c in clients:
        for sid in (c.get("site_ids") or "").split(","):
            sid = sid.strip()
            if sid and sid in subject_lower:
                return c
    return None


async def fetch_open_tickets(clients: list[dict]) -> list[dict]:
    """
    Получить открытые тикеты из TBank Time.
    Возвращает список тикетов с привязкой к клиентам.
    """
    if not TIME_TOKEN:
        logger.info("tbank_time: TBANK_TIME_TOKEN not set, skipping")
        return []

    tickets = []
    today = date.today()

    try:
        async with httpx.AsyncClient(timeout=20) as hx:
            # Endpoint: GET /api/v1/issues или аналогичный
            # Пробуем несколько форматов API
            for endpoint in [
                f"{TIME_BASE_URL}/api/v1/{TIME_ORG}/channels/{TIME_CHANNEL}/tickets",
                f"{TIME_BASE_URL}/api/v1/tickets?channel={TIME_CHANNEL}&status=open",
                f"{TIME_BASE_URL}/tinkoff/channels/{TIME_CHANNEL}/api/tickets",
            ]:
                try:
                    resp = await hx.get(
                        endpoint,
                        headers=_headers(),
                        params={"status": "open", "limit": 200},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_tickets = data if isinstance(data, list) else data.get("tickets") or data.get("issues") or data.get("items") or []
                        for t in raw_tickets:
                            subject = t.get("subject") or t.get("title") or t.get("name") or ""
                            created = t.get("created_at") or t.get("createdAt") or t.get("created") or ""
                            status  = t.get("status") or "open"
                            priority = t.get("priority") or "normal"
                            ticket_id = str(t.get("id") or t.get("key") or "")
                            url = t.get("url") or t.get("link") or f"{TIME_BASE_URL}/tinkoff/channels/{TIME_CHANNEL}/ticket/{ticket_id}"

                            # Считаем дни открытия
                            days_open = 0
                            if created:
                                try:
                                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                                    days_open = (datetime.now(created_dt.tzinfo) - created_dt).days
                                except Exception:
                                    pass

                            matched_client = _match_client(subject, clients)
                            tickets.append({
                                "external_id": ticket_id,
                                "client_id":   matched_client["id"] if matched_client else None,
                                "client_name": matched_client["name"] if matched_client else None,
                                "subject":     subject[:300],
                                "status":      status,
                                "priority":    priority,
                                "days_open":   days_open,
                                "url":         url,
                            })
                        logger.info("TBank Time: fetched %d tickets from %s", len(tickets), endpoint)
                        break  # успешно получили данные
                except Exception as e:
                    logger.debug("TBank Time endpoint %s failed: %s", endpoint, e)
                    continue

    except Exception as exc:
        logger.warning("TBank Time fetch error: %s", exc)

    return tickets


async def sync_tickets_to_db(clients: list[dict]) -> list[dict]:
    """
    Синхронизирует тикеты из TBank Time в БД.
    Возвращает список новых/обновлённых тикетов.
    """
    from database import upsert_support_ticket

    raw_tickets = await fetch_open_tickets(clients)
    result = []

    for t in raw_tickets:
        ticket_id = upsert_support_ticket(
            external_id=t["external_id"] or f"time_{hash(t['subject'])}",
            client_id=t["client_id"],
            subject=t["subject"],
            status=t["status"],
            priority=t["priority"],
            days_open=t["days_open"],
            url=t["url"],
            source="time",
        )
        t["db_id"] = ticket_id
        result.append(t)

    return result
