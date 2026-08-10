from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

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
    app_env: str = "production",
    auth_required: bool = True,
    webhook_hmac_required: bool = True,
    enable_live_orders: bool = True,
    aws_slots_enabled: bool = True,
    aws_shared_password: str = "aws-shared-secret",
    per_slot_passwords: dict[int, str] | None = None,
    egress_nodes_json: str = "[]",
    payment_provider: str = "razorpay",
    razorpay_key_id: str = "test-razorpay-key-id",
    razorpay_key_secret: str = "razorpay-key-secret",
    razorpay_webhook_secret: str = "razorpay-webhook-secret",
    razorpay_plan_premium_monthly: str = "plan_premium_monthly",
    payment_simulation_enabled: bool = False,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", app_env, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "REAL", raising=False)
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "s" * 32, raising=False)
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "a" * 32, raising=False)
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "test-fernet-key", raising=False)
    monkeypatch.setattr(settings, "DEBUG_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_SECURITY_ID", False, raising=False)
    monkeypatch.setattr(settings, "DEFAULT_SECURITY_ID", "", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:pass@db.example/nova", raising=False)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", auth_required, raising=False)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "google-client-id", raising=False)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "google-client-secret", raising=False)
    monkeypatch.setattr(settings, "GOOGLE_REDIRECT_URI", "https://api.example.com/api/auth/google/callback", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", webhook_hmac_required, raising=False)
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", "w" * 24, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", payment_provider, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", razorpay_key_id, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", razorpay_key_secret, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", razorpay_webhook_secret, raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_PLAN_PREMIUM_MONTHLY", razorpay_plan_premium_monthly, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_SIMULATION_ENABLED", payment_simulation_enabled, raising=False)
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


def test_production_auth_disabled_fails_startup_safely(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        auth_required=False,
        enable_live_orders=False,
    )
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "google-secret-should-not-leak", raising=False)

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    message = str(exc.value)
    assert message == "AUTH_REQUIRED must be true in production."
    assert "google-secret-should-not-leak" not in message
    assert "GOOGLE_CLIENT_SECRET" not in message


def test_production_missing_database_url_fails_startup_safely(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        enable_live_orders=False,
    )
    monkeypatch.setattr(settings, "DATABASE_URL", "", raising=False)

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    assert str(exc.value) == "DATABASE_URL must be set in production (Neon PostgreSQL)."


def test_production_schema_startup_uses_alembic_guard_not_create_all(monkeypatch):
    import app.main as main_module

    _set_production_live_baseline(
        monkeypatch,
        enable_live_orders=False,
    )
    calls: dict[str, bool] = {}

    def fake_validate_migrations() -> None:
        calls["migration_guard"] = True

    def fail_init_db() -> None:
        raise AssertionError("production startup must not call init_db/create_all")

    monkeypatch.setattr(main_module, "validate_production_database_migration_state", fake_validate_migrations)
    monkeypatch.setattr(main_module, "init_db", fail_init_db)

    main_module.ensure_database_schema_ready_for_startup()

    assert calls == {"migration_guard": True}


def test_production_migration_validation_accepts_current_head(monkeypatch, tmp_path):
    import app.main as main_module

    db_path = tmp_path / "migrated.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('current_head')"))

    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:secret@db.example/nova", raising=False)
    monkeypatch.setattr(main_module, "_current_alembic_heads", lambda: {"current_head"})
    monkeypatch.setattr(main_module, "get_engine", lambda: engine)

    main_module.validate_production_database_migration_state()


def test_unreachable_database_does_not_block_startup(monkeypatch, tmp_path):
    """A restart during a database outage must still boot.

    Production incident: Neon refused connections ("data transfer quota
    exceeded") and this guard turned an already-degraded engine into a
    hard-down one -- the running process was riding the outage out fine, but
    once restarted it could not start again until the database came back.
    """
    import app.main as main_module
    from sqlalchemy.exc import OperationalError

    class UnreachableEngine:
        def connect(self):
            raise OperationalError("SELECT 1", {}, Exception("data transfer quota exceeded"))

    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:secret@db.example/nova", raising=False)
    monkeypatch.setattr(main_module, "_current_alembic_heads", lambda: {"current_head"})
    monkeypatch.setattr(main_module, "get_engine", lambda: UnreachableEngine())

    main_module.validate_production_database_migration_state()


