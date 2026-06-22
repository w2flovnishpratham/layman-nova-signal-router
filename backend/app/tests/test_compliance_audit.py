"""
DhanHQ v2 Compliance & Production-Readiness Tests
===================================================
Covers all gaps identified in the DHAN_V2_COMPLIANCE_AUDIT.md:

- Webhook secret: no-secret blocks, wrong secret blocks, correct secret accepts
- Signal parser: invalid qty, missing order_legs, missing secret, unsupported payload
- Security ID: NIFTY underlying ID=13 blocked, resolver order, scrip-master lookup
- Quantity: ABSOLUTE mode, signal qty passes through as absolute contracts
- Order gating: MOCK never calls Dhan, REAL+disabled never calls Dhan,
                missing securityId blocks, emergency stop blocks entry
- Engine start: fails if Dhan not connected, fails if webhook secret not set,
                fails if token expired, passes in dry-run with live disabled
- Dhan payload: required fields present, no raw Pine fields, triggerPrice present
- Post-order polling: status result included in execution result
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import setup as setup_router
from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, credential_vault, state_store
from app.services import risk_manager
from app.services.dhan_client import DhanListResult, DhanLtpResult, DhanOrderResult, DhanOrderStatusResult
from app.services.dhan_debugger import validate_dhan_payload
from app.services.execution_router import (
    _build_dhan_payload_and_resolution,
    _build_dhan_super_order_payload,
    _place_order,
    route_entry_signal,
    route_exit_signal,
    route_signal,
)
from app.services.security_id_resolver import (
    NIFTY_UNDERLYING_ID,
    resolve_security_id,
)
from app.services.signal_parser import (
    PayloadParseError,
    UnsupportedPayloadFormatError,
    parse_webhook_payload,
)
from app.tests.test_signal_parser import TEST_WEBHOOK_SECRET, nova_payload, pine_payload


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Full integration test client with isolated tmp state."""
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
    credential_vault._LOCAL_MEMORY_PAYLOAD.update({"version": 1, "dhan": None, "webhook_secret": None})
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "WEBHOOK_TRADING_ENABLED", True)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)
    setup_router._DHAN_CONNECT_RATE_LIMIT.clear()

    from app.main import app

    with TestClient(app) as test_client:
        test_client.post("/api/control/reset-state")
        test_client.post("/api/control/clear-seen-signals")
        test_client.post("/api/setup/webhook-secret", json={"webhook_secret": TEST_WEBHOOK_SECRET})
        test_client.post(
            "/api/setup/risk",
            json={
                "max_qty_per_order": 1,
                "allow_entry": True,
                "allow_exit": True,
            },
        )
        state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
        yield test_client


def make_signal(
    security_id: str | None = None,
    qty: int = 1,
    action: str = "ENTRY",
    side: str = "BUY",
) -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="PINE_MULTI_LEG",
        secret=TEST_WEBHOOK_SECRET,
        signal_id="compliance-test",
        strategy_code="TRADINGVIEW_NIFTY_V1",
        action=action,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        symbol="NIFTY",
        instrument_type="OPT",
        exchange_segment="NSE_FNO",
        security_id=security_id,
        trading_symbol="NIFTY 2026-05-28 22500 CE",
        option_side="CE",
        strike=22500.0,
        expiry="2026-05-28",
        qty=qty,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload={"test": True},
    )


# ===========================================================================
# TASK 10: Webhook secret flow
# ===========================================================================

class TestWebhookSecretFlow:
    def test_webhook_rejected_if_secret_not_configured(self, tmp_path, monkeypatch):
        """If no webhook secret is stored, every webhook must be rejected with 403."""
        state_dir = tmp_path / "runtime_state"
        log_dir = tmp_path / "runtime_logs"
        monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
        monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
        monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
        monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
        monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
        credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
        credential_vault._LOCAL_MEMORY_PAYLOAD.update({"version": 1, "dhan": None, "webhook_secret": None})
        log_files = {
            "webhook": log_dir / "webhook_events.jsonl",
            "order": log_dir / "order_events.jsonl",
            "audit": log_dir / "audit_events.jsonl",
            "error": log_dir / "errors.jsonl",
        }
        monkeypatch.setattr(state_store, "LOG_FILES", log_files)
        monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
        monkeypatch.setattr(settings, "APP_ENV", "local")
        monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
        monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")

        from app.main import app
        with TestClient(app) as c:
            # No webhook secret configured — do not call /api/setup/webhook-secret
            state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
            response = c.post("/webhook/tradingview", json=pine_payload("B"))
        assert response.status_code == 403
        body = response.json()
        assert body["status"] == "SETUP_INCOMPLETE"

    def test_webhook_rejected_if_wrong_secret(self, client):
        payload = pine_payload("B")
        payload["secret"] = "totally-wrong-secret"
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 403
        assert response.json()["status"] == "UNAUTHORIZED"

    def test_webhook_accepted_if_correct_secret(self, client):
        payload = nova_payload("ENTRY", "BUY", "correct-secret-test-001")
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 200
        assert response.json()["accepted"] is True

    def test_setup_status_does_not_return_webhook_secret(self, client):
        response = client.get("/api/setup/status")
        body = response.json()
        assert "webhook_secret" not in body
        assert "webhook_secret_set" in body
        # Must only be a boolean, not the actual secret value
        assert isinstance(body["webhook_secret_set"], bool)

    def test_webhook_secret_requires_minimum_length_and_entropy(self, client):
        short_response = client.post("/api/setup/webhook-secret", json={"webhook_secret": "short"})
        assert short_response.status_code == 422

        weak_response = client.post("/api/setup/webhook-secret", json={"webhook_secret": "1234567890123456"})
        assert weak_response.status_code == 400
        assert "too weak" in weak_response.json()["detail"]

        strong_response = client.post("/api/setup/webhook-secret", json={"webhook_secret": TEST_WEBHOOK_SECRET})
        assert strong_response.status_code == 200
        assert strong_response.json()["webhook_secret_set"] is True

    def test_existing_weak_webhook_secret_blocks_routing(self, client):
        credential_vault._LOCAL_MEMORY_PAYLOAD["webhook_secret"] = "1234567890123456"

        response = client.post("/webhook/tradingview", json=pine_payload("B"))

        assert response.status_code == 403
        body = response.json()
        assert body["status"] == "SETUP_INCOMPLETE"
        assert "too weak" in body["message"]


# ===========================================================================
# TASK 3 / TASK 18: Signal parser edge cases
# ===========================================================================

