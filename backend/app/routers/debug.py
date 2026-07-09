from __future__ import annotations

import re
import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.config import DEFAULT_EXCHANGE_SEGMENT, settings
from app.core.enums import (
    StrategyCatalogStatus,
    StrategyExecutionMode,
    StrategyInstanceStatus,
)
from app.db import models
from app.services.user_context import CurrentUser
from app.core.feature_flags import (
    MULTI_STRATEGY_FANOUT,
    MULTI_STRATEGY_MODEL,
    STRATEGY_INSTANCE_MUTATION_DEBUG,
    V2_PAPER_RUNNER_DEBUG,
    feature_flag_states,
    is_feature_enabled,
)
from app.db.engine import session_scope
from app.schemas.signal import NormalizedSignal
from app.services.audit_logger import log_audit_event, log_order_event, read_jsonl
from app.services.credential_vault import get_dhan_credentials, get_webhook_secret
from app.services.dhan_client import DHAN_BASE_URL
from app.services.dhan_debugger import (
    build_dhan_headers_debug,
    get_outgoing_ip,
    mask_secret,
    validate_dhan_env,
    validate_dhan_payload,
)
from app.services.dhan_error_interpreter import interpret_dhan_error
from app.services.dhan_response_safety import sanitize_dhan_response_surface
from app.services.execution_router import _build_dhan_payload_and_resolution
from app.services.risk_manager import _market_is_open, evaluate_entry, evaluate_exit
from app.services.security_id_resolver import DEFAULT_SECURITY_ID_WARNING, resolve_security_id_for_contract
from app.services.signal_parser import PayloadParseError, UnsupportedPayloadFormatError, parse_webhook_payload
from app.services.signal_validator import validate_signal
from app.services.state_store import get_app_state, get_runtime_settings
from app.services.strategy_paper_fanout_runner_v2 import (
    process_ready_v2_paper_jobs_once,
    run_v2_paper_fanout_once,
)
from app.services.strategy_paper_fanout_inspector_v2 import (
    get_v2_paper_fanout_status,
    list_v2_paper_fanout_jobs,
    list_v2_paper_strategy_instances,
)


V2_PAPER_DEBUG_MAX_JOBS = 5


class V2PaperFanoutRunOnceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_code: str = Field(min_length=1)
    payload: dict[str, Any]
    max_jobs: int = Field(default=1, ge=1, le=V2_PAPER_DEBUG_MAX_JOBS)
    confirm_paper_only: bool = False


class V2PaperFanoutProcessReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_jobs: int = Field(default=1, ge=1, le=V2_PAPER_DEBUG_MAX_JOBS)
    confirm_paper_only: bool = False


# Phase 2F-1: paper strategy instance lifecycle (create born paused / pause /
# resume). extra="forbid" rejects any client attempt to smuggle execution_mode,
# status, user_id, secrets, or live/real markers as extra body fields (422).
V2_INSTANCE_MAX_LOTS = 20  # matches the manual-order panel's existing safe cap

V2_INSTANCE_LIFECYCLE_MODES = {
    StrategyExecutionMode.PAPER_LIVE_DATA.value,
    StrategyExecutionMode.SIGNAL_ONLY.value,
}
V2_INSTANCE_TRANSITION_STATUSES = {"active", "paused"}
V2_INSTANCE_DETAILS_HISTORY_EVENTS = {
    "V2_INSTANCE_ARCHIVED",
    "V2_INSTANCE_CREATED",
    "V2_INSTANCE_PAUSED",
    "V2_INSTANCE_RESTORED",
    "V2_INSTANCE_RESUMED",
    "V2_INSTANCE_SETTINGS_EDITED",
}
V2_INSTANCE_DETAILS_SAFE_HISTORY_FIELDS = {
    "execution_mode",
    "instance_label",
    "lots",
    "side_preference",
    "status",
    "strategy_code",
}
V2_INSTANCE_DETAILS_SAFE_STATUSES = {status.value for status in StrategyInstanceStatus}
V2_INSTANCE_DETAILS_SAFE_EXECUTION_MODES = {mode.value for mode in StrategyExecutionMode}
V2_INSTANCE_DETAILS_SAFE_SIDE_PREFERENCES = {"BOTH", "CE", "PE"}
V2_INSTANCE_DETAILS_HISTORY_LIMIT = 100
V2_INSTANCE_DETAILS_AUDIT_FILE_LIMIT = 500


class V2InstanceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_code: str = Field(min_length=1, max_length=120)
    instance_label: str | None = Field(default=None, max_length=120)
    lots: int = Field(default=1, ge=1, le=V2_INSTANCE_MAX_LOTS)
    side_preference: Literal["BOTH", "CE", "PE"] | None = None
    confirm_paper_only: bool = False


class V2InstanceLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_paper_only: bool = False


class V2InstanceSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_label: str | None = None
    lots: int | None = Field(default=None, ge=1, le=V2_INSTANCE_MAX_LOTS)
    side_preference: Literal["BOTH", "CE", "PE"] | None = None
    confirm_paper_only: bool = False


def require_debug_enabled() -> None:
    if not settings.DEBUG_ENABLED:
        raise HTTPException(status_code=404, detail="Debug endpoints are disabled.")


router = APIRouter(dependencies=[Depends(require_debug_enabled)])


