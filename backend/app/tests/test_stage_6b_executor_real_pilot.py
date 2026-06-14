from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.db import init_auth_db, session_scope
from app.auth.models import LiveOrderJob, utc_now_dt
from app.config import settings
from app.executor_service.dhan_order_client import ExecutorOrderResult
from app.executor_service.main import app as executor_app
from app.services.credential_vault import save_dhan_credentials
from app.services.executor_registry_service import (
    assign_executor_to_user,
    disable_executor,
    register_executor_node,
    verify_assignment,
    verify_executor_egress_ip,
    verify_executor_health,
)
from app.services.executor_signing import canonical_json_bytes, signed_executor_headers
from app.services.live_execution_service import execute_live_order_job
from app.services.live_pilot_service import approve_user, revoke_approval
from app.services.signal_fanout_service import fanout_strategy_signal
from app.services.strategy_signal_service import accept_strategy_signal
from app.services.subscription_service import SubscriptionRisk, subscribe_user
from app.services.user_connections import upsert_dhan_account_metadata
from app.services.user_context import reset_current_user_id, set_current_user_id
from app.tests.security_test_helpers import create_authenticated_user, isolate_runtime

import hashlib
import hmac


REPO_ROOT = Path(__file__).resolve().parents[3]
SECRET_1 = "executor-one-" + "a" * 40
SECRET_2 = "executor-two-" + "b" * 40
RELAY_SECRET = "relay-" + "r" * 40


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


class RegistryClient:
    def __init__(self, code: str, ip: str):
        self.code = code
        self.ip = ip

    def get(self, url):
        if url.endswith("/health"):
            return FakeResponse({"status": "ok", "executor_code": self.code})
        return FakeResponse({"egress_ip": self.ip})


class RealExecutorClient:
    """Captures the outbound executor request and returns a real 'sent' result."""

    def __init__(self, status="sent", order_id="DHAN-TEST-1"):
        self.calls = []
        self._status = status
        self._order_id = order_id

    def post(self, url, *, content, headers):
        payload = json.loads(content)
        self.calls.append((url, payload, dict(headers)))
        code = headers["X-Nova-Executor-Code"]
        ip = "64.225.87.19" if code == "EXECUTOR_001" else "152.42.157.165"
        return FakeResponse(
            {
                "status": self._status,
                "executor_code": code,
                "correlation_id": payload["correlation_id"],
                "order_id": self._order_id,
                "egress_ip": ip,
            }
        )


class _OkResolution:
    ok = True
    security_id = "TESTSEC123"
    method = "SCRIP_MASTER"
    reason = ""


# --------------------------------------------------------------------------- #
# Shared setup helpers                                                         #
# --------------------------------------------------------------------------- #
def _safe_live_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LIVE_PILOT_ENABLED", True)
    monkeypatch.setattr(settings, "LIVE_ORDER_DRY_RUN_ONLY", True)
    monkeypatch.setattr(settings, "LIVE_PILOT_ALLOWED_STRATEGIES", "SUPERTREND_FLIP")
    monkeypatch.setattr(settings, "RELAY_ALLOWED_STRATEGIES", "SUPERTREND_FLIP")
    monkeypatch.setattr(settings, "RELAY_ENABLED", True)
    monkeypatch.setattr(settings, "RELAY_SHARED_SECRET", RELAY_SECRET)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "EXECUTOR_SHARED_SECRETS_JSON",
        json.dumps({"EXECUTOR_001": SECRET_1, "EXECUTOR_002": SECRET_2}),
    )


