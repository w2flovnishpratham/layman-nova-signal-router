from __future__ import annotations

import json

import pytest

from app.config import settings
from app.main import validate_production_configuration


def _legacy_nodes_json() -> str:
    return json.dumps(
        [
            {
                "public_ip": "8.8.8.8",
                "proxy_url": "http://legacy-user:legacy-secret@8.8.4.4:8888",
            },
            {
                "public_ip": "1.1.1.1",
                "proxy_url": "http://legacy-user:legacy-secret@1.0.0.1:8888",
            },
        ]
    )


def _set_production_live_baseline(
    monkeypatch,
    *,
    enable_live_orders: bool = True,
    aws_slots_enabled: bool = True,
    aws_shared_password: str = "aws-shared-secret",
    per_slot_passwords: dict[int, str] | None = None,
    egress_nodes_json: str = "[]",
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "s" * 32, raising=False)
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "a" * 32, raising=False)
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "test-fernet-key", raising=False)
    monkeypatch.setattr(settings, "DEBUG_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False, raising=False)
    monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:pass@db.example/nova", raising=False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False, raising=False)
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", "w" * 24, raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", enable_live_orders, raising=False)
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False, raising=False)
    monkeypatch.setattr(settings, "EXECUTION_NODE_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "STRATEGY_JOB_WORKER_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AWS_PROXY_SLOTS_ENABLED", aws_slots_enabled, raising=False)
    monkeypatch.setattr(settings, "AWS_PROXY_HOST", "13.203.58.220", raising=False)
    monkeypatch.setattr(settings, "AWS_PROXY_SHARED_PASSWORD", aws_shared_password, raising=False)
    for slot_number in range(1, 6):
        monkeypatch.setattr(
            settings,
            f"AWS_PROXY_SLOT_{slot_number}_PASSWORD",
            (per_slot_passwords or {}).get(slot_number, ""),
            raising=False,
        )
    monkeypatch.setattr(settings, "EGRESS_NODES_JSON", egress_nodes_json, raising=False)


def test_production_live_aws_slots_missing_password_fails_safely(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        aws_shared_password="",
        egress_nodes_json=_legacy_nodes_json(),
    )

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    message = str(exc.value)
    assert "AWS proxy slot configuration invalid" in message
    assert "AWS proxy credential is missing for slot 1." in message
    assert "legacy-secret" not in message
    assert "proxy_url" not in message
    assert "@" not in message


def test_production_live_aws_slots_valid_shared_password_passes_without_egress_json(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        aws_shared_password="aws@shared-secret",
        egress_nodes_json="not-json",
    )

    validate_production_configuration()


def test_production_live_aws_slots_valid_per_slot_passwords_pass(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        aws_shared_password="",
        per_slot_passwords={slot_number: f"slot-{slot_number}@secret" for slot_number in range(1, 6)},
        egress_nodes_json="not-json",
    )

    validate_production_configuration()


def test_production_live_orders_disabled_does_not_require_aws_password(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        enable_live_orders=False,
        aws_shared_password="",
        egress_nodes_json="not-json",
    )

    validate_production_configuration()


def test_production_live_legacy_egress_json_fallback_passes_when_aws_slots_disabled(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        aws_slots_enabled=False,
        aws_shared_password="",
        egress_nodes_json=_legacy_nodes_json(),
    )

    validate_production_configuration()
