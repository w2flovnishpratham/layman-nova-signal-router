"""Owner-scoped manual and durable opt-in AI Pine conversion workflow."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import func, select

from app.config import settings
from app.db import crud, models
from app.db.engine import session_scope
from app.schemas.pine_conversion import ConversionOptions
from app.services import personal_pine_service as pine, pine_conversion_provider, pine_validation

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
PROMPT_PATH = PROMPT_DIR / f"pine_conversion_{settings.PINE_CONVERSION_PROMPT_VERSION}.txt"
TRANSPORT_VERSION = "pine_transport_v1"
TRANSPORT_PATH = PROMPT_DIR / f"{TRANSPORT_VERSION}.txt"
PROMPT_V3_SHA256 = "7ccf88726d47732a5326d586a78b2639d1f23ec4043aca82d4cf7d4e6f4e29f7"
TRANSPORT_V1_SHA256 = "b72f2efcf839e693c83773e40c2324009065ded7a2ddfcbdb31a1f110efdc611"
PROMPT_V31_SHA256 = "4fc31dbd5a94429806227754a32959716e87f68cbb20b952c746d8a11e8785b7"
TRANSPORT_V2_VERSION = "pine_transport_v2"
TRANSPORT_V2_PATH = PROMPT_DIR / f"{TRANSPORT_V2_VERSION}.txt"
TRANSPORT_V2_SHA256 = "18a3247c93c0c17e2bb70847a635c721bacf6e231d8d14c14db7871da56ef96f"
PROMPT_V4_SHA256 = "96708cdca3cc5fb6c224eaf4310d285ebc355565ed391f085274815a93707087"
PROMPT_V41_SHA256 = "9b025ac35013e0a137f16b0816e60b8f0a53fc044a713e9d1a32b8e7c00e4e49"
PROMPT_V42_SHA256 = "165f19a93707d0bdee93f7ff149fc3ec0849a79de4e464e6b01ebdd9c4fc9f4a"
TRANSPORT_V3_STRATEGY_FILL_VERSION = "pine_transport_v3_fill"  # <=30 chars: TradingViewCompileEvidence.transport_version is VARCHAR(30)
TRANSPORT_V3_STRATEGY_FILL_PATH = PROMPT_DIR / f"{TRANSPORT_V3_STRATEGY_FILL_VERSION}.txt"
TRANSPORT_V3_STRATEGY_FILL_SHA256 = "9450968b40589cf9d3f9d5dcafa70e3dd4b79cac6a81e14afca3739cebde01f5"
# Broadcast counterparts: same layer contract (same novaWebhookPayload
# signature the STRATEGY/INDICATOR layer calls), swapped to the single
# NOVA-managed secret instead of a per-user nwk_ credential. Used only to
# rewrite an already-approved candidate's transport block at publish time
# (see admin_pine_conversion_service.publish_as_shared) -- never sent to the
# LLM, so no prompt-side placeholders needed.
TRANSPORT_V2_BCAST_VERSION = "pine_transport_v2_bcast"
TRANSPORT_V2_BCAST_PATH = PROMPT_DIR / f"{TRANSPORT_V2_BCAST_VERSION}.txt"
TRANSPORT_V2_BCAST_SHA256 = "fd2d200626cb91a7b7b340a7b23f5741fbe8f1a645fb80ff1cc5e7c127ca0d25"
TRANSPORT_V3_STRATEGY_FILL_BCAST_VERSION = "pine_transport_v3_fill_bcast"
TRANSPORT_V3_STRATEGY_FILL_BCAST_PATH = PROMPT_DIR / f"{TRANSPORT_V3_STRATEGY_FILL_BCAST_VERSION}.txt"
TRANSPORT_V3_STRATEGY_FILL_BCAST_SHA256 = "2da20e9d616f216ed06daa4341bd955d1bb5955ac5d1551b3b5f1e28d201b93c"
QUALIFICATION_PACKAGES = {
    "v3": (PROMPT_V3_SHA256, TRANSPORT_VERSION, TRANSPORT_PATH, TRANSPORT_V1_SHA256),
    "v3.1": (PROMPT_V31_SHA256, TRANSPORT_V2_VERSION, TRANSPORT_V2_PATH, TRANSPORT_V2_SHA256),
}
PACKAGE_PLACEHOLDERS = {"{{TRANSPORT}}", "{{OPTIONS}}", "{{SOURCE}}"}
PACKAGE_PLACEHOLDERS_V4 = PACKAGE_PLACEHOLDERS | {"{{SOURCE_TYPE}}"}
TRANSPORT_PLACEHOLDERS = {"{{STRATEGY_CODE}}", "{{STRATEGY_VERSION}}"}
RESERVED_DELIMITERS = (
    "BEGIN_FROZEN_NOVA_TRANSPORT", "END_FROZEN_NOVA_TRANSPORT",
    "BEGIN_UNTRUSTED_CONVERSION_OPTIONS", "END_UNTRUSTED_CONVERSION_OPTIONS",
    "BEGIN_UNTRUSTED_PINE_SOURCE", "END_UNTRUSTED_PINE_SOURCE",
)
SUPPORTED_PACKAGE_OPTIONS = {"requested_setup_type", "intended_symbol", "intended_timeframe"}
PLACEHOLDER_PATTERN = re.compile(r"{{[A-Z][A-Z0-9_]*}}")
PACKAGE_ASSEMBLY_ERROR = "The NOVA conversion package could not be generated safely. Please retry or contact NOVA support."
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


def manual_prompt_version() -> str:
    if settings.PINE_CONVERSION_QUALIFICATION_PACKAGE_ENABLED:
        return settings.PINE_CONVERSION_QUALIFICATION_PROMPT_VERSION
    return settings.PINE_CONVERSION_PROMPT_VERSION


def prompt_path(version: str) -> Path:
    if not re.fullmatch(r"v\d+(?:\.\d+)?", version):
        raise ConversionError("Configured Pine prompt version is invalid.", 500, "PROMPT_VERSION_INVALID")
    return PROMPT_DIR / f"pine_conversion_{version}.txt"


def _assembly_failed() -> ConversionError:
    return ConversionError(PACKAGE_ASSEMBLY_ERROR, 500, "PINE_PACKAGE_ASSEMBLY_FAILED")


def _read_canonical(path: Path, expected_sha256: str) -> str:
    try:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise _assembly_failed()
        return content.decode("utf-8")
    except ConversionError:
        raise
    except (OSError, UnicodeError) as exc:
        raise _assembly_failed() from exc


def _serialize_package_options(options: Mapping[str, Any] | None) -> str:
    values = dict(options or {})
    if values.keys() - SUPPORTED_PACKAGE_OPTIONS or any(not isinstance(value, str) for value in values.values()):
        raise _assembly_failed()
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def _section(package: str, begin: str, end: str) -> str:
    if package.count(begin) != 1 or package.count(end) != 1:
        raise _assembly_failed()
    start = package.index(begin) + len(begin)
    finish = package.index(end, start)
    if package[start:start + 1] != "\n" or package[finish - 1:finish] != "\n":
        raise _assembly_failed()
    return package[start + 1:finish - 1]


def _validate_v3_package(
    package: str,
    prompt_template: str,
    transport: str,
    source: str,
    options_json: str,
    *,
    version: str = "v3",
    prompt_sha256: str = PROMPT_V3_SHA256,
    transport_sha256: str = TRANSPORT_V1_SHA256,
    selected_source_sha256: str | None = None,
) -> None:
    if f"Prompt version: {version}" not in package or "Prompt status: QUALIFICATION" not in package:
        raise _assembly_failed()
    transport_block = _section(package, "BEGIN_FROZEN_NOVA_TRANSPORT", "END_FROZEN_NOVA_TRANSPORT")
    options_block = _section(package, "BEGIN_UNTRUSTED_CONVERSION_OPTIONS", "END_UNTRUSTED_CONVERSION_OPTIONS")
    source_block = _section(package, "BEGIN_UNTRUSTED_PINE_SOURCE", "END_UNTRUSTED_PINE_SOURCE")
    if not transport_block or not options_block or not source_block:
        raise _assembly_failed()
    if transport_block != transport or hashlib.sha256(transport_block.encode("utf-8")).hexdigest() != transport_sha256:
        raise _assembly_failed()
    if options_block != options_json or source_block != source:
        raise _assembly_failed()
    if package.count(transport) != 1 or package.count(source) != 1:
        raise _assembly_failed()
    if any(token in package for token in PACKAGE_PLACEHOLDERS):
        raise _assembly_failed()
    if set(PLACEHOLDER_PATTERN.findall(package)) != TRANSPORT_PLACEHOLDERS:
        raise _assembly_failed()
    if hashlib.sha256(prompt_template.encode("utf-8")).hexdigest() != prompt_sha256:
        raise _assembly_failed()
    if selected_source_sha256:
        header = package.partition("\n\nPrivacy warning:")[0]
        if f"Selected source SHA-256: {selected_source_sha256}" not in header:
            raise _assembly_failed()


def _assemble_v3_prompt(prompt_template: str, transport: str, source: str, options: Mapping[str, Any] | None = None) -> tuple[str, str]:
    if not source or any(delimiter in source for delimiter in RESERVED_DELIMITERS) or PLACEHOLDER_PATTERN.search(source):
        raise _assembly_failed()
    prompt_placeholders = set(PLACEHOLDER_PATTERN.findall(prompt_template))
    if not PACKAGE_PLACEHOLDERS <= prompt_placeholders or prompt_placeholders - PACKAGE_PLACEHOLDERS - TRANSPORT_PLACEHOLDERS:
        raise _assembly_failed()
    if set(PLACEHOLDER_PATTERN.findall(transport)) != TRANSPORT_PLACEHOLDERS:
        raise _assembly_failed()
    options_json = _serialize_package_options(options)
    prompt = prompt_template
    for placeholder, value in (("{{TRANSPORT}}", transport), ("{{OPTIONS}}", options_json), ("{{SOURCE}}", source)):
        if prompt.count(placeholder) != 1:
            raise _assembly_failed()
        prompt = prompt.replace(placeholder, value, 1)
    return prompt, options_json


def _assemble_v4_prompt(
    prompt_template: str, transport: str, source: str, source_type: str, options: Mapping[str, Any] | None = None
) -> tuple[str, str]:
    if not source or any(delimiter in source for delimiter in RESERVED_DELIMITERS) or PLACEHOLDER_PATTERN.search(source):
        raise _assembly_failed()
    if source_type not in {"STRATEGY", "INDICATOR"}:
        raise _assembly_failed()
    prompt_placeholders = set(PLACEHOLDER_PATTERN.findall(prompt_template))
    if not PACKAGE_PLACEHOLDERS_V4 <= prompt_placeholders or prompt_placeholders - PACKAGE_PLACEHOLDERS_V4 - TRANSPORT_PLACEHOLDERS:
        raise _assembly_failed()
    if set(PLACEHOLDER_PATTERN.findall(transport)) != TRANSPORT_PLACEHOLDERS:
        raise _assembly_failed()
    options_json = _serialize_package_options(options)
    prompt = prompt_template
    for placeholder, value in (
        ("{{TRANSPORT}}", transport), ("{{OPTIONS}}", options_json),
        ("{{SOURCE}}", source), ("{{SOURCE_TYPE}}", source_type),
    ):
        if prompt.count(placeholder) != 1:
            raise _assembly_failed()
        prompt = prompt.replace(placeholder, value, 1)
    return prompt, options_json


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
    version = manual_prompt_version()
    qualification = QUALIFICATION_PACKAGES.get(version)
    return {
        "manual_package_enabled": settings.PINE_CONVERSION_MANUAL_PACKAGE_ENABLED,
        "ai_enabled": settings.PINE_CONVERSION_AI_ENABLED,
        "provider": settings.PINE_CONVERSION_PROVIDER if settings.PINE_CONVERSION_AI_ENABLED else None,
        "model": settings.PINE_CONVERSION_MODEL if settings.PINE_CONVERSION_AI_ENABLED else None,
        "prompt_version": version,
        "prompt_status": "QUALIFICATION" if version == settings.PINE_CONVERSION_QUALIFICATION_PROMPT_VERSION else "DEPLOYED",
        "transport_version": qualification[1] if qualification else None,
        "contract_version": pine.CONTRACT_VERSION,
        "daily_limit": settings.PINE_CONVERSION_MAX_DAILY_REQUESTS_PER_USER,
    }


def manual_package(user_id: uuid.UUID, strategy_id, version_id) -> dict[str, Any]:
    if not settings.PINE_CONVERSION_MANUAL_PACKAGE_ENABLED:
        raise ConversionError("Manual conversion packages are disabled.", 404, "FEATURE_DISABLED")
    source = pine.get_package_source(user_id, strategy_id, version_id)
    version = manual_prompt_version()
    qualification = QUALIFICATION_PACKAGES.get(version)
    if qualification is None:
        prompt_template = prompt_path(version).read_text(encoding="utf-8")
        prompt = prompt_template.replace("{{OPTIONS}}", json.dumps({"workflow": "manual_external_conversion"}, sort_keys=True)).replace("{{SOURCE}}", source["source"])
        package = f"""# NOVA Pine Contract v1 conversion package

