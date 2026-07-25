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

from sqlalchemy import func, select

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
    settings_rev = _settings_revision(settings)
    selected = selected_configuration(user_id, mode)
    if selected is not None:
        setup_rev = int(selected["revision"])
        complete = True
        configuration_id = selected["id"]
    else:
        saved = _saved_setup(user_id, mode)
        setup_rev = _setup_revision(saved)
        complete = bool(saved.get("complete"))
        configuration_id = None
    return {
        "revision": settings_rev,
        "settings_revision": settings_rev,
        "setup_revision": setup_rev,
        "coherent": settings_rev == setup_rev,
        "complete": complete,
        "configuration_id": configuration_id,
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

            previous_revision = db.scalar(
                select(models.StrategyConfigurationRevision)
                .where(
                    models.StrategyConfigurationRevision.user_id == user_id,
                    models.StrategyConfigurationRevision.strategy_instance_id == instance_id,
                    models.StrategyConfigurationRevision.strategy_version_id == row.strategy_version_id,
                    models.StrategyConfigurationRevision.mode == normalized_mode,
                    models.StrategyConfigurationRevision.status == "active",
                )
                .order_by(models.StrategyConfigurationRevision.revision.desc())
                .with_for_update()
            )
            highest_revision = db.scalar(
                select(func.max(models.StrategyConfigurationRevision.revision)).where(
                    models.StrategyConfigurationRevision.user_id == user_id,
                    models.StrategyConfigurationRevision.strategy_instance_id == instance_id,
                    models.StrategyConfigurationRevision.strategy_version_id == row.strategy_version_id,
                    models.StrategyConfigurationRevision.mode == normalized_mode,
                )
            )
            # Runtime revisions predate the canonical table. Keep numbering
            # monotonic across that transition.
            next_revision = max(current, int(highest_revision or 0)) + 1

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

            if previous_revision is not None:
                previous_revision.status = "superseded"
                previous_revision.updated_at = strategy_catalog_service._now()
            canonical_revision = models.StrategyConfigurationRevision(
                user_id=user_id,
                strategy_instance_id=instance_id,
                strategy_version_id=row.strategy_version_id,
                mode=normalized_mode,
                revision=next_revision,
                configuration_json=normalized_setup,
                risk_json=normalized_risk,
                status="active",
                supersedes_revision_id=previous_revision.id if previous_revision is not None else None,
                committed_at=strategy_catalog_service._now(),
            )
            db.add(canonical_revision)
            db.flush()
            saved_setup["configuration_revision_id"] = str(canonical_revision.id)
            risk_config = deepcopy(row.risk_config) if isinstance(row.risk_config, dict) else {}
            setups = deepcopy(risk_config.get(_SETUP_ROOT)) if isinstance(risk_config.get(_SETUP_ROOT), dict) else {}
            setups[normalized_mode] = saved_setup
            risk_config[_SETUP_ROOT] = setups
            row.risk_config = risk_config

            engine_config = db.scalar(
                select(models.UserEngineConfig)
                .where(models.UserEngineConfig.user_id == user_id)
                .with_for_update()
            )
            if engine_config is None:
                engine_config = models.UserEngineConfig(
                    user_id=user_id,
                    selected_strategy_instance_id=instance_id,
                )
                db.add(engine_config)
            engine_config.selected_configuration_revision_id = canonical_revision.id
            engine_config.selected_configuration_revision = next_revision
            engine_config.updated_at = strategy_catalog_service._now()
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
        "configuration_revision_id": str(canonical_revision.id),
        "strategy_key": strategy_key,
        "strategy_instance_id": str(instance_id),
        "mode": normalized_mode,
        "setup": saved_setup,
        "risk": {key: saved_settings.get(key) for key in normalized_risk},
        "settings": saved_settings,
    }


def _serialize_revision(row: models.StrategyConfigurationRevision) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "user_id": str(row.user_id),
        "strategy_instance_id": str(row.strategy_instance_id),
        "strategy_version_id": str(row.strategy_version_id),
        "mode": row.mode,
        "revision": row.revision,
        "configuration": deepcopy(row.configuration_json),
        "risk": deepcopy(row.risk_json),
        "status": row.status,
        "supersedes_revision_id": str(row.supersedes_revision_id) if row.supersedes_revision_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "committed_at": row.committed_at.isoformat() if row.committed_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def selected_configuration(user_id: uuid.UUID, mode: str | None = None) -> dict[str, Any] | None:
    with session_scope() as db:
        engine_config = db.scalar(
            select(models.UserEngineConfig).where(models.UserEngineConfig.user_id == user_id)
        )
        if engine_config is None or engine_config.selected_configuration_revision_id is None:
            return None
        row = db.get(models.StrategyConfigurationRevision, engine_config.selected_configuration_revision_id)
        if row is None or row.user_id != user_id or (mode is not None and row.mode != mode):
            return None
        return _serialize_revision(row)


def list_configurations(
    user_id: uuid.UUID,
    *,
    mode: str | None = None,
    strategy_instance_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    with session_scope() as db:
        stmt = select(models.StrategyConfigurationRevision).where(
            models.StrategyConfigurationRevision.user_id == user_id
        )
        if mode is not None:
            stmt = stmt.where(models.StrategyConfigurationRevision.mode == mode)
        if strategy_instance_id is not None:
            stmt = stmt.where(
                models.StrategyConfigurationRevision.strategy_instance_id == strategy_instance_id
            )
        rows = db.scalars(
            stmt.order_by(
                models.StrategyConfigurationRevision.committed_at.desc(),
                models.StrategyConfigurationRevision.revision.desc(),
            )
        ).all()
        return [_serialize_revision(row) for row in rows]


def get_configuration(user_id: uuid.UUID, configuration_id: uuid.UUID) -> dict[str, Any] | None:
    with session_scope() as db:
        row = db.get(models.StrategyConfigurationRevision, configuration_id)
        if row is None or row.user_id != user_id:
            return None
        return _serialize_revision(row)


__all__ = [
    "ConfigurationConflict",
    "SettingsVersionMismatch",
    "committed_revision",
    "current_revision",
    "get_configuration",
    "list_configurations",
    "save_configuration",
    "selected_configuration",
]
