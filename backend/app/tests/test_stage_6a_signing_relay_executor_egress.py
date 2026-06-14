from __future__ import annotations

import json
import hashlib
import hmac
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import func, select

from app.auth.db import session_scope
from app.auth.models import (
    ExecutorNode,
    LiveOrderJob,
    StrategySignal,
    UserExecutorAssignment,
    UserSignalJob,
    utc_now_dt,
)
from app.config import settings
from app.executor_service.main import app as executor_app
from app.main import app
from app.services.credential_vault import save_dhan_credentials
from app.services.executor_registry_service import (
    ExecutorRegistryConflict,
    assign_executor_to_user,
    register_executor_node,
    verified_route_for_user,
    verify_assignment,
    verify_executor_egress_ip,
    verify_executor_health,
)
from app.services.executor_signing import canonical_json_bytes, signed_executor_headers
from app.services.live_execution_service import execute_live_order_job
from app.services.live_pilot_service import approve_user
from app.services.signal_fanout_service import fanout_strategy_signal
from app.services.strategy_signal_service import accept_strategy_signal
from app.services.subscription_service import SubscriptionForbidden, SubscriptionRisk, subscribe_user
from app.services.user_connections import upsert_dhan_account_metadata
from app.services.user_context import reset_current_user_id, set_current_user_id
from app.tests.security_test_helpers import authenticate_client, create_authenticated_user, isolate_runtime


SECRET_1 = "executor-one-" + "a" * 40
SECRET_2 = "executor-two-" + "b" * 40
RELAY_SECRET = "relay-" + "r" * 40


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


class ExecutorClient:
    def __init__(self):
        self.calls = []

    def post(self, url, *, content, headers):
        payload = json.loads(content)
        self.calls.append((url, payload, dict(headers)))
        code = headers["X-Nova-Executor-Code"]
        ip = "64.225.87.19" if code == "EXECUTOR_001" else "152.42.157.165"
        return FakeResponse(
            {
                "status": "dry_run_verified",
                "executor_code": code,
                "correlation_id": payload["correlation_id"],
                "egress_ip": ip,
            }
        )


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


def _connect_broker(user_id: str, suffix: str) -> None:
    client_id = f"1100{suffix}{user_id[-6:]}"
    token = set_current_user_id(user_id)
    try:
        save_dhan_credentials(client_id, f"token-{suffix}-{user_id[-6:]}")
    finally:
        reset_current_user_id(token)
    upsert_dhan_account_metadata(
        user_id,
        client_id=client_id,
        access_token_present=True,
        validated=True,
    )


def _registered_node(code: str, ip: str) -> ExecutorNode:
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
    user, cookie = create_authenticated_user(label)
    _connect_broker(user.id, suffix)
    assignment = assign_executor_to_user(user.id, int(node.id))
    verify_assignment(int(assignment.id))
    approve_user(
        user_id=user.id,
        strategy_code="SUPERTREND_FLIP",
        admin_user_id=admin_id,
        max_qty=5,
        max_trades_per_day=3,
        max_daily_loss=1000,
        allowed_option_side="BOTH",
        expires_at=utc_now_dt() + timedelta(days=1),
    )
    subscription = subscribe_user(
        user_id=user.id,
        strategy_code="SUPERTREND_FLIP",
        mode="live",
        risk=SubscriptionRisk(1, 500, 2, "BOTH"),
    )
    return user, cookie, subscription


def _relay_signal(signal_id="STAGE6A-SUPERTREND-1"):
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
            RELAY_SECRET.encode(),
            str(timestamp).encode() + b"." + raw_body,
            hashlib.sha256,
        ).hexdigest(),
    }
    return accept_strategy_signal(payload)


