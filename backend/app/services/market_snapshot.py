from __future__ import annotations

import threading
import time
from typing import Any

from app.services.atm_ltp_service import get_atm_option_snapshot
from app.services.risk_manager import _market_is_open
from app.services.state_store import get_app_state, get_open_position, utc_now


# Process-wide shared snapshot cache. The NIFTY spot + ATM CE/PE are identical
# for every user, so the snapshot is built at most once per TTL and reused by
# all user sessions AND all REST callers. Cost then scales with the number of
# strikes, not the number of users — the point of a shared market-data service.
_SHARED_SNAPSHOT_LOCK = threading.RLock()
_SHARED_SNAPSHOT_CACHE: dict[str, Any] = {"built_monotonic": 0.0, "data": None}
SHARED_SNAPSHOT_TTL_SECONDS = 0.4


def get_shared_nifty_snapshot(
    *,
    allow_rest_fallback: bool = True,
    max_age_seconds: float = SHARED_SNAPSHOT_TTL_SECONDS,
) -> dict[str, Any]:
    """Return the global NIFTY/ATM snapshot, rebuilding at most once per TTL.

    WS reads come from the shared singleton tick cache, so concurrent callers
    reuse one computation instead of each triggering their own Dhan reads.
    """
    now = time.monotonic()
    with _SHARED_SNAPSHOT_LOCK:
        cached = _SHARED_SNAPSHOT_CACHE.get("data")
        built_at = float(_SHARED_SNAPSHOT_CACHE.get("built_monotonic") or 0.0)
        if cached is not None and (now - built_at) <= max_age_seconds:
            return cached
    # Build outside the lock so a (rare) REST-backed build never stalls the
    # WS-only hot path; a concurrent double-build is harmless and idempotent.
    snapshot = build_nifty_snapshot(allow_rest_fallback=allow_rest_fallback)
    with _SHARED_SNAPSHOT_LOCK:
        _SHARED_SNAPSHOT_CACHE["data"] = snapshot
        _SHARED_SNAPSHOT_CACHE["built_monotonic"] = time.monotonic()
    return snapshot


def build_nifty_snapshot(*, allow_rest_fallback: bool = True) -> dict[str, Any]:
    app_state = get_app_state()
    position = get_open_position()
    latest_signal = app_state.get("last_signal") if isinstance(app_state.get("last_signal"), dict) else {}
    live_pnl = position.get("live_pnl") if isinstance(position.get("live_pnl"), dict) else {}
    active_ltp = _number(live_pnl.get("ltp") or position.get("entry_price"))
    nifty_spot = _number(latest_signal.get("niftyPrice") or latest_signal.get("niftySpot"))
    atm = get_atm_option_snapshot(option_side="BOTH", lots=1, allow_rest_fallback=allow_rest_fallback)
    if atm.get("niftySpot") is not None:
        nifty_spot = _number(atm.get("niftySpot"))
    return {
        "niftySpot": nifty_spot,
        "niftySpotSource": atm.get("niftySpotSource"),
        "dayChangePct": _number(latest_signal.get("dayChangePct")),
        "marketStatus": "open" if _market_is_open() else "closed",
        "lastUpdatedAt": utc_now(),
        "atm": atm,
        "latestSignal": {
            "signalId": latest_signal.get("signalId") or app_state.get("last_signal_id"),
            "action": latest_signal.get("action"),
            "side": latest_signal.get("side"),
            "optionSide": latest_signal.get("optionSide"),
            "receivedAt": latest_signal.get("receivedAt") or app_state.get("last_alert_at"),
        },
        "activeOptionLtp": active_ltp if position.get("has_open_position") else None,
        "activeOptionSide": position.get("option_side") if position.get("has_open_position") else None,
        "sparkline": _sparkline_from_position(active_ltp, live_pnl),
        "markers": _markers(latest_signal),
    }


def _sparkline_from_position(active_ltp: float | None, live_pnl: dict[str, Any]) -> list[float]:
    history = live_pnl.get("ltp_history")
    if isinstance(history, list):
        values = [_number(item) for item in history[-24:]]
        return [float(item) for item in values if item is not None]
    return [float(active_ltp)] if active_ltp is not None else []


def _markers(latest_signal: dict[str, Any]) -> list[dict[str, Any]]:
    action = latest_signal.get("action")
    if not action:
        return []
    return [
        {
            "side": action,
            "optionSide": latest_signal.get("optionSide"),
            "timestamp": latest_signal.get("receivedAt"),
        }
    ]


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
