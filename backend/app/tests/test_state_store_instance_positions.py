from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.services import audit_logger, state_store, user_context
from app.services.execution_context import bind_execution_context
from app.services.user_context import CurrentUser


def _isolate_runtime(monkeypatch, tmp_path):
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
        "OPEN_POSITIONS_BY_INSTANCE_FILE": "open_positions_by_instance.json",
        "PAPER_PORTFOLIO_FILE": "paper_portfolio.json",
        "EXTERNAL_POSITIONS_FILE": "external_positions.json",
        "SEEN_SIGNALS_FILE": "seen_signals.json",
        "SETTINGS_FILE": "settings.json",
    }.items():
        monkeypatch.setattr(state_store, name, state_root / filename)
    log_files = {
        "webhook": log_root / "webhook_events.jsonl",
        "order": log_root / "order_events.jsonl",
        "audit": log_root / "audit_events.jsonl",
        "error": log_root / "errors.jsonl",
        "paper_orders": log_root / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    state_store.init_runtime_files()
    state_store.update_app_state(webhook_trading_enabled=False, engine_started=False)
    return state_root


def _position(**overrides: Any) -> dict[str, Any]:
    position = {
        "symbol": "NIFTY",
        "option_side": "CE",
        "strike": 25000,
        "expiry": "2026-07-09",
        "qty": 75,
        "entry_price": 100.5,
    }
    position.update(overrides)
    return position


def _legacy_position(**overrides: Any) -> dict[str, Any]:
    position = {
        **state_store.default_open_position(),
        "has_open_position": True,
        "trading_symbol": "NIFTY LEGACY CE",
        "option_side": "CE",
        "qty": 75,
        "entry_price": 100.5,
    }
    position.update(overrides)
    return position


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_instance_get_and_clear_missing_file_are_noops(monkeypatch, tmp_path):
    state_root = _isolate_runtime(monkeypatch, tmp_path)
    instance_path = state_root / "open_positions_by_instance.json"
    assert not instance_path.exists()

    assert state_store.get_open_position("missing") is None
    assert state_store.list_open_positions_by_instance() == {}
    assert not instance_path.exists()

    assert state_store.clear_open_position("missing") is None
    assert not instance_path.exists()


def test_instance_set_get_list_and_clear_are_isolated(monkeypatch, tmp_path):
    state_root = _isolate_runtime(monkeypatch, tmp_path)
    instance_path = state_root / "open_positions_by_instance.json"
    open_before = _read_json(state_root / "open_position.json")
    paper_before = _read_json(state_root / "paper_position.json")

    pos_a = _position(option_side="CE", entry_price=100.5)
    pos_b = _position(option_side="PE", strike=24900, entry_price=88.2)

    assert state_store.set_open_position(pos_a, instance_id="A") == pos_a

    assert instance_path.exists()
    assert _read_json(state_root / "open_position.json") == open_before
    assert _read_json(state_root / "paper_position.json") == paper_before
    assert _read_json(instance_path) == {"A": pos_a}
    assert state_store.get_open_position("A") == pos_a
    assert state_store.get_open_position("B") is None

    state_store.set_open_position(pos_b, instance_id="B")

    assert state_store.get_open_position("A") == pos_a
    assert state_store.get_open_position("B") == pos_b
    assert state_store.list_open_positions_by_instance() == {"A": pos_a, "B": pos_b}

    assert state_store.clear_open_position("A") is None

    assert state_store.get_open_position("A") is None
    assert state_store.get_open_position("B") == pos_b
    assert _read_json(instance_path) == {"B": pos_b}

    assert state_store.clear_open_position("missing") is None
    assert _read_json(instance_path) == {"B": pos_b}


def test_legacy_open_position_calls_do_not_touch_instance_positions(monkeypatch, tmp_path):
    state_root = _isolate_runtime(monkeypatch, tmp_path)
    state_store.set_engine_mode("paper")
    instance_path = state_root / "open_positions_by_instance.json"
    pos_a = _position(option_side="CE")
    legacy = _legacy_position(trading_symbol="NIFTY LEGACY PAPER CE")

    state_store.set_open_position(pos_a, instance_id="A")
    instance_before = _read_json(instance_path)

    saved = state_store.set_open_position(legacy)

    assert saved == legacy
    assert state_store.get_open_position() == legacy
    assert _read_json(state_root / "paper_position.json") == legacy
    assert _read_json(instance_path) == instance_before

    cleared = state_store.clear_open_position()

    assert cleared == state_store.default_open_position()
    assert state_store.get_open_position()["has_open_position"] is False
    assert _read_json(instance_path) == instance_before
    assert state_store.get_open_position("A") == pos_a


def test_instance_positions_use_scoped_runtime_path_per_user(monkeypatch, tmp_path):
    state_root = _isolate_runtime(monkeypatch, tmp_path)
    alice = CurrentUser(id=uuid.uuid4(), email="alice@example.com", is_dev=False)
    bob = CurrentUser(id=uuid.uuid4(), email="bob@example.com", is_dev=False)
    alice_pos = _position(option_side="CE", entry_price=101.0)
    bob_pos = _position(option_side="PE", entry_price=89.0)

    with bind_execution_context(alice):
        state_store.set_open_position(alice_pos, instance_id="shared")

    with bind_execution_context(bob):
        assert state_store.get_open_position("shared") is None
        state_store.set_open_position(bob_pos, instance_id="shared")

    with bind_execution_context(alice):
        assert state_store.get_open_position("shared") == alice_pos

    with bind_execution_context(bob):
        assert state_store.get_open_position("shared") == bob_pos

    alice_path = state_root / "users" / str(alice.id) / "open_positions_by_instance.json"
    bob_path = state_root / "users" / str(bob.id) / "open_positions_by_instance.json"
    assert _read_json(alice_path) == {"shared": alice_pos}
    assert _read_json(bob_path) == {"shared": bob_pos}
    assert not (state_root / "open_positions_by_instance.json").exists()
