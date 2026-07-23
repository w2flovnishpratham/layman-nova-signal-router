"""Concurrent duplicate-exit safety.

Every exit source — session.exit_open, manual exit, runtime square-off, server
SL/TP from the monitor, strategy EXIT, opposite-signal exit — converges on
``paper_broker`` -> ``apply_paper_exit``. The incident booked a phantom second
SELL (false profit) when a duplicate exit ran against an already-closed trade.

The guard alone (reject when open_trade is absent) is only safe if the exit's
read-check-write is atomic; otherwise two concurrent exits can both read the
same open_trade before either writes. These tests drive real threads through
the real booking component and assert exactly one SELL / one closed trade / one
realized P&L is ever booked.
"""
from __future__ import annotations

import threading

from app.config import settings
from app.schemas.signal import NormalizedSignal
from app.services import (
    audit_logger,
    credential_vault,
    execution_router,
    paper_broker,
    paper_portfolio,
    state_store,
)
from app.services.paper_broker import PaperBroker


def _isolate_paper_runtime(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "paper_orders": log_dir / "paper_orders.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(paper_broker, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(paper_broker, "_market_is_open", lambda: True)


def _sell_payload(source: str) -> dict:
    return {
        "transactionType": "SELL",
        "quantity": 10,
        "tradingSymbol": "NIFTY TEST CE",
        "securityId": "123",
        "exchangeSegment": "NSE_FNO",
        "exitSource": source,  # stands in for the distinct upstream exit entry point
    }


def _exit_signal(signal_id: str, source: str) -> NormalizedSignal:
    return NormalizedSignal(
        payload_format="NOVA",
        secret="test",
        signal_id=signal_id,
        strategy_code="supertrend",
        action="EXIT",
        side="SELL",
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment="NSE_FNO",
        security_id="123",
        trading_symbol="NIFTY TEST CE",
        option_side="CE",
        strike=24200,
        expiry="2026-07-21",
        qty=10,
        order_type="MARKET",
        product_type="INTRADAY",
        source=source,
        raw_payload={},
    )


def _seed_open_paper_trade(monkeypatch, *, ltp: float = 100.0) -> None:
    monkeypatch.setattr(
        paper_broker,
        "get_quote_snapshot",
        lambda **_kwargs: {"ltp": ltp, "source": "DHAN_WEBSOCKET", "status": "FRESH", "stale": False},
    )
    state_store.set_engine_mode("paper")
    state_store.update_runtime_settings(paper_starting_balance=100000, paper_slippage_percent=0.10)
    paper_portfolio.reset_paper_portfolio(100000)
    entry = PaperBroker().place_order(
        client_id="client",
        access_token="token",
        payload={
            "transactionType": "BUY",
            "quantity": 10,
            "tradingSymbol": "NIFTY TEST CE",
            "securityId": "123",
            "exchangeSegment": "NSE_FNO",
        },
    )
    assert entry.success is True


def test_two_concurrent_exits_book_exactly_one_sell(tmp_path, monkeypatch):
    _isolate_paper_runtime(tmp_path, monkeypatch)
    _seed_open_paper_trade(monkeypatch, ltp=100.0)
    # Exit LTP differs from entry so a booked SELL produces a non-zero P&L.
    monkeypatch.setattr(
        paper_broker,
        "get_quote_snapshot",
        lambda **_kwargs: {"ltp": 110.0, "source": "DHAN_WEBSOCKET", "status": "FRESH", "stale": False},
    )

    broker = PaperBroker()
    start = threading.Barrier(2)
    results: list = []
    lock = threading.Lock()

    def fire(source: str) -> None:
        start.wait()  # release both threads into the exit at the same instant
        result = broker.place_order(client_id="client", access_token="token", payload=_sell_payload(source))
        with lock:
            results.append(result)

    threads = [
        threading.Thread(target=fire, args=("session_exit_open",)),
        threading.Thread(target=fire, args=("manual_square_off",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one exit books; the other is rejected because the open trade is gone.
    successes = [r for r in results if r.success]
    rejections = [r for r in results if not r.success]
    assert len(successes) == 1, [r.status for r in results]
    assert len(rejections) == 1 and rejections[0].status == "REJECTED"

    portfolio = paper_portfolio.get_paper_portfolio()
    assert portfolio.open_trade is None
    assert len(portfolio.closed_trades) == 1  # one SELL -> one closed trade, never two
    assert portfolio.utilized_amount == 0  # not double-decremented
    assert portfolio.closed_trades[0]["realized_pnl"] == portfolio.realized_pnl


def test_apply_paper_exit_atomic_under_concurrency_stress(tmp_path, monkeypatch):
    """Repeat the two-thread race enough times that a non-atomic (unlocked)
    read-check-write would double-book on at least one iteration."""
    _isolate_paper_runtime(tmp_path, monkeypatch)
    state_store.set_engine_mode("paper")
    state_store.update_runtime_settings(paper_starting_balance=100000, paper_slippage_percent=0.0)

    for _ in range(40):
        paper_portfolio.reset_paper_portfolio(100000)
        paper_portfolio.apply_paper_entry(qty=10, price=100.0, charges=0.0, symbol="NIFTY TEST CE", order_id="ENTRY")

        start = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def fire(order_id: str) -> None:
            start.wait()
            try:
                paper_portfolio.apply_paper_exit(
                    qty=10, exit_price=110.0, charges=0.0, symbol="NIFTY TEST CE", order_id=order_id
                )
                outcome = "booked"
            except paper_portfolio.NoOpenPaperTradeError:
                outcome = "rejected"
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=fire, args=(f"EXIT-{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert outcomes.count("booked") == 1
        assert outcomes.count("rejected") == 1
        portfolio = paper_portfolio.get_paper_portfolio()
        assert len(portfolio.closed_trades) == 1
        assert portfolio.realized_pnl == portfolio.closed_trades[0]["realized_pnl"]


def test_concurrent_exit_routes_persist_one_owner_and_send_one_order(tmp_path, monkeypatch):
    """Only the durable claim owner may cross the order-placement boundary."""
    _isolate_paper_runtime(tmp_path, monkeypatch)
    state_store.init_runtime_files()
    state_store.set_engine_mode("paper")
    state_store.update_runtime_settings(allow_exit=True)
    opened = state_store.set_open_position(
        state_store.stamp_new_open_position(
            {
                "strategy_code": "supertrend",
                "symbol": "NIFTY",
                "instrument_type": "OPTIDX",
                "exchange_segment": "NSE_FNO",
                "security_id": "123",
                "trading_symbol": "NIFTY TEST CE",
                "option_side": "CE",
                "qty": 10,
                "entry_price": 100.0,
                "order_type": "MARKET",
                "product_type": "INTRADAY",
            }
        )
    )
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)
    monkeypatch.setattr(execution_router, "_cancel_super_order_exit_legs", lambda _p: [])
    monkeypatch.setattr(execution_router, "refresh_wallet_snapshot", lambda **_k: {})
    monkeypatch.setattr(execution_router.position_operations, "on_position_closed", lambda *_a, **_k: None)

    order_started = threading.Event()
    release_order = threading.Event()
    call_count = {"value": 0}
    call_lock = threading.Lock()

    def place_once(*_args, **_kwargs):
        with call_lock:
            call_count["value"] += 1
        order_started.set()
        assert release_order.wait(timeout=5), "test did not release claimed order"
        return {
            "success": True,
            "status": "TRADED",
            "order_id": "PAPER-ONLY-EXIT",
            "avg_price": 110.0,
        }

    monkeypatch.setattr(execution_router, "_place_order", place_once)
    results: list[dict] = []
    results_lock = threading.Lock()

    def fire(signal_id: str, source: str) -> None:
        result = execution_router.route_exit_signal(_exit_signal(signal_id, source))
        with results_lock:
            results.append(result)

    owner = threading.Thread(target=fire, args=("EXIT-OWNER", "session_exit_open"))
    duplicate = threading.Thread(target=fire, args=("EXIT-DUPLICATE", "manual_square_off"))
    owner.start()
    assert order_started.wait(timeout=5), "owner never reached order boundary"
    duplicate.start()
    duplicate.join(timeout=5)
    release_order.set()
    owner.join(timeout=5)

    assert not owner.is_alive()
    assert not duplicate.is_alive()
    assert call_count["value"] == 1
    assert sum(result.get("status") == "ORDER_PLACED" for result in results) == 1
    assert sum(result.get("status") == "EXIT_IN_PROGRESS" for result in results) == 1

    flat = state_store.get_open_position()
    assert flat["has_open_position"] is False
    assert flat["last_closed_position_id"] == opened["position_id"]
    assert flat["last_exit_operation"]["status"] == "COMPLETED"
    assert flat["last_exit_operation"]["order_id"] == "PAPER-ONLY-EXIT"
