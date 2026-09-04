"""
services/sports/limiter.py

Logovo.bet — Provider-Aware Adaptive Rate Limiter (Phase 8).
Manages request quotas, prevents HTTP 429 penalties, and honors Retry-After headers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ProviderRateLimiter:
    """
    Sliding-window token rate limiter with backoff tracking.
    Enforces maximum requests per minute and handles upstream 429 signals politely.
    """

    def __init__(self, requests_per_minute: int = 60) -> None:
        self.rpm = max(1, requests_per_minute)
        self.window_seconds = 60.0
        self._timestamps: list[float] = []
        self._cooldown_until: float = 0.0
        self._total_requests: int = 0
        self._throttled_count: int = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """
        Wait until capacity is available under the rate limit.
        Honors any active 429 cooldowns.
        """
        while True:
            async with self._lock:
                now = time.monotonic()

                # Check if we are in an active backoff cooldown
                if now < self._cooldown_until:
                    sleep_time = self._cooldown_until - now
                else:
                    # Clean expired timestamps older than 60s
                    cutoff = now - self.window_seconds
                    self._timestamps = [t for t in self._timestamps if t > cutoff]

                    if len(self._timestamps) < self.rpm:
                        # Capacity available
                        self._timestamps.append(now)
                        self._total_requests += 1
                        return
                    else:
                        # Window full: sleep until the oldest timestamp expires
                        sleep_time = (self._timestamps[0] + self.window_seconds) - now

            # Sleep outside lock to allow other non-blocking tasks to progress
            self._throttled_count += 1
            await asyncio.sleep(max(0.05, sleep_time))

    def record_response(self, status_code: int, retry_after: Optional[float] = None) -> None:
        """Inform rate limiter of response status for dynamic throttle adjustment."""
        now = time.monotonic()
        if status_code == 429:
            backoff = retry_after if retry_after and retry_after > 0 else 5.0
            self._cooldown_until = now + backoff
            logger.warning("ProviderRateLimiter received HTTP 429! Backing off for %.2fs.", backoff)

    def get_stats(self) -> dict[str, Any]:
        """Telemetry on rate limiting status."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        active_in_window = len([t for t in self._timestamps if t > cutoff])
        is_cooling_down = now < self._cooldown_until
        return {
            "rpm_limit": self.rpm,
            "requests_in_current_window": active_in_window,
            "remaining_in_window": max(0, self.rpm - active_in_window),
            "total_requests": self._total_requests,
            "throttled_count": self._throttled_count,
            "is_cooling_down": is_cooling_down,
            "cooldown_remaining_sec": max(0.0, round(self._cooldown_until - now, 2)) if is_cooling_down else 0.0,
        }
