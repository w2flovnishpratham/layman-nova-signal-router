"""Versioned Paper/Live risk configuration and entry-time snapshot contract."""
from __future__ import annotations

import pytest

from app.db import models
from app.db.engine import session_scope
from app.schemas.signal import NormalizedSignal
from app.services import risk_configuration, state_store
from app.services.execution_router import _entry_position
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _isolate_runtime_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(state_store, "SETTINGS_FILE", tmp_path / "runtime_state" / "settings.json")


def test_first_time_defaults_are_backend_balanced_and_mode_separate(mu_db):  # noqa: F811
    user = make_user("risk-defaults@example.com")

    paper = risk_configuration.configuration(user.id, "paper")
    live = risk_configuration.configuration(user.id, "live")

    assert paper["profileType"] == live["profileType"] == "BALANCED"
    assert paper["activeVersion"] == live["activeVersion"] == 0
    assert paper["values"]["daily_loss_cap"] == 25_000
    assert paper["suggestedDefault"] is True


def test_custom_save_creates_an_immutable_version(mu_db):  # noqa: F811
    user = make_user("risk-custom@example.com")
    values = risk_configuration.preset_values("BALANCED")
    values["daily_loss_cap"] = 20_000

    saved = risk_configuration.save_configuration(
        user.id,
        mode="paper",
        based_on_preset="BALANCED",
        values=values,
        change_source="RISK_PAGE",
        expected_version=0,
    )

    assert saved["activeVersion"] == 1
    assert saved["profileType"] == "CUSTOM"
    assert saved["basedOnPreset"] == "BALANCED"
    assert saved["values"]["daily_loss_cap"] == 20_000
    assert risk_configuration.preset_values("BALANCED")["daily_loss_cap"] == 25_000
    with session_scope() as db:
        rows = list(db.query(models.UserRiskConfigurationVersion).all())
        assert len(rows) == 1
        assert rows[0].daily_loss_cap_paise == 2_000_000


def test_paper_save_never_changes_live(mu_db):  # noqa: F811
    user = make_user("risk-mode@example.com")
    values = risk_configuration.preset_values("CONSERVATIVE")
    risk_configuration.save_configuration(
        user.id,
        mode="paper",
        based_on_preset="CONSERVATIVE",
        values=values,
        change_source="SETUP",
        expected_version=0,
    )

    assert risk_configuration.configuration(user.id, "paper")["profileType"] == "CONSERVATIVE"
    assert risk_configuration.configuration(user.id, "live")["profileType"] == "BALANCED"
    assert risk_configuration.configuration(user.id, "live")["activeVersion"] == 0


def test_save_syncs_max_trades_into_runtime_settings(mu_db, tmp_path, monkeypatch):  # noqa: F811
    """Confirmed against production: an owner saved max_trades_per_day=0
    (no limit) via this exact path, it displayed as saved everywhere, but
    trades still got blocked at a stale value of 50 -- because
    risk_manager's actual entry-blocking check reads runtime_settings, not
    this table, and nothing was syncing the two. RiskPage.tsx and the
    Trading-page widget both save through here directly (change_source
    RISK_PAGE / TRADING_TERMINAL); only setup_configuration.py's own flow
    (change_source SETUP, which passes _db=db) already handled its own
    sync and must not get a second, redundant write here."""
    _isolate_runtime_settings(tmp_path, monkeypatch)
    user = make_user("risk-sync@example.com")
    # Reproduce the exact production scenario: runtime_settings already has
    # a stale explicit cap (this is what a prior SETUP-flow save, or the
    # unpatched bug, leaves behind) -- 0's own default-value coincidence
    # can't prove a sync happened, a stale nonzero value overwritten to 0 can.
    state_store.update_mode_runtime_settings("paper", max_trades_per_day=50, max_daily_loss=25_000.0)
    state_store.update_mode_runtime_settings("live", max_trades_per_day=7, max_daily_loss=9_000.0)
    values = risk_configuration.preset_values("BALANCED")
    values["max_trades_per_day"] = 0
    values["daily_loss_cap"] = 0

    risk_configuration.save_configuration(
        user.id,
        mode="paper",
        based_on_preset="BALANCED",
        values=values,
        change_source="TRADING_TERMINAL",
        expected_version=0,
    )

    synced = state_store.get_mode_runtime_settings("paper")
    assert synced["max_trades_per_day"] == 0
    assert synced["max_daily_loss"] == 0
    # Live must stay untouched by a paper-mode save, same guarantee as the
    # risk_configuration table itself.
    live = state_store.get_mode_runtime_settings("live")
    assert live["max_trades_per_day"] == 7
    assert live["max_daily_loss"] == 9_000.0