def test_reachable_database_at_wrong_revision_still_fails_closed(monkeypatch, tmp_path):
    """The relaxation above must not weaken the actual guarantee: a database we
    CAN reach, sitting at the wrong revision, still refuses to start."""
    import app.main as main_module

    db_path = tmp_path / "stale.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('an_older_revision')"))

    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:secret@db.example/nova", raising=False)
    monkeypatch.setattr(main_module, "_current_alembic_heads", lambda: {"current_head"})
    monkeypatch.setattr(main_module, "get_engine", lambda: engine)

    with pytest.raises(RuntimeError, match="not at the current Alembic head"):
        main_module.validate_production_database_migration_state()


def test_production_migration_validation_requires_alembic_version_safely(monkeypatch, tmp_path):
    import app.main as main_module

    secret_url = "postgresql://user:database-secret@db.example/nova"
    engine = create_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")

    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", secret_url, raising=False)
    monkeypatch.setattr(main_module, "_current_alembic_heads", lambda: {"current_head"})
    monkeypatch.setattr(main_module, "get_engine", lambda: engine)

    with pytest.raises(RuntimeError) as exc:
        main_module.validate_production_database_migration_state()

    message = str(exc.value)
    assert message == (
        "Database schema is not Alembic-managed. Run `python -m alembic upgrade head` "
        "before starting production."
    )
    assert "database-secret" not in message
    assert "postgresql://" not in message


