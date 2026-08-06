# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Pytest discovers the imported fixture by name; parameters reuse it intentionally.
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _current_user(model):
    from app.services.user_context import current_user_from_model

    return current_user_from_model(model)


def _grant_entitlement(
    user,
    *,
    live: bool = False,
    static_ip: bool = False,
    strategy: bool = False,
    status: str = "active",
) -> None:
    from app.db import models
    from app.db.engine import session_scope

    now = models.utcnow()
    with session_scope() as db:
        db.add(
            models.UserEntitlement(
                user_id=user.id,
                plan_code="nova_test",
                status=status,
                source="payment_provider",
                starts_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=30),
                live_orders_enabled=live,
                static_ip_enabled=static_ip,
                strategy_access_enabled=strategy,
                metadata_json={"test": "strategy_fanout"},
                created_at=now,
                updated_at=now,
            )
        )


def _signal(secret: str, signal_id: str = "fanout-1"):
    from app.config import DEFAULT_STRATEGY_CODE
    from app.schemas.signal import NormalizedSignal

    # A pre-resolved contract (security_id set) so the worker's per-subscriber
    # ATM-resolution enricher (wired for placeholder broadcast signals -- see
    # build_broadcast_signal) leaves this signal untouched. These tests exist
    # to verify fan-out/queueing/qty mechanics, not contract resolution.
    payload = {
        "secret": secret,
        "signal_id": signal_id,
        "strategy_code": DEFAULT_STRATEGY_CODE,
        "action": "ENTRY",
        "side": "BUY",
        "symbol": "NIFTY",
        "qty": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
    }
    return NormalizedSignal(
        payload_format="NOVA",
        secret=secret,
        signal_id=signal_id,
        strategy_code=DEFAULT_STRATEGY_CODE,
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        security_id="TEST-SECURITY-ID",
        strike=25000.0,
        expiry="2026-12-31",
        option_side="CE",
        qty=1,
        order_type="MARKET",
        product_type="INTRADAY",
        raw_payload=payload,
    )


def _bind_paper_configuration(user, *, lots: int) -> None:
    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        subscription = db.scalar(
            select(models.StrategySubscription)
            .where(models.StrategySubscription.user_id == user.id)
            .order_by(models.StrategySubscription.created_at.desc())
        )
        assert subscription is not None
        instance = db.scalar(
            select(models.StrategyInstance)
            .where(
                models.StrategyInstance.user_id == user.id,
                models.StrategyInstance.legacy_subscription_id == subscription.id,
            )
            .order_by(models.StrategyInstance.created_at.desc())
        )
        if instance is None:
            strategy = models.StrategyCatalog(
                code=f"fanout-{user.id.hex[:10]}",
                display_name="Fanout Test",
                owner_type="personal",
                owner_user_id=user.id,
                status="active",
            )
            db.add(strategy)
            db.flush()
            version = models.StrategyVersion(
                strategy_id=strategy.id,
                version="1.0",
                payload_spec_version="nova.v1",
                source_journey="personal_tradingview",
                status="approved",
                execution_kind="external_webhook",
            )
            db.add(version)
            db.flush()
            instance = models.StrategyInstance(
                user_id=user.id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                source_journey="NOVA_SHARED",
                label="Fanout Test",
                status="active",
                execution_mode=subscription.execution_mode,
                current_lots=lots,
                legacy_subscription_id=subscription.id,
            )
            db.add(instance)
            db.flush()
        revision = models.StrategyConfigurationRevision(
            user_id=user.id,
            strategy_instance_id=instance.id,
            strategy_version_id=instance.strategy_version_id,
            mode="paper",
            revision=1,
            configuration_json={"lots": lots},
            risk_json={},
            status="active",
        )
        db.add(revision)
        db.flush()
        db.add(
            models.UserEngineConfig(
                user_id=user.id,
                selected_strategy_instance_id=instance.id,
                selected_configuration_revision_id=revision.id,
                selected_configuration_revision=revision.revision,
            )
        )