def _public_signal(signal: NormalizedSignal) -> dict[str, Any]:
    data = signal.model_dump(mode="json")
    data["secret"] = mask_secret(data.get("secret"))
    data.pop("raw_payload", None)
    return data


@router.get("/feature-flags")
def get_feature_flags() -> dict[str, bool]:
    return feature_flag_states()


def _require_v2_paper_runner_debug_enabled() -> None:
    if not is_feature_enabled(MULTI_STRATEGY_FANOUT):
        raise HTTPException(status_code=403, detail="MULTI_STRATEGY_FANOUT is disabled.")
    if not is_feature_enabled(V2_PAPER_RUNNER_DEBUG):
        raise HTTPException(status_code=403, detail="V2 paper runner debug trigger is disabled.")


def _require_paper_confirmation(confirm_paper_only: bool) -> None:
    if confirm_paper_only is not True:
        raise HTTPException(status_code=403, detail="confirm_paper_only must be true.")


def _reject_non_paper_execution_marker(payload: dict[str, Any]) -> None:
    values: list[Any] = []
    if isinstance(payload, dict):
        values.append(payload.get("execution_mode"))
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            values.append(metadata.get("execution_mode"))
    for value in values:
        if value in (None, ""):
            continue
        if str(value).strip().lower() != StrategyExecutionMode.PAPER_LIVE_DATA.value:
            raise HTTPException(status_code=403, detail="Only paper_live_data execution is allowed.")


def _runner_response(value: Any) -> dict[str, Any]:
    data = value.as_dict() if hasattr(value, "as_dict") else value
    if not isinstance(data, dict):
        return {"ok": False, "errors": [{"reason": "INVALID_RUNNER_RESPONSE"}]}
    return _sanitize_v2_paper_debug_response(data)


