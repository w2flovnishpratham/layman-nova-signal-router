from __future__ import annotations

import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime
from typing import Any

from app.config import settings


class DhanApiRateLimiter:
    """Process-wide limiter for every outbound Dhan HTTP request."""

    def __init__(self) -> None:
        self._lock = threading.Condition(threading.RLock())
        self._requests: deque[float] = deque()
        self._blocked_until = 0.0

    def wait(self) -> None:
        rate = max(float(settings.DHAN_API_MAX_REQUESTS_PER_SECOND), 0.1)
        burst = max(int(settings.DHAN_API_BURST), 1)
        window = burst / rate

        with self._lock:
            while True:
                now = time.monotonic()
                while self._requests and now - self._requests[0] >= window:
                    self._requests.popleft()

                wait_for_block = self._blocked_until - now
                if wait_for_block > 0:
                    self._lock.wait(timeout=wait_for_block)
                    continue

                if len(self._requests) < burst:
                    self._requests.append(now)
                    return

                wait_for_slot = window - (now - self._requests[0])
                self._lock.wait(timeout=max(wait_for_slot, 0.01))

    def observe_response(self, response: Any) -> None:
        if getattr(response, "status_code", None) != 429:
            return

        retry_after = self._retry_after_seconds(getattr(response, "headers", {}))
        retry_after = min(
            max(retry_after, 1.0),
            float(settings.DHAN_API_MAX_RETRY_AFTER_SECONDS),
        )
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + retry_after)
            self._lock.notify_all()

    @staticmethod
    def _retry_after_seconds(headers: Any) -> float:
        raw = str(headers.get("Retry-After") or "").strip()
        if not raw:
            return 1.0
        try:
            return float(raw)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
                return max(retry_at.timestamp() - time.time(), 1.0)
            except (TypeError, ValueError, OverflowError):
                return 1.0


dhan_api_rate_limiter = DhanApiRateLimiter()


def before_dhan_request(_request: Any) -> None:
    dhan_api_rate_limiter.wait()


def after_dhan_response(response: Any) -> None:
    dhan_api_rate_limiter.observe_response(response)
