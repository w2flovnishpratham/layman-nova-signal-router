from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.state_store import (
    _LOCK,
    PAPER_PORTFOLIO_FILE,
    get_runtime_settings,
    record_losing_exit,
    utc_now,
)


_IST = ZoneInfo("Asia/Kolkata")


@dataclass
class PaperPortfolio:
    starting_balance: float
    available_balance: float
    utilized_amount: float = 0.0
    realized_pnl: float = 0.0
    session_pnl: float = 0.0
    session_start_balance: float = 0.0
    session_start_date_ist: str = ""
    closed_trades: list[dict[str, Any]] = field(default_factory=list)
    open_trade: dict[str, Any] | None = None
    last_checked_at: str = ""


def _today() -> str:
    return datetime.now(_IST).date().isoformat()


def _default_balance() -> float:
    return max(float(get_runtime_settings().get("paper_starting_balance") or 100000.0), 0.0)


def _default() -> PaperPortfolio:
    balance = _default_balance()
    return PaperPortfolio(
        starting_balance=balance,
        available_balance=balance,
        session_start_balance=balance,
        session_start_date_ist=_today(),
        last_checked_at=utc_now(),
    )


def _write(portfolio: PaperPortfolio) -> PaperPortfolio:
    from app.services.state_store import _atomic_write_json

    portfolio.last_checked_at = utc_now()
    _atomic_write_json(PAPER_PORTFOLIO_FILE, asdict(portfolio))
    return portfolio


def get_paper_portfolio() -> PaperPortfolio:
    from app.services.state_store import _read_json

    data = _read_json(PAPER_PORTFOLIO_FILE, lambda: asdict(_default()))
    defaults = asdict(_default())
    defaults.update(data)
    portfolio = PaperPortfolio(**defaults)
    if portfolio.session_start_date_ist != _today():
        portfolio.session_start_date_ist = _today()
        portfolio.session_start_balance = portfolio.available_balance
        portfolio.session_pnl = 0.0
        return _write(portfolio)
    return portfolio


def reset_paper_portfolio(starting_balance: float | None = None) -> PaperPortfolio:
    balance = max(float(starting_balance if starting_balance is not None else _default_balance()), 0.0)
    return _write(
        PaperPortfolio(
            starting_balance=balance,
            available_balance=balance,
            session_start_balance=balance,
            session_start_date_ist=_today(),
        )
    )


def apply_paper_entry(*, qty: int, price: float, charges: float, symbol: str, order_id: str) -> PaperPortfolio:
    # Serialize read-check-write under the shared runtime lock so two concurrent
    # mutations can't both read the same portfolio and double-book.
    with _LOCK:
        portfolio = get_paper_portfolio()
        cost = round(qty * price, 2)
        debit = round(cost + charges, 2)
        if portfolio.available_balance < debit:
            raise ValueError("Paper order rejected: insufficient virtual balance.")
        portfolio.available_balance = round(portfolio.available_balance - debit, 2)
        portfolio.utilized_amount = round(portfolio.utilized_amount + cost, 2)
        portfolio.open_trade = {
            "symbol": symbol,
            "qty": qty,
            "entry_price": price,
            "entry_charges": charges,
            "entry_value": cost,
            "entry_order_id": order_id,
            "opened_at": utc_now(),
        }
        portfolio.session_pnl = round(portfolio.available_balance + portfolio.utilized_amount - portfolio.session_start_balance, 2)
        return _write(portfolio)


