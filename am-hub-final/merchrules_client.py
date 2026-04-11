"""
Расширенный клиент для работы с Merchrules API.
Поддерживает все новые эндпоинты: аналитика, роадмап, фиды, встречи, чекапы.
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

import httpx

logger = logging.getLogger(__name__)

MERCHRULES_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")

# Кэш токенов per-user: { tg_id: {"token": ..., "expires_at": ...} }
_user_auth_cache: Dict[int, Dict[str, Any]] = {}


async def get_auth_token_for_user(
    client: httpx.AsyncClient, 
    tg_id: int, 
    mr_login: str, 
    mr_password: str
) -> Optional[str]:
    """Авторизация по конкретным credentials (per-user), кэш per tg_id."""
    now = datetime.now()
    cache = _user_auth_cache.get(tg_id, {})
    
    if cache.get("token") and cache.get("expires_at") and now < cache["expires_at"]:
        return cache["token"]

    try:
        resp = await client.post(
            f"{MERCHRULES_URL}/backend-v2/auth/login",
            json={"username": mr_login, "password": mr_password},
            timeout=10,
        )
        if resp.status_code == 200:
            body = resp.json()
            token = body.get("token") or body.get("access_token") or body.get("accessToken")
            if token:
                _user_auth_cache[tg_id] = {
                    "token": token,
                    "expires_at": now + timedelta(hours=1),
                }
                logger.info("MR per-user auth OK (tg_id=%s)", tg_id)
                return token
        logger.warning("MR per-user auth failed (tg_id=%s): %s", tg_id, resp.status_code)
    except Exception as exc:
        logger.warning("MR per-user auth error (tg_id=%s): %s", tg_id, exc)
    
    return None


def invalidate_user_cache(tg_id: int):
    """Сбрасывает кэш токена конкретного пользователя."""
    _user_auth_cache.pop(tg_id, None)


class MerchrulesClient:
    """
    Расширенный клиент для работы со всеми эндпоинтами Merchrules.
    """
    
    def __init__(self, base_url: str = MERCHRULES_URL):
        self.base_url = base_url
    
    async def get_site_all(self, client: httpx.AsyncClient, token: str) -> List[Dict]:
        """Получить список всех сайтов (/api/site/all)."""
        try:
            resp = await client.get(
                f"{self.base_url}/api/site/all",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("sites") or data.get("items") or []
        except Exception as exc:
            logger.warning("get_site_all error: %s", exc)
        return []
    
    async def get_content(self, client: httpx.AsyncClient, token: str, limit: int = 20) -> List[Dict]:
        """Получить контент (/backend-v2/content)."""
        try:
            resp = await client.get(
                f"{self.base_url}/backend-v2/content",
                params={"limit": limit},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("items") or data.get("content") or []
        except Exception as exc:
            logger.warning("get_content error: %s", exc)
        return []
    
    async def get_custom_report(self, client: httpx.AsyncClient, token: str, site_id: int) -> Dict:
        """Получить кастомный отчет (/api/custom-report)."""
        try:
            resp = await client.get(
                f"{self.base_url}/api/custom-report",
                params={"siteId": site_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("get_custom_report(%s) error: %s", site_id, exc)
        return {}
    
    async def get_agg_report(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
        metrics: List[str],
        from_date: str,
        to_date: str,
    ) -> Dict:
        """
        Получить агрегированный отчет (/api/report/agg/{site_id}/global).
        
        metrics: список метрик, например:
        - AUTOCOMPLETE_AND_SEARCH_SESSIONS_AOV
        - ORDERS_TOTAL, REVENUE_TOTAL, CONVERSION, RPS, AOV
        - TOP_SEARCH_QUERIES, SEARCH_EVENTS_TOTAL, etc.
        """
        try:
            metrics_str = ",".join(metrics)
            resp = await client.get(
                f"{self.base_url}/api/report/agg/{site_id}/global",
                params={
                    "name": metrics_str,
                    "from": from_date,
                    "to": to_date,
                    "siteId": site_id,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("get_agg_report(%s) error: %s", site_id, exc)
        return {}
    
    async def get_daily_report(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
        metric: str,
        from_date: str,
        to_date: str,
    ) -> Dict:
        """Получить ежедневный отчет (/api/report/daily/{site_id}/global)."""
        try:
            resp = await client.get(
                f"{self.base_url}/api/report/daily/{site_id}/global",
                params={
                    "name": metric,
                    "from": from_date,
                    "to": to_date,
                    "siteId": site_id,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("get_daily_report(%s) error: %s", site_id, exc)
        return {}
    
    async def get_roadmap_comments(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
        limit: int = 5,
    ) -> List[Dict]:
        """Получить последние комментарии из роадмапа (/backend-v2/roadmap/comments/latest)."""
        try:
            resp = await client.get(
                f"{self.base_url}/backend-v2/roadmap/comments/latest",
                params={"site_id": site_id, "limit": limit},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("comments") or data.get("items") or []
        except Exception as exc:
            logger.warning("get_roadmap_comments(%s) error: %s", site_id, exc)
        return []
    
    async def get_next_meeting(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
    ) -> Optional[Dict]:
        """Получить следующую запланированную встречу (/backend-v2/meetings/next)."""
        try:
            resp = await client.get(
                f"{self.base_url}/backend-v2/meetings/next",
                params={"site_id": site_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, dict) else (data[0] if data and isinstance(data, list) else None)
        except Exception as exc:
            logger.warning("get_next_meeting(%s) error: %s", site_id, exc)
        return None
    
    async def get_checkups(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
    ) -> List[Dict]:
        """Получить список чекапов (/checkups)."""
        try:
            resp = await client.get(
                f"{self.base_url}/checkups",
                params={"site_id": site_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("items") or data.get("checkups") or []
        except Exception as exc:
            logger.warning("get_checkups(%s) error: %s", site_id, exc)
        return []
    
    async def get_feeds(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
    ) -> List[Dict]:
        """Получить список фидов (/feeds)."""
        try:
            resp = await client.get(
                f"{self.base_url}/feeds",
                params={"site_id": site_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("items") or data.get("feeds") or []
        except Exception as exc:
            logger.warning("get_feeds(%s) error: %s", site_id, exc)
        return []
    
    async def get_feed_status(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
    ) -> Dict:
        """Получить статус обработки фида (/feed-processing)."""
        try:
            resp = await client.get(
                f"{self.base_url}/feed-processing",
                params={"site_id": site_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("get_feed_status(%s) error: %s", site_id, exc)
        return {}
    
    async def get_search_settings(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
    ) -> Dict:
        """Получить настройки поиска (/search-settings)."""
        try:
            resp = await client.get(
                f"{self.base_url}/search-settings",
                params={"site_id": site_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("get_search_settings(%s) error: %s", site_id, exc)
        return {}
    
    async def get_full_analytics(
        self,
        client: httpx.AsyncClient,
        token: str,
        site_id: int,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict:
        """
        Получить полную аналитику по сайту.
        Автоматически собирает данные за последнюю неделю, если даты не указаны.
        """
        if not from_date or not to_date:
            # По умолчанию берем последнюю неделю
            today = datetime.now()
            from_date = (today - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
            to_date = today.strftime("%Y-%m-%dT23:59:59")
        
        # Собираем все ключевые метрики параллельно
        metrics_groups = [
            ["ORDERS_TOTAL", "REVENUE_TOTAL", "CONVERSION", "RPS", "AOV"],
            ["AUTOCOMPLETE_AND_SEARCH_SESSIONS_TOTAL", "AUTOCOMPLETE_AND_SEARCH_SESSIONS_ORDERS_TOTAL",
             "AUTOCOMPLETE_AND_SEARCH_SESSIONS_REVENUE", "AUTOCOMPLETE_AND_SEARCH_SESSIONS_CONVERSION",
             "AUTOCOMPLETE_AND_SEARCH_SESSIONS_RPS"],
            ["TOP_SEARCH_QUERIES", "SEARCH_EVENTS_TOTAL", "TOP_ZERO_QUERIES", "ZERO_QUERIES_COUNT", "SESSIONS_TOTAL"],
        ]
        
        results = {}
        async with httpx.AsyncClient(timeout=30) as hx:
            tasks = []
            for metrics in metrics_groups:
                tasks.append(self.get_agg_report(hx, token, site_id, metrics, from_date, to_date))
            
            # Daily revenue trend
            tasks.append(self.get_daily_report(hx, token, site_id, "REVENUE_TOTAL", from_date, to_date))
            
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, resp in enumerate(responses):
                if isinstance(resp, Exception):
                    logger.warning("Analytics fetch error: %s", resp)
                    continue
                if i < len(metrics_groups):
                    results[f"metrics_group_{i}"] = resp
                else:
                    results["daily_revenue"] = resp
        
        return results


# Для обратной совместимости импортируем asyncio здесь
import asyncio
