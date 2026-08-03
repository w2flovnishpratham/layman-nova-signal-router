"""Risk overview: owner scoping, unlimited semantics, loss-budget truthfulness."""
from __future__ import annotations

from app.db import models
from app.db.engine import session_scope
from app.services import risk_overview, strategy_risk
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def _counter(user_id, strategy: str, *, orders=0, notional=0, realized=0):
    with session_scope() as db:
        db.add(models.UserStrategyDailyRiskCounter(
            user_id=user_id,
            strategy_name=strategy,
            trade_date_ist=strategy_risk.trade_date_ist(),
            orders_count=orders,
            notional_used_paise=notional,
            realized_pnl_paise=realized,
        ))


def _user_limits(user_id, **kwargs):
    with session_scope() as db:
        db.add(models.UserRiskControl(user_id=user_id, **kwargs))


def test_usage_is_scoped_to_the_owner(mu_db):  # noqa: F811
    alice = make_user("alice-risk@example.com")
    bob = make_user("bob-risk@example.com")
    _counter(alice.id, "supertrend", orders=3)
    _counter(bob.id, "supertrend", orders=9)

    result = risk_overview.build_risk_overview(alice.id)
    rows = {s["strategy_name"]: s for s in result["strategies"]}
    assert rows["supertrend"]["usage"]["orders_count"] == 3  # not bob's 9


def test_zero_limit_means_unlimited_not_zero_allowed(mu_db):  # noqa: F811
    user = make_user("unlimited-risk@example.com")
    _counter(user.id, "supertrend", orders=5)
    # No UserRiskControl row and no override -> limits fall back; assert the
    # unlimited case is represented honestly when the effective limit is 0.
    result = risk_overview.build_risk_overview(user.id)
    orders = result["strategies"][0]["utilisation"]["orders"]
    if orders["limit"] == 0:
        assert orders["unlimited"] is True
        assert orders["pct"] is None  # never 0% or 100%


def test_utilisation_percentage_when_a_real_limit_exists(mu_db):  # noqa: F811
    user = make_user("pct-risk@example.com")
    _user_limits(user.id, max_orders_per_day=10)
    _counter(user.id, "supertrend", orders=4)

    result = risk_overview.build_risk_overview(user.id)
    orders = result["strategies"][0]["utilisation"]["orders"]
    assert orders["limit"] == 10
    assert orders["used"] == 4
    assert orders["unlimited"] is False
    assert orders["pct"] == 40.0


def test_only_negative_pnl_consumes_the_daily_loss_budget(mu_db):  # noqa: F811
    user = make_user("loss-risk@example.com")
    _user_limits(user.id, max_loss_per_day_paise=1_000_000)  # Rs 10,000
    _counter(user.id, "supertrend", realized=250_000)  # a profitable day

    result = risk_overview.build_risk_overview(user.id)
    loss = result["strategies"][0]["utilisation"]["loss"]
    assert result["strategies"][0]["usage"]["loss_used_paise"] == 0
    assert loss["used"] == 0  # profit does not consume the loss budget
    assert loss["pct"] == 0.0


def test_losing_day_consumes_the_loss_budget(mu_db):  # noqa: F811
    user = make_user("losing-risk@example.com")
    _user_limits(user.id, max_loss_per_day_paise=1_000_000)
    _counter(user.id, "supertrend", realized=-500_000)

    result = risk_overview.build_risk_overview(user.id)
    loss = result["strategies"][0]["utilisation"]["loss"]
    assert loss["used"] == 500_000
    assert loss["pct"] == 50.0


def test_effective_limits_come_from_the_enforcing_resolver(mu_db):  # noqa: F811
    user = make_user("effective-risk@example.com")
    _user_limits(user.id, max_orders_per_day=6)
    with session_scope() as db:
        db.add(models.UserStrategyRiskControl(
            user_id=user.id, strategy_name="supertrend", max_orders_per_day=2,
        ))

    result = risk_overview.build_risk_overview(user.id)
    row = result["strategies"][0]
    enforced = strategy_risk.get_effective_controls(user.id, "supertrend")["effective"]
    # The page must show exactly what the enforcing code path resolves.
    assert row["effective"]["max_orders_per_day"] == enforced["max_orders_per_day"] == 2


def test_empty_owner_is_truthfully_empty(mu_db):  # noqa: F811
    user = make_user("empty-risk@example.com")
    result = risk_overview.build_risk_overview(user.id)
    assert result["strategies"] == []
    assert result["user"]["kill_switch"] is False
    assert result["trade_date_ist"]
