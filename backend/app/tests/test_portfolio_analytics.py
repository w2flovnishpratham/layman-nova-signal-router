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


def test_paper_exit_attributes_the_triggering_strategy_by_catalog_code(mu_db):
    """apply_paper_entry never captured any strategy identity before --
    every single AUTOMATED paper trade, for every strategy, fell back to
    "Unattributed automated" in Reports regardless of which strategy
    actually triggered it. Now the catalog code travels with the trade
    (see execution_router._tag_paper_origin) and resolves to the display
    name shown everywhere else (e.g. user_runs.strategy_name)."""
    from app.db import models
    from app.db.engine import session_scope
    from app.services import reports_service
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    user = make_user("paper-attribution@example.com")
    current = current_user_from_model(user)
    with session_scope() as db:
        db.add(models.StrategyCatalog(
            code="pvtext", display_name="Pivot Extension",
            owner_type="nova", visibility="nova_shared", status="active",
        ))
    closed_trade = {
        "symbol": "NIFTY 25000 CE",
        "qty": 75,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "entry_charges": 5.0,
        "exit_charges": 5.0,
        "realized_pnl": 740.0,
        "entry_order_id": "PAPER-ENTRY-2",
        "exit_order_id": "PAPER-EXIT-2",
        "opened_at": "2026-08-03T09:20:00+00:00",
        "closed_at": "2026-08-03T09:25:00+00:00",
        "origin": "AUTOMATED",
        "strategy_code": "pvtext",
    }
    with bind_user_execution_context(current):
        pa.persist_paper_trade(closed_trade)

    report = reports_service.build_report(
        user.id, start=date(2026, 8, 3), end=date(2026, 8, 3), mode="paper"
    )
    assert report["daily_sessions"][0]["strategy_mix"] == ["Pivot Extension"]


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


def test_paper_mode_dashboard_reads_the_virtual_wallet_not_the_live_log(mu_db, tmp_path, monkeypatch):
    """The gap this closes: /api/dashboard/portfolio had no mode=paper branch
    at all — build_portfolio_analytics() always read the live order log and
    hardcoded "mode": "live", so a Paper dashboard view was impossible."""
    from app.services import paper_portfolio, state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    portfolio_path = tmp_path / "paper_portfolio.json"
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", portfolio_path)
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", portfolio_path)
    current = current_user_from_model(make_user("paper-dashboard@example.com"))

    with bind_user_execution_context(current):
        paper_portfolio.reset_paper_portfolio(1_000_000)
        paper_portfolio.apply_paper_entry(
            qty=75, price=100.0, charges=5.0, symbol="NIFTY TEST CE", order_id="PAPER-DASH-ENTRY",
        )
        paper_portfolio.apply_paper_exit(
            qty=75, exit_price=110.0, charges=5.0, symbol="NIFTY TEST CE", order_id="PAPER-DASH-EXIT",
        )
        payload = pa.build_portfolio_analytics(mode="paper")

    assert payload["mode"] == "paper"
    assert payload["kpis"]["total_trades"] == 1
    assert payload["kpis"]["realized_pnl"] == 740.0
    assert payload["trades"][0]["symbol"] == "NIFTY TEST CE"
    assert payload["trades"][0]["option_side"] == "CE"
    assert payload["open_position"] is None
    assert payload["wallet"]["realized_pnl"] == 740.0
    # Sourced from the virtual wallet, not a real Dhan funds snapshot.
    assert payload["wallet"]["available_balance"] == 1_000_740.0  # 1,000,000 + realized 740
    assert payload["wallet"]["sod_limit"] is None


