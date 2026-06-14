from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import func, select

from app.auth.db import session_scope
from app.auth.models import (
    PaperLedgerEntry,
    PaperPosition,
    UserSignalJob,
    WorkerJob,
    utc_now_dt,
)
from app.config import settings
from app.main import app
from app.services.queue_service import (
    JOB_USER_NOTIFICATION,
    WorkerQueueError,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    retry_job,
)
from app.services.worker_runtime import drain_worker_jobs
from app.services.worker_policy import validate_paper_worker_configuration
from app.tests.security_test_helpers import (
    authenticate_client,
    create_authenticated_user,
    isolate_runtime,
)


BUY_SIGNAL = {
    "strategy_code": "ORB_PORTAL",
    "signal_id": "STAGE5B-BUY-1",
    "symbol": "NIFTY",
    "action": "BUY",
    "option_type": "CE",
    "strike": 23000,
    "expiry": "2026-06-16",
    "timeframe": "5m",
    "price": 100,
    "source": "internal",
}


def _subscribe(
    client: TestClient,
    headers: dict[str, str],
    *,
    max_daily_loss: float = 1000,
):
    return client.post(
        "/api/me/strategy-subscriptions",
        headers=headers,
        json={
            "strategy_code": "ORB_PORTAL",
            "mode": "paper",
            "max_qty": 10,
            "max_daily_loss": max_daily_loss,
            "max_trades_per_day": 5,
            "allowed_option_side": "BOTH",
        },
    )


def _intake(client: TestClient, headers: dict[str, str], payload: dict):
    return client.post("/api/strategy-signals/intake", headers=headers, json=payload)


def test_intake_is_durable_and_async_by_default(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "PAPER_QUEUE_INLINE_LOCAL", False)
    admin, cookie = create_authenticated_user("stage5b-async")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)

    with TestClient(app) as client:
        headers = authenticate_client(client, cookie, "stage5b-async-csrf")
        assert _subscribe(client, headers).status_code == 201
        response = _intake(client, headers, BUY_SIGNAL)

    assert response.status_code == 200
    assert response.json()["fanout_status"] == "queued"
    assert response.json()["fanout_jobs_created"] == 0
    with session_scope() as session:
        queue_job = session.get(WorkerJob, response.json()["queue_job_id"])
        assert queue_job is not None
        assert queue_job.status == "queued"
        assert queue_job.payload_json == {"strategy_signal_id": response.json()["strategy_signal_id"]}
        assert session.exec(select(func.count()).select_from(UserSignalJob)).one() == 0


def test_worker_drains_fanout_and_opens_one_position_idempotently(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "PAPER_QUEUE_INLINE_LOCAL", False)
    admin, cookie = create_authenticated_user("stage5b-worker")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)

    with TestClient(app) as client:
        headers = authenticate_client(client, cookie, "stage5b-worker-csrf")
        _subscribe(client, headers)
        accepted = _intake(client, headers, BUY_SIGNAL)
        assert accepted.status_code == 200

    assert drain_worker_jobs() > 0
    assert drain_worker_jobs() == 0
    with session_scope() as session:
        positions = list(session.exec(select(PaperPosition)).all())
        entries = list(session.exec(select(PaperLedgerEntry)).all())
        jobs = list(session.exec(select(UserSignalJob)).all())
    assert len(positions) == 1
    assert positions[0].status == "open"
    assert len([entry for entry in entries if entry.entry_type == "order_fill"]) == 1
    assert len(jobs) == 1
    assert jobs[0].status == "paper_filled"


def test_paper_position_close_writes_realized_pnl_ledger(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "PAPER_QUEUE_INLINE_LOCAL", False)
    admin, cookie = create_authenticated_user("stage5b-close")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)

    with TestClient(app) as client:
        headers = authenticate_client(client, cookie, "stage5b-close-csrf")
        _subscribe(client, headers)
        assert _intake(client, headers, BUY_SIGNAL).status_code == 200
        drain_worker_jobs()
        exit_signal = {
            **BUY_SIGNAL,
            "signal_id": "STAGE5B-EXIT-1",
            "action": "EXIT",
            "price": 112.5,
        }
        assert _intake(client, headers, exit_signal).status_code == 200
        drain_worker_jobs()
        positions_response = client.get("/api/me/paper-positions", headers=headers)
        ledger_response = client.get("/api/me/paper-ledger", headers=headers)

    position = positions_response.json()["positions"][0]
    assert position["status"] == "closed"
    assert position["realizedPnl"] == 125
    close_entries = [
        entry for entry in ledger_response.json()["entries"] if entry["entryType"] == "position_close"
    ]
    assert len(close_entries) == 1
    assert close_entries[0]["realizedPnl"] == 125


