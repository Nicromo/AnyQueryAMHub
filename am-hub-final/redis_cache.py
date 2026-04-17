"""
Redis кеш — замена in-memory кеша.
При отсутствии Redis автоматически падает назад на dict.
"""
import os, json, logging, time, hashlib
from functools import wraps
from typing import Any, Callable, Optional

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


# Экспорт "сырого" Redis-клиента для проверок (`python -c "import redis_cache; print(redis_cache._REDIS)"`).
_REDIS = _get_redis()


# ---------------------------------------------------------------------------
# Декоратор @cache(ttl=...) для async endpoint-функций FastAPI
# ---------------------------------------------------------------------------
# Ключ строится из имени модуля + функции + хэша "стабильных" kwargs
# (исключаем db/request/auth/user — они не влияют на результат напрямую,
# но auth_token включён в хэш, чтобы не отдавать чужой кэш).
# При недоступности Redis — просто вызывает функцию без кэша.
_HASH_SKIP = {"db", "request", "user", "current_user"}


def cache(ttl: int = 60):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            r = _get_redis()
            if not r:
                return await func(*args, **kwargs)

            hash_kwargs = {k: v for k, v in kwargs.items() if k not in _HASH_SKIP}
            try:
                raw = f"{func.__module__}.{func.__name__}:{json.dumps(hash_kwargs, default=str, sort_keys=True)}"
            except Exception:
                raw = f"{func.__module__}.{func.__name__}:{repr(hash_kwargs)}"
            key = "amhub:" + hashlib.md5(raw.encode()).hexdigest()

            try:
                cached_raw = r.get(key)
                if cached_raw:
                    return json.loads(cached_raw)
            except Exception:
                pass

            result = await func(*args, **kwargs)

            # Не пытаемся сериализовать не-JSON объекты (Response, StreamingResponse и т.д.).
            try:
                payload = json.dumps(result, default=str)
                r.setex(key, ttl, payload)
            except Exception:
                pass

            return result
        return wrapper
    return decorator


def invalidate_pattern(pattern: str) -> int:
    """Удалить все ключи по паттерну (например "clients")."""
    r = _get_redis()
    if not r:
        return 0
    try:
        keys = r.keys(f"amhub:*{pattern}*")
        if keys:
            return r.delete(*keys)
    except Exception:
        pass
    return 0
