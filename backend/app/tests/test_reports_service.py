# ruff: noqa: F811
"""Reports: real aggregates over closed trades, honest about zero denominators."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.db import models
from app.db.engine import session_scope
from app.services import reports_service
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401

TODAY = datetime.now(reports_service.IST).date()


def _trade(user_id, pnl: float, *, minutes: int = 0, mode: str = "paper", symbol: str = "NIFTY CE"):
    closed = datetime.now(reports_service.IST) - timedelta(minutes=minutes)
    with session_scope() as db:
        db.add(models.PortfolioTrade(
            user_id=user_id,
            mode=mode,
            strategy_name="supertrend",
            symbol=symbol,
            option_side="CE",
            qty=75,
            entry_price=100.0,
            exit_price=100.0 + pnl / 75,
            entry_charges=0.0,
            exit_charges=0.0,
            gross_pnl=pnl,
            realized_pnl=pnl,
            exit_order_id=f"x-{user_id}-{minutes}-{pnl}",
            closed_at=closed,
        ))


def _report(user_id, **kwargs):
    return reports_service.build_report(user_id, start=TODAY, end=TODAY, **kwargs)


def test_an_empty_period_reports_unknown_rather_than_zero(mu_db):
    user = make_user("report-empty@example.com")
    result = _report(user.id)
    assert result["totals"]["trades"] == 0
    assert result["win_rate"]["value"] is None
    assert "No closed trades" in result["win_rate"]["reason"]
    assert result["profit_factor"]["value"] is None
    assert result["max_drawdown"]["value"] is None


def test_winning_and_losing_metrics(mu_db):
    user = make_user("report-mix@example.com")
    _trade(user.id, 300.0, minutes=30)
    _trade(user.id, -100.0, minutes=20)
    _trade(user.id, 200.0, minutes=10)

    result = _report(user.id)
    totals = result["totals"]
    assert totals["trades"] == 3
    assert totals["winning_trades"] == 2
    assert totals["losing_trades"] == 1
    assert totals["gross_profit"] == 500.0
    assert totals["gross_loss"] == 100.0
    assert totals["net_pnl"] == 400.0
    assert result["win_rate"]["value"] == 66.7
    assert result["average_winner"]["value"] == 250.0
    assert result["average_loser"]["value"] == -100.0
    assert result["profit_factor"]["value"] == 5.0


def test_profit_factor_is_undefined_without_a_losing_trade(mu_db):
    user = make_user("report-nolose@example.com")
    _trade(user.id, 150.0, minutes=5)
    result = _report(user.id)
    assert result["profit_factor"]["value"] is None
    assert "Undefined" in result["profit_factor"]["reason"]
    assert result["average_loser"]["value"] is None


def test_max_drawdown_is_measured_on_closed_trade_equity(mu_db):
    user = make_user("report-dd@example.com")
    _trade(user.id, 500.0, minutes=40)   # equity 500 (peak)
    _trade(user.id, -300.0, minutes=30)  # equity 200, drawdown 300
    _trade(user.id, -100.0, minutes=20)  # equity 100, drawdown 400
    _trade(user.id, 250.0, minutes=10)   # equity 350

    drawdown = _report(user.id)["max_drawdown"]
    assert drawdown["value"] == 400.0
    # Named honestly: NOVA stores no intraday equity curve.
    assert drawdown["basis"] == "closed_trade_equity"


def test_a_scratch_trade_counts_as_neither_win_nor_loss(mu_db):
    user = make_user("report-scratch@example.com")
    _trade(user.id, 0.0, minutes=5)
    totals = _report(user.id)["totals"]
    assert (totals["winning_trades"], totals["losing_trades"], totals["scratch_trades"]) == (0, 0, 1)


def test_one_owner_never_sees_another(mu_db):
    alice = make_user("report-alice@example.com")
    bob = make_user("report-bob@example.com")
    _trade(alice.id, 100.0, minutes=10)
    _trade(bob.id, 999.0, minutes=10)
    assert _report(alice.id)["totals"]["net_pnl"] == 100.0


def test_live_trades_do_not_leak_into_the_paper_report(mu_db):
    user = make_user("report-mode@example.com")
    _trade(user.id, 100.0, minutes=10, mode="paper")
    _trade(user.id, 700.0, minutes=9, mode="live")
    assert _report(user.id, mode="paper")["totals"]["net_pnl"] == 100.0


def test_csv_contains_the_trades_and_no_secret_columns(mu_db):
    user = make_user("report-csv@example.com")
    _trade(user.id, 300.0, minutes=15)
    _trade(user.id, -120.0, minutes=5)

    csv_text = reports_service.report_csv(_report(user.id))
    lines = csv_text.strip().splitlines()
    assert lines[0] == ",".join(reports_service.CSV_COLUMNS)
    assert len(lines) == 3
    assert "300.0" in csv_text and "-120.0" in csv_text
    for forbidden in ("token", "secret", "client_id", "password"):
        assert forbidden not in csv_text.lower()


def test_an_empty_csv_still_carries_its_header(mu_db):
    user = make_user("report-csv-empty@example.com")
    csv_text = reports_service.report_csv(_report(user.id))
    assert csv_text.strip() == ",".join(reports_service.CSV_COLUMNS)
