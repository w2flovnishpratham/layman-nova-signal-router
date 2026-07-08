from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.feature_flags import (
    CUSTOM_WEBHOOKS,
    MULTI_STRATEGY_FANOUT,
    MULTI_STRATEGY_MODEL,
    STRATEGY_CATALOG_UI,
    STRATEGY_INSTANCE_MUTATION_DEBUG,
    V2_PAPER_RUNNER_DEBUG,
)


def _client(monkeypatch, *, debug_enabled: bool = True) -> TestClient:
    from app.config import settings
    from app.routers import debug

    monkeypatch.setattr(settings, "DEBUG_ENABLED", debug_enabled, raising=False)
    app = FastAPI()
    app.include_router(debug.router, prefix="/api/debug")
    return TestClient(app)


def _set_flags(monkeypatch, *, fanout: bool = True, runner_debug: bool | None = True) -> None:
    monkeypatch.setenv(MULTI_STRATEGY_FANOUT, "true" if fanout else "false")
    if runner_debug is None:
        monkeypatch.delenv(V2_PAPER_RUNNER_DEBUG, raising=False)
    else:
        monkeypatch.setenv(V2_PAPER_RUNNER_DEBUG, "true" if runner_debug else "false")


def _payload(**overrides: Any) -> dict[str, Any]:
    data = {
        "version": "nova.v1",
        "secret": "test-only-secret",
        "signal_id": "debug-v2-paper-001",
        "strategy_code": "SUPERTREND_V1",
        "action": "ENTRY",
        "intent": "BULLISH",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": "AUTO",
        "strike_mode": "MANUAL",
        "strike": 25000,
        "expiry_mode": "MANUAL",
        "expiry": "2026-07-09",
        "qty_mode": "LOTS",
        "lots": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "backend",
        "timestamp": "2026-07-03T09:15:00+05:30",
    }
    data.update(overrides)
    return data


def _run_body(**overrides: Any) -> dict[str, Any]:
    data = {
        "strategy_code": "SUPERTREND_V1",
        "payload": _payload(),
        "max_jobs": 3,
        "confirm_paper_only": True,
    }
    data.update(overrides)
    return data


def _install_fake_session(monkeypatch, db: Any = "fake-db") -> None:
    from app.routers import debug

    @contextmanager
    def fake_session_scope():
        yield db

    monkeypatch.setattr(debug, "session_scope", fake_session_scope)


class _FakeRunnerResult:
    def __init__(self, **overrides: Any) -> None:
        self.data = {
            "ok": True,
            "strategy_code": "SUPERTREND_V1",
            "signal_id": "debug-v2-paper-001",
            "planned_count": 1,
            "job_created_count": 1,
            "processed_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "skipped_count": 0,
            "errors": [],
            "job_results": [
                {
                    "ok": True,
                    "status": "v2_completed_paper",
                    "route_result_summary": {
                        "order_id": "paper-1",
                        "access_token": "raw-token-secret",
                        "raw_response": {"secret": "broker-secret"},
                        "nested": {"proxy_url": "http://proxy-secret", "keep": "safe"},
                    },
                }
            ],
            "payload": {"secret": "raw-webhook-secret"},
            "secret": "top-level-secret",
        }
        self.data.update(overrides)

    def as_dict(self) -> dict[str, Any]:
        return self.data


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                return True
            if _contains_key(item, forbidden):
                return True
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def test_run_once_unavailable_when_debug_disabled(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    monkeypatch.setattr(
        debug,
        "run_v2_paper_fanout_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runner should not be called")),
    )

    response = _client(monkeypatch, debug_enabled=False).post(
        "/api/debug/v2/paper-fanout/run-once",
        json=_run_body(),
    )

    assert response.status_code == 404


def test_run_once_unavailable_when_multi_strategy_fanout_disabled(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=False, runner_debug=True)
    monkeypatch.setattr(
        debug,
        "run_v2_paper_fanout_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runner should not be called")),
    )

    response = _client(monkeypatch).post("/api/debug/v2/paper-fanout/run-once", json=_run_body())

    assert response.status_code == 403
    assert "MULTI_STRATEGY_FANOUT" in response.json()["detail"]


def test_run_once_unavailable_when_v2_paper_runner_debug_default_off(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=None)
    monkeypatch.setattr(
        debug,
        "run_v2_paper_fanout_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runner should not be called")),
    )

    response = _client(monkeypatch).post("/api/debug/v2/paper-fanout/run-once", json=_run_body())

    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_confirm_paper_only_false_returns_403_and_does_nothing(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    monkeypatch.setattr(
        debug,
        "run_v2_paper_fanout_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runner should not be called")),
    )

    response = _client(monkeypatch).post(
        "/api/debug/v2/paper-fanout/run-once",
        json=_run_body(confirm_paper_only=False),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "confirm_paper_only must be true."


def test_max_jobs_above_cap_is_rejected_before_runner_call(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    monkeypatch.setattr(
        debug,
        "run_v2_paper_fanout_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runner should not be called")),
    )

    response = _client(monkeypatch).post(
        "/api/debug/v2/paper-fanout/run-once",
        json=_run_body(max_jobs=6),
    )

    assert response.status_code == 422


