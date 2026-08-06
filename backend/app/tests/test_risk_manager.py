from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services import risk_manager
# conftest.py's autouse market_hours_open_by_default fixture overwrites the
# risk_manager module's _market_is_open attribute with an always-True lambda
# for every other test's determinism. Capturing the real function object here
# at import time bypasses that -- it's the same function either way, Python
# resolves its `datetime` global dynamically at call time, so freezing
# risk_manager.datetime below still works even called through this reference.
from app.services.risk_manager import _market_is_open as real_market_is_open

_IST = ZoneInfo("Asia/Kolkata")


def _freeze(monkeypatch, wall_clock: datetime) -> None:
    frozen = type("_FrozenDatetime", (datetime,), {"_frozen": wall_clock})
    frozen.now = classmethod(lambda cls, tz=None: cls._frozen.astimezone(tz) if tz else cls._frozen)
    monkeypatch.setattr(risk_manager, "datetime", frozen)


def test_market_open_extends_to_1540_after_cas_change(monkeypatch):
    # F&O close moved 15:30 -> 15:40 IST with NSE's Closing Auction Session
    # change (2026-08-03) -- this window used to wrongly block real entries here.
    _freeze(monkeypatch, datetime(2026, 8, 10, 15, 35, tzinfo=_IST))  # Monday
    assert real_market_is_open() is True


def test_market_closed_after_1540(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 8, 10, 15, 41, tzinfo=_IST))
    assert real_market_is_open() is False


def test_market_closed_before_915(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 8, 10, 9, 14, tzinfo=_IST))
    assert real_market_is_open() is False


def test_market_closed_on_weekend(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 8, 8, 12, 0, tzinfo=_IST))  # Saturday
    assert real_market_is_open() is False