class TestSignalParserEdgeCases:
    def test_invalid_qty_string_blocks(self):
        with pytest.raises(PayloadParseError, match="positive integer"):
            parse_webhook_payload(pine_payload("B", quantity="abc"))

    def test_zero_qty_blocks(self):
        with pytest.raises(PayloadParseError, match="positive integer"):
            parse_webhook_payload(pine_payload("B", quantity="0"))

    def test_negative_qty_blocks(self):
        with pytest.raises(PayloadParseError, match="positive integer"):
            parse_webhook_payload(pine_payload("B", quantity="-1"))

    def test_missing_order_legs_blocks(self):
        payload = {
            "secret": TEST_WEBHOOK_SECRET,
            "alertType": "multi_leg_order",
            # No order_legs key
        }
        with pytest.raises(PayloadParseError):
            parse_webhook_payload(payload)

    def test_empty_order_legs_array_blocks(self):
        payload = {
            "secret": TEST_WEBHOOK_SECRET,
            "alertType": "multi_leg_order",
            "order_legs": [],
        }
        with pytest.raises(PayloadParseError, match="non-empty"):
            parse_webhook_payload(payload)

    def test_missing_secret_blocks(self):
        payload = pine_payload("B")
        del payload["secret"]
        with pytest.raises(PayloadParseError, match="Missing required fields"):
            parse_webhook_payload(payload)

    def test_unsupported_payload_format_raises(self):
        with pytest.raises(UnsupportedPayloadFormatError):
            parse_webhook_payload({"secret": TEST_WEBHOOK_SECRET, "foo": "bar"})

    def test_nova_requires_strategy_code(self):
        # Without strategy_code the NOVA format cannot be detected by the parser,
        # so UnsupportedPayloadFormatError is raised (the format detector uses
        # strategy_code as the primary NOVA discriminator).
        payload = nova_payload()
        del payload["strategy_code"]
        with pytest.raises((PayloadParseError, UnsupportedPayloadFormatError)):
            parse_webhook_payload(payload)

    def test_nova_requires_qty(self):
        payload = nova_payload()
        del payload["qty"]
        with pytest.raises(PayloadParseError, match="Missing required fields"):
            parse_webhook_payload(payload)


# ===========================================================================
# TASK 5: Security ID resolution — NIFTY underlying ID guard
# ===========================================================================

class TestSecurityIdResolutionNiftyGuard:
    def disable_fallbacks(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)

    def test_nifty_underlying_id_13_blocked_when_provided_in_signal(self, monkeypatch):
        """Security ID '13' (NIFTY underlying index) must never be accepted for option orders."""
        self.disable_fallbacks(monkeypatch)
        signal = make_signal(security_id=NIFTY_UNDERLYING_ID)
        result = resolve_security_id(signal)
        assert result.ok is False
        assert result.method == "NOT_FOUND"
        assert "13" in result.reason or "underlying" in result.reason.lower()

    def test_nifty_underlying_id_13_blocked_as_default_env(self, monkeypatch):
        """DEFAULT_SECURITY_ID='13' must be blocked even when ALLOW_DEFAULT_SECURITY_ID=true."""
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", NIFTY_UNDERLYING_ID)
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        signal = make_signal()
        result = resolve_security_id(signal)
        assert result.ok is False
        assert result.method == "NOT_FOUND"

    def test_valid_security_id_not_13_passes(self, monkeypatch):
        self.disable_fallbacks(monkeypatch)
        signal = make_signal(security_id="123456")
        result = resolve_security_id(signal)
        assert result.ok is True
        assert result.security_id == "123456"
        assert result.method == "PROVIDED_IN_SIGNAL"

    def test_resolution_order_scrip_master_before_default_env(self, monkeypatch, tmp_path):
        """
        SCRIP_MASTER must be tried before DEFAULT_ENV.
        Even if DEFAULT_SECURITY_ID is set, scrip master result wins.
        """
        # Create a minimal scrip master with a known row
        csv_content = (
            "SEM_SMST_SECURITY_ID,UNDERLYING_SYMBOL,SEM_INSTRUMENT_NAME,SEM_EXPIRY_DATE,"
            "SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_SEGMENT,EXCH_ID,SEM_TRADING_SYMBOL,SEM_LOT_UNITS\n"
            "999888,NIFTY,OPTIDX,2026-05-28,22500.000,CE,D,NSE,NIFTY28MAY2622500CE,75\n"
        )
        scrip_path = tmp_path / "test_scrip_master.csv"
        scrip_path.write_text(csv_content, encoding="utf-8")

        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "SHOULD_NOT_USE_THIS")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DHAN_SCRIP_MASTER_PATH", str(scrip_path))

        signal = make_signal()  # no security_id provided in signal
        result = resolve_security_id(signal)

        # Scrip master should win, not DEFAULT_ENV
        assert result.ok is True
        assert result.method == "SCRIP_MASTER"
        assert result.security_id == "999888"
        assert result.lot_size == 75  # lot_size extracted from CSV

    def test_default_env_used_only_when_scrip_master_not_available(self, monkeypatch, tmp_path):
        """DEFAULT_ENV is only used when scrip master has no match."""
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "777777")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", True)
        # Point to non-existent scrip master path
        monkeypatch.setattr(settings, "DHAN_SCRIP_MASTER_PATH", str(tmp_path / "nonexistent.csv"))

        signal = make_signal()
        result = resolve_security_id(signal)
        assert result.ok is True
        assert result.method == "DEFAULT_ENV"
        assert result.security_id == "777777"


# ===========================================================================
# TASK 6 / TASK 18: Quantity mode
# ===========================================================================

class TestQuantityMode:
    def test_absolute_qty_1_sends_quantity_1(self, monkeypatch):
        """QTY_MODE=ABSOLUTE: signal qty 1 → Dhan quantity 1."""
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        signal = make_signal(qty=1)
        payload, _ = _build_dhan_payload_and_resolution(signal, 1, "ENTRY")
        assert payload["quantity"] == 1

    def test_max_qty_no_longer_blocks_oversized_order(self, client):
        """Signal qty is absolute contracts and is not capped by backend max_qty_per_order."""
        response = client.post("/webhook/tradingview", json=pine_payload("B", quantity="2"))
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True


# ===========================================================================
# TASK 12 / TASK 18: Live order gating
# ===========================================================================

