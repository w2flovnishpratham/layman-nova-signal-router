from __future__ import annotations

import json
from typing import Any


def _response_to_text(response_text_or_json: Any) -> str:
    if response_text_or_json is None:
        return ""
    if isinstance(response_text_or_json, str):
        return response_text_or_json
    try:
        return json.dumps(response_text_or_json, sort_keys=True, default=str)
    except TypeError:
        return str(response_text_or_json)


def interpret_dhan_error(status_code: int | None, response_text_or_json: Any) -> dict[str, str]:
    text = _response_to_text(response_text_or_json)
    lowered = text.lower()

    if any(term in lowered for term in ("static ip", "whitelist", "white list", "unauthorised ip", "unauthorized ip", "invalid ip")):
        return {
            "category": "STATIC_IP",
            "message": "Dhan rejected the request because the backend outgoing IP may not match the configured static IP whitelist.",
            "next_action": "Confirm outgoing IP from backend, Dhan static IP whitelist, same account/client ID, and regenerate token after whitelist.",
        }

    if "expired" in lowered and "token" in lowered:
        return {
            "category": "TOKEN_EXPIRED",
            "message": "Dhan reported an expired or invalid access token.",
            "next_action": "Regenerate Dhan access token and restart backend.",
        }

    if any(term in lowered for term in ("access token", "authentication", "unauthorized", "unauthorised", "invalid token", "token")) or status_code in (401, 403):
        return {
            "category": "AUTH",
            "message": "Dhan authentication failed for the configured client ID/access token.",
            "next_action": "Regenerate Dhan access token and restart backend.",
        }

    if any(term in lowered for term in ("market closed", "market hours", "exchange closed", "trading not allowed")):
        return {
            "category": "MARKET_CLOSED",
            "message": "Dhan rejected the request because the market/session is closed or trading is not allowed now.",
            "next_action": "This is expected after market close; test read-only endpoint or wait for market.",
        }

    if any(term in lowered for term in ("invalid field", "missing field", "security id", "securityid", "quantity", "invalid input", "invalid request")):
        return {
            "category": "PAYLOAD_INVALID",
            "message": "Dhan rejected the order payload shape or one of its required order fields.",
            "next_action": "Inspect final Dhan payload. Do not send Pine payload directly.",
        }

    if status_code == 429 or "rate" in lowered:
        return {
            "category": "RATE_LIMIT",
            "message": "Dhan appears to be rate limiting the request.",
            "next_action": "Wait before retrying and avoid repeated debug/live calls.",
        }

    if any(term in lowered for term in ("insufficient", "fund", "margin", "balance")):
        return {
            "category": "INSUFFICIENT_FUNDS",
            "message": "Dhan reported insufficient funds, margin, or balance.",
            "next_action": "Check Dhan funds/margin before placing the order.",
        }

    return {
        "category": "UNKNOWN",
        "message": "Dhan error could not be classified from the current response.",
        "next_action": "Inspect status code, response body, outgoing IP, headers, and final Dhan payload.",
    }
