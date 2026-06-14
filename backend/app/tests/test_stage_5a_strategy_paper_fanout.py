from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import func, select

from app.auth.db import session_scope
from app.auth.models import PaperStrategyOrder, Strategy, StrategySignal, UserSignalJob, UserStrategySubscription
from app.config import settings
from app.main import app
from app.services.signal_fanout_service import fanout_strategy_signal
from app.tests.security_test_helpers import authenticate_client, create_authenticated_user, isolate_runtime


SIGNAL = {
    "strategy_code": "ORB_PORTAL",
    "signal_id": "ORB_2026_06_14_0935_NIFTY_23000_CE_BUY",
    "symbol": "NIFTY",
    "action": "BUY",
    "option_type": "CE",
    "strike": 23000,
    "expiry": "2026-06-16",
    "timeframe": "5m",
    "price": 102.5,
    "source": "tradingview",
}


def _subscribe(client: TestClient, headers: dict[str, str], strategy_code: str = "ORB_PORTAL", **overrides):
    payload = {
        "strategy_code": strategy_code,
        "mode": "paper",
        "max_qty": 65,
        "max_daily_loss": 2000,
        "max_trades_per_day": 3,
        "allowed_option_side": "BOTH",
    }
    payload.update(overrides)
    return client.post("/api/me/strategy-subscriptions", json=payload, headers=headers)


def _intake(client: TestClient, headers: dict[str, str], **overrides):
    payload = dict(SIGNAL)
    payload.update(overrides)
    return client.post("/api/strategy-signals/intake", json=payload, headers=headers)


