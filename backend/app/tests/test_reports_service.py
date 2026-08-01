# ruff: noqa: F811
"""Reports: real aggregates over closed trades, honest about zero denominators."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.db import models
from app.db.engine import session_scope
from app.services import reports_service
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401

# Pinned, never "now": an earlier version captured today's date at import and
# created trades relative to the clock, so a suite that ran across midnight put
# the trades on the next IST day and the report range excluded them.
TRADE_DAY = date(2026, 7, 24)
BASE = datetime(2026, 7, 24, 11, 0, tzinfo=reports_service.IST)


def _trade(
    user_id,
    pnl: float,
    *,
    minutes: int = 0,
    mode: str = "paper",
    symbol: str = "NIFTY CE",
    origin: str | None = None,
    strategy: str | None = "supertrend",
    closed_at: datetime | None = None,
):
    # `minutes` orders trades within the pinned day; later values close earlier.
    closed = BASE - timedelta(minutes=minutes)
    with session_scope() as db:
        db.add(models.PortfolioTrade(
            user_id=user_id,
            mode=mode,
            strategy_name=strategy,
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
            closed_at=closed_at or closed,
            origin=origin,
        ))


def _report(user_id, **kwargs):
    return reports_service.build_report(user_id, start=TRADE_DAY, end=TRADE_DAY, **kwargs)


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
    assert lines[0] == "NOVA REPORT"
    assert "CLOSED TRADES" in lines
    assert ",".join(reports_service.CSV_COLUMNS) in lines
    assert "300.0" in csv_text and "-120.0" in csv_text
    for forbidden in ("token", "secret", "client_id", "password"):
        assert forbidden not in csv_text.lower()


def test_an_empty_csv_still_carries_its_header(mu_db):
    user = make_user("report-csv-empty@example.com")
    csv_text = reports_service.report_csv(_report(user.id))
    assert "NOVA REPORT" in csv_text
    assert ",".join(reports_service.CSV_COLUMNS) in csv_text


def test_trade_origin_filter_all_automated_manual(mu_db):
    user = make_user("report-origin@example.com")
    _trade(user.id, 300.0, minutes=30, origin="MANUAL")
    _trade(user.id, 200.0, minutes=20, origin="BUILT_IN_STRATEGY")
    _trade(user.id, 100.0, minutes=10, origin="TRADINGVIEW_WEBHOOK")

    all_trades = _report(user.id, trade_origin="all")
    automated = _report(user.id, trade_origin="automated")
    manual = _report(user.id, trade_origin="manual")

    assert all_trades["totals"]["trades"] == 3
    assert automated["totals"]["trades"] == 2
    assert automated["totals"]["net_pnl"] == 300.0
    assert manual["totals"]["trades"] == 1
    assert manual["totals"]["net_pnl"] == 300.0
    assert manual["trade_origin"] == "manual"


def test_default_trade_origin_is_all_and_export_honours_the_filter(mu_db):
    user = make_user("report-origin-default@example.com")
    _trade(user.id, 50.0, minutes=5, origin="MANUAL")
    _trade(user.id, 75.0, minutes=1, origin="USER_STRATEGY")

    assert _report(user.id)["trade_origin"] == "all"
    assert _report(user.id)["totals"]["trades"] == 2

    csv_text = reports_service.report_csv(_report(user.id, trade_origin="manual"))
    assert "Manual Orders" in csv_text
    assert "BUILT_IN_STRATEGY" not in csv_text


def test_daily_calendar_and_strategy_totals_reconcile(mu_db):
    user = make_user("report-reconcile@example.com")
    _trade(user.id, 300.0, minutes=30, origin="MANUAL", strategy=None)
    _trade(user.id, -100.0, minutes=20, origin="BUILT_IN_STRATEGY", strategy="NOVA Supertrend")
    _trade(user.id, 0.0, minutes=10, origin="USER_STRATEGY", strategy="Flat test")

    report = _report(user.id)
    assert report["totals"]["sessions"] == 1
    assert report["daily_sessions"][0]["net_pnl"] == report["totals"]["net_pnl"] == 200.0
    assert sum(row["realized_pnl"] for row in report["by_strategy"]) == 200.0
    assert "Manual Orders" in report["daily_sessions"][0]["strategy_mix"]
    manual = next(row for row in report["by_strategy"] if row["display_name"] == "Manual Orders")
    assert manual["realized_pnl"] == 300.0
    assert report["win_rate"]["value"] == 50.0  # scratch is excluded from the denominator


def test_ist_midnight_boundary_assigns_the_correct_session(mu_db):
    user = make_user("report-ist@example.com")
    # 18:45 UTC is 00:15 on 25 July in India.
    instant = datetime(2026, 7, 24, 18, 45, tzinfo=reports_service.ZoneInfo("UTC"))
    _trade(user.id, 125.0, origin="MANUAL", strategy=None, closed_at=instant)
    report = reports_service.build_report(
        user.id,
        start=date(2026, 7, 25),
        end=date(2026, 7, 25),
        mode="paper",
    )
    assert report["daily_sessions"][0]["date"] == "2026-07-25"
    assert report["trades"][0]["trading_date_ist"] == "2026-07-25"


def test_pdf_export_is_a_real_filter_scoped_pdf(mu_db):
    user = make_user("report-pdf@example.com")
    _trade(user.id, 75.0, origin="MANUAL", strategy=None)
    pdf = reports_service.report_pdf(_report(user.id, trade_origin="manual"))
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000