def test_exit_without_position_is_auditable_rejection(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "PAPER_QUEUE_INLINE_LOCAL", False)
    admin, cookie = create_authenticated_user("stage5b-no-position")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)

    with TestClient(app) as client:
        headers = authenticate_client(client, cookie, "stage5b-no-position-csrf")
        _subscribe(client, headers)
        response = _intake(
            client,
            headers,
            {**BUY_SIGNAL, "signal_id": "STAGE5B-EXIT-NONE", "action": "SELL"},
        )
        assert response.status_code == 200
        drain_worker_jobs()
        jobs = client.get("/api/me/signal-jobs", headers=headers).json()["jobs"]
        ledger = client.get("/api/me/paper-ledger", headers=headers).json()["entries"]

    assert jobs[0]["status"] == "paper_rejected"
    assert jobs[0]["reasonCode"] == "no_open_position"
    assert any(entry["entryType"] == "risk_block" for entry in ledger)


def test_atomic_claim_allows_only_one_worker_to_take_job() -> None:
    job = enqueue_job(
        job_type=JOB_USER_NOTIFICATION,
        payload={"user_notification_id": 900001},
        dedupe_key="stage5b-atomic-claim",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim_next_job, ("claim-worker-a", "claim-worker-b")))
    claimed_ids = [item.id for item in claimed if item is not None]
    assert claimed_ids == [job.id]


def test_enqueue_claim_complete_job() -> None:
    job = enqueue_job(
        job_type=JOB_USER_NOTIFICATION,
        payload={"user_notification_id": 900000},
        dedupe_key="stage5b-claim-complete",
    )
    claimed = claim_next_job("complete-worker")
    assert claimed and claimed.id == job.id
    completed = complete_job(int(job.id), "complete-worker")
    assert completed.status == "succeeded"
    assert completed.finished_at is not None


def test_expired_lock_is_recovered_and_reclaimed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WORKER_JOB_LOCK_SECONDS", 5)
    job = enqueue_job(
        job_type=JOB_USER_NOTIFICATION,
        payload={"user_notification_id": 900002},
        dedupe_key="stage5b-expired-lock",
    )
    claimed = claim_next_job("expired-worker")
    assert claimed and claimed.id == job.id
    with session_scope() as session:
        stored = session.get(WorkerJob, job.id)
        stored.locked_until = utc_now_dt() - timedelta(seconds=1)
        session.add(stored)
        session.commit()

    reclaimed = claim_next_job("replacement-worker")
    assert reclaimed and reclaimed.id == job.id
    assert reclaimed.attempt_count == 2
    assert reclaimed.locked_by == "replacement-worker"


def test_failure_retries_then_dead_letters_and_admin_retry_resets() -> None:
    job = enqueue_job(
        job_type=JOB_USER_NOTIFICATION,
        payload={"user_notification_id": 900003},
        dedupe_key="stage5b-dead-letter",
        max_attempts=2,
    )
    first = claim_next_job("retry-worker")
    failed = fail_job(
        int(first.id),
        "retry-worker",
        "temporary failure access_token=must-not-persist",
    )
    assert failed.status == "failed"
    assert "must-not-persist" not in str(failed.last_error)
    with session_scope() as session:
        stored = session.get(WorkerJob, job.id)
        stored.available_at = utc_now_dt()
        session.add(stored)
        session.commit()
    second = claim_next_job("retry-worker")
    dead = fail_job(int(second.id), "retry-worker", "permanent failure")
    assert dead.status == "dead"
    assert dead.attempt_count == 2

    reset = retry_job(int(job.id))
    assert reset.status == "queued"
    assert reset.attempt_count == 0
    assert reset.last_error is None


def test_worker_payload_schema_rejects_extra_or_secret_fields() -> None:
    with pytest.raises(WorkerQueueError):
        enqueue_job(
            job_type=JOB_USER_NOTIFICATION,
            payload={"user_notification_id": 1, "access_token": "must-not-persist"},
            dedupe_key="stage5b-secret-payload",
        )