Prompt version: {settings.PINE_CONVERSION_PROMPT_VERSION}
Prompt content SHA-256: {_hash(prompt_template)}

Privacy warning: copying this package to an external assistant shares your Pine source with that service. Review that service's privacy terms before pasting private strategy code.

## Current master prompt
{prompt}

## Manual validation and TradingView compilation checklist
- Pine v6 and exactly one declaration.
- BUY_CE, BUY_PE, EXIT and optional HOLD use the NOVA action vocabulary.
- No credentials or server-authority fields.
- NIFTY intraday, one position, no scale-in or pyramiding.
- Completed-bar behavior and repainting risks are explicit.
- Entry, exit, reversal assumptions and unsupported constructs are documented.
- Paste into TradingView and confirm compilation separately; NOVA static validation is not compilation.
- Add the script to the intended symbol/timeframe chart and use once-per-bar-close alerts where compatible.
"""
        return {"package": package, "filename": "nova-pine-conversion-package.txt", "package_sha256": _hash(package), "source_sha256": source["source_sha256"], "prompt_version": settings.PINE_CONVERSION_PROMPT_VERSION, "prompt_content_sha256": _hash(prompt_template)}

    prompt_sha256, transport_version, transport_path, transport_sha256 = qualification
    prompt_template = _read_canonical(prompt_path(version), prompt_sha256)
    transport = _read_canonical(transport_path, transport_sha256)
    prompt, options_json = _assemble_v3_prompt(prompt_template, transport, source["source"])
    instructions = """1. Copy this package into ChatGPT or Claude.
