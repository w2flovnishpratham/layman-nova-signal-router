"""Approved risk controls and protected safety policies.

The audit that preceded this module found every editable value already has a
home in the engine's runtime settings, so **no automation table was created**
and no generic rule-builder model exists. The four editable rules are:

* ``cooldown_after_loss_minutes``
* ``max_trades_per_day``
* ``max_daily_loss``
* ``entry_cutoff_ist``

The protected policies are not settings at all - they are code paths (durable
exit operations, CAS position state, stale-quote handling, the daily-loss
breaker and the end-of-day square-off). They are listed so the page can show
them, and they carry no toggle because there is nothing to write.

Every editable rule declares unit, bounds, basis, when it takes effect, whether
a restart is needed, and whether it touches the open position - so the UI can
never invent those answers.
"""
from __future__ import annotations

from typing import Any

from app.services.state_store import get_runtime_settings

APPLIES_IMMEDIATELY = "Applies immediately"
APPLIES_NEXT_ENTRY = "Applies to next entry"
REQUIRES_RESTART = "Requires engine restart"
PROTECTED = "Protected system rule"


def _editable_rules(settings: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "key": "cooldown_after_loss_minutes",
            "label": "Cooldown after a losing trade",
            "value": int(settings.get("cooldown_after_loss_minutes") or 0),
            "unit": "minutes",
            "minimum": 0,
            "maximum": 390,
            "zero_means": "no cooldown",
            "basis": "Measured from the exit that booked a negative realised P&L.",
            "effect": APPLIES_NEXT_ENTRY,
            "requires_restart": False,
            "affects_open_position": False,
        },
        {
            "key": "max_trades_per_day",
            "label": "Maximum trades per day",
            "value": int(settings.get("max_trades_per_day") or 0),
            "unit": "entries",
            "minimum": 0,
            "maximum": 50,
            "zero_means": "no cap",
            "basis": "Counts entries recorded for the current IST trading day.",
            "effect": APPLIES_NEXT_ENTRY,
            "requires_restart": False,
            "affects_open_position": False,
        },
        {
            "key": "max_daily_loss",
            "label": "Daily loss cap",
            "value": float(settings.get("max_daily_loss") or 0),
            "unit": "rupees",
            "minimum": 0,
            "maximum": 10_000_000,
            "zero_means": "no cap",
            "basis": "Compared against the session P&L; hitting it hard-stops and squares off.",
            "effect": APPLIES_IMMEDIATELY,
            "requires_restart": False,
            # The breaker acts on the live session, so a tighter cap can close
            # the current position. Say so rather than implying it is inert.
            "affects_open_position": True,
        },
        {
            "key": "entry_cutoff_ist",
            "label": "Entry cutoff time",
            "value": str(settings.get("entry_cutoff_ist") or ""),
            "unit": "IST time (HH:MM)",
            "minimum": None,
            "maximum": None,
            "zero_means": "empty means no cutoff",
            "basis": "Wall-clock IST. Blocks new entries only; exits are never blocked.",
            "effect": APPLIES_NEXT_ENTRY,
            "requires_restart": False,
            "affects_open_position": False,
        },
    ]


PROTECTED_RULES: tuple[dict[str, Any], ...] = (
    {
        "key": "stale_feed_protection",
        "label": "Stale-feed protection",
        "description": "A quote older than the staleness window is reported STALE or UNAVAILABLE "
                       "instead of being used as a live price.",
        "effect": PROTECTED,
        "why_protected": "Acting on a stale price is how a position gets exited at a price that never existed.",
    },
    {
        "key": "duplicate_exit_protection",
        "label": "Duplicate-exit protection",
        "description": "Exits are claimed as durable operations, so a retry returns ALREADY_FLAT "
                       "rather than booking a second sell.",
        "effect": PROTECTED,
        "why_protected": "A duplicate exit books phantom profit and leaves the position state wrong.",
    },
    {
        "key": "confirmed_flat_handling",
        "label": "Confirmed-flat handling",
        "description": "A confirmed-flat position is tombstoned; a late event cannot resurrect it.",
        "effect": PROTECTED,
        "why_protected": "Position resurrection caused the incident this safeguard exists for.",
    },
    {
        "key": "daily_loss_breaker",
        "label": "Daily-loss circuit breaker",
        "description": "When the daily loss cap is reached the engine hard-stops and squares off.",
        "effect": PROTECTED,
        "why_protected": "The cap value is yours to set; the breaker mechanism is not disableable.",
    },
    {
        "key": "eod_square_off",
        "label": "End-of-day safety square-off",
        "description": "Open positions are squared off at end of day rather than carried overnight.",
        "effect": PROTECTED,
        "why_protected": "An intraday option position carried overnight is an unbounded overnight risk.",
    },
)

EDITABLE_KEYS = frozenset({
    "cooldown_after_loss_minutes",
    "max_trades_per_day",
    "max_daily_loss",
    "entry_cutoff_ist",
})


class AutomationError(ValueError):
    """A value outside the declared bounds, or an attempt to edit a protected rule."""


def build_automations_overview() -> dict[str, Any]:
    settings = get_runtime_settings()
    return {
        "ok": True,
        "editable": _editable_rules(settings),
        "protected": list(PROTECTED_RULES),
        # No automation table exists; these are the engine's own settings.
        "storage": "runtime_settings",
    }


def validate_automation_changes(values: dict[str, Any]) -> dict[str, Any]:
    """Bounds-check the four editable rules. Anything else is refused."""
    unknown = set(values) - EDITABLE_KEYS
    if unknown:
        raise AutomationError(
            f"These are protected system rules and cannot be changed here: {', '.join(sorted(unknown))}."
        )
    clean: dict[str, Any] = {}
    bounds = {rule["key"]: rule for rule in _editable_rules(get_runtime_settings())}
    for key, value in values.items():
        rule = bounds[key]
        if key == "entry_cutoff_ist":
            text = str(value or "").strip()
            if text:
                try:
                    hour, minute = (int(part) for part in text.split(":", 1))
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        raise ValueError
                except (TypeError, ValueError) as exc:
                    raise AutomationError("Entry cutoff must be an IST time as HH:MM.") from exc
                text = f"{hour:02d}:{minute:02d}"
            clean[key] = text
            continue
        number = float(value)
        if number < rule["minimum"] or number > rule["maximum"]:
            raise AutomationError(
                f"{rule['label']} must be between {rule['minimum']} and {rule['maximum']} {rule['unit']}."
            )
        clean[key] = int(number) if key != "max_daily_loss" else number
    return clean
