from __future__ import annotations

from typing import Any, Literal

from app.config import DEFAULT_NIFTY_LOT_SIZE


def map_signal_to_trade(
    *,
    action: Literal["BUY", "SELL"],
    current_position: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    side_filter = config.get("risk", {}).get("side", "BOTH")
    lots = int(config.get("risk", {}).get("lots", 1))

    if action == "BUY" and side_filter == "PE":
        return {"blocked": True, "reason": "BUY signal blocked by PE-only side filter."}
    if action == "SELL" and side_filter == "CE":
        return {"blocked": True, "reason": "SELL signal blocked by CE-only side filter."}

    opt_type = "CE" if action == "BUY" else "PE"
    quantity = lots * DEFAULT_NIFTY_LOT_SIZE
    intent = "open"
    if current_position and current_position.get("optType") != opt_type:
        intent = "reverse"

    return {
        "blocked": False,
        "intent": intent,
        "optType": opt_type,
        "quantity": quantity,
    }