def test_live_never_falls_back_to_the_paper_starting_balance(mu_db, monkeypatch):
    """Regression test: the Live dashboard must never show the Paper
    virtual-wallet starting balance (₹10,00,000) as its own equity, whether
    Dhan is disconnected, credentials are invalid, or nothing was ever fetched."""
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    monkeypatch.setattr(pa, "_live_wallet_snapshot", lambda force=False: {})
    current = current_user_from_model(make_user("live-no-fallback@example.com"))
    with bind_user_execution_context(current):
        payload = pa.build_portfolio_analytics(mode="live", persist=False)

    assert payload["wallet"]["available_balance"] is None
    assert payload["wallet"]["equity"] != 1_000_000.0
    assert payload["wallet"]["balance_source"] == "none"
    assert payload["funds_connected"] is False


def test_live_wallet_falls_back_to_last_known_snapshot_when_disconnected(mu_db, monkeypatch):
    from app.db import models
    from app.db.engine import session_scope
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    current = current_user_from_model(make_user("live-last-known@example.com"))
    with session_scope() as db:
        db.add(
            models.PortfolioSnapshot(
                user_id=current.id, mode="live", starting_balance=500_000.0,
                available_balance=480_000.0, utilized_amount=20_000.0, equity=500_000.0,
                realized_pnl=0.0, trade_count=0, fetch_status="ok",
            )
        )

    monkeypatch.setattr(pa, "_live_wallet_snapshot", lambda force=False: {"success": False, "message": "stale"})
    with bind_user_execution_context(current):
        payload = pa.build_portfolio_analytics(mode="live", persist=False)

    assert payload["wallet"]["balance_source"] == "last_known"
    assert payload["wallet"]["available_balance"] == 480_000.0
    assert payload["wallet"]["equity"] == 500_000.0
    assert payload["wallet"]["last_known_at"] is not None


def test_paper_entry_and_exit_thread_origin_into_the_closed_trade(mu_db, tmp_path, monkeypatch):
    from app.services import paper_portfolio, state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    portfolio_path = tmp_path / "paper_portfolio.json"
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", portfolio_path)
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", portfolio_path)
    current = current_user_from_model(make_user("paper-origin@example.com"))

    with bind_user_execution_context(current):
        paper_portfolio.reset_paper_portfolio(1_000_000)
        paper_portfolio.apply_paper_entry(
            qty=75, price=100.0, charges=5.0, symbol="NIFTY TEST CE",
            order_id="PAPER-ORIGIN-ENTRY", origin="MANUAL",
        )
        portfolio = paper_portfolio.apply_paper_exit(
            qty=75, exit_price=110.0, charges=5.0, symbol="NIFTY TEST CE", order_id="PAPER-ORIGIN-EXIT",
        )

    assert portfolio.closed_trades[-1]["origin"] == "MANUAL"


def test_trade_origin_filter_all_automated_manual(mu_db, tmp_path, monkeypatch):
    from app.services import paper_portfolio, state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    portfolio_path = tmp_path / "paper_portfolio.json"
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", portfolio_path)
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", portfolio_path)
    current = current_user_from_model(make_user("paper-filter@example.com"))

    with bind_user_execution_context(current):
        paper_portfolio.reset_paper_portfolio(1_000_000)
        paper_portfolio.apply_paper_entry(
            qty=75, price=100.0, charges=0.0, symbol="NIFTY MANUAL CE",
            order_id="PF-MANUAL-ENTRY", origin="MANUAL",
        )
        paper_portfolio.apply_paper_exit(
            qty=75, exit_price=110.0, charges=0.0, symbol="NIFTY MANUAL CE", order_id="PF-MANUAL-EXIT",
        )
        paper_portfolio.apply_paper_entry(
            qty=75, price=100.0, charges=0.0, symbol="NIFTY AUTO CE",
            order_id="PF-AUTO-ENTRY", origin="BUILT_IN_STRATEGY",
        )
        paper_portfolio.apply_paper_exit(
            qty=75, exit_price=120.0, charges=0.0, symbol="NIFTY AUTO CE", order_id="PF-AUTO-EXIT",
        )

        all_trades = pa.build_portfolio_analytics(mode="paper", persist=False)
        automated = pa.build_portfolio_analytics(mode="paper", persist=False, trade_origin="automated")
        manual = pa.build_portfolio_analytics(mode="paper", persist=False, trade_origin="manual")

    assert all_trades["kpis"]["total_trades"] == 2
    assert automated["kpis"]["total_trades"] == 1
    assert automated["trades"][0]["symbol"] == "NIFTY AUTO CE"
    assert manual["kpis"]["total_trades"] == 1
    assert manual["trades"][0]["symbol"] == "NIFTY MANUAL CE"
    # The real account balance is never filtered — same in every view.
    assert all_trades["wallet"]["available_balance"] == automated["wallet"]["available_balance"] == manual["wallet"]["available_balance"]