class TestLiveOrderGating:
    def test_mock_mode_never_calls_real_dhan_client(self, monkeypatch, client):
        """DHAN_MODE=MOCK — RealDhanClient.place_order must never be invoked."""
        real_called = {"called": False}

        def fake_real_place_order(self, *, client_id, access_token, payload):
            real_called["called"] = True
            raise RuntimeError("RealDhanClient called in MOCK mode!")

        monkeypatch.setattr(
            "app.services.dhan_client.RealDhanClient.place_order",
            fake_real_place_order,
        )
        payload = nova_payload("ENTRY", "BUY", "mock-gate-001")
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 200
        assert real_called["called"] is False

    def test_real_mode_with_live_disabled_never_calls_dhan(self, monkeypatch, client):
        """REAL mode + ENABLE_LIVE_ORDERS=false must never call place_order."""
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
        # In REAL mode local_mock_without_key_allowed() returns False, so the
        # vault reads from an encrypted file (missing in tests). Patch
        # get_webhook_secret directly so webhook validation still passes.
        # Webhook router binds get_webhook_secret at import time — must patch
        # its local binding, not the credential_vault module attribute.
        monkeypatch.setattr(
            "app.routers.webhook.get_webhook_secret",
            lambda: TEST_WEBHOOK_SECRET,
        )
        real_called = {"called": False}

        def fake_real_place_order(self, *, client_id, access_token, payload):
            real_called["called"] = True
            raise RuntimeError("RealDhanClient.place_order called when ENABLE_LIVE_ORDERS=false")

        monkeypatch.setattr(
            "app.services.dhan_client.RealDhanClient.place_order",
            fake_real_place_order,
        )
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")

        payload = nova_payload("ENTRY", "BUY", "real-disabled-001")
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert real_called["called"] is False
        assert body["accepted"] is False
        assert "ENABLE_LIVE_ORDERS=false" in body.get("message", "") or body["status"] == "BLOCKED"

    def test_expired_token_blocks_signal_before_dhan_preflight(self, monkeypatch, client):
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(
            "app.services.execution_router.dhan_token_age_metadata",
            lambda: {
                "token_saved_at": "2026-05-30T09:00:00+00:00",
                "token_age_minutes": 1500,
                "token_estimated_expiry_at": "2026-05-31T09:00:00+00:00",
                "token_expired": True,
                "token_warn": True,
                "token_max_age_hours": 24,
                "token_warn_age_hours": 23,
            },
        )

        class DhanMustNotBeCalled:
            def __init__(self):
                raise AssertionError("Dhan client must not be created when token is expired")

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", DhanMustNotBeCalled)

        result = route_signal(make_signal(security_id="CE123").model_copy(update={"signal_id": "expired-token-001"}))

        assert result["blocked"] is True
        assert result["success"] is False
        assert result["block_code"] == "DHAN_TOKEN_EXPIRED"
        assert "Dhan token expired" in result["reason"]
        assert result["token_age"]["token_expired"] is True

    def test_fresh_token_allows_live_signal_to_reach_dhan(self, monkeypatch, client):
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr(
            "app.services.execution_router.dhan_token_age_metadata",
            lambda: {
                "token_saved_at": "2026-05-31T09:00:00+00:00",
                "token_age_minutes": 30,
                "token_estimated_expiry_at": "2026-06-01T09:00:00+00:00",
                "token_expired": False,
                "token_warn": False,
                "token_max_age_hours": 24,
                "token_warn_age_hours": 23,
            },
        )
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        monkeypatch.setattr("app.services.execution_router.refresh_wallet_snapshot", lambda **kwargs: {})
        state_store.update_runtime_settings(
            option_exit_mode="SERVER",
            max_qty_per_order=1,
            allow_entry=True,
            allow_exit=True,
        )
        calls = {"placed": 0}

        class FreshTokenDhanClient:
            def get_positions_snapshot(self, *, client_id, access_token):
                return DhanListResult(success=True, message="positions", items=[])

            def get_order_book(self, *, client_id, access_token):
                return DhanListResult(success=True, message="orders", items=[])

            def place_order(self, *, client_id, access_token, payload):
                calls["placed"] += 1
                return DhanOrderResult(
                    success=True,
                    order_id="FRESH-TOKEN-ENTRY",
                    status="TRADED",
                    avg_price=100.0,
                    raw_response={"orderId": "FRESH-TOKEN-ENTRY", "orderStatus": "TRADED", "avgPrice": 100.0},
                )

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", FreshTokenDhanClient)

        result = route_signal(make_signal(security_id="CE123").model_copy(update={"signal_id": "fresh-token-001"}))

        assert result["status"] == "ORDER_PLACED"
        assert calls["placed"] == 1

    def test_missing_security_id_blocks_before_dhan(self, monkeypatch, client):
        """Missing securityId must block the order before calling Dhan."""
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        real_called = {"called": False}

        def fake_real_place_order(self, *, client_id, access_token, payload):
            real_called["called"] = True
            raise RuntimeError("Dhan called without securityId")

        monkeypatch.setattr(
            "app.services.dhan_client.RealDhanClient.place_order",
            fake_real_place_order,
        )
        # NOVA payload with no security_id and no scrip master to resolve from
        payload = nova_payload("ENTRY", "BUY", "no-secid-001")
        del payload["security_id"]
        response = client.post("/webhook/tradingview", json=payload)
        assert real_called["called"] is False
        body = response.json()
        assert body["accepted"] is False

    def test_real_entry_blocks_when_dhan_has_open_position(self, monkeypatch, client):
        """Before live ENTRY, broker positions are checked and active exposure blocks order placement."""
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr("app.routers.webhook.get_webhook_secret", lambda: TEST_WEBHOOK_SECRET)
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        real_called = {"called": False}

        class FakeRealDhanClient:
            def get_positions_snapshot(self, *, client_id, access_token):
                return DhanListResult(
                    success=True,
                    message="positions",
                    items=[{"tradingSymbol": "NIFTY 26 MAY 23900 CALL", "securityId": "72176", "netQty": 65}],
                )

            def get_order_book(self, *, client_id, access_token):
                return DhanListResult(success=True, message="orders", items=[])

            def place_order(self, *, client_id, access_token, payload):
                real_called["called"] = True
                raise RuntimeError("place_order must not be called when Dhan has open position")

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", FakeRealDhanClient)

        payload = nova_payload("ENTRY", "BUY", "dhan-open-position-001")
        response = client.post("/webhook/tradingview", json=payload)
        body = response.json()

        assert response.status_code == 200
        assert body["accepted"] is False
        assert "Dhan already has open position" in body.get("message", "")
        assert real_called["called"] is False

    def test_stale_local_position_is_cleared_when_dhan_is_flat_before_new_entry(self, monkeypatch, client):
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        monkeypatch.setattr("app.services.execution_router.refresh_wallet_snapshot", lambda **kwargs: {})
        state_store.update_runtime_settings(
            option_exit_mode="SERVER",
            max_qty_per_order=1,
            allow_entry=True,
            allow_exit=True,
        )
        state_store.set_open_position(
            {
                "has_open_position": True,
                "strategy_code": "TRADINGVIEW_NIFTY_V1",
                "symbol": "NIFTY",
                "instrument_type": "OPTIDX",
                "exchange_segment": "NSE_FNO",
                "security_id": "PE123",
                "trading_symbol": "NIFTY 2026-05-28 22500 PE",
                "option_side": "PE",
                "strike": 22500.0,
                "expiry": "2026-05-28",
                "qty": 1,
                "entry_order_id": "ENTRY-PE",
                "entry_price": 100.0,
                "exit_management": "SERVER",
                "opened_at": state_store.utc_now(),
            }
        )
        calls = {"placed": 0}

        class FlatDhanClient:
            def get_positions_snapshot(self, *, client_id, access_token):
                return DhanListResult(success=True, message="positions", items=[])

            def get_order_book(self, *, client_id, access_token):
                return DhanListResult(success=True, message="orders", items=[])

            def place_order(self, *, client_id, access_token, payload):
                calls["placed"] += 1
                return DhanOrderResult(
                    success=True,
                    order_id="ENTRY-CE",
                    status="TRADED",
                    avg_price=110.0,
                    raw_response={"orderId": "ENTRY-CE", "orderStatus": "TRADED", "avgPrice": 110.0},
                )

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", FlatDhanClient)

        ce_signal = make_signal(security_id="CE456").model_copy(
            update={
                "signal_id": "stale-local-to-ce-001",
                "trading_symbol": "NIFTY 2026-05-28 22500 CE",
                "option_side": "CE",
                "security_id": "CE456",
            }
        )
        result = route_entry_signal(ce_signal)

        assert result["status"] == "ORDER_PLACED"
        assert calls["placed"] == 1
        open_position = state_store.get_open_position()
        assert open_position["has_open_position"] is True
        assert open_position["option_side"] == "CE"
        assert open_position["entry_order_id"] == "ENTRY-CE"

    def test_partial_entry_fill_tracks_only_filled_quantity(self, monkeypatch, client):
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        monkeypatch.setattr("app.services.execution_router.refresh_wallet_snapshot", lambda **kwargs: {})
        state_store.update_runtime_settings(
            option_exit_mode="SERVER",
            max_qty_per_order=65,
            allow_entry=True,
            allow_exit=True,
        )

        class PartialEntryDhanClient:
            def get_positions_snapshot(self, *, client_id, access_token):
                return DhanListResult(success=True, message="positions", items=[])

            def get_order_book(self, *, client_id, access_token):
                return DhanListResult(success=True, message="orders", items=[])

            def place_order(self, *, client_id, access_token, payload):
                return DhanOrderResult(
                    success=True,
                    order_id="ENTRY-PARTIAL",
                    status="PENDING",
                    avg_price=None,
                    raw_response={"orderId": "ENTRY-PARTIAL", "orderStatus": "PENDING"},
                )

            def poll_order_status(self, *, client_id, access_token, order_id, max_polls=4, poll_delay=1.5):
                return DhanOrderStatusResult(
                    success=True,
                    order_id=order_id,
                    order_status="EXPIRED",
                    is_terminal=True,
                    is_filled=False,
                    avg_price=120.0,
                    raw_response={"orderId": order_id, "orderStatus": "EXPIRED", "filled_qty": 30},
                    filled_qty=30,
                    remaining_qty=35,
                )

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", PartialEntryDhanClient)

        result = route_entry_signal(make_signal(security_id="CE123", qty=65))

        assert result["status"] == "PARTIAL_ENTRY_FILLED"
        assert result["partial_fill"] is True
        assert result["filled_qty"] == 30
        open_position = state_store.get_open_position()
        assert open_position["qty"] == 30
        assert open_position["requested_qty"] == 65
        assert open_position["partial_fill"] is True

    def test_partial_exit_fill_reduces_local_open_quantity(self, monkeypatch, client):
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        monkeypatch.setattr("app.services.execution_router.refresh_wallet_snapshot", lambda **kwargs: {})
        monkeypatch.setattr("app.services.execution_router.get_engine_mode", lambda **kwargs: "live")
        state_store.update_runtime_settings(allow_entry=True, allow_exit=True)
        state_store.set_open_position(
            {
                "has_open_position": True,
                "strategy_code": "TRADINGVIEW_NIFTY_V1",
                "symbol": "NIFTY",
                "instrument_type": "OPTIDX",
                "exchange_segment": "NSE_FNO",
                "security_id": "CE123",
                "trading_symbol": "NIFTY 2026-05-28 22500 CE",
                "option_side": "CE",
                "strike": 22500.0,
                "expiry": "2026-05-28",
                "qty": 65,
                "entry_order_id": "ENTRY-CE",
                "entry_price": 100.0,
                "exit_management": "SERVER",
                "opened_at": state_store.utc_now(),
            }
        )

        class PartialExitDhanClient:
            def place_order(self, *, client_id, access_token, payload):
                return DhanOrderResult(
                    success=True,
                    order_id="EXIT-PARTIAL",
                    status="PENDING",
                    avg_price=None,
                    raw_response={"orderId": "EXIT-PARTIAL", "orderStatus": "PENDING"},
                )

            def poll_order_status(self, *, client_id, access_token, order_id, max_polls=4, poll_delay=1.5):
                return DhanOrderStatusResult(
                    success=True,
                    order_id=order_id,
                    order_status="EXPIRED",
                    is_terminal=True,
                    is_filled=False,
                    avg_price=95.0,
                    raw_response={"orderId": order_id, "orderStatus": "EXPIRED", "filled_qty": 30},
                    filled_qty=30,
                    remaining_qty=35,
                )

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", PartialExitDhanClient)

        exit_signal = make_signal(security_id="CE123", qty=65, action="EXIT", side="SELL")
        result = route_exit_signal(exit_signal)

        assert result["status"] == "PARTIAL_EXIT_FILLED"
        assert result["partial_fill"] is True
        assert result["filled_qty"] == 30
        open_position = state_store.get_open_position()
        assert open_position["has_open_position"] is True
        assert open_position["qty"] == 35
        assert open_position["partial_exit"]["filled_qty"] == 30

    @pytest.mark.parametrize(
        ("current_side", "incoming_side", "current_security_id", "incoming_security_id"),
        [
            ("CE", "PE", "CE123", "PE456"),
            ("PE", "CE", "PE123", "CE456"),
        ],
    )
    def test_opposite_option_entry_exits_then_enters_reversal(
        self,
        monkeypatch,
        client,
        current_side,
        incoming_side,
        current_security_id,
        incoming_security_id,
    ):
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        monkeypatch.setattr("app.services.execution_router.refresh_wallet_snapshot", lambda **kwargs: {})
        state_store.update_runtime_settings(
            option_exit_mode="DHAN_SUPER",
            option_disable_sl=False,
            option_sl_percent=30,
            option_tp_percent=60,
            max_qty_per_order=1,
            allow_entry=True,
            allow_exit=True,
        )
        state_store.set_open_position(
            {
                "has_open_position": True,
                "strategy_code": "TRADINGVIEW_NIFTY_V1",
                "symbol": "NIFTY",
                "instrument_type": "OPTIDX",
                "exchange_segment": "NSE_FNO",
                "security_id": current_security_id,
                "trading_symbol": f"NIFTY 2026-05-28 22500 {current_side}",
                "option_side": current_side,
                "strike": 22500.0,
                "expiry": "2026-05-28",
                "qty": 1,
                "entry_order_id": f"SUPER-{current_side}",
                "entry_price": 100.0,
                "exit_management": "DHAN_SUPER",
                "broker_sl_price": 70.0,
                "broker_tp_price": 160.0,
                "order_type": "MARKET",
                "product_type": "INTRADAY",
                "opened_at": state_store.utc_now(),
            }
        )
        calls: dict[str, list] = {"orders": [], "super_orders": [], "modified": [], "cancelled": []}

        class FakeRealDhanClient:
            def get_positions_snapshot(self, *, client_id, access_token):
                return DhanListResult(
                    success=True,
                    message="positions",
                    items=[
                        {
                            "tradingSymbol": f"NIFTY 2026-05-28 22500 {current_side}",
                            "securityId": current_security_id,
                            "netQty": 1,
                        }
                    ],
                )

            def get_order_book(self, *, client_id, access_token):
                return DhanListResult(success=True, message="orders", items=[])

            def place_order(self, *, client_id, access_token, payload):
                calls["orders"].append(payload)
                return DhanOrderResult(
                    success=True,
                    order_id=f"EXIT-{current_side}",
                    status="TRADED",
                    avg_price=95.0,
                    raw_response={"orderId": f"EXIT-{current_side}", "orderStatus": "TRADED", "avgPrice": 95.0},
                )

            def cancel_super_order_leg(self, *, client_id, access_token, order_id, leg_name):
                calls["cancelled"].append({"order_id": order_id, "leg_name": leg_name})
                return DhanOrderResult(
                    success=True,
                    order_id=order_id,
                    status="CANCELLED",
                    avg_price=None,
                    raw_response={"orderId": order_id, "orderStatus": "CANCELLED"},
                )

            def get_ltp(self, *, client_id, access_token, exchange_segment, security_id):
                return DhanLtpResult(success=True, message="ltp", ltp=100.0)

            def place_super_order(self, *, client_id, access_token, payload):
                calls["super_orders"].append(payload)
                return DhanOrderResult(
                    success=True,
                    order_id=f"SUPER-{incoming_side}",
                    status="TRADED",
                    avg_price=110.0,
                    raw_response={"orderId": f"SUPER-{incoming_side}", "orderStatus": "TRADED", "avgPrice": 110.0},
                )

            def modify_super_order(self, *, client_id, access_token, order_id, payload):
                calls["modified"].append(payload)
                return DhanOrderResult(
                    success=True,
                    order_id=order_id,
                    status="TRADED",
                    avg_price=None,
                    raw_response={"orderId": order_id, "orderStatus": "TRADED"},
                )

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", FakeRealDhanClient)

        incoming_signal = make_signal(security_id=incoming_security_id).model_copy(
            update={
                "signal_id": f"reversal-{incoming_side.lower()}-001",
                "trading_symbol": f"NIFTY 2026-05-28 22500 {incoming_side}",
                "option_side": incoming_side,
                "security_id": incoming_security_id,
            }
        )
        result = route_entry_signal(incoming_signal)

        assert result["status"] == "REVERSAL_ORDER_PLACED"
        assert calls["orders"][0]["transactionType"] == "SELL"
        assert calls["orders"][0]["securityId"] == current_security_id
        assert calls["super_orders"][0]["transactionType"] == "BUY"
        assert calls["super_orders"][0]["securityId"] == incoming_security_id
        assert calls["super_orders"][0]["orderType"] == "MARKET"
        assert {item["leg_name"] for item in calls["cancelled"]} == {"TARGET_LEG", "STOP_LOSS_LEG"}
        assert calls["modified"][0]["targetPrice"] == 176.0
        assert calls["modified"][1]["stopLossPrice"] == 77.0
        assert state_store.get_open_position()["option_side"] == incoming_side

    def test_real_entry_blocks_when_dhan_has_pending_order(self, monkeypatch, client):
        """Before live ENTRY, broker order book is checked and pending orders block order placement."""
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr("app.routers.webhook.get_webhook_secret", lambda: TEST_WEBHOOK_SECRET)
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        real_called = {"called": False}

        class FakeRealDhanClient:
            def get_positions_snapshot(self, *, client_id, access_token):
                return DhanListResult(success=True, message="positions", items=[])

            def get_order_book(self, *, client_id, access_token):
                return DhanListResult(
                    success=True,
                    message="orders",
                    items=[
                        {
                            "orderId": "112111182198",
                            "orderStatus": "PENDING",
                            "tradingSymbol": "NIFTY 26 MAY 23900 PUT",
                            "securityId": "72176",
                            "quantity": 65,
                        }
                    ],
                )

            def place_order(self, *, client_id, access_token, payload):
                real_called["called"] = True
                raise RuntimeError("place_order must not be called when Dhan has pending order")

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", FakeRealDhanClient)

        payload = nova_payload("ENTRY", "BUY", "dhan-pending-order-001")
        response = client.post("/webhook/tradingview", json=payload)
        body = response.json()

        assert response.status_code == 200
        assert body["accepted"] is False
        assert "Dhan already has open order" in body.get("message", "")
        assert real_called["called"] is False

    def test_exit_uses_tracked_open_position_contract(self, monkeypatch):
        """EXIT alerts must close NOVA's tracked position, not a stale alert contract."""
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        monkeypatch.setattr("app.services.execution_router.refresh_wallet_snapshot", lambda **kwargs: {})
        monkeypatch.setattr("app.services.execution_router.get_engine_mode", lambda **kwargs: "live")
        state_store.update_runtime_settings(allow_entry=True, allow_exit=True)
        state_store.set_open_position(
            {
                "has_open_position": True,
                "strategy_code": "TRADINGVIEW_NIFTY_V1",
                "symbol": "NIFTY",
                "instrument_type": "OPTIDX",
                "exchange_segment": "NSE_FNO",
                "security_id": "OPEN123",
                "trading_symbol": "NIFTY 2026-06-09 23300 CE",
                "option_side": "CE",
                "strike": 23300.0,
                "expiry": "2026-06-09",
                "qty": 65,
                "entry_order_id": "ENTRY-OPEN123",
                "entry_price": 100.0,
                "exit_management": "SERVER",
                "opened_at": state_store.utc_now(),
            }
        )
        calls: list[dict] = []

        class FakeRealDhanClient:
            def place_order(self, *, client_id, access_token, payload):
                calls.append(payload)
                return DhanOrderResult(
                    success=True,
                    order_id="EXIT-OPEN123",
                    status="TRADED",
                    avg_price=95.0,
                    raw_response={"orderId": "EXIT-OPEN123", "orderStatus": "TRADED", "avgPrice": 95.0},
                )

            def poll_order_status(self, *, client_id, access_token, order_id, max_polls=4, poll_delay=1.5):
                return DhanOrderStatusResult(
                    success=True,
                    order_id=order_id,
                    order_status="TRADED",
                    is_terminal=True,
                    is_filled=True,
                    avg_price=95.0,
                    raw_response={"orderId": order_id, "orderStatus": "TRADED", "avgPrice": 95.0},
                    filled_qty=65,
                    remaining_qty=0,
                )

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", FakeRealDhanClient)

        stale_exit_signal = make_signal(security_id="STALE999", action="EXIT", side="SELL").model_copy(
            update={
                "signal_id": "eod-exit-alert-001",
                "source": "tradingview_eod",
                "option_side": "PE",
                "strike": 23000.0,
                "expiry": "2026-06-16",
                "trading_symbol": "NIFTY 2026-06-16 23000 PE",
                "raw_payload": {"exit_reason": "EOD_FLATTEN"},
            }
        )
        result = route_exit_signal(stale_exit_signal)

        assert result["success"] is True
        assert calls[0]["transactionType"] == "SELL"
        assert calls[0]["securityId"] == "OPEN123"
        assert calls[0]["quantity"] == 65
        assert state_store.get_open_position()["has_open_position"] is False

    def test_emergency_stop_blocks_entry(self, client):
        """EMERGENCY_STOP=true must block all entry signals."""
        client.post("/api/control/emergency-stop")
        payload = nova_payload("ENTRY", "BUY", "es-entry-001")
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert "EMERGENCY_STOP" in body.get("message", "")

    def test_global_kill_switch_blocks_entry(self, client):
        """GLOBAL_KILL_SWITCH=true must block entries."""
        state_store.update_runtime_settings(global_kill_switch=True)
        payload = nova_payload("ENTRY", "BUY", "gks-entry-001")
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert "GLOBAL_KILL_SWITCH" in body.get("message", "")
        # Reset
        state_store.update_runtime_settings(global_kill_switch=False)


