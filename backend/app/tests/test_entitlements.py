from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from inspect import signature
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.engine import session_scope
from app.services import entitlements
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


NOW = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)


def _add_entitlement(
    user_id,
    *,
    status: str = "active",
    source: str = "payment_provider",
    starts_at: datetime | None = NOW - timedelta(days=1),
    expires_at: datetime | None = NOW + timedelta(days=30),
    live_orders_enabled: bool = True,
    static_ip_enabled: bool = True,
    strategy_access_enabled: bool = True,
    paper_trading_enabled: bool = False,
    max_strategy_count: int | None = None,
):
    with session_scope() as db:
        row = models.UserEntitlement(
            user_id=user_id,
            plan_code="nova_live",
            status=status,
            source=source,
            starts_at=starts_at,
            expires_at=expires_at,
            live_orders_enabled=live_orders_enabled,
            static_ip_enabled=static_ip_enabled,
            strategy_access_enabled=strategy_access_enabled,
            paper_trading_enabled=paper_trading_enabled,
            max_strategy_count=max_strategy_count,
            metadata_json={"grant": "unit_test"},
            created_at=NOW - timedelta(minutes=5),
            updated_at=NOW - timedelta(minutes=5),
        )
        db.add(row)
        db.flush()
        return row.id


def test_no_entitlement_means_no_live_entitlement(mu_db):
    user = make_user("no-entitlement@example.com")

    with session_scope() as db:
        assert entitlements.get_user_entitlement(db, user.id) is None
        assert entitlements.has_live_entitlement(db, user.id, now=NOW) is False
        result = entitlements.evaluate_entitlement(None, now=NOW)

    assert result.valid is False
    assert result.reason == "missing_entitlement"


def test_active_future_entitlement_passes_live_entitlement(mu_db):
    user = make_user("active-entitlement@example.com")
    _add_entitlement(user.id, live_orders_enabled=True)

    with session_scope() as db:
        result = entitlements.require_live_entitlement(db, user.id, now=NOW)

    assert result.valid is True
    assert result.live_orders_enabled is True
    assert result.status == "active"
    assert result.as_safe_dict()["reason"] == "valid"


def test_trialing_future_entitlement_passes_when_feature_enabled(mu_db):
    user = make_user("trialing-entitlement@example.com")
    _add_entitlement(user.id, status="trialing", source="trial", live_orders_enabled=True)

    with session_scope() as db:
        assert entitlements.has_live_entitlement(db, user.id, now=NOW) is True


def test_expired_entitlement_fails(mu_db):
    user = make_user("expired-entitlement@example.com")
    _add_entitlement(user.id, status="active", expires_at=NOW - timedelta(seconds=1))

    with session_scope() as db:
        result = entitlements.evaluate_entitlement(entitlements.get_user_entitlement(db, user.id), now=NOW)
        assert entitlements.has_live_entitlement(db, user.id, now=NOW) is False

    assert result.valid is False
    assert result.reason == "expired"


@pytest.mark.parametrize("status", ["cancelled", "revoked", "past_due", "expired"])
def test_deny_statuses_fail_live_entitlement(mu_db, status):
    user = make_user(f"{status}-entitlement@example.com")
    _add_entitlement(user.id, status=status)

    with session_scope() as db:
        result = entitlements.evaluate_entitlement(entitlements.get_user_entitlement(db, user.id), now=NOW)
        assert entitlements.has_live_entitlement(db, user.id, now=NOW) is False

    assert result.valid is False
    assert result.reason == f"status_{status}"


def test_static_ip_entitlement_is_separate_from_live_orders(mu_db):
    user = make_user("static-only-entitlement@example.com")
    _add_entitlement(
        user.id,
        live_orders_enabled=False,
        static_ip_enabled=True,
        strategy_access_enabled=False,
    )

    with session_scope() as db:
        assert entitlements.has_static_ip_entitlement(db, user.id, now=NOW) is True
        assert entitlements.has_live_entitlement(db, user.id, now=NOW) is False


def test_strategy_entitlement_is_separate_from_live_orders(mu_db):
    user = make_user("strategy-only-entitlement@example.com")
    _add_entitlement(
        user.id,
        live_orders_enabled=False,
        static_ip_enabled=False,
        strategy_access_enabled=True,
        max_strategy_count=3,
    )

    with session_scope() as db:
        assert entitlements.has_strategy_entitlement(db, user.id, now=NOW) is True
        assert entitlements.has_live_entitlement(db, user.id, now=NOW) is False
        result = entitlements.require_strategy_entitlement(db, user.id, now=NOW)

    assert result.max_strategy_count == 3


def test_no_entitlement_means_no_paper_entitlement(mu_db):
    user = make_user("no-paper-entitlement@example.com")
    with session_scope() as db:
        assert entitlements.has_paper_entitlement(db, user.id) is False
        with pytest.raises(entitlements.EntitlementError):
            entitlements.require_paper_entitlement(db, user.id)