def _sanitize_v2_paper_debug_response(value: Any) -> Any:
    redacted_keys = {
        "access_token",
        "client_id",
        "credential_hash",
        "credentials_hash",
        "headers",
        "payload",
        "password",
        "pin",
        "proxy_url",
        "raw_payload",
        "raw_response",
        "request",
        "response",
        "response_json",
        "response_text",
        "secret",
        "signal_payload",
        "token",
        "webhook_key_hash",
    }
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in redacted_keys:
                continue
            sanitized[str(key)] = _sanitize_v2_paper_debug_response(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_v2_paper_debug_response(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_v2_paper_debug_response(item) for item in value]
    return value


@router.get("/v2/paper-fanout/status")
def get_v2_paper_fanout_status_debug(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    _require_v2_paper_runner_debug_enabled()
    with session_scope() as db:
        return _sanitize_v2_paper_debug_response(get_v2_paper_fanout_status(db, limit=limit))


@router.get("/v2/paper-fanout/jobs")
def list_v2_paper_fanout_jobs_debug(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    _require_v2_paper_runner_debug_enabled()
    with session_scope() as db:
        return _sanitize_v2_paper_debug_response(list_v2_paper_fanout_jobs(db, limit=limit))


@router.get("/v2/paper-fanout/instances")
def list_v2_paper_strategy_instances_debug(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    _require_v2_paper_runner_debug_enabled()
    with session_scope() as db:
        return _sanitize_v2_paper_debug_response(list_v2_paper_strategy_instances(db, limit=limit))


@router.post("/v2/paper-fanout/run-once")
def run_v2_paper_fanout_debug(request: V2PaperFanoutRunOnceRequest) -> dict[str, Any]:
    _require_v2_paper_runner_debug_enabled()
    _require_paper_confirmation(request.confirm_paper_only)
    _reject_non_paper_execution_marker(request.payload)

    with session_scope() as db:
        result = run_v2_paper_fanout_once(
            db,
            dict(request.payload),
            request.strategy_code,
            max_jobs=request.max_jobs,
        )
    return _runner_response(result)


@router.post("/v2/paper-fanout/process-ready")
def process_ready_v2_paper_jobs_debug(request: V2PaperFanoutProcessReadyRequest) -> dict[str, Any]:
    _require_v2_paper_runner_debug_enabled()
    _require_paper_confirmation(request.confirm_paper_only)

    with session_scope() as db:
        result = process_ready_v2_paper_jobs_once(db, max_jobs=request.max_jobs)
    return _runner_response(result)


# ---------------------------------------------------------------------------
# Phase 2F-1: paper strategy instance lifecycle (create born paused / pause /
# resume). Data-only mutations: exactly one table (user_strategy_instances) is
# written. No signals, no jobs, no credentials, no runtime state, and NEVER
# route_signal / Dhan / PaperBroker. Deliberately NOT gated on the execution
# flags (MULTI_STRATEGY_FANOUT / V2_PAPER_RUNNER_DEBUG) so "can mutate rows"
# and "can execute paper jobs" stay separate switches.
# ---------------------------------------------------------------------------


def _require_instance_mutation_debug_enabled() -> None:
    if not is_feature_enabled(MULTI_STRATEGY_MODEL):
        raise HTTPException(status_code=403, detail="MULTI_STRATEGY_MODEL is disabled.")
    if not is_feature_enabled(STRATEGY_INSTANCE_MUTATION_DEBUG):
        raise HTTPException(status_code=403, detail="Strategy instance mutation debug is disabled.")


def _require_internal_debug_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.is_admin or user.is_dev:
        return user
    raise HTTPException(status_code=403, detail="Internal debug access required.")


def _require_internal_mutation_user(user: CurrentUser = Depends(_require_internal_debug_user)) -> CurrentUser:
    return user


def _sanitized_instance_label(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", str(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:120] or None


def _instance_mutation_public(instance: models.UserStrategyInstance, strategy_code: str) -> dict[str, Any]:
    """Safe public projection only — no config internals, no secrets."""
    return {
        "id": str(instance.id),
        "strategy_code": strategy_code,
        "instance_label": instance.instance_label,
        "execution_mode": instance.execution_mode,
        "status": instance.status,
        "lots": instance.lots,
        "side_preference": instance.side_preference,
    }


def _v2_details_dt(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _instance_details_public(
    instance: models.UserStrategyInstance,
    catalog: models.StrategyCatalog | None,
    version: models.StrategyVersion | None,
) -> dict[str, Any]:
    """Read-only details projection. Never exposes config/risk internals."""
    return {
        "id": str(instance.id),
        "strategy_code": catalog.code if catalog is not None else "UNKNOWN",
        "strategy_name": catalog.name if catalog is not None else None,
        "strategy_version": version.version if version is not None else None,
        "status": instance.status,
        "execution_mode": instance.execution_mode,
        "instance_label": instance.instance_label,
        "lots": instance.lots,
        "side_preference": instance.side_preference,
        "created_at": _v2_details_dt(instance.created_at),
        "updated_at": _v2_details_dt(instance.updated_at),
    }


def _safe_history_strategy_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()[:80]
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", cleaned):
        return None
    return cleaned


def _safe_history_field_value(field: str, value: Any) -> tuple[bool, Any]:
    if value is None:
        return True, None
    if field == "instance_label":
        if not isinstance(value, str):
            return False, None
        safe_label = _sanitized_instance_label(value)
        return (safe_label is not None), safe_label
    if field == "lots":
        if isinstance(value, bool):
            return False, None
        if isinstance(value, int):
            lots = value
        elif isinstance(value, float) and value.is_integer():
            lots = int(value)
        else:
            return False, None
        if 1 <= lots <= V2_INSTANCE_MAX_LOTS:
            return True, lots
        return False, None
    if field == "side_preference":
        text = str(value).strip().upper()
        return (text in V2_INSTANCE_DETAILS_SAFE_SIDE_PREFERENCES), text
    if field == "status":
        text = str(value).strip().lower()
        return (text in V2_INSTANCE_DETAILS_SAFE_STATUSES), text
    if field == "execution_mode":
        text = str(value).strip().lower()
        return (text in V2_INSTANCE_DETAILS_SAFE_EXECUTION_MODES), text
    if field == "strategy_code":
        safe_code = _safe_history_strategy_code(value)
        return (safe_code is not None), safe_code
    return False, None


def _safe_history_status(value: Any) -> str | None:
    if value is None:
        return None
    allowed, safe_value = _safe_history_field_value("status", value)
    return safe_value if allowed else None


def _safe_history_values(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, item in value.items():
        field = str(key)
        if field not in V2_INSTANCE_DETAILS_SAFE_HISTORY_FIELDS:
            continue
        allowed, safe_value = _safe_history_field_value(field, item)
        if allowed:
            safe[field] = safe_value
    return safe


def _safe_history_changed_fields(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        str(field)
        for field in value
        if str(field) in V2_INSTANCE_DETAILS_SAFE_HISTORY_FIELDS
    ]


def _history_summary(event_type: str) -> str:
    return {
        "V2_INSTANCE_ARCHIVED": "Archived",
        "V2_INSTANCE_CREATED": "Instance created paused",
        "V2_INSTANCE_PAUSED": "Instance paused",
        "V2_INSTANCE_RESTORED": "Restored",
        "V2_INSTANCE_RESUMED": "Instance resumed",
        "V2_INSTANCE_SETTINGS_EDITED": "Instance settings edited",
    }.get(event_type, "Instance event")


def _instance_history_item(event_type: str, created_at: Any, metadata: Any) -> dict[str, Any] | None:
    if event_type not in V2_INSTANCE_DETAILS_HISTORY_EVENTS:
        return None
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    item: dict[str, Any] = {
        "event_type": event_type,
        "created_at": _v2_details_dt(created_at),
        "summary": _history_summary(event_type),
        "changed_fields": _safe_history_changed_fields(safe_metadata.get("changed_fields")),
        "previous_status": _safe_history_status(safe_metadata.get("previous_status")),
        "new_status": _safe_history_status(safe_metadata.get("new_status")),
    }
    old_values = _safe_history_values(safe_metadata.get("old_values"))
    new_values = _safe_history_values(safe_metadata.get("new_values"))
    if old_values:
        item["old_values"] = old_values
    if new_values:
        item["new_values"] = new_values
    if safe_metadata.get("actor_user_id"):
        item["actor_type"] = "internal_user"
    return item


def _instance_history_from_db(
    db,
    *,
    user: CurrentUser,
    instance_id: uuid.UUID,
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(models.AuditLog)
        .where(models.AuditLog.user_id == user.id)
        .where(models.AuditLog.action.in_(V2_INSTANCE_DETAILS_HISTORY_EVENTS))
        .order_by(models.AuditLog.created_at.asc())
        .limit(V2_INSTANCE_DETAILS_HISTORY_LIMIT)
    ).all()
    history: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.audit_metadata if isinstance(row.audit_metadata, dict) else {}
        if str(metadata.get("instance_id") or "") != str(instance_id):
            continue
        event_type = str(metadata.get("event_type") or row.action)
        item = _instance_history_item(event_type, row.created_at, metadata)
        if item is not None:
            history.append(item)
    return history


def _instance_history_from_audit_file(
    *,
    user: CurrentUser,
    instance_id: uuid.UUID,
) -> list[dict[str, Any]]:
    try:
        records = read_jsonl("audit", limit=V2_INSTANCE_DETAILS_AUDIT_FILE_LIMIT)
    except Exception:
        return []

    history: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("instance_id") or "") != str(instance_id):
            continue
        actor_user_id = metadata.get("actor_user_id")
        if actor_user_id and str(actor_user_id) != user.id_str:
            continue
        event_type = str(record.get("event_type") or metadata.get("event_type") or "")
        item = _instance_history_item(event_type, record.get("timestamp"), metadata)
        if item is not None:
            history.append(item)
    return history


def _merge_instance_history(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for group in groups:
        for item in group:
            key = (
                item.get("event_type"),
                item.get("created_at"),
                tuple(item.get("changed_fields") or []),
                item.get("previous_status"),
                item.get("new_status"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    merged.sort(key=lambda item: str(item.get("created_at") or ""))
    return merged[-V2_INSTANCE_DETAILS_HISTORY_LIMIT:]


def _audit_instance_mutation(
    event_type: str,
    *,
    user: CurrentUser,
    instance: models.UserStrategyInstance,
    strategy_code: str,
    previous_status: str | None,
    changed: bool,
) -> None:
    # Metadata only. Never request bodies, never secrets, never raw payloads.
    log_audit_event(
        event_type,
        f"{event_type} for strategy {strategy_code}.",
        severity="INFO",
        metadata={
            "actor_user_id": user.id_str,
            "instance_id": str(instance.id),
            "strategy_code": strategy_code,
            "previous_status": previous_status,
            "new_status": instance.status,
            "execution_mode": instance.execution_mode,
            "changed": changed,
            "feature_flags": feature_flag_states(),
        },
    )


def _audit_instance_settings_edit(
    *,
    user: CurrentUser,
    instance: models.UserStrategyInstance,
    strategy_code: str,
    changed_fields: list[str],
    old_values: dict[str, Any],
    new_values: dict[str, Any],
) -> None:
    # Metadata only. Never request bodies, credentials, or raw payloads.
    log_audit_event(
        "V2_INSTANCE_SETTINGS_EDITED",
        f"V2_INSTANCE_SETTINGS_EDITED for strategy {strategy_code}.",
        severity="INFO",
        metadata={
            "actor_user_id": user.id_str,
            "instance_id": str(instance.id),
            "strategy_code": strategy_code,
            "execution_mode": instance.execution_mode,
            "changed_fields": changed_fields,
            "old_values": old_values,
            "new_values": new_values,
            "feature_flags": feature_flag_states(),
        },
    )


def _mutation_breadcrumb(instance: models.UserStrategyInstance, action: str, actor: CurrentUser) -> None:
    # Reassign (not mutate in place) so SQLAlchemy detects the JSON change.
    instance.config_json = {
        **(instance.config_json or {}),
        "last_mutation": {
            "action": action,
            "actor_user_id": actor.id_str,
            "at": models.utcnow().isoformat(),
            "surface": "debug_lifecycle_v2f1",
        },
    }


@router.post("/v2/instances")
def create_v2_paper_instance_debug(
    request: V2InstanceCreateRequest,
    user: CurrentUser = Depends(_require_internal_mutation_user),
) -> dict[str, Any]:
    _require_instance_mutation_debug_enabled()
    _require_paper_confirmation(request.confirm_paper_only)

    with session_scope() as db:
        catalog = db.scalar(
            select(models.StrategyCatalog).where(models.StrategyCatalog.code == request.catalog_code.strip())
        )
        if catalog is None:
            raise HTTPException(status_code=404, detail="Strategy catalog entry not found.")
        if str(catalog.status) not in {StrategyCatalogStatus.ACTIVE.value, StrategyCatalogStatus.BETA.value}:
            raise HTTPException(status_code=409, detail="Strategy is not enabled for paper instances.")

        versions = db.scalars(
            select(models.StrategyVersion)
            .where(models.StrategyVersion.strategy_id == catalog.id)
            .order_by(models.StrategyVersion.created_at.asc())
        ).all()
        active_versions = [
            v
            for v in versions
            if v.status in {StrategyCatalogStatus.ACTIVE.value, StrategyCatalogStatus.BETA.value}
        ]
        if not active_versions:
            raise HTTPException(status_code=409, detail="Strategy has no active version.")
        version = active_versions[-1]

        instance = models.UserStrategyInstance(
            # SERVER-SIDE HARDCODED SAFETY INVARIANTS (Phase 2F-0 design):
            # owner is the authenticated user, mode is paper, and the instance
            # is BORN PAUSED so the debug fanout trigger cannot widen scope.
            user_id=user.id,
            strategy_id=catalog.id,
            strategy_version_id=version.id,
            instance_label=_sanitized_instance_label(request.instance_label) or f"{catalog.name} Paper",
            source_type=catalog.source_type,
            status=StrategyInstanceStatus.PAUSED.value,
            execution_mode=StrategyExecutionMode.PAPER_LIVE_DATA.value,
            lots=request.lots,
            side_preference=request.side_preference,
            config_json={"created_by": "debug_lifecycle_v2f1", "born_paused": True},
            risk_config={},
        )
        db.add(instance)
        db.flush()
        _mutation_breadcrumb(instance, "create", user)
        _audit_instance_mutation(
            "V2_INSTANCE_CREATED",
            user=user,
            instance=instance,
            strategy_code=catalog.code,
            previous_status=None,
            changed=True,
        )
        return {"ok": True, "changed": True, "instance": _instance_mutation_public(instance, catalog.code)}


def _v2_instance_lifecycle_transition(
    instance_id: str,
    target_status: str,
    action: str,
    audit_event: str,
    request: V2InstanceLifecycleRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    _require_instance_mutation_debug_enabled()
    _require_paper_confirmation(request.confirm_paper_only)

    try:
        parsed_id = uuid.UUID(str(instance_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Strategy instance not found.")

    with session_scope() as db:
        instance = db.get(models.UserStrategyInstance, parsed_id)
        # Non-owner gets 404 (not 403) so instance existence is not leaked.
        if instance is None or instance.user_id != user.id:
            raise HTTPException(status_code=404, detail="Strategy instance not found.")

        if str(instance.execution_mode) == StrategyExecutionMode.REAL_ORDERS.value:
            raise HTTPException(status_code=403, detail="real_orders instances cannot be mutated here.")
        if str(instance.execution_mode) not in V2_INSTANCE_LIFECYCLE_MODES:
            raise HTTPException(status_code=409, detail="This instance execution mode does not support lifecycle mutation.")

        catalog = db.get(models.StrategyCatalog, instance.strategy_id)
        strategy_code = catalog.code if catalog is not None else "UNKNOWN"

        current_status = str(instance.status)
        if current_status == target_status:
            return {"ok": True, "changed": False, "instance": _instance_mutation_public(instance, strategy_code)}
        if current_status not in V2_INSTANCE_TRANSITION_STATUSES:
            raise HTTPException(status_code=409, detail="This instance status does not support lifecycle mutation.")

        instance.status = target_status
        instance.updated_at = models.utcnow()
        _mutation_breadcrumb(instance, action, user)
        db.flush()
        _audit_instance_mutation(
            audit_event,
            user=user,
            instance=instance,
            strategy_code=strategy_code,
            previous_status=current_status,
            changed=True,
        )
        return {"ok": True, "changed": True, "instance": _instance_mutation_public(instance, strategy_code)}


def _v2_instance_archive_restore_transition(
    instance_id: str,
    target_status: str,
    *,
    audit_event: str,
    request: V2InstanceLifecycleRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    _require_instance_mutation_debug_enabled()
    _require_paper_confirmation(request.confirm_paper_only)

    try:
        parsed_id = uuid.UUID(str(instance_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Strategy instance not found.")

    with session_scope() as db:
        instance = db.get(models.UserStrategyInstance, parsed_id)
        # Non-owner gets 404 (not 403) so instance existence is not leaked.
        if instance is None or instance.user_id != user.id:
            raise HTTPException(status_code=404, detail="Strategy instance not found.")

        if str(instance.execution_mode) == StrategyExecutionMode.REAL_ORDERS.value:
            raise HTTPException(status_code=403, detail="real_orders instances cannot be mutated here.")
        if str(instance.execution_mode) not in V2_INSTANCE_LIFECYCLE_MODES:
            raise HTTPException(status_code=409, detail="This instance execution mode does not support lifecycle mutation.")

        catalog = db.get(models.StrategyCatalog, instance.strategy_id)
        strategy_code = catalog.code if catalog is not None else "UNKNOWN"
        current_status = str(instance.status)

        if current_status == target_status:
            return {"ok": True, "changed": False, "instance": _instance_mutation_public(instance, strategy_code)}

        if target_status == StrategyInstanceStatus.ARCHIVED.value:
            if current_status == StrategyInstanceStatus.ACTIVE.value:
                raise HTTPException(status_code=409, detail="Pause the instance before archiving.")
            if current_status != StrategyInstanceStatus.PAUSED.value:
                raise HTTPException(status_code=409, detail="This instance status does not support archive.")
        elif target_status == StrategyInstanceStatus.PAUSED.value:
            if current_status == StrategyInstanceStatus.PAUSED.value:
                return {"ok": True, "changed": False, "instance": _instance_mutation_public(instance, strategy_code)}
            if current_status != StrategyInstanceStatus.ARCHIVED.value:
                raise HTTPException(status_code=409, detail="This instance status does not support restore.")

        instance.status = target_status
        instance.updated_at = models.utcnow()
        db.flush()
        _audit_instance_mutation(
            audit_event,
            user=user,
            instance=instance,
            strategy_code=strategy_code,
            previous_status=current_status,
            changed=True,
        )
        return {"ok": True, "changed": True, "instance": _instance_mutation_public(instance, strategy_code)}


def _settings_fields_present(request: V2InstanceSettingsRequest) -> set[str]:
    return {"instance_label", "lots", "side_preference"} & set(request.model_fields_set)


@router.get("/v2/instances/{instance_id}/details")
def get_v2_instance_details_debug(
    instance_id: str,
    user: CurrentUser = Depends(_require_internal_debug_user),
) -> dict[str, Any]:
    try:
        parsed_id = uuid.UUID(str(instance_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Strategy instance not found.")

    with session_scope() as db:
        instance = db.get(models.UserStrategyInstance, parsed_id)
        # Non-owner gets 404 (not 403) so instance existence is not leaked.
        if instance is None or instance.user_id != user.id:
            raise HTTPException(status_code=404, detail="Strategy instance not found.")

        catalog = db.get(models.StrategyCatalog, instance.strategy_id)
        version = db.get(models.StrategyVersion, instance.strategy_version_id) if instance.strategy_version_id else None
        db_history = _instance_history_from_db(db, user=user, instance_id=parsed_id)

        return {
            "ok": True,
            "instance": _instance_details_public(instance, catalog, version),
            "history": _merge_instance_history(
                db_history,
                _instance_history_from_audit_file(user=user, instance_id=parsed_id),
            ),
        }


@router.post("/v2/instances/{instance_id}/settings")
def update_v2_paper_instance_settings_debug(
    instance_id: str,
    request: V2InstanceSettingsRequest,
    user: CurrentUser = Depends(_require_internal_mutation_user),
) -> dict[str, Any]:
    _require_instance_mutation_debug_enabled()
    _require_paper_confirmation(request.confirm_paper_only)

    requested_fields = _settings_fields_present(request)
    if not requested_fields:
        raise HTTPException(status_code=400, detail="At least one settings field must be provided.")

    try:
        parsed_id = uuid.UUID(str(instance_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Strategy instance not found.")

    with session_scope() as db:
        instance = db.get(models.UserStrategyInstance, parsed_id)
        if instance is None or instance.user_id != user.id:
            raise HTTPException(status_code=404, detail="Strategy instance not found.")

        if str(instance.execution_mode) == StrategyExecutionMode.REAL_ORDERS.value:
            raise HTTPException(status_code=403, detail="real_orders instances cannot be mutated here.")
        if str(instance.execution_mode) not in V2_INSTANCE_LIFECYCLE_MODES:
            raise HTTPException(status_code=409, detail="This instance execution mode does not support settings edit.")

        current_status = str(instance.status)
        if current_status == StrategyInstanceStatus.ACTIVE.value:
            raise HTTPException(status_code=409, detail="Pause the instance before editing.")
        if current_status != StrategyInstanceStatus.PAUSED.value:
            raise HTTPException(status_code=409, detail="This instance status does not support settings edit.")

        catalog = db.get(models.StrategyCatalog, instance.strategy_id)
        strategy_code = catalog.code if catalog is not None else "UNKNOWN"

        candidate_values: dict[str, Any] = {}
        if "instance_label" in requested_fields:
            candidate_values["instance_label"] = _sanitized_instance_label(request.instance_label)
        if "lots" in requested_fields:
            candidate_values["lots"] = request.lots
        if "side_preference" in requested_fields:
            candidate_values["side_preference"] = request.side_preference

        old_values: dict[str, Any] = {}
        new_values: dict[str, Any] = {}
        changed_fields: list[str] = []
        for field, new_value in candidate_values.items():
            old_value = getattr(instance, field)
            if old_value != new_value:
                changed_fields.append(field)
                old_values[field] = old_value
                new_values[field] = new_value

        if not changed_fields:
            return {"ok": True, "changed": False, "instance": _instance_mutation_public(instance, strategy_code)}

        for field in changed_fields:
            setattr(instance, field, new_values[field])
        instance.updated_at = models.utcnow()
        _mutation_breadcrumb(instance, "settings_edit", user)
        db.flush()
        _audit_instance_settings_edit(
            user=user,
            instance=instance,
            strategy_code=strategy_code,
            changed_fields=changed_fields,
            old_values=old_values,
            new_values=new_values,
        )
        return {"ok": True, "changed": True, "instance": _instance_mutation_public(instance, strategy_code)}


@router.post("/v2/instances/{instance_id}/pause")
def pause_v2_paper_instance_debug(
    instance_id: str,
    request: V2InstanceLifecycleRequest,
    user: CurrentUser = Depends(_require_internal_mutation_user),
) -> dict[str, Any]:
    return _v2_instance_lifecycle_transition(
        instance_id,
        StrategyInstanceStatus.PAUSED.value,
        "pause",
        "V2_INSTANCE_PAUSED",
        request,
        user,
    )


@router.post("/v2/instances/{instance_id}/archive")
def archive_v2_paper_instance_debug(
    instance_id: str,
    request: V2InstanceLifecycleRequest,
    user: CurrentUser = Depends(_require_internal_mutation_user),
) -> dict[str, Any]:
    return _v2_instance_archive_restore_transition(
        instance_id,
        StrategyInstanceStatus.ARCHIVED.value,
        audit_event="V2_INSTANCE_ARCHIVED",
        request=request,
        user=user,
    )


@router.post("/v2/instances/{instance_id}/restore")
def restore_v2_paper_instance_debug(
    instance_id: str,
    request: V2InstanceLifecycleRequest,
    user: CurrentUser = Depends(_require_internal_mutation_user),
) -> dict[str, Any]:
    return _v2_instance_archive_restore_transition(
        instance_id,
        StrategyInstanceStatus.PAUSED.value,
        audit_event="V2_INSTANCE_RESTORED",
        request=request,
        user=user,
    )


@router.post("/v2/instances/{instance_id}/resume")
def resume_v2_paper_instance_debug(
    instance_id: str,
    request: V2InstanceLifecycleRequest,
    user: CurrentUser = Depends(_require_internal_mutation_user),
) -> dict[str, Any]:
    return _v2_instance_lifecycle_transition(
        instance_id,
        StrategyInstanceStatus.ACTIVE.value,
        "resume",
        "V2_INSTANCE_RESUMED",
        request,
        user,
    )


def _risk_decision(signal: NormalizedSignal) -> dict[str, Any]:
    decision = evaluate_entry(signal) if signal.action == "ENTRY" else evaluate_exit(signal)
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "final_qty": decision.final_qty,
    }


def _last_dhan_response() -> dict[str, Any] | None:
    for row in reversed(read_jsonl("order", limit=500)):
        if row.get("event") in {"DHAN_ORDER_RESPONSE", "DHAN_ORDER_EXCEPTION"}:
            return sanitize_dhan_response_surface(row)
        if row.get("phase") == "after_response":
            return sanitize_dhan_response_surface(row)
    return None


def _prepare_order_response(raw_payload: dict[str, Any]) -> dict[str, Any]:
    try:
        signal = parse_webhook_payload(raw_payload)
    except UnsupportedPayloadFormatError as exc:
        return {"ok": False, "status": "UNSUPPORTED_PAYLOAD_FORMAT", "message": str(exc)}
    except PayloadParseError as exc:
        return {"ok": False, "status": getattr(exc, "status", "INVALID_PAYLOAD"), "message": str(exc)}

    signal_ok, signal_error = validate_signal(signal)
    risk = _risk_decision(signal)
    final_qty = risk["final_qty"] if risk["final_qty"] and risk["final_qty"] > 0 else signal.qty
    final_dhan_payload, security_id_resolution = _build_dhan_payload_and_resolution(signal, int(final_qty), signal.action)
    payload_validation = validate_dhan_payload(final_dhan_payload)

    warnings: list[str] = []
    expected_secret = get_webhook_secret()
    if not expected_secret or signal.secret != expected_secret:
        warnings.append("Payload parsed, but its webhook secret does not match the saved backend webhook secret.")
    if not signal_ok and signal_error:
        warnings.append(signal_error)
    if security_id_resolution.get("method") == "DEFAULT_ENV":
        warnings.append(DEFAULT_SECURITY_ID_WARNING)

    return {
        "ok": bool(signal_ok and payload_validation.get("ok")),
        "payload_format": signal.payload_format,
        "normalized_signal": _public_signal(signal),
        "signal_validation": {"ok": signal_ok, "error": signal_error},
        "risk_decision": risk,
        "security_id_resolution": security_id_resolution,
        "final_dhan_payload": final_dhan_payload,
        "payload_validation_result": payload_validation,
        "warnings": warnings,
    }


@router.get("/dhan/config")
def dhan_config() -> dict[str, Any]:
    outgoing_ip = get_outgoing_ip()
    env = validate_dhan_env()
    runtime_settings = get_runtime_settings()
    app_state = get_app_state()
    market_closed = not _market_is_open()

    warnings = list(env["warnings"])
    if market_closed:
        warnings.append("Market may be closed; order placement test may fail due to session.")

    last_response = _last_dhan_response()
    interpreted_error = None
    if isinstance(last_response, dict):
        interpreted_error = last_response.get("interpreted_error")

    return {
        "ok": bool(env["ok"] and outgoing_ip.get("ok")),
        "outgoing_ip": outgoing_ip.get("outgoing_ip"),
        "outgoing_ip_check": outgoing_ip,
        "dhan": {
            "mode": env["config"]["dhan_mode"],
            "live_orders_enabled": env["config"]["live_orders_enabled"],
            "client_id_present": env["config"]["client_id_present"],
            "access_token_present": env["config"]["access_token_present"],
            "access_token_masked": env["config"]["access_token_masked"],
            "public_webhook_url": env["config"]["public_webhook_url"],
            "market_closed_debug": env["config"]["market_closed_debug"],
            "force_allow_order_when_market_closed": env["config"]["force_allow_order_when_market_closed"],
            "allow_default_security_id": env["config"]["allow_default_security_id"],
            "default_security_id_present": env["config"]["default_security_id_present"],
            "dhan_scrip_master_path": env["config"]["dhan_scrip_master_path"],
            "auto_resolve_security_id": env["config"]["auto_resolve_security_id"],
        },
        "safety": {
            "emergency_stop": bool(runtime_settings.get("emergency_stop")),
            "global_kill_switch": bool(runtime_settings.get("global_kill_switch")),
            "require_market_hours": settings.REQUIRE_MARKET_HOURS,
            "market_is_open": not market_closed,
            "market_closed_debug": settings.MARKET_CLOSED_DEBUG,
            "force_allow_order_when_market_closed": settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED,
        },
        "runtime_state": {
            "app_state": sanitize_dhan_response_surface(app_state),
            "settings": runtime_settings,
        },
        "last_dhan_response": last_response,
        "last_dhan_status_code": last_response.get("status_code") if isinstance(last_response, dict) else None,
        "last_dhan_interpreted_error": interpreted_error,
        "issues": env["issues"],
        "warnings": warnings,
    }


@router.get("/dhan/security-id/resolve")
def resolve_security_id_debug(
    symbol: str = Query(...),
    expiry: str = Query(...),
    strike: float = Query(...),
    option_side: str = Query(...),
) -> dict[str, Any]:
    result = resolve_security_id_for_contract(
        symbol=symbol,
        expiry=expiry,
        strike=strike,
        option_side=option_side,
        exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
    ).model_dump()
    warnings = [DEFAULT_SECURITY_ID_WARNING] if result.get("method") == "DEFAULT_ENV" else []
    return {"ok": bool(result.get("ok")), "security_id_resolution": result, "warnings": warnings}


@router.post("/dhan/prepare-order")
def prepare_order(raw_payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _prepare_order_response(raw_payload)


@router.post("/dhan/live-order-dry-run")
def live_order_dry_run(raw_payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    result = _prepare_order_response(raw_payload)
    outgoing_ip = get_outgoing_ip()
    url = f"{DHAN_BASE_URL}/orders"
    creds = get_dhan_credentials()
    headers_masked = build_dhan_headers_debug(creds.client_id if creds else None, creds.access_token if creds else None)
    market_is_open = _market_is_open()
    would_send_real_order = bool(
        settings.DHAN_MODE.upper() == "REAL"
        and settings.ENABLE_LIVE_ORDERS
        and creds
        and (
            market_is_open
            or not settings.MARKET_CLOSED_DEBUG
            or settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED
        )
    )

    debug = {
        "outgoing_ip": outgoing_ip.get("outgoing_ip"),
        "outgoing_ip_check": outgoing_ip,
        "url": url,
        "headers_masked": headers_masked,
        "final_payload": result.get("final_dhan_payload"),
        "security_id_resolution": result.get("security_id_resolution"),
        "dhan_mode": settings.DHAN_MODE.upper(),
        "enable_live_orders": settings.ENABLE_LIVE_ORDERS,
        "market_is_open": market_is_open,
        "market_closed_debug": settings.MARKET_CLOSED_DEBUG,
        "force_allow_order_when_market_closed": settings.FORCE_ALLOW_ORDER_WHEN_MARKET_CLOSED,
        "would_send_real_order": would_send_real_order,
    }
    log_order_event(
        {
            "event": "DHAN_LIVE_ORDER_DRY_RUN",
            "outgoing_ip": debug["outgoing_ip"],
            "url": url,
            "headers_masked": headers_masked,
            "payload": result.get("final_dhan_payload"),
            "security_id_resolution": result.get("security_id_resolution"),
            "risk_decision": result.get("risk_decision"),
            "payload_validation_result": result.get("payload_validation_result"),
            "dhan_mode": settings.DHAN_MODE.upper(),
            "live_orders_enabled": settings.ENABLE_LIVE_ORDERS,
            "would_send_real_order": would_send_real_order,
        }
    )
    return {**result, "debug": debug}


@router.post("/dhan/ping-safe")
def ping_safe() -> dict[str, Any]:
    outgoing_ip = get_outgoing_ip()
    creds = get_dhan_credentials()
    header_status = {
        "client_id_present": bool(creds and creds.client_id),
        "access_token_present": bool(creds and creds.access_token),
    }

    if settings.DHAN_MODE.upper() != "REAL":
        return {
            "ok": True,
            "message": "MOCK Dhan mode is active; no read-only Dhan request was sent.",
            "endpoint": None,
            "header_status": header_status,
            "outgoing_ip": outgoing_ip.get("outgoing_ip"),
            "outgoing_ip_check": outgoing_ip,
        }

    if not creds:
        return {
            "ok": False,
            "message": "Dhan Client ID or Access Token missing.",
            "endpoint": None,
            "header_status": header_status,
            "outgoing_ip": outgoing_ip.get("outgoing_ip"),
            "outgoing_ip_check": outgoing_ip,
        }

    endpoint = f"{DHAN_BASE_URL}/profile"
    headers = {
        "client-id": creds.client_id,
        "access-token": creds.access_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.get(endpoint, headers=headers)
        try:
            parsed: Any = response.json()
        except ValueError:
            parsed = None
        interpreted_error = (
            None
            if 200 <= response.status_code <= 299
            else sanitize_dhan_response_surface(interpret_dhan_error(response.status_code, parsed or response.text))
        )
        masked_client_id = None
        if isinstance(parsed, dict):
            masked_client_id = sanitize_dhan_response_surface({"client_id": parsed.get("dhanClientId")}).get("client_id")
        return {
            "ok": 200 <= response.status_code <= 299,
            "status_code": response.status_code,
            "broker": "DHAN",
            "message": "Dhan profile check completed." if 200 <= response.status_code <= 299 else "Dhan profile check failed.",
            "masked_client_id": masked_client_id,
            "endpoint": endpoint,
            "header_status": header_status,
            "outgoing_ip": outgoing_ip.get("outgoing_ip"),
            "outgoing_ip_check": outgoing_ip,
            "safe_error": interpreted_error,
        }
    except Exception as exc:
        interpreted_error = sanitize_dhan_response_surface(interpret_dhan_error(None, type(exc).__name__))
        return {
            "ok": False,
            "status_code": None,
            "broker": "DHAN",
            "message": "Dhan profile check failed.",
            "endpoint": endpoint,
            "header_status": header_status,
            "outgoing_ip": outgoing_ip.get("outgoing_ip"),
            "outgoing_ip_check": outgoing_ip,
            "safe_error": interpreted_error,
            "error_type": type(exc).__name__,
        }