def test_strategy_catalog_seeded(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user, auth_cookie = create_authenticated_user("stage5-catalog")
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-catalog-csrf")
        response = client.get("/api/strategies", headers=headers)
    assert response.status_code == 200
    records = response.json()["strategies"]
    assert {item["strategyCode"] for item in records} >= {
        "ORB_PORTAL",
        "SUPERTREND_FLIP",
        "SUPPORT_RESISTANCE_REVERSAL",
        "OOPS_GAP_REVERSAL",
        "NIFTY_MOMENTUM_SCALPER",
    }
    assert all(item["paperAllowed"] and item["activeVersion"] for item in records)
    assert next(item for item in records if item["strategyCode"] == "SUPERTREND_FLIP")["liveAllowed"] is True
    assert user.id


def test_list_strategies_requires_auth(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get("/api/strategies")
    assert response.status_code == 401


def test_subscribe_paper_strategy(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _user, auth_cookie = create_authenticated_user("stage5-subscribe")
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-subscribe-csrf")
        response = _subscribe(client, headers)
    assert response.status_code == 201
    body = response.json()["subscription"]
    assert body["mode"] == "paper"
    assert body["status"] == "active"
    assert body["risk"]["maxQty"] == 65


def test_reject_live_subscription_stage5a(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _user, auth_cookie = create_authenticated_user("stage5-live-block")
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-live-csrf")
        response = _subscribe(client, headers, mode="live")
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"].lower()


def test_free_plan_one_active_strategy_limit(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _user, auth_cookie = create_authenticated_user("stage5-free-limit")
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-free-csrf")
        assert _subscribe(client, headers).status_code == 201
        blocked = _subscribe(client, headers, "SUPERTREND_FLIP")
    assert blocked.status_code == 403
    assert "free plan allows 1" in blocked.json()["detail"].lower()


def test_pause_resume_subscription(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _user, auth_cookie = create_authenticated_user("stage5-pause")
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-pause-csrf")
        created = _subscribe(client, headers).json()["subscription"]
        paused = client.post(
            f"/api/me/strategy-subscriptions/{created['id']}/pause",
            headers=headers,
        )
        resumed = client.post(
            f"/api/me/strategy-subscriptions/{created['id']}/resume",
            headers=headers,
        )
    assert paused.status_code == 200
    assert paused.json()["subscription"]["status"] == "paused"
    assert resumed.status_code == 200
    assert resumed.json()["subscription"]["status"] == "active"


def test_central_signal_saved_once_and_duplicate_rejected(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, auth_cookie = create_authenticated_user("stage5-duplicate")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-duplicate-csrf")
        first = _intake(client, headers)
        duplicate = _intake(client, headers)
    assert first.status_code == 200
    assert duplicate.status_code == 409
    with session_scope() as session:
        count = session.exec(select(func.count()).select_from(StrategySignal)).one()
    assert count == 1


def test_signal_fanout_creates_user_jobs_and_scoped_orders(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "FREE_MAX_ACTIVE_PAPER_STRATEGIES", 2)
    user_a, cookie_a = create_authenticated_user("stage5-fanout-a")
    user_b, cookie_b = create_authenticated_user("stage5-fanout-b")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", user_a.email)
    with TestClient(app) as client:
        headers_a = authenticate_client(client, cookie_a, "stage5-fanout-a-csrf")
        assert _subscribe(client, headers_a).status_code == 201
        headers_b = authenticate_client(client, cookie_b, "stage5-fanout-b-csrf")
        assert _subscribe(client, headers_b).status_code == 201
        headers_a = authenticate_client(client, cookie_a, "stage5-fanout-a-csrf")
        response = _intake(client, headers_a)
    assert response.status_code == 200
    assert response.json()["fanout_jobs_created"] == 2
    with session_scope() as session:
        jobs = list(session.exec(select(UserSignalJob)).all())
        orders = list(session.exec(select(PaperStrategyOrder)).all())
    assert {job.user_id for job in jobs} == {user_a.id, user_b.id}
    assert all(job.status == "paper_filled" for job in jobs)
    assert {order.user_id for order in orders} == {user_a.id, user_b.id}
    assert all(order.fill_price == 102.5 and order.qty == 65 for order in orders)


def test_paused_subscription_gets_no_execution_or_skipped_job(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, auth_cookie = create_authenticated_user("stage5-paused-fanout")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-paused-fanout-csrf")
        subscription = _subscribe(client, headers).json()["subscription"]
        client.post(f"/api/me/strategy-subscriptions/{subscription['id']}/pause", headers=headers)
        response = _intake(client, headers)
    assert response.status_code == 200
    assert response.json()["fanout_jobs_created"] == 0
    with session_scope() as session:
        assert session.exec(select(func.count()).select_from(UserSignalJob)).one() == 0
        assert session.exec(select(func.count()).select_from(PaperStrategyOrder)).one() == 0


def test_user_subscription_and_job_isolation(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user_a, cookie_a = create_authenticated_user("stage5-isolation-a")
    user_b, cookie_b = create_authenticated_user("stage5-isolation-b")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", user_a.email)
    with TestClient(app) as client:
        headers_a = authenticate_client(client, cookie_a, "stage5-isolation-a-csrf")
        subscription_a = _subscribe(client, headers_a).json()["subscription"]
        intake = _intake(client, headers_a)
        assert intake.status_code == 200
        jobs_a = client.get("/api/me/signal-jobs", headers=headers_a).json()["jobs"]

        headers_b = authenticate_client(client, cookie_b, "stage5-isolation-b-csrf")
        subscriptions_b = client.get("/api/me/strategy-subscriptions", headers=headers_b)
        jobs_b = client.get("/api/me/signal-jobs", headers=headers_b)
        foreign_job = client.get(f"/api/me/signal-jobs/{jobs_a[0]['id']}", headers=headers_b)
        foreign_pause = client.post(
            f"/api/me/strategy-subscriptions/{subscription_a['id']}/pause",
            headers=headers_b,
        )
    assert subscriptions_b.json()["subscriptions"] == []
    assert jobs_b.json()["jobs"] == []
    assert foreign_job.status_code == 404
    assert foreign_pause.status_code == 404
    assert user_a.id != user_b.id


def test_duplicate_fanout_does_not_create_duplicate_job_or_order(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, auth_cookie = create_authenticated_user("stage5-fanout-duplicate")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-fanout-duplicate-csrf")
        _subscribe(client, headers)
        accepted = _intake(client, headers).json()
    second = fanout_strategy_signal(accepted["strategy_signal_id"])
    assert second.jobs_created == 0
    with session_scope() as session:
        assert session.exec(select(func.count()).select_from(UserSignalJob)).one() == 1
        assert session.exec(select(func.count()).select_from(PaperStrategyOrder)).one() == 1


def test_paper_execution_no_dhan_or_executor_call(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, auth_cookie = create_authenticated_user("stage5-no-dhan")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    monkeypatch.setattr(
        "app.services.dhan_client.RealDhanClient",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Dhan must not be called")),
    )
    monkeypatch.setattr(
        "app.services.execution_router.route_signal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Executor must not be called")),
    )
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-no-dhan-csrf")
        _subscribe(client, headers)
        response = _intake(client, headers)
    assert response.status_code == 200
    assert response.json()["fanout_jobs_created"] == 1


def test_fanout_records_unexpected_user_job_failure_and_continues(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, auth_cookie = create_authenticated_user("stage5-job-failure")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    monkeypatch.setattr(
        "app.services.signal_fanout_service.execute_paper_job",
        lambda _job_id: (_ for _ in ()).throw(RuntimeError("simulated worker failure")),
    )
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-job-failure-csrf")
        _subscribe(client, headers)
        response = _intake(client, headers)
        jobs = client.get("/api/me/signal-jobs", headers=headers).json()["jobs"]
    assert response.status_code == 200
    assert response.json()["fanout_jobs_created"] == 1
    assert response.json()["fanout_jobs_skipped"] == 1
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["reasonCode"] == "paper_execution_failed"


def test_risk_limit_skips_job(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, auth_cookie = create_authenticated_user("stage5-risk-limit")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-risk-limit-csrf")
        assert _subscribe(client, headers, max_trades_per_day=1).status_code == 201
        first = _intake(client, headers)
        second = _intake(client, headers, signal_id="ORB_2026_06_14_0940_NIFTY_23000_CE_BUY")
        jobs = client.get("/api/me/signal-jobs", headers=headers).json()["jobs"]
    assert first.json()["fanout_jobs_created"] == 1
    assert second.json()["fanout_jobs_skipped"] == 1
    assert jobs[0]["status"] == "skipped_daily_trade_limit"
    assert "daily paper trade limit" in jobs[0]["reasonMessage"].lower()


def test_missing_price_is_auditable_paper_rejection(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, auth_cookie = create_authenticated_user("stage5-missing-price")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-missing-price-csrf")
        _subscribe(client, headers)
        response = _intake(client, headers, price=None)
        jobs = client.get("/api/me/signal-jobs", headers=headers).json()["jobs"]
    assert response.status_code == 200
    assert jobs[0]["status"] == "paper_rejected"
    assert jobs[0]["reasonCode"] == "missing_signal_price"


def test_signal_history_is_user_scoped(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user_a, cookie_a = create_authenticated_user("stage5-history-a")
    _user_b, cookie_b = create_authenticated_user("stage5-history-b")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", user_a.email)
    with TestClient(app) as client:
        headers_a = authenticate_client(client, cookie_a, "stage5-history-a-csrf")
        _subscribe(client, headers_a)
        _intake(client, headers_a)
        history_a = client.get("/api/me/strategy-signals", headers=headers_a)
        headers_b = authenticate_client(client, cookie_b, "stage5-history-b-csrf")
        history_b = client.get("/api/me/strategy-signals", headers=headers_b)
    assert len(history_a.json()["signals"]) == 1
    assert history_b.json()["signals"] == []


def test_production_intake_rejects_unauthenticated_request(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr("app.main.validate_production_configuration", lambda: None)
    monkeypatch.setattr("app.main.vault_status", lambda: {"ready": True, "error": None})
    with TestClient(app) as client:
        response = client.post("/api/strategy-signals/intake", json=SIGNAL)
    assert response.status_code == 401


def test_signal_payload_secrets_are_redacted(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, auth_cookie = create_authenticated_user("stage5-redaction")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-redaction-csrf")
        response = _intake(client, headers, token="never-store-this", nested={"secret": "also-never"})
    assert response.status_code == 200
    with session_scope() as session:
        signal = session.exec(select(StrategySignal)).one()
    assert signal.raw_payload_redacted_json["token"] == "[REDACTED]"
    assert signal.raw_payload_redacted_json["nested"]["secret"] == "[REDACTED]"


def test_remove_subscription_is_user_scoped_and_soft_disables(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user, auth_cookie = create_authenticated_user("stage5-remove")
    with TestClient(app) as client:
        headers = authenticate_client(client, auth_cookie, "stage5-remove-csrf")
        subscription = _subscribe(client, headers).json()["subscription"]
        removed = client.delete(f"/api/me/strategy-subscriptions/{subscription['id']}", headers=headers)
        listed = client.get("/api/me/strategy-subscriptions", headers=headers)
    assert removed.status_code == 200
    assert listed.json()["subscriptions"] == []
    with session_scope() as session:
        stored = session.get(UserStrategySubscription, subscription["id"])
        strategy = session.get(Strategy, stored.strategy_id)
    assert stored.user_id == user.id
    assert stored.status == "disabled"
    assert strategy.strategy_code == "ORB_PORTAL"