def test_paper_entitlement_is_read_directly_off_the_row_not_gated_by_evaluate_entitlement(mu_db):
    """paper_trading_enabled is a one-time, non-expiring grant, unlike the
    monthly live/static-IP/strategy bundle -- it must stay valid even when
    the row's shared status/expiry (driven by an unrelated subscription
    lifecycle) would fail evaluate_entitlement()'s normal gate."""
    user = make_user("paper-only-entitlement@example.com")
    _add_entitlement(
        user.id,
        status="cancelled",
        expires_at=NOW - timedelta(days=1),
        live_orders_enabled=False,
        static_ip_enabled=False,
        strategy_access_enabled=False,
        paper_trading_enabled=True,
    )

    with session_scope() as db:
        assert entitlements.has_paper_entitlement(db, user.id) is True
        entitlements.require_paper_entitlement(db, user.id)  # does not raise
        # The shared row is genuinely expired/cancelled for the other flags.
        result = entitlements.evaluate_entitlement(entitlements.get_user_entitlement(db, user.id), now=NOW)
        assert result.valid is False


def test_entitlement_lookup_is_scoped_by_user_id(mu_db):
    alice = make_user("alice-entitlement@example.com")
    bob = make_user("bob-entitlement@example.com")
    _add_entitlement(alice.id, live_orders_enabled=True)

    with session_scope() as db:
        assert entitlements.has_live_entitlement(db, alice.id, now=NOW) is True
        assert entitlements.has_live_entitlement(db, bob.id, now=NOW) is False


def test_active_manual_admin_without_expiry_is_explicitly_allowed(mu_db):
    user = make_user("manual-admin-entitlement@example.com")
    _add_entitlement(
        user.id,
        status="active",
        source="manual_admin",
        expires_at=None,
        live_orders_enabled=True,
    )

    with session_scope() as db:
        assert entitlements.has_live_entitlement(db, user.id, now=NOW) is True


def test_provider_entitlement_without_expiry_fails_closed(mu_db):
    user = make_user("provider-missing-expiry@example.com")
    _add_entitlement(
        user.id,
        status="active",
        source="payment_provider",
        expires_at=None,
        live_orders_enabled=True,
    )

    with session_scope() as db:
        result = entitlements.evaluate_entitlement(entitlements.get_user_entitlement(db, user.id), now=NOW)

    assert result.valid is False
    assert result.reason == "missing_expiry"


def test_same_payment_provider_event_cannot_be_inserted_twice(mu_db):
    user = make_user("payment-event-unique@example.com")

    def event_row():
        return models.PaymentEvent(
            user_id=user.id,
            provider="test_provider",
            provider_event_id="evt_duplicate",
            event_type="invoice.paid",
            event_status="received",
            amount=19900,
            currency="INR",
            payload_hash="a" * 64,
            processed=False,
            received_at=NOW,
            metadata_json={"invoice_id": "inv_unit"},
            created_at=NOW,
        )

    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(event_row())
            db.flush()
            db.add(event_row())
            db.flush()


def test_payment_event_model_stores_hash_not_raw_secret_payload(mu_db):
    columns = {column.name for column in models.PaymentEvent.__table__.columns}
    assert "payload_hash" in columns
    assert "raw_payload" not in columns
    assert "client_secret" not in columns
    assert "access_token" not in columns

    user = make_user("payment-event-safe@example.com")
    with session_scope() as db:
        row = models.PaymentEvent(
            user_id=user.id,
            provider="test_provider",
            provider_event_id="evt_safe",
            event_type="payment.captured",
            event_status="received",
            amount=9900,
            currency="INR",
            payload_hash="b" * 64,
            processed=False,
            received_at=NOW,
            metadata_json={"invoice_id": "inv_safe"},
            created_at=NOW,
        )
        db.add(row)
        db.flush()
        safe_snapshot = {
            "provider": row.provider,
            "provider_event_id": row.provider_event_id,
            "payload_hash": row.payload_hash,
            "metadata_json": row.metadata_json,
        }

    serialized = str(safe_snapshot)
    assert "secret" not in serialized.lower()
    assert "raw_payload" not in serialized.lower()


def test_service_errors_and_outputs_do_not_expose_provider_secrets(mu_db):
    user = make_user("entitlement-error-safe@example.com")

    with session_scope() as db:
        with pytest.raises(entitlements.EntitlementError) as exc:
            entitlements.require_live_entitlement(db, user.id, now=NOW)
        result = entitlements.evaluate_entitlement(None, now=NOW).as_safe_dict()

    serialized = f"{exc.value} {result}"
    forbidden = [
        "provider_secret",
        "client_secret",
        "access_token",
        "raw_payload",
        "dhan",
        "proxy_url",
    ]
    for value in forbidden:
        assert value not in serialized.lower()


def test_service_does_not_accept_frontend_payment_or_identity_flags():
    forbidden = {
        "payment_status",
        "subscription_status",
        "is_live",
        "mode",
        "account_id",
        "session_id",
    }
    for fn in (
        entitlements.get_user_entitlement,
        entitlements.has_live_entitlement,
        entitlements.has_static_ip_entitlement,
        entitlements.has_strategy_entitlement,
        entitlements.require_live_entitlement,
        entitlements.require_static_ip_entitlement,
        entitlements.require_strategy_entitlement,
    ):
        assert forbidden.isdisjoint(signature(fn).parameters)


def test_entitlement_migration_imports_cleanly():
    path = Path("alembic/versions/20260629_0004_entitlements_payment_events.py")
    spec = importlib.util.spec_from_file_location("migration_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
