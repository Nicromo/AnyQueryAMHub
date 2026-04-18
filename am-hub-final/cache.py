"""Simple in-process TTL cache for expensive DB operations.

Usage:
    from cache import ttl_cache, invalidate

    @ttl_cache(ttl=60)  # cache for 60 seconds
    def my_func(arg):
        ...

    invalidate("my_func", arg)  # clear specific cache entry
    invalidate_prefix("my_func")  # clear all entries for function
"""
import time
import functools
import threading
from typing import Any, Dict, Optional, Tuple

_cache: Dict[str, Tuple[float, Any]] = {}
_lock = threading.Lock()


def ttl_cache(ttl: int = 60, key_fn=None):
    """Decorator: cache the return value for `ttl` seconds.

    key_fn(args, kwargs) → str  (default: repr of first arg)
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Build cache key
            if key_fn:
                cache_key = f"{fn.__name__}:{key_fn(args, kwargs)}"
            else:
                # Use first arg as key (typically user.id or user.email)
                k = args[0] if args else "default"
                if hasattr(k, 'id'):
                    k = k.id
                elif hasattr(k, 'email'):
                    k = k.email
                cache_key = f"{fn.__name__}:{k}"

            now = time.monotonic()
            with _lock:
                if cache_key in _cache:
                    expires, val = _cache[cache_key]
                    if now < expires:
                        return val

            result = fn(*args, **kwargs)

            with _lock:
                _cache[cache_key] = (now + ttl, result)
            return result

        wrapper._cache_fn_name = fn.__name__
        return wrapper
    return decorator


def invalidate(fn_name: str, key=None):
    """Remove cache entry. fn_name is the function name (string)."""
    if key is not None:
        k_str = key.id if hasattr(key, 'id') else str(key)
        cache_key = f"{fn_name}:{k_str}"
        with _lock:
            _cache.pop(cache_key, None)
    else:
        invalidate_prefix(fn_name)


def invalidate_prefix(prefix: str):
    """Remove all cache entries whose key starts with prefix."""
    with _lock:
        keys_to_del = [k for k in _cache if k.startswith(prefix)]
        for k in keys_to_del:
            del _cache[k]


def cache_stats() -> dict:
    """Return cache statistics for debugging."""
    now = time.monotonic()
    with _lock:
        total = len(_cache)
        expired = sum(1 for _, (exp, _) in _cache.items() if now >= exp)
    return {"total": total, "alive": total - expired, "expired": expired}