def test_stale_writer_is_rejected(mu_db):  # noqa: F811
    user = make_user("risk-conflict@example.com")
    values = risk_configuration.preset_values("BALANCED")
    risk_configuration.save_configuration(
        user.id,
        mode="paper",
        based_on_preset="BALANCED",
        values=values,
        change_source="RISK_PAGE",
        expected_version=0,
    )

    with pytest.raises(risk_configuration.RiskConfigurationError) as caught:
        risk_configuration.save_configuration(
            user.id,
            mode="paper",
            based_on_preset="BALANCED",
            values=values,
            change_source="TRADING_TERMINAL",
            expected_version=0,
        )
    assert caught.value.status_code == 409
    assert caught.value.code == "RISK_VERSION_CONFLICT"


def test_server_rejects_unsafe_values(mu_db):  # noqa: F811
    user = make_user("risk-validation@example.com")
    values = risk_configuration.preset_values("BALANCED")
    values["max_loss_per_trade"] = 30_000

    with pytest.raises(risk_configuration.RiskConfigurationError, match="cannot exceed"):
        risk_configuration.save_configuration(
            user.id,
            mode="paper",
            based_on_preset="BALANCED",
            values=values,
            change_source="RISK_PAGE",
            expected_version=0,
        )


def test_owner_isolation_and_entry_overlay(mu_db):  # noqa: F811
    alice = make_user("alice-risk-config@example.com")
    bob = make_user("bob-risk-config@example.com")
    values = risk_configuration.preset_values("AGGRESSIVE")
    saved = risk_configuration.save_configuration(
        alice.id,
        mode="live",
        based_on_preset="AGGRESSIVE",
        values=values,
        change_source="TRADING_TERMINAL",
        expected_version=0,
    )

    overlay = risk_configuration.active_runtime_overlay(alice.id, "live")
    assert overlay["risk_configuration_version_id"] == saved["activeVersionId"]
    assert overlay["max_daily_loss"] == 50_000
    assert overlay["risk_exit_mode"] == "CUSTOM_SL_TP"
    assert risk_configuration.configuration(bob.id, "live")["activeVersion"] == 0


def test_open_position_keeps_its_entry_risk_snapshot():
    signal = NormalizedSignal(
        payload_format="NOVA",
        secret="test",
        signal_id="risk-snapshot-1",
        strategy_code="NOVA_TEST",
        action="ENTRY",
        side="BUY",
        symbol="NIFTY",
        qty=50,
        raw_payload={},
    )
    position = _entry_position(
        signal,
        {"avg_price": 200.0, "order_id": "entry-1"},
        50,
        runtime={
            "risk_configuration_version_id": "11111111-1111-1111-1111-111111111111",
            "risk_configuration_version": 1,
            "risk_exit_mode": "CUSTOM_SL_TP",
            "risk_stop_loss_value": 20,
            "risk_take_profit_value": 40,
            "risk_stop_loss_basis": "POINTS",
            "risk_take_profit_basis": "POINTS",
            "risk_max_loss_per_trade": 500,
        },
    )

    assert position["risk_configuration_version"] == 1
    assert position["entry_stop_rule"]["value"] == 20
    assert position["maximum_loss_rule"]["amount"] == 500
    assert position["active_exit_levels"]["stopLossPrice"] == 190
    assert position["active_exit_levels"]["targetPrice"] == 240
