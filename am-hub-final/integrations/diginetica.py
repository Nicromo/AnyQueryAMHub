"""Diginetica Search API integration.

Базовый URL: https://sort.diginetica.net/search
Каждому клиенту соответствует свой apiKey (виден в Merchrules панели,
поле data-testid="site-selector-api-key").
"""
from __future__ import annotations
import logging
import time
from typing import Any, Dict
import httpx

logger = logging.getLogger(__name__)
BASE_URL = "https://sort.diginetica.net"


async def run_search(api_key: str, query: str, size: int = 10, timeout: float = 10.0) -> Dict[str, Any]:
    """Выполняет поисковый запрос. Возвращает dict с ok/response_time_ms/results_count/has_correction/raw."""
    params = {
        "st": query,
        "apiKey": api_key,
        "strategy": "advanced_xname,zero_queries",
        "fullData": "false",
        "withCorrection": "true",
        "regionId": "global",
        "useCategoryPrediction": "false",
        "size": str(size),
        "offset": "0",
        "showUnavailable": "true",
        "withFacets": "true",
        "treeFacets": "true",
        "unavailableMultiplier": "0.2",
        "withSku": "false",
        "sort": "PRICE_ASC",
    }
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as hx:
            resp = await hx.get(f"{BASE_URL}/search", params=params)
            dt = int((time.time() - t0) * 1000)
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "error": f"HTTP {resp.status_code}",
                    "response_time_ms": dt,
                    "raw": resp.text[:500],
                }
            data = resp.json() if resp.text else {}
            items = data.get("products") or data.get("items") or data.get("results") or []
            return {
                "ok": True,
                "response_time_ms": dt,
                "results_count": len(items) if isinstance(items, list) else 0,
                "has_correction": bool(data.get("correction") or data.get("corrections")),
                "raw": data,
            }
    except Exception as e:
        logger.warning("diginetica search failed: %s", e)
        return {
            "ok": False,
            "error": str(e),
            "response_time_ms": int((time.time() - t0) * 1000),
        }


def auto_score(result: Dict[str, Any]) -> int:
    """Простой автоскор 0..3:
      0 — ошибка/таймаут
      1 — 0 результатов
      2 — мало результатов (<5) или сработал correction
      3 — 5+ результатов без correction
    """
    if not result.get("ok"):
        return 0
    n = result.get("results_count", 0) or 0
    if n == 0:
        return 1
    if n < 5 or result.get("has_correction"):
        return 2
    return 3