2. Copy only ARTIFACT 1 back into NOVA as the converted Pine.
3. Artifact 2 is a simple status.
4. Artifact 3 is for NOVA review; you do not need to edit it."""
    package = f"""# NOVA Pine Contract v1 conversion package

Prompt version: {version}
Prompt status: QUALIFICATION
Selected source SHA-256: {source["source_sha256"]}
Prompt V3 SHA-256: {_hash(prompt_template)}
Transport version: {transport_version}
Transport SHA-256: {_hash(transport)}

Privacy warning: copying this package to an external assistant shares your Pine source with that service. Review that service's privacy terms before pasting private strategy code.

## What to do
{instructions}

## Current master prompt
{prompt}
"""
    _validate_v3_package(
        package, prompt_template, transport, source["source"], options_json,
        version=version, prompt_sha256=prompt_sha256, transport_sha256=transport_sha256,
        selected_source_sha256=source["source_sha256"],
    )
    return {
        "package": package, "filename": "nova-pine-conversion-package.txt",
        "package_sha256": _hash(package), "source_sha256": source["source_sha256"],
        "prompt_version": version, "prompt_status": "QUALIFICATION",
        "prompt_content_sha256": _hash(prompt_template), "transport_version": transport_version,
        "transport_content_sha256": _hash(transport),
    }


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
