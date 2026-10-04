from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from app.core.config import settings


class QueryCache:
    def __init__(self):
        self._memory: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._redis = None
        if settings.cache_enabled:
            try:
                import redis
                self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.2)
                self._redis.ping()
            except Exception:
                self._redis = None

    @staticmethod
    def key(namespace: str, value: str) -> str:
        digest = hashlib.sha256(value.encode()).hexdigest()
        return f"nocmind:{namespace}:{digest}"

    def get(self, namespace: str, value: str):
        if not settings.cache_enabled:
            return None
        key = self.key(namespace, value)
        try:
            if self._redis:
                raw = self._redis.get(key)
                return json.loads(raw) if raw else None
        except Exception:
            pass
        with self._lock:
            return self._memory.get(key)

    def set(self, namespace: str, value: str, data: Any, ttl: int = 300):
        if not settings.cache_enabled:
            return
        key = self.key(namespace, value)
        try:
            if self._redis:
                self._redis.setex(key, ttl, json.dumps(data, default=str))
                return
        except Exception:
            pass
        with self._lock:
            self._memory[key] = data