def test_nonproduction_schema_startup_keeps_dev_init_db_convenience(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(settings, "APP_ENV", "local", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///dev-test.db", raising=False)
    calls: dict[str, bool] = {}

    monkeypatch.setattr(main_module, "init_db", lambda: calls.setdefault("init_db", True))

    main_module.ensure_database_schema_ready_for_startup()

    assert calls == {"init_db": True}


def test_vps_deploy_uses_alembic_not_legacy_init_script():
    repo_root = Path(__file__).resolve().parents[3]
    deploy_script = (repo_root / "deploy" / "deploy_vps.sh").read_text(encoding="utf-8")

    assert ".venv/bin/python -m alembic upgrade head" in deploy_script
    assert "scripts.init_db" not in deploy_script


def test_prod_alias_auth_disabled_fails_startup(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        app_env="prod",
        auth_required=False,
        enable_live_orders=False,
    )

    with pytest.raises(RuntimeError, match="AUTH_REQUIRED must be true in production"):
        validate_production_configuration()


def test_production_auth_required_passes_auth_startup_check(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        auth_required=True,
        enable_live_orders=False,
    )

    validate_production_configuration()


def test_production_webhook_hmac_disabled_fails_startup_safely(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        enable_live_orders=False,
        webhook_hmac_required=False,
    )
    monkeypatch.setattr(settings, "STRATEGY_WEBHOOK_SECRET", "webhook-secret-should-not-leak", raising=False)

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    message = str(exc.value)
    assert message == "WEBHOOK_HMAC_REQUIRED must be true in production."
    assert "webhook-secret-should-not-leak" not in message


def test_production_webhook_hmac_enabled_passes_startup_check(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        enable_live_orders=False,
        webhook_hmac_required=True,
    )

    validate_production_configuration()


def test_local_dev_live_orders_disabled_does_not_require_razorpay_keys(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        app_env="local",
        auth_required=False,
        enable_live_orders=False,
        payment_provider="none",
        razorpay_key_id="",
        razorpay_key_secret="",
        razorpay_webhook_secret="",
    )

    validate_production_configuration()


def test_production_live_orders_payment_provider_none_fails(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        payment_provider="none",
        razorpay_key_id="razorpay-key-id-should-not-leak",
        razorpay_key_secret="razorpay-key-secret-should-not-leak",
        razorpay_webhook_secret="razorpay-webhook-secret-should-not-leak",
    )

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    message = str(exc.value)
    assert message == "ENABLE_LIVE_ORDERS=true requires PAYMENT_PROVIDER=razorpay."
    assert "razorpay-key-id-should-not-leak" not in message
    assert "razorpay-key-secret-should-not-leak" not in message
    assert "razorpay-webhook-secret-should-not-leak" not in message


def test_production_live_orders_missing_razorpay_key_id_fails_safely(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        razorpay_key_id="",
        razorpay_key_secret="razorpay-key-secret-should-not-leak",
        razorpay_webhook_secret="razorpay-webhook-secret-should-not-leak",
    )

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    message = str(exc.value)
    assert message == "ENABLE_LIVE_ORDERS=true requires RAZORPAY_KEY_ID."
    assert "razorpay-key-secret-should-not-leak" not in message
    assert "razorpay-webhook-secret-should-not-leak" not in message


def test_production_live_orders_missing_razorpay_key_secret_fails_safely(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        razorpay_key_id="test-razorpay-key-id",
        razorpay_key_secret="",
        razorpay_webhook_secret="razorpay-webhook-secret-should-not-leak",
    )

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    message = str(exc.value)
    assert message == "ENABLE_LIVE_ORDERS=true requires RAZORPAY_KEY_SECRET."
    assert "test-razorpay-key-id" not in message
    assert "razorpay-webhook-secret-should-not-leak" not in message


def test_production_live_orders_missing_razorpay_webhook_secret_fails_safely(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        razorpay_key_id="test-razorpay-key-id",
        razorpay_key_secret="razorpay-key-secret-should-not-leak",
        razorpay_webhook_secret="",
    )

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    message = str(exc.value)
    assert message == "ENABLE_LIVE_ORDERS=true requires RAZORPAY_WEBHOOK_SECRET."
    assert "test-razorpay-key-id" not in message
    assert "razorpay-key-secret-should-not-leak" not in message


def test_production_live_orders_missing_razorpay_premium_plan_fails_safely(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        razorpay_key_id="test-razorpay-key-id",
        razorpay_key_secret="razorpay-key-secret-should-not-leak",
        razorpay_webhook_secret="razorpay-webhook-secret-should-not-leak",
        razorpay_plan_premium_monthly="",
    )

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    message = str(exc.value)
    assert message == "ENABLE_LIVE_ORDERS=true requires RAZORPAY_PLAN_PREMIUM_MONTHLY."
    assert "test-razorpay-key-id" not in message
    assert "razorpay-key-secret-should-not-leak" not in message
    assert "razorpay-webhook-secret-should-not-leak" not in message


def test_production_payment_simulation_enabled_fails_even_without_live_orders(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        enable_live_orders=False,
        payment_simulation_enabled=True,
    )

    with pytest.raises(RuntimeError) as exc:
        validate_production_configuration()

    assert str(exc.value) == "PAYMENT_SIMULATION_ENABLED must be false in production."


def test_production_live_orders_complete_razorpay_config_passes(monkeypatch):
    _set_production_live_baseline(
        monkeypatch,
        payment_provider="razorpay",
        razorpay_key_id="test-razorpay-key-id",
        razorpay_key_secret="razorpay-key-secret",
        razorpay_webhook_secret="razorpay-webhook-secret",
        razorpay_plan_premium_monthly="plan_premium_monthly",
    )

    validate_production_configuration()


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


def test_test_market_data_provider_is_environment_locked(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TEST_MARKET_DATA_PROVIDER", True, raising=False)
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)
    with pytest.raises(RuntimeError, match="allowed only"):
        validate_production_configuration()

    monkeypatch.setattr(settings, "APP_ENV", "isolated_staging", raising=False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False, raising=False)
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK", raising=False)
    validate_production_configuration()
