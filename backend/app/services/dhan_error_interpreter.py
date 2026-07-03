from __future__ import annotations

import json
from typing import Any

from app.services.normalized_errors import classify_failure


def _response_to_text(response_text_or_json: Any) -> str:
    if response_text_or_json is None:
        return ""
    if isinstance(response_text_or_json, str):
        return response_text_or_json
    try:
        return json.dumps(response_text_or_json, sort_keys=True, default=str)
    except TypeError:
        return str(response_text_or_json)


def interpret_dhan_error(status_code: int | None, response_text_or_json: Any) -> dict[str, Any]:
    text = _response_to_text(response_text_or_json)
    lowered = text.lower()
    normalized = classify_failure(
        text,
        source="DHAN",
        status_code=status_code,
        raw_response=response_text_or_json,
        order_sent_to_broker=False,
        money_at_risk=False,
    )

    if any(term in lowered for term in ("static ip", "whitelist", "white list", "unauthorised ip", "unauthorized ip", "invalid ip")):
        return {
            **normalized,
            "category": "STATIC_IP",
            "message": "Dhan rejected the request because the backend outgoing IP may not match the configured static IP whitelist.",
            "next_action": "Confirm outgoing IP from backend, Dhan static IP whitelist, same account/client ID, and regenerate token after whitelist.",
            "nextAction": "Confirm outgoing IP from backend, Dhan static IP whitelist, same account/client ID, and regenerate token after whitelist.",
        }

    if "expired" in lowered and "token" in lowered:
        return {
            **normalized,
            "category": "TOKEN_EXPIRED",
            "message": "Dhan reported an expired or invalid access token.",
            "next_action": "Regenerate Dhan access token and restart backend.",
            "nextAction": "Regenerate Dhan access token and restart backend.",
        }

    if any(term in lowered for term in ("access token", "authentication", "unauthorized", "unauthorised", "invalid token", "token")) or status_code in (401, 403):
        return {
            **normalized,
            "category": "AUTH",
            "message": "Dhan authentication failed for the configured client ID/access token.",
            "next_action": "Regenerate Dhan access token and restart backend.",
            "nextAction": "Regenerate Dhan access token and restart backend.",
        }

    if any(term in lowered for term in ("market closed", "market hours", "exchange closed", "trading not allowed")):
        return {
            **normalized,
            "category": "MARKET_CLOSED",
            "message": "Dhan rejected the request because the market/session is closed or trading is not allowed now.",
            "next_action": "This is expected after market close; test read-only endpoint or wait for market.",
            "nextAction": "This is expected after market close; test read-only endpoint or wait for market.",
        }

    if any(term in lowered for term in ("profit price", "stop loss price", "stoploss price", "target price")):
        return {
            **normalized,
            "category": "LTP",
            "message": "Dhan rejected the Super Order because the TP/SL leg prices went stale against the live premium (price moved between Nova's LTP snapshot and Dhan's validation).",
            "next_action": "Nova retries once automatically with fresh LTP. If it still fails, resend the signal; consider a wider TP percent.",
            "nextAction": "Nova retries once automatically with fresh LTP. If it still fails, resend the signal; consider a wider TP percent.",
        }

    if any(term in lowered for term in ("invalid field", "missing field", "security id", "securityid", "quantity", "invalid input", "invalid request", "expired instrument", "invalid security")):
        return {
            **normalized,
            "category": "PAYLOAD_INVALID",
            "message": "Dhan rejected the order payload shape or one of its required order fields.",
            "next_action": "Inspect final Dhan payload. Do not send Pine payload directly.",
            "nextAction": "Inspect final Dhan payload. Do not send Pine payload directly.",
        }

    if status_code == 429 or "rate" in lowered:
        return {
            **normalized,
            "category": "RATE_LIMIT",
            "message": "Dhan appears to be rate limiting the request.",
            "next_action": "Wait before retrying and avoid repeated debug/live calls.",
            "nextAction": "Wait before retrying and avoid repeated debug/live calls.",
        }

    if any(term in lowered for term in ("insufficient margin", "margin", "rms")):
        return {
            **normalized,
            "category": "MARGIN",
            "message": "Dhan reported insufficient margin or an RMS rejection.",
            "next_action": "Check Dhan margin/RMS remarks and reduce lots before retrying.",
            "nextAction": "Check Dhan margin/RMS remarks and reduce lots before retrying.",
        }

    if any(term in lowered for term in ("insufficient", "fund", "balance")):
        return {
            **normalized,
            "category": "FUNDS",
            "message": "Dhan reported insufficient funds, margin, or balance.",
            "next_action": "Check Dhan funds/margin before placing the order.",
            "nextAction": "Check Dhan funds/margin before placing the order.",
        }

    if any(term in lowered for term in ("ltp", "last traded price")):
        return {
            **normalized,
            "category": "LTP",
            "message": "Dhan LTP was unavailable for this instrument.",
            "next_action": "Wait for automatic LTP polling after confirming market data access and static IP routing.",
            "nextAction": "Wait for automatic LTP polling after confirming market data access and static IP routing.",
        }

    if any(term in lowered for term in ("timeout", "timed out", "unavailable", "service unavailable")):
        return {
            **normalized,
            "category": normalized["category"],
            "message": normalized["userMessage"],
            "next_action": normalized["nextAction"],
        }

    if any(term in lowered for term in ("partial", "part_traded")):
        normalized = classify_failure(text, source="DHAN", status_code=status_code, raw_response=response_text_or_json, order_sent_to_broker=True, money_at_risk=True)
        return {
            **normalized,
            "category": "PARTIAL_FILL",
            "message": normalized["userMessage"],
            "next_action": normalized["nextAction"],
        }

    if any(term in lowered for term in ("pending", "transit")):
        normalized = classify_failure(text, source="DHAN", status_code=status_code, raw_response=response_text_or_json, order_sent_to_broker=True, money_at_risk=True)
        return {
            **normalized,
            "category": "ORDER_PENDING",
            "message": normalized["userMessage"],
            "next_action": normalized["nextAction"],
        }

    if any(term in lowered for term in ("order_state_unknown", "unknown order", "verification failed", "manual dhan")):
        normalized = classify_failure(text, source="DHAN", status_code=status_code, raw_response=response_text_or_json, order_sent_to_broker=True, money_at_risk=True)
        return {
            **normalized,
            "category": "ORDER_UNKNOWN",
            "message": normalized["userMessage"],
            "next_action": normalized["nextAction"],
        }

    return {
        **normalized,
        "category": "UNKNOWN",
        "message": "Dhan error could not be classified from the current response.",
        "next_action": "Inspect status code, response body, outgoing IP, headers, and final Dhan payload.",
        "nextAction": "Inspect status code, response body, outgoing IP, headers, and final Dhan payload.",
    }
