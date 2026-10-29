import os
import time
from typing import Any
import redis

from app.config import settings


def get_redis_client() -> redis.Redis | None:
    if not settings.REDIS_URL:
        return None
    try:
        return redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    except Exception:
        return None


def check_rate_limit(user_id: str, client: Any = None) -> tuple[bool, int]:
    """Check fixed-window rate limit per user.

    Returns (allowed, retry_after_seconds).
    """
    if client is None:
        client = get_redis_client()
    if client is None:
        return True, 0

    window_s = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", str(settings.RATE_LIMIT_WINDOW_SECONDS)))
    max_reqs = int(os.environ.get("RATE_LIMIT_REQUESTS", str(settings.RATE_LIMIT_REQUESTS)))
    now = int(time.time())
    window_bucket = now // window_s
    key = f"ratelimit:{user_id}:{window_bucket}"

    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_s)
        if count > max_reqs:
            retry_after = max(1, window_s - (now % window_s))
            return False, retry_after
        return True, 0
    except Exception:
        return True, 0
