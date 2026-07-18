"""Insert-only writer for strategy_signal_rejections (R1B-2A).

EVIDENCE ONLY — NOT EXECUTION AUTHORITY. Persists safe, post-authentication
ingress rejection evidence for one trusted, owner-resolved StrategyInstance.
This library explicitly assumes the (future) caller has already resolved a
valid credential to an instance and owner: it must never be handed
unauthenticated material, and it never stores credential plaintext, raw
bodies, hostile payloads, emails, exception text or Pine source.

R1B-2A boundary: ZERO production call sites. Rejection persistence is
best-effort evidence only — it can never convert a rejection into acceptance
or vice versa, because it does not participate in any HTTP response.

Taint policy: a credential-shaped or request-secret-matching free-text input
suppresses the entire row with closed code TAINT_DETECTED; the unsafe value
never appears in the row, the error, logs or metrics.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config import settings
from app.db import models
from app.domain.canonical_signal import CanonicalContractVersion
from app.domain.legacy_signal_adapter import LEGACY_ADAPTER_VERSION
from app.domain.secret_taint import is_credential_shaped, project_safe_signal_id
from app.services.r1b_evidence_safety import (
    IntegrityError,
    SignalRejectionPersistenceError,
    SQLAlchemyError,
    evidence_key,
    require_aware_utc,
    require_closed,
    require_hex64,
    sha256_text,
    translate_db_error,
)

REJECTION_WRITER_VERSION = "nova.signal-rejection-writer.v1"
REJECTION_KEY_NAMESPACE = "nova.r1b2.rejection.v1"

# Exactly the 0015 CHECK vocabulary.
REJECTION_STAGES = frozenset(
    {
        "LIFECYCLE",
        "SIGNAL_TIME",
        "REPLAY_CONFLICT",
        "CANONICAL_NORMALIZATION",
        "SEMANTIC_POLICY",
        "JOB_CREATION",
    }
)
REJECTION_CODES = frozenset(
    {
        "INVALID_ACTION",
        "MISSING_TIMEZONE",
        "STALE_SIGNAL",
        "INACTIVE_INSTANCE",
        "LIVE_EXECUTION_SAFETY_BLOCK",
        "CONFLICTING_DUPLICATE",
        "STORE_UNAVAILABLE",
        "JOB_PERSISTENCE_FAILED",
    }
)
# Closed safe-detail vocabulary: the rejection codes themselves plus the
# R1B-0 generic value. No free text ever lands in safe_detail.
REJECTION_SAFE_DETAILS = REJECTION_CODES | {"canonical-evidence-unavailable"}
_ALLOWED_ADAPTER_VERSIONS = frozenset({LEGACY_ADAPTER_VERSION})
_ALLOWED_CONTRACT_VERSIONS = frozenset({version.value for version in CanonicalContractVersion})


def _error(code: str) -> SignalRejectionPersistenceError:
    return SignalRejectionPersistenceError(code)


def rejection_dedupe_key(
    *,
    strategy_instance_id,
    stage: str,
    rejection_code: str,
    identity: str | None,
    received_at_utc,
) -> str:
    """Deterministic internal dedupe key with a UTC daily bucket, so identical
    invalid retries collapse to one row and no-signal-ID cases stay bounded
    per instance/stage/code/day."""
    return evidence_key(
        REJECTION_KEY_NAMESPACE,
        (
            strategy_instance_id,
            stage,
            rejection_code,
            identity,
            received_at_utc.date().isoformat(),
        ),
    )


def persist_strategy_signal_rejection(
    session,
    *,
    strategy_instance: models.StrategyInstance,
    owner_user_id: uuid.UUID,
    stage: str,
    rejection_code: str,
    received_at,
    signal_id: str | None = None,
    webhook_event: models.WebhookEvent | None = None,
    request_fingerprint: str | None = None,
    safe_detail: str | None = None,
    adapter_version: str | None = None,
    contract_version: str | None = None,
    known_secrets: tuple[str, ...] = (),
) -> models.StrategySignalRejection:
    """Insert or return one deduplicated post-authentication rejection row."""
    if not settings.R1B_SIGNAL_REJECTION_PERSISTENCE:
        raise _error("PERSISTENCE_DISABLED")

    # ---- trusted-input validation (no DB access) --------------------------
    if not isinstance(strategy_instance, models.StrategyInstance) or strategy_instance.id is None:
        raise _error("INVALID_INPUT")
    if strategy_instance.user_id != owner_user_id:
        raise _error("OWNER_INSTANCE_MISMATCH")
    require_closed(stage, REJECTION_STAGES, _error("INVALID_INPUT"))
    require_closed(rejection_code, REJECTION_CODES, _error("INVALID_INPUT"))
    received_at_utc = require_aware_utc(received_at, _error("INVALID_INPUT"))
    if safe_detail is not None:
        require_closed(safe_detail, REJECTION_SAFE_DETAILS, _error("INVALID_INPUT"))
    if adapter_version is not None:
        require_closed(adapter_version, _ALLOWED_ADAPTER_VERSIONS, _error("INVALID_INPUT"))
    if contract_version is not None:
        require_closed(contract_version, _ALLOWED_CONTRACT_VERSIONS, _error("INVALID_INPUT"))
    if request_fingerprint is not None:
        require_hex64(request_fingerprint, _error("INVALID_INPUT"))
    if webhook_event is not None:
        if not isinstance(webhook_event, models.WebhookEvent) or webhook_event.id is None:
            raise _error("INVALID_INPUT")
        if webhook_event.provider != f"instance-webhook:{strategy_instance.id}":
            raise _error("FOREIGN_REFERENCE")

    # Taint: a credential-shaped signal_id suppresses the whole row — the
    # value is never projected, hashed, stored, logged or echoed.
    signal_id_safe: str | None = None
    signal_id_sha256: str | None = None
    if signal_id is not None:
        if not isinstance(signal_id, str):
            raise _error("INVALID_INPUT")
        if is_credential_shaped(signal_id, known_secrets):
            raise _error("TAINT_DETECTED")
        projected = project_safe_signal_id(signal_id, known_secrets)
        if projected == "invalid-signal-id":
            # Malformed but not secret: keep only the closed projection.
            signal_id_safe = projected
        else:
            signal_id_safe = projected
            signal_id_sha256 = sha256_text(signal_id)

    # Conflicting duplicates dedupe per conflicting payload fingerprint (one
    # row per distinct conflicting payload); everything else prefers the
    # signal hash.
    if rejection_code == "CONFLICTING_DUPLICATE" and request_fingerprint is not None:
        identity: str | None = request_fingerprint
    else:
        identity = (
            signal_id_sha256
            or request_fingerprint
            or (str(webhook_event.id) if webhook_event is not None else None)
        )
    dedupe_key = rejection_dedupe_key(
        strategy_instance_id=strategy_instance.id,
        stage=stage,
        rejection_code=rejection_code,
        identity=identity,
        received_at_utc=received_at_utc,
    )
    candidate = models.StrategySignalRejection(
        user_id=owner_user_id,
        strategy_instance_id=strategy_instance.id,
        webhook_event_id=webhook_event.id if webhook_event is not None else None,
        signal_id_safe=signal_id_safe,
        signal_id_sha256=signal_id_sha256,
        payload_fingerprint=request_fingerprint,
        stage=stage,
        rejection_code=rejection_code,
        safe_detail=safe_detail,
        adapter_version=adapter_version,
        contract_version=contract_version,
        dedupe_key=dedupe_key,
        received_at=received_at_utc,
    )

    # ---- closed database envelope ----------------------------------------
    try:
        existing = session.scalar(
            select(models.StrategySignalRejection).where(
                models.StrategySignalRejection.dedupe_key == dedupe_key
            )
        )
        if existing is not None:
            return existing
        try:
            with session.begin_nested():
                session.add(candidate)
        except IntegrityError:
            existing = session.scalar(
                select(models.StrategySignalRejection).where(
                    models.StrategySignalRejection.dedupe_key == dedupe_key
                )
            )
            if existing is None:
                raise _error("PERSISTENCE_UNAVAILABLE") from None
            return existing
        return candidate
    except SignalRejectionPersistenceError:
        raise
    except SQLAlchemyError as exc:
        raise translate_db_error(exc, _error("PERSISTENCE_UNAVAILABLE")) from None
