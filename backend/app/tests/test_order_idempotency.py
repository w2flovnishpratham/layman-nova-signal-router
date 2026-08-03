# ruff: noqa: F811
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.engine import session_scope
from app.schemas.signal import NormalizedSignal
from app.services.credential_vault import DhanCredentials
from app.services.dhan_client import DhanListResult, DhanOrderResult
from app.services.execution_context import bind_execution_context
from app.services.user_context import current_user_from_model
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


@dataclass(frozen=True)
class _Resolution:
    security_id: str
    trading_symbol: str
    ok: bool = True
    reason: str | None = None
    lot_size: int | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "security_id": self.security_id,
            "trading_symbol": self.trading_symbol,
            "reason": self.reason,
            "lot_size": self.lot_size,
        }


class _FakeLiveDhanClient:
    def __init__(self, calls: dict[str, int], *, order_id_prefix: str = "LIVE-IDEMP") -> None:
        self.calls = calls
        self.order_id_prefix = order_id_prefix

    def get_positions_snapshot(self, *, client_id, access_token):
        return DhanListResult(success=True, message="positions", items=[])

    def get_order_book(self, *, client_id, access_token):
        return DhanListResult(success=True, message="orders", items=[])

    def place_order(self, *, client_id, access_token, payload):
        self.calls["place_order"] = self.calls.get("place_order", 0) + 1
        order_id = f"{self.order_id_prefix}-{self.calls['place_order']}"
        return DhanOrderResult(
            success=True,
            order_id=order_id,
            status="TRADED",
            avg_price=100.0,
            raw_response={"orderId": order_id, "orderStatus": "TRADED", "avgPrice": 100.0},
        )


def _signal(
    signal_id: str,
    *,
    option_side: str = "CE",
    security_id: str = "CE123",
    source: str = "manual_panel",
    manual_key: str | None = "manual-key-1",
) -> NormalizedSignal:
    raw_payload: dict[str, Any] = {}
    if source == "manual_panel":
        raw_payload["manual_order"] = True
        if manual_key is not None:
            raw_payload["idempotency_key"] = manual_key
    return NormalizedSignal(
        payload_format="NOVA",
        secret="",
        signal_id=signal_id,
        strategy_code="MANUAL" if source == "manual_panel" else "TRADINGVIEW_NIFTY_V1",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id=security_id,
        trading_symbol=f"NIFTY {option_side}",
        option_side=option_side,
        strike=22500,
        expiry="2026-06-30",
        qty=65,
        order_type="MARKET",
        product_type="INTRADAY",
        source=source,
        raw_payload=raw_payload,
    )


def _prepare_live_order(monkeypatch, tmp_path, calls: dict[str, int], *, app_env: str = "local") -> None:
    from app.config import settings
    from app.services import execution_router, state_store

    state_dir = tmp_path / "runtime_state"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    state_store.init_runtime_files()
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)
    state_store.update_runtime_settings(
        option_exit_mode="SERVER",
        max_qty_per_order=65,
        allow_entry=True,
        allow_exit=True,
    )

    monkeypatch.setattr(settings, "APP_ENV", app_env, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "MARKET_CLOSED_DEBUG", False, raising=False)
    monkeypatch.setattr(settings, "FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED", False, raising=False)
    monkeypatch.setattr(settings, "AUTO_RESOLVE_SECURITY_ID", False, raising=False)
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False, raising=False)
    monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "", raising=False)
    monkeypatch.setattr(execution_router, "_market_is_open", lambda: True)
    monkeypatch.setattr(
        execution_router,
        "get_dhan_credentials",
        lambda: DhanCredentials("1000000001", "secret-live-token"),
    )
    monkeypatch.setattr(
        execution_router,
        "dhan_token_age_metadata",
        lambda: {
            "token_age_minutes": 1,
            "token_warn": False,
            "token_expired": False,
        },
    )
    monkeypatch.setattr(execution_router, "refresh_wallet_snapshot", lambda **_kwargs: {})
    monkeypatch.setattr(
        execution_router,
        "resolve_security_id",
        lambda signal: _Resolution(
            security_id=str(signal.security_id),
            trading_symbol=str(signal.trading_symbol or signal.security_id),
        ),
    )
    fake_client = _FakeLiveDhanClient(calls)
    monkeypatch.setattr(execution_router, "_live_broker_client", lambda: fake_client)


