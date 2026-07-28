"""Phase 3A private strategy-instance webhook: ingestion, credentials,
payload contract, idempotency, tenant isolation, and status API."""
# ruff: noqa: DTZ005, F811
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_webhook_auth_status_file(tmp_path, monkeypatch):
    from app.services import private_webhook_service

    monkeypatch.setattr(
        private_webhook_service,
        "_AUTH_FAILURE_FILE",
        tmp_path / "private_webhook_auth_failures.json",
    )


def _now_iso(offset_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


@pytest.fixture
def webhook_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "PRIVATE_WEBHOOK_RATE_LIMIT_PER_MINUTE", 10_000, raising=False)
    from app.routers import private_webhook

    private_webhook._RATE_BUCKETS.clear()


@pytest.fixture
def client(mu_db, webhook_enabled):
    from app.routers import private_webhook

    app = FastAPI()
    app.include_router(private_webhook.router)
    return TestClient(app)


def _make_instance(
    user,
    *,
    label: str = "My Pine",
    status: str = "active",
    execution_mode: str = "paper_live_data",
    lots: int = 1,
    source_journey: str = "PERSONAL_TRADINGVIEW",
) -> str:
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        strategy = models.StrategyCatalog(
            code=f"pine-{uuid.uuid4().hex[:10]}",
            display_name="Personal Pine",
            owner_type="personal",
            owner_user_id=user.id,
            status="active",
        )
        db.add(strategy)
        db.flush()
        version = models.StrategyVersion(
            strategy_id=strategy.id,
            version="1.0",
            payload_spec_version="nova.v1",
            source_journey="personal_tradingview",
            status="approved",
            execution_kind="external_webhook",
        )
        db.add(version)
        db.flush()
        instance = models.StrategyInstance(
            user_id=user.id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            source_journey=source_journey,
            label=label,
            status=status,
            execution_mode=execution_mode,
            current_lots=lots,
        )
        db.add(instance)
        db.flush()
        mode = "live" if execution_mode == "real_orders" else "paper"
        revision = models.StrategyConfigurationRevision(
            user_id=user.id,
            strategy_instance_id=instance.id,
            strategy_version_id=version.id,
            mode=mode,
            revision=1,
            configuration_json={
                "lots": lots,
                "allowed_option_side": "BOTH",
            },
            risk_json={},
            status="active",
        )
        db.add(revision)
        db.flush()
        selection = db.scalar(
            select(models.UserEngineConfig).where(
                models.UserEngineConfig.user_id == user.id
            )
        )
        if selection is None:
            db.add(
                models.UserEngineConfig(
                    user_id=user.id,
                    selected_strategy_instance_id=instance.id,
                    selected_configuration_revision_id=revision.id,
                    selected_configuration_revision=revision.revision,
                )
            )
        return str(instance.id)


def _issue_token(user, instance_id: str) -> str:
    from app.services import strategy_instance_service

    credential = strategy_instance_service.generate_webhook_credential(user.id, instance_id)
    return credential["token"]


def _payload(**overrides):
    body = {"action": "BUY_CE", "signal_id": f"sig-{uuid.uuid4().hex[:10]}", "signal_time": _now_iso()}
    body.update(overrides)
    return body


def _post(client, token, body):
    return client.post("/api/webhooks/private", json={"credential": token, **body})