def test_live_pair_round_trips_derives_origin_from_the_entry_leg():
    entry = {
        "phase": "after_response", "success": True, "status": "TRADED", "mode": "live",
        "order_id": "REAL-ORIGIN-1", "normalized_action": "ENTRY", "normalized_qty": 50,
        "avg_price": 100.0, "normalized_option_side": "CE", "source": "built_in_strategy",
        "timestamp": "2026-06-17T09:21:00+00:00",
    }
    exit_leg = {
        **entry, "order_id": "REAL-ORIGIN-2", "normalized_action": "EXIT", "avg_price": 110.0,
        "source": "manual_panel",  # exit leg's source must NOT override the entry's origin
        "timestamp": "2026-06-17T09:25:00+00:00",
    }
    trades, _ = pa._pair_round_trips([entry, exit_leg])
    assert trades[0]["origin"] == "BUILT_IN_STRATEGY"


def test_live_exit_fill_persists_a_row_without_needing_a_dashboard_load(mu_db, tmp_path, monkeypatch):
    """The gap this closes: live PortfolioTrade rows were only ever written as
    a side effect of GET /api/dashboard/portfolio (build_portfolio_analytics),
    wrapped in a bare except. If nobody opened the dashboard, realized P&L for
    a live trade never reached the DB. log_order_event now triggers the same
    (idempotent, cheap) persistence right when the EXIT fill is logged."""
    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope
    from app.services import audit_logger, state_store
    from app.services.execution_context import bind_user_execution_context
    from app.services.user_context import current_user_from_model

    log_root = tmp_path / "logs"
    log_files = {
        "webhook": log_root / "webhook_events.jsonl",
        "order": log_root / "order_events.jsonl",
        "audit": log_root / "audit_events.jsonl",
        "error": log_root / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "get_engine_mode", lambda legacy_fallback=True: "live")

    current = current_user_from_model(make_user("live-exit-persist@example.com"))
    with bind_user_execution_context(current):
        audit_logger.log_order_event({
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "order_id": "REAL-ENTRY-1",
            "normalized_action": "ENTRY",
            "normalized_qty": 75,
            "avg_price": 100.0,
            "normalized_option_side": "CE",
            "trading_symbol": "NIFTY TEST CE",
            "timestamp": "2026-07-27T09:20:00+00:00",
        })
        audit_logger.log_order_event({
            "phase": "after_response",
            "success": True,
            "status": "TRADED",
            "order_id": "REAL-EXIT-1",
            "normalized_action": "EXIT",
            "normalized_qty": 75,
            "avg_price": 110.0,
            "normalized_option_side": "CE",
            "trading_symbol": "NIFTY TEST CE",
            "timestamp": "2026-07-27T09:25:00+00:00",
        })

    with session_scope() as db:
        trade = db.scalar(
            select(models.PortfolioTrade).where(
                models.PortfolioTrade.user_id == current.id,
                models.PortfolioTrade.mode == "live",
            )
        )
    assert trade is not None, "a live EXIT fill must persist without any dashboard GET"
    assert trade.exit_order_id == "REAL-EXIT-1"
    assert trade.realized_pnl == 750.0  # (110 - 100) * 75
