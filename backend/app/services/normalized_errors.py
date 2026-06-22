from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.schemas.signal import NormalizedSignal


SENSITIVE_KEYS = {
    "access_token",
    "access-token",
    "authorization",
    "secret",
    "token",
    "webhook_secret",
    "google_auth_token",
    "client_secret",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_text(value: Any) -> str:
    text = _to_text(value)
    if not text:
        return ""
    text = re.sub(r"(?i)(access-token|access_token|authorization|webhook_secret|secret|token)\s*[:=]\s*[^,\s}\]]+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]", text)
    return text[:4000]


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in SENSITIVE_KEYS:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def build_error(
    *,
    source: str,
    category: str,
    severity: str,
    user_title: str,
    user_message: str,
    technical_message: str = "",
    next_action: str,
    retryable: bool = False,
    money_at_risk: bool | None = False,
    order_sent_to_broker: bool | None = False,
    correlation_id: str | None = None,
    signal_id: str | None = None,
    raw_status_code: int | None = None,
    action_buttons: list[str] | None = None,
    mode: str | None = None,
    debug_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = utc_timestamp()
    stable = "|".join(
        str(item or "")
        for item in (source, category, signal_id, correlation_id, raw_status_code, sanitize_text(technical_message)[:200])
    )
    error_id = f"ERR-{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:10].upper()}"
    error = {
        "errorId": error_id,
        "source": _normalize_source(source),
        "category": _normalize_category(category),
        "severity": severity if severity in {"info", "warning", "blocked", "critical"} else "warning",
        "userTitle": user_title,
        "userMessage": user_message,
        "technicalMessage": sanitize_text(technical_message),
        "nextAction": next_action,
        "retryable": bool(retryable),
        "moneyAtRisk": money_at_risk,
        "orderSentToBroker": order_sent_to_broker,
        "actionButtons": action_buttons or _default_action_buttons(category, source, retryable),
        "correlationId": correlation_id,
        "signalId": signal_id,
        "rawStatusCode": raw_status_code,
        "timestamp": timestamp,
        "mode": mode,
    }
    error["debugPack"] = sanitize_payload(
        {
            "timestamp": timestamp,
            "mode": mode,
            "source": error["source"],
            "category": error["category"],
            "severity": error["severity"],
            "signalId": signal_id,
            "correlationId": correlation_id,
            "orderSentToBroker": order_sent_to_broker,
            "moneyAtRisk": money_at_risk,
            "rawStatusCode": raw_status_code,
            "technicalMessage": error["technicalMessage"],
            **(debug_pack or {}),
        }
    )
    return error


def classify_failure(
    message: Any,
    *,
    source: str = "NOVA_BACKEND",
    status: str | None = None,
    status_code: int | None = None,
    signal: NormalizedSignal | None = None,
    mode: str | None = None,
    order_sent_to_broker: bool | None = False,
    money_at_risk: bool | None = False,
    correlation_id: str | None = None,
    raw_response: Any = None,
    debug_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    technical = sanitize_text(message)
    raw_text = sanitize_text(raw_response)
    lowered = f"{technical} {raw_text} {status or ''}".lower()
    source = _normalize_source(source)

    if any(term in lowered for term in ("token expired", "expired token", "jwt expired")):
        return build_error(
            source=source,
            category="TOKEN_EXPIRED",
            severity="blocked",
            user_title="Dhan token expired",
            user_message="Nova blocked routing because the Dhan access token is no longer valid.",
            technical_message=technical or raw_text,
            next_action="Reconnect Dhan in setup, then restart or reconfigure the router.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("invalid token", "unauthorized", "unauthorised", "authentication", "client id", "client-id", "access token")) or status_code in {401, 403}:
        return build_error(
            source=source,
            category="AUTH",
            severity="blocked",
            user_title="Dhan authentication failed",
            user_message="Nova could not authenticate with Dhan for this account.",
            technical_message=technical or raw_text,
            next_action="Reconnect Dhan and confirm the client ID matches the generated access token.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("static ip", "whitelist", "white list", "unauthorized ip", "unauthorised ip", "invalid ip", "outgoing ip", "execution node")):
        return build_error(
            source="STATIC_IP" if source == "NOVA_BACKEND" else source,
            category="STATIC_IP",
            severity="blocked",
            user_title="Static IP check failed",
            user_message="Dhan appears to be rejecting the request because the outgoing IP is not whitelisted.",
            technical_message=technical or raw_text,
            next_action="Verify the assigned static IP and whitelist it in Dhan before routing live orders.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("insufficient fund", "insufficient balance", "available balance", "virtual balance", "funds")) or ("insufficient" in lowered and "balance" in lowered):
        return build_error(
            source=source,
            category="FUNDS",
            severity="blocked",
            user_title="Insufficient funds",
            user_message="Nova did not route the order because available funds are not enough for this trade.",
            technical_message=technical or raw_text,
            next_action="Check Dhan funds, reduce lots, or add margin before retrying.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=bool(order_sent_to_broker),
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("insufficient margin", "margin", "rms", "risk management system")):
        return build_error(
            source=source,
            category="MARGIN",
            severity="blocked",
            user_title="Margin/RMS rejection",
            user_message="Dhan or Nova blocked the order because margin or RMS checks failed.",
            technical_message=technical or raw_text,
            next_action="Check Dhan RMS remarks, reduce lots, and verify product type before retrying.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=order_sent_to_broker,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("market closed", "market is closed", "market hours", "exchange closed", "trading not allowed")):
        return build_error(
            source=source,
            category="MARKET_CLOSED",
            severity="blocked",
            user_title="Market is closed",
            user_message="Nova blocked the order because the exchange session is closed.",
            technical_message=technical or raw_text,
            next_action="Wait for market hours or use paper-only checks that do not place orders.",
            retryable=True,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("ltp", "last traded price", "market-feed", "market feed", "tick")):
        return build_error(
            source=source if source != "NOVA_BACKEND" else "DHAN",
            category="LTP",
            severity="blocked",
            user_title="LTP unavailable",
            user_message="Nova could not fetch the option LTP needed to price or simulate this order.",
            technical_message=technical or raw_text,
            next_action="Wait for automatic LTP polling after confirming Dhan market data and static IP access.",
            retryable=True,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if status_code == 429 or "rate limit" in lowered or "too many" in lowered:
        return build_error(
            source=source,
            category="RATE_LIMIT",
            severity="warning",
            user_title="Rate limit hit",
            user_message="Nova or Dhan is temporarily rate limiting requests.",
            technical_message=technical or raw_text,
            next_action="Wait briefly before retrying. Avoid repeated manual clicks.",
            retryable=True,
            money_at_risk=False,
            order_sent_to_broker=order_sent_to_broker,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("timeout", "timed out")):
        unknown_order = "unknown" in lowered or bool(order_sent_to_broker)
        return build_error(
            source=source if source != "NOVA_BACKEND" else "NETWORK",
            category="ORDER_UNKNOWN" if unknown_order else "TIMEOUT",
            severity="critical" if unknown_order else "warning",
            user_title="Manual Dhan check required" if unknown_order else "Request timed out",
            user_message=(
                "Nova cannot prove whether Dhan accepted the order. Check Dhan before sending another order."
                if unknown_order
                else "The request timed out before Nova received a confirmed result."
            ),
            technical_message=technical or raw_text,
            next_action="Open Dhan and verify order status before retrying." if unknown_order else "Retry only after checking connectivity and static IP health.",
            retryable=not unknown_order,
            money_at_risk=True if unknown_order else False,
            order_sent_to_broker=True if unknown_order else order_sent_to_broker,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if "duplicate" in lowered:
        return build_error(
            source="TRADINGVIEW" if source == "NOVA_BACKEND" else source,
            category="DUPLICATE_SIGNAL",
            severity="info",
            user_title="Duplicate signal ignored",
            user_message="Nova ignored this alert because the same signal ID was already processed.",
            technical_message=technical or raw_text,
            next_action="No action needed unless the TradingView alert ID is being reused incorrectly.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if "no open position" in lowered:
        return build_error(
            source="RISK_ENGINE",
            category="NO_POSITION",
            severity="blocked",
            user_title="No open position to exit",
            user_message="Nova blocked the EXIT because it is not tracking an open position.",
            technical_message=technical or raw_text,
            next_action="Check whether the position was already closed manually in Dhan.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("open position already exists", "position already exists", "position mismatch", "reconcile")):
        return build_error(
            source="RISK_ENGINE",
            category="POSITION_MISMATCH",
            severity="blocked",
            user_title="Position state mismatch",
            user_message="Nova blocked the signal because the tracked position state does not match the requested action.",
            technical_message=technical or raw_text,
            next_action="Verify the active position in Dhan and Nova before sending another entry.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("invalid json", "invalid payload", "payload", "missing", "invalid quantity", "quantity", "qty", "whole lot", "lot count", "securityid", "security id", "instrument")):
        return build_error(
            source=source,
            category="PAYLOAD_INVALID",
            severity="blocked",
            user_title="Signal payload invalid",
            user_message="Nova blocked the signal because required order fields were missing or invalid.",
            technical_message=technical or raw_text,
            next_action="Fix the TradingView alert template or instrument mapping, then send a new signal.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("pending_confirmation", "pending", "transit")):
        return build_error(
            source=source,
            category="ORDER_PENDING",
            severity="warning",
            user_title="Order sent, fill pending",
            user_message="Dhan accepted the order request, but Nova has not verified the final fill yet.",
            technical_message=technical or raw_text,
            next_action="Open Dhan and verify order status before sending another related order.",
            retryable=False,
            money_at_risk=True,
            order_sent_to_broker=True,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if "partial" in lowered:
        return build_error(
            source=source,
            category="PARTIAL_FILL",
            severity="warning",
            user_title="Partial fill",
            user_message="Dhan filled only part of the requested quantity.",
            technical_message=technical or raw_text,
            next_action="Check remaining quantity in Dhan before sending another order.",
            retryable=False,
            money_at_risk=True,
            order_sent_to_broker=True,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("order_state_unknown", "state unknown", "verification failed", "manual dhan check")):
        return build_error(
            source=source,
            category="ORDER_UNKNOWN",
            severity="critical",
            user_title="Manual Dhan check required",
            user_message="Nova could not verify the order state after sending the request.",
            technical_message=technical or raw_text,
            next_action="Open Dhan and confirm order/position status before retrying.",
            retryable=False,
            money_at_risk=True,
            order_sent_to_broker=True,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("blocked", "global_kill", "emergency_stop", "allow_entry", "side filter", "flip blocked", "risk")):
        return build_error(
            source="RISK_ENGINE",
            category="RISK_BLOCK",
            severity="blocked",
            user_title="Nova blocked this for your safety",
            user_message="The signal reached Nova, but risk controls prevented an order.",
            technical_message=technical or raw_text,
            next_action="Review engine mode, side filter, entry pause, and risk configuration.",
            retryable=False,
            money_at_risk=False,
            order_sent_to_broker=False,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    if any(term in lowered for term in ("unavailable", "service unavailable", "connection", "network")):
        return build_error(
            source="NETWORK" if source == "NOVA_BACKEND" else source,
            category="TIMEOUT",
            severity="warning",
            user_title="Broker/network unavailable",
            user_message="Nova could not reach the broker or network dependency reliably.",
            technical_message=technical or raw_text,
            next_action="Check Dhan connectivity, static IP routing, and retry after the service recovers.",
            retryable=True,
            money_at_risk=False,
            order_sent_to_broker=order_sent_to_broker,
            signal_id=_signal_id(signal),
            correlation_id=correlation_id,
            raw_status_code=status_code,
            mode=mode,
            debug_pack=debug_pack,
        )

    return build_error(
        source=source,
        category="UNKNOWN",
        severity="warning",
        user_title="Order could not be completed",
        user_message="Nova could not classify this failure with high confidence.",
        technical_message=technical or raw_text,
        next_action="Copy the debug pack and inspect broker/router logs before retrying.",
        retryable=False,
        money_at_risk=money_at_risk,
        order_sent_to_broker=order_sent_to_broker,
        signal_id=_signal_id(signal),
        correlation_id=correlation_id,
        raw_status_code=status_code,
        mode=mode,
        debug_pack=debug_pack,
    )


def order_journey(
    *,
    signal: NormalizedSignal,
    execution_result: dict[str, Any],
    normalized_error: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {
            "label": "TradingView signal received" if signal.source in {"tradingview", "webhook"} else "Nova signal received",
            "status": "complete",
            "detail": f"{signal.action} {signal.option_side or signal.symbol}",
        }
    ]
    blocked = bool(execution_result.get("blocked"))
    success = execution_result.get("success")
    error_category = str((normalized_error or {}).get("category") or "")
    order_sent = (normalized_error or {}).get("orderSentToBroker")

    if blocked:
        steps.append({"label": "Nova risk check blocked", "status": "failed", "detail": execution_result.get("reason")})
    else:
        steps.append({"label": "Nova risk check passed", "status": "complete", "detail": "Backend validation completed."})

    if error_category == "LTP":
        steps.append({"label": "Dhan LTP failed", "status": "failed", "detail": "No order sent."})
    elif execution_result.get("ltp_check"):
        steps.append({"label": "Dhan LTP fetched", "status": "complete", "detail": "Option premium check completed."})

    if success is True:
        steps.append({"label": "Order sent", "status": "complete", "detail": execution_result.get("order_id")})
        if execution_result.get("broker_status") in {"TRADED", "PARTIAL_FILL"} or execution_result.get("avg_price"):
            steps.append({"label": "Fill verified", "status": "complete", "detail": execution_result.get("broker_status") or execution_result.get("status")})
        elif execution_result.get("status") in {"PENDING_CONFIRMATION", "PARTIAL_FILL_PENDING"}:
            steps.append({"label": "Fill pending", "status": "pending", "detail": execution_result.get("status")})
    elif success is False or blocked:
        if order_sent is True:
            steps.append({"label": "Order sent", "status": "warning", "detail": "Broker verification required."})
        else:
            steps.append({"label": "No order sent", "status": "failed", "detail": (normalized_error or {}).get("userMessage")})
    else:
        steps.append({"label": "Order status unknown", "status": "warning", "detail": execution_result.get("status")})

    if signal.action == "ENTRY" and success is True and execution_result.get("avg_price"):
        steps.append({"label": "Position opened", "status": "complete", "detail": execution_result.get("trading_symbol") or signal.trading_symbol})
    if signal.action == "EXIT" and success is True:
        steps.append({"label": "Exit verified", "status": "complete", "detail": execution_result.get("status")})
    return steps


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _signal_id(signal: NormalizedSignal | None) -> str | None:
    return signal.signal_id if signal is not None else None


def _normalize_source(source: str) -> str:
    source = str(source or "NOVA_BACKEND").upper()
    aliases = {
        "WEBHOOK": "TRADINGVIEW",
        "TRADING_VIEW": "TRADINGVIEW",
        "BROKER": "DHAN",
        "DHAN_CLIENT": "DHAN",
        "RISK": "RISK_ENGINE",
        "PAPER": "PAPER_ENGINE",
    }
    source = aliases.get(source, source)
    allowed = {"TRADINGVIEW", "DHAN", "NOVA_BACKEND", "PAPER_ENGINE", "RISK_ENGINE", "NETWORK", "PUBSUB", "STATIC_IP"}
    return source if source in allowed else "NOVA_BACKEND"


def _normalize_category(category: str) -> str:
    category = str(category or "UNKNOWN").upper()
    aliases = {
        "INSUFFICIENT_FUNDS": "FUNDS",
        "INVALID_TOKEN": "AUTH",
        "UNAUTHORIZED": "AUTH",
        "INVALID_SIGNAL": "PAYLOAD_INVALID",
        "INVALID_JSON": "PAYLOAD_INVALID",
        "SETUP_INCOMPLETE": "CONFIG_MISSING",
    }
    category = aliases.get(category, category)
    allowed = {
        "AUTH",
        "TOKEN_EXPIRED",
        "STATIC_IP",
        "FUNDS",
        "MARGIN",
        "LTP",
        "MARKET_CLOSED",
        "PAYLOAD_INVALID",
        "DUPLICATE_SIGNAL",
        "NO_POSITION",
        "POSITION_MISMATCH",
        "RATE_LIMIT",
        "TIMEOUT",
        "ORDER_PENDING",
        "PARTIAL_FILL",
        "ORDER_UNKNOWN",
        "RISK_BLOCK",
        "CONFIG_MISSING",
        "UNKNOWN",
    }
    return category if category in allowed else "UNKNOWN"


def _default_action_buttons(category: str, source: str, retryable: bool) -> list[str]:
    normalized = _normalize_category(category)
    buttons: list[str] = []
    if retryable:
        buttons.append("retry")
    if normalized in {"AUTH", "TOKEN_EXPIRED", "CONFIG_MISSING"}:
        buttons.append("reconnect_dhan")
    if normalized == "STATIC_IP":
        buttons.append("verify_static_ip")
    buttons.append("copy_debug_pack")
    if _normalize_source(source) == "DHAN" or normalized in {"ORDER_UNKNOWN", "ORDER_PENDING", "PARTIAL_FILL"}:
        buttons.append("open_dhan")
    return buttons