def test_run_once_calls_runner_and_sanitizes_response(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    _install_fake_session(monkeypatch, db="debug-db")
    calls: list[dict[str, Any]] = []

    def fake_runner(db, raw_payload, strategy_code, *, max_jobs):
        calls.append(
            {
                "db": db,
                "raw_payload": raw_payload,
                "strategy_code": strategy_code,
                "max_jobs": max_jobs,
            }
        )
        return _FakeRunnerResult()

    monkeypatch.setattr(debug, "run_v2_paper_fanout_once", fake_runner)

    response = _client(monkeypatch).post("/api/debug/v2/paper-fanout/run-once", json=_run_body())

    assert response.status_code == 200
    assert calls == [
        {
            "db": "debug-db",
            "raw_payload": _payload(),
            "strategy_code": "SUPERTREND_V1",
            "max_jobs": 3,
        }
    ]
    body = response.json()
    assert body["ok"] is True
    assert body["completed_count"] == 1
    assert body["job_results"][0]["route_result_summary"]["nested"] == {"keep": "safe"}
    assert _contains_key(
        body,
        {"secret", "access_token", "headers", "raw_response", "proxy_url", "payload"},
    ) is False


def test_process_ready_calls_service_and_sanitizes_response(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    _install_fake_session(monkeypatch, db="debug-db")
    calls: list[dict[str, Any]] = []

    def fake_process_ready(db, *, max_jobs):
        calls.append({"db": db, "max_jobs": max_jobs})
        return _FakeRunnerResult(strategy_code="", signal_id=None, job_created_count=0)

    monkeypatch.setattr(debug, "process_ready_v2_paper_jobs_once", fake_process_ready)

    response = _client(monkeypatch).post(
        "/api/debug/v2/paper-fanout/process-ready",
        json={"max_jobs": 2, "confirm_paper_only": True},
    )

    assert response.status_code == 200
    assert calls == [{"db": "debug-db", "max_jobs": 2}]
    assert response.json()["processed_count"] == 1
    assert _contains_key(response.json(), {"secret", "access_token", "raw_response", "payload"}) is False


def test_run_once_rejects_real_orders_execution_marker(monkeypatch):
    from app.routers import debug

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    monkeypatch.setattr(
        debug,
        "run_v2_paper_fanout_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runner should not be called")),
    )

    response = _client(monkeypatch).post(
        "/api/debug/v2/paper-fanout/run-once",
        json=_run_body(payload=_payload(metadata={"execution_mode": "real_orders"})),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Only paper_live_data execution is allowed."


def test_debug_endpoint_does_not_call_dhan_live_client(monkeypatch):
    from app.routers import debug
    from app.services.dhan_client import RealDhanClient

    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    _install_fake_session(monkeypatch)
    dhan_calls = []
    monkeypatch.setattr(
        RealDhanClient,
        "place_order",
        lambda self, **kwargs: dhan_calls.append(kwargs) or {"success": False},
    )
    monkeypatch.setattr(debug, "run_v2_paper_fanout_once", lambda *args, **kwargs: _FakeRunnerResult())

    response = _client(monkeypatch).post("/api/debug/v2/paper-fanout/run-once", json=_run_body())

    assert response.status_code == 200
    assert dhan_calls == []


def test_no_startup_worker_or_public_webhook_wiring_added():
    app_root = Path(__file__).resolve().parents[1]
    checked_paths = [
        app_root / "main.py",
        app_root / "routers" / "webhook.py",
        app_root / "routers" / "strategies.py",
        app_root / "workers" / "strategy_job_worker.py",
    ]

    for path in checked_paths:
        source = path.read_text(encoding="utf-8")
        assert "v2/paper-fanout/run-once" not in source
        assert "v2/paper-fanout/process-ready" not in source


def test_legacy_debug_feature_flags_endpoint_still_works(monkeypatch):
    _set_flags(monkeypatch, fanout=True, runner_debug=True)
    monkeypatch.setenv(MULTI_STRATEGY_MODEL, "true")
    monkeypatch.setenv(CUSTOM_WEBHOOKS, "false")
    monkeypatch.delenv(STRATEGY_CATALOG_UI, raising=False)

    response = _client(monkeypatch).get("/api/debug/feature-flags")

    assert response.status_code == 200
    assert response.json() == {
        MULTI_STRATEGY_MODEL: True,
        MULTI_STRATEGY_FANOUT: True,
        V2_PAPER_RUNNER_DEBUG: True,
        CUSTOM_WEBHOOKS: False,
        STRATEGY_CATALOG_UI: False,
        STRATEGY_INSTANCE_MUTATION_DEBUG: False,
    }
    assert all(isinstance(value, bool) for value in response.json().values())