def test_executor_node_registration(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    node = _registered_node("EXECUTOR_001", "64.225.87.19")
    assert node.executor_code == "EXECUTOR_001"
    assert node.status == "active"


def test_executor_egress_verification_matches_reserved_ip(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    node = _registered_node("EXECUTOR_001", "64.225.87.19")
    assert node.last_egress_ip == node.reserved_ip


def test_executor_egress_mismatch_blocks_live(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    node = register_executor_node(
        executor_code="EXECUTOR_001",
        provider="digitalocean",
        droplet_name="nova-exec-user-001",
        region="blr1",
        reserved_ip="64.225.87.19",
        health_url="https://64.225.87.19/health",
        execute_url="https://64.225.87.19/execute-order",
        egress_ip_url="https://64.225.87.19/egress-ip",
    )
    verify_executor_health(int(node.id), client=RegistryClient("EXECUTOR_001", "64.225.87.20"))
    with pytest.raises(ExecutorRegistryConflict):
        verify_executor_egress_ip(int(node.id), client=RegistryClient("EXECUTOR_001", "64.225.87.20"))


def test_assign_executor_to_user(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user, _ = create_authenticated_user("stage6-assign")
    _connect_broker(user.id, "601")
    node = _registered_node("EXECUTOR_001", "64.225.87.19")
    assignment = assign_executor_to_user(user.id, int(node.id))
    assert assignment.status == "pending_whitelist"
    assert verify_assignment(int(assignment.id)).status == "verified"


def test_executor_cannot_be_assigned_to_two_users(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    first, _ = create_authenticated_user("stage6-exclusive-a")
    second, _ = create_authenticated_user("stage6-exclusive-b")
    _connect_broker(first.id, "602")
    _connect_broker(second.id, "603")
    node = _registered_node("EXECUTOR_001", "64.225.87.19")
    assign_executor_to_user(first.id, int(node.id))
    with pytest.raises(ExecutorRegistryConflict):
        assign_executor_to_user(second.id, int(node.id))


def test_user_cannot_route_through_another_executor(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    user, _ = create_authenticated_user("stage6-route-owner")
    _connect_broker(user.id, "604")
    node1 = _registered_node("EXECUTOR_001", "64.225.87.19")
    _registered_node("EXECUTOR_002", "152.42.157.165")
    assignment = assign_executor_to_user(user.id, int(node1.id))
    verify_assignment(int(assignment.id))
    assert verified_route_for_user(user.id).node.executor_code == "EXECUTOR_001"


def test_live_subscription_rejected_without_admin_approval(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _safe_live_defaults(monkeypatch)
    user, _ = create_authenticated_user("stage6-no-approval")
    _connect_broker(user.id, "605")
    node = _registered_node("EXECUTOR_001", "64.225.87.19")
    verify_assignment(int(assign_executor_to_user(user.id, int(node.id)).id))
    with pytest.raises(SubscriptionForbidden):
        subscribe_user(
            user_id=user.id,
            strategy_code="SUPERTREND_FLIP",
            mode="live",
            risk=SubscriptionRisk(1, 500, 2, "BOTH"),
        )


def test_live_subscription_allowed_with_approval_and_executor(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _safe_live_defaults(monkeypatch)
    admin, _ = create_authenticated_user("stage6-admin-subscribe")
    node = _registered_node("EXECUTOR_001", "64.225.87.19")
    user, _, subscription = _prepare_user("stage6-approved", "606", node, admin.id)
    assert subscription.user_id == user.id
    assert subscription.mode == "live"


def test_supertrend_signal_creates_two_isolated_live_jobs(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _safe_live_defaults(monkeypatch)
    admin, _ = create_authenticated_user("stage6-admin-fanout")
    node1 = _registered_node("EXECUTOR_001", "64.225.87.19")
    node2 = _registered_node("EXECUTOR_002", "152.42.157.165")
    user1, _, _ = _prepare_user("stage6-user1", "607", node1, admin.id)
    user2, _, _ = _prepare_user("stage6-user2", "608", node2, admin.id)
    signal = _relay_signal()
    fanout_strategy_signal(int(signal.id))
    with session_scope() as session:
        jobs = list(session.exec(select(LiveOrderJob)).all())
    assert len(jobs) == 2
    assert {job.user_id for job in jobs} == {user1.id, user2.id}
    assert {job.executor_node_id for job in jobs} == {node1.id, node2.id}


def test_user1_job_routes_to_executor1(tmp_path, monkeypatch) -> None:
    client, jobs, users = _two_user_jobs(tmp_path, monkeypatch)
    job1 = next(job for job in jobs if job.user_id == users[0].id)
    execute_live_order_job(int(job1.id), client=client)
    assert client.calls[0][0] == "https://64.225.87.19/execute-order"
    assert client.calls[0][2]["X-Nova-Executor-Code"] == "EXECUTOR_001"


def test_user2_job_routes_to_executor2(tmp_path, monkeypatch) -> None:
    client, jobs, users = _two_user_jobs(tmp_path, monkeypatch)
    job2 = next(job for job in jobs if job.user_id == users[1].id)
    execute_live_order_job(int(job2.id), client=client)
    assert client.calls[0][0] == "https://152.42.157.165/execute-order"
    assert client.calls[0][2]["X-Nova-Executor-Code"] == "EXECUTOR_002"


def test_live_job_dry_run_does_not_call_dhan(tmp_path, monkeypatch) -> None:
    client, jobs, _ = _two_user_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.services.dhan_client.RealDhanClient",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Dhan must not be called")),
    )
    result = execute_live_order_job(int(jobs[0].id), client=client)
    assert result.status == "dry_run_verified"


def test_real_live_job_blocked_when_enable_live_orders_false(tmp_path, monkeypatch) -> None:
    client, jobs, _ = _two_user_jobs(tmp_path, monkeypatch)
    with session_scope() as session:
        stored = session.get(LiveOrderJob, jobs[0].id)
        stored.dry_run = False
        session.add(stored)
        session.commit()
    result = execute_live_order_job(int(jobs[0].id), client=client)
    assert result.status == "blocked"
    assert result.reason_code == "live_orders_disabled"
    assert client.calls == []


def test_final_kill_switch_blocks_before_executor_call(tmp_path, monkeypatch) -> None:
    client, jobs, users = _two_user_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.services.live_execution_service.get_runtime_settings",
        lambda: {"global_kill_switch": True, "emergency_stop": False},
    )
    result = execute_live_order_job(int(next(job for job in jobs if job.user_id == users[0].id).id), client=client)
    assert result.reason_code == "kill_switch_active"
    assert client.calls == []


def test_executor_request_signature_required(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _executor_settings(monkeypatch)
    with TestClient(executor_app) as client:
        response = client.post("/execute-order", json={"dry_run": True})
    assert response.status_code == 401


def test_executor_request_replay_rejected(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _executor_settings(monkeypatch)
    monkeypatch.setattr("app.executor_service.main._lookup_public_ip", lambda: "64.225.87.19")
    body = canonical_json_bytes(_executor_body())
    headers = signed_executor_headers("EXECUTOR_001", SECRET_1, body)
    with TestClient(executor_app) as client:
        first = client.post("/execute-order", content=body, headers=headers)
        second = client.post("/execute-order", content=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409


def test_executor_rejects_wrong_executor_code(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _executor_settings(monkeypatch)
    body = canonical_json_bytes(_executor_body())
    headers = signed_executor_headers("EXECUTOR_002", SECRET_1, body)
    with TestClient(executor_app) as client:
        response = client.post("/execute-order", content=body, headers=headers)
    assert response.status_code == 403


def test_executor_never_logs_access_token(tmp_path, monkeypatch, caplog) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _executor_settings(monkeypatch)
    payload = _executor_body()
    payload["access_token"] = "never-log-this-token"
    body = canonical_json_bytes(payload)
    headers = signed_executor_headers("EXECUTOR_001", SECRET_1, body)
    with TestClient(executor_app) as client:
        response = client.post("/execute-order", content=body, headers=headers)
    assert response.status_code == 422
    assert "never-log-this-token" not in caplog.text


def test_hostinger_main_never_places_dhan_live_order_directly(tmp_path, monkeypatch) -> None:
    client, jobs, _ = _two_user_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.services.dhan_client.RealDhanClient.place_order",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Hostinger must not call Dhan")),
    )
    assert execute_live_order_job(int(jobs[0].id), client=client).status == "dry_run_verified"


def test_forged_relay_metadata_blocks_before_executor_call(tmp_path, monkeypatch) -> None:
    client, jobs, _ = _two_user_jobs(tmp_path, monkeypatch)
    with session_scope() as session:
        signal = session.get(StrategySignal, jobs[0].strategy_signal_id)
        forged = dict(signal.raw_payload_redacted_json)
        forged["_nova_relay"] = {**forged["_nova_relay"], "signature": "0" * 64}
        signal.raw_payload_redacted_json = forged
        session.add(signal)
        session.commit()
    result = execute_live_order_job(int(jobs[0].id), client=client)
    assert result.reason_code == "signed_relay_required"
    assert client.calls == []


def test_same_signal_does_not_duplicate_live_order(tmp_path, monkeypatch) -> None:
    _, jobs, _ = _two_user_jobs(tmp_path, monkeypatch)
    with session_scope() as session:
        signal_id = jobs[0].strategy_signal_id
    fanout_strategy_signal(signal_id)
    with session_scope() as session:
        count = session.exec(select(func.count()).select_from(LiveOrderJob)).one()
    assert count == 2


def test_different_users_same_signal_is_allowed(tmp_path, monkeypatch) -> None:
    _, jobs, users = _two_user_jobs(tmp_path, monkeypatch)
    assert len(jobs) == 2
    assert {job.user_id for job in jobs} == {users[0].id, users[1].id}


def test_live_job_status_user_scoped(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    _safe_live_defaults(monkeypatch)
    admin, admin_cookie = create_authenticated_user("stage6-admin-scope")
    user, user_cookie = create_authenticated_user("stage6-user-scope")
    node = _registered_node("EXECUTOR_001", "64.225.87.19")
    _connect_broker(admin.id, "611")
    assignment = assign_executor_to_user(admin.id, int(node.id))
    verify_assignment(int(assignment.id))
    approve_user(
        user_id=admin.id,
        strategy_code="SUPERTREND_FLIP",
        admin_user_id=admin.id,
        max_qty=2,
        max_trades_per_day=2,
        max_daily_loss=500,
        allowed_option_side="BOTH",
        expires_at=utc_now_dt() + timedelta(days=1),
    )
    subscribe_user(
        user_id=admin.id,
        strategy_code="SUPERTREND_FLIP",
        mode="live",
        risk=SubscriptionRisk(1, 100, 1, "BOTH"),
    )
    fanout_strategy_signal(int(_relay_signal("STAGE6A-SCOPE").id))
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        admin_headers = authenticate_client(client, admin_cookie, "stage6-admin-scope-csrf")
        assert len(client.get("/api/me/live-order-jobs", headers=admin_headers).json()["jobs"]) == 1
        user_headers = authenticate_client(client, user_cookie, "stage6-user-scope-csrf")
        assert client.get("/api/me/live-order-jobs", headers=user_headers).json()["jobs"] == []
    assert user.id != admin.id


def test_non_admin_cannot_approve_live_pilot(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, _ = create_authenticated_user("stage6-real-admin")
    user, cookie = create_authenticated_user("stage6-not-admin")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        headers = authenticate_client(client, cookie, "stage6-not-admin-csrf")
        response = client.post(
            "/api/admin/live-pilot/approvals",
            headers=headers,
            json={
                "user_id": user.id,
                "strategy_code": "SUPERTREND_FLIP",
                "max_qty": 1,
                "max_trades_per_day": 1,
                "max_daily_loss": 100,
                "allowed_option_side": "BOTH",
                "expires_at": (utc_now_dt() + timedelta(days=1)).isoformat(),
            },
        )
    assert response.status_code == 403


def test_non_admin_cannot_assign_executor(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    admin, _ = create_authenticated_user("stage6-real-admin-assign")
    user, cookie = create_authenticated_user("stage6-not-admin-assign")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", admin.email)
    with TestClient(app) as client:
        headers = authenticate_client(client, cookie, "stage6-not-admin-assign-csrf")
        response = client.post(
            "/api/admin/executors/1/assign",
            headers=headers,
            json={"user_id": user.id},
        )
    assert response.status_code == 403


def test_signing_relay_requires_token_and_active_approval(tmp_path, monkeypatch) -> None:
    isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "RELAY_ENABLED", True)
    monkeypatch.setattr(settings, "RELAY_SHARED_SECRET", "relay-" + "r" * 40)
    with TestClient(app) as client:
        response = client.post("/relay/tradingview/SUPERTREND_FLIP", json=_relay_http_body())
    assert response.status_code == 401


def _two_user_jobs(tmp_path, monkeypatch):
    isolate_runtime(tmp_path, monkeypatch)
    _safe_live_defaults(monkeypatch)
    admin, _ = create_authenticated_user("stage6-route-admin")
    node1 = _registered_node("EXECUTOR_001", "64.225.87.19")
    node2 = _registered_node("EXECUTOR_002", "152.42.157.165")
    user1, _, _ = _prepare_user("stage6-route-user1", "609", node1, admin.id)
    user2, _, _ = _prepare_user("stage6-route-user2", "610", node2, admin.id)
    signal = _relay_signal(f"STAGE6A-ROUTE-{time.time_ns()}")
    fanout_strategy_signal(int(signal.id))
    with session_scope() as session:
        jobs = list(session.exec(select(LiveOrderJob).where(LiveOrderJob.strategy_signal_id == signal.id)).all())
        for job in jobs:
            session.expunge(job)
    return ExecutorClient(), jobs, (user1, user2)


def _executor_settings(monkeypatch):
    monkeypatch.setattr(settings, "EXECUTOR_CODE", "EXECUTOR_001")
    monkeypatch.setattr(settings, "EXECUTOR_RESERVED_IP", "64.225.87.19")
    monkeypatch.setattr(settings, "EXECUTOR_SHARED_SECRET", SECRET_1)
    monkeypatch.setattr(settings, "EXECUTOR_REAL_ORDERS_ENABLED", False)
    monkeypatch.setattr(settings, "EXECUTOR_TIMESTAMP_TOLERANCE_SECONDS", 60)


def _executor_body():
    return {
        "correlation_id": "corr-stage6a",
        "user_id": "u-stage6a",
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


def _relay_http_body():
    return {
        "signal_id": "STAGE6A-RELAY-HTTP",
        "action": "BUY",
        "symbol": "NIFTY",
        "option_type": "CE",
        "strike": 23000,
        "expiry": "2026-06-18",
        "timeframe": "5m",
        "price": 100.5,
        "source": "tradingview",
    }
