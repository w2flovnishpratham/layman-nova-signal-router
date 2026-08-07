"""One versioned owner/mode risk configuration shared by every UI surface."""
from __future__ import annotations

import uuid
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.db import models
from app.db.engine import database_configured, session_scope
from app.services.state_store import update_mode_runtime_settings

MODES = {"paper", "live"}
PRESET_KEYS = {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}
CHANGE_SOURCES = {"SETUP", "RISK_PAGE", "TRADING_TERMINAL", "ADMIN", "SYSTEM_MIGRATION"}
EXIT_MODES = {"FLIPS_ONLY", "TARGET_PROFIT", "CUSTOM_SL_TP"}
BASES = {"POINTS", "PERCENT"}
IST = ZoneInfo("Asia/Kolkata")

_PRESETS: dict[str, dict[str, Any]] = {
    "CONSERVATIVE": {
        "key": "CONSERVATIVE",
        "name": "Conservative",
        "description": "Lower exposure with a longer post-loss cooldown.",
        "values": {
            "daily_loss_cap": 10_000,
            "max_loss_per_trade": 1_000,
            "max_trades_per_day": 3,
            "max_open_positions": 1,
            "lots_per_trade_min": 1,
            "lots_per_trade_max": 1,
            "cooldown_minutes": 60,
            "exit_mode": "CUSTOM_SL_TP",
            "stop_loss_value": 50,
            "take_profit_value": 100,
            "stop_loss_basis": "POINTS",
            "take_profit_basis": "POINTS",
            "margin_exposure_cap": None,
        },
    },
    "BALANCED": {
        "key": "BALANCED",
        "name": "Balanced",
        "description": "The suggested default for measured intraday risk.",
        "values": {
            "daily_loss_cap": 25_000,
            "max_loss_per_trade": 2_500,
            "max_trades_per_day": 6,
            "max_open_positions": 3,
            "lots_per_trade_min": 1,
            "lots_per_trade_max": 2,
            "cooldown_minutes": 30,
            "exit_mode": "CUSTOM_SL_TP",
            "stop_loss_value": 75,
            "take_profit_value": 150,
            "stop_loss_basis": "POINTS",
            "take_profit_basis": "POINTS",
            "margin_exposure_cap": None,
        },
    },
    "AGGRESSIVE": {
        "key": "AGGRESSIVE",
        "name": "Aggressive",
        "description": "Higher capacity with tighter recovery pauses.",
        "values": {
            "daily_loss_cap": 50_000,
            "max_loss_per_trade": 5_000,
            "max_trades_per_day": 12,
            "max_open_positions": 5,
            "lots_per_trade_min": 1,
            "lots_per_trade_max": 5,
            "cooldown_minutes": 15,
            "exit_mode": "CUSTOM_SL_TP",
            "stop_loss_value": 100,
            "take_profit_value": 200,
            "stop_loss_basis": "POINTS",
            "take_profit_basis": "POINTS",
            "margin_exposure_cap": None,
        },
    },
}