def test_context_credentials_are_isolated_per_user(mu_db):
    from app.services.credential_vault import (
        get_dhan_credentials,
        save_dhan_credentials,
    )
    from app.services.execution_context import bind_execution_context

    alice = _current_user(make_user("fanout-alice@gmail.com"))
    bob = _current_user(make_user("fanout-bob@gmail.com"))

    with bind_execution_context(alice):
        save_dhan_credentials("ALICE-ID", "alice-token")
    with bind_execution_context(bob):
        save_dhan_credentials("BOB-ID", "bob-token")

    with bind_execution_context(alice):
        assert get_dhan_credentials().client_id == "ALICE-ID"
    with bind_execution_context(bob):
        assert get_dhan_credentials().client_id == "BOB-ID"


def test_runtime_state_is_isolated_per_user(mu_db, monkeypatch, tmp_path):
    from app.services import state_store, user_context
    from app.services.execution_context import bind_execution_context

    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", state_root)
    monkeypatch.setattr(state_store, "RUNTIME_LOG_DIR", log_root)
    monkeypatch.setattr(user_context, "RUNTIME_STATE_DIR", state_root)
    monkeypatch.setattr(user_context, "RUNTIME_LOG_DIR", log_root)
    for name, filename in {
        "APP_STATE_FILE": "app_state.json",
        "OPEN_POSITION_FILE": "open_position.json",
        "PAPER_POSITION_FILE": "paper_position.json",
        "PAPER_PORTFOLIO_FILE": "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": "external_positions.json",
        "SEEN_SIGNALS_FILE": "seen_signals.json",
        "SETTINGS_FILE": "settings.json",
    }.items():
        monkeypatch.setattr(state_store, name, state_root / filename)
    monkeypatch.setattr(
        state_store,
        "LOG_FILES",
        {
            "webhook": log_root / "webhook_events.jsonl",
            "order": log_root / "order_events.jsonl",
            "audit": log_root / "audit_events.jsonl",
            "error": log_root / "errors.jsonl",
            "paper_orders": log_root / "paper_orders.jsonl",
        },
    )

    alice = _current_user(make_user("state-alice@gmail.com"))
    bob = _current_user(make_user("state-bob@gmail.com"))
    with bind_execution_context(alice):
        state_store.init_runtime_files()
        state_store.update_app_state(last_message="alice-state")
    with bind_execution_context(bob):
        state_store.init_runtime_files()
        state_store.update_app_state(last_message="bob-state")

    with bind_execution_context(alice):
        assert state_store.get_app_state()["last_message"] == "alice-state"
    with bind_execution_context(bob):
        assert state_store.get_app_state()["last_message"] == "bob-state"


def test_active_routing_user_ids_includes_paper_users_by_default(mu_db):
    # EOD square-off (and anything else that needs "every user with an
    # active subscription") relies on the default including paper users --
    # real_orders_only=True is opt-in for callers that specifically only
    # care about live-money routing.
    from app.services import strategy_fanout

    paper_user = make_user("route-eod-paper@gmail.com")
    real_user = make_user("route-eod-real@gmail.com")
    _grant_entitlement(real_user, live=True, strategy=True)
    strategy_fanout.subscribe_user(paper_user.id, "supertrend", lots=1, execution_mode="paper_live_data")
    strategy_fanout.subscribe_user(real_user.id, "supertrend", lots=1, execution_mode="real_orders")

    all_ids = set(strategy_fanout.active_routing_user_ids())
    assert paper_user.id in all_ids
    assert real_user.id in all_ids

    real_only_ids = set(strategy_fanout.active_routing_user_ids(real_orders_only=True))
    assert paper_user.id not in real_only_ids
    assert real_user.id in real_only_ids


def test_one_signal_routes_to_two_subscribers_with_their_lots(mu_db, monkeypatch):
    from app.services import strategy_fanout
    from app.services.execution_context import current_execution_user

    alice = make_user("route-alice@gmail.com")
    bob = make_user("route-bob@gmail.com")
    strategy_fanout.subscribe_user(
        alice.id,
        "supertrend",
        lots=1,
        execution_mode="paper_live_data",
    )
    strategy_fanout.subscribe_user(
        bob.id,
        "supertrend",
        lots=2,
        execution_mode="paper_live_data",
    )
    _bind_paper_configuration(alice, lots=1)
    _bind_paper_configuration(bob, lots=2)

    calls = []

    def fake_route(signal, **_kwargs):
        calls.append((current_execution_user().email, signal.qty))
        return {"success": True, "status": "TRADED"}

    monkeypatch.setattr(strategy_fanout, "route_signal", fake_route)
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    monkeypatch.setattr(
        strategy_fanout,
        "_quantity_for_subscription",
        lambda lots: lots * 75,
    )

    result = strategy_fanout.process_signal("supertrend", _signal("shared-secret"))

    assert result["subscriber_count"] == 2
    assert sorted(calls) == [
        ("route-alice@gmail.com", 75),
        ("route-bob@gmail.com", 150),
    ]


