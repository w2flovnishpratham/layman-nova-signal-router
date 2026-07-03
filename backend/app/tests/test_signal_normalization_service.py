from __future__ import annotations

import inspect
from datetime import date

from app.core.enums import (
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
    StrategySourceType,
)
from app.db import models
from app.db.engine import session_scope
from app.schemas.nova_signal_v1 import NovaSignalV1
from app.services.signal_normalization_service import (
    create_normalized_option_signal,
    SignalNormalizationService,
)
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401


def payload(**overrides) -> NovaSignalV1:
    data = {
        "version": "nova.v1",
        "secret": "super-secret-value",
        "signal_id": "phase2a-normalize-001",
        "strategy_code": "SUPERTREND_V1",
        "action": "ENTRY",
        "intent": "BULLISH",
        "symbol": "NIFTY",
        "instrument_type": "OPTIDX",
        "option_side": "AUTO",
        "strike_mode": "MANUAL",
        "strike": 24500,
        "expiry_mode": "MANUAL",
        "expiry": "2026-07-09",
        "qty_mode": "LOTS",
        "lots": 2,
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "source": "tradingview",
        "timestamp": "2026-07-03T09:15:00+05:30",
    }
    data.update(overrides)
    return NovaSignalV1.model_validate(data)


def _seed_instance(db, user_id, *, side_preference: str | None = None):
    catalog = models.StrategyCatalog(
        code="SUPERTREND_V1",
        name="Supertrend",
        status=StrategyCatalogStatus.ACTIVE.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
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
    instance = models.UserStrategyInstance(
        user_id=user_id,
        strategy_id=catalog.id,
        strategy_version_id=version.id,
        status=StrategyInstanceStatus.ACTIVE.value,
        execution_mode=StrategyExecutionMode.SIGNAL_ONLY.value,
        source_type=StrategySourceType.NOVA_OWNED_TRADINGVIEW.value,
        side_preference=side_preference,
        lots=2,
    )
    db.add(instance)
    db.flush()
    return catalog, version, instance


def test_valid_payload_creates_normalized_draft(mu_db):
    user = make_user("phase2a-draft@example.com")
    with session_scope() as db:
        _, version, instance = _seed_instance(db, user.id)
        result = SignalNormalizationService().normalize(payload(), instance=instance)

    assert result.ok is True
    assert result.draft is not None
    assert result.draft.action == "ENTRY"
    assert result.draft.intent == "BULLISH"
    assert result.draft.option_side == "CE"
    assert result.draft.resolved_strike == 24500
    assert result.draft.resolved_expiry == date(2026, 7, 9)
    assert result.draft.lots == 2
    assert result.draft.strategy_version_id == version.id
    assert result.draft.instance_id == instance.id
    assert result.draft.needs_resolution is False


def test_non_manual_strike_and_expiry_return_unresolved_draft():
    result = SignalNormalizationService().normalize(
        payload(strike_mode="ATM", strike=None, expiry_mode="NEXT_WEEKLY", expiry=None)
    )

    assert result.ok is True
    assert result.draft is not None
    assert result.draft.resolved_strike is None
    assert result.draft.resolved_expiry is None
    assert result.draft.needs_resolution is True
    assert any("strike_mode ATM" in item for item in result.draft.resolution_reasons)
    assert any("expiry_mode NEXT_WEEKLY" in item for item in result.draft.resolution_reasons)


def test_db_helper_creates_normalized_option_signal_row(mu_db):
    user = make_user("phase2a-db-row@example.com")
    with session_scope() as db:
        _, _, instance = _seed_instance(db, user.id)
        strategy_signal = models.StrategySignal(
            strategy_name="supertrend",
            signal_id="phase2a-db-row",
            status="accepted",
            instance_id=instance.id,
            strategy_version_id=instance.strategy_version_id,
        )
        db.add(strategy_signal)
        db.flush()

        normalized = SignalNormalizationService().normalize(payload(), instance=instance)
        assert normalized.draft is not None
        row = create_normalized_option_signal(
            db,
            strategy_signal_id=strategy_signal.id,
            draft=normalized.draft,
        )
        row_id = row.id

    with session_scope() as db:
        saved = db.get(models.NormalizedOptionSignal, row_id)
        assert saved is not None
        assert saved.strategy_signal_id == strategy_signal.id
        assert saved.instance_id == instance.id
        assert saved.option_side == "CE"
        assert saved.resolved_strike == 24500
        assert saved.raw_mapping_details["signal_id"] == "phase2a-normalize-001"


def test_normalization_services_do_not_import_execution_or_dhan_paths():
    import app.services.option_intent_mapper as mapper
    import app.services.signal_normalization_service as normalizer
    import app.services.signal_validation_service as validator

    combined_source = "\n".join(
        inspect.getsource(module)
        for module in (mapper, normalizer, validator)
    )

    assert "route_signal" not in combined_source
    assert "execution_router" not in combined_source
    assert "dhan_client" not in combined_source
    assert "RealDhanClient" not in combined_source
    assert "get_broker_client" not in combined_source
