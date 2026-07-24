"""One coherent, revision-bound save for strategy setup + risk settings.

Why this exists
---------------
The two halves of a configuration live in different stores:

* strategy setup   -> ``StrategyInstance.risk_config`` in PostgreSQL
* risk settings    -> the owner's JSON runtime settings file

Saving them through two requests could leave a partially applied configuration.
That is not fail-safe in either direction: a risk-only save can *loosen* limits
(``max_daily_loss`` 10,000 -> 25,000, cooldown 30 -> 5) while the strategy half
never lands, and a strategy-only save can leave sizing that the old limits were
never sized for.

Ordering used here
------------------
1. Validate **both** halves completely. Nothing is written if either fails.
2. Inside one DB transaction: apply the strategy setup, then write the runtime
   settings file (atomic temp-file replace). A DB error before the file write
   rolls back with nothing written; a file error rolls the DB back too.
3. Commit. If the commit itself fails, the previous settings snapshot is
   restored as a compensating write.

True cross-store transactionality is impossible while one half is JSON-backed.
The residual window is process death between the settings write and the DB
commit. That state is *detectable*, not silent: both halves carry the same
``configuration_revision``, and ``committed_revision`` refuses to report a
revision when they disagree, so the engine cannot start on a torn configuration.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select

from app.db import models
from app.db.engine import session_scope
from app.services import strategy_catalog_service
from app.services import strategy_instance_service as instances
from app.services.audit_logger import log_audit_event
from app.services.state_store import (
    SettingsVersionMismatch,
    get_runtime_settings,
    set_runtime_settings,
)

# The single key strategy_catalog_service already stores setups under.
_SETUP_ROOT = strategy_catalog_service._SETUP_ROOT


class ConfigurationConflict(Exception):
    """A stale expected revision, or a torn configuration."""

    def __init__(self, message: str, *, expected: int | None, current: int, code: str = "REVISION_CONFLICT"):
        super().__init__(message)
        self.message = message
        self.expected = expected
        self.current = current
        self.code = code
        self.status_code = 409


def _settings_revision(settings: dict[str, Any] | None = None) -> int:
    settings = settings if settings is not None else get_runtime_settings()
    try:
        return int(settings.get("configuration_revision") or 0)
    except (TypeError, ValueError):
        return 0


def _saved_setup(user_id: uuid.UUID, mode: str) -> dict[str, Any]:
    selection = instances.get_engine_selection(user_id)
    selected = selection.get("selected") or {}
    setups = selected.get("saved_setup")
    if not isinstance(setups, dict):
        return {}
    saved = setups.get(mode)
    return saved if isinstance(saved, dict) else {}


def _setup_revision(saved: dict[str, Any]) -> int:
    try:
        return int(saved.get("configuration_revision") or 0)
    except (TypeError, ValueError):
        return 0


def current_revision(user_id: uuid.UUID, mode: str = "paper") -> dict[str, Any]:
    """The revision the client must echo back, plus whether the halves agree."""
    settings = get_runtime_settings()
    saved = _saved_setup(user_id, mode)
    settings_rev = _settings_revision(settings)
    setup_rev = _setup_revision(saved)
    return {
        "revision": settings_rev,
        "settings_revision": settings_rev,
        "setup_revision": setup_rev,
        "coherent": settings_rev == setup_rev,
        "complete": bool(saved.get("complete")),
    }


def committed_revision(user_id: uuid.UUID, mode: str = "paper") -> int:
    """The revision both halves agree on. Raises when they don't."""
    state = current_revision(user_id, mode)
    if not state["coherent"]:
        raise ConfigurationConflict(
            "Strategy setup and risk settings are at different revisions. "
            "Save the configuration again before starting the engine.",
            expected=state["setup_revision"],
            current=state["settings_revision"],
            code="CONFIGURATION_TORN",
        )
    return int(state["revision"])


