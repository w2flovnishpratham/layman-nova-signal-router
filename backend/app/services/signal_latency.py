"""How long a webhook takes to become a placed order.

The product claim is "TradingView alert to live order in milliseconds", and
until now nothing measured it -- a slow broker round-trip or a stall on the
event loop would only surface as a user noticing their fill was late.

Deliberately records only signals that actually reached a placed order.
Rejections (rate limit, duplicate, blocked by risk) return far earlier and
would drag the percentiles down, hiding exactly the slowness this exists to
catch.

In-process and bounded: this is an operational read for /api/health and the
logs, not an analytics store. A restart clears it, which is fine -- the
question it answers is "is routing slow right now".
"""
from __future__ import annotations

import threading
from collections import deque

# ~a day of signals for a retail terminal, and cheap to keep resident.
_MAX_SAMPLES = 500

_lock = threading.Lock()
_samples: deque[float] = deque(maxlen=_MAX_SAMPLES)


def record_order_latency(seconds: float) -> None:
    """Record one webhook-received -> order-placed duration. Never raises:
    this sits on the live trading path and must not break a routed order."""
    try:
        if seconds < 0:
            return
        with _lock:
            _samples.append(seconds * 1000.0)
    except Exception:
        pass


def _percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        return 0.0
    # Nearest-rank: with tens of samples, interpolating between neighbours
    # implies a precision this sample size does not have.
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def latency_snapshot() -> dict[str, float | int | None]:
    """Percentiles over the recent window, in milliseconds."""
    with _lock:
        values = sorted(_samples)
    if not values:
        return {"samples": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "samples": len(values),
        "p50_ms": round(_percentile(values, 0.50), 1),
        "p95_ms": round(_percentile(values, 0.95), 1),
        "max_ms": round(values[-1], 1),
    }


def reset() -> None:
    """Test hook."""
    with _lock:
        _samples.clear()
