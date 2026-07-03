from __future__ import annotations

from sqlalchemy import select

from app.core.enums import (
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.db import models
from app.db.engine import session_scope
from app.services.signal_validation_service import (
    MANUAL_EXPIRY_REQUIRED,
    MANUAL_STRIKE_REQUIRED,
    STRATEGY_MISMATCH,
    UNKNOWN_STRATEGY,
    UNSUPPORTED_ACTION,
    SignalValidationService,
)
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def payload(**overrides) -> dict:
    data = {
        "version": "nova.v1",
        "secret": "super-secret-value",
        "signal_id": "phase2a-signal-001",
        "strategy_code": "SUPERTREND_V1",
        "action": "ENTRY",
        "intent": "BULLISH",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": "AUTO",
        "strike_mode": "ATM",
        "expiry_mode": "NEXT_WEEKLY",
        "qty_mode": "LOTS",
        "lots": 1,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "tradingview",
        "timestamp": "2026-07-03T09:15:00+05:30",
    }
    data.update(overrides)
    return data


def _seed_catalog(db, code: str = "SUPERTREND_V1"):
    catalog = models.StrategyCatalog(
        code=code,
        name=code,
        status=StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        metadata_json={"aliases": ["TRADINGVIEW_NIFTY_V1"] if code == "SUPERTREND_V1" else []},
    )
    db.add(catalog)
    db.flush()
    version = models.StrategyVersion(
        strategy_id=catalog.id,
        version="v1",
        status=StrategyCatalogStatus.ACTIVE.value,
        payload_version="nova.v1",
    )
    db.add(version)
    db.flush()
    return catalog, version


def test_valid_payload_with_known_strategy_passes_db_validation(mu_db):
    with session_scope() as db:
        _seed_catalog(db)
        result = SignalValidationService().validate(payload(), db=db)

    assert result.ok is True
    assert result.payload is not None
    assert result.payload.strategy_code == "SUPERTREND_V1"


def test_manual_strike_requires_strike():
    result = SignalValidationService().validate(payload(strike_mode="MANUAL"))

    assert result.ok is False
    assert result.error_code == MANUAL_STRIKE_REQUIRED
    assert "super-secret-value" not in (result.user_message or "")


def test_manual_expiry_requires_expiry():
    result = SignalValidationService().validate(payload(expiry_mode="MANUAL"))

    assert result.ok is False
    assert result.error_code == MANUAL_EXPIRY_REQUIRED


def test_unknown_action_rejects_with_stable_error_code():
    result = SignalValidationService().validate(payload(action="BUY"))

    assert result.ok is False
    assert result.error_code == UNSUPPORTED_ACTION


def test_unknown_strategy_code_rejects_when_db_validation_enabled(mu_db):
    with session_scope() as db:
        _seed_catalog(db, "SUPERTREND_V1")
        result = SignalValidationService().validate(payload(strategy_code="UNKNOWN"), db=db)

    assert result.ok is False
    assert result.error_code == UNKNOWN_STRATEGY


def test_strategy_instance_mismatch_rejects(mu_db):
    user = make_user("phase2a-mismatch@example.com")
    with session_scope() as db:
        _seed_catalog(db, "SUPERTREND_V1")
        orb_catalog, orb_version = _seed_catalog(db, "ORB")
        instance = models.UserStrategyInstance(
            user_id=user.id,
            strategy_id=orb_catalog.id,
            strategy_version_id=orb_version.id,
            status=StrategyInstanceStatus.ACTIVE.value,
            execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
            source_type=StrategySourceType.BACKEND_HOSTED.value,
            lots=1,
        )
        db.add(instance)
        db.flush()

        result = SignalValidationService().validate(payload(strategy_code="SUPERTREND_V1"), db=db, instance=instance)

    assert result.ok is False
    assert result.error_code == STRATEGY_MISMATCH


def test_strategy_catalog_alias_is_accepted(mu_db):
    with session_scope() as db:
        _seed_catalog(db, "SUPERTREND_V1")
        result = SignalValidationService().validate(payload(strategy_code="TRADINGVIEW_NIFTY_V1"), db=db)
        catalogs = db.scalars(select(models.StrategyCatalog)).all()

    assert result.ok is True
    assert len(catalogs) == 1
