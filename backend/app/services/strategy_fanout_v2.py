"""Phase 2B fanout planner for Nova-owned strategy signals."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from app.config import DEFAULT_STRATEGY_CODE
from app.core.enums import (
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
)
from app.core.feature_flags import MULTI_STRATEGY_FANOUT, is_feature_enabled
from app.db import models
from app.services.signal_normalization_service import (
    NormalizedOptionSignalDraft,
    SignalNormalizationService,
    create_normalized_option_signal,
)
from app.services.signal_validation_service import SignalValidationService


PLANNED_STATUS = "planned"
DRY_RUN_STATUS = "dry_run"

FEATURE_FLAG_DISABLED = "FEATURE_FLAG_DISABLED"
UNKNOWN_STRATEGY = "UNKNOWN_STRATEGY"
STRATEGY_VERSION_NOT_FOUND = "STRATEGY_VERSION_NOT_FOUND"
DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"
PAUSED_INSTANCE = "PAUSED_INSTANCE"
DISABLED_INSTANCE = "DISABLED_INSTANCE"
INACTIVE_INSTANCE = "INACTIVE_INSTANCE"
STRATEGY_VERSION_MISMATCH = "STRATEGY_VERSION_MISMATCH"
UNSUPPORTED_EXECUTION_MODE = "UNSUPPORTED_EXECUTION_MODE"
VALIDATION_FAILED = "VALIDATION_FAILED"
NORMALIZATION_FAILED = "NORMALIZATION_FAILED"

SUPPORTED_EXECUTION_MODES = {
    StrategyExecutionMode.SIGNAL_ONLY.value,
    StrategyExecutionMode.PAPER_LIVE_DATA.value,
    StrategyExecutionMode.REAL_ORDERS.value,
}

_SUPERTREND_CODE = "SUPERTREND_V1"
_SUPERTREND_ALIASES = {
    "supertrend",
    DEFAULT_STRATEGY_CODE,
    _SUPERTREND_CODE,
    "TRADINGVIEW_NIFTY_V1",
}


@dataclass
class FanoutPlan:
    user_id: uuid.UUID
    instance_id: uuid.UUID
    execution_mode: str
    status: str
    normalized_draft: NormalizedOptionSignalDraft
    needs_resolution: bool
    normalized_signal_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class FanoutSkip:
    reason: str
    user_id: uuid.UUID | None = None
    instance_id: uuid.UUID | None = None
    execution_mode: str | None = None
    instance_status: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class FanoutPlanError:
    error_code: str
    user_message: str
    user_id: uuid.UUID | None = None
    instance_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class FanoutPlanResult:
    ok: bool
    strategy_code: str
    strategy_version_id: uuid.UUID | None
    signal_id: str | None
    subscriber_count: int
    planned_count: int
    skipped_count: int
    error_count: int
    plans: list[FanoutPlan] = field(default_factory=list)
    skips: list[FanoutSkip] = field(default_factory=list)
    errors: list[FanoutPlanError] = field(default_factory=list)
    duplicate: bool = False
    dry_run: bool = True
    signal_row_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def plan_strategy_fanout_v2(
    db: Any,
    raw_payload: dict[str, Any],
    strategy_code: str,
    dry_run: bool = True,
) -> FanoutPlanResult:
    """Plan per-instance fanout without creating executable worker jobs."""
    requested_code = str(strategy_code or raw_payload.get("strategy_code") or "").strip()
    if not is_feature_enabled(MULTI_STRATEGY_FANOUT):
        return _result_with_error(
            strategy_code=requested_code,
            strategy_version_id=None,
            signal_id=_raw_signal_id(raw_payload),
            error=FanoutPlanError(
                error_code=FEATURE_FLAG_DISABLED,
                user_message="Multi-strategy fanout is disabled.",
            ),
            dry_run=dry_run,
        )

    payload_data = dict(raw_payload)
    payload_data.setdefault("strategy_code", requested_code)
    inbound_code = str(payload_data.get("strategy_code") or requested_code)

    catalog = _find_strategy_catalog(db, inbound_code)
    if catalog is None:
        return _result_with_error(
            strategy_code=inbound_code,
            strategy_version_id=None,
            signal_id=_raw_signal_id(raw_payload),
            error=FanoutPlanError(
                error_code=UNKNOWN_STRATEGY,
                user_message="The requested strategy is not available.",
            ),
            dry_run=dry_run,
        )

    version = _find_strategy_version(
        db,
        catalog,
        payload_version=str(payload_data.get("version") or ""),
    )
    if version is None:
        return _result_with_error(
            strategy_code=catalog.code,
            strategy_version_id=None,
            signal_id=_raw_signal_id(raw_payload),
            error=FanoutPlanError(
                error_code=STRATEGY_VERSION_NOT_FOUND,
                user_message="No active strategy version matches this signal payload.",
            ),
            dry_run=dry_run,
        )

    payload_data["strategy_code"] = catalog.code
    validator = SignalValidationService()
    validation = validator.validate(payload_data, db=db)
    if not validation.ok or validation.payload is None:
        return _result_with_error(
            strategy_code=catalog.code,
            strategy_version_id=version.id,
            signal_id=_raw_signal_id(raw_payload),
            error=FanoutPlanError(
                error_code=validation.error_code or VALIDATION_FAILED,
                user_message=validation.user_message or "The signal payload is invalid.",
            ),
            dry_run=dry_run,
        )

    payload = validation.payload
    existing_signal = _find_strategy_signal(db, catalog.code, payload.signal_id)
    if existing_signal is not None:
        return FanoutPlanResult(
            ok=True,
            strategy_code=catalog.code,
            strategy_version_id=version.id,
            signal_id=payload.signal_id,
            subscriber_count=0,
            planned_count=0,
            skipped_count=1,
            error_count=0,
            skips=[
                FanoutSkip(
                    reason=DUPLICATE_SIGNAL,
                    details={"existing_signal_id": str(existing_signal.id)},
                )
            ],
            duplicate=True,
            dry_run=dry_run,
            signal_row_id=existing_signal.id,
        )

    normalizer = SignalNormalizationService()
    instances = _load_strategy_instances(db, catalog.id)
    plans: list[FanoutPlan] = []
    skips: list[FanoutSkip] = []
    errors: list[FanoutPlanError] = []
    plan_status = DRY_RUN_STATUS if dry_run else PLANNED_STATUS

    for instance in instances:
        skip = _skip_for_instance(instance, strategy_version_id=version.id)
        if skip is not None:
            skips.append(skip)
            continue

        instance_validation = validator.validate(payload_data, db=db, instance=instance)
        if not instance_validation.ok or instance_validation.payload is None:
            errors.append(
                FanoutPlanError(
                    error_code=instance_validation.error_code or VALIDATION_FAILED,
                    user_message=instance_validation.user_message or "The signal payload is invalid.",
                    user_id=instance.user_id,
                    instance_id=instance.id,
                )
            )
            continue

        normalized = normalizer.normalize(instance_validation.payload, instance=instance)
        if not normalized.ok or normalized.draft is None:
            errors.append(
                FanoutPlanError(
                    error_code=normalized.error_code or NORMALIZATION_FAILED,
                    user_message=normalized.user_message or "The signal could not be normalized.",
                    user_id=instance.user_id,
                    instance_id=instance.id,
                )
            )
            continue

        plans.append(
            FanoutPlan(
                user_id=instance.user_id,
                instance_id=instance.id,
                execution_mode=instance.execution_mode,
                status=plan_status,
                normalized_draft=normalized.draft,
                needs_resolution=normalized.draft.needs_resolution,
            )
        )

    signal_row_id: uuid.UUID | None = None
    if not dry_run:
        signal_row = _persist_planned_signal(
            db,
            catalog=catalog,
            version=version,
            payload=payload,
            plans=plans,
            skips=skips,
            errors=errors,
        )
        signal_row_id = signal_row.id
        for plan in plans:
            normalized_row = create_normalized_option_signal(
                db,
                strategy_signal_id=signal_row.id,
                draft=plan.normalized_draft,
            )
            plan.normalized_signal_id = normalized_row.id
        db.flush()

    return FanoutPlanResult(
        ok=not errors,
        strategy_code=catalog.code,
        strategy_version_id=version.id,
        signal_id=payload.signal_id,
        subscriber_count=len(instances),
        planned_count=len(plans),
        skipped_count=len(skips),
        error_count=len(errors),
        plans=plans,
        skips=skips,
        errors=errors,
        dry_run=dry_run,
        signal_row_id=signal_row_id,
    )


def _skip_for_instance(
    instance: models.UserStrategyInstance,
    *,
    strategy_version_id: uuid.UUID,
) -> FanoutSkip | None:
    status = str(instance.status or "")
    if status == StrategyInstanceStatus.PAUSED.value:
        return _instance_skip(instance, PAUSED_INSTANCE)
    if status == StrategyInstanceStatus.DISABLED.value:
        return _instance_skip(instance, DISABLED_INSTANCE)
    if status != StrategyInstanceStatus.ACTIVE.value:
        return _instance_skip(instance, INACTIVE_INSTANCE)
    if instance.strategy_version_id is not None and instance.strategy_version_id != strategy_version_id:
        return _instance_skip(
            instance,
            STRATEGY_VERSION_MISMATCH,
            details={"expected_strategy_version_id": str(strategy_version_id)},
        )
    if str(instance.execution_mode or "") not in SUPPORTED_EXECUTION_MODES:
        return _instance_skip(
            instance,
            UNSUPPORTED_EXECUTION_MODE,
            details={"execution_mode": instance.execution_mode},
        )
    return None


def _instance_skip(
    instance: models.UserStrategyInstance,
    reason: str,
    *,
    details: dict[str, Any] | None = None,
) -> FanoutSkip:
    return FanoutSkip(
        reason=reason,
        user_id=instance.user_id,
        instance_id=instance.id,
        execution_mode=instance.execution_mode,
        instance_status=instance.status,
        details=details or {},
    )


def _persist_planned_signal(
    db: Any,
    *,
    catalog: models.StrategyCatalog,
    version: models.StrategyVersion,
    payload: Any,
    plans: list[FanoutPlan],
    skips: list[FanoutSkip],
    errors: list[FanoutPlanError],
) -> models.StrategySignal:
    signal_row = models.StrategySignal(
        strategy_name=catalog.code,
        signal_id=payload.signal_id,
        strategy_version_id=version.id,
        payload_version=payload.version,
        source_type=catalog.source_type,
        dedupe_key=_dedupe_key(catalog.code, payload.signal_id),
        status=PLANNED_STATUS,
        result_summary={
            "planner": "strategy_fanout_v2",
            "status": PLANNED_STATUS,
            "strategy_code": catalog.code,
            "strategy_version_id": str(version.id),
            "signal_id": payload.signal_id,
            "planned_count": len(plans),
            "skipped_count": len(skips),
            "error_count": len(errors),
            "plans": [plan.as_dict() for plan in plans],
            "skips": [skip.as_dict() for skip in skips],
            "errors": [error.as_dict() for error in errors],
        },
    )
    db.add(signal_row)
    db.flush()
    return signal_row


def _result_with_error(
    *,
    strategy_code: str,
    strategy_version_id: uuid.UUID | None,
    signal_id: str | None,
    error: FanoutPlanError,
    dry_run: bool,
) -> FanoutPlanResult:
    return FanoutPlanResult(
        ok=False,
        strategy_code=strategy_code,
        strategy_version_id=strategy_version_id,
        signal_id=signal_id,
        subscriber_count=0,
        planned_count=0,
        skipped_count=0,
        error_count=1,
        errors=[error],
        dry_run=dry_run,
    )


def _find_strategy_catalog(db: Any, strategy_code: str) -> models.StrategyCatalog | None:
    catalogs = db.scalars(select(models.StrategyCatalog)).all()
    for catalog in catalogs:
        if catalog.status not in {
            StrategyCatalogStatus.ACTIVE.value,
            StrategyCatalogStatus.BETA.value,
        }:
            continue
        if _catalog_accepts_strategy_code(catalog, strategy_code):
            return catalog
    return None


def _find_strategy_version(
    db: Any,
    catalog: models.StrategyCatalog,
    *,
    payload_version: str,
) -> models.StrategyVersion | None:
    versions = db.scalars(
        select(models.StrategyVersion)
        .where(models.StrategyVersion.strategy_id == catalog.id)
        .order_by(models.StrategyVersion.created_at.asc())
    ).all()
    active_versions = [
        version
        for version in versions
        if version.status in {
            StrategyCatalogStatus.ACTIVE.value,
            StrategyCatalogStatus.BETA.value,
        }
    ]
    for version in active_versions:
        if version.payload_version and _codes_match(version.payload_version, payload_version):
            return version
    return active_versions[0] if active_versions else None


def _load_strategy_instances(db: Any, strategy_id: uuid.UUID) -> list[models.UserStrategyInstance]:
    return list(
        db.scalars(
            select(models.UserStrategyInstance)
            .where(models.UserStrategyInstance.strategy_id == strategy_id)
            .order_by(models.UserStrategyInstance.created_at.asc())
        ).all()
    )


def _find_strategy_signal(
    db: Any,
    strategy_code: str,
    signal_id: str,
) -> models.StrategySignal | None:
    return db.scalar(
        select(models.StrategySignal).where(
            models.StrategySignal.strategy_name == strategy_code,
            models.StrategySignal.signal_id == signal_id,
        )
    )


def _catalog_accepts_strategy_code(
    catalog: models.StrategyCatalog,
    strategy_code: str,
) -> bool:
    if _codes_match(catalog.code, strategy_code):
        return True
    if _codes_match(catalog.code, _SUPERTREND_CODE) and any(
        _codes_match(alias, strategy_code) for alias in _SUPERTREND_ALIASES
    ):
        return True

    metadata = catalog.metadata_json or {}
    if not isinstance(metadata, dict):
        return False
    aliases: list[str] = []
    for key in (
        "aliases",
        "legacy_strategy_names",
        "legacy_strategy_name",
        "legacy_strategy_code",
        "strategy_code",
    ):
        value = metadata.get(key)
        if isinstance(value, list):
            aliases.extend(str(item) for item in value)
        elif value:
            aliases.append(str(value))
    return any(_codes_match(alias, strategy_code) for alias in aliases)


def _codes_match(left: str | None, right: str | None) -> bool:
    return _normalize_code(left) == _normalize_code(right)


def _normalize_code(value: str | None) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _raw_signal_id(raw_payload: dict[str, Any]) -> str | None:
    signal_id = raw_payload.get("signal_id")
    return str(signal_id) if signal_id else None


def _dedupe_key(strategy_code: str, signal_id: str) -> str:
    return f"{_normalize_code(strategy_code)}:{signal_id}"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
