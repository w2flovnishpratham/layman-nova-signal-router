"""Owner-scoped manual and durable opt-in AI Pine conversion workflow."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.config import settings
from app.db import crud, models
from app.db.engine import session_scope
from app.schemas.pine_conversion import ConversionOptions
from app.services import personal_pine_service as pine, pine_conversion_provider, pine_validation

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "pine_conversion_v1.txt"
ACTIVE = {"queued", "processing"}
TERMINAL = {"succeeded", "validation_failed", "provider_failed", "rejected_secret_detected", "canceled", "rejected", "accepted"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(r"\b(?:ghp|github_pat|sk|AKIA)[_-]?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:cookie|session)[_-]?(?:id|token)?\s*[:=]\s*[\"']?[A-Za-z0-9._-]{16,}", re.I),
)


class ConversionError(ValueError):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json_hash(value: Any) -> str:
    return _hash(json.dumps(value, sort_keys=True, separators=(",", ":")))


def contains_secret(source: str) -> bool:
    return pine_validation.contains_credential_like_text(source) or any(pattern.search(source) for pattern in SECRET_PATTERNS)


def _owned_request(db, user_id: uuid.UUID, request_id, *, lock: bool = False):
    query = select(models.PineConversionRequest).where(
        models.PineConversionRequest.id == uuid.UUID(str(request_id)),
        models.PineConversionRequest.owner_user_id == user_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ConversionError("Pine conversion not found.", 404, "NOT_FOUND")
    return row


def _public(row: models.PineConversionRequest) -> dict[str, Any]:
    return {
        "id": str(row.id), "strategy_id": str(row.strategy_id), "input_version_id": str(row.input_version_id),
        "input_source_sha256": row.input_source_sha256, "contract_version": row.contract_version,
        "prompt_version": row.prompt_version, "provider": row.provider, "model": row.model,
        "options": row.options, "attempt": row.attempt, "consent_at": row.consent_at.isoformat(),
        "status": row.status, "provider_request_id": row.provider_request_id,
        "candidate_version_id": str(row.candidate_version_id) if row.candidate_version_id else None,
        "validation_report_id": str(row.validation_report_id) if row.validation_report_id else None,
        "safe_error_code": row.safe_error_code, "usage_summary": row.usage_summary,
        "estimated_cost_micros": row.estimated_cost_micros, "conversion_summary": row.conversion_summary,
        "assumptions": row.assumptions or [], "unsupported_features": row.unsupported_features or [],
        "warnings": row.warnings or [], "action_mapping": row.action_mapping or {},
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def public_config() -> dict[str, Any]:
    return {
        "manual_package_enabled": settings.PINE_CONVERSION_MANUAL_PACKAGE_ENABLED,
        "ai_enabled": settings.PINE_CONVERSION_AI_ENABLED,
        "provider": settings.PINE_CONVERSION_PROVIDER if settings.PINE_CONVERSION_AI_ENABLED else None,
        "model": settings.PINE_CONVERSION_MODEL if settings.PINE_CONVERSION_AI_ENABLED else None,
        "prompt_version": settings.PINE_CONVERSION_PROMPT_VERSION,
        "contract_version": pine.CONTRACT_VERSION,
        "daily_limit": settings.PINE_CONVERSION_MAX_DAILY_REQUESTS_PER_USER,
    }


def manual_package(user_id: uuid.UUID, strategy_id, version_id) -> dict[str, Any]:
    if not settings.PINE_CONVERSION_MANUAL_PACKAGE_ENABLED:
        raise ConversionError("Manual conversion packages are disabled.", 404, "FEATURE_DISABLED")
    source = pine.get_source(user_id, strategy_id, version_id)
    package = f"""# NOVA Pine Contract v1 conversion package

Privacy warning: copying this package to an external assistant shares your Pine source with that service. Review that service's privacy terms before pasting private strategy code.

