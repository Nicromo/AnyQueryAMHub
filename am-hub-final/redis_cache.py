"""
Redis кеш — замена in-memory кеша.
При отсутствии Redis автоматически падает назад на dict.
"""
import os, json, logging, time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_redis = None
_fallback: dict = {}   # in-memory fallback

def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_PRIVATE_URL", "")
    if not url:
        return None
    try:
        import redis
        _redis = redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        _redis.ping()
        logger.info("✅ Redis connected")
        return _redis
    except Exception as e:
        logger.warning(f"Redis unavailable, using in-memory fallback: {e}")
        _redis = False  # не пробуем снова
        return None


def cache_get(key: str) -> Optional[Any]:
    r = _get_redis()
    if r:
        try:
            val = r.get(key)
            return json.loads(val) if val else None
        except Exception:
            pass
    # Fallback
    entry = _fallback.get(key)
    if entry and time.time() < entry["exp"]:
        return entry["val"]
    return None


def cache_set(key: str, val: Any, ttl: int = 60):
    r = _get_redis()
    if r:
        try:
            r.setex(key, ttl, json.dumps(val, default=str))
            return
        except Exception:
            pass
    # Fallback
    _fallback[key] = {"val": val, "exp": time.time() + ttl}


def cache_del(prefix: str):
    r = _get_redis()
    if r:
        try:
            keys = r.keys(f"{prefix}*")
            if keys:
                r.delete(*keys)
            return
        except Exception:
            pass
    # Fallback
    for k in list(_fallback.keys()):
        if k.startswith(prefix):
            del _fallback[k]


def cache_key(user_id: int, suffix: str) -> str:
    return f"amhub:u{user_id}:{suffix}"