def test_web_role_does_not_enable_paper_worker(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WORKER_ROLE", "web")
    monkeypatch.setattr(settings, "ENABLE_PAPER_WORKERS", True)
    assert validate_paper_worker_configuration() is False


def test_unknown_worker_job_type_is_rejected() -> None:
    with pytest.raises(WorkerQueueError):
        enqueue_job(
            job_type="unknown_order_execution",
            payload={"user_signal_job_id": 1},
            dedupe_key="stage5b-live-job",
        )


def test_positions_and_ledger_are_user_scoped(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "PAPER_QUEUE_INLINE_LOCAL", False)
    user_a, cookie_a = create_authenticated_user("stage5b-isolation-a")
    _user_b, cookie_b = create_authenticated_user("stage5b-isolation-b")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", user_a.email)

    with TestClient(app) as client:
        headers_a = authenticate_client(client, cookie_a, "stage5b-isolation-a-csrf")
        _subscribe(client, headers_a)
        _intake(client, headers_a, BUY_SIGNAL)
        drain_worker_jobs()
        assert len(client.get("/api/me/paper-positions", headers=headers_a).json()["positions"]) == 1
        assert client.get("/api/me/paper-ledger", headers=headers_a).json()["entries"]

        headers_b = authenticate_client(client, cookie_b, "stage5b-isolation-b-csrf")
        assert client.get("/api/me/paper-positions", headers=headers_b).json()["positions"] == []
        assert client.get("/api/me/paper-ledger", headers=headers_b).json()["entries"] == []
        assert client.get("/api/me/notifications", headers=headers_b).json()["notifications"] == []


def test_sql_daily_loss_is_scoped_per_user_and_subscription(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "PAPER_QUEUE_INLINE_LOCAL", False)
    user_a, cookie_a = create_authenticated_user("stage5b-loss-a")
    user_b, cookie_b = create_authenticated_user("stage5b-loss-b")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", user_a.email)

    with TestClient(app) as client:
        headers_a = authenticate_client(client, cookie_a, "stage5b-loss-a-csrf")
        assert _subscribe(client, headers_a, max_daily_loss=500).status_code == 201
        headers_b = authenticate_client(client, cookie_b, "stage5b-loss-b-csrf")
        assert _subscribe(client, headers_b, max_daily_loss=500).status_code == 201

        headers_a = authenticate_client(client, cookie_a, "stage5b-loss-a-csrf")
        assert _intake(client, headers_a, BUY_SIGNAL).status_code == 200
        drain_worker_jobs()

        headers_b = authenticate_client(client, cookie_b, "stage5b-loss-b-csrf")
        subscription_b = client.get("/api/me/strategy-subscriptions", headers=headers_b).json()["subscriptions"][0]
        client.post(
            f"/api/me/strategy-subscriptions/{subscription_b['id']}/pause",
            headers=headers_b,
        )
        headers_a = authenticate_client(client, cookie_a, "stage5b-loss-a-csrf")
        assert _intake(
            client,
            headers_a,
            {**BUY_SIGNAL, "signal_id": "STAGE5B-LOSS-EXIT", "action": "EXIT", "price": 0.5},
        ).status_code == 200
        drain_worker_jobs()

        headers_b = authenticate_client(client, cookie_b, "stage5b-loss-b-csrf")
        client.post(
            f"/api/me/strategy-subscriptions/{subscription_b['id']}/resume",
            headers=headers_b,
        )
        headers_a = authenticate_client(client, cookie_a, "stage5b-loss-a-csrf")
        assert _intake(
            client,
            headers_a,
            {**BUY_SIGNAL, "signal_id": "STAGE5B-AFTER-LOSS", "strike": 23100},
        ).status_code == 200
        drain_worker_jobs()
        jobs_a = client.get("/api/me/signal-jobs", headers=headers_a).json()["jobs"]
        headers_b = authenticate_client(client, cookie_b, "stage5b-loss-b-csrf")
        jobs_b = client.get("/api/me/signal-jobs", headers=headers_b).json()["jobs"]

    assert jobs_a[0]["status"] == "skipped_daily_loss_limit"
    assert jobs_b[0]["status"] == "paper_filled"


def test_admin_queue_endpoints_require_admin(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, admin_cookie = create_authenticated_user("stage5b-admin")
    _user, user_cookie = create_authenticated_user("stage5b-non-admin")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    enqueue_job(
        job_type=JOB_USER_NOTIFICATION,
        payload={"user_notification_id": 900004},
        dedupe_key="stage5b-admin-visible",
    )

    with TestClient(app) as client:
        user_headers = authenticate_client(client, user_cookie, "stage5b-non-admin-csrf")
        denied = client.get("/api/admin/worker-queue", headers=user_headers)
        admin_headers = authenticate_client(client, admin_cookie, "stage5b-admin-csrf")
        allowed = client.get("/api/admin/worker/jobs", headers=admin_headers)
        detail = client.get(
            f"/api/admin/worker/jobs/{allowed.json()['jobs'][0]['id']}",
            headers=admin_headers,
        )
        auth_status = client.get("/api/auth/status", headers=admin_headers)

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert detail.status_code == 200
    assert allowed.json()["counts"]["queued"] >= 1
    assert auth_status.json()["isAdmin"] is True
