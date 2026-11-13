"""Per-process response counters for the platform health page (Q50 "error rate").

Kept in memory: one API process (Q52) and an hour of history are enough for a health signal.
Nothing about the request is stored except the time and whether it was a server error.
"""

from __future__ import annotations

import threading
import time
from collections import deque

WINDOW_SECONDS = 3600
_events: deque[tuple[float, bool]] = deque()
_lock = threading.Lock()


def record(status: int, now: float | None = None) -> None:
    t = time.monotonic() if now is None else now
    with _lock:
        _events.append((t, status >= 500))
        _trim(t)


def _trim(now: float) -> None:
    while _events and _events[0][0] <= now - WINDOW_SECONDS:
        _events.popleft()


def last_hour(now: float | None = None) -> tuple[int, int]:
    """(responses, server errors) in the last hour."""
    t = time.monotonic() if now is None else now
    with _lock:
        _trim(t)
        return len(_events), sum(1 for _, err in _events if err)


def reset() -> None:
    with _lock:
        _events.clear()
