from __future__ import annotations

import secrets

import pytest

from app.auth.db import init_auth_db, session_scope
from app.auth.models import User
from app.services import credential_vault, paper_portfolio, state_store
from app.services.credential_vault import get_dhan_credentials, save_dhan_credentials, save_webhook_secret
from app.services.user_connections import (
    NoEgressNodeAvailable,
    assign_unique_egress_node,
    connection_status,
    find_user_id_by_webhook_secret,
    register_egress_node,
)
from app.services.user_context import reset_current_user_id, set_current_user_id


def _create_user(suffix: str) -> User:
    init_auth_db()
    user = User(
        id=f"u_test_{suffix}",
        email=f"{suffix}@example.test",
        google_sub=f"google-{suffix}",
    )
    with session_scope() as session:
        existing = session.get(User, user.id)
        if existing:
            return existing
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def test_assign_unique_egress_node_allows_only_one_active_user_per_ip() -> None:
    suffix = secrets.token_hex(6)
    user_a = _create_user(f"{suffix}_a")
    user_b = _create_user(f"{suffix}_b")

    node = register_egress_node(
        name=f"vultr-bom-{suffix}",
        public_ip=f"10.255.{int(suffix[:2], 16)}.{int(suffix[2:4], 16)}",
        region="bom",
    )

    assignment, assigned_node = assign_unique_egress_node(user_a.id, preferred_node_id=node.id)
    assert assignment.user_id == user_a.id
    assert assigned_node.public_ip == node.public_ip

    with pytest.raises(NoEgressNodeAvailable, match="already assigned"):
        assign_unique_egress_node(user_b.id, preferred_node_id=node.id)

    status = connection_status(user_a.id)
    assert status["egressAssigned"] is True
    assert status["egressNode"]["publicIp"] == node.public_ip


def test_user_runtime_state_and_credentials_are_isolated(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "EXTERNAL_POSITIONS_FILE", state_dir / "external_positions.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    monkeypatch.setattr(
        state_store,
        "LOG_FILES",
        {
            "webhook": log_dir / "webhook_events.jsonl",
            "order": log_dir / "order_events.jsonl",
            "audit": log_dir / "audit_events.jsonl",
            "error": log_dir / "errors.jsonl",
            "paper_orders": log_dir / "paper_orders.jsonl",
        },
    )

    suffix = secrets.token_hex(6)
    user_a = _create_user(f"{suffix}_state_a")
    user_b = _create_user(f"{suffix}_state_b")
    webhook_secret = secrets.token_urlsafe(32)

    token = set_current_user_id(user_a.id)
    try:
        state_store.set_engine_mode("paper")
        state_store.update_runtime_settings(paper_starting_balance=125000)
        paper_portfolio.reset_paper_portfolio(125000)
        save_dhan_credentials("DHAN-A", "TOKEN-A")
        save_webhook_secret(webhook_secret)
        assert paper_portfolio.paper_wallet_snapshot()["available_balance"] == 125000
        assert get_dhan_credentials().client_id == "DHAN-A"
    finally:
        reset_current_user_id(token)

    token = set_current_user_id(user_b.id)
    try:
        state_store.set_engine_mode("paper")
        assert state_store.get_runtime_settings()["paper_starting_balance"] == 100000.0
        assert paper_portfolio.paper_wallet_snapshot()["available_balance"] == 100000.0
        assert get_dhan_credentials() is None
    finally:
        reset_current_user_id(token)

    assert find_user_id_by_webhook_secret(webhook_secret) == user_a.id
