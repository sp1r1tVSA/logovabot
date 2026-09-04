"""
services/sports/circuit.py

Logovo.bet — Provider Circuit Breaker (Phase 8).
Prevents cascading failures and continuous failed outbound network requests when
an external sports provider is unavailable or experiencing severe outages.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


class ProviderCircuitBreaker:
    """
    Standard three-state circuit breaker:
    - CLOSED: Normal operation, all calls permitted.
    - OPEN: Outage detected after max_failures, calls immediately short-circuited.
    - HALF_OPEN: Cooldown expired, allows a probe request to test upstream recovery.
    """

    def __init__(self, max_failures: int = 5, cooldown_seconds: float = 60.0) -> None:
        self.max_failures = max(1, max_failures)
        self.cooldown_seconds = max(0.01, cooldown_seconds)

        self.state = CLOSED
        self.consecutive_failures = 0
        self.last_failure_time: float = 0.0
        self.last_success_time: Optional[float] = None
        self.total_trips = 0

    def can_execute(self) -> bool:
        """Returns True if request can proceed; False if circuit is OPEN."""
        now = time.monotonic()
        if self.state == CLOSED:
            return True

        if self.state == OPEN:
            if now - self.last_failure_time > self.cooldown_seconds:
                logger.info("Circuit breaker entering HALF_OPEN probe mode after %.1fs cooldown.", self.cooldown_seconds)
                self.state = HALF_OPEN
                return True
            return False

        if self.state == HALF_OPEN:
            # Probe is already in-flight or allowed
            return True

        return True

    def record_success(self) -> None:
        """Record successful request: reset failure counter and close circuit."""
        self.last_success_time = time.monotonic()
        if self.state != CLOSED:
            logger.info("Circuit breaker recovered: transitioning from %s to CLOSED.", self.state)
        self.state = CLOSED
        self.consecutive_failures = 0

    def record_failure(self, err: Optional[Exception] = None) -> None:
        """Record upstream failure: increment failure counter and trip if threshold reached."""
        self.consecutive_failures += 1
        self.last_failure_time = time.monotonic()
        err_msg = str(err) if err else "unknown error"
        logger.warning(
            "Circuit breaker failure (%d/%d): %s",
            self.consecutive_failures, self.max_failures, err_msg
        )

        if self.consecutive_failures >= self.max_failures:
            if self.state != OPEN:
                self.total_trips += 1
                logger.error("Circuit breaker TRIPPED to OPEN! Short-circuiting calls for %.1fs.", self.cooldown_seconds)
            self.state = OPEN

    def get_state(self) -> dict[str, Any]:
        """Telemetry on circuit breaker health."""
        now = time.monotonic()
        cooldown_remaining = max(0.0, round(self.cooldown_seconds - (now - self.last_failure_time), 2)) if self.state == OPEN else 0.0
        return {
            "state": self.state,
            "is_open": self.state == OPEN,
            "consecutive_failures": self.consecutive_failures,
            "max_failures": self.max_failures,
            "cooldown_remaining_sec": cooldown_remaining,
            "total_trips": self.total_trips,
            "last_failure_time": self.last_failure_time if self.last_failure_time > 0 else None,
            "last_success_time": self.last_success_time,
        }
