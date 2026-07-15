"""Private strategy-instance webhook ingestion (Phase 3A).

One opaque credential in the JSON body identifies exactly one StrategyInstance;
everything else (owner, mode, lots, contract) is derived from trusted server
state. The HTTP path only authenticates, validates, and persists a durable
StrategySignal + StrategyExecutionJob — execution happens asynchronously in
the existing durable job worker.

Durable idempotency identity: (strategy_instance_id, signal_id), enforced by
the existing unique constraints via the instance-scoped strategy name
``instance:{instance_id}`` on strategy_signals / strategy_execution_jobs and
the instance-scoped provider on webhook_events.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import DEFAULT_EXCHANGE_SEGMENT, settings
from app.db import crud, models
from app.db.engine import database_configured, session_scope
from app.schemas.signal import NormalizedSignal
from app.services import webhook_replay_store
from app.services.audit_logger import log_audit_event
from app.domain.strategy_instance_state_machine import InstanceState

PRIVATE_STRATEGY_PREFIX = "instance:"
PRIVATE_WEBHOOK_SOURCE = "PRIVATE_TRADINGVIEW_WEBHOOK"

# Exactly the four normalized actions. Case normalization only — no free-form
# Pine vocabulary (LONG/SHORT/CALL/PUT/...) is interpreted here.
NORMALIZED_ACTIONS = {"BUY_CE", "BUY_PE", "EXIT", "HOLD"}


class PrivateWebhookError(Exception):
    """Ingestion failure with a safe public message and HTTP status."""

    def __init__(self, message: str, *, status_code: int, reason: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class PrivateWebhookPayload(BaseModel):
    """Strict wire contract. extra="forbid" rejects every unknown field, which
    includes all privileged fields (user_id, lots, quantity, strike, expiry,
    security_id, mode, event_source, ...) — server state is the only authority
    for those values."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=20)
    signal_id: str = Field(min_length=1, max_length=128)
    signal_time: datetime
    # Optional allow-listed audit metadata. Never affects execution authority.
    strategy_version: str | None = Field(default=None, max_length=40)
    timeframe: str | None = Field(default=None, max_length=12)
    reference_price: float | None = Field(default=None, gt=0)
    comment: str | None = Field(default=None, max_length=200)


