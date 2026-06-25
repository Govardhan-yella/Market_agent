from __future__ import annotations

import json
from functools import wraps
from typing import Any, Callable

from market_agent.db import cache_get, cache_set


def cached_json(key: str, ttl_seconds: int) -> Callable:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                hit = cache_get(key)
                if hit is not None:
                    return json.loads(hit)
            except Exception:
                pass
            result = func(*args, **kwargs)
            try:
                cache_set(key, json.dumps(result, default=str), ttl_seconds)
            except Exception:
                pass
            return result
        return wrapper
    return decorator


def cached_object(key: str, ttl_seconds: int) -> Callable:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                hit = cache_get(key)
                if hit is not None:
                    import pickle
                    return pickle.loads(hit.encode("latin-1"))
            except Exception:
                pass
            result = func(*args, **kwargs)
            try:
                import pickle
                cache_set(key, pickle.dumps(result).decode("latin-1"), ttl_seconds)
            except Exception:
                pass
            return result
        return wrapper
    return decorator