class RiskConfigurationError(ValueError):
    def __init__(self, message: str, *, status_code: int = 422, code: str = "INVALID_RISK_CONFIGURATION"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in MODES:
        raise RiskConfigurationError("Mode must be PAPER or LIVE.")
    return mode


def _preset(value: str) -> str:
    key = str(value or "").strip().upper()
    if key not in PRESET_KEYS:
        raise RiskConfigurationError("Preset must be CONSERVATIVE, BALANCED, or AGGRESSIVE.")
    return key


def presets(mode: str) -> list[dict[str, Any]]:
    normalized = _mode(mode)
    return [{**deepcopy(_PRESETS[key]), "mode": normalized.upper()} for key in _PRESETS]


def preset_values(key: str) -> dict[str, Any]:
    return deepcopy(_PRESETS[_preset(key)]["values"])


def _integer(values: dict[str, Any], key: str, minimum: int, maximum: int, *, allow_unlimited: bool = False) -> int:
    try:
        value = int(values.get(key))
    except (TypeError, ValueError):
        raise RiskConfigurationError(f"{key} must be a whole number.") from None
    # 0 means "no limit" -- risk_overview._utilisation() and risk_manager's
    # entry-limit checks already treat a 0/absent cap this way (that's how
    # "No cap" shows up for margin exposure today); this input gate just
    # never allowed 0 through for the other capped fields.
    if allow_unlimited and value == 0:
        return 0
    if not minimum <= value <= maximum:
        message = f"{key} must be 0 (no limit) or between {minimum} and {maximum}." if allow_unlimited else f"{key} must be between {minimum} and {maximum}."
        raise RiskConfigurationError(message)
    return value


def _number(values: dict[str, Any], key: str, *, required: bool = True, allow_unlimited: bool = False) -> float | None:
    raw = values.get(key)
    if raw in (None, "") and not required:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise RiskConfigurationError(f"{key} must be a number.") from None
    if allow_unlimited and value == 0:
        return 0.0
    if value <= 0:
        message = f"{key} must be 0 (no limit) or greater than zero." if allow_unlimited else f"{key} must be greater than zero."
        raise RiskConfigurationError(message)
    return round(value, 2)


def validate_values(values: dict[str, Any]) -> dict[str, Any]:
    daily = _number(values, "daily_loss_cap", allow_unlimited=True)
    per_trade = _number(values, "max_loss_per_trade")
    if daily > 0 and per_trade > daily:
        raise RiskConfigurationError("Maximum loss per trade cannot exceed the daily loss cap.")
    lots_min = _integer(values, "lots_per_trade_min", 1, 20)
    lots_max = _integer(values, "lots_per_trade_max", 1, 20)
    if lots_min > lots_max:
        raise RiskConfigurationError("Minimum lots cannot exceed maximum lots.")
    exit_mode = str(values.get("exit_mode") or "").strip().upper()
    if exit_mode not in EXIT_MODES:
        raise RiskConfigurationError("Exit mode is invalid.")
    sl_basis = str(values.get("stop_loss_basis") or "POINTS").strip().upper()
    tp_basis = str(values.get("take_profit_basis") or "POINTS").strip().upper()
    if sl_basis not in BASES or tp_basis not in BASES:
        raise RiskConfigurationError("Stop-loss and take-profit basis must be POINTS or PERCENT.")
    stop = _number(values, "stop_loss_value", required=exit_mode == "CUSTOM_SL_TP")
    target = _number(values, "take_profit_value", required=exit_mode != "FLIPS_ONLY")
    margin = _number(values, "margin_exposure_cap", required=False)
    if sl_basis == "PERCENT" and stop is not None and stop >= 100:
        raise RiskConfigurationError("Percentage stop loss must be below 100.")
    return {
        "daily_loss_cap": daily,
        "max_loss_per_trade": per_trade,
        "max_trades_per_day": _integer(values, "max_trades_per_day", 1, 50, allow_unlimited=True),
        "max_open_positions": _integer(values, "max_open_positions", 1, 20, allow_unlimited=True),
        "lots_per_trade_min": lots_min,
        "lots_per_trade_max": lots_max,
        "cooldown_minutes": _integer(values, "cooldown_minutes", 0, 1440),
        "exit_mode": exit_mode,
        "stop_loss_value": stop,
        "take_profit_value": target,
        "stop_loss_basis": sl_basis,
        "take_profit_basis": tp_basis,
        "margin_exposure_cap": margin,
    }


def _paise(value: float | None) -> int | None:
    return None if value is None else int(round(value * 100))


def _rupees(value: int | None) -> float | None:
    return None if value is None else round(int(value) / 100, 2)


def _version_values(row: models.UserRiskConfigurationVersion) -> dict[str, Any]:
    return {
        "daily_loss_cap": _rupees(row.daily_loss_cap_paise),
        "max_loss_per_trade": _rupees(row.max_loss_per_trade_paise),
        "max_trades_per_day": row.max_trades_per_day,
        "max_open_positions": row.max_open_positions,
        "lots_per_trade_min": row.lots_per_trade_min,
        "lots_per_trade_max": row.lots_per_trade_max,
        "cooldown_minutes": row.cooldown_minutes,
        "exit_mode": row.exit_mode,
        "stop_loss_value": row.stop_loss_value,
        "take_profit_value": row.take_profit_value,
        "stop_loss_basis": row.stop_loss_basis,
        "take_profit_basis": row.take_profit_basis,
        "margin_exposure_cap": _rupees(row.margin_exposure_cap_paise),
    }


def _suggested(mode: str) -> dict[str, Any]:
    return {
        "mode": mode.upper(),
        "activeVersion": 0,
        "activeVersionId": None,
        "profileType": "BALANCED",
        "basedOnPreset": "BALANCED",
        "values": preset_values("BALANCED"),
        "updatedAt": None,
        "changeSource": None,
        "suggestedDefault": True,
    }


def configuration(user_id: uuid.UUID, mode: str) -> dict[str, Any]:
    normalized = _mode(mode)
    if not database_configured():
        return _suggested(normalized)
    with session_scope() as db:
        config = db.scalar(
            select(models.UserRiskConfiguration).where(
                models.UserRiskConfiguration.user_id == user_id,
                models.UserRiskConfiguration.execution_mode == normalized,
            )
        )
        if config is None or config.active_version_id is None:
            return _suggested(normalized)
        version = db.get(models.UserRiskConfigurationVersion, config.active_version_id)
        if version is None:
            return _suggested(normalized)
        return {
            "mode": normalized.upper(),
            "activeVersion": version.version_number,
            "activeVersionId": str(version.id),
            "profileType": "CUSTOM" if version.is_custom else version.preset_key,
            "basedOnPreset": version.preset_key,
            "values": _version_values(version),
            "updatedAt": version.created_at.isoformat(),
            "changeSource": version.change_source,
            "suggestedDefault": False,
        }


def save_configuration(
    user_id: uuid.UUID,
    *,
    mode: str,
    based_on_preset: str,
    values: dict[str, Any],
    change_source: str,
    expected_version: int,
    _db=None,
) -> dict[str, Any]:
    normalized_mode = _mode(mode)
    preset_key = _preset(based_on_preset)
    source = str(change_source or "").strip().upper()
    if source not in CHANGE_SOURCES:
        raise RiskConfigurationError("Change source is invalid.")
    clean = validate_values(values)
    if not database_configured():
        raise RiskConfigurationError(
            "Risk settings require the configured database.",
            status_code=503,
            code="RISK_DATABASE_UNAVAILABLE",
        )
    with (session_scope() if _db is None else nullcontext(_db)) as db:
        config = db.scalar(
            select(models.UserRiskConfiguration)
            .where(
                models.UserRiskConfiguration.user_id == user_id,
                models.UserRiskConfiguration.execution_mode == normalized_mode,
            )
            .with_for_update()
        )
        if config is None:
            if expected_version != 0:
                raise RiskConfigurationError(
                    "Risk settings changed elsewhere. Reload before saving.",
                    status_code=409,
                    code="RISK_VERSION_CONFLICT",
                )
            config = models.UserRiskConfiguration(
                user_id=user_id,
                execution_mode=normalized_mode,
                based_on_preset=preset_key,
            )
            db.add(config)
            db.flush()
            current = 0
        else:
            active = (
                db.get(models.UserRiskConfigurationVersion, config.active_version_id)
                if config.active_version_id
                else None
            )
            current = int(active.version_number if active else 0)
            if current != expected_version:
                raise RiskConfigurationError(
                    "Risk settings changed elsewhere. Reload before saving.",
                    status_code=409,
                    code="RISK_VERSION_CONFLICT",
                )
        highest = db.scalar(
            select(func.max(models.UserRiskConfigurationVersion.version_number)).where(
                models.UserRiskConfigurationVersion.risk_configuration_id == config.id
            )
        )
        version_number = max(current, int(highest or 0)) + 1
        version = models.UserRiskConfigurationVersion(
            risk_configuration_id=config.id,
            version_number=version_number,
            preset_key=preset_key,
            is_custom=clean != validate_values(preset_values(preset_key)),
            daily_loss_cap_paise=_paise(clean["daily_loss_cap"]),
            max_loss_per_trade_paise=_paise(clean["max_loss_per_trade"]),
            max_trades_per_day=clean["max_trades_per_day"],
            max_open_positions=clean["max_open_positions"],
            lots_per_trade_min=clean["lots_per_trade_min"],
            lots_per_trade_max=clean["lots_per_trade_max"],
            cooldown_minutes=clean["cooldown_minutes"],
            exit_mode=clean["exit_mode"],
            stop_loss_value=clean["stop_loss_value"],
            take_profit_value=clean["take_profit_value"],
            stop_loss_basis=clean["stop_loss_basis"],
            take_profit_basis=clean["take_profit_basis"],
            margin_exposure_cap_paise=_paise(clean["margin_exposure_cap"]),
            change_source=source,
            created_by=user_id,
        )
        db.add(version)
        db.flush()
        config.active_version_id = version.id
        config.based_on_preset = preset_key
        config.updated_at = models.utcnow()
        db.add(
            models.AuditLog(
                user_id=user_id,
                action="RISK_CONFIGURATION_SAVED",
                audit_metadata={
                    "mode": normalized_mode,
                    "version": version_number,
                    "risk_configuration_version_id": str(version.id),
                    "based_on_preset": preset_key,
                    "is_custom": version.is_custom,
                    "change_source": source,
                },
            )
        )
        trade_day = datetime.now(IST).date()
        begin = datetime.combine(trade_day, time.min, tzinfo=IST).astimezone(timezone.utc)
        finish = begin + timedelta(days=1)
        realized = list(
            db.scalars(
                select(models.PortfolioTrade.realized_pnl).where(
                    models.PortfolioTrade.user_id == user_id,
                    models.PortfolioTrade.mode == normalized_mode,
                    models.PortfolioTrade.closed_at >= begin,
                    models.PortfolioTrade.closed_at < finish,
                )
            )
        )
        current_loss = max(0.0, -sum(float(value or 0) for value in realized))
        # daily_loss_cap == 0 means no limit (see _number's allow_unlimited
        # above) -- without this guard, any loss at all satisfied
        # `current_loss >= 0` and logged a false "entries blocked" breaker
        # event for an owner who explicitly asked for no cap.
        if clean["daily_loss_cap"] > 0 and current_loss >= clean["daily_loss_cap"]:
            db.add(
                models.AuditLog(
                    user_id=user_id,
                    action="RISK_BREAKER_TRIGGERED",
                    audit_metadata={
                        "mode": normalized_mode,
                        "message": (
                            f"Daily loss ₹{current_loss:,.2f} meets the newly saved "
                            f"₹{clean['daily_loss_cap']:,.2f} cap; new entries blocked."
                        ),
                        "trigger_type": "DAILY_LOSS_CAP",
                        "outcome": "Entries blocked",
                        "risk_configuration_version_id": str(version.id),
                    },
                )
            )
    if _db is None:
        # RiskPage.tsx and the Trading-page risk widget both save straight
        # to this table with no dual-write -- only setup_configuration.py's
        # own save flow (which passes _db=db here, so this branch is
        # skipped for it) already keeps runtime_settings in sync itself.
        # Without this, risk_manager's actual entry-blocking check -- which
        # reads runtime_settings, NOT this table -- kept enforcing whatever
        # was last saved through Setup and silently ignored every later
        # edit made here. Confirmed against production: an owner set
        # max_trades_per_day to 0 (no limit) here, it saved and displayed
        # correctly everywhere, but trades still got blocked at the stale
        # value of 50 because runtime_settings was never told about it.
        update_mode_runtime_settings(
            normalized_mode,
            max_trades_per_day=clean["max_trades_per_day"],
            max_daily_loss=clean["daily_loss_cap"],
        )
    if _db is not None:
        return {
            "mode": normalized_mode.upper(),
            "activeVersion": version.version_number,
            "activeVersionId": str(version.id),
            "profileType": "CUSTOM" if version.is_custom else version.preset_key,
            "basedOnPreset": version.preset_key,
            "values": _version_values(version),
            "updatedAt": version.created_at.isoformat(),
            "changeSource": version.change_source,
            "suggestedDefault": False,
        }
    return configuration(user_id, normalized_mode)


def active_runtime_overlay(user_id: uuid.UUID, mode: str) -> dict[str, Any]:
    active = configuration(user_id, mode)
    values = active["values"]
    return {
        "risk_configuration_version_id": active["activeVersionId"],
        "risk_configuration_version": active["activeVersion"],
        "risk_profile_type": active["profileType"],
        "risk_based_on_preset": active["basedOnPreset"],
        "risk_max_loss_per_trade": values["max_loss_per_trade"],
        "risk_max_open_positions": values["max_open_positions"],
        "risk_lots_min": values["lots_per_trade_min"],
        "risk_lots_max": values["lots_per_trade_max"],
        "risk_cooldown_minutes": values["cooldown_minutes"],
        "risk_exit_mode": values["exit_mode"],
        "risk_stop_loss_value": values["stop_loss_value"],
        "risk_take_profit_value": values["take_profit_value"],
        "risk_stop_loss_basis": values["stop_loss_basis"],
        "risk_take_profit_basis": values["take_profit_basis"],
        "risk_margin_exposure_cap": values["margin_exposure_cap"],
        "max_daily_loss": values["daily_loss_cap"],
        "max_trades_per_day": values["max_trades_per_day"],
        "configured_lots": values["lots_per_trade_min"],
        "option_exit_mode": "SERVER",
        "option_disable_sl": values["exit_mode"] != "CUSTOM_SL_TP",
        "server_side_exit_enabled": values["exit_mode"] != "FLIPS_ONLY",
    }


def overlay_for_current_entry(runtime: dict[str, Any]) -> dict[str, Any]:
    from app.services.execution_context import current_execution_user
    from app.services.state_store import get_engine_mode

    user = current_execution_user()
    mode = get_engine_mode(legacy_fallback=False)
    if user is None or mode not in MODES:
        return dict(runtime)
    return {**runtime, **active_runtime_overlay(user.id, mode)}
