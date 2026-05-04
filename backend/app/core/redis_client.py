from __future__ import annotations

import os
from functools import lru_cache

import redis


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    url = (os.getenv("REDIS_URL") or "").strip() or "redis://localhost:6379/0"
    # decode_responses=True чтобы получать str, а не bytes
    return redis.Redis.from_url(url, decode_responses=True)