def save_configuration(
    user_id: uuid.UUID,
    *,
    strategy_key: str,
    mode: str,
    setup_values: dict[str, Any],
    risk_values: dict[str, Any],
    expected_revision: int | None,
    normalize_risk,
) -> dict[str, Any]:
    """Validate and commit both halves as one revision.

    ``normalize_risk`` is the router's risk validator, injected so this service
    never re-implements the risk rules the settings API already enforces.
    """
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"paper", "live"}:
        raise instances.InstanceError("Mode must be paper or live.", status_code=422, code="INVALID_SETUP")

    selection = instances.get_engine_selection(user_id)
    selected = selection.get("selected")
    if selected is None or strategy_catalog_service.strategy_key_for_entry(selected) != strategy_key:
        raise instances.InstanceError(
            "The selected strategy changed. Select it again before saving.",
            status_code=409,
            code="SELECTION_CHANGED",
        )
    if normalized_mode == "live" and not bool(selected.get("live_eligible")):
        raise instances.InstanceError(
            "Live setup is unavailable for this strategy.",
            status_code=409,
            code="LIVE_UNAVAILABLE",
        )

    # --- validate both halves before anything is written ---------------------
    schema = strategy_catalog_service.setup_schema_for(strategy_key)
    normalized_setup = strategy_catalog_service.validate_setup_values(schema, setup_values)
    normalized_risk = normalize_risk(risk_values)

    settings_before = get_runtime_settings()
    current = _settings_revision(settings_before)
    if expected_revision is not None and int(expected_revision) != current:
        raise ConfigurationConflict(
            "This configuration was changed elsewhere. Reload before saving again.",
            expected=int(expected_revision),
            current=current,
        )
    next_revision = current + 1

    instance_id = uuid.UUID(str(selection["selected_instance_id"]))
    settings_written = False
    saved_settings: dict[str, Any] = settings_before
    saved_setup: dict[str, Any] = {}

    try:
        with session_scope() as db:
            row = db.scalar(
                select(models.StrategyInstance).where(
                    models.StrategyInstance.id == instance_id,
                    models.StrategyInstance.user_id == user_id,
                ).with_for_update()
            )
            if row is None:
                raise instances.InstanceError("Selected strategy is unavailable.", status_code=409)

            risk_config = deepcopy(row.risk_config) if isinstance(row.risk_config, dict) else {}
            setups = risk_config.get(_SETUP_ROOT)
            setups = deepcopy(setups) if isinstance(setups, dict) else {}
            saved_setup = {
                **normalized_setup,
                "complete": True,
                "strategy_key": strategy_key,
                "mode": normalized_mode,
                "configuration_revision": next_revision,
                "saved_at": strategy_catalog_service._now().isoformat(),
            }
            setups[normalized_mode] = saved_setup
            risk_config[_SETUP_ROOT] = setups
            row.risk_config = risk_config
            row.current_lots = int(normalized_setup["lots"])
            row.updated_at = strategy_catalog_service._now()
            if row.legacy_subscription_id:
                subscription = db.get(models.StrategySubscription, row.legacy_subscription_id)
                if subscription:
                    subscription.lots = row.current_lots
                    subscription.updated_at = strategy_catalog_service._now()
            db.flush()

            # Written inside the transaction: a DB failure raised before this
            # point leaves the settings file untouched.
            merged = dict(settings_before)
            merged.update(normalized_risk)
            merged["configuration_revision"] = next_revision
            saved_settings = set_runtime_settings(merged)
            settings_written = True
    except Exception:
        if settings_written:
            # The DB commit failed after the settings landed. Put the previous
            # settings back so the two halves cannot disagree.
            try:
                set_runtime_settings(dict(settings_before))
            except Exception:  # noqa: BLE001 - restore is best effort; the
                # revision gate still refuses to start on a torn configuration.
                log_audit_event(
                    "CONFIGURATION_ROLLBACK_FAILED",
                    "Risk settings could not be restored after a failed configuration commit.",
                    severity="ERROR",
                )
        raise

    log_audit_event(
        "CONFIGURATION_SAVED",
        f"Strategy setup and risk settings committed at revision {next_revision}.",
        metadata={
            "strategy_key": strategy_key,
            "mode": normalized_mode,
            "configuration_revision": next_revision,
            "setup": normalized_setup,
            "risk": normalized_risk,
        },
    )

    return {
        "ok": True,
        "configuration_revision": next_revision,
        "strategy_key": strategy_key,
        "strategy_instance_id": str(instance_id),
        "mode": normalized_mode,
        "setup": saved_setup,
        "risk": {key: saved_settings.get(key) for key in normalized_risk},
        "settings": saved_settings,
    }


__all__ = [
    "ConfigurationConflict",
    "SettingsVersionMismatch",
    "committed_revision",
    "current_revision",
    "save_configuration",
]