## Task
Convert the untrusted Pine source below to Pine v6 compatible with NOVA_PINE_CONTRACT_VERSION=1. Do not execute, compile, backtest, approve, or make profitability claims.

## Mandatory contract
- Emit only BUY_CE, BUY_PE, EXIT, and optional HOLD from alert()/alertcondition().
- Preserve compatible logic; document ambiguous long/short mappings and every meaningful removal.
- NIFTY intraday only; one current position; no pyramiding, scale-in, martingale, portfolio, strike, or expiry execution logic.
- EXIT coverage is mandatory. Prefer bar-close confirmation and disclose repainting risk.
- Never emit credentials or server-controlled owner, instance, broker, mode, lots, quantity, strike, expiry, security, symbol, order, or product fields.
- Return Pine source plus a summary, assumptions, unsupported features, warnings, and action mapping.

## Untrusted source
BEGIN_UNTRUSTED_PINE_SOURCE
{source['source']}
END_UNTRUSTED_PINE_SOURCE

## Final checklist
Pine v6; one declaration; supported actions only; EXIT present; no secrets; no server-authority fields; NIFTY/intraday/one-position; repainting disclosed; no execution or profitability claim.
"""
    return {"package": package, "filename": "nova-pine-conversion-package.txt", "package_sha256": _hash(package), "source_sha256": source["source_sha256"]}


def create_request(user_id: uuid.UUID, strategy_id, version_id, options: ConversionOptions) -> dict[str, Any]:
    if not settings.PINE_CONVERSION_AI_ENABLED:
        raise ConversionError("AI Pine conversion is disabled. Use the manual conversion package.", 404, "AI_DISABLED")
    pine_conversion_provider.validate_provider_configuration()
    options_dict = options.model_dump(mode="json")
    options_hash = _json_hash(options_dict)
    with session_scope() as db:
        db.scalar(select(models.User).where(models.User.id == user_id).with_for_update())
        strategy, version = pine._owned_version(db, user_id, strategy_id, version_id)
        artifact = pine._artifact(db, version.id)
        if len(artifact.content.encode()) > settings.PINE_CONVERSION_MAX_SOURCE_BYTES:
            raise ConversionError("Source exceeds the AI conversion size limit.", 413, "SOURCE_TOO_LARGE")
        if contains_secret(artifact.content):
            raise ConversionError("Credential-like content must be removed in a new source version before external conversion.", 422, "SECRET_DETECTED")
        identity = _json_hash({
            "owner": str(user_id), "version": str(version.id), "source": artifact.content_sha256,
            "contract": pine.CONTRACT_VERSION, "prompt": settings.PINE_CONVERSION_PROMPT_VERSION,
            "provider": settings.PINE_CONVERSION_PROVIDER, "model": settings.PINE_CONVERSION_MODEL,
            "options": options_hash,
        })
        existing = db.scalar(select(models.PineConversionRequest).where(
            models.PineConversionRequest.identity_sha256 == identity,
            models.PineConversionRequest.status.in_(ACTIVE | {"succeeded", "validation_failed", "accepted"}),
        ).order_by(models.PineConversionRequest.attempt.desc()))
        if existing:
            return {"conversion": _public(existing), "reused": True}
        today = _now().date()
        daily = db.scalar(select(func.count(models.PineConversionRequest.id)).where(
            models.PineConversionRequest.owner_user_id == user_id,
            func.date(models.PineConversionRequest.created_at) == today,
        )) or 0
        if daily >= settings.PINE_CONVERSION_MAX_DAILY_REQUESTS_PER_USER:
            raise ConversionError("Daily Pine conversion limit reached.", 429, "DAILY_LIMIT")
        active = db.scalar(select(func.count(models.PineConversionRequest.id)).where(
            models.PineConversionRequest.owner_user_id == user_id,
            models.PineConversionRequest.status.in_(ACTIVE),
        )) or 0
        if active >= settings.PINE_CONVERSION_MAX_CONCURRENT_PER_USER:
            raise ConversionError("A Pine conversion is already in progress.", 429, "CONCURRENT_LIMIT")
        row = models.PineConversionRequest(
            owner_user_id=user_id, strategy_id=strategy.id, input_version_id=version.id,
            input_source_sha256=artifact.content_sha256, contract_version=pine.CONTRACT_VERSION,
            prompt_version=settings.PINE_CONVERSION_PROMPT_VERSION, provider=settings.PINE_CONVERSION_PROVIDER,
            model=settings.PINE_CONVERSION_MODEL, options=options_dict, options_sha256=options_hash,
            identity_sha256=identity, consent_at=_now(), status="queued", available_at=_now(),
            max_attempts=max(settings.PINE_CONVERSION_MAX_RETRIES + 1, 1),
        )
        db.add(row); db.flush()
        crud.add_audit_log(db, user_id=user_id, action="PINE_CONVERSION_CONSENTED", metadata={"conversion_id": str(row.id), "version_id": str(version.id), "source_sha256": artifact.content_sha256, "provider": row.provider, "model": row.model, "prompt_version": row.prompt_version})
        return {"conversion": _public(row), "reused": False}


def list_requests(user_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 100)), max(offset, 0)
    with session_scope() as db:
        query = select(models.PineConversionRequest).where(models.PineConversionRequest.owner_user_id == user_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(query.order_by(models.PineConversionRequest.created_at.desc()).limit(limit).offset(offset)).all()
        return {"conversions": [_public(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def get_request(user_id: uuid.UUID, request_id, *, include_source: bool = True) -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_request(db, user_id, request_id)
        result = _public(row)
        if include_source:
            result["original_source"] = pine._artifact(db, row.input_version_id).content
            result["candidate_source"] = pine._artifact(db, row.candidate_version_id).content if row.candidate_version_id else None
            report = pine._latest_report(db, row.candidate_version_id) if row.candidate_version_id else None
            result["validation"] = pine._report_public(report) if report else None
        return {"conversion": result}


def cancel(user_id: uuid.UUID, request_id) -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_request(db, user_id, request_id, lock=True)
        if row.status not in ACTIVE:
            raise ConversionError("Only a queued or processing conversion can be canceled.", 409, "STATE_CONFLICT")
        row.status = "canceled"; row.completed_at = _now(); row.updated_at = _now()
        return {"conversion": _public(row)}


def accept(user_id: uuid.UUID, request_id) -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_request(db, user_id, request_id, lock=True)
        if row.status != "succeeded" or not row.candidate_version_id:
            raise ConversionError("Only a passing candidate can be accepted.", 409, "CANDIDATE_NOT_READY")
        artifact = pine._artifact(db, row.candidate_version_id)
        artifact.conversion_method = "ai_conversion_accepted"
        row.status = "accepted"; row.completed_at = _now(); row.updated_at = _now()
        crud.add_audit_log(db, user_id=user_id, action="PINE_CONVERSION_ACCEPTED", metadata={"conversion_id": str(row.id), "candidate_version_id": str(row.candidate_version_id), "source_sha256": artifact.content_sha256})
        return {"conversion": _public(row)}


def reject(user_id: uuid.UUID, request_id, reason: str | None = None) -> dict[str, Any]:
    with session_scope() as db:
        row = _owned_request(db, user_id, request_id, lock=True)
        if row.status not in {"succeeded", "validation_failed"}:
            raise ConversionError("This candidate cannot be rejected in its current state.", 409, "STATE_CONFLICT")
        if row.candidate_version_id:
            version = db.get(models.StrategyVersion, row.candidate_version_id)
            if version and version.status != "approved":
                version.status = "archived"
        row.status = "rejected"; row.safe_error_code = "USER_REJECTED"; row.completed_at = _now(); row.updated_at = _now()
        crud.add_audit_log(db, user_id=user_id, action="PINE_CONVERSION_REJECTED", metadata={"conversion_id": str(row.id), "reason": (reason or "")[:500]})
        return {"conversion": _public(row)}


def retry(user_id: uuid.UUID, request_id) -> dict[str, Any]:
    with session_scope() as db:
        db.scalar(select(models.User).where(models.User.id == user_id).with_for_update())
        previous = _owned_request(db, user_id, request_id, lock=True)
        if previous.status not in {"provider_failed", "canceled"}:
            raise ConversionError("Only failed or canceled conversions can be retried.", 409, "RETRY_NOT_ALLOWED")
        today = _now().date()
        daily = db.scalar(select(func.count(models.PineConversionRequest.id)).where(
            models.PineConversionRequest.owner_user_id == user_id,
            func.date(models.PineConversionRequest.created_at) == today,
        )) or 0
        if daily >= settings.PINE_CONVERSION_MAX_DAILY_REQUESTS_PER_USER:
            raise ConversionError("Daily Pine conversion limit reached.", 429, "DAILY_LIMIT")
        active = db.scalar(select(func.count(models.PineConversionRequest.id)).where(
            models.PineConversionRequest.owner_user_id == user_id,
            models.PineConversionRequest.status.in_(ACTIVE),
        )) or 0
        if active:
            raise ConversionError("A Pine conversion is already in progress.", 429, "CONCURRENT_LIMIT")
        attempt = (db.scalar(select(func.max(models.PineConversionRequest.attempt)).where(models.PineConversionRequest.identity_sha256 == previous.identity_sha256)) or 0) + 1
        row = models.PineConversionRequest(
            owner_user_id=user_id, strategy_id=previous.strategy_id, input_version_id=previous.input_version_id,
            input_source_sha256=previous.input_source_sha256, contract_version=previous.contract_version,
            prompt_version=previous.prompt_version, provider=previous.provider, model=previous.model,
            options=previous.options, options_sha256=previous.options_sha256, identity_sha256=previous.identity_sha256,
            attempt=attempt, consent_at=_now(), status="queued", available_at=_now(), max_attempts=previous.max_attempts,
        )
        db.add(row); db.flush()
        crud.add_audit_log(db, user_id=user_id, action="PINE_CONVERSION_RETRY_CONSENTED", metadata={"conversion_id": str(row.id), "previous_conversion_id": str(previous.id), "version_id": str(row.input_version_id), "source_sha256": row.input_source_sha256})
        return {"conversion": _public(row)}


def build_prompt(source: str, options: dict[str, Any]) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{{OPTIONS}}", json.dumps(options, sort_keys=True)).replace("{{SOURCE}}", source)


def validate_provider_output(output) -> None:
    source = pine_validation.canonicalize_source(output.converted_source)
    if len(source.encode()) > settings.PINE_CONVERSION_MAX_SOURCE_BYTES:
        raise ConversionError("Provider output exceeded the safe source limit.", 422, "OUTPUT_TOO_LARGE")
    if contains_secret(source):
        raise ConversionError("Provider output contained credential-like content.", 422, "SECRET_IN_OUTPUT")
    if not re.search(r"(?m)^\s*//@version\s*=\s*[56]\s*$", source) or not re.search(r"\b(?:indicator|strategy)\s*\(", source):
        raise ConversionError("Provider output was not supported Pine source.", 422, "NON_PINE_OUTPUT")
    for field in pine_validation.SERVER_FIELDS:
        if re.search(rf"\\?[\"']{re.escape(field)}\\?[\"']\s*:", source, re.I):
            raise ConversionError("Provider output attempted to control server-authoritative fields.", 422, "SERVER_AUTHORITY_OUTPUT")


def process_claim(claim: dict[str, Any]) -> None:
    request_id = claim["id"]
    try:
        with session_scope() as db:
            row = db.get(models.PineConversionRequest, request_id)
            if row is None or row.status != "processing": return
            strategy = pine._owned_strategy(db, row.owner_user_id, row.strategy_id)
            artifact = pine._artifact(db, row.input_version_id)
            if strategy.status != "active" or artifact.content_sha256 != row.input_source_sha256:
                raise ConversionError("Conversion input is no longer valid.", 409, "INPUT_STALE")
            if contains_secret(artifact.content):
                raise ConversionError("Credential-like content detected.", 422, "SECRET_DETECTED")
            source, prompt = artifact.content, build_prompt(artifact.content, row.options)
        result = pine_conversion_provider.get_provider().convert(pine_conversion_provider.PineConversionProviderRequest(prompt, claim["model"], settings.PINE_CONVERSION_TIMEOUT_SECONDS))
        validate_provider_output(result.output)
        if _hash(pine_validation.canonicalize_source(result.output.converted_source)) == claim["input_source_sha256"]:
            raise ConversionError("Provider returned the unchanged input source.", 422, "OUTPUT_UNCHANGED")
        with session_scope() as db:
            row = db.scalar(select(models.PineConversionRequest).where(models.PineConversionRequest.id == request_id).with_for_update())
            if row is None or row.status != "processing": return
            strategy = pine._owned_strategy(db, row.owner_user_id, row.strategy_id, lock=True)
            input_artifact = pine._artifact(db, row.input_version_id)
            if input_artifact.content_sha256 != row.input_source_sha256 or strategy.status != "active":
                raise ConversionError("Conversion input changed or was archived.", 409, "INPUT_STALE")
            version, _ = pine._create_version(db, row.owner_user_id, strategy, result.output.converted_source, "nova-ai-candidate.pine", f"AI conversion from {row.input_version_id}")
            artifact = pine._artifact(db, version.id)
            artifact.conversion_method = "ai_conversion_pending"
            row.candidate_version_id = version.id; row.provider_request_id = result.request_id
            row.usage_summary = result.usage; row.conversion_summary = result.output.conversion_summary
            row.assumptions = result.output.assumptions; row.unsupported_features = result.output.unsupported_features
            row.warnings = result.output.warnings; row.action_mapping = result.output.action_mapping; row.updated_at = _now()
            crud.add_audit_log(db, user_id=row.owner_user_id, action="PINE_CONVERSION_CANDIDATE_CREATED", metadata={"conversion_id": str(row.id), "candidate_version_id": str(version.id), "source_sha256": artifact.content_sha256})
            owner, strategy_id, candidate_id = row.owner_user_id, row.strategy_id, version.id
        validated = pine.validate_version(owner, strategy_id, candidate_id)
        with session_scope() as db:
            row = db.scalar(select(models.PineConversionRequest).where(models.PineConversionRequest.id == request_id).with_for_update())
            if row is None or row.status != "processing": return
            report = validated["report"]
            row.validation_report_id = uuid.UUID(report["id"])
            row.status = "succeeded" if report["eligible_for_review"] else "validation_failed"
            row.completed_at = _now(); row.locked_at = None; row.updated_at = _now()
    except pine_conversion_provider.ProviderError as exc:
        _fail(request_id, "provider_failed", exc.code, retryable=True)
    except ConversionError as exc:
        status = "rejected_secret_detected" if exc.code in {"SECRET_DETECTED", "SECRET_IN_OUTPUT"} else "provider_failed"
        _fail(request_id, status, exc.code or "CONVERSION_REJECTED")
    except Exception:
        _fail(request_id, "provider_failed", "INTERNAL_CONVERSION_ERROR")


def _fail(request_id, status: str, code: str, *, retryable: bool = False) -> None:
    with session_scope() as db:
        row = db.get(models.PineConversionRequest, request_id)
        if row is None or row.status != "processing": return
        if retryable and row.worker_attempts < row.max_attempts:
            row.status = "queued"; row.safe_error_code = code; row.available_at = _now(); row.locked_at = None; row.updated_at = _now()
            return
        row.status = status; row.safe_error_code = code; row.completed_at = _now(); row.locked_at = None; row.updated_at = _now()