# ===========================================================================
# TASK 2: Dhan payload compliance
# ===========================================================================

class TestDhanPayloadCompliance:
    def test_final_payload_contains_all_required_fields(self, monkeypatch):
        """Final Dhan payload must contain all v2 required fields including triggerPrice."""
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        signal = make_signal()
        payload, resolution = _build_dhan_payload_and_resolution(signal, 1, "ENTRY")

        required = [
            "dhanClientId", "transactionType", "exchangeSegment", "productType",
            "orderType", "validity", "securityId", "quantity",
            "disclosedQuantity", "price", "triggerPrice", "afterMarketOrder",
        ]
        for field in required:
            assert field in payload, f"Missing required field: {field}"

    def test_trigger_price_is_zero_for_market_order(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        signal = make_signal()
        payload, _ = _build_dhan_payload_and_resolution(signal, 1, "ENTRY")
        assert payload["triggerPrice"] == 0

    def test_super_order_uses_market_entry_with_provisional_ltp_sltp(self):
        """Super Order enters at market, then target/SL are corrected from fill price after execution."""
        base_payload = {
            "dhanClientId": "1000000001",
            "correlationId": "super-order-test",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_FNO",
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "securityId": "57046",
            "quantity": 65,
            "price": 0,
        }

        payload, levels = _build_dhan_super_order_payload(
            base_payload=base_payload,
            reference_ltp=156.4,
            runtime={"option_sl_percent": 5, "option_tp_percent": 10},
        )

        assert payload["orderType"] == "MARKET"
        assert payload["price"] == 0
        assert payload["targetPrice"] == levels["target_price"]
        assert payload["stopLossPrice"] == levels["stop_loss_price"]
        assert payload["targetPrice"] > levels["reference_ltp"]
        assert payload["stopLossPrice"] < levels["reference_ltp"]
        assert levels["reference_ltp"] == 156.4

    def test_super_order_post_fill_updates_sltp_from_average_price(self, monkeypatch, client):
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr(
            "app.services.execution_router.get_dhan_credentials",
            lambda: credential_vault.DhanCredentials(client_id="1000000001", access_token="token"),
        )
        state_store.update_runtime_settings(
            option_exit_mode="DHAN_SUPER",
            option_disable_sl=False,
            option_sl_percent=30,
            option_tp_percent=60,
            allow_entry=True,
            allow_exit=True,
        )
        calls: dict[str, list[dict] | dict | None] = {"placed": None, "modified": []}

        class FakeRealDhanClient:
            def get_positions_snapshot(self, *, client_id, access_token):
                return DhanListResult(success=True, message="positions", items=[])

            def get_order_book(self, *, client_id, access_token):
                return DhanListResult(success=True, message="orders", items=[])

            def get_ltp(self, *, client_id, access_token, exchange_segment, security_id):
                return DhanLtpResult(success=True, message="ltp", ltp=100.0)

            def place_super_order(self, *, client_id, access_token, payload):
                calls["placed"] = payload
                return DhanOrderResult(
                    success=True,
                    order_id="SUPER1",
                    status="PENDING",
                    avg_price=None,
                    raw_response={"orderId": "SUPER1", "orderStatus": "PENDING"},
                )

            def poll_order_status(self, *, client_id, access_token, order_id, max_polls=4, poll_delay=1.5):
                return DhanOrderStatusResult(
                    success=True,
                    order_id=order_id,
                    order_status="TRADED",
                    is_terminal=True,
                    is_filled=True,
                    avg_price=120.0,
                    raw_response={"orderId": order_id, "orderStatus": "TRADED", "averageTradedPrice": 120.0},
                )

            def modify_super_order(self, *, client_id, access_token, order_id, payload):
                calls["modified"].append(payload)
                return DhanOrderResult(
                    success=True,
                    order_id=order_id,
                    status="TRADED",
                    avg_price=None,
                    raw_response={"orderId": order_id, "orderStatus": "TRADED"},
                )

        monkeypatch.setattr("app.services.execution_router.RealDhanClient", FakeRealDhanClient)

        result = _place_order(make_signal(security_id="57046"), 1, "ENTRY")

        assert result["success"] is True
        assert result["status"] == "TRADED"
        assert calls["placed"]["orderType"] == "MARKET"
        assert calls["placed"]["price"] == 0
        assert calls["placed"]["stopLossPrice"] == 70.0
        assert calls["placed"]["targetPrice"] == 160.0
        assert {"legName": "TARGET_LEG", "targetPrice": 192.0}.items() <= calls["modified"][0].items()
        assert {"legName": "STOP_LOSS_LEG", "stopLossPrice": 84.0}.items() <= calls["modified"][1].items()
        assert result["broker_sl_price"] == 84.0
        assert result["broker_tp_price"] == 192.0
        assert result["super_order_post_fill_update"]["success"] is True

    def test_after_market_order_is_false(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        signal = make_signal()
        payload, _ = _build_dhan_payload_and_resolution(signal, 1, "ENTRY")
        assert payload["afterMarketOrder"] is False

    def test_payload_has_correlation_id(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        signal = make_signal()
        payload, _ = _build_dhan_payload_and_resolution(signal, 1, "ENTRY")
        assert "correlationId" in payload
        assert payload["correlationId"]  # non-empty

    def test_raw_pine_fields_not_in_dhan_payload(self, monkeypatch):
        """The final Dhan payload must never contain raw Pine fields."""
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        signal = make_signal()
        payload, _ = _build_dhan_payload_and_resolution(signal, 1, "ENTRY")
        pine_only_fields = {"alertType", "order_legs", "strike_price", "expiry_date", "option_type"}
        for field in pine_only_fields:
            assert field not in payload, f"Raw Pine field found in Dhan payload: {field}"

    def test_transaction_type_buy_not_b(self, monkeypatch):
        """transactionType must be BUY/SELL, not B/S."""
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        signal = make_signal(side="BUY")
        payload, _ = _build_dhan_payload_and_resolution(signal, 1, "ENTRY")
        assert payload["transactionType"] == "BUY"

    def test_missing_trigger_price_warns_not_blocks_market_order(self):
        """
        Per Dhan v2 docs: triggerPrice is optional for MARKET/LIMIT orders (default 0).
        Missing triggerPrice must generate a warning but NOT block the payload (ok=True).
        """
        payload = {
            "dhanClientId": "1000000001",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_FNO",
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": "123456",
            "quantity": 1,
            "disclosedQuantity": 0,
            "price": 0,
            # triggerPrice intentionally absent
            "afterMarketOrder": False,
        }
        result = validate_dhan_payload(payload)
        assert result["ok"] is True, "Missing triggerPrice for MARKET order must NOT block — it has a safe default of 0"
        assert "triggerPrice" not in result["missing_fields"]
        assert any("triggerPrice" in w for w in result["warnings"]), "Should warn about missing triggerPrice"

    def test_missing_correlation_id_warns_not_blocks(self):
        """correlationId is optional per Dhan v2 docs — absence must warn but not block."""
        payload = {
            "dhanClientId": "1000000001",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_FNO",
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": "123456",
            "quantity": 1,
            "disclosedQuantity": 0,
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
            # correlationId intentionally absent
        }
        result = validate_dhan_payload(payload)
        assert result["ok"] is True, "Missing correlationId must NOT block — it is optional"
        assert "correlationId" not in result["missing_fields"]
        assert any("correlationId" in w for w in result["warnings"]), "Should warn about missing correlationId"

    def test_missing_disclosed_quantity_warns_not_blocks(self):
        """disclosedQuantity is optional per Dhan v2 docs — absence must warn but not block."""
        payload = {
            "dhanClientId": "1000000001",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_FNO",
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": "123456",
            "quantity": 1,
            # disclosedQuantity intentionally absent
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
        }
        result = validate_dhan_payload(payload)
        assert result["ok"] is True, "Missing disclosedQuantity must NOT block — Dhan default is 0"
        assert "disclosedQuantity" not in result["missing_fields"]
        assert any("disclosedQuantity" in w for w in result["warnings"]), "Should warn about missing disclosedQuantity"

    def test_missing_after_market_order_warns_not_blocks(self):
        """afterMarketOrder is optional per Dhan v2 docs — absence must warn but not block."""
        payload = {
            "dhanClientId": "1000000001",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_FNO",
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": "123456",
            "quantity": 1,
            "disclosedQuantity": 0,
            "price": 0,
            "triggerPrice": 0,
            # afterMarketOrder intentionally absent
        }
        result = validate_dhan_payload(payload)
        assert result["ok"] is True, "Missing afterMarketOrder must NOT block — Dhan default is false"
        assert "afterMarketOrder" not in result["missing_fields"]
        assert any("afterMarketOrder" in w for w in result["warnings"]), "Should warn about missing afterMarketOrder"

    def test_missing_core_required_field_blocks(self):
        """Missing core required field (e.g. price) must block the payload."""
        payload = {
            "dhanClientId": "1000000001",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_FNO",
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": "123456",
            "quantity": 1,
            # price intentionally absent — this IS a hard required field
        }
        result = validate_dhan_payload(payload)
        assert result["ok"] is False
        assert "price" in result["missing_fields"]

    def test_dhan_payload_validation_passes_complete_payload(self):
        """Complete Dhan v2 payload with all fields (including optional ones) must pass cleanly."""
        payload = {
            "dhanClientId": "1000000001",
            "correlationId": "test-correlation-01",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_FNO",
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": "123456",
            "quantity": 1,
            "disclosedQuantity": 0,
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
        }
        result = validate_dhan_payload(payload)
        assert result["ok"] is True
        assert not result["issues"]


# ===========================================================================
# TASK 11: Engine start readiness
# ===========================================================================

class TestEngineStartReadiness:
    def test_engine_start_fails_if_dhan_not_connected(self, tmp_path, monkeypatch):
        """Engine start must fail when Dhan is not connected."""
        state_dir = tmp_path / "runtime_state"
        log_dir = tmp_path / "runtime_logs"
        monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
        monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
        monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
        monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
        monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
        credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
        credential_vault._LOCAL_MEMORY_PAYLOAD.update({"version": 1, "dhan": None, "webhook_secret": None})
        log_files = {
            "webhook": log_dir / "webhook_events.jsonl",
            "order": log_dir / "order_events.jsonl",
            "audit": log_dir / "audit_events.jsonl",
            "error": log_dir / "errors.jsonl",
        }
        monkeypatch.setattr(state_store, "LOG_FILES", log_files)
        monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
        monkeypatch.setattr(settings, "APP_ENV", "local")
        monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
        monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")

        from app.main import app
        with TestClient(app) as c:
            # Set webhook secret but NOT Dhan credentials
            state_store.set_engine_mode("paper")
            c.post("/api/setup/webhook-secret", json={"webhook_secret": TEST_WEBHOOK_SECRET})
            c.post("/api/setup/risk", json={
                "max_qty_per_order": 1,
                "allow_entry": True, "allow_exit": True,
            })
            response = c.post("/api/engine/start", json={})
        assert response.status_code == 400
        body = response.json()
        detail = body.get("detail", body)
        readiness = detail.get("readiness", {})
        issues = readiness.get("issues", [])
        assert any("Dhan" in i or "credentials" in i.lower() for i in issues)

    def test_engine_start_fails_if_webhook_secret_not_set(self, tmp_path, monkeypatch):
        """Engine start must fail when webhook secret is not configured."""
        state_dir = tmp_path / "runtime_state"
        log_dir = tmp_path / "runtime_logs"
        monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
        monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
        monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
        monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
        monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
        credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
        credential_vault._LOCAL_MEMORY_PAYLOAD.update({"version": 1, "dhan": None, "webhook_secret": None})
        log_files = {
            "webhook": log_dir / "webhook_events.jsonl",
            "order": log_dir / "order_events.jsonl",
            "audit": log_dir / "audit_events.jsonl",
            "error": log_dir / "errors.jsonl",
        }
        monkeypatch.setattr(state_store, "LOG_FILES", log_files)
        monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
        monkeypatch.setattr(settings, "APP_ENV", "local")
        monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
        monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
        monkeypatch.setattr(
            setup_router,
            "validate_dhan_credentials",
            lambda *_args, **_kwargs: (True, "Dhan connected successfully.", None, {}),
        )
        from app.main import app
        with TestClient(app) as c:
            # Dhan connected (mock), but no webhook secret
            c.post("/api/setup/dhan/connect", json={"client_id": "1000000001", "access_token": "testtoken"})
            state_store.set_engine_mode("paper")
            c.post("/api/setup/risk", json={
                "max_qty_per_order": 1,
                "allow_entry": True, "allow_exit": True,
            })
            response = c.post("/api/engine/start", json={})
        assert response.status_code == 400
        detail = response.json().get("detail", response.json())
        readiness = detail.get("readiness", {})
        issues = readiness.get("issues", [])
        assert any("Webhook" in i or "secret" in i.lower() for i in issues)

    def test_engine_start_fails_if_token_expired(self, tmp_path, monkeypatch):
        """Engine start must fail when Dhan token is expired (age >= TOKEN_MAX_AGE_HOURS)."""
        state_dir = tmp_path / "runtime_state"
        log_dir = tmp_path / "runtime_logs"
        monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
        monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
        monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
        monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
        monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
        credential_vault._LOCAL_MEMORY_PAYLOAD.clear()
        # Simulate an old token: connected_at 25 hours ago
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        credential_vault._LOCAL_MEMORY_PAYLOAD.update({
            "version": 1,
            "dhan": {"client_id": "1000000001", "access_token": "old-token", "connected_at": old_time},
            "webhook_secret": TEST_WEBHOOK_SECRET,
        })
        log_files = {
            "webhook": log_dir / "webhook_events.jsonl",
            "order": log_dir / "order_events.jsonl",
            "audit": log_dir / "audit_events.jsonl",
            "error": log_dir / "errors.jsonl",
        }
        monkeypatch.setattr(state_store, "LOG_FILES", log_files)
        monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
        monkeypatch.setattr(settings, "APP_ENV", "local")
        monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
        monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
        monkeypatch.setattr(settings, "TOKEN_MAX_AGE_HOURS", 24)

        from app.main import app
        with TestClient(app) as c:
            c.post("/api/setup/mode", json={"engine_mode": "paper", "paper_starting_balance": 100000})
            c.post("/api/setup/risk", json={
                "max_qty_per_order": 1,
                "allow_entry": True, "allow_exit": True,
            })
            response = c.post("/api/engine/start", json={})
        assert response.status_code == 400
        detail = response.json().get("detail", response.json())
        msg = str(detail.get("message", "")).lower()
        assert "expired" in msg or "token" in msg

    def test_engine_start_passes_dry_run(self, client, monkeypatch):
        """Engine start must succeed in Paper mode without enabling live orders."""
        monkeypatch.setattr(
            setup_router,
            "validate_dhan_credentials",
            lambda *_args, **_kwargs: (True, "Dhan connected successfully.", None, {}),
        )
        # Connect credentials before selecting Paper so the legacy local setup
        # fixture does not make a network request.
        client.post("/api/setup/dhan/connect", json={"client_id": "MOCK123", "access_token": "mock-token"})
        client.post("/api/engine/stop")
        state_store.set_engine_mode("paper")
        response = client.post("/api/engine/start", json={})
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body.get("success") is True or body.get("engine_started") is True

    def test_engine_start_response_includes_checks(self, client):
        """Engine start response must include structured per-check list."""
        response = client.post("/api/engine/start", json={})
        if response.status_code == 200:
            body = response.json()
            assert "checks" in body
            check_names = [c["name"] for c in body["checks"]]
            assert "dhan_connected" in check_names
            assert "webhook_secret_set" in check_names
            assert "risk_limits" in check_names
            assert "token_age" in check_names


# ===========================================================================
# TASK 8: Setup security (no token in responses)
# ===========================================================================

class TestSetupSecurity:
    def test_dhan_connect_is_rate_limited_per_client_ip(self, client, monkeypatch):
        setup_router._DHAN_CONNECT_RATE_LIMIT.clear()
        validation_calls = {"count": 0}

        def fake_validate(client_id: str, access_token: str):
            validation_calls["count"] += 1
            return False, "Dhan connection failed: token invalid.", None, {"status_code": 401}

        monkeypatch.setattr(setup_router, "validate_dhan_credentials", fake_validate)
        headers = {"x-real-ip": "198.51.100.10"}

        try:
            for index in range(setup_router.DHAN_CONNECT_RATE_LIMIT_MAX_ATTEMPTS):
                response = client.post(
                    "/api/setup/dhan/connect",
                    json={"client_id": "1000000001", "access_token": f"bad-token-{index}"},
                    headers=headers,
                )
                assert response.status_code == 400

            blocked = client.post(
                "/api/setup/dhan/connect",
                json={"client_id": "1000000001", "access_token": "bad-token-over-limit"},
                headers=headers,
            )

            assert blocked.status_code == 429
            assert blocked.headers["retry-after"]
            assert "Too many Dhan connect attempts" in blocked.json()["detail"]
            assert validation_calls["count"] == setup_router.DHAN_CONNECT_RATE_LIMIT_MAX_ATTEMPTS
        finally:
            setup_router._DHAN_CONNECT_RATE_LIMIT.clear()

    def test_connect_dhan_rolls_back_saved_credentials_if_save_fails(self, client, monkeypatch):
        client.post(
            "/api/setup/dhan/connect",
            json={"client_id": "1000000001", "access_token": "old-token"},
            headers={"x-real-ip": "198.51.100.20"},
        )
        previous_wallet = state_store.set_wallet_snapshot(
            {
                **state_store.default_wallet_snapshot(),
                "success": True,
                "message": "Previous wallet snapshot.",
                "available_balance": 12345.0,
            }
        )

        def fake_save(client_id: str, access_token: str):
            credential_vault._LOCAL_MEMORY_PAYLOAD["dhan"] = {
                "client_id": client_id,
                "access_token": access_token,
                "connected_at": state_store.utc_now(),
            }
            raise credential_vault.VaultError("simulated vault write failure")

        monkeypatch.setattr(setup_router, "save_dhan_credentials", fake_save)

        response = client.post(
            "/api/setup/dhan/connect",
            json={"client_id": "1000000002", "access_token": "new-token"},
            headers={"x-real-ip": "198.51.100.20"},
        )

        creds = credential_vault.get_dhan_credentials()
        assert response.status_code == 400
        assert creds is not None
        assert creds.client_id == "1000000001"
        assert creds.access_token == "old-token"
        assert state_store.get_wallet_snapshot()["available_balance"] == previous_wallet["available_balance"]

    def test_connect_dhan_does_not_return_access_token(self, client):
        """POST /api/setup/dhan/connect must never return the access_token."""
        response = client.post(
            "/api/setup/dhan/connect",
            json={"client_id": "1000000001", "access_token": "secret-token-123"},
        )
        # In MOCK mode this should succeed
        body = response.json()
        body_str = str(body)
        assert "secret-token-123" not in body_str
        assert "access_token" not in body or body.get("access_token") != "secret-token-123"

    def test_setup_status_returns_only_masked_saved_credentials(self, client):
        client.post(
            "/api/setup/dhan/connect",
            json={"client_id": "1000000001", "access_token": "secret-token-123"},
        )
        client.post("/api/setup/webhook-secret", json={"webhook_secret": "webhook-secret-123"})

        response = client.get("/api/setup/status")
        body = response.json()
        body_str = str(body)

        assert body["dhan_client_id_masked"] == "******0001"
        assert body["access_token_present"] is True
        assert body["access_token_masked"]
        assert body["webhook_secret_set"] is True
        assert body["webhook_secret_masked"]
        assert "secret-token-123" not in body_str
        assert "webhook-secret-123" not in body_str

    def test_connect_dhan_can_update_only_access_token_when_client_is_saved(self, client):
        client.post(
            "/api/setup/dhan/connect",
            json={"client_id": "1000000001", "access_token": "old-token"},
        )

        response = client.post("/api/setup/dhan/connect", json={"access_token": "new-token"})
        creds = credential_vault.get_dhan_credentials()

        assert response.status_code == 200
        assert creds is not None
        assert creds.client_id == "1000000001"
        assert creds.access_token == "new-token"

    def test_patch_risk_updates_selected_values_without_resetting_other_settings(self, client):
        state_store.update_runtime_settings(
            option_ltp_source="REST",
            option_exit_mode="SERVER",
            option_ws_stale_seconds=9,
        )

        response = client.patch("/api/setup/risk", json={"option_sl_percent": 30, "option_tp_percent": 60})
        saved = response.json()["settings"]

        assert response.status_code == 200
        assert saved["option_sl_percent"] == 30
        assert saved["option_tp_percent"] == 60
        assert saved["option_ltp_source"] == "REST"
        assert saved["option_exit_mode"] == "SERVER"
        assert saved["option_ws_stale_seconds"] == 9

    def test_risk_settings_reject_extreme_sl_and_tp_percentages(self, client):
        sl_response = client.patch("/api/setup/risk", json={"option_sl_percent": 80})
        tp_response = client.patch("/api/setup/risk", json={"option_tp_percent": 500})

        assert sl_response.status_code == 422
        assert tp_response.status_code == 422
        assert "less than 80" in str(sl_response.json())
        assert "less than 500" in str(tp_response.json())

    def test_real_mode_risk_settings_do_not_cap_current_nifty_lot_size(self, client, monkeypatch):
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(setup_router, "_current_nifty_lot_size_from_scrip_master", lambda: 65)

        small = client.patch("/api/setup/risk", json={"max_qty_per_order": 1})
        valid = client.patch("/api/setup/risk", json={"max_qty_per_order": 65})

        assert small.status_code == 200
        assert small.json()["settings"]["max_qty_per_order"] == 1
        assert valid.status_code == 200
        assert valid.json()["settings"]["max_qty_per_order"] == 65

    def test_fresh_start_requires_typed_confirmation(self, client):
        missing = client.post("/api/control/fresh-start", json={})
        wrong = client.post("/api/control/fresh-start", json={"confirmation": "fresh start"})
        valid = client.post("/api/control/fresh-start", json={"confirmation": "FRESH START"})

        assert missing.status_code == 422
        assert wrong.status_code == 400
        assert "Type FRESH START" in wrong.json()["detail"]
        assert valid.status_code == 200
        assert valid.json()["ok"] is True

    def test_setup_status_does_not_return_access_token(self, client):
        """GET /api/setup/status must never expose the access token."""
        response = client.get("/api/setup/status")
        body = response.json()
        # access_token_present should be a bool, not t