def _bound_user(user_model):
    return bind_execution_context(
        current_user_from_model(user_model),
        proxy_url="http://proxy-user:proxy-secret@13.203.58.220:3001",
        egress_ip="13.203.58.220",
        expected_egress_ip="13.203.58.220",
        observed_egress_ip="13.203.58.220",
        egress_verified=True,
    )


def _grant_live_entitlement(user_model) -> None:
    from app.db import models

    now = models.utcnow()
    with session_scope() as db:
        db.add(
            models.UserEntitlement(
                user_id=user_model.id,
                plan_code="nova_live",
                status="active",
                source="payment_provider",
                starts_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=30),
                live_orders_enabled=True,
                static_ip_enabled=False,
                strategy_access_enabled=False,
                metadata_json={"test": "manual_order"},
                created_at=now,
                updated_at=now,
            )
        )


def test_manual_live_same_idempotency_key_same_payload_does_not_submit_twice(mu_db, monkeypatch, tmp_path):
    from app.services import execution_router

    calls: dict[str, int] = {}
    _prepare_live_order(monkeypatch, tmp_path, calls)
    user = make_user("manual-idempotent@example.com")

    with _bound_user(user):
        first = execution_router._place_order(_signal("manual-random-1"), 65, "ENTRY", runtime={"option_exit_mode": "SERVER"})
        second = execution_router._place_order(_signal("manual-random-2"), 65, "ENTRY", runtime={"option_exit_mode": "SERVER"})

    assert first["success"] is True
    assert second["success"] is True
    assert second["idempotent_replay"] is True
    assert second["order_id"] == first["order_id"]
    assert calls["place_order"] == 1


def test_manual_live_route_signal_retry_replays_after_open_position_is_set(mu_db, monkeypatch, tmp_path):
    from app.services import execution_router

    calls: dict[str, int] = {}
    _prepare_live_order(monkeypatch, tmp_path, calls)
    user = make_user("manual-route-retry@example.com")

    with _bound_user(user):
        first = execution_router.route_signal(_signal("manual-route-1"))
        second = execution_router.route_signal(_signal("manual-route-2"))

    assert first["status"] == "ORDER_PLACED"
    assert second["idempotent_replay"] is True
    assert second["order_id"] == first["order_id"]
    assert calls["place_order"] == 1


