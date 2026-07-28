# ruff: noqa: F811
from __future__ import annotations

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _runtime(user):
    return {
        "ok": True,
        "owner_user_id": str(user.id),
        "engine": {
            "state": "STOPPED",
            "running": False,
            "accepting_signals": False,
            "mode": None,
            "display": "STOPPED",
            "last_transition_at": None,
        },
        "exit": {
            "state": "NONE",
            "operation_id": None,
            "requested_at": None,
        },
        "position": {
            "has_open_position": False,
            "position_version": 0,
            "qty": 0,
            "lots": 0,
            "ltp": {
                "value": None,
                "source": None,
                "status": "unavailable",
                "received_at": None,
                "age_seconds": None,
                "stale": False,
                "message": None,
            },
        },
        "pnl": {
            "realized": 0,
            "unrealized": 0,
            "session": 0,
            "available_balance": 1_000_000,
        },
        "config": {"active": {}, "paper": {}, "live": {}},
        "account": {},
        "safety": {},
        "selected_strategy": None,
        "eligible_strategies": [],
        "selection_issue": None,
    }


def _configuration_for(user):
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        catalog = models.StrategyCatalog(
            code=f"bootstrap-{user.id.hex[:8]}",
            display_name="Bootstrap",
            owner_type="personal",
            owner_user_id=user.id,
            status="active",
        )
        db.add(catalog)
        db.flush()
        version = models.StrategyVersion(
            strategy_id=catalog.id,
            version="1.0.0",
            payload_spec_version="nova.v1",
            source_journey="personal_tradingview",
            status="approved",
            execution_kind="external_webhook",
        )
        db.add(version)
        db.flush()
        instance = models.StrategyInstance(
            user_id=user.id,
            strategy_id=catalog.id,
            strategy_version_id=version.id,
            source_journey="PERSONAL_TRADINGVIEW",
            label="Bootstrap",
            status="active",
            execution_mode="paper_live_data",
            current_lots=1,
        )
        db.add(instance)
        db.flush()
        revision = models.StrategyConfigurationRevision(
            user_id=user.id,
            strategy_instance_id=instance.id,
            strategy_version_id=version.id,
            mode="paper",
            revision=1,
            configuration_json={"lots": 1, "direction": "BOTH"},
            risk_json={"max_daily_loss": 25_000},
            status="active",
        )
        db.add(revision)
        db.flush()
        db.add(
            models.UserEngineConfig(
                user_id=user.id,
                selected_strategy_instance_id=instance.id,
                selected_configuration_revision_id=revision.id,
                selected_configuration_revision=1,
            )
        )
        db.add(
            models.UserRun(
                user_id=user.id,
                run_type="paper",
                strategy_name="Bootstrap",
                status="stopped",
                execution_mode="paper_live_data",
                configuration_revision_id=revision.id,
                configuration_revision=1,
                strategy_version_id=version.id,
            )
        )
        return str(revision.id)


def test_bootstrap_is_owner_scoped_and_restores_exact_revision(mu_db, monkeypatch):
    from app.routers import strategies
    from app.services.user_context import current_user_from_model

    alice = make_user("bootstrap-alice@example.com")
    bob = make_user("bootstrap-bob@example.com")
    alice_revision = _configuration_for(alice)
    _configuration_for(bob)

    monkeypatch.setattr(strategies, "_owner_runtime", lambda user: _runtime(user))
    monkeypatch.setattr(
        strategies.strategy_catalog_service,
        "get_catalog",
        lambda *_args, **_kwargs: {
            "setup_progress": {"state": "saved"},
            "selected_strategy_key": "bootstrap",
            "strategies": [],
        },
    )
    monkeypatch.setattr(
        strategies.live_engine,
        "evaluate_live_readiness",
        lambda *_args, **_kwargs: {
            "ready": False,
            "real_orders_allowed": False,
            "blockers": ["test"],
            "checks": {},
        },
    )

    payload = strategies.trading_bootstrap(current_user_from_model(alice))

    assert payload["owner_user_id"] == str(alice.id)
    assert payload["mode"] == "paper"
    assert payload["setup"]["saved_complete"] is True
    assert payload["selected_configuration"]["id"] == alice_revision
    assert payload["selected_configuration"]["user_id"] == str(alice.id)
    assert payload["current_run"]["configuration_revision_id"] == alice_revision
    assert payload["position"]["position_version"] == 0


def test_bootstrap_does_not_invent_paper_mode_for_fresh_owner(mu_db, monkeypatch):
    from app.routers import strategies
    from app.services.user_context import current_user_from_model

    user = make_user("bootstrap-fresh@example.com")
    monkeypatch.setattr(strategies, "_owner_runtime", lambda owner: _runtime(owner))
    monkeypatch.setattr(
        strategies.strategy_catalog_service,
        "get_catalog",
        lambda *_args, **_kwargs: {
            "setup_progress": {"state": "fresh"},
            "selected_strategy_key": None,
            "strategies": [],
        },
    )
    monkeypatch.setattr(
        strategies.live_engine,
        "evaluate_live_readiness",
        lambda *_args, **_kwargs: {
            "ready": False,
            "real_orders_allowed": False,
            "blockers": ["test"],
            "checks": {},
        },
    )

    payload = strategies.trading_bootstrap(current_user_from_model(user))

    assert payload["mode"] is None
    assert payload["setup"]["mode_selected"] is False
    assert payload["setup"]["saved_complete"] is False
    assert payload["selected_configuration"] is None
    assert payload["current_run"] is None


def test_bootstrap_ignores_stale_stopped_runtime_mode_for_fresh_owner(
    mu_db,
    monkeypatch,
):
    from app.routers import strategies
    from app.services.user_context import current_user_from_model

    user = make_user("bootstrap-stale-mode@example.com")
    stale = _runtime(user)
    stale["engine"]["mode"] = "paper"
    monkeypatch.setattr(strategies, "_owner_runtime", lambda owner: stale)
    monkeypatch.setattr(
        strategies.strategy_catalog_service,
        "get_catalog",
        lambda *_args, **_kwargs: {
            "setup_progress": {"state": "fresh"},
            "selected_strategy_key": None,
            "strategies": [],
        },
    )
    monkeypatch.setattr(
        strategies.live_engine,
        "evaluate_live_readiness",
        lambda *_args, **_kwargs: {
            "ready": False,
            "real_orders_allowed": False,
            "blockers": ["test"],
            "checks": {},
        },
    )

    payload = strategies.trading_bootstrap(current_user_from_model(user))

    assert payload["engine"]["mode"] == "paper"
    assert payload["mode"] is None
    assert payload["setup"]["mode_selected"] is False
    assert payload["manual_defaults"]["paper"]["available"] is False
