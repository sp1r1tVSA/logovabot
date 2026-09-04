"""
services/sports/cache.py

Logovo.bet — Provider-Aware Namespaced In-Memory Cache (Phase 8).
Guarantees strict provider isolation, automatic TTL invalidation, and zero perpetual stale caches.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ProviderCache:
    """
    Thread-safe namespaced cache storing provider data with explicit TTLs.
    Key format: sports:{provider}:{key}.
    """

    def __init__(self, default_ttl_seconds: float = 30.0) -> None:
        self.default_ttl = max(0.01, default_ttl_seconds)

        self._entries: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_timestamp)
        self._hits = 0
        self._misses = 0

    def _make_key(self, provider: str, key: str) -> str:
        clean_p = (provider or "unknown").strip().lower()
        clean_k = str(key).strip()
        return f"sports:{clean_p}:{clean_k}"

    def get(self, provider: str, key: str) -> Optional[Any]:
        """Fetch cached item. Returns None if expired or missing."""
        full_key = self._make_key(provider, key)
        entry = self._entries.get(full_key)
        if not entry:
            self._misses += 1
            return None

        val, expires_at = entry
        if time.monotonic() > expires_at:
            # Expired
            self._entries.pop(full_key, None)
            self._misses += 1
            return None

        self._hits += 1
        return val

    def set(self, provider: str, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        """Store value under provider namespace with explicit TTL."""
        full_key = self._make_key(provider, key)
        ttl = ttl_seconds if (ttl_seconds is not None and ttl_seconds > 0) else self.default_ttl
        expires_at = time.monotonic() + ttl
        self._entries[full_key] = (value, expires_at)

    def invalidate(self, provider: str, key: Optional[str] = None) -> int:
        """Invalidate single key or all keys under a given provider namespace."""
        if key is not None:
            full_key = self._make_key(provider, key)
            if self._entries.pop(full_key, None) is not None:
                return 1
            return 0

        prefix = f"sports:{(provider or 'unknown').strip().lower()}:"
        to_delete = [k for k in self._entries.keys() if k.startswith(prefix)]
        for k in to_delete:
            self._entries.pop(k, None)
        return len(to_delete)

    def clear(self) -> None:
        """Purge all entries across all providers."""
        self._entries.clear()

    def get_stats(self) -> dict[str, Any]:
        """Telemetry on cache size, hits, and misses."""
        now = time.monotonic()
        active_count = len([k for k, (_, exp) in self._entries.items() if exp > now])
        total_requests = self._hits + self._misses
        hit_ratio = round(self._hits / total_requests, 3) if total_requests > 0 else 0.0
        return {
            "active_entries": active_count,
            "total_stored": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
            "hit_ratio": hit_ratio,
        }
