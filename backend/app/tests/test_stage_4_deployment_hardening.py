from __future__ import annotations

import os
import json
from pathlib import Path

import pytest

from app import config
from app.config import settings
from app.main import _health, readiness, validate_production_configuration
from app.middleware.user_scope import public_request_path
from app.routers.safety import _live_blockers
from app.services import readiness as readiness_service


def _set_ready_checks(monkeypatch) -> None:
    monkeypatch.setattr(readiness_service, "database_ready", lambda: True)
    monkeypatch.setattr(readiness_service, "migrations_ready", lambda: True)
    monkeypatch.setattr(readiness_service, "vault_ready", lambda: True)
    monkeypatch.setattr(readiness_service, "runtime_directory_ready", lambda _path: True)
    monkeypatch.setattr(readiness_service, "configured_worker_role", lambda: "web")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "ENABLE_TRADING_WORKERS", False)
    monkeypatch.setattr(settings, "DEBUG_ENABLED", False)


def test_health_is_lightweight_and_non_secret() -> None:
    assert _health() == {"status": "ok"}
    assert public_request_path("/api/readiness") is True


def test_readiness_endpoint_returns_503_when_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.readiness_payload",
        lambda: ({"status": "not_ready", "database": "unavailable"}, False),
    )

    response = readiness()

    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "not_ready", "database": "unavailable"}


def test_readiness_returns_only_safe_categorical_status(monkeypatch) -> None:
    _set_ready_checks(monkeypatch)

    payload, ready = readiness_service.readiness_payload()

    assert ready is True
    assert payload == {
        "status": "ready",
        "database": "ok",
        "migrations": "ok",
        "vault": "ok",
        "runtime_state": "ok",
        "runtime_logs": "ok",
        "auth_policy": "ok",
        "live_policy": "disabled",
        "worker_policy": "ok",
        "debug": "disabled",
    }
    serialized = repr(payload).lower()
    assert "postgresql://" not in serialized
    assert "token" not in serialized
    assert "secret" not in serialized
    assert "/var/" not in serialized
    assert "\\\\" not in serialized


@pytest.mark.parametrize(
    ("failed_check", "field", "expected"),
    [
        ("database_ready", "database", "unavailable"),
        ("migrations_ready", "migrations", "out_of_date"),
        ("vault_ready", "vault", "unavailable"),
        ("runtime_directory_ready", "runtime_state", "unavailable"),
    ],
)
def test_readiness_fails_closed_when_dependency_is_broken(
    monkeypatch,
    failed_check: str,
    field: str,
    expected: str,
) -> None:
    _set_ready_checks(monkeypatch)
    if failed_check == "runtime_directory_ready":
        monkeypatch.setattr(
            readiness_service,
            "runtime_directory_ready",
            lambda path: path != readiness_service.RUNTIME_STATE_DIR,
        )
    else:
        monkeypatch.setattr(readiness_service, failed_check, lambda: False)

    payload, ready = readiness_service.readiness_payload()

    assert ready is False
    assert payload["status"] == "not_ready"
    assert payload[field] == expected


def test_production_environment_file_rejects_repo_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")

    with pytest.raises(RuntimeError, match="outside the repository"):
        config.validate_production_environment_file(config.REPO_ROOT / ".env")


def test_production_environment_file_rejects_filesystem_root(monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    root = Path(Path.cwd().anchor)

    with pytest.raises(RuntimeError, match="filesystem root"):
        config.validate_production_environment_file(root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission policy")
def test_production_environment_file_requires_mode_0600(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    env_file = tmp_path / "layman.env"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    env_file.chmod(0o640)
    monkeypatch.setattr(config, "PRODUCTION_ENV_FILE", env_file)

    with pytest.raises(RuntimeError, match="0600"):
        config.validate_production_environment_file(env_file)


def test_production_boot_rejects_debug_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "s" * 32)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "ops@example.test")
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+psycopg://user:pass@localhost/layman")
    monkeypatch.setattr(settings, "AUTH_DATABASE_URL", "")
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", True)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL")
    monkeypatch.setattr(settings, "DEBUG_ENABLED", True)

    with pytest.raises(RuntimeError, match="DEBUG_ENABLED"):
        validate_production_configuration()


def test_stage_4_still_blocks_live(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", True)
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL", False)
    monkeypatch.setattr(settings, "UNIQUE_EGRESS_PER_USER_REQUIRED", True)

    blockers = _live_blockers(
        broker_connected=True,
        replay_protection=True,
        egress_ready=False,
        market_hours_valid=True,
        authenticated_runtime=True,
    )

    assert "Live orders are disabled by system policy." in blockers
    assert "Webhook signing relay is not configured." in blockers
    assert "Executor routing and unique egress IP are not verified." in blockers
