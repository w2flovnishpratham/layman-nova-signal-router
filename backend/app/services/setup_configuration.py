"""One coherent, revision-bound save for strategy setup + risk settings.

Why this exists
---------------
The two halves of a configuration live in different stores:

* strategy setup   -> ``StrategyInstance.risk_config`` in PostgreSQL
* risk settings    -> the owner's JSON runtime settings file

Saving them through two requests could leave a partially applied configuration.
That is not fail-safe in either direction: a risk-only save can *loosen* limits
(``max_daily_loss`` 10,000 -> 25,000) while the strategy half
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
from app.services import risk_configuration, strategy_catalog_service
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


def configuration_mode_for_execution(execution_mode: str) -> str | None:
    normalized = str(execution_mode or "").strip().lower()
    if normalized == "real_orders":
        return "live"
    if normalized == "paper_live_data":
        return "paper"
    return None


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
    shared_before = risk_configuration.configuration(user_id, normalized_mode)
    shared_values = dict(shared_before["values"])
    if "max_daily_loss" in normalized_risk:
        shared_values["daily_loss_cap"] = normalized_risk["max_daily_loss"]
    if "max_trades_per_day" in normalized_risk:
        shared_values["max_trades_per_day"] = normalized_risk["max_trades_per_day"]

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
    saved_risk_configuration: dict[str, Any] = shared_before

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

            saved_risk_configuration = risk_configuration.save_configuration(
                user_id,
                mode=normalized_mode,
                based_on_preset=shared_before["basedOnPreset"],
                values=shared_values,
                change_source="SETUP",
                expected_version=shared_before["activeVersion"],
                _db=db,
            )

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
        "risk_configuration": saved_risk_configuration,
        "settings": saved_settings,
    }


def revise_automation_configuration(
    user_id: uuid.UUID,
    *,
    configuration_id: uuid.UUID,
    expected_revision: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Create one immutable successor for one reviewed automation batch.

    Runtime and position files are intentionally untouched. A running position
    and already queued jobs therefore keep the revision they were created with.
    """
    if not changes:
        raise ConfigurationConflict(
            "No automation changes were supplied.",
            expected=expected_revision,
            current=expected_revision,
            code="NO_CHANGES",
        )
    with session_scope() as db:
        engine_config = db.scalar(
            select(models.UserEngineConfig)
            .where(models.UserEngineConfig.user_id == user_id)
            .with_for_update()
        )
        if (
            engine_config is None
            or engine_config.selected_configuration_revision_id != configuration_id
        ):
            current = (
                int(engine_config.selected_configuration_revision or 0)
                if engine_config is not None
                else 0
            )
            raise ConfigurationConflict(
                "The selected configuration changed. Refresh before confirming.",
                expected=expected_revision,
                current=current,
                code="SELECTION_CHANGED",
            )
        current_row = db.scalar(
            select(models.StrategyConfigurationRevision)
            .where(
                models.StrategyConfigurationRevision.id == configuration_id,
                models.StrategyConfigurationRevision.user_id == user_id,
            )
            .with_for_update()
        )
        if current_row is None:
            raise ConfigurationConflict(
                "The selected configuration is unavailable.",
                expected=expected_revision,
                current=0,
                code="CONFIGURATION_UNAVAILABLE",
            )
        if (
            current_row.status != "active"
            or current_row.revision != expected_revision
            or engine_config.selected_configuration_revision != expected_revision
        ):
            raise ConfigurationConflict(
                "This configuration was changed elsewhere. Refresh before confirming.",
                expected=expected_revision,
                current=int(engine_config.selected_configuration_revision or 0),
            )
        highest_revision = db.scalar(
            select(func.max(models.StrategyConfigurationRevision.revision)).where(
                models.StrategyConfigurationRevision.user_id == user_id,
                models.StrategyConfigurationRevision.strategy_instance_id
                == current_row.strategy_instance_id,
                models.StrategyConfigurationRevision.strategy_version_id
                == current_row.strategy_version_id,
                models.StrategyConfigurationRevision.mode == current_row.mode,
            )
        )
        next_revision = max(
            int(highest_revision or 0),
            int(current_row.revision),
        ) + 1
        next_risk = deepcopy(current_row.risk_json or {})
        next_risk.update(deepcopy(changes))
        successor = models.StrategyConfigurationRevision(
            user_id=user_id,
            strategy_instance_id=current_row.strategy_instance_id,
            strategy_version_id=current_row.strategy_version_id,
            mode=current_row.mode,
            revision=next_revision,
            configuration_json=deepcopy(current_row.configuration_json or {}),
            risk_json=next_risk,
            status="active",
            supersedes_revision_id=current_row.id,
            committed_at=strategy_catalog_service._now(),
        )
        current_row.status = "superseded"
        current_row.updated_at = strategy_catalog_service._now()
        db.add(successor)
        db.flush()
        engine_config.selected_configuration_revision_id = successor.id
        engine_config.selected_configuration_revision = successor.revision
        engine_config.updated_at = strategy_catalog_service._now()
        db.flush()
        result = _serialize_revision(successor)

    log_audit_event(
        "AUTOMATION_CONFIGURATION_REVISED",
        f"Automation settings confirmed as configuration revision {result['revision']}.",
        metadata={
            "configuration_revision_id": result["id"],
            "configuration_revision": result["revision"],
            "supersedes_revision_id": result["supersedes_revision_id"],
            "changes": deepcopy(changes),
        },
    )
    return result


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


def selected_configuration_row(
    db,
    user_id: uuid.UUID,
    mode: str | None = None,
    *,
    lock: bool = False,
) -> models.StrategyConfigurationRevision | None:
    """Return the owner's selected immutable revision inside the caller's transaction."""
    statement = select(models.UserEngineConfig).where(
        models.UserEngineConfig.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update()
    engine_config = db.scalar(statement)
    if engine_config is None or engine_config.selected_configuration_revision_id is None:
        return None
    row = db.get(
        models.StrategyConfigurationRevision,
        engine_config.selected_configuration_revision_id,
    )
    if (
        row is None
        or row.user_id != user_id
        or row.status != "active"
        or (mode is not None and row.mode != mode)
    ):
        return None
    return row


def configuration_for_instance_row(
    db,
    user_id: uuid.UUID,
    strategy_instance_id: uuid.UUID,
    mode: str,
    *,
    lock: bool = False,
) -> models.StrategyConfigurationRevision | None:
    """Return the active immutable revision owned by one strategy instance.

    Private webhook credentials and legacy subscription fan-out are bound to a
    concrete instance. They must capture that instance's revision rather than a
    different strategy that happens to be selected in the Trading UI.
    """
    statement = (
        select(models.StrategyConfigurationRevision)
        .where(
            models.StrategyConfigurationRevision.user_id == user_id,
            models.StrategyConfigurationRevision.strategy_instance_id
            == strategy_instance_id,
            models.StrategyConfigurationRevision.mode == mode,
            models.StrategyConfigurationRevision.status == "active",
        )
        .order_by(
            models.StrategyConfigurationRevision.revision.desc(),
            models.StrategyConfigurationRevision.committed_at.desc(),
        )
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def selected_configuration(user_id: uuid.UUID, mode: str | None = None) -> dict[str, Any] | None:
    with session_scope() as db:
        row = selected_configuration_row(db, user_id, mode)
        if row is None:
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
    "configuration_for_instance_row",
    "configuration_mode_for_execution",
    "current_revision",
    "get_configuration",
    "list_configurations",
    "revise_automation_configuration",
    "save_configuration",
    "selected_configuration",
    "selected_configuration_row",
]