def test_live_broker_requires_and_uses_user_proxy(mu_db, monkeypatch):
    from app.config import settings
    from app.services import dhan_client
    from app.services.execution_context import bind_execution_context

    user = _current_user(make_user("proxy-user@gmail.com"))
    monkeypatch.setattr(
        settings,
        "EXECUTION_NODE_ROUTING_ENABLED",
        True,
        raising=False,
    )
    captured = {}

    def fake_http_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(dhan_client.httpx, "Client", fake_http_client)
    with bind_execution_context(
        user,
        proxy_url="http://proxy-user:secret@152.42.157.165:8888",
        egress_ip="152.42.157.165",
    ):
        client = dhan_client.get_broker_client("live")
        client._client(timeout=10)

    assert captured["proxy"].startswith("http://proxy-user:")
    assert "transport" not in captured


def _enable_aws_proxy_slots(monkeypatch, *, password: str = "aws-secret") -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "AWS_PROXY_SLOTS_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AWS_PROXY_HOST", "13.203.58.220", raising=False)
    monkeypatch.setattr(settings, "AWS_PROXY_SHARED_PASSWORD", password, raising=False)
    for slot_number in range(1, 6):
        monkeypatch.setattr(
            settings,
            f"AWS_PROXY_SLOT_{slot_number}_PASSWORD",
            "",
            raising=False,
        )


