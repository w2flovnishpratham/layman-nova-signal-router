"""Live-P&L monitor ticks must not invalidate a user's pending position op.

Regression for "The position changed. Refresh before submitting this operation."
which fired on Add Lots / Partial Exit / Edit SL-TP because every LTP tick bumped
position_version.
"""
from __future__ import annotations

import pytest

from app.services import state_store


@pytest.fixture()
def runtime_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "RUNTIME_STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(state_store, "APP_STATE_FILE", tmp_path / "app_state.json", raising=False)
    monkeypatch.setattr(state_store, "_scoped_dir", lambda: tmp_path, raising=False)
    return tmp_path


def _open(pid="pos-1", version=5):
    state_store.set_open_position({
        "has_open_position": True, "position_id": pid, "position_version": version,
        "qty": 65, "entry_price": 73.90, "live_pnl": {"ltp": 73.0},
    })


def test_monitor_tick_does_not_advance_the_version(runtime_state):
    _open(version=5)
    # 20 cosmetic LTP ticks like the monitor does.
    for i in range(20):
        applied, _ = state_store.patch_open_position_cas(
            position_id="pos-1", position_version=5,
            patch={"live_pnl": {"ltp": 73.0 + i}}, bump_version=False,
        )
        assert applied
    assert state_store.get_open_position()["position_version"] == 5


def test_user_op_with_original_version_survives_many_ticks(runtime_state):
    _open(version=5)
    for i in range(30):
        state_store.patch_open_position_cas(
            position_id="pos-1", position_version=5,
            patch={"live_pnl": {"ltp": 73.0 + i}}, bump_version=False,
        )
    # The user still holds version 5 (page load). Their Add-Lots must apply.
    applied, updated = state_store.patch_open_position_cas(
        position_id="pos-1", position_version=5, patch={"qty": 130},
    )
    assert applied is True
    assert updated["qty"] == 130
    assert updated["position_version"] == 6  # structural change advances it


def test_structural_change_still_bumps_the_version(runtime_state):
    _open(version=5)
    applied, updated = state_store.patch_open_position_cas(
        position_id="pos-1", position_version=5, patch={"qty": 130},
    )
    assert applied and updated["position_version"] == 6


def test_stale_monitor_write_is_still_rejected_after_a_structural_change(runtime_state):
    _open(version=5)
    # A structural op advances the version to 6.
    state_store.patch_open_position_cas(position_id="pos-1", position_version=5, patch={"qty": 130})
    # A monitor tick still holding version 5 must be rejected (anti-resurrection).
    applied, _ = state_store.patch_open_position_cas(
        position_id="pos-1", position_version=5,
        patch={"live_pnl": {"ltp": 99.0}}, bump_version=False,
    )
    assert applied is False