def resize_paper_open_trade_quantity(*, qty: int) -> PaperPortfolio:
    portfolio = get_paper_portfolio()
    trade = dict(portfolio.open_trade or {})
    if not trade:
        raise ValueError("Paper quantity update rejected: no paper open trade exists.")

    new_qty = max(int(qty), 0)
    if new_qty <= 0:
        raise ValueError("Paper quantity update rejected: quantity must be positive.")
    old_qty = max(int(trade.get("qty") or 0), 0)
    entry_price = float(trade.get("entry_price") or 0)
    old_entry_value = float(trade.get("entry_value") or old_qty * entry_price)
    new_entry_value = round(new_qty * entry_price, 2)
    value_delta = round(new_entry_value - old_entry_value, 2)
    if value_delta > portfolio.available_balance:
        raise ValueError("Paper quantity update rejected: insufficient virtual balance.")

    portfolio.available_balance = round(portfolio.available_balance - value_delta, 2)
    portfolio.utilized_amount = round(max(0.0, portfolio.utilized_amount + value_delta), 2)
    portfolio.session_pnl = round(portfolio.available_balance + portfolio.utilized_amount - portfolio.session_start_balance, 2)
    trade.update(
        {
            "qty": new_qty,
            "entry_value": new_entry_value,
            "quantity_adjusted_at": utc_now(),
        }
    )
    portfolio.open_trade = trade
    return _write(portfolio)


class NoOpenPaperTradeError(ValueError):
    """Raised when a Paper exit is attempted with no matching open trade.

    Without this guard, apply_paper_exit computed entry_value=0 for an absent
    open_trade and booked exit_value-charges as false realized profit — the
    root cause of the duplicate-exit incident's phantom second SELL.
    """


def apply_paper_exit(*, qty: int, exit_price: float, charges: float, symbol: str, order_id: str) -> PaperPortfolio:
    # Serialize read-check-write: without the lock two concurrent exits (e.g.
    # monitor SL/TP + manual/session/square-off) could both read the same
    # open_trade and each book a SELL — the duplicate-exit false-profit race.
    # Holding the lock across the guard makes the second exit see open_trade=None.
    with _LOCK:
        portfolio = get_paper_portfolio()
        if not isinstance(portfolio.open_trade, dict) or not portfolio.open_trade:
            raise NoOpenPaperTradeError("Paper exit rejected: no open trade exists.")
        trade = dict(portfolio.open_trade)
        if qty <= 0 or int(qty) != int(trade.get("qty") or 0):
            raise NoOpenPaperTradeError(
                f"Paper exit rejected: quantity {qty} does not match open trade quantity {trade.get('qty')}."
            )
        entry_price = float(trade.get("entry_price") or 0)
        entry_charges = float(trade.get("entry_charges") or 0)
        entry_value = float(trade.get("entry_value") or qty * entry_price)
        exit_value = round(qty * exit_price, 2)
        realized = round(exit_value - entry_value - entry_charges - charges, 2)
        portfolio.available_balance = round(portfolio.available_balance + exit_value - charges, 2)
        portfolio.utilized_amount = round(max(0.0, portfolio.utilized_amount - entry_value), 2)
        portfolio.realized_pnl = round(portfolio.realized_pnl + realized, 2)
        portfolio.session_pnl = round(portfolio.available_balance + portfolio.utilized_amount - portfolio.session_start_balance, 2)
        portfolio.closed_trades = (
            portfolio.closed_trades
            + [{
                **trade,
                "symbol": symbol or trade.get("symbol"),
                "qty": qty,
                "exit_price": exit_price,
                "exit_charges": charges,
                "realized_pnl": realized,
                "exit_order_id": order_id,
                "closed_at": utc_now(),
            }]
        )[-200:]
        portfolio.open_trade = None
        written = _write(portfolio)
    # Outside the portfolio lock: the cooldown stamp lives in a different file,
    # and failing to record it must never roll back an exit that was booked.
    record_losing_exit(realized)
    return written


def paper_wallet_snapshot() -> dict[str, Any]:
    portfolio = get_paper_portfolio()
    return {
        "success": True,
        "message": "Paper portfolio ready.",
        "client_id": None,
        "available_balance": portfolio.available_balance,
        "withdrawable_balance": portfolio.available_balance,
        "utilized_amount": portfolio.utilized_amount,
        "sod_limit": portfolio.starting_balance,
        "collateral_amount": 0.0,
        "blocked_payout_amount": 0.0,
        "session_start_balance": portfolio.session_start_balance,
        "session_pnl": portfolio.session_pnl,
        "realized_pnl": portfolio.realized_pnl,
        "session_start_date_ist": portfolio.session_start_date_ist,
        "last_checked_at": portfolio.last_checked_at,
        "raw_response": {"mode": "paper"},
    }
