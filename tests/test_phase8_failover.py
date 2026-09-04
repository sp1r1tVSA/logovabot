"""
tests/test_phase8_failover.py

Phase 8 — Resilience, Circuit Breaker, Rate Limiting, Cache Isolation & Failover Test Suite.
Verifies:
1. Circuit breaker three-state lifecycle (CLOSED -> OPEN -> HALF_OPEN -> CLOSED).
2. Rate Limiter sliding window and polite HTTP 429 Retry-After cooldown.
3. Provider Cache namespacing and zero cross-provider key collision.
4. Graceful failover to NullSportsDataProvider on outages without synthetic data.
"""

import asyncio
import time
import unittest
from unittest.mock import patch

from services.sports.circuit import (
    ProviderCircuitBreaker,
    CLOSED,
    OPEN,
    HALF_OPEN
)
from services.sports.limiter import ProviderRateLimiter
from services.sports.cache import ProviderCache
from services.sports.adapters.null_provider import NullSportsDataProvider
from services.sports import get_sports_provider, set_sports_provider


class TestPhase8Failover(unittest.IsolatedAsyncioTestCase):
    """Resilience and failover verification for sports data pipelines."""

    def test_circuit_breaker_full_lifecycle(self) -> None:
        """Circuit breaker trips after max failures and recovers after cooldown probe."""
        cb = ProviderCircuitBreaker(max_failures=3, cooldown_seconds=0.1)
        self.assertEqual(cb.state, CLOSED)
        self.assertTrue(cb.can_execute())

        # 2 failures -> still CLOSED
        cb.record_failure(Exception("Error 1"))
        cb.record_failure(Exception("Error 2"))
        self.assertEqual(cb.state, CLOSED)
        self.assertTrue(cb.can_execute())

        # 3rd failure -> TRIPS to OPEN
        cb.record_failure(Exception("Error 3"))
        self.assertEqual(cb.state, OPEN)
        self.assertFalse(cb.can_execute())

        # Wait for cooldown (0.1s)
        time.sleep(0.12)

        # Entering HALF_OPEN probe mode
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, HALF_OPEN)

        # Successful probe recovers circuit back to CLOSED
        cb.record_success()
        self.assertEqual(cb.state, CLOSED)
        self.assertEqual(cb.consecutive_failures, 0)

    async def test_rate_limiter_sliding_window_and_backoff(self) -> None:
        """Rate limiter enforces RPM limit and honors 429 backoff cooldown."""
        # Limiter with 3 requests per minute (window=0.2s for fast test)
        limiter = ProviderRateLimiter(requests_per_minute=3)
        limiter.window_seconds = 0.2

        t0 = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()
        t1 = time.monotonic()
        self.assertLess(t1 - t0, 0.1)

        # Inform limiter of HTTP 429 with 0.15s retry-after
        limiter.record_response(status_code=429, retry_after=0.15)
        stats = limiter.get_stats()
        self.assertTrue(stats["is_cooling_down"])
        self.assertGreater(stats["cooldown_remaining_sec"], 0.0)

        # Next acquire must wait for cooldown
        t_before = time.monotonic()
        await limiter.acquire()
        t_after = time.monotonic()
        self.assertGreaterEqual(t_after - t_before, 0.10)

    def test_cache_provider_isolation(self) -> None:
        """Cache keys are strictly isolated by provider namespace."""
        cache = ProviderCache(default_ttl_seconds=10.0)

        cache.set(provider="api_sports", key="fixture:100", value={"source": "api_sports", "score": "2:1"})
        cache.set(provider="mock", key="fixture:100", value={"source": "mock", "score": "0:0"})

        # Retrieve each under its own provider namespace
        item_api = cache.get(provider="api_sports", key="fixture:100")
        item_mock = cache.get(provider="mock", key="fixture:100")

        self.assertIsNotNone(item_api)
        self.assertIsNotNone(item_mock)
        self.assertEqual(item_api["source"], "api_sports")
        self.assertEqual(item_mock["source"], "mock")

        # Invalidate only mock provider
        deleted = cache.invalidate(provider="mock")
        self.assertEqual(deleted, 1)

        self.assertIsNone(cache.get(provider="mock", key="fixture:100"))
        self.assertIsNotNone(cache.get(provider="api_sports", key="fixture:100"))

    def test_cache_ttl_expiration(self) -> None:
        """Cached items expire cleanly after TTL."""
        cache = ProviderCache(default_ttl_seconds=0.05)
        cache.set(provider="api_sports", key="live_match", value={"status": "live"})

        # Immediate check: present
        self.assertIsNotNone(cache.get(provider="api_sports", key="live_match"))

        # Wait past TTL
        time.sleep(0.07)
        self.assertIsNone(cache.get(provider="api_sports", key="live_match"))

    async def test_null_provider_failover_safety(self) -> None:
        """When provider fails or is unconfigured, Null provider yields zero fake data."""
        null_prov = NullSportsDataProvider(reason="Circuit breaker tripped upstream")
        set_sports_provider(null_prov)

        prov = get_sports_provider()
        self.assertEqual(prov.provider_name, "null")
        self.assertFalse(prov.is_connected)

        live_fixtures = await prov.get_live_fixtures()
        self.assertEqual(live_fixtures, [])

        status = prov.get_provider_status()
        self.assertEqual(status["status"], "UNAVAILABLE")