def _real_live_flags(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", True)
    monkeypatch.setattr(settings, "LIVE_ORDER_DRY_RUN_ONLY", False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True)
    monkeypatch.setattr(
        "app.services.live_execution_service.resolve_security_id_for_contract",
        lambda **_k: _OkResolution(),
    )


def _connect_broker(user_id: str, suffix: str) -> tuple[str, str]:
    client_id = f"1100{suffix}{user_id[-6:]}"
    token = set_current_user_id(user_id)
    access_token = f"token-{suffix}-{user_id[-6:]}"
    try:
        save_dhan_credentials(client_id, access_token)
    finally:
        reset_current_user_id(token)
    upsert_dhan_account_metadata(user_id, client_id=client_id, access_token_present=True, validated=True)
    return client_id, access_token


def _registered_node(code: str, ip: str):
    node = register_executor_node(
        executor_code=code,
        provider="digitalocean",
        droplet_name="nova-exec-user-001" if code == "EXECUTOR_001" else "nova-exec-user-002",
        region="blr1",
        reserved_ip=ip,
        health_url=f"https://{ip}/health",
        execute_url=f"https://{ip}/execute-order",
        egress_ip_url=f"https://{ip}/egress-ip",
    )
    verify_executor_health(int(node.id), client=RegistryClient(code, ip))
    return verify_executor_egress_ip(int(node.id), client=RegistryClient(code, ip))


def _prepare_user(label, suffix, node, admin_id):
    user, _cookie = create_authenticated_user(label)
    client_id, access_token = _connect_broker(user.id, suffix)
    assignment = assign_executor_to_user(user.id, int(node.id))
    verify_assignment(int(assignment.id))
    approval = approve_user(
        user_id=user.id,
        strategy_code="SUPERTREND_FLIP",
        admin_user_id=admin_id,
        max_qty=5,
        max_trades_per_day=3,
        max_daily_loss=1000,
        allowed_option_side="BOTH",
        expires_at=utc_now_dt() + timedelta(days=1),
    )
    subscribe_user(
        user_id=user.id,
        strategy_code="SUPERTREND_FLIP",
        mode="live",
        risk=SubscriptionRisk(1, 500, 2, "BOTH"),
    )
    return user, client_id, access_token, int(approval.id)


def _relay_signal(signal_id):
    timestamp = int(time.time())
    payload = {
        "strategy_code": "SUPERTREND_FLIP",
        "signal_id": signal_id,
        "symbol": "NIFTY",
        "action": "BUY",
        "option_type": "CE",
        "strike": 23000,
        "expiry": "2026-06-18",
        "timeframe": "5m",
        "price": 100.5,
        "source": "tradingview-relay",
    }
    raw_body = canonical_json_bytes(payload)
    payload["_nova_relay"] = {
        "verified": True,
        "timestamp": timestamp,
        "payload_sha256": hashlib.sha256(raw_body).hexdigest(),
        "signature": hmac.new(
            RELAY_SECRET.encode(), str(timestamp).encode() + b"." + raw_body, hashlib.sha256
        ).hexdigest(),
    }
    return accept_strategy_signal(payload)


def _two_real_jobs(tmp_path, monkeypatch):
    """Create two live jobs already flagged dry_run=False under real flags."""
    isolate_runtime(tmp_path, monkeypatch)
    _safe_live_defaults(monkeypatch)
    _real_live_flags(monkeypatch)
    admin, _ = create_authenticated_user("s6b-admin")
    node1 = _registered_node("EXECUTOR_001", "64.225.87.19")
    node2 = _registered_node("EXECUTOR_002", "152.42.157.165")
    u1 = _prepare_user("s6b-u1", "701", node1, admin.id)
    u2 = _prepare_user("s6b-u2", "702", node2, admin.id)
    signal = _relay_signal(f"S6B-{time.time_ns()}")
    fanout_strategy_signal(int(signal.id))
    with session_scope() as session:
        from sqlmodel import select

        jobs = list(session.exec(select(LiveOrderJob).where(LiveOrderJob.strategy_signal_id == signal.id)).all())
        for job in jobs:
            session.expunge(job)
    return jobs, (u1, u2)


# --------------------------------------------------------------------------- #
# Executor service settings + body helpers                                    #
# --------------------------------------------------------------------------- #
def _executor_settings(monkeypatch, *, real: bool):
    monkeypatch.setattr(settings, "EXECUTOR_CODE", "EXECUTOR_001")
    monkeypatch.setattr(settings, "EXECUTOR_RESERVED_IP", "64.225.87.19")
    monkeypatch.setattr(settings, "EXECUTOR_SHARED_SECRET", SECRET_1)
    monkeypatch.setattr(settings, "EXECUTOR_REAL_ORDERS_ENABLED", real)
    monkeypatch.setattr(settings, "EXECUTOR_TIMESTAMP_TOLERANCE_SECONDS", 60)
    if real:
        # Real mode startup validation requires a non-dev APP_ENV.
        monkeypatch.setattr(settings, "APP_ENV", "live_route_test")
    monkeypatch.setattr("app.executor_service.main._lookup_public_ip", lambda: "64.225.87.19")


def _real_body(**overrides):
    body = {
        "correlation_id": "live_corr_6b",
        "user_id": "u-6b",
        "broker_client_id": "1100777001",
        "dhan_access_token": "super-secret-live-token-XYZ",
        "order": {
            "symbol": "NIFTY",
            "action": "BUY",
            "option_type": "CE",
            "strike": 23000,
            "expiry": "2026-06-18",
            "security_id": "TESTSEC123",
            "exchange_segment": "NSE_FNO",
            "quantity": 1,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
        },
        "dry_run": False,
    }
    body.update(overrides)
    return body


def _dry_body():
    return {
        "correlation_id": "live_corr_6b_dry",
        "user_id": "u-6b",
        "broker_client_id": "11****01",
        "order": {
            "symbol": "NIFTY",
            "action": "BUY",
            "option_type": "CE",
            "strike": 23000,
            "expiry": "2026-06-18",
            "quantity": 1,
            "price": 100.5,
            "product_type": "INTRADAY",
        },
        "dry_run": True,
    }


def _send(client, body):
    raw = canonical_json_bytes(body)
    headers = signed_executor_headers("EXECUTOR_001", SECRET_1, raw)
    return client.post("/execute-order", content=raw, headers=headers)


# =========================================================================== #
# Executor-side tests                                                          #
# =========================================================================== #
def test_executor_real_order_rejected_when_real_mode_disabled(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=False)
    with TestClient(executor_app) as client:
        response = _send(client, _real_body())
    assert response.status_code == 403


def test_executor_real_order_requires_dhan_credentials(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=True)
    body = _real_body()
    body.pop("dhan_access_token")
    with TestClient(executor_app) as client:
        response = _send(client, body)
    assert response.status_code == 422


def test_executor_rejects_credentials_in_dry_run(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=False)
    body = _dry_body()
    body["dhan_access_token"] = "should-not-be-here"
    with TestClient(executor_app) as client:
        response = _send(client, body)
    assert response.status_code == 422


def test_executor_redacts_dhan_token_in_error(tmp_path, monkeypatch, caplog):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=True)
    monkeypatch.setattr(
        "app.executor_service.main.place_dhan_order",
        lambda **_k: ExecutorOrderResult(status="broker_rejected", order_id=None, message="rejected"),
    )
    token = "ULTRA-SECRET-TOKEN-DO-NOT-LEAK"
    with TestClient(executor_app) as client:
        response = _send(client, _real_body(dhan_access_token=token))
    assert response.status_code == 200
    assert token not in response.text
    assert token not in caplog.text


