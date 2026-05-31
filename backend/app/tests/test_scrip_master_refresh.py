from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from app.config import settings
from app.routers import setup as setup_router
from app.services import audit_logger, credential_vault, state_store


class FakeResponse:
    content = b"col_a,col_b\n" + (b"value_a,value_b\n" * 20)

    def raise_for_status(self) -> None:
        return None


def _reset_scrip_master_job() -> None:
    with setup_router._scrip_master_job_lock:
        setup_router._scrip_master_last_download.clear()
        setup_router._scrip_master_last_download.update({"downloaded_at": None, "ok": None, "error": None, "path": None})
        setup_router._scrip_master_refresh_job.update(
            {
                "job_id": None,
                "status": "IDLE",
                "started_at": None,
                "finished_at": None,
                "success": None,
                "message": None,
                "error": None,
                "path": None,
                "size_bytes": None,
                "results": [],
            }
        )


def _client(tmp_path, monkeypatch) -> TestClient:
    state_dir = tmp_path / "runtime_state"
    log_dir = tmp_path / "runtime_logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    monkeypatch.setattr("app.config.RUNTIME_STATE_DIR", state_dir)
    monkeypatch.setattr(settings, "DHAN_SCRIP_MASTER_PATH", str(tmp_path / "scrip_master.csv"))
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    _reset_scrip_master_job()

    from app import main
    monkeypatch.setattr(main, "start_instrument_cache_warmup", lambda: None)

    return TestClient(main.app)


def test_scrip_master_refresh_returns_job_before_download_finishes(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake_get(*args, **kwargs):
        started.set()
        assert release.wait(timeout=3), "test did not release fake scrip master download"
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    with _client(tmp_path, monkeypatch) as client:
        response = client.post("/api/setup/scrip-master/refresh")

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["status"] == "RUNNING"
        assert body["job_id"]
        assert started.wait(timeout=1)

        duplicate = client.post("/api/setup/scrip-master/refresh").json()
        assert duplicate["accepted"] is False
        assert duplicate["job_id"] == body["job_id"]

        release.set()
        deadline = time.time() + 3
        status = {}
        while time.time() < deadline:
            status = client.get("/api/setup/scrip-master/status").json()
            if status["refresh_job"]["status"] == "SUCCEEDED":
                break
            time.sleep(0.05)

        assert status["refresh_job"]["status"] == "SUCCEEDED"
        assert status["refresh_job"]["job_id"] == body["job_id"]
        assert status["last_download"]["ok"] is True
        assert status["configured_path"]["exists"] is True


def test_scrip_master_refresh_job_records_failure(tmp_path, monkeypatch):
    def fake_get(*args, **kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr("httpx.get", fake_get)

    with _client(tmp_path, monkeypatch) as client:
        response = client.post("/api/setup/scrip-master/refresh")
        job_id = response.json()["job_id"]

        deadline = time.time() + 3
        status = {}
        while time.time() < deadline:
            status = client.get("/api/setup/scrip-master/status").json()
            if status["refresh_job"]["status"] == "FAILED":
                break
            time.sleep(0.05)

        assert status["refresh_job"]["status"] == "FAILED"
        assert status["refresh_job"]["job_id"] == job_id
        assert status["last_download"]["ok"] is False
        assert "failed" in status["refresh_job"]["message"].lower()
