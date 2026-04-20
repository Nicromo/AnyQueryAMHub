"""Merchrules analytics API — источник запросов для чекапов."""
import logging
from datetime import date, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://merchrules.any-platform.ru"

KIND_TO_ENDPOINT = {
    "top":    ("top_queries",  0),
    "random": ("top_queries",  1),  # randomizer=1
    "null":   ("null_queries", 0),
    "zero":   ("zero_queries", 0),
}


async def fetch_queries(
    token: str,
    site_id: str,
    kind: str = "top",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 30,
) -> list[dict]:
    """Вернуть список запросов из Merchrules analytics.

    Каждый элемент: {"query": str, "count": int}.
    `kind` ∈ {top, random, null, zero}.
    """
    if kind not in KIND_TO_ENDPOINT:
        raise ValueError(f"unknown kind: {kind}")
    endpoint, randomizer = KIND_TO_ENDPOINT[kind]

    # Default: последние 30 дней.
    if not date_to:
        date_to = date.today().isoformat()
    if not date_from:
        date_from = (date.today() - timedelta(days=30)).isoformat()

    params = {
        "site_id":    site_id,
        "date_from":  date_from,
        "date_to":    date_to,
        "platform":   "all",
        "limit":      limit,
        "min_count":  0,
        "volume":     0,
        "randomizer": randomizer,
    }
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=20) as hx:
        r = await hx.get(
            f"{BASE_URL}/backend-v2/analytics/{endpoint}",
            params=params,
            headers=headers,
        )
    r.raise_for_status()
    data = r.json()

    # Ответ может быть {"queries":[...]}, [...], {"items":[...]}, нужно обрабатывать гибко.
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = (
            data.get("queries")
            or data.get("items")
            or data.get("data")
            or data.get("results")
            or []
        )
    else:
        rows = []

    out: list[dict] = []
    for row in rows:
        if isinstance(row, dict):
            q = (
                row.get("query")
                or row.get("q")
                or row.get("text")
                or row.get("phrase")
                or row.get("term")
            )
            cnt = (
                row.get("count")
                or row.get("cnt")
                or row.get("shows")
                or row.get("hits")
                or row.get("shows_count")
                or row.get("frequency")
                or 0
            )
            if q:
                try:
                    cnt_int = int(cnt or 0)
                except (TypeError, ValueError):
                    cnt_int = 0
                out.append({"query": str(q), "count": cnt_int})
        elif isinstance(row, str):
            out.append({"query": row, "count": 0})
    return out
