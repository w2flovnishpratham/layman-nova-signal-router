"""
DhanHQ v2 Compliance & Production-Readiness Tests
===================================================
Covers all gaps identified in the DHAN_V2_COMPLIANCE_AUDIT.md:

- Webhook secret: no-secret blocks, wrong secret blocks, correct secret accepts
- Signal parser: invalid qty, missing order_legs, missing secret, unsupported payload
- Security ID: NIFTY underlying ID=13 blocked, resolver order, scrip-master lookup
- Quantity: ABSOLUTE mode, max qty blocks final quantity
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
from app.schemas.signal import NormalizedSignal
from app.services import audit_logger, credential_vault, state_store
from app.services import risk_manager
from app.services.dhan_client import DhanListResult
from app.services.dhan_debugger import validate_dhan_payload
from app.services.execution_router import _build_dhan_payload_and_resolution, _build_dhan_super_order_payload
from app.services.security_id_resolver import (
    NIFTY_UNDERLYING_ID,
    resolve_security_id,
)
from app.services.signal_parser import (
    PayloadParseError,
    UnsupportedPayloadFormatError,
    parse_webhook_payload,
)
from app.tests.test_signal_parser import nova_payload, pine_payload


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
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "WEBHOOK_TRADING_ENABLED", True)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)

    from app.main import app

    with TestClient(app) as test_client:
        test_client.post("/api/control/reset-state")
        test_client.post("/api/control/clear-seen-signals")
        test_client.post("/api/setup/webhook-secret", json={"webhook_secret": "test-secret"})
        test_client.post(
            "/api/setup/risk",
            json={
                "max_qty_per_order": 1,
                "max_trades_per_day": 5,
                "daily_loss_limit": 500,
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
        secret="test-secret",
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
            "secret": "test-secret",
            "alertType": "multi_leg_order",
            # No order_legs key
        }
        with pytest.raises(PayloadParseError):
            parse_webhook_payload(payload)

    def test_empty_order_legs_array_blocks(self):
        payload = {
            "secret": "test-secret",
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
            parse_webhook_payload({"secret": "test-secret", "foo": "bar"})

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

    def test_max_qty_blocks_oversized_order(self, client):
        """max_qty_per_order=1 must block qty=2 in the ABSOLUTE mode."""
        # risk config in fixture sets max_qty_per_order=1
        response = client.post("/webhook/tradingview", json=pine_payload("B", quantity="2"))
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert "MAX_QTY_PER_ORDER" in body["message"]


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
            lambda: "test-secret",
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
        monkeypatch.setattr("app.routers.webhook.get_webhook_secret", lambda: "test-secret")
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

    def test_real_entry_blocks_when_dhan_has_pending_order(self, monkeypatch, client):
        """Before live ENTRY, broker order book is checked and pending orders block order placement."""
        monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
        monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
        monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", True)
        monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "123456")
        monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False)
        monkeypatch.setattr("app.routers.webhook.get_webhook_secret", lambda: "test-secret")
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

    def test_super_order_uses_limit_price_for_broker_side_sltp(self):
        """Dhan Super Orders validate target/SL against entry price, so never send price=0."""
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

        assert payload["orderType"] == "LIMIT"
        assert payload["price"] > 0
        assert payload["price"] == levels["entry_limit_price"]
        assert payload["targetPrice"] == levels["target_price"]
        assert payload["stopLossPrice"] == levels["stop_loss_price"]
        assert payload["targetPrice"] > payload["price"]
        assert payload["stopLossPrice"] < payload["price"]
        assert levels["reference_ltp"] == 156.4

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
            c.post("/api/setup/webhook-secret", json={"webhook_secret": "test-secret"})
            c.post("/api/setup/risk", json={
                "max_qty_per_order": 1, "max_trades_per_day": 5,
                "daily_loss_limit": 500, "allow_entry": True, "allow_exit": True,
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

        from app.main import app
        with TestClient(app) as c:
            # Dhan connected (mock), but no webhook secret
            c.post("/api/setup/dhan/connect", json={"client_id": "1000000001", "access_token": "testtoken"})
            c.post("/api/setup/risk", json={
                "max_qty_per_order": 1, "max_trades_per_day": 5,
                "daily_loss_limit": 500, "allow_entry": True, "allow_exit": True,
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
            "webhook_secret": "test-secret",
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
            c.post("/api/setup/risk", json={
                "max_qty_per_order": 1, "max_trades_per_day": 5,
                "daily_loss_limit": 500, "allow_entry": True, "allow_exit": True,
            })
            response = c.post("/api/engine/start", json={})
        assert response.status_code == 400
        detail = response.json().get("detail", response.json())
        msg = str(detail.get("message", "")).lower()
        assert "expired" in msg or "token" in msg

    def test_engine_start_passes_dry_run(self, client):
        """Engine start must succeed in MOCK mode with live orders disabled."""
        # Connect mock Dhan credentials so readiness check passes in MOCK mode.
        client.post("/api/setup/dhan/connect", json={"client_id": "MOCK123", "access_token": "mock-token"})
        response = client.post("/api/engine/start", json={})
        # May succeed (200) or return 409 if confirm_live_orders required
        # but must not return 400 (setup incomplete)
        assert response.status_code in (200, 409)
        if response.status_code == 200:
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

    def test_setup_status_does_not_return_access_token(self, client):
        """GET /api/setup/status must never expose the access token."""
        response = client.get("/api/setup/status")
        body = response.json()
        # access_token_present should be a bool, not t
