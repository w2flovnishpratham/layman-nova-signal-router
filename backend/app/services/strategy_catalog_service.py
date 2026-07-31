"""Owner-scoped unified strategy catalog and setup persistence."""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import crud, models
from app.db.engine import session_scope
from app.domain.strategy_instance_state_machine import InstanceState
from app.services import built_in_strategy_registry as built_ins
from app.services import strategy_instance_service as instances

_SETUP_ROOT = "unified_setup"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def strategy_key_for_entry(entry: dict[str, Any]) -> str:
    if entry.get("source_type") == "NOVA_SHARED":
        key = built_ins.key_for_catalog_code(entry.get("strategy_code"))
        if key:
            return key
    return str(entry["instance_id"])


def _saved_setups(row: models.StrategyInstance) -> dict[str, Any]:
    risk = row.risk_config if isinstance(row.risk_config, dict) else {}
    setup = risk.get(_SETUP_ROOT)
    return deepcopy(setup) if isinstance(setup, dict) else {}


def saved_setups_for_instance(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> dict[str, Any]:
    with session_scope() as db:
        row = db.scalar(
            select(models.StrategyInstance).where(
                models.StrategyInstance.id == uuid.UUID(str(instance_id)),
                models.StrategyInstance.user_id == user_id,
            )
        )
        return _saved_setups(row) if row else {}


def _imported_catalog_entry(entry: dict[str, Any]) -> dict[str, Any]:
    selectable = bool(entry["selectable"] and entry["paper_eligible"])
    return {
        "strategy_key": str(entry["instance_id"]),
        "strategy_instance_id": str(entry["instance_id"]),
        "source_type": "IMPORTED",
        "name": entry["display_name"],
        "version": entry.get("strategy_version"),
        "description": "Imported owner-bound Pine strategy.",
        "availability": "READY" if selectable else "UNAVAILABLE",
        "disabled_reason": None if selectable else entry.get("blocking_reason") or "Not Paper-ready",
        "paper_eligible": selectable,
        "live_eligible": False,
        "selected": bool(entry.get("selected")),
        "runtime_state": str(entry.get("instance_status") or "stopped").upper(),
        "setup_schema": built_ins.standard_setup_schema(),
        "saved_setup": deepcopy(entry.get("saved_setup") or {}),
        "readiness": entry.get("readiness"),
        "pine_exit_behavior": entry.get("pine_exit_behavior"),
    }


def get_catalog(user_id: uuid.UUID, *, runtime_state: str = "STOPPED") -> dict[str, Any]:
    picker = instances.list_engine_strategies(user_id)
    selected_id = picker["selected_instance_id"]
    built_in_selection: str | None = None
    imported: list[dict[str, Any]] = []
    selected_setup: dict[str, Any] = {}

    for entry in picker["strategies"]:
        if entry.get("source_type") == "NOVA_SHARED":
            if entry.get("selected"):
                built_in_selection = built_ins.key_for_catalog_code(entry.get("strategy_code"))
                selected_setup = deepcopy(entry.get("saved_setup") or {})
            continue
        normalized = _imported_catalog_entry(entry)
        imported.append(normalized)
        if normalized["selected"]:
            selected_setup = deepcopy(normalized["saved_setup"])

    nova = []
    for definition in built_ins.list_built_ins():
        selected = definition["strategy_key"] == built_in_selection
        nova.append(
            {
                **definition,
                # The selected built-in must expose its real instance id so the
                # client can start the engine; unselected built-ins have no
                # persistent instance and stay null.
                "strategy_instance_id": str(selected_id) if selected and selected_id is not None else None,
                "source_type": "BUILT_IN",
                "selected": selected,
                "runtime_state": runtime_state,
                "saved_setup": deepcopy(selected_setup) if selected else {},
            }
        )

    selected_key = built_in_selection
    if selected_key is None and selected_id is not None:
        selected_key = str(selected_id)
    return {
        "strategies": [*nova, *imported],
        "selected_strategy_key": selected_key,
        "selected_strategy_instance_id": selected_id,
        "setup_progress": _setup_progress(selected_key, selected_setup),
    }


def _setup_progress(strategy_key: str | None, saved: dict[str, Any]) -> dict[str, Any]:
    completed_mode = next(
        (mode for mode in ("paper", "live") if isinstance(saved.get(mode), dict) and saved[mode].get("complete")),
        None,
    )
    return {
        "strategy_key": strategy_key,
        "mode": completed_mode,
        "stage": "REVIEW" if completed_mode else ("CONFIGURE" if strategy_key else "CHOOSE_STRATEGY"),
        "complete": completed_mode is not None,
    }


def _materialize_builtin(user_id: uuid.UUID, definition: dict[str, Any]) -> uuid.UUID:
    """Return a real owner-bound instance for a runnable built-in.

    A missing legacy subscription is created inactive, so selection cannot
    enqueue a signal or start execution.  The existing mirror function creates
    the correctly linked NOVA_SHARED instance in the same transaction.
    """
    with session_scope() as db:
        catalog = db.scalar(
            select(models.StrategyCatalog).where(
                models.StrategyCatalog.owner_user_id.is_(None),
                models.StrategyCatalog.code == definition["catalog_code"],
            )
        )
        if catalog is None or catalog.status not in {"active", "beta"}:
            raise instances.InstanceError(
                "The built-in strategy registry row is unavailable.",
                status_code=409,
                code="BUILT_IN_REGISTRY_UNAVAILABLE",
            )
        existing = db.scalar(
            select(models.StrategyInstance)
            .where(
                models.StrategyInstance.user_id == user_id,
                models.StrategyInstance.strategy_id == catalog.id,
                models.StrategyInstance.source_journey == "NOVA_SHARED",
                models.StrategyInstance.status != InstanceState.ARCHIVED.value,
            )
            .order_by(models.StrategyInstance.created_at.desc())
        )
        if (
            existing is not None
            and existing.status != InstanceState.STOPPED.value
            and existing.legacy_subscription_id is not None
        ):
            return existing.id

        subscription = db.scalar(
            select(models.StrategySubscription).where(
                models.StrategySubscription.user_id == user_id,
                models.StrategySubscription.strategy_name == definition["catalog_code"],
            )
        )
        if subscription is None:
            subscription = models.StrategySubscription(
                user_id=user_id,
                strategy_name=definition["catalog_code"],
                lots=1,
                execution_mode="signal_only",
                active=False,
            )
            db.add(subscription)
            db.flush()
        elif subscription.active:
            subscription.active = False
            subscription.updated_at = _now()
            db.flush()

        linked = db.scalar(
            select(models.StrategyInstance).where(
                models.StrategyInstance.legacy_subscription_id == subscription.id,
                models.StrategyInstance.status != InstanceState.ARCHIVED.value,
            )
        )
        if (
            linked is None
            and existing is not None
            and existing.status != InstanceState.STOPPED.value
            and existing.legacy_subscription_id is None
        ):
            existing.legacy_subscription_id = subscription.id
            instances.sync_instance_from_subscription(db, subscription)
            db.flush()
            linked = existing
        if linked is None or linked.status == InstanceState.STOPPED.value:
            if linked is not None:
                linked.legacy_subscription_id = None
                db.flush()
            instances.sync_instance_from_subscription(db, subscription)
            db.flush()
            linked = db.scalar(
                select(models.StrategyInstance).where(
                    models.StrategyInstance.legacy_subscription_id == subscription.id
                )
            )
        if linked is None:
            raise instances.InstanceError(
                "Could not create the owner-bound built-in strategy instance.",
                status_code=409,
                code="BUILT_IN_INSTANCE_UNAVAILABLE",
            )
        _ensure_managed_tradingview_setup(db, user_id, linked, catalog)
        return linked.id


def _ensure_managed_tradingview_setup(
    db,
    user_id: uuid.UUID,
    instance: models.StrategyInstance,
    catalog: models.StrategyCatalog,
) -> None:
    """NOVA_SHARED strategies run on a NOVA-owned Pine script the subscriber
    never receives -- self-managed TradingView setup (their own credential in
    their own copy of the chart) is impossible for them. Every such instance
    must start NOVA-managed, not default to self-managed and silently break.
    """
    existing = db.scalar(
        select(models.TradingViewSetup).where(
            models.TradingViewSetup.strategy_instance_id == instance.id,
        )
    )
    if existing is not None:
        return
    version = db.scalar(
        select(models.StrategyVersion)
        .where(
            models.StrategyVersion.strategy_id == catalog.id,
            models.StrategyVersion.status == "approved",
        )
        .order_by(models.StrategyVersion.approved_at.desc())
    )
    if version is None:
        return
    # (user_id, approved_version_id, pine_conversion_request_id=NULL) is
    # unique -- a prior instance of this same shared strategy for this user
    # may already have a setup row pointing at the same approved version.
    # Check first rather than insert-and-catch: this runs inside the same
    # transaction as the instance creation above, and Postgres aborts the
    # whole transaction (not just the failed statement) on an IntegrityError,
    # which would roll back that instance creation too.
    duplicate = db.scalar(
        select(models.TradingViewSetup).where(
            models.TradingViewSetup.user_id == user_id,
            models.TradingViewSetup.approved_version_id == version.id,
            models.TradingViewSetup.pine_conversion_request_id.is_(None),
        )
    )
    if duplicate is not None:
        return
    row = models.TradingViewSetup(
        user_id=user_id,
        strategy_instance_id=instance.id,
        approved_version_id=version.id,
        setup_type="NOVA_MANAGED_TRADINGVIEW",
    )
    db.add(row)
    db.flush()
    crud.add_audit_log(
        db,
        user_id=user_id,
        action="TRADINGVIEW_SETUP_CREATED",
        metadata={
            "setup_id": str(row.id),
            "instance_id": str(instance.id),
            "setup_type": "NOVA_MANAGED_TRADINGVIEW",
            "source": "nova_shared_auto_managed",
        },
    )


def select_strategy(user_id: uuid.UUID, strategy_key: str) -> dict[str, Any]:
    definition = built_ins.get_built_in(strategy_key)
    if definition is not None:
        if definition["availability"] != "READY" or not definition["paper_eligible"]:
            raise instances.InstanceError(
                definition["disabled_reason"] or "This strategy is unavailable.",
                status_code=409,
                code="STRATEGY_UNAVAILABLE",
            )
        instance_id = _materialize_builtin(user_id, definition)
    else:
        try:
            instance_id = uuid.UUID(str(strategy_key))
        except ValueError as exc:
            raise instances.InstanceError(
                "Strategy not found.",
                status_code=404,
                code="STRATEGY_NOT_FOUND",
            ) from exc
    selected = instances.set_engine_selection(user_id, instance_id)
    selected["selected_strategy_key"] = strategy_key_for_entry(selected["selected"])
    return selected


def _field_map(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(field["key"]): field for field in schema.get("fields", [])}


def validate_setup_values(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Public alias: lets a caller validate without writing anything."""
    return _validate_values(schema, values)


def setup_schema_for(strategy_key: str) -> dict[str, Any]:
    definition = built_ins.get_built_in(strategy_key)
    return definition["setup_schema"] if definition else built_ins.standard_setup_schema()


def _validate_values(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    fields = _field_map(schema)
    unknown = set(values) - set(fields)
    if unknown:
        raise instances.InstanceError(
            f"Unsupported setup fields: {', '.join(sorted(unknown))}.",
            status_code=422,
            code="INVALID_SETUP",
        )
    normalized: dict[str, Any] = {}
    for key, field in fields.items():
        value = values.get(key, field.get("default"))
        if field.get("required") and value is None:
            raise instances.InstanceError(
                f"{field['label']} is required.",
                status_code=422,
                code="INVALID_SETUP",
            )
        if field["type"] == "choice":
            value = str(value).upper()
            if value not in field["options"]:
                raise instances.InstanceError(
                    f"{field['label']} must be one of {', '.join(field['options'])}.",
                    status_code=422,
                    code="INVALID_SETUP",
                )
        elif field["type"] == "integer":
            if isinstance(value, bool) or int(value) != float(value):
                raise instances.InstanceError(
                    f"{field['label']} must be a whole number.",
                    status_code=422,
                    code="INVALID_SETUP",
                )
            value = int(value)
        elif field["type"] == "decimal":
            if isinstance(value, bool):
                raise instances.InstanceError(
                    f"{field['label']} must be numeric.",
                    status_code=422,
                    code="INVALID_SETUP",
                )
            value = float(value)
        if field["type"] in {"integer", "decimal"}:
            if value < field["minimum"] or value > field["maximum"]:
                raise instances.InstanceError(
                    f"{field['label']} must be between {field['minimum']} and {field['maximum']}.",
                    status_code=422,
                    code="INVALID_SETUP",
                )
        normalized[key] = value
    return normalized


def save_setup(
    user_id: uuid.UUID,
    *,
    strategy_key: str,
    mode: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"paper", "live"}:
        raise instances.InstanceError("Mode must be paper or live.", status_code=422, code="INVALID_SETUP")

    selection = instances.get_engine_selection(user_id)
    selected = selection["selected"]
    if selected is None or strategy_key_for_entry(selected) != strategy_key:
        raise instances.InstanceError(
            "The selected strategy changed. Select it again before saving setup.",
            status_code=409,
            code="SELECTION_CHANGED",
        )
    definition = built_ins.get_built_in(strategy_key)
    if normalized_mode == "live" and not bool(selected.get("live_eligible")):
        raise instances.InstanceError(
            "Live setup is unavailable for this strategy.",
            status_code=409,
            code="LIVE_UNAVAILABLE",
        )
    schema = definition["setup_schema"] if definition else built_ins.standard_setup_schema()
    normalized = _validate_values(schema, values)

    with session_scope() as db:
        row = db.scalar(
            select(models.StrategyInstance).where(
                models.StrategyInstance.id == uuid.UUID(str(selection["selected_instance_id"])),
                models.StrategyInstance.user_id == user_id,
            ).with_for_update()
        )
        if row is None:
            raise instances.InstanceError("Selected strategy is unavailable.", status_code=409)
        risk = deepcopy(row.risk_config) if isinstance(row.risk_config, dict) else {}
        setups = risk.get(_SETUP_ROOT)
        setups = deepcopy(setups) if isinstance(setups, dict) else {}
        saved = {
            **normalized,
            "complete": True,
            "strategy_key": strategy_key,
            "mode": normalized_mode,
            "saved_at": _now().isoformat(),
        }
        setups[normalized_mode] = saved
        risk[_SETUP_ROOT] = setups
        row.risk_config = risk
        row.current_lots = int(normalized["lots"])
        row.updated_at = _now()
        if row.legacy_subscription_id:
            subscription = db.get(models.StrategySubscription, row.legacy_subscription_id)
            if subscription:
                subscription.lots = row.current_lots
                subscription.updated_at = _now()
        db.flush()
        return {
            "strategy_key": strategy_key,
            "strategy_instance_id": str(row.id),
            "mode": normalized_mode,
            "values": saved,
            "setup_progress": _setup_progress(strategy_key, setups),
        }


def apply_selected_setup(user_id: uuid.UUID, selected: dict[str, Any]) -> dict[str, Any]:
    setups = selected.get("saved_setup") or {}
    paper = setups.get("paper") if isinstance(setups, dict) else None
    if not isinstance(paper, dict) or not paper.get("complete"):
        raise instances.InstanceError(
            "Complete and save the Paper setup before starting the engine.",
            status_code=409,
            code="SETUP_INCOMPLETE",
        )
    _require_committed_revision(paper)
    return paper


def _require_committed_revision(saved: dict[str, Any]) -> None:
    """The engine may only start on a configuration whose two halves agree.

    A setup saved through the combined endpoint carries the same revision as the
    risk settings. A mismatch means a torn or half-applied save, so refuse
    rather than start the engine on limits that were never committed together.
    """
    from app.services.state_store import get_runtime_settings

    try:
        setup_revision = int(saved.get("configuration_revision") or 0)
    except (TypeError, ValueError):
        setup_revision = 0
    try:
        settings_revision = int(get_runtime_settings().get("configuration_revision") or 0)
    except (TypeError, ValueError):
        settings_revision = 0
    if setup_revision != settings_revision:
        raise instances.InstanceError(
            "Strategy setup and risk settings are at different revisions. "
            "Save the configuration again before starting the engine.",
            status_code=409,
            code="CONFIGURATION_TORN",
        )
