from __future__ import annotations

from datetime import date
import uuid

from app.routers.market import _build_nifty_markers, nifty_markers
from app.services.execution_context import bind_user_execution_context
from app.services.user_context import CurrentUser


TRADING_DATE = date(2026, 7, 15)


def fill(order_id: str, action: str, timestamp: str, **overrides):
    return {
        "phase": "after_response",
        "success": True,
        "status": "TRADED",
        "mode": "paper",
        "order_id": order_id,
        "timestamp": timestamp,
        "normalized_action": action,
        "normalized_option_side": "CE",
        "trading_symbol": "NIFTY-TEST-CE",
        "avg_price": 128.5,
        "normalized_qty": 75,
        **overrides,
    }


def test_markers_include_only_confirmed_unique_fills_and_hide_broker_identity():
    entry = fill("PAPER-ENTRY", "ENTRY", "2026-07-15T09:22:35+05:30")
    exit_fill = fill("PAPER-EXIT", "EXIT", "2026-07-15T09:24:00+05:30", exit_reason="TP")
    rows = _build_nifty_markers([
        entry,
        entry,
        exit_fill,
        fill("PAPER-HOLD", "HOLD", "2026-07-15T09:25:00+05:30"),
        fill("PAPER-FAILED", "ENTRY", "2026-07-15T09:26:00+05:30", success=False, status="REJECTED"),
        fill("PAPER-PENDING", "ENTRY", "2026-07-15T09:26:30+05:30", status="PENDING", avg_price=None),
        fill("PAPER-OTHER", "ENTRY", "2026-07-15T09:27:00+05:30", mode="live"),
    ], mode="paper", trading_date=TRADING_DATE, limit=200)

    assert [row["label"] for row in rows] == ["BUY CE", "TARGET"]
    assert len({row["id"] for row in rows}) == 2
    assert rows[0]["execution_price"] == 128.5
    assert rows[0]["quantity"] == 75
    assert rows[0]["price"] is None
    assert all("order_id" not in row and "token" not in row and "credential" not in row for row in rows)


def test_exit_marker_exposes_only_recorded_pnl():
    rows = _build_nifty_markers([
        fill("PAPER-EXIT-PNL", "EXIT", "2026-07-15T09:24:00+05:30", pnl=2307),
    ], mode="paper", trading_date=TRADING_DATE, limit=200)

    assert rows[0]["pnl"] == 2307


def test_exit_reason_mapping_and_multiple_markers_preserve_stable_order():
    timestamp = "2026-07-15T10:08:20+05:30"
    rows = _build_nifty_markers([
        fill("PAPER-SL", "EXIT", timestamp, exit_reason="SL"),
        fill("PAPER-EOD", "EXIT", timestamp, exit_reason="EOD_FLATTEN"),
        fill("PAPER-REV", "EXIT", timestamp, reversal_exit=True),
        fill("PAPER-PE", "ENTRY", timestamp, normalized_option_side="PE"),
    ], mode="paper", trading_date=TRADING_DATE, limit=200)

    assert sorted(row["label"] for row in rows) == ["BUY PE", "EOD EXIT", "REV EXIT", "SL EXIT"]
    assert [row["label"] for row in rows] == ["SL EXIT", "EOD EXIT", "REV EXIT", "BUY PE"]


def test_markers_are_date_bounded():
    rows = _build_nifty_markers([
        fill("PAPER-OLD", "ENTRY", "2026-07-14T09:22:35+05:30"),
    ], mode="paper", trading_date=TRADING_DATE, limit=200)

    assert rows == []


def test_marker_endpoint_reads_only_the_bound_users_log(tmp_path, monkeypatch):
    from app.services import audit_logger, market_chart_service, state_store, user_context

    log_root = tmp_path / "logs"
    monkeypatch.setattr(state_store, "RUNTIME_LOG_DIR", log_root)
    monkeypatch.setattr(user_context, "RUNTIME_LOG_DIR", log_root)
    monkeypatch.setattr(audit_logger, "LOG_FILES", {**audit_logger.LOG_FILES, "order": log_root / "order_events.jsonl"})
    monkeypatch.setattr(market_chart_service, "chart_trading_date_ist", lambda: TRADING_DATE)
    user_a = CurrentUser(id=uuid.uuid4(), email="a@example.com")
    user_b = CurrentUser(id=uuid.uuid4(), email="b@example.com")

    with bind_user_execution_context(user_a):
        audit_logger.log_order_event(fill("PAPER-A", "ENTRY", "2026-07-15T09:22:35+05:30", trading_symbol="NIFTY-A-CE"))
    with bind_user_execution_context(user_b):
        audit_logger.log_order_event(fill("PAPER-B", "ENTRY", "2026-07-15T09:22:35+05:30", trading_symbol="NIFTY-B-CE"))
    with bind_user_execution_context(user_a):
        payload = nifty_markers(mode="paper", limit=200)

    assert [marker["contract"] for marker in payload["markers"]] == ["NIFTY-A-CE"]
