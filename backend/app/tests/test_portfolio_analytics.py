# ruff: noqa: F811
"""Tests for the live-only portfolio analytics that powers the dashboard.

These are deterministic and clock-independent: they do not touch market-hours
logic, so they pass regardless of when CI runs.
"""
from __future__ import annotations

from datetime import date

from app.services import portfolio_analytics as pa
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def test_payload_is_live_only_and_well_formed():
    payload = pa.build_portfolio_analytics(persist=False)

    # The dashboard is intentionally live-only.
    assert payload["mode"] == "live"

    for key in (
        "wallet",
        "kpis",
        "equity_curve",
        "daily_pnl",
        "side_breakdown",
        "symbol_breakdown",
        "trades",
    ):
        assert key in payload, f"missing key: {key}"

    kpis = payload["kpis"]
    for key in ("realized_pnl", "total_trades", "win_rate", "profit_factor", "max_drawdown"):
        assert key in kpis

    # Paper fills must never leak into the live ledger.
    for trade in payload["trades"]:
        assert not str(trade.get("exit_order_id") or "").startswith("PAPER-")


def test_paper_legs_are_excluded():
    paper_entry = {
        "phase": "after_response",
        "success": True,
        "status": "TRADED",
        "mode": "paper",
        "order_id": "PAPER-AAA",
        "normalized_action": "ENTRY",
        "normalized_qty": 50,
        "avg_price": 100.0,
        "normalized_option_side": "CE",
        "timestamp": "2026-06-17T09:20:00+00:00",
    }
    live_entry = {**paper_entry, "mode": "live", "order_id": "REAL-1", "timestamp": "2026-06-17T09:21:00+00:00"}
    live_exit = {
        **live_entry,
        "order_id": "REAL-2",
        "normalized_action": "EXIT",
        "avg_price": 110.0,
        "timestamp": "2026-06-17T09:25:00+00:00",
    }

    trades, _ = pa._pair_round_trips([paper_entry, live_entry, live_exit])

    # Only the live round-trip is paired; the paper entry is ignored.
    assert len(trades) == 1
    assert trades[0]["entry_order_id"] == "REAL-1"
    assert trades[0]["exit_order_id"] == "REAL-2"
    assert trades[0]["realized_pnl"] == 500.0  # (110 - 100) * 50
    assert trades[0]["result"] == "win"


def test_empty_when_no_live_trades():
    # Only paper legs -> nothing on the live dashboard.
    paper = {
        "phase": "after_response",
        "success": True,
        "mode": "paper",
        "order_id": "PAPER-X",
        "normalized_action": "ENTRY",
        "avg_price": 10.0,
        "normalized_qty": 1,
        "timestamp": "2026-06-17T09:20:00+00:00",
    }
    trades, open_entry = pa._pair_round_trips([paper])
    assert trades == []
    assert open_entry is None


def test_paper_exit_persists_a_row_reports_can_read_back(mu_db):
    """The gap this closes: a closed Paper trade used to vanish into a JSON
    file the Reports API never reads, so /api/reports(mode=paper) always
    showed zero trades regardless of real activity."""
    from app.services import reports_service
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    user = make_user("paper-persist@example.com")
    current = current_user_from_model(user)
    closed_trade = {
        "symbol": "NIFTY 25000 CE",
        "qty": 75,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "entry_charges": 5.0,
        "exit_charges": 5.0,
        "realized_pnl": 740.0,
        "entry_order_id": "PAPER-ENTRY-1",
        "exit_order_id": "PAPER-EXIT-1",
        "opened_at": "2026-07-27T09:20:00+00:00",
        "closed_at": "2026-07-27T09:25:00+00:00",
    }
    with bind_user_execution_context(current):
        pa.persist_paper_trade(closed_trade)
        pa.persist_paper_trade(closed_trade)  # idempotent: must not duplicate

    report = reports_service.build_report(
        user.id, start=date(2026, 7, 27), end=date(2026, 7, 27), mode="paper"
    )
    assert report["totals"]["trades"] == 1
    assert report["totals"]["net_pnl"] == 740.0
    assert report["trades"][0]["option_side"] == "CE"
    assert report["trades"][0]["symbol"] == "NIFTY 25000 CE"


def test_paper_wallet_writes_owner_snapshot_and_audits_reset(
    mu_db,
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope
    from app.services import paper_portfolio, state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    portfolio_path = tmp_path / "paper_portfolio.json"
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", portfolio_path)
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", portfolio_path)
    current = current_user_from_model(make_user("paper-snapshot@example.com"))

    with bind_user_execution_context(current):
        paper_portfolio.reset_paper_portfolio(1_000_000)
        paper_portfolio.apply_paper_entry(
            qty=75,
            price=100.0,
            charges=5.0,
            symbol="NIFTY TEST CE",
            order_id="PAPER-SNAPSHOT-ENTRY",
        )

    with session_scope() as db:
        snapshots = db.scalars(
            select(models.PortfolioSnapshot)
            .where(
                models.PortfolioSnapshot.user_id == current.id,
                models.PortfolioSnapshot.mode == "paper",
            )
            .order_by(models.PortfolioSnapshot.captured_at.asc())
        ).all()
        reset_audit = db.scalar(
            select(models.AuditLog).where(
                models.AuditLog.user_id == current.id,
                models.AuditLog.action == "PAPER_PORTFOLIO_RESET",
            )
        )

    assert len(snapshots) == 2
    assert snapshots[-1].available_balance == 992_495.0
    assert snapshots[-1].utilized_amount == 7_500.0
    assert reset_audit is not None
    assert reset_audit.audit_metadata["starting_balance"] == 1_000_000.0