def test_executor_real_order_calls_dhan_once(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=True)
    calls = []

    def _fake_place(**kwargs):
        calls.append(kwargs)
        return ExecutorOrderResult(status="sent", order_id="DHAN-OK-1", message="ok")

    monkeypatch.setattr("app.executor_service.main.place_dhan_order", _fake_place)
    with TestClient(executor_app) as client:
        response = _send(client, _real_body())
    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert len(calls) == 1


def test_executor_broker_timeout_returns_sanitized_error(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=True)
    monkeypatch.setattr(
        "app.executor_service.main.place_dhan_order",
        lambda **_k: ExecutorOrderResult(status="broker_timeout", order_id=None, message="timeout"),
    )
    with TestClient(executor_app) as client:
        response = _send(client, _real_body())
    assert response.status_code == 504
    assert response.json()["detail"] == "broker_timeout"


def test_executor_broker_rejection_returns_sanitized_error(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=True)
    monkeypatch.setattr(
        "app.executor_service.main.place_dhan_order",
        lambda **_k: ExecutorOrderResult(status="broker_rejected", order_id=None, message="Dhan rejected the order: insufficient funds"),
    )
    with TestClient(executor_app) as client:
        response = _send(client, _real_body())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "broker_rejected"
    assert "rejected" in body["message"].lower()


