"""Per-user presentation preferences.

Only settings NOVA can actually honour are exposed. Notification channels use
the browser Notification API; permission and delivery remain browser-controlled
and never affect trading execution.

Nothing here touches the engine's runtime settings, so a preference save can
never affect a trading decision or bump the configuration revision.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.db import models
from app.db.engine import database_configured, session_scope

TABLE_DENSITIES = ("comfortable", "compact")
CHART_TIMEFRAMES = ("1m", "5m", "15m")

# Channels that may create local browser notifications while Trading is open.
NOTIFICATION_CHANNELS: tuple[dict[str, Any], ...] = (
    {
        "key": "entry_exit",
        "label": "Entry and exit fills",
        "available": True,
        "reason": "Delivered by this browser when notification permission is granted.",
    },
    {
        "key": "risk_breach",
        "label": "Risk limit reached",
        "available": True,
        "reason": "Delivered by this browser when notification permission is granted.",
    },
    {
        "key": "engine_state",
        "label": "Engine started or stopped",
        "available": True,
        "reason": "Delivered by this browser when notification permission is granted.",
    },
)

DEFAULTS: dict[str, Any] = {
    "timezone": "Asia/Kolkata",
    "reduced_motion": False,
    "table_density": "comfortable",
    "default_chart_timeframe": "5m",
    "notification_preferences": {},
}


class PreferenceError(ValueError):
    """An unsupported preference value."""


def _validate(values: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    if "timezone" in values:
        tz = str(values["timezone"]).strip()
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(tz)
        except Exception as exc:
            raise PreferenceError(f"Unknown timezone: {tz}.") from exc
        clean["timezone"] = tz
    if "reduced_motion" in values:
        clean["reduced_motion"] = bool(values["reduced_motion"])
    if "table_density" in values:
        density = str(values["table_density"]).lower()
        if density not in TABLE_DENSITIES:
            raise PreferenceError(f"Table density must be one of {', '.join(TABLE_DENSITIES)}.")
        clean["table_density"] = density
    if "default_chart_timeframe" in values:
        timeframe = str(values["default_chart_timeframe"]).lower()
        if timeframe not in CHART_TIMEFRAMES:
            raise PreferenceError(f"Chart timeframe must be one of {', '.join(CHART_TIMEFRAMES)}.")
        clean["default_chart_timeframe"] = timeframe
    if "notification_preferences" in values:
        raw = values["notification_preferences"] or {}
        if not isinstance(raw, dict):
            raise PreferenceError("Notification preferences must be an object.")
        known = {channel["key"] for channel in NOTIFICATION_CHANNELS}
        unknown = set(raw) - known
        if unknown:
            raise PreferenceError(f"Unknown notification channels: {', '.join(sorted(unknown))}.")
        clean["notification_preferences"] = {key: bool(value) for key, value in raw.items()}
    return clean


def _public(row: models.UserPreference | None) -> dict[str, Any]:
    if row is None:
        return {**DEFAULTS, "revision": 0, "stored": False}
    return {
        "timezone": row.timezone,
        "reduced_motion": bool(row.reduced_motion),
        "table_density": row.table_density,
        # Old rows may contain a previously supported presentation interval.
        # Fall back without rewriting history or requiring a migration.
        "default_chart_timeframe": (
            row.default_chart_timeframe
            if row.default_chart_timeframe in CHART_TIMEFRAMES
            else "5m"
        ),
        "notification_preferences": dict(row.notification_preferences or {}),
        "revision": int(row.revision or 0),
        "stored": True,
    }


def get_preferences(user_id: uuid.UUID) -> dict[str, Any]:
    if not database_configured():
        return {**DEFAULTS, "revision": 0, "stored": False, "channels": list(NOTIFICATION_CHANNELS)}
    with session_scope() as db:
        row = db.scalar(select(models.UserPreference).where(models.UserPreference.user_id == user_id))
        return {**_public(row), "channels": list(NOTIFICATION_CHANNELS)}


def save_preferences(user_id: uuid.UUID, values: dict[str, Any]) -> dict[str, Any]:
    """Validate, then upsert the owner's single preference row."""
    clean = _validate(values)
    if not database_configured():
        raise PreferenceError("Preferences cannot be saved without a database.")
    with session_scope() as db:
        row = db.scalar(
            select(models.UserPreference)
            .where(models.UserPreference.user_id == user_id)
            .with_for_update()
        )
        if row is None:
            row = models.UserPreference(user_id=user_id, **{**DEFAULTS, **clean})
            row.revision = 1
            db.add(row)
        else:
            for key, value in clean.items():
                setattr(row, key, value)
            row.revision = int(row.revision or 0) + 1
        db.flush()
        return {**_public(row), "channels": list(NOTIFICATION_CHANNELS)}
