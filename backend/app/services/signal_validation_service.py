from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from app.db import models
from app.schemas.nova_signal_v1 import NovaSignalV1


INVALID_PAYLOAD = "INVALID_PAYLOAD"
UNKNOWN_STRATEGY = "UNKNOWN_STRATEGY"
STRATEGY_MISMATCH = "STRATEGY_MISMATCH"
MANUAL_STRIKE_REQUIRED = "MANUAL_STRIKE_REQUIRED"
MANUAL_EXPIRY_REQUIRED = "MANUAL_EXPIRY_REQUIRED"
UNSUPPORTED_SYMBOL = "UNSUPPORTED_SYMBOL"
UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"


@dataclass(frozen=True)
class SignalValidationResult:
    ok: bool
    payload: NovaSignalV1 | None = None
    error_code: str | None = None
    user_message: str | None = None


class SignalValidationService:
    def validate(
        self,
        raw_payload: dict[str, Any],
        *,
        db: Any | None = None,
        instance: models.UserStrategyInstance | None = None,
    ) -> SignalValidationResult:
        try:
            payload = NovaSignalV1.model_validate(raw_payload)
        except ValidationError as exc:
            return SignalValidationResult(
                ok=False,
                error_code=_validation_error_code(exc),
                user_message=_safe_validation_message(exc),
            )

        if db is not None:
            catalog = _find_strategy_catalog(db, payload.strategy_code)
            if catalog is None:
                return SignalValidationResult(
                    ok=False,
                    error_code=UNKNOWN_STRATEGY,
                    user_message="The requested strategy is not available.",
                )
        else:
            catalog = None

        mismatch = _instance_mismatch(payload, instance=instance, db=db, catalog=catalog)
        if mismatch:
            return SignalValidationResult(
                ok=False,
                error_code=STRATEGY_MISMATCH,
                user_message="The signal does not match the selected strategy instance.",
            )

        return SignalValidationResult(ok=True, payload=payload)


def _validation_error_code(exc: ValidationError) -> str:
    errors = exc.errors()
    text = str(errors).lower()
    if "strike is required" in text:
        return MANUAL_STRIKE_REQUIRED
    if "expiry is required" in text:
        return MANUAL_EXPIRY_REQUIRED
    for error in errors:
        loc = error.get("loc") or ()
        field = loc[-1] if loc else None
        if field == "action":
            return UNSUPPORTED_ACTION
        if field == "intent":
            return UNSUPPORTED_INTENT
        if field == "symbol":
            return UNSUPPORTED_SYMBOL
    return INVALID_PAYLOAD


def _safe_validation_message(exc: ValidationError) -> str:
    code = _validation_error_code(exc)
    messages = {
        MANUAL_STRIKE_REQUIRED: "Manual strike mode requires a strike.",
        MANUAL_EXPIRY_REQUIRED: "Manual expiry mode requires an expiry.",
        UNSUPPORTED_ACTION: "This signal action is not supported.",
        UNSUPPORTED_INTENT: "This signal intent is not supported.",
        UNSUPPORTED_SYMBOL: "This signal symbol is not supported.",
    }
    return messages.get(code, "The signal payload is invalid.")


def _find_strategy_catalog(db: Any, strategy_code: str) -> models.StrategyCatalog | None:
    catalogs = db.scalars(select(models.StrategyCatalog)).all()
    for catalog in catalogs:
        if _catalog_accepts_strategy_code(catalog, strategy_code):
            return catalog
    return None


def _instance_mismatch(
    payload: NovaSignalV1,
    *,
    instance: models.UserStrategyInstance | None,
    db: Any | None,
    catalog: models.StrategyCatalog | None,
) -> bool:
    if instance is None:
        return False

    if payload.strategy_instance_id and str(payload.strategy_instance_id) != str(instance.id):
        return True

    if db is not None:
        instance_catalog = db.get(models.StrategyCatalog, instance.strategy_id)
        if instance_catalog is None:
            return True
        if catalog is not None and instance_catalog.id != catalog.id:
            return True
        if not _catalog_accepts_strategy_code(instance_catalog, payload.strategy_code):
            return True
        if instance.strategy_version_id is not None:
            version = db.get(models.StrategyVersion, instance.strategy_version_id)
            if version is None or version.strategy_id != instance.strategy_id:
                return True
        return False

    config = getattr(instance, "config_json", None) or {}
    if not isinstance(config, dict):
        return False
    configured_codes = [
        config.get("legacy_strategy_code"),
        config.get("strategy_code"),
        config.get("legacy_strategy_name"),
    ]
    configured_codes = [str(code) for code in configured_codes if code]
    return bool(configured_codes) and not any(
        _codes_match(code, payload.strategy_code) for code in configured_codes
    )


def _catalog_accepts_strategy_code(catalog: models.StrategyCatalog, strategy_code: str) -> bool:
    if _codes_match(catalog.code, strategy_code):
        return True
    metadata = catalog.metadata_json or {}
    if not isinstance(metadata, dict):
        return False
    aliases = []
    for key in ("aliases", "legacy_strategy_names"):
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