def _jobs(instance_id: str):
    from sqlalchemy import select

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        rows = db.scalars(
            select(models.StrategyExecutionJob).where(
                models.StrategyExecutionJob.strategy_name == f"instance:{instance_id}"
            )
        ).all()
        return [
            {
                "user_id": str(row.user_id),
                "status": row.status,
                "signal_id": row.signal_id,
                "lots": row.lots,
                "execution_mode": row.execution_mode,
                "signal_payload": row.signal_payload,
            }
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def test_disabled_flag_rejects_and_creates_nothing(mu_db, monkeypatch):
    from app.config import settings
    from app.routers import private_webhook

    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", False, raising=False)
    app = FastAPI()
    app.include_router(private_webhook.router)
    test_client = TestClient(app)
    user = make_user("flag@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    response = _post(test_client, token, _payload())
    assert response.status_code == 403
    assert response.json()["reason"] == "DISABLED"
    assert _jobs(instance_id) == []


# ---------------------------------------------------------------------------
# Credential tests
# ---------------------------------------------------------------------------

def test_valid_credential_queues_job(client):
    user = make_user("cred-valid@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload(action="BUY_CE"))
    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True and body["status"] == "QUEUED"
    jobs = _jobs(instance_id)
    assert len(jobs) == 1
    assert jobs[0]["user_id"] == str(user.id)
    assert jobs[0]["status"] == "queued"


def test_unknown_credential_is_uniform_401(client):
    response = _post(client, "nwk_" + "x" * 43, _payload())
    assert response.status_code == 401
    assert response.json()["reason"] == "INVALID_CREDENTIAL"


def test_revoked_credential_rejected_like_unknown(client):
    from app.services import private_webhook_service, strategy_instance_service

    user = make_user("cred-revoked@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    strategy_instance_service.revoke_webhook_credential(user.id, instance_id)
    response = _post(client, token, _payload())
    assert response.status_code == 401
    # Identical shape to the unknown-credential response: no tenant probing.
    unknown = _post(client, "nwk_" + "y" * 43, _payload())
    assert response.json() == unknown.json()
    assert _jobs(instance_id) == []
    auth_status = private_webhook_service.webhook_auth_status(instance_id, user.id)
    assert auth_status["reason"] == "REVOKED_OR_ROTATED_CREDENTIAL"
    assert auth_status["last_failed_at"] is not None
    assert token not in private_webhook_service._AUTH_FAILURE_FILE.read_text(encoding="utf-8")


def test_rotated_credential_old_rejected_new_works(client):
    from app.services import strategy_instance_service

    user = make_user("cred-rotate@gmail.com")
    instance_id = _make_instance(user)
    old_token = _issue_token(user, instance_id)
    new_token = strategy_instance_service.rotate_webhook_credential(user.id, instance_id)["token"]
    assert _post(client, old_token, _payload()).status_code == 401
    assert _post(client, new_token, _payload()).status_code == 202


@pytest.mark.parametrize("status", ["stopped", "ready"])
def test_inactive_instance_rejected(client, status):
    user = make_user(f"cred-{status}@gmail.com")
    instance_id = _make_instance(user, status=status)
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload())
    assert response.status_code == 409
    assert response.json()["reason"] == "STRATEGY_INSTANCE_INACTIVE"
    assert _jobs(instance_id) == []


@pytest.mark.parametrize("action", ["BUY_CE", "BUY_PE", "EXIT", "HOLD"])
def test_paused_instance_persists_action_for_worker_policy(client, action):
    user = make_user(f"paused-{action.lower()}@gmail.com")
    instance_id = _make_instance(user, status="paused")
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload(action=action))
    assert response.status_code == 202
    assert len(_jobs(instance_id)) == (0 if action == "HOLD" else 1)


def test_non_webhook_journey_rejected(client):
    # Credential issuance blocks non-PERSONAL_TRADINGVIEW journeys, so forge a
    # credential row directly to prove the ingest-side lifecycle check too.
    import hashlib

    from app.db import models
    from app.db.engine import session_scope

    user = make_user("cred-journey@gmail.com")
    instance_id = _make_instance(user, source_journey="NOVA_SHARED")
    token = "nwk_forged_" + uuid.uuid4().hex
    with session_scope() as db:
        db.add(
            models.StrategyInstanceWebhookCredential(
                strategy_instance_id=uuid.UUID(instance_id),
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                token_prefix=token[:10],
            )
        )
    response = _post(client, token, _payload())
    assert response.status_code == 409
    assert response.json()["reason"] == "STRATEGY_INSTANCE_INACTIVE"


def test_plaintext_credential_never_persisted_or_logged(client, tmp_path, monkeypatch):
    from sqlalchemy import text

    from app.db.engine import session_scope
    from app.services import audit_logger, state_store

    log_file = tmp_path / "audit_events.jsonl"
    log_files = dict(state_store.LOG_FILES)
    log_files["audit"] = log_file
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)

    user = make_user("cred-hygiene@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    assert _post(client, token, _payload()).status_code == 202
    secret_part = token.removeprefix("nwk_")

    with session_scope() as db:
        for table in ("strategy_signals", "strategy_execution_jobs", "audit_logs", "webhook_events"):
            rows = db.execute(text(f"SELECT * FROM {table}")).fetchall()
            assert secret_part not in json.dumps([tuple(map(str, row)) for row in rows])
    if log_file.exists():
        assert secret_part not in log_file.read_text(encoding="utf-8")


def test_credential_is_body_only_and_legacy_path_is_unmounted(client):
    user = make_user("cred-body-only@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload()

    assert client.post(f"/api/webhooks/private/{token}", json=body).status_code == 404
    assert client.post(
        f"/api/webhooks/private?credential={token}", json=body
    ).status_code == 422
    assert client.post(
        "/api/webhooks/private", json=body, headers={"X-Webhook-Credential": token}
    ).status_code == 422
    assert _jobs(instance_id) == []


def test_request_model_masks_and_excludes_credential():
    from app.services.private_webhook_service import PrivateWebhookRequest

    sentinel = "SENTINEL_PRIVATE_WEBHOOK_CREDENTIAL"
    request = PrivateWebhookRequest.model_validate({"credential": sentinel, **_payload()})
    assert sentinel not in repr(request)
    assert sentinel not in json.dumps(request.model_dump(mode="json"))
    assert "credential" not in request.model_dump()


def test_failed_auth_audit_has_only_safe_correlation(client, monkeypatch):
    from app.services import private_webhook_service

    events = []
    monkeypatch.setattr(
        private_webhook_service,
        "log_audit_event",
        lambda action, message, **kwargs: events.append((action, message, kwargs)),
    )
    sentinel = "SENTINEL_UNKNOWN_PRIVATE_WEBHOOK_CREDENTIAL"
    response = _post(client, sentinel, _payload())

    assert response.status_code == 401
    assert len(events) == 1
    action, message, kwargs = events[0]
    assert action == "private_webhook_auth_failed"
    assert message == "private_webhook_auth_failed"
    assert set(kwargs["metadata"]) == {"correlation_id"}
    assert len(kwargs["metadata"]["correlation_id"]) == 32
    assert sentinel not in json.dumps(events)


def test_sentinel_never_leaks_from_rejected_or_failed_requests(
    mu_db, webhook_enabled, monkeypatch, caplog
):
    from app.routers import private_webhook

    sentinel = "SENTINEL_PRIVATE_WEBHOOK_CREDENTIAL_9dff28"
    caplog.set_level(logging.DEBUG)
    app = FastAPI()
    app.include_router(private_webhook.router)
    safe_client = TestClient(app, raise_server_exceptions=False)

    malformed = safe_client.post(
        "/api/webhooks/private",
        content=f'{{"credential":"{sentinel}"'.encode(),
        headers={"Content-Type": "application/json"},
    )
    oversized = safe_client.post(
        "/api/webhooks/private",
        json={"credential": sentinel, **_payload(), "comment": "x" * 10_000},
    )
    unknown = _post(safe_client, sentinel, _payload())

    monkeypatch.setattr(private_webhook.service, "authenticate_credential", lambda *a, **k: {
        "credential_id": "safe-id",
        "correlation_id": "safe-correlation",
        "instance_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "instance_status": "active",
        "source_journey": "PERSONAL_TRADINGVIEW",
        "execution_mode": "paper_live_data",
        "current_lots": 1,
        "strategy_id": str(uuid.uuid4()),
    })
    monkeypatch.setattr(
        private_webhook.service, "ingest", lambda *_: (_ for _ in ()).throw(RuntimeError("forced"))
    )
    failed = _post(safe_client, sentinel, _payload())

    assert [malformed.status_code, oversized.status_code, unknown.status_code, failed.status_code] == [
        400, 413, 401, 500
    ]
    surfaces = "\n".join(response.text for response in (malformed, oversized, unknown, failed))
    surfaces += "\n" + caplog.text
    assert sentinel not in surfaces


def test_credential_a_cannot_execute_instance_b(client):
    user_a = make_user("tenant-a@gmail.com")
    user_b = make_user("tenant-b@gmail.com")
    instance_a = _make_instance(user_a, label="A")
    instance_b = _make_instance(user_b, label="B")
    token_a = _issue_token(user_a, instance_a)
    _issue_token(user_b, instance_b)

    response = _post(client, token_a, _payload())
    assert response.status_code == 202
    jobs_a = _jobs(instance_a)
    assert len(jobs_a) == 1 and jobs_a[0]["user_id"] == str(user_a.id)
    assert _jobs(instance_b) == []


# ---------------------------------------------------------------------------
# Payload tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "action,expected_status",
    [("BUY_CE", "QUEUED"), ("BUY_PE", "QUEUED"), ("EXIT", "QUEUED"), ("HOLD", "NO_OP")],
)
def test_all_actions_accepted(client, action, expected_status):
    user = make_user(f"payload-{action.lower()}@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload(action=action))
    assert response.status_code == 202
    assert response.json()["status"] == expected_status
    assert len(_jobs(instance_id)) == (0 if action == "HOLD" else 1)


def test_case_normalization(client):
    user = make_user("payload-case@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload(action="buy_ce"))
    assert response.status_code == 202
    assert response.json()["action"] == "BUY_CE"
    response = _post(client, token, _payload(action="Buy_PE"))
    assert response.status_code == 202
    assert response.json()["action"] == "BUY_PE"


@pytest.mark.parametrize("action", ["LONG", "SHORT", "CALL", "PUT", "BULLISH", "BEARISH", "BUY", "SELL", "nonsense"])
def test_unknown_actions_rejected(client, action):
    user = make_user(f"payload-bad-{action.lower()}@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload(action=action))
    assert response.status_code == 422
    assert response.json()["reason"] == "INVALID_ACTION"
    assert _jobs(instance_id) == []


def test_missing_and_oversized_signal_id(client):
    user = make_user("payload-sid@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload()
    body.pop("signal_id")
    assert _post(client, token, body).status_code == 422
    assert _post(client, token, _payload(signal_id="x" * 129)).status_code == 422
    assert _jobs(instance_id) == []


def test_missing_timezone_rejected(client):
    user = make_user("payload-tz@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    naive = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    response = _post(client, token, _payload(signal_time=naive))
    assert response.status_code == 422
    assert response.json()["reason"] == "MISSING_TIMEZONE"


def test_stale_and_future_signals_rejected(client):
    user = make_user("payload-stale@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    stale = _post(client, token, _payload(signal_time=_now_iso(-3600)))
    assert stale.status_code == 422 and stale.json()["reason"] == "STALE_SIGNAL"
    future = _post(client, token, _payload(signal_time=_now_iso(3600)))
    assert future.status_code == 422 and future.json()["reason"] == "STALE_SIGNAL"
    assert _jobs(instance_id) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "someone-else"),
        ("strategy_instance_id", str(uuid.uuid4())),
        ("broker_account_id", "X123"),
        ("mode", "live"),
        ("lots", 500),
        ("quantity", 65000),
        ("strike", 26000),
        ("expiry", "2026-07-16"),
        ("security_id", "13"),
        ("trading_symbol", "NIFTY EVIL CE"),
        ("event_source", "EOD"),
        ("exit_reason", "manual"),
        ("manual", True),
        ("eod", True),
        ("order_type", "LIMIT"),
        ("product_type", "CNC"),
    ],
)
def test_privileged_fields_rejected(client, field, value):
    """Server authority: a payload can never name tenant, quantity, contract,
    mode, or event source."""
    user = make_user(f"payload-priv-{field}@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload(**{field: value}))
    assert response.status_code == 422
    assert response.json()["reason"] == "INVALID_PAYLOAD"
    assert _jobs(instance_id) == []


def test_invalid_json_and_wrong_content_type_and_oversized_body(client):
    user = make_user("payload-body@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    url = "/api/webhooks/private"

    bad_json = client.post(url, content=b"{not json", headers={"Content-Type": "application/json"})
    assert bad_json.status_code == 400

    not_object = client.post(url, content=b"[1,2]", headers={"Content-Type": "application/json"})
    assert not_object.status_code == 400

    wrong_type = client.post(url, content=b"action=BUY_CE", headers={"Content-Type": "text/plain"})
    assert wrong_type.status_code == 415

    oversized = client.post(
        url,
        content=json.dumps({"credential": token, **_payload(), "comment": "x" * 10_000}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert _jobs(instance_id) == []


def test_rate_limit_is_per_credential(mu_db, monkeypatch):
    from app.config import settings
    from app.routers import private_webhook

    monkeypatch.setattr(settings, "PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "PRIVATE_WEBHOOK_RATE_LIMIT_PER_MINUTE", 2, raising=False)
    private_webhook._RATE_BUCKETS.clear()
    app = FastAPI()
    app.include_router(private_webhook.router)
    test_client = TestClient(app)

    user_a = make_user("rate-a@gmail.com")
    user_b = make_user("rate-b@gmail.com")
    instance_a = _make_instance(user_a)
    instance_b = _make_instance(user_b)
    token_a = _issue_token(user_a, instance_a)
    token_b = _issue_token(user_b, instance_b)

    assert _post(test_client, token_a, _payload()).status_code == 202
    assert _post(test_client, token_a, _payload()).status_code == 202
    assert _post(test_client, token_a, _payload()).status_code == 429
    # One tenant exhausting its budget never blocks another tenant.
    assert _post(test_client, token_b, _payload()).status_code == 202


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------

def test_identical_duplicate_returns_existing_status_without_new_job(client):
    user = make_user("idem-dup@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(action="BUY_CE", signal_id="dup-1")
    first = _post(client, token, body)
    assert first.status_code == 202
    second = _post(client, token, body)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["job_status"] == "queued"
    assert len(_jobs(instance_id)) == 1


def test_conflicting_duplicate_rejected_original_untouched(client):
    user = make_user("idem-conflict@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    time_iso = _now_iso()
    first = _post(client, token, _payload(action="BUY_CE", signal_id="conflict-1", signal_time=time_iso))
    assert first.status_code == 202
    second = _post(client, token, _payload(action="BUY_PE", signal_id="conflict-1", signal_time=time_iso))
    assert second.status_code == 409
    assert second.json()["reason"] == "CONFLICTING_DUPLICATE"
    jobs = _jobs(instance_id)
    assert len(jobs) == 1
    assert jobs[0]["signal_payload"]["option_side"] == "CE"


def test_same_signal_id_isolated_between_instances(client):
    user = make_user("idem-cross@gmail.com")
    instance_1 = _make_instance(user, label="one")
    instance_2 = _make_instance(user, label="two")
    token_1 = _issue_token(user, instance_1)
    token_2 = _issue_token(user, instance_2)
    body = _payload(signal_id="shared-sig")
    assert _post(client, token_1, body).status_code == 202
    # Same signal_id on a DIFFERENT instance is a fresh signal, not a duplicate.
    assert _post(client, token_2, body).status_code == 202
    assert len(_jobs(instance_1)) == 1
    assert len(_jobs(instance_2)) == 1


def test_concurrent_identical_deliveries_create_one_job(client):
    user = make_user("idem-concurrent@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(signal_id="race-1")
    statuses: list[int] = []

    def fire():
        statuses.append(_post(client, token, body).status_code)

    threads = [threading.Thread(target=fire) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses.count(202) == 1
    assert all(code in {200, 202} for code in statuses)
    assert len(_jobs(instance_id)) == 1


def test_duplicate_hold_returns_existing_status(client):
    user = make_user("idem-hold@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    body = _payload(action="HOLD", signal_id="hold-1")
    assert _post(client, token, body).status_code == 202
    second = _post(client, token, body)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert _jobs(instance_id) == []


# ---------------------------------------------------------------------------
# Authority: normalized signal contents
# ---------------------------------------------------------------------------

def test_job_signal_uses_server_authority_only(client):
    user = make_user("authority@gmail.com")
    instance_id = _make_instance(user, lots=3)
    token = _issue_token(user, instance_id)
    response = _post(client, token, _payload(action="BUY_PE", reference_price=25012.35, timeframe="5"))
    assert response.status_code == 202
    job = _jobs(instance_id)[0]
    payload = job["signal_payload"]
    assert payload["action"] == "ENTRY"
    assert payload["side"] == "BUY"
    assert payload["option_side"] == "PE"
    assert payload["symbol"] == "NIFTY"
    assert payload["product_type"] == "INTRADAY"
    assert payload["order_type"] == "MARKET"
    # Contract + qty are resolved by the worker from server state, never here.
    assert payload["security_id"] is None
    assert payload["strike"] is None
    assert payload["expiry"] is None
    assert payload["qty"] == 1  # placeholder overridden by engine lots later
    assert job["lots"] == 3
    assert job["execution_mode"] == "paper_live_data"
    assert payload["secret"] == ""


# ---------------------------------------------------------------------------
# Status API (owner-scoped)
# ---------------------------------------------------------------------------

def test_status_api_owner_scoped_and_paginated(client):
    from app.services import private_webhook_service

    user = make_user("status-owner@gmail.com")
    other = make_user("status-other@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    for index in range(3):
        assert _post(client, token, _payload(signal_id=f"status-{index}")).status_code == 202

    page = private_webhook_service.list_webhook_executions(user.id, instance_id, limit=2, offset=0)
    assert len(page["executions"]) == 2
    page_two = private_webhook_service.list_webhook_executions(user.id, instance_id, limit=2, offset=2)
    assert len(page_two["executions"]) == 1
    entry = page["executions"][0]
    assert entry["action"] == "BUY_CE"
    assert entry["signal_time"] is not None
    assert entry["received_at"] is not None
    assert "token" not in json.dumps(page)

    from app.services.strategy_instance_service import InstanceError

    with pytest.raises(InstanceError):
        private_webhook_service.list_webhook_executions(other.id, instance_id)


def test_status_api_never_returns_credentials(client):
    from app.services import private_webhook_service

    user = make_user("status-safe@gmail.com")
    instance_id = _make_instance(user)
    token = _issue_token(user, instance_id)
    assert _post(client, token, _payload()).status_code == 202
    result = private_webhook_service.list_webhook_executions(user.id, instance_id)
    dumped = json.dumps(result)
    assert token.removeprefix("nwk_") not in dumped
    assert "token_hash" not in dumped
