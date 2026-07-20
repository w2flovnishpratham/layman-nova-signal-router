from __future__ import annotations

import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime
from typing import Any

from app.config import settings


class DhanApiRateLimiter:
    """Process-wide limiter for outbound Dhan HTTP requests.

    By default it reads the general API budget; pass ``rate_getter`` /
    ``burst_getter`` to enforce a stricter per-endpoint limit (e.g. the
    1 req/sec Market Quote / LTP cap).
    """

    def __init__(
        self,
        *,
        rate_getter: Any = None,
        burst_getter: Any = None,
    ) -> None:
        self._lock = threading.Condition(threading.RLock())
        self._requests: deque[float] = deque()
        self._blocked_until = 0.0
        self._rate_getter = rate_getter or (lambda: settings.DHAN_API_MAX_REQUESTS_PER_SECOND)
        self._burst_getter = burst_getter or (lambda: settings.DHAN_API_BURST)

    def wait(self) -> None:
        rate = max(float(self._rate_getter()), 0.1)
        burst = max(int(self._burst_getter()), 1)
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

    def cooldown_remaining(self) -> float:
        """Return the active provider-directed cooldown without waiting."""
        with self._lock:
            return max(self._blocked_until - time.monotonic(), 0.0)

    def snapshot(self) -> dict[str, float | int]:
        """Expose safe limiter state for structured quote metrics."""
        with self._lock:
            now = time.monotonic()
            return {
                "queued_requests": len(self._requests),
                "cooldown_seconds": round(max(self._blocked_until - now, 0.0), 3),
            }

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

# Dedicated serializer for the Market Quote / LTP endpoint (1 req/sec on Dhan).
# Quote calls pass through BOTH limiters; this stricter one is the binding
# constraint and makes concurrent LTP reads queue instead of getting a 429.
dhan_quote_rate_limiter = DhanApiRateLimiter(
    rate_getter=lambda: settings.DHAN_QUOTE_MAX_REQUESTS_PER_SECOND,
    burst_getter=lambda: settings.DHAN_QUOTE_BURST,
)


def before_dhan_request(_request: Any) -> None:
    dhan_api_rate_limiter.wait()


def after_dhan_response(response: Any) -> None:
    dhan_api_rate_limiter.observe_response(response)