class PrivateWebhookRequest(PrivateWebhookPayload):
    """Ingress-only envelope; the credential cannot survive serialization."""

    credential: SecretStr = Field(min_length=1, max_length=128, exclude=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_credential(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def instance_strategy_name(instance_id: uuid.UUID | str) -> str:
    return f"{PRIVATE_STRATEGY_PREFIX}{instance_id}"


def normalize_action(raw: str) -> str | None:
    action = (raw or "").strip().upper()
    return action if action in NORMALIZED_ACTIONS else None


def parse_payload(data: dict[str, Any]) -> PrivateWebhookPayload:
    try:
        return PrivateWebhookPayload.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc") or ())
        raise PrivateWebhookError(
            f"Invalid payload: {location}: {first.get('msg')}",
            status_code=422,
            reason="INVALID_PAYLOAD",
        ) from None


def parse_request(data: dict[str, Any]) -> tuple[str, PrivateWebhookPayload]:
    try:
        request = PrivateWebhookRequest.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc") or ())
        raise PrivateWebhookError(
            f"Invalid payload: {location}: {first.get('msg')}",
            status_code=422,
            reason="INVALID_PAYLOAD",
        ) from None
    return request.credential.get_secret_value(), PrivateWebhookPayload.model_validate(
        request.model_dump()
    )


def validate_signal_time(signal_time: datetime, *, received_at: datetime) -> None:
    if signal_time.tzinfo is None:
        raise PrivateWebhookError(
            "signal_time must include a timezone offset.",
            status_code=422,
            reason="MISSING_TIMEZONE",
        )
    age_seconds = (received_at - signal_time).total_seconds()
    if age_seconds > max(int(settings.PRIVATE_WEBHOOK_MAX_AGE_SECONDS), 1):
        raise PrivateWebhookError(
            "Signal is too old.", status_code=422, reason="STALE_SIGNAL"
        )
    if -age_seconds > max(int(settings.PRIVATE_WEBHOOK_MAX_FUTURE_SKEW_SECONDS), 1):
        raise PrivateWebhookError(
            "Signal timestamp is in the future.", status_code=422, reason="STALE_SIGNAL"
        )


def payload_fingerprint(payload: PrivateWebhookPayload, action: str) -> str:
    """Deterministic canonical identity of one normalized alert. Identical
    retries fingerprint identically; a changed action/time/metadata does not."""
    canonical = json.dumps(
        {
            "action": action,
            "signal_id": payload.signal_id,
            "signal_time": payload.signal_time.astimezone(timezone.utc).isoformat(),
            "strategy_version": payload.strategy_version,
            "timeframe": payload.timeframe,
            "reference_price": payload.reference_price,
            "comment": payload.comment,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Credential authentication
# ---------------------------------------------------------------------------

def authenticate_credential(token: str, *, correlation_id: str | None = None) -> dict[str, Any]:
    """Resolve credential -> instance -> owner from trusted server state.

    Raises PrivateWebhookError with a uniform 401 for unknown AND revoked
    credentials so callers cannot probe which instances exist. The audit
    trail records only a safe correlation ID.
    """
    correlation_id = correlation_id or uuid.uuid4().hex
    if not database_configured():
        raise PrivateWebhookError(
            "Webhook store unavailable.", status_code=503, reason="STORE_UNAVAILABLE"
        )
    token = (token or "").strip()
    if not token or len(token) > 128:
        _audit_credential_failure(correlation_id)
        raise _invalid_credential()
    token_hash = hash_credential(token)
    with session_scope() as db:
        credential = db.scalar(
            select(models.StrategyInstanceWebhookCredential).where(
                models.StrategyInstanceWebhookCredential.token_hash == token_hash
            )
        )
        if credential is None:
            _audit_credential_failure(correlation_id)
            raise _invalid_credential()
        if credential.revoked_at is not None:
            _audit_credential_failure(correlation_id)
            raise _invalid_credential()
        instance = db.get(models.StrategyInstance, credential.strategy_instance_id)
        if instance is None:
            _audit_credential_failure(correlation_id)
            raise _invalid_credential()
        owner = crud.get_user_by_id(db, instance.user_id)
        if owner is None:
            _audit_credential_failure(correlation_id)
            raise _invalid_credential()
        credential.last_used_at = _now()
        return {
            "credential_id": str(credential.id),
            "correlation_id": correlation_id,
            "instance_id": str(instance.id),
            "user_id": str(owner.id),
            "instance_status": instance.status,
            "source_journey": instance.source_journey,
            "execution_mode": instance.execution_mode,
            "verification_mode": bool(instance.verification_mode),
            "current_lots": int(instance.current_lots),
            "strategy_id": str(instance.strategy_id),
        }


def _invalid_credential() -> PrivateWebhookError:
    # Identical message/status for unknown and revoked: no tenant probing.
    return PrivateWebhookError(
        "Invalid webhook credential.", status_code=401, reason="INVALID_CREDENTIAL"
    )


def _audit_credential_failure(correlation_id: str | None) -> None:
    log_audit_event(
        "private_webhook_auth_failed",
        "private_webhook_auth_failed",
        severity="WARNING",
        metadata={"correlation_id": correlation_id},
    )


def validate_instance_lifecycle(auth: dict[str, Any]) -> None:
    if auth["source_journey"] != "PERSONAL_TRADINGVIEW":
        raise PrivateWebhookError(
            "This strategy instance does not accept private webhooks.",
            status_code=409,
            reason="INACTIVE_INSTANCE",
        )
    in_verification = bool(auth.get("verification_mode"))
    if auth["instance_status"] not in {InstanceState.ACTIVE.value, InstanceState.PAUSED.value} and not in_verification:
        # A strategy in controlled verification is not yet ACTIVE but must accept
        # genuine paper signals to produce entry/exit evidence.
        raise PrivateWebhookError(
            "Strategy instance is stopped or not active.",
            status_code=409,
            reason="INACTIVE_INSTANCE",
        )
    if in_verification and auth.get("execution_mode") != "paper_live_data":
        # Verification is paper-only — a live instance can never ingest here.
        raise PrivateWebhookError(
            "Verification accepts paper strategies only.",
            status_code=409,
            reason="LIVE_EXECUTION_SAFETY_BLOCK",
        )


# ---------------------------------------------------------------------------
# Normalized signal construction (server-authoritative fields only)
# ---------------------------------------------------------------------------

_ACTION_MAP = {
    "BUY_CE": {"action": "ENTRY", "side": "BUY", "option_side": "CE"},
    "BUY_PE": {"action": "ENTRY", "side": "BUY", "option_side": "PE"},
    "EXIT": {"action": "EXIT", "side": "SELL", "option_side": None},
}


def build_normalized_signal(
    auth: dict[str, Any],
    payload: PrivateWebhookPayload,
    action: str,
    *,
    received_at: datetime,
) -> NormalizedSignal:
    mapping = _ACTION_MAP[action]
    # Namespaced so per-user order-intent/correlation keys can never collide
    # across two instances that reuse the same Pine signal_id.
    routed_signal_id = f"PWH-{auth['instance_id']}-{payload.signal_id}"
    raw_payload = {
        "private_instance_webhook": True,
        "strategy_instance_id": auth["instance_id"],
        "normalized_action": action,
        "signal_id": payload.signal_id,
        "signal_time": payload.signal_time.astimezone(timezone.utc).isoformat(),
        "received_at": received_at.isoformat(),
        "strategy_version": payload.strategy_version,
        "timeframe": payload.timeframe,
        "reference_price": payload.reference_price,
        "comment": payload.comment,
    }
    return NormalizedSignal(
        payload_format="NOVA",
        secret="",
        signal_id=routed_signal_id,
        strategy_code=instance_strategy_name(auth["instance_id"]),
        action=mapping["action"],
        side=mapping["side"],
        symbol="NIFTY",
        instrument_type="OPTIDX",
        exchange_segment=DEFAULT_EXCHANGE_SEGMENT,
        option_side=mapping["option_side"],
        # Contract + quantity are resolved server-side at execution time.
        security_id=None,
        strike=None,
        expiry=None,
        qty=1,  # placeholder; worker overrides with engine lots x lot size
        order_type="MARKET",
        product_type="INTRADAY",
        source=PRIVATE_WEBHOOK_SOURCE.lower(),
        raw_payload=raw_payload,
    )


# ---------------------------------------------------------------------------
# Durable ingestion
# ---------------------------------------------------------------------------

def ingest(auth: dict[str, Any], payload: PrivateWebhookPayload) -> dict[str, Any]:
    """Claim + persist one private alert. Returns the safe HTTP response body."""
    received_at = _now()
    action = normalize_action(payload.action)
    if action is None:
        _audit_signal(auth, payload.signal_id, "rejected", "INVALID_ACTION")
        raise PrivateWebhookError(
            "Unsupported action.", status_code=422, reason="INVALID_ACTION"
        )
    validate_signal_time(payload.signal_time, received_at=received_at)

    strategy_name = instance_strategy_name(auth["instance_id"])
    fingerprint = payload_fingerprint(payload, action)
    try:
        claim = webhook_replay_store.claim_webhook_event(
            provider=f"instance-webhook:{auth['instance_id']}",
            event_id=payload.signal_id,
            raw_body=fingerprint,
            user_id=auth["user_id"],
            signature_ok=True,
            metadata={
                "source": PRIVATE_WEBHOOK_SOURCE,
                "credential_id": auth["credential_id"],
                "correlation_id": auth.get("correlation_id"),
                "strategy_instance_id": auth["instance_id"],
                "action": action,
                "signal_time": payload.signal_time.astimezone(timezone.utc).isoformat(),
                "received_at": received_at.isoformat(),
                "payload_fingerprint": fingerprint,
            },
        )
    except Exception as exc:
        raise PrivateWebhookError(
            "Webhook store unavailable.", status_code=503, reason="STORE_UNAVAILABLE"
        ) from exc

    status = claim.get("status")
    if status == "tampered":
        _audit_signal(auth, payload.signal_id, "rejected", "CONFLICTING_DUPLICATE")
        raise PrivateWebhookError(
            "Duplicate signal_id with a different payload.",
            status_code=409,
            reason="CONFLICTING_DUPLICATE",
        )
    if status == "duplicate":
        existing = _existing_status_response(strategy_name, payload.signal_id)
        if existing is not None:
            return existing
        # The event claim committed but the signal/job commit crashed before
        # completing on the earlier delivery. The strategy_signals unique
        # constraint below remains the durable guard, so persist now.
    elif status != "fresh":
        raise PrivateWebhookError(
            "Webhook store unavailable.", status_code=503, reason="STORE_UNAVAILABLE"
        )

    if action == "HOLD":
        result = _persist_hold(auth, payload, strategy_name, received_at)
    else:
        result = _persist_execution_job(auth, payload, action, strategy_name, received_at)
    _audit_signal(auth, payload.signal_id, result["status"], action)
    return result


def test_connection(user_id: uuid.UUID, instance_id: uuid.UUID | str) -> dict[str, Any]:
    """Owner-authenticated HOLD through the durable webhook ingestion path."""
    from app.services.strategy_instance_service import _active_credential, _owned_instance

    if not settings.PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED:
        raise PrivateWebhookError(
            "Private strategy webhooks are disabled.", status_code=403, reason="DISABLED"
        )
    with session_scope() as db:
        instance = _owned_instance(db, user_id, instance_id)
        if instance.source_journey != "PERSONAL_TRADINGVIEW":
            raise PrivateWebhookError(
                "This strategy does not accept private webhooks.",
                status_code=409,
                reason="INACTIVE_INSTANCE",
            )
        if instance.execution_mode == "real_orders":
            raise PrivateWebhookError(
                "Connection tests are available only in paper mode.",
                status_code=409,
                reason="LIVE_MODE_UNAVAILABLE",
            )
        credential = _active_credential(db, instance.id)
        if credential is None:
            raise PrivateWebhookError(
                "Generate a webhook credential first.",
                status_code=409,
                reason="INVALID_CREDENTIAL",
            )
        auth = {
            "credential_id": str(credential.id),
            "correlation_id": uuid.uuid4().hex,
            "instance_id": str(instance.id),
            "user_id": str(instance.user_id),
            "instance_status": instance.status,
            "source_journey": instance.source_journey,
            "execution_mode": instance.execution_mode,
            "verification_mode": bool(instance.verification_mode),
            "current_lots": instance.current_lots,
            "strategy_id": str(instance.strategy_id),
        }
    return ingest(
        auth,
        PrivateWebhookPayload(
            action="HOLD",
            signal_id=f"connection-test-{uuid.uuid4().hex}",
            signal_time=_now(),
            comment="NOVA paper connection test",
        ),
    )


def _persist_hold(
    auth: dict[str, Any],
    payload: PrivateWebhookPayload,
    strategy_name: str,
    received_at: datetime,
) -> dict[str, Any]:
    """HOLD is audit-only: durable signal record, no execution job, no broker."""
    summary = {
        "status": "NO_OP",
        "reason": "HOLD",
        "action": "HOLD",
        "signal_time": payload.signal_time.astimezone(timezone.utc).isoformat(),
        "received_at": received_at.isoformat(),
    }
    try:
        with session_scope() as db:
            db.add(
                models.StrategySignal(
                    strategy_name=strategy_name,
                    signal_id=payload.signal_id,
                    status="completed",
                    result_summary=summary,
                )
            )
            db.flush()
    except IntegrityError:
        return _existing_status_response(strategy_name, payload.signal_id) or {
            "ok": True,
            "duplicate": True,
            "signal_id": payload.signal_id,
            "status": "accepted",
        }
    return {
        "ok": True,
        "signal_id": payload.signal_id,
        "status": "NO_OP",
        "reason": "HOLD",
    }


def _persist_execution_job(
    auth: dict[str, Any],
    payload: PrivateWebhookPayload,
    action: str,
    strategy_name: str,
    received_at: datetime,
) -> dict[str, Any]:
    signal = build_normalized_signal(auth, payload, action, received_at=received_at)
    try:
        with session_scope() as db:
            signal_row = models.StrategySignal(
                strategy_name=strategy_name,
                signal_id=payload.signal_id,
                status="queued",
                result_summary={
                    "action": action,
                    "signal_time": payload.signal_time.astimezone(timezone.utc).isoformat(),
                    "received_at": received_at.isoformat(),
                },
            )
            db.add(signal_row)
            db.flush()
            db.add(
                models.StrategyExecutionJob(
                    strategy_signal_id=signal_row.id,
                    user_id=uuid.UUID(auth["user_id"]),
                    strategy_name=strategy_name,
                    signal_id=payload.signal_id,
                    signal_payload=signal.model_dump(mode="json"),
                    # Snapshot only — the worker re-reads instance lots/mode at
                    # claim time (revalidation), these are audit values.
                    lots=auth["current_lots"],
                    execution_mode=auth["execution_mode"],
                    status="queued",
                )
            )
            db.flush()
    except IntegrityError:
        # Concurrent identical delivery lost the race after the event claim.
        return _existing_status_response(strategy_name, payload.signal_id) or {
            "ok": True,
            "duplicate": True,
            "signal_id": payload.signal_id,
            "status": "accepted",
        }

    from app.workers.strategy_job_worker import wake_strategy_job_worker

    wake_strategy_job_worker()
    return {
        "ok": True,
        "signal_id": payload.signal_id,
        "status": "QUEUED",
        "action": action,
    }


def _existing_status_response(strategy_name: str, signal_id: str) -> dict[str, Any] | None:
    """Identical duplicate: return the existing signal/job status. Never a new
    job, never another order. None when no signal row exists yet (a crashed
    earlier delivery) so the caller can persist."""
    with session_scope() as db:
        signal_row = db.scalar(
            select(models.StrategySignal).where(
                models.StrategySignal.strategy_name == strategy_name,
                models.StrategySignal.signal_id == signal_id,
            )
        )
        if signal_row is None:
            return None
        job_row = db.scalar(
            select(models.StrategyExecutionJob).where(
                models.StrategyExecutionJob.strategy_name == strategy_name,
                models.StrategyExecutionJob.signal_id == signal_id,
            )
        )
        return {
            "ok": True,
            "duplicate": True,
            "signal_id": signal_id,
            "status": signal_row.status,
            "job_status": job_row.status if job_row else None,
        }


def _audit_signal(auth: dict[str, Any], signal_id: str, status: str, detail: str) -> None:
    try:
        with session_scope() as db:
            crud.add_audit_log(
                db,
                user_id=uuid.UUID(auth["user_id"]),
                action="PRIVATE_WEBHOOK_SIGNAL",
                metadata={
                    "source": PRIVATE_WEBHOOK_SOURCE,
                    "credential_id": auth["credential_id"],
                    "correlation_id": auth.get("correlation_id"),
                    "strategy_instance_id": auth["instance_id"],
                    "signal_id": signal_id,
                    "status": status,
                    "detail": detail,
                },
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Owner-scoped execution history (user-facing status API)
# ---------------------------------------------------------------------------

_SAFE_RESULT_KEYS = ("status", "reason", "no_op", "block_code", "order_id")


def _safe_job_result(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    safe = {key: summary.get(key) for key in _SAFE_RESULT_KEYS if key in summary}
    execution = summary.get("execution_result")
    if isinstance(execution, dict):
        safe["execution_status"] = execution.get("status")
        safe["execution_reason"] = execution.get("reason")
        safe["order_id"] = execution.get("order_id") or safe.get("order_id")
        contract = {
            "trading_symbol": execution.get("trading_symbol")
            or execution.get("normalized_symbol"),
            "option_side": execution.get("normalized_option_side"),
            "strike": execution.get("normalized_strike"),
            "expiry": execution.get("normalized_expiry"),
            "qty": execution.get("normalized_qty"),
        }
        if any(value is not None for value in contract.values()):
            safe["contract"] = contract
    return safe or None


def list_webhook_executions(
    user_id: uuid.UUID,
    instance_id: uuid.UUID | str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Owner-scoped, paginated execution history for one instance."""
    from app.services.strategy_instance_service import _owned_instance

    limit = max(1, min(int(limit), 200))
    offset = max(int(offset), 0)
    with session_scope() as db:
        instance = _owned_instance(db, user_id, instance_id)  # 404s on foreign tenant
        strategy_name = instance_strategy_name(instance.id)
        signals = db.scalars(
            select(models.StrategySignal)
            .where(models.StrategySignal.strategy_name == strategy_name)
            .order_by(models.StrategySignal.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        jobs_by_signal: dict[str, models.StrategyExecutionJob] = {}
        if signals:
            jobs = db.scalars(
                select(models.StrategyExecutionJob).where(
                    models.StrategyExecutionJob.strategy_name == strategy_name,
                    models.StrategyExecutionJob.signal_id.in_(
                        [row.signal_id for row in signals]
                    ),
                )
            ).all()
            jobs_by_signal = {job.signal_id: job for job in jobs}
        executions = []
        for row in signals:
            job = jobs_by_signal.get(row.signal_id)
            summary = row.result_summary if isinstance(row.result_summary, dict) else {}
            # The worker overwrites the signal's result_summary on completion;
            # the immutable job payload keeps the normalized alert fields.
            job_raw: dict[str, Any] = {}
            if job is not None and isinstance(job.signal_payload, dict):
                raw = job.signal_payload.get("raw_payload")
                job_raw = raw if isinstance(raw, dict) else {}
            executions.append(
                {
                    "signal_id": row.signal_id,
                    "strategy_instance_id": str(instance.id),
                    "action": summary.get("action") or job_raw.get("normalized_action"),
                    "signal_time": summary.get("signal_time") or job_raw.get("signal_time"),
                    "received_at": (
                        summary.get("received_at")
                        or job_raw.get("received_at")
                        or (row.created_at.isoformat() if row.created_at else None)
                    ),
                    "status": row.status,
                    "reason": summary.get("reason"),
                    "execution_mode": job.execution_mode if job else None,
                    "job_status": job.status if job else None,
                    "job_created_at": (
                        job.created_at.isoformat() if job and job.created_at else None
                    ),
                    "job_completed_at": (
                        job.completed_at.isoformat() if job and job.completed_at else None
                    ),
                    "result": _safe_job_result(job.result_summary if job else None),
                }
            )
        return {
            "instance_id": str(instance.id),
            "limit": limit,
            "offset": offset,
            "executions": executions,
        }