def test_manual_live_same_key_different_payload_is_rejected_before_dhan(mu_db, monkeypatch, tmp_path):
    from app.services import execution_router

    calls: dict[str, int] = {}
    _prepare_live_order(monkeypatch, tmp_path, calls)
    user = make_user("manual-conflict@example.com")

    with _bound_user(user):
        first = execution_router._place_order(_signal("manual-conflict-1"), 65, "ENTRY", runtime={"option_exit_mode": "SERVER"})
        conflict = execution_router._place_order(
            _signal("manual-conflict-2", option_side="PE", security_id="PE456"),
            65,
            "ENTRY",
            runtime={"option_exit_mode": "SERVER"},
        )

    assert first["success"] is True
    assert conflict["blocked"] is True
    assert conflict["block_code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert calls["place_order"] == 1
    serialized = str(conflict)
    assert "secret-live-token" not in serialized
    assert "proxy-secret" not in serialized


def test_production_manual_live_missing_idempotency_key_fails_closed(mu_db, monkeypatch, tmp_path):
    from app.services import execution_router

    calls: dict[str, int] = {}
    _prepare_live_order(monkeypatch, tmp_path, calls, app_env="production")
    user = make_user("manual-missing-key@example.com")

    with _bound_user(user):
        result = execution_router._place_order(
            _signal("manual-missing-key", manual_key=None),
            65,
            "ENTRY",
            runtime={"option_exit_mode": "SERVER"},
        )

    assert result["blocked"] is True
    assert result["block_code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert calls.get("place_order", 0) == 0


def test_manual_paper_route_remains_compatible_without_idempotency_key(monkeypatch, tmp_path):
    from app.config import settings
    from app.routers import orders as orders_router
    from app.services import state_store

    state_dir = tmp_path / "runtime_state"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    monkeypatch.setattr(settings, "APP_ENV", "local", raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False, raising=False)
    routed: list[str] = []
    monkeypatch.setattr(
        orders_router,
        "route_signal",
        lambda signal, **_kwargs: routed.append(signal.signal_id)
        or {"success": True, "status": "PAPER_ORDER_PLACED"},
    )

    app = FastAPI()
    app.include_router(orders_router.router, prefix="/api")
    response = TestClient(app).post(
        "/api/orders/manual-entry",
        json={"side": "CE", "lots": 1, "securityId": "CE123", "tradingSymbol": "NIFTY CE"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["operationState"] == "RECONCILIATION_REQUIRED"
    assert len(routed) == 1


def test_production_manual_route_missing_idempotency_key_blocks_before_routing(monkeypatch, tmp_path):
    from app.config import settings
    from app.routers import orders as orders_router
    from app.services import state_store

    state_dir = tmp_path / "runtime_state"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    state_store.init_runtime_files()
    state_store.set_engine_mode("live")
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(
        orders_router,
        "route_signal",
        lambda _signal: (_ for _ in ()).throw(AssertionError("missing key must not route")),
    )

    app = FastAPI()
    app.include_router(orders_router.router, prefix="/api")
    response = TestClient(app).post(
        "/api/orders/manual-entry",
        json={"side": "CE", "lots": 1, "securityId": "CE123", "tradingSymbol": "NIFTY CE"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False
    assert body["executionResult"]["block_code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_manual_live_route_requires_live_entitlement_before_routing(monkeypatch, tmp_path):
    from app.config import settings
    from app.routers import orders as orders_router
    from app.services import state_store

    state_dir = tmp_path / "runtime_state"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    state_store.init_runtime_files()
    state_store.set_engine_mode("live")
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(
        orders_router,
        "route_signal",
        lambda _signal: (_ for _ in ()).throw(AssertionError("entitlement must block before route_signal")),
    )

    app = FastAPI()
    app.include_router(orders_router.router, prefix="/api")
    response = TestClient(app).post(
        "/api/orders/manual-entry",
        headers={"Idempotency-Key": "manual-live-no-entitlement"},
        json={"side": "CE", "lots": 1, "securityId": "CE123", "tradingSymbol": "NIFTY CE"},
    )

    assert response.status_code == 403
    body = response.json()
    assert body["ok"] is False
    assert body["executionResult"]["block_code"] == "LIVE_ENTITLEMENT_REQUIRED"
    assert "Live entitlement is required." in body["executionResult"]["reason"]


def test_manual_live_route_allows_entitled_server_user_to_reach_router(mu_db, monkeypatch, tmp_path):
    from starlette.requests import Request

    from app.config import settings
    from app.routers import orders as orders_router
    from app.services import state_store
    from app.services.execution_context import bind_execution_context
    from app.services.user_context import current_user_from_model

    state_dir = tmp_path / "runtime_state"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    state_store.init_runtime_files()
    state_store.set_engine_mode("live")
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    user = make_user("manual-live-entitled-route@example.com")
    _grant_live_entitlement(user)
    routed: list[str] = []
    monkeypatch.setattr(
        orders_router,
        "route_signal",
        lambda signal, **_kwargs: routed.append(signal.signal_id)
        or {"success": True, "status": "ORDER_PLACED"},
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/orders/manual-entry",
            "headers": [(b"idempotency-key", b"manual-live-entitled")],
        }
    )
    body = orders_router.ManualEntryRequest(
        side="CE",
        lots=1,
        securityId="CE123",
        tradingSymbol="NIFTY CE",
    )

    with bind_execution_context(current_user_from_model(user)):
        response = orders_router.manual_entry(request, body)

    assert response["ok"] is False
    assert response["operationState"] == "RECONCILIATION_REQUIRED"
    assert routed


def test_next_manual_entry_uses_latest_confirmed_revision(mu_db, monkeypatch):
    from app.routers import orders as orders_router

    owner = current_user_from_model(make_user("manual-latest-revision@example.com"))
    monkeypatch.setattr(
        orders_router,
        "current_execution_user",
        lambda: owner,
    )
    monkeypatch.setattr(
        orders_router,
        "get_engine_mode",
        lambda **_kwargs: "paper",
    )
    monkeypatch.setattr(
        orders_router,
        "get_runtime_settings",
        lambda: {
            "max_trades_per_day": 6,
            "max_daily_loss": 25_000,
            "stop_loss_percent": 10,
        },
    )
    monkeypatch.setattr(
        orders_router.setup_configuration,
        "selected_configuration",
        lambda user_id, mode: {
            "id": "11111111-1111-4111-8111-111111111111",
            "revision": 8,
            "mode": mode,
            "risk": {
                "max_trades_per_day": 3,
                "max_daily_loss": 10_000,
                "entry_cutoff_ist": "14:45",
            },
            "configuration": {
                "stop_loss_percent": 7,
                "take_profit_percent": 14,
            },
        },
    )

    runtime = orders_router._selected_manual_entry_runtime()

    assert runtime["max_trades_per_day"] == 3
    assert runtime["max_daily_loss"] == 10_000
    assert runtime["stop_loss_percent"] == 7
    assert runtime["configuration_revision"] == 8


def test_tradingview_live_order_uses_signal_identity_for_idempotency(mu_db, monkeypatch, tmp_path):
    from app.services import execution_router

    calls: dict[str, int] = {}
    _prepare_live_order(monkeypatch, tmp_path, calls)
    user = make_user("tradingview-idempotent@example.com")
    signal = _signal(
        "tv-live-signal-1",
        source="tradingview",
        manual_key=None,
    )

    with _bound_user(user):
        first = execution_router._place_order(signal, 65, "ENTRY", runtime={"option_exit_mode": "SERVER"})
        second = execution_router._place_order(signal, 65, "ENTRY", runtime={"option_exit_mode": "SERVER"})

    assert first["success"] is True
    assert second["idempotent_replay"] is True
    assert calls["place_order"] == 1


def test_live_order_in_progress_retry_fails_closed(mu_db):
    from app.services import order_idempotency

    user = make_user("intent-in-progress@example.com")
    payload_hash = order_idempotency.stable_payload_hash({"security_id": "CE123", "qty": 65})
    claim = order_idempotency.claim_live_order_intent(
        user_id=user.id,
        scope="manual_order",
        idempotency_key="in-progress-key",
        payload_hash=payload_hash,
        signal_id="in-progress-signal",
        action="ENTRY",
    )
    order_idempotency.mark_order_intent_submitted(claim.intent_id or "")

    retry = order_idempotency.claim_live_order_intent(
        user_id=user.id,
        scope="manual_order",
        idempotency_key="in-progress-key",
        payload_hash=payload_hash,
        signal_id="in-progress-signal-2",
        action="ENTRY",
    )

    assert retry.status == "in_progress"
    assert "in progress" in (retry.message or "")


def test_live_order_idempotency_is_scoped_by_user(mu_db, monkeypatch, tmp_path):
    from app.services import execution_router

    calls: dict[str, int] = {}
    _prepare_live_order(monkeypatch, tmp_path, calls)
    alice = make_user("alice-idempotency@example.com")
    bob = make_user("bob-idempotency@example.com")

    with _bound_user(alice):
        alice_result = execution_router._place_order(_signal("same-key-alice"), 65, "ENTRY", runtime={"option_exit_mode": "SERVER"})
    with _bound_user(bob):
        bob_result = execution_router._place_order(_signal("same-key-bob"), 65, "ENTRY", runtime={"option_exit_mode": "SERVER"})

    assert alice_result["success"] is True
    assert bob_result["success"] is True
    assert calls["place_order"] == 2
    with session_scope() as db:
        from app.db import models

        assert db.query(models.LiveOrderIntent).count() == 2
