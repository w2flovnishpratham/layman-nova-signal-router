import json

from app.services.dhan_error_interpreter import interpret_dhan_error
from app.services.normalized_errors import classify_failure


def test_dhan_error_classifies_token_expired():
    error = interpret_dhan_error(401, {"errorMessage": "Invalid token or token expired"})

    assert error["category"] == "TOKEN_EXPIRED"
    assert error["source"] == "DHAN"
    assert error["orderSentToBroker"] is False
    assert "Dhan access token" in error["nextAction"]


def test_dhan_error_classifies_static_ip_failure():
    error = interpret_dhan_error(403, {"errorMessage": "Unauthorized IP. Static IP not whitelisted."})

    assert error["category"] == "STATIC_IP"
    assert error["source"] in {"DHAN", "STATIC_IP"}
    assert error["moneyAtRisk"] is False
    assert "static ip" in error["nextAction"].lower()


def test_dhan_error_classifies_funds_margin_ltp_and_unknown_order():
    funds = interpret_dhan_error(400, "insufficient funds")
    margin = interpret_dhan_error(400, "RMS rejection: insufficient margin")
    ltp = interpret_dhan_error(500, "Dhan LTP request failed")
    unknown = interpret_dhan_error(None, "Dhan API timeout; order_state_unknown manual Dhan verification required")

    assert funds["category"] == "FUNDS"
    assert margin["category"] == "MARGIN"
    assert ltp["category"] == "LTP"
    assert unknown["category"] == "ORDER_UNKNOWN"
    assert unknown["moneyAtRisk"] is True


def test_debug_pack_redacts_sensitive_values():
    error = classify_failure(
        "access-token=raw-token secret=webhook-secret",
        source="DHAN",
        raw_response={"access_token": "raw-token", "nested": {"webhook_secret": "webhook-secret"}},
    )

    serialized = json.dumps(error)
    assert "raw-token" not in serialized
    assert "webhook-secret" not in serialized
    assert "[REDACTED]" in serialized
