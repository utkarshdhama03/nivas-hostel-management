"""A small in-process rate limiter (docs/04 §5.10, Q72).

Used for limits that don't need to survive a restart: registration per IP, password-reset
requests, slug checks, and a global per-IP login-failure throttle. Limits that protect an
ACCOUNT (wrong passwords, MFA codes, email OTP sends) are counted in the database instead,
so restarting the API can't reset them.

Sliding window: remember the timestamps of recent hits per key; a key is over the limit
when `limit` hits fall inside the last `window`. Fine for one API process (Q52, 02 §4);
several processes would need a shared store.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Limit:
    name: str
    limit: int
    window_seconds: float
    _hits: dict[str, deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _trim(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window_seconds:
            hits.popleft()
        return hits

    def retry_after(self, key: str, now: float | None = None) -> int | None:
        """Seconds until another hit is allowed, or None if allowed now. Doesn't record."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._trim(key, now)
            if len(hits) < self.limit:
                return None
            return max(1, math.ceil(hits[0] + self.window_seconds - now))

    def hit(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._trim(key, now).append(now)

    def take(self, key: str) -> int | None:
        """Record a hit if allowed; return None, or the Retry-After seconds if over the limit."""
        wait = self.retry_after(key)
        if wait is None:
            self.hit(key)
        return wait

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


REGISTRATION_PER_IP = Limit("registration_per_ip", 3, 3600)  # limit replaced from settings at first use
RESET_PER_IDENTIFIER = Limit("password_reset_per_identifier", 3, 3600)
RESET_PER_IP = Limit("password_reset_per_ip", 20, 3600)
RESET_CODE_FAILURES_PER_IP = Limit("password_reset_code_failures_per_ip", 20, 15 * 60)  # D77
LOGIN_FAILURES_PER_IP = Limit("login_failures_per_ip", 20, 15 * 60)
SLUG_CHECKS_PER_IP = Limit("slug_checks_per_ip", 30, 60)
STUDENT_REQUESTS_PER_IP = Limit("student_requests_per_ip", 5, 3600)  # D74

ALL_LIMITS = (
    REGISTRATION_PER_IP,
    RESET_PER_IDENTIFIER,
    RESET_PER_IP,
    RESET_CODE_FAILURES_PER_IP,
    LOGIN_FAILURES_PER_IP,
    SLUG_CHECKS_PER_IP,
    STUDENT_REQUESTS_PER_IP,
)


def reset_all() -> None:
    """Tests only."""
    for limit in ALL_LIMITS:
        limit.reset()