def test_configured_egress_nodes_prefers_aws_slots_without_egress_nodes_json(monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout

    _enable_aws_proxy_slots(monkeypatch, password="abc@123")
    monkeypatch.setattr(settings, "EGRESS_NODES_JSON", "not-json", raising=False)

    nodes = strategy_fanout.configured_egress_nodes()

    assert len(nodes) == 5
    assert [node["public_ip"] for node in nodes] == [
        "13.203.58.220",
        "13.127.93.199",
        "13.206.213.151",
        "35.154.61.32",
        "65.0.153.89",
    ]
    assert [node["proxy_port"] for node in nodes] == [3001, 3002, 3003, 3004, 3005]
    assert nodes[0]["proxy_url"] == "http://nova_user_1:abc%40123@13.203.58.220:3001"
    assert nodes[0]["expected_egress_ip"] == "13.203.58.220"
    assert nodes[0]["private_ip"] == "172.31.43.47"
    assert nodes[0]["label"] == "Nova Static IP 1"


def test_configured_egress_nodes_uses_legacy_json_when_aws_slots_disabled(monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout

    monkeypatch.setattr(settings, "AWS_PROXY_SLOTS_ENABLED", False, raising=False)
    nodes = [
        {
            "public_ip": "165.232.184.177",
            "proxy_url": "http://legacy-user:secret@64.225.87.19:8888",
        }
    ]
    monkeypatch.setattr(settings, "EGRESS_NODES_JSON", json.dumps(nodes), raising=False)

    assert strategy_fanout.configured_egress_nodes() == nodes


def test_configured_egress_nodes_missing_aws_password_raises_safe_error(monkeypatch):
    from app.services import strategy_fanout
    from app.services.aws_proxy_slots import AWSProxySlotConfigError

    _enable_aws_proxy_slots(monkeypatch, password="")

    with pytest.raises(AWSProxySlotConfigError) as exc:
        strategy_fanout.configured_egress_nodes()

    message = str(exc.value)
    assert message == "AWS proxy credential is missing for slot 1."
    assert "proxy_url" not in message
    assert "@" not in message


def test_user_egress_options_for_aws_slots_do_not_expose_proxy_credentials(
    mu_db,
    monkeypatch,
):
    from app.services import strategy_fanout

    user = make_user("aws-egress-options@gmail.com")
    _grant_entitlement(user, static_ip=True)
    _enable_aws_proxy_slots(monkeypatch, password="abc@123")

    options = strategy_fanout.user_egress_options(user.id)
    serialized = json.dumps(options, sort_keys=True)

    assert len(options["nodes"]) == 1
    assert options["nodes"][0] == {
        "public_ip": "13.203.58.220",
        "expected_egress_ip": "13.203.58.220",
        "provider": "AWS",
        "slot_number": 1,
        "label": "Nova Static IP 1",
        "available": True,
        "selected": True,
    }
    assert options["egress"]["public_ip"] == "13.203.58.220"
    assert "proxy_url" not in serialized
    assert "abc@123" not in serialized
    assert "abc%40123" not in serialized
    assert "nova_user" not in serialized


def test_user_egress_options_require_static_ip_entitlement_before_listing_slots(
    mu_db,
    monkeypatch,
):
    from app.services import entitlements, strategy_fanout

    user = make_user("aws-egress-options-no-entitlement@gmail.com")
    _enable_aws_proxy_slots(monkeypatch, password="abc@123")

    with pytest.raises(entitlements.EntitlementError) as exc:
        strategy_fanout.user_egress_options(user.id)

    message = str(exc.value)
    assert message == "Static IP entitlement is required."
    assert "13.203.58.220" not in message
    assert "abc" not in message


def test_authenticated_users_select_distinct_configured_egress_ips(
    mu_db,
    monkeypatch,
):
    from app.auth.dependencies import get_current_user
    from app.config import settings
    from app.routers.strategies import router
    from app.services import strategy_fanout

    alice = make_user("egress-alice@gmail.com")
    bob = make_user("egress-bob@gmail.com")
    _grant_entitlement(alice, static_ip=True)
    _grant_entitlement(bob, static_ip=True)
    monkeypatch.setattr(settings, "AWS_PROXY_SLOTS_ENABLED", False, raising=False)
    nodes = [
        {
            "public_ip": "165.232.184.177",
            "proxy_url": "http://alice-node:secret-one@64.225.87.19:8888",
        },
        {
            "public_ip": "167.71.232.232",
            "proxy_url": "http://bob-node:secret-two@152.42.157.165:8888",
        },
    ]
    monkeypatch.setattr(
        settings,
        "EGRESS_NODES_JSON",
        json.dumps(nodes),
        raising=False,
    )
    monkeypatch.setattr(
        strategy_fanout,
        "verify_user_egress",
        lambda user_id: {
            "ok": True,
            "expected_ip": (
                "165.232.184.177"
                if user_id == alice.id
                else "167.71.232.232"
            ),
        },
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _current_user(alice)
    client = TestClient(app)

    options = client.get("/api/strategies/egress/options")
    assert options.status_code == 200
    assert [node["public_ip"] for node in options.json()["nodes"]] == ["165.232.184.177"]
    assert options.json()["nodes"][0]["selected"] is True
    assert options.json()["egress"]["public_ip"] == "165.232.184.177"
    assert "secret-one" not in options.text
    assert "secret-two" not in options.text

    selected = client.post(
        "/api/strategies/egress/select",
        json={"public_ip": "165.232.184.177"},
    )
    assert selected.status_code == 200
    assert selected.json()["verification"]["ok"] is True
    assert "secret-one" not in selected.text

    verified = client.post("/api/strategies/egress/verify")
    assert verified.status_code == 200
    assert verified.json()["ok"] is True
    assert verified.json()["egress"]["expected_ip"] == "165.232.184.177"
    assert "secret-one" not in verified.text

    app.dependency_overrides[get_current_user] = lambda: _current_user(bob)
    bob_options = client.get("/api/strategies/egress/options").json()
    assert [node["public_ip"] for node in bob_options["nodes"]] == ["167.71.232.232"]
    assert bob_options["nodes"][0]["available"] is True
    assert bob_options["nodes"][0]["selected"] is True
    assert bob_options["egress"]["public_ip"] == "167.71.232.232"

    conflict = client.post(
        "/api/strategies/egress/select",
        json={"public_ip": "165.232.184.177"},
    )
    assert conflict.status_code == 400
    assert conflict.json()["error"] == "Nova Static IP is assigned server-side."

    bob_selected = client.post(
        "/api/strategies/egress/select",
        json={"public_ip": "167.71.232.232"},
    )
    assert bob_selected.status_code == 200


def test_egress_select_and_verify_require_static_ip_entitlement(mu_db, monkeypatch):
    from app.auth.dependencies import get_current_user
    from app.config import settings
    from app.routers.strategies import router
    from app.services import strategy_fanout

    user = make_user("egress-no-entitlement@gmail.com")
    monkeypatch.setattr(settings, "AWS_PROXY_SLOTS_ENABLED", False, raising=False)
    monkeypatch.setattr(
        settings,
        "EGRESS_NODES_JSON",
        json.dumps(
            [
                {
                    "public_ip": "165.232.184.177",
                    "proxy_url": "http://node-user:secret@64.225.87.19:8888",
                }
            ]
        ),
        raising=False,
    )
    monkeypatch.setattr(
        strategy_fanout,
        "verify_user_egress",
        lambda _user_id: (_ for _ in ()).throw(AssertionError("static IP entitlement must block first")),
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _current_user(user)
    client = TestClient(app)

    options = client.get("/api/strategies/egress/options")
    selected = client.post("/api/strategies/egress/select", json={"public_ip": "165.232.184.177"})
    verified = client.post("/api/strategies/egress/verify")

    assert options.status_code == 403
    assert selected.status_code == 403
    assert verified.status_code == 403
    for response in (options, selected, verified):
        assert response.json()["error"] == "Static IP entitlement is required."
        assert "secret" not in response.text


def test_context_free_live_router_is_blocked_when_egress_routing_is_enabled(
    monkeypatch,
):
    from app.config import settings
    from app.services.execution_router import _live_broker_client

    monkeypatch.setattr(
        settings,
        "EXECUTION_NODE_ROUTING_ENABLED",
        True,
        raising=False,
    )
    with pytest.raises(RuntimeError, match="Context-free live routing"):
        _live_broker_client()


def test_strategy_real_order_subscription_requires_server_entitlement(mu_db):
    from app.auth.dependencies import get_current_user
    from app.routers.strategies import router

    user = make_user("strategy-real-subscribe-entitlement@gmail.com")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _current_user(user)
    client = TestClient(app)

    blocked = client.post(
        "/api/strategies/subscribe",
        json={
            "strategy_name": "supertrend",
            "lots": 1,
            "execution_mode": "real_orders",
            "payment_status": "active",
            "subscription_status": "active",
            "is_paid": True,
        },
    )

    assert blocked.status_code == 403
    assert blocked.json()["error"] == "Live entitlement is required."

    _grant_entitlement(user, live=True, strategy=True)
    allowed = client.post(
        "/api/strategies/subscribe",
        json={"strategy_name": "supertrend", "lots": 1, "execution_mode": "real_orders"},
    )

    assert allowed.status_code == 200
    assert allowed.json()["subscription"]["execution_mode"] == "real_orders"


def test_strategy_signal_only_subscription_remains_offline_compatible(mu_db):
    from app.services import strategy_fanout

    user = make_user("strategy-signal-only-free@gmail.com")

    subscription = strategy_fanout.subscribe_user(
        user.id,
        "supertrend",
        lots=1,
        execution_mode="signal_only",
    )

    assert subscription["execution_mode"] == "signal_only"


def test_real_order_fanout_blocks_without_entitlement_before_routing(mu_db, monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout

    user = make_user("fanout-no-live-entitlement@gmail.com")
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(
        strategy_fanout,
        "route_signal",
        lambda _signal: (_ for _ in ()).throw(AssertionError("entitlement must block before routing")),
    )

    result = strategy_fanout.dispatch_signal_job(
        user_id=user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="real_orders",
        signal=_signal("shared-secret", signal_id="fanout-no-entitlement"),
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "live_entitlement_required"


def test_real_order_fanout_requires_strategy_entitlement_separately(mu_db, monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout

    user = make_user("fanout-no-strategy-entitlement@gmail.com")
    _grant_entitlement(user, live=True, strategy=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True, raising=False)

    result = strategy_fanout.dispatch_signal_job(
        user_id=user.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="real_orders",
        signal=_signal("shared-secret", signal_id="fanout-no-strategy-entitlement"),
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "strategy_entitlement_required"


def test_real_order_fanout_dispatches_only_for_entitled_user(mu_db, monkeypatch):
    from app.config import settings
    from app.services import strategy_fanout
    from app.services.execution_context import current_execution_user

    entitled = make_user("fanout-entitled@gmail.com")
    blocked = make_user("fanout-other-user@gmail.com")
    _grant_entitlement(entitled, live=True, strategy=True)
    calls: list[str] = []
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(
        strategy_fanout,
        "get_user_egress",
        lambda user_id: {
            "public_ip": "13.203.58.220",
            "expected_egress_ip": "13.203.58.220",
            "proxy_url": "http://nova_user_1:proxy-secret@13.203.58.220:3001",
            "active": True,
            "verified": True,
            "last_observed_ip": "13.203.58.220",
            "last_verified_at": "2026-06-29T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(strategy_fanout.vault, "get_user_dhan_credentials", lambda _user_id: object())
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    monkeypatch.setattr(strategy_fanout, "_quantity_for_subscription", lambda lots: lots * 75)
    monkeypatch.setattr(
        strategy_fanout,
        "route_signal",
        lambda signal: calls.append(current_execution_user().email) or {"success": True, "status": "TRADED"},
    )

    allowed = strategy_fanout.dispatch_signal_job(
        user_id=entitled.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="real_orders",
        signal=_signal("shared-secret", signal_id="fanout-entitled"),
    )
    denied = strategy_fanout.dispatch_signal_job(
        user_id=blocked.id,
        strategy_name="supertrend",
        lots=1,
        execution_mode="real_orders",
        signal=_signal("shared-secret", signal_id="fanout-blocked-other"),
    )

    assert allowed["status"] == "completed"
    assert denied["status"] == "blocked"
    assert denied["reason"] == "live_entitlement_required"
    assert calls == ["fanout-entitled@gmail.com"]


@pytest.mark.usefixtures("ready_default_strategy")
def test_strategy_webhook_accepts_tradingview_json_and_blocks_duplicate(
    mu_db,
    monkeypatch,
):
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.routers.strategies import router
    from app.services import strategy_fanout

    secret = "strategy-secret-1234567890-strong"
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    user = make_user("queued-user@gmail.com")
    strategy_fanout.subscribe_user(
        user.id,
        "supertrend",
        lots=1,
        execution_mode="signal_only",
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    body = {
        "secret": secret,
        "signal_id": "tv-shared-1",
        "action": "BUY_CE",
        "signal_time": "2026-07-31T09:00:00Z",
    }

    response = client.post("/api/webhook/strategy/supertrend", json=body)
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["subscriber_count"] == 1
    with session_scope() as db:
        jobs = db.query(models.StrategyExecutionJob).all()
        assert len(jobs) == 1
        assert jobs[0].user_id == user.id
        assert jobs[0].status == "queued"
        assert jobs[0].signal_payload["qty"] == 1
        assert jobs[0].signal_payload["secret"] == ""
        assert "secret" not in jobs[0].signal_payload["raw_payload"]

    duplicate = client.post("/api/webhook/strategy/supertrend", json=body)
    assert duplicate.status_code == 409


@pytest.mark.usefixtures("ready_default_strategy")
def test_strategy_webhook_signal_is_visible_on_each_subscribers_signals_page(
    mu_db,
    monkeypatch,
):
    # signals_feed.list_signals() filters strictly on WebhookEvent.user_id.
    # The webhook's own dedup claim has no owner (one alert fans out to many
    # subscribers), so without a per-subscriber record too, this broadcast
    # strategy's signals were invisible on every subscriber's Signals page.
    from app.config import settings
    from app.routers.strategies import router
    from app.services import signals_feed, strategy_fanout

    secret = "strategy-secret-1234567890-strong"
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", secret, raising=False)
    alice = make_user("signals-visible-alice@gmail.com")
    bob = make_user("signals-visible-bob@gmail.com")
    strategy_fanout.subscribe_user(alice.id, "supertrend", lots=1, execution_mode="signal_only")
    strategy_fanout.subscribe_user(bob.id, "supertrend", lots=1, execution_mode="signal_only")

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    body = {
        "secret": secret,
        "signal_id": "tv-shared-visibility-1",
        "action": "BUY_CE",
        "signal_time": "2026-07-31T09:00:00Z",
    }

    response = client.post("/api/webhook/strategy/supertrend", json=body)
    assert response.status_code == 202

    for user in (alice, bob):
        feed = signals_feed.list_signals(user.id)
        assert feed["counts"].get("total", 0) >= 1
        assert any(item["event_id"] == "tv-shared-visibility-1" for item in feed["items"])


def test_durable_jobs_process_two_users_independently(mu_db, monkeypatch):
    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope
    from app.services import strategy_fanout
    from app.services.execution_context import current_execution_user
    from app.workers.strategy_job_worker import process_queued_jobs_once

    alice = make_user("job-alice@gmail.com")
    bob = make_user("job-bob@gmail.com")
    strategy_fanout.subscribe_user(
        alice.id,
        "supertrend",
        lots=1,
        execution_mode="paper_live_data",
    )
    strategy_fanout.subscribe_user(
        bob.id,
        "supertrend",
        lots=2,
        execution_mode="paper_live_data",
    )
    _bind_paper_configuration(alice, lots=1)
    _bind_paper_configuration(bob, lots=2)
    calls = []

    def fake_route(signal, **_kwargs):
        calls.append((current_execution_user().email, signal.qty))
        return {"success": True, "status": "TRADED"}

    monkeypatch.setattr(strategy_fanout, "route_signal", fake_route)
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)
    monkeypatch.setattr(
        strategy_fanout,
        "_quantity_for_subscription",
        lambda lots: lots * 75,
    )

    queued = strategy_fanout.enqueue_strategy_signal(
        "supertrend",
        _signal("shared-secret", signal_id="durable-two-user-1"),
    )
    assert queued["subscriber_count"] == 2
    with session_scope() as db:
        jobs = db.scalars(
            select(models.StrategyExecutionJob).where(
                models.StrategyExecutionJob.signal_id == "durable-two-user-1"
            )
        ).all()
        assert len(jobs) == 2
        assert {job.configuration_revision for job in jobs} == {1}
        assert all(job.configuration_revision_id is not None for job in jobs)
    assert process_queued_jobs_once(limit=2) == 2
    assert sorted(calls) == [
        ("job-alice@gmail.com", 75),
        ("job-bob@gmail.com", 150),
    ]
    with session_scope() as db:
        jobs = db.query(models.StrategyExecutionJob).all()
        assert {job.status for job in jobs} == {"completed"}
        signal = db.query(models.StrategySignal).one()
        assert signal.status == "completed"


def test_queued_jobs_keep_old_and_new_immutable_automation_revisions(mu_db, monkeypatch):
    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope
    from app.services import setup_configuration, strategy_fanout

    user = make_user("automation-fanout@gmail.com")
    strategy_fanout.subscribe_user(
        user.id,
        "supertrend",
        lots=1,
        execution_mode="paper_live_data",
    )
    _bind_paper_configuration(user, lots=1)
    monkeypatch.setattr(strategy_fanout, "init_runtime_files", lambda: None)

    strategy_fanout.enqueue_strategy_signal(
        "supertrend",
        _signal("shared-secret", signal_id="automation-revision-old"),
    )
    selected = setup_configuration.selected_configuration(user.id, "paper")
    assert selected is not None
    successor = setup_configuration.revise_automation_configuration(
        user.id,
        configuration_id=uuid.UUID(selected["id"]),
        expected_revision=1,
        changes={"max_daily_loss": 12_000},
    )
    strategy_fanout.enqueue_strategy_signal(
        "supertrend",
        _signal("shared-secret", signal_id="automation-revision-new"),
    )

    with session_scope() as db:
        jobs = db.scalars(
            select(models.StrategyExecutionJob).where(
                models.StrategyExecutionJob.user_id == user.id,
                models.StrategyExecutionJob.signal_id.in_(
                    ["automation-revision-old", "automation-revision-new"]
                ),
            )
        ).all()
        by_signal = {job.signal_id: job for job in jobs}
        assert by_signal["automation-revision-old"].configuration_revision == 1
        assert by_signal["automation-revision-new"].configuration_revision == successor["revision"] == 2
        assert (
            by_signal["automation-revision-old"].configuration_revision_id
            != by_signal["automation-revision-new"].configuration_revision_id
        )


def test_new_session_does_not_replay_recent_strategy_job_result_by_default(mu_db, monkeypatch, tmp_path):
    from app.api.session import start_session
    from app.db import models
    from app.db.engine import session_scope
    from app.domain.state_machine import SetupState
    from app.services import state_store, user_context
    from app.store.redis_session import session_store

    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", state_root)
    monkeypatch.setattr(state_store, "RUNTIME_LOG_DIR", log_root)
    monkeypatch.setattr(user_context, "RUNTIME_STATE_DIR", state_root)
    monkeypatch.setattr(user_context, "RUNTIME_LOG_DIR", log_root)
    for name, filename in {
        "APP_STATE_FILE": "app_state.json",
        "OPEN_POSITION_FILE": "open_position.json",
        "PAPER_POSITION_FILE": "paper_position.json",
        "PAPER_PORTFOLIO_FILE": "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": "external_positions.json",
        "SEEN_SIGNALS_FILE": "seen_signals.json",
        "SETTINGS_FILE": "settings.json",
    }.items():
        monkeypatch.setattr(state_store, name, state_root / filename)
    monkeypatch.setattr(
        state_store,
        "LOG_FILES",
        {
            "webhook": log_root / "webhook_events.jsonl",
            "order": log_root / "order_events.jsonl",
            "audit": log_root / "audit_events.jsonl",
            "error": log_root / "errors.jsonl",
            "paper_orders": log_root / "paper_orders.jsonl",
        },
    )
    state_store.init_runtime_files()
    state_store.set_engine_mode("live")
    state_store.update_app_state(engine_started=True, webhook_trading_enabled=True)

    user = make_user("replay-user@gmail.com")
    signal = _signal("", signal_id="missed-eod")
    signal = signal.model_copy(update={"action": "EXIT", "source": "tradingview_eod"})
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        signal_row = models.StrategySignal(
            strategy_name="supertrend",
            signal_id=signal.signal_id,
            status="completed",
            result_summary={"subscriber_count": 1},
        )
        db.add(signal_row)
        db.flush()
        db.add(
            models.StrategyExecutionJob(
                strategy_signal_id=signal_row.id,
                user_id=user.id,
                strategy_name="supertrend",
                signal_id=signal.signal_id,
                signal_payload=signal.model_dump(mode="json"),
                lots=1,
                execution_mode="real_orders",
                status="completed",
                locked_at=now,
                completed_at=now,
                result_summary={
                    "status": "blocked",
                    "execution_result": {
                        "blocked": True,
                        "status": "BLOCKED",
                        "reason": "Exit blocked: no open position exists.",
                    },
                },
            )
        )

    async def default_scenario():
        bootstrap = await start_session(_current_user(user))
        session = await session_store.get(str(bootstrap["sessionId"]))
        assert session is not None
        await session_store.update_state(session.id, SetupState.ENDED, {})
        return session

    session = asyncio.run(default_scenario())
    default_event_types = [item.type for item in session.events]
    assert "funds.update" in default_event_types
    assert "position.update" in default_event_types
    assert "system.event" not in default_event_types
    assert "signal.received" not in default_event_types
    assert "order.rejected" not in default_event_types

    async def restore_scenario():
        bootstrap = await start_session(_current_user(user), restore_recent=True)
        session = await session_store.get(str(bootstrap["sessionId"]))
        assert session is not None
        await session_store.update_state(session.id, SetupState.ENDED, {})
        return session

    session = asyncio.run(restore_scenario())
    event_types = [item.type for item in session.events]
    signal_events = [item for item in session.events if item.type == "signal.received"]
    rejected_events = [item for item in session.events if item.type == "order.rejected"]

    assert "system.event" in event_types
    assert signal_events[-1].data["strategy"] == "Supertrend"
    assert signal_events[-1].data["action"] == "EXIT"
    assert rejected_events[-1].data["message"] == "Exit blocked: no open position exists."
    assert rejected_events[-1].data["normalizedError"]["category"] == "NO_POSITION"


def test_additive_schema_upgrades_pre_release_user_egress_table(tmp_path):
    from scripts.init_db import ensure_additive_schema
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(f"sqlite:///{tmp_path / 'old-schema.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE user_egress ("
                "id VARCHAR(36) PRIMARY KEY, "
                "user_id VARCHAR(36) NOT NULL, "
                "public_ip VARCHAR(64), "
                "proxy_url_encrypted TEXT, "
                "active BOOLEAN NOT NULL"
                ")"
            )
        )

    ensure_additive_schema(engine)
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("user_egress")
    }
    assert {
        "last_verified_at",
        "last_observed_ip",
        "verification_error",
    }.issubset(columns)