def test_executor_request_signature_covers_credentials(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=False)
    signed_body = canonical_json_bytes(_real_body(dhan_access_token="TOKEN-A"))
    headers = signed_executor_headers("EXECUTOR_001", SECRET_1, signed_body)
    tampered = canonical_json_bytes(_real_body(dhan_access_token="TOKEN-B"))
    with TestClient(executor_app) as client:
        response = client.post("/execute-order", content=tampered, headers=headers)
    assert response.status_code == 401


def test_replayed_real_executor_request_rejected(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    init_auth_db()
    _executor_settings(monkeypatch, real=True)
    monkeypatch.setattr(
        "app.executor_service.main.place_dhan_order",
        lambda **_k: ExecutorOrderResult(status="sent", order_id="DHAN-OK-2", message="ok"),
    )
    raw = canonical_json_bytes(_real_body())
    headers = signed_executor_headers("EXECUTOR_001", SECRET_1, raw)
    with TestClient(executor_app) as client:
        first = client.post("/execute-order", content=raw, headers=headers)
        second = client.post("/execute-order", content=raw, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409


# =========================================================================== #
# Main-app live job tests                                                      #
# =========================================================================== #
def test_main_live_job_sends_short_lived_credentials_only_in_real_mode(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    client = RealExecutorClient()
    user1, client_id1, access_token1, _ = users[0]
    job1 = next(job for job in jobs if job.user_id == user1.id)
    result = execute_live_order_job(int(job1.id), client=client)
    assert result.status == "sent"
    sent_payload = client.calls[0][1]
    assert sent_payload["dry_run"] is False
    assert sent_payload["dhan_access_token"] == access_token1
    assert sent_payload["broker_client_id"] == client_id1


def test_main_live_job_does_not_send_credentials_in_dry_run(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    _safe_live_defaults(monkeypatch)  # dry-run only
    admin, _ = create_authenticated_user("s6b-dry-admin")
    node = _registered_node("EXECUTOR_001", "64.225.87.19")
    user, _client_id, _token, _ = _prepare_user("s6b-dry-u", "703", node, admin.id)
    signal = _relay_signal(f"S6B-DRY-{time.time_ns()}")
    fanout_strategy_signal(int(signal.id))
    with session_scope() as session:
        from sqlmodel import select

        job = session.exec(select(LiveOrderJob).where(LiveOrderJob.user_id == user.id)).first()
        session.expunge(job)
    client = RealExecutorClient(status="dry_run_verified")
    execute_live_order_job(int(job.id), client=client)
    sent_payload = client.calls[0][1]
    assert sent_payload["dry_run"] is True
    assert "dhan_access_token" not in sent_payload


def test_main_live_job_blocks_when_enable_live_orders_false(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    client = RealExecutorClient()
    result = execute_live_order_job(int(jobs[0].id), client=client)
    assert result.status == "blocked"
    assert result.reason_code == "live_orders_disabled"
    assert client.calls == []


def test_main_live_job_blocks_when_live_order_dry_run_only_true(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "LIVE_ORDER_DRY_RUN_ONLY", True)
    client = RealExecutorClient()
    result = execute_live_order_job(int(jobs[0].id), client=client)
    assert result.status == "blocked"
    assert result.reason_code == "dry_run_only"
    assert client.calls == []


def test_main_live_job_blocks_when_executor_not_verified(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    job1 = next(job for job in jobs if job.user_id == users[0][0].id)
    disable_executor(int(job1.executor_node_id))
    client = RealExecutorClient()
    result = execute_live_order_job(int(job1.id), client=client)
    assert result.status == "blocked"
    assert client.calls == []


def test_main_live_job_blocks_when_approval_expired(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    user1, _cid, _tok, approval_id = users[0]
    revoke_approval(approval_id)
    job1 = next(job for job in jobs if job.user_id == user1.id)
    client = RealExecutorClient()
    result = execute_live_order_job(int(job1.id), client=client)
    assert result.status == "blocked"
    assert client.calls == []


def test_main_live_job_blocks_when_kill_switch_enabled(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.services.live_execution_service.get_runtime_settings",
        lambda: {"global_kill_switch": True, "emergency_stop": False},
    )
    client = RealExecutorClient()
    result = execute_live_order_job(int(jobs[0].id), client=client)
    assert result.reason_code == "kill_switch_active"
    assert client.calls == []


def test_user1_real_job_routes_to_executor1_only(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    client = RealExecutorClient()
    job1 = next(job for job in jobs if job.user_id == users[0][0].id)
    execute_live_order_job(int(job1.id), client=client)
    assert len(client.calls) == 1
    assert client.calls[0][0] == "https://64.225.87.19/execute-order"
    assert client.calls[0][2]["X-Nova-Executor-Code"] == "EXECUTOR_001"


def test_user2_real_job_routes_to_executor2_only(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    client = RealExecutorClient()
    job2 = next(job for job in jobs if job.user_id == users[1][0].id)
    execute_live_order_job(int(job2.id), client=client)
    assert len(client.calls) == 1
    assert client.calls[0][0] == "https://152.42.157.165/execute-order"
    assert client.calls[0][2]["X-Nova-Executor-Code"] == "EXECUTOR_002"


def test_no_direct_hostinger_dhan_order_path(tmp_path, monkeypatch):
    jobs, users = _two_real_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.services.dhan_client.RealDhanClient.place_order",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Hostinger must not call Dhan directly")),
    )
    client = RealExecutorClient()
    result = execute_live_order_job(int(jobs[0].id), client=client)
    assert result.status == "sent"
    assert len(client.calls) == 1


# =========================================================================== #
# Deploy / runbook artifact tests                                             #
# =========================================================================== #
def test_runbook_mentions_disable_flags_after_test():
    runbook = (REPO_ROOT / "docs" / "CONTROLLED_REAL_LIVE_PILOT_RUNBOOK.md").read_text(encoding="utf-8")
    assert "ENABLE_LIVE_ORDERS=false" in runbook
    assert "EXECUTOR_REAL_ORDERS_ENABLED=false" in runbook
    assert "LIVE_ORDER_DRY_RUN_ONLY=true" in runbook
    assert "disable" in runbook.lower()


def test_deploy_scripts_contain_executor_non_root_hardening():
    service = (REPO_ROOT / "deploy" / "executor" / "layman-executor.service").read_text(encoding="utf-8")
    for token in (
        "User=laymanexec",
        "Group=laymanexec",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "MemoryDenyWriteExecute=true",
        "app.executor_service.main:app",
    ):
        assert token in service, f"executor systemd unit missing {token}"
    assert "User=root" not in service


def test_reserved_ip_check_script_fails_on_mismatch():
    script = REPO_ROOT / "deploy" / "executor" / "check_reserved_ip_route.sh"
    if not script.exists() or os.name == "nt":
        pytest.skip("bash reserved-ip script not runnable in this environment")
    import shutil

    if shutil.which("bash") is None:
        pytest.skip("bash unavailable")

    mismatch = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "EXECUTOR_RESERVED_IP": "64.225.87.19", "EXECUTOR_ACTUAL_IP_OVERRIDE": "10.0.0.5"},
        capture_output=True,
        text=True,
    )
    assert mismatch.returncode != 0

    match = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "EXECUTOR_RESERVED_IP": "64.225.87.19", "EXECUTOR_ACTUAL_IP_OVERRIDE": "64.225.87.19"},
        capture_output=True,
        text=True,
    )
    assert match.returncode == 0
