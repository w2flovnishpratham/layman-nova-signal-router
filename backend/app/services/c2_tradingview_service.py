"""C2: exact C1 candidate -> manual TradingView setup -> Paper eligibility.

This module is control-plane only. It reuses the existing StrategyInstance,
hash-only private credential, TradingViewSetup, private webhook, engine picker,
and audit foundations. It never starts an engine or creates execution work.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import crud, models
from app.db.engine import session_scope
from app.services import admin_pine_conversion_service as c1
from app.services import personal_pine_service as pine
from app.services import pine_conversion_service as frozen


SELF = "SELF"
MANAGED = "MANAGED"
MODE_TO_SETUP = {
    SELF: "USER_MANAGED_TRADINGVIEW",
    MANAGED: "NOVA_MANAGED_TRADINGVIEW",
}
SETUP_TO_MODE = {value: key for key, value in MODE_TO_SETUP.items()}
WEBHOOK_PATH = "/api/webhooks/private"
PLACEHOLDER = "{{ONE_TIME_CREDENTIAL}}"
SAFE_MODES = {"signal_only", "paper_live_data"}

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|cookie|session|secret|token|api[_ -]?key|credential)\b\s*[:=]\s*\S+"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")


class C2Error(ValueError):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_enabled() -> None:
    if not settings.C2_TRADINGVIEW_INSTALLATION_ENABLED:
        raise C2Error("TradingView installation is unavailable.", 404, "FEATURE_DISABLED")


def public_config() -> dict[str, Any]:
    return {
        "enabled": bool(settings.C2_TRADINGVIEW_INSTALLATION_ENABLED),
        "webhook_path": WEBHOOK_PATH,
        "live_eligibility": False,
        "browser_automation": False,
    }


def _safe_text(value: str | None, *, required: bool = False) -> str | None:
    text = " ".join(_CONTROL.sub(" ", value or "").split()).strip()
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = text[:1000] or None
    if required and text is None:
        raise C2Error("A sanitized compiler-error summary is required.", 422, "SUMMARY_REQUIRED")
    return text


def _query_one(db, model, *criteria, lock: bool = False):
    query = select(model).where(*criteria)
    return db.scalar(query.with_for_update() if lock else query)


def _approved_context(
    db,
    conversion_id: uuid.UUID | str,
    *,
    lock: bool,
) -> dict[str, Any]:
    conversion = _query_one(
        db,
        models.PineConversionRequest,
        models.PineConversionRequest.id == uuid.UUID(str(conversion_id)),
        models.PineConversionRequest.provider == c1.PROVIDER,
        lock=lock,
    )
    if (
        conversion is None
        or conversion.status != "approved_for_tv_compile"
        or conversion.candidate_version_id is None
        or conversion.validation_report_id is None
    ):
        raise C2Error(
            "Only an exact C1-approved candidate can enter TradingView setup.",
            409,
            "C1_APPROVAL_REQUIRED",
        )

    source, source_error = c1._verified_current_source_artifact(  # noqa: SLF001
        db, conversion, lock=lock
    )
    candidate_version = _query_one(
        db,
        models.StrategyVersion,
        models.StrategyVersion.id == conversion.candidate_version_id,
        models.StrategyVersion.strategy_id == conversion.strategy_id,
        lock=lock,
    )
    candidate = _query_one(
        db,
        models.StrategySourceArtifact,
        models.StrategySourceArtifact.strategy_version_id == conversion.candidate_version_id,
        models.StrategySourceArtifact.artifact_type == pine.PINE_ARTIFACT,
        lock=lock,
    )
    layer = _query_one(
        db,
        models.StrategySourceArtifact,
        models.StrategySourceArtifact.strategy_version_id == conversion.candidate_version_id,
        models.StrategySourceArtifact.artifact_type == c1.STRATEGY_LAYER_ARTIFACT,
        lock=lock,
    )
    report = _query_one(
        db,
        models.StrategyValidationReport,
        models.StrategyValidationReport.id == conversion.validation_report_id,
        models.StrategyValidationReport.strategy_version_id == conversion.candidate_version_id,
        lock=lock,
    )
    review_query = (
        select(models.StrategyAdminReview)
        .where(
            models.StrategyAdminReview.strategy_version_id == conversion.candidate_version_id,
            models.StrategyAdminReview.decision == "approved",
        )
        .order_by(models.StrategyAdminReview.reviewed_at.desc())
    )
    review = db.scalar(review_query.with_for_update() if lock else review_query)
    strategy = _query_one(
        db,
        models.StrategyCatalog,
        models.StrategyCatalog.id == conversion.strategy_id,
        models.StrategyCatalog.owner_user_id == conversion.owner_user_id,
        models.StrategyCatalog.owner_type == "personal",
        models.StrategyCatalog.visibility == "private",
        lock=lock,
    )

    summary = conversion.usage_summary if isinstance(conversion.usage_summary, dict) else {}
    provenance = summary.get("provenance") if isinstance(summary.get("provenance"), dict) else {}
    approval = summary.get("approval_binding") if isinstance(summary.get("approval_binding"), dict) else {}
    try:
        review_binding = json.loads(review.notes) if review and review.notes else {}
    except (TypeError, ValueError):
        review_binding = {}
    expected = {
        "source_sha256": conversion.input_source_sha256,
        "strategy_layer_sha256": layer.content_sha256 if layer else None,
        "candidate_sha256": candidate.content_sha256 if candidate else None,
        "prompt_version": "v3.1",
        "prompt_sha256": frozen.PROMPT_V31_SHA256,
        "transport_version": frozen.TRANSPORT_V2_VERSION,
        "transport_sha256": frozen.TRANSPORT_V2_SHA256,
    }
    binding_keys = tuple(expected)
    valid = bool(
        source_error is None
        and source is not None
        and strategy is not None
        and candidate_version is not None
        and candidate is not None
        and layer is not None
        and report is not None
        and review is not None
        and conversion.prompt_version == "v3.1"
        and summary.get("review_status") == "APPROVED_FOR_TRADINGVIEW_COMPILE"
        and candidate_version.source_sha256 == candidate.content_sha256 == _hash(candidate.content)
        and layer.content_sha256 == _hash(layer.content)
        and report.eligible_for_review
        and report.status in {"passed", "passed_with_warnings"}
        and report.source_sha256 == candidate.content_sha256
        and review.validation_report_id == report.id
        and review.new_status == "approved_for_tv_compile"
        and review.source_sha256 == candidate.content_sha256
        and all(provenance.get(key) == value for key, value in expected.items())
        and all(approval.get(key) == value for key, value in expected.items())
        and all(review_binding.get(key) == value for key, value in expected.items())
    )
    if not valid:
        raise C2Error(
            "Approved candidate integrity no longer matches C1 evidence.",
            409,
            "CANDIDATE_INTEGRITY_INVALID",
        )
    return {
        "conversion": conversion,
        "strategy": strategy,
        "source": source,
        "candidate_version": candidate_version,
        "candidate": candidate,
        "layer": layer,
        "report": report,
        "review": review,
        "binding": {key: expected[key] for key in binding_keys},
    }


def _compile_public(row: models.TradingViewCompileEvidence | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row.id),
        "conversion_id": str(row.pine_conversion_request_id),
        "candidate_version_id": str(row.candidate_version_id),
        "result": row.result,
        "source_sha256": row.source_sha256,
        "strategy_layer_sha256": row.strategy_layer_sha256,
        "candidate_sha256": row.candidate_sha256,
        "prompt_version": row.prompt_version,
        "prompt_sha256": row.prompt_sha256,
        "transport_version": row.transport_version,
        "transport_sha256": row.transport_sha256,
        "compiler_error_summary": row.compiler_error_summary,
        "setup_notes": row.setup_notes,
        "admin_user_id": str(row.admin_user_id) if row.admin_user_id else None,
        "compiled_at": row.compiled_at.isoformat(),
    }


def _compile_for(db, conversion_id, *, lock: bool = False):
    return _query_one(
        db,
        models.TradingViewCompileEvidence,
        models.TradingViewCompileEvidence.pine_conversion_request_id
        == uuid.UUID(str(conversion_id)),
        lock=lock,
    )


def _compile_matches(row, context: dict[str, Any]) -> bool:
    binding = context["binding"]
    return bool(
        row
        and row.result == "SUCCESS"
        and row.candidate_version_id == context["candidate_version"].id
        and row.source_sha256 == binding["source_sha256"]
        and row.strategy_layer_sha256 == binding["strategy_layer_sha256"]
        and row.candidate_sha256 == binding["candidate_sha256"]
        and row.prompt_version == binding["prompt_version"]
        and row.prompt_sha256 == binding["prompt_sha256"]
        and row.transport_version == binding["transport_version"]
        and row.transport_sha256 == binding["transport_sha256"]
    )


def record_compile(
    admin_id: uuid.UUID,
    conversion_id: uuid.UUID | str,
    *,
    succeeded: bool,
    compiler_error_summary: str | None = None,
    setup_notes: str | None = None,
) -> dict[str, Any]:
    _require_enabled()
    result = "SUCCESS" if succeeded else "FAILURE"
    error_summary = _safe_text(compiler_error_summary, required=not succeeded)
    safe_notes = _safe_text(setup_notes)
    with session_scope() as db:
        context = _approved_context(db, conversion_id, lock=True)
        existing = _compile_for(db, conversion_id, lock=True)
        if existing is not None:
            if existing.result == result:
                return _compile_public(existing)
            raise C2Error(
                "This candidate already has a terminal compile result.",
                409,
                "COMPILE_RESULT_FINAL",
            )
        binding = context["binding"]
        now = _now()
        row = models.TradingViewCompileEvidence(
            pine_conversion_request_id=context["conversion"].id,
            candidate_version_id=context["candidate_version"].id,
            result=result,
            source_sha256=binding["source_sha256"],
            strategy_layer_sha256=binding["strategy_layer_sha256"],
            candidate_sha256=binding["candidate_sha256"],
            prompt_version=binding["prompt_version"],
            prompt_sha256=binding["prompt_sha256"],
            transport_version=binding["transport_version"],
            transport_sha256=binding["transport_sha256"],
            compiler_error_summary=error_summary,
            setup_notes=safe_notes,
            admin_user_id=admin_id,
            compiled_at=now,
        )
        db.add(row)
        db.flush()
        crud.add_audit_log(
            db,
            user_id=admin_id,
            action=(
                "TRADINGVIEW_COMPILE_SUCCEEDED"
                if succeeded
                else "TRADINGVIEW_COMPILE_FAILED"
            ),
            metadata={
                "conversion_id": str(context["conversion"].id),
                "version_id": str(context["candidate_version"].id),
                "candidate_sha_ref": binding["candidate_sha256"][:12],
                "result": result,
            },
        )
        return _compile_public(row)


def admin_conversion_status(admin_id, conversion_id) -> dict[str, Any]:
    with session_scope() as db:
        context = _approved_context(db, conversion_id, lock=False)
        evidence = _compile_for(db, conversion_id)
        crud.add_audit_log(
            db,
            user_id=admin_id,
            action="C2_APPROVED_PINE_VIEWED",
            metadata={
                "conversion_id": str(context["conversion"].id),
                "candidate_sha_ref": context["binding"]["candidate_sha256"][:12],
            },
        )
        return {
            "enabled": bool(settings.C2_TRADINGVIEW_INSTALLATION_ENABLED),
            "compile": _compile_public(evidence),
            "candidate": {
                **context["binding"],
                "conversion_id": str(context["conversion"].id),
                "strategy_id": str(context["strategy"].id),
                "strategy_name": context["strategy"].display_name,
                "candidate_version_id": str(context["candidate_version"].id),
                "version": context["candidate_version"].version,
                "pine": context["candidate"].content,
            },
        }


def approved_pine(admin_id, conversion_id) -> tuple[str, str]:
    detail = admin_conversion_status(admin_id, conversion_id)
    candidate = detail["candidate"]
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate["strategy_name"]).strip("-")
    return candidate["pine"], f"{safe_name or 'nova-strategy'}-{candidate['candidate_sha256'][:12]}.pine"


def _get_setup(db, installation_id, *, lock=False):
    row = _query_one(
        db,
        models.TradingViewSetup,
        models.TradingViewSetup.id == uuid.UUID(str(installation_id)),
        models.TradingViewSetup.pine_conversion_request_id.is_not(None),
        lock=lock,
    )
    if row is None:
        raise C2Error("Strategy installation not found.", 404, "NOT_FOUND")
    return row


def _owned_setup(db, owner_id, installation_id, *, lock=False):
    row = _get_setup(db, installation_id, lock=lock)
    if row.user_id != owner_id:
        raise C2Error("Strategy installation not found.", 404, "NOT_FOUND")
    return row


def _active_credential(db, instance_id, *, lock=False):
    query = select(models.StrategyInstanceWebhookCredential).where(
        models.StrategyInstanceWebhookCredential.strategy_instance_id == instance_id,
        models.StrategyInstanceWebhookCredential.revoked_at.is_(None),
    )
    return db.scalar(query.with_for_update() if lock else query)


def _readiness(db, setup: models.TradingViewSetup) -> dict[str, Any]:
    instance = db.get(models.StrategyInstance, setup.strategy_instance_id)
    credential = _active_credential(db, setup.strategy_instance_id) if instance else None
    context = None
    try:
        context = _approved_context(db, setup.pine_conversion_request_id, lock=False)
    except C2Error:
        pass
    compile_evidence = (
        db.get(models.TradingViewCompileEvidence, setup.compile_evidence_id)
        if setup.compile_evidence_id
        else None
    )
    candidate_integrity = bool(
        context
        and setup.approved_version_id == context["candidate_version"].id
        and setup.approved_candidate_sha256 == context["binding"]["candidate_sha256"]
        and setup.approved_source_sha256 == context["binding"]["source_sha256"]
        and setup.approved_strategy_layer_sha256
        == context["binding"]["strategy_layer_sha256"]
    )
    compile_success = bool(
        context
        and compile_evidence
        and compile_evidence.id == setup.compile_evidence_id
        and _compile_matches(compile_evidence, context)
    )
    owner_bound = bool(
        instance
        and instance.user_id == setup.user_id
        and instance.strategy_version_id == setup.approved_version_id
        and instance.approved_candidate_sha256 == setup.approved_candidate_sha256
        and instance.installation_mode == SETUP_TO_MODE.get(setup.setup_type)
    )
    credential_active = bool(
        credential
        and setup.current_credential_id == credential.id
        and setup.credential_revoked_at is None
    )
    hold_verified = bool(
        setup.hold_verified_at
        and setup.hold_signal_id
        and setup.hold_credential_id
        and credential
        and setup.hold_credential_id == credential.id
    )
    paper_safe = bool(instance and instance.execution_mode in SAFE_MODES)
    not_suspended = setup.suspended_at is None and setup.status != "INSTALLATION_SUSPENDED"
    gates = {
        "c1_approval": context is not None,
        "compile_success": compile_success,
        "installation": True,
        "strategy_instance": instance is not None,
        "owner_bound": owner_bound,
        "credential_active": credential_active,
        "hold_verified": hold_verified,
        "candidate_integrity": candidate_integrity,
        "installation_not_suspended": not_suspended,
        "paper_safe_mode": paper_safe,
    }
    eligible = all(gates.values())
    reasons = []
    for key, label in (
        ("c1_approval", "Candidate changed"),
        ("compile_success", "Awaiting compile"),
        ("strategy_instance", "Awaiting installation"),
        ("owner_bound", "Installation ownership invalid"),
        ("credential_active", "Credential not generated"),
        ("hold_verified", "Awaiting HOLD"),
        ("candidate_integrity", "Candidate changed"),
        ("installation_not_suspended", "Installation suspended"),
        ("paper_safe_mode", "Paper-safe mode required"),
    ):
        if not gates[key] and label not in reasons:
            reasons.append(label)
    if setup.credential_revoked_at and "Credential revoked" not in reasons:
        reasons.insert(0, "Credential revoked")
    if compile_evidence and compile_evidence.result == "FAILURE":
        reasons = ["Compile failed"] + [item for item in reasons if item != "Awaiting compile"]
    return {
        "gates": gates,
        "paper_eligible": eligible,
        "live_eligible": False,
        "reasons": reasons,
        "instance": instance,
        "credential": credential,
        "context": context,
        "compile": compile_evidence,
    }


def readiness_for_setup(db, setup: models.TradingViewSetup) -> dict[str, Any]:
    """Shared engine/read API verdict for one C2 setup."""
    return _readiness(db, setup)


def _setup_package(
    setup: models.TradingViewSetup,
    context: dict[str, Any],
    *,
    credential: str = PLACEHOLDER,
) -> dict[str, Any]:
    mode = SETUP_TO_MODE[setup.setup_type]
    alert = {
        "credential": credential,
        "action": "HOLD",
        "signal_id": "{{UNIQUE_SIGNAL_ID}}",
        "signal_time": "{{ISO_8601_WITH_TIMEZONE}}",
        "strategy_version": context["candidate_version"].version,
        "comment": "NOVA C2 routing verification",
    }
    return {
        "strategy_name": context["strategy"].display_name,
        "instance_label": None,
        "mode": mode,
        "approved_pine": context["candidate"].content,
        "candidate_sha256": context["binding"]["candidate_sha256"],
        "webhook_url": WEBHOOK_PATH,
        "alert_message": json.dumps(alert, sort_keys=True, separators=(",", ":")),
        "credential_display": "one_time" if credential != PLACEHOLDER else "placeholder",
        "expected_hold_behavior": (
            "A valid HOLD verifies routing only. It creates no execution job, order, or position."
        ),
        "instructions": (
            "The owner manually installs this exact candidate in TradingView."
            if mode == SELF
            else "An administrator manually installs this exact candidate in the controlled TradingView environment."
        ),
    }


def _installation_public(
    db,
    setup: models.TradingViewSetup,
    *,
    include_admin: bool,
    include_source: bool,
) -> dict[str, Any]:
    readiness = _readiness(db, setup)
    context = readiness["context"]
    instance = readiness["instance"]
    credential = readiness["credential"]
    strategy = db.get(models.StrategyCatalog, instance.strategy_id) if instance else None
    version = db.get(models.StrategyVersion, setup.approved_version_id)
    payload = {
        "id": str(setup.id),
        "owner_user_id": str(setup.user_id),
        "conversion_id": str(setup.pine_conversion_request_id),
        "compile_evidence_id": str(setup.compile_evidence_id) if setup.compile_evidence_id else None,
        "strategy_id": str(instance.strategy_id) if instance else None,
        "strategy_name": strategy.display_name if strategy else "Unknown",
        "strategy_version_id": str(setup.approved_version_id),
        "strategy_version": version.version if version else None,
        "candidate_sha256": setup.approved_candidate_sha256,
        "source_sha256": setup.approved_source_sha256,
        "mode": SETUP_TO_MODE.get(setup.setup_type),
        "status": "PAPER_ELIGIBLE" if readiness["paper_eligible"] else setup.status,
        "strategy_instance_id": str(setup.strategy_instance_id),
        "instance_label": instance.label if instance else None,
        "instance_status": instance.status if instance else None,
        "execution_mode": instance.execution_mode if instance else None,
        "credential_status": (
            "ACTIVE"
            if credential
            else "REVOKED"
            if setup.credential_revoked_at
            else "NOT_GENERATED"
        ),
        "credential": (
            {
                "id": str(credential.id),
                "token_prefix": credential.token_prefix,
                "created_at": credential.created_at.isoformat(),
                "last_verified_at": (
                    setup.hold_verified_at.isoformat() if setup.hold_verified_at else None
                ),
            }
            if credential
            else None
        ),
        "hold_status": "VERIFIED" if setup.hold_verified_at else "AWAITING_HOLD",
        "hold_verified_at": setup.hold_verified_at.isoformat() if setup.hold_verified_at else None,
        "paper_eligible": readiness["paper_eligible"],
        "paper_eligible_at": (
            setup.paper_eligible_at.isoformat() if setup.paper_eligible_at else None
        ),
        "live_eligible": False,
        "gates": readiness["gates"],
        "blocking_reasons": readiness["reasons"],
        "suspended_at": setup.suspended_at.isoformat() if setup.suspended_at else None,
        "created_at": setup.created_at.isoformat(),
        "updated_at": setup.updated_at.isoformat(),
    }
    if include_source and context:
        package = _setup_package(setup, context)
        package["instance_label"] = instance.label if instance else None
        payload["setup_package"] = package
    if include_admin:
        payload["compile"] = _compile_public(readiness["compile"])
        payload["installed_by_user_id"] = (
            str(setup.installed_by_user_id) if setup.installed_by_user_id else None
        )
        payload["admin_notes"] = setup.admin_notes
    return payload


def create_installation(
    admin_id: uuid.UUID,
    conversion_id: uuid.UUID | str,
    owner_user_id: uuid.UUID,
    *,
    mode: str,
    instance_label: str,
) -> dict[str, Any]:
    _require_enabled()
    if mode not in MODE_TO_SETUP:
        raise C2Error("Installation mode must be MANAGED or SELF.", 422, "INVALID_MODE")
    try:
        with session_scope() as db:
            context = _approved_context(db, conversion_id, lock=True)
            compile_evidence = _compile_for(db, conversion_id, lock=True)
            if not _compile_matches(compile_evidence, context):
                raise C2Error(
                    "A successful exact-candidate compile is required.",
                    409,
                    "COMPILE_SUCCESS_REQUIRED",
                )
            owner = db.get(models.User, owner_user_id)
            if owner is None:
                raise C2Error("Installation owner was not found.", 404, "OWNER_NOT_FOUND")
            existing = _query_one(
                db,
                models.TradingViewSetup,
                models.TradingViewSetup.user_id == owner.id,
                models.TradingViewSetup.approved_version_id
                == context["candidate_version"].id,
                models.TradingViewSetup.pine_conversion_request_id
                == context["conversion"].id,
                lock=True,
            )
            if existing:
                return _installation_public(
                    db, existing, include_admin=True, include_source=True
                )
            now = _now()
            instance = models.StrategyInstance(
                user_id=owner.id,
                strategy_id=context["strategy"].id,
                strategy_version_id=context["candidate_version"].id,
                source_journey="PERSONAL_TRADINGVIEW",
                label=instance_label.strip(),
                status="ready",
                status_reason="C2 installation awaiting HOLD verification",
                execution_mode="signal_only",
                current_lots=1,
                verification_mode=False,
                approved_candidate_sha256=context["binding"]["candidate_sha256"],
                installation_mode=mode,
            )
            db.add(instance)
            db.flush()
            setup = models.TradingViewSetup(
                user_id=owner.id,
                strategy_instance_id=instance.id,
                approved_version_id=context["candidate_version"].id,
                pine_conversion_request_id=context["conversion"].id,
                compile_evidence_id=compile_evidence.id,
                approved_candidate_sha256=context["binding"]["candidate_sha256"],
                approved_source_sha256=context["binding"]["source_sha256"],
                approved_strategy_layer_sha256=context["binding"][
                    "strategy_layer_sha256"
                ],
                setup_type=MODE_TO_SETUP[mode],
                status="INSTALLATION_PENDING",
                installation_confirmed_at=now,
                installation_metadata={"c2": True, "mode": mode},
                installed_by_user_id=admin_id,
            )
            db.add(setup)
            db.flush()
            crud.add_audit_log(
                db,
                user_id=admin_id,
                action="STRATEGY_INSTALLATION_CREATED",
                metadata={
                    "actor_user_id": str(admin_id),
                    "owner_user_id": str(owner.id),
                    "strategy_id": str(context["strategy"].id),
                    "version_id": str(context["candidate_version"].id),
                    "instance_id": str(instance.id),
                    "installation_id": str(setup.id),
                    "candidate_sha_ref": context["binding"]["candidate_sha256"][:12],
                    "installation_mode": mode,
                    "prior_status": None,
                    "new_status": setup.status,
                },
            )
            return _installation_public(
                db, setup, include_admin=True, include_source=True
            )
    except IntegrityError as exc:
        raise C2Error(
            "An installation already exists for this owner and candidate.",
            409,
            "INSTALLATION_EXISTS",
        ) from exc


def _authorize_credential_action(
    setup: models.TradingViewSetup,
    actor_id: uuid.UUID,
    *,
    admin: bool,
) -> None:
    if not admin:
        if setup.user_id != actor_id:
            raise C2Error("Strategy installation not found.", 404, "NOT_FOUND")
        if SETUP_TO_MODE.get(setup.setup_type) != SELF:
            raise C2Error(
                "Managed credentials are available only to administrators.",
                403,
                "ADMIN_ACTION_REQUIRED",
            )


def _credential_payload(row, token: str, package: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "strategy_instance_id": str(row.strategy_instance_id),
        "token_prefix": row.token_prefix,
        "token": token,
        "created_at": row.created_at.isoformat(),
        "setup_package": package,
    }


def generate_credential(
    actor_id: uuid.UUID,
    installation_id: uuid.UUID | str,
    *,
    admin: bool,
) -> dict[str, Any]:
    _require_enabled()
    from app.services import strategy_instance_service as instances

    with session_scope() as db:
        setup = _get_setup(db, installation_id, lock=True)
        _authorize_credential_action(setup, actor_id, admin=admin)
        context = _approved_context(db, setup.pine_conversion_request_id, lock=True)
        if setup.suspended_at is not None:
            raise C2Error("Installation is suspended.", 409, "INSTALLATION_SUSPENDED")
        compile_evidence = db.get(
            models.TradingViewCompileEvidence, setup.compile_evidence_id
        )
        if not _compile_matches(compile_evidence, context):
            raise C2Error(
                "Compile evidence no longer matches the approved candidate.",
                409,
                "COMPILE_EVIDENCE_INVALID",
            )
        instance = _query_one(
            db,
            models.StrategyInstance,
            models.StrategyInstance.id == setup.strategy_instance_id,
            models.StrategyInstance.user_id == setup.user_id,
            lock=True,
        )
        if instance is None or instance.execution_mode not in SAFE_MODES:
            raise C2Error("Paper-safe StrategyInstance is required.", 409, "INSTANCE_INVALID")
        if _active_credential(db, instance.id, lock=True):
            raise C2Error(
                "An active credential already exists. Rotate it instead.",
                409,
                "CREDENTIAL_EXISTS",
            )
        credential, token = instances._issue_credential(db, instance)  # noqa: SLF001
        setup.current_credential_id = credential.id
        setup.credential_revoked_at = None
        setup.hold_signal_id = None
        setup.hold_verified_at = None
        setup.hold_credential_id = None
        setup.hold_webhook_event_id = None
        setup.paper_eligible_at = None
        setup.status = "AWAITING_HOLD"
        setup.blocking_reason = "Send a real TradingView HOLD alert."
        setup.updated_at = _now()
        action = "STRATEGY_CREDENTIAL_GENERATED"
        crud.add_audit_log(
            db,
            user_id=actor_id,
            action=action,
            metadata={
                "actor_user_id": str(actor_id),
                "owner_user_id": str(setup.user_id),
                "installation_id": str(setup.id),
                "instance_id": str(instance.id),
                "credential_id": str(credential.id),
                "installation_mode": SETUP_TO_MODE[setup.setup_type],
                "new_status": setup.status,
            },
        )
        package = _setup_package(setup, context, credential=token)
        package["instance_label"] = instance.label
        return _credential_payload(credential, token, package)


def rotate_credential(
    actor_id: uuid.UUID,
    installation_id: uuid.UUID | str,
    *,
    admin: bool,
) -> dict[str, Any]:
    _require_enabled()
    from app.services import strategy_instance_service as instances

    with session_scope() as db:
        setup = _get_setup(db, installation_id, lock=True)
        _authorize_credential_action(setup, actor_id, admin=admin)
        context = _approved_context(db, setup.pine_conversion_request_id, lock=True)
        instance = _query_one(
            db,
            models.StrategyInstance,
            models.StrategyInstance.id == setup.strategy_instance_id,
            models.StrategyInstance.user_id == setup.user_id,
            lock=True,
        )
        current = _active_credential(db, setup.strategy_instance_id, lock=True)
        if instance is None or current is None or current.id != setup.current_credential_id:
            raise C2Error("No active credential to rotate.", 404, "CREDENTIAL_NOT_FOUND")
        current.revoked_at = _now()
        current.revoked_reason = "c2_rotated"
        db.flush()
        replacement, token = instances._issue_credential(db, instance)  # noqa: SLF001
        current.replaced_by_id = replacement.id
        setup.current_credential_id = replacement.id
        setup.credential_revoked_at = None
        setup.hold_signal_id = None
        setup.hold_verified_at = None
        setup.hold_credential_id = None
        setup.hold_webhook_event_id = None
        setup.paper_eligible_at = None
        setup.status = "AWAITING_HOLD"
        setup.blocking_reason = "Credential rotated; send a new real HOLD alert."
        setup.updated_at = _now()
        crud.add_audit_log(
            db,
            user_id=actor_id,
            action="STRATEGY_CREDENTIAL_ROTATED",
            metadata={
                "actor_user_id": str(actor_id),
                "owner_user_id": str(setup.user_id),
                "installation_id": str(setup.id),
                "instance_id": str(instance.id),
                "old_credential_id": str(current.id),
                "new_credential_id": str(replacement.id),
                "new_status": setup.status,
            },
        )
        package = _setup_package(setup, context, credential=token)
        package["instance_label"] = instance.label
        return _credential_payload(replacement, token, package)


def revoke_credential(
    actor_id: uuid.UUID,
    installation_id: uuid.UUID | str,
    *,
    admin: bool,
) -> dict[str, Any]:
    _require_enabled()
    with session_scope() as db:
        setup = _get_setup(db, installation_id, lock=True)
        _authorize_credential_action(setup, actor_id, admin=admin)
        current = _active_credential(db, setup.strategy_instance_id, lock=True)
        if current is None or current.id != setup.current_credential_id:
            raise C2Error("No active credential to revoke.", 404, "CREDENTIAL_NOT_FOUND")
        now = _now()
        current.revoked_at = now
        current.revoked_reason = "c2_revoked"
        setup.credential_revoked_at = now
        setup.hold_signal_id = None
        setup.hold_verified_at = None
        setup.hold_credential_id = None
        setup.hold_webhook_event_id = None
        setup.paper_eligible_at = None
        setup.status = "CREDENTIAL_REVOKED"
        setup.blocking_reason = "Credential revoked."
        setup.updated_at = now
        crud.add_audit_log(
            db,
            user_id=actor_id,
            action="STRATEGY_CREDENTIAL_REVOKED",
            metadata={
                "actor_user_id": str(actor_id),
                "owner_user_id": str(setup.user_id),
                "installation_id": str(setup.id),
                "instance_id": str(setup.strategy_instance_id),
                "credential_id": str(current.id),
                "new_status": setup.status,
            },
        )
        return _installation_public(
            db, setup, include_admin=admin, include_source=True
        )


def suspend_installation(admin_id, installation_id, reason: str) -> dict[str, Any]:
    _require_enabled()
    safe_reason = _safe_text(reason, required=True)
    with session_scope() as db:
        setup = _get_setup(db, installation_id, lock=True)
        if setup.suspended_at is None:
            setup.suspended_at = _now()
            setup.paper_eligible_at = None
            setup.status = "INSTALLATION_SUSPENDED"
            setup.blocking_reason = safe_reason
            setup.updated_at = _now()
            crud.add_audit_log(
                db,
                user_id=admin_id,
                action="STRATEGY_INSTALLATION_SUSPENDED",
                metadata={
                    "actor_user_id": str(admin_id),
                    "owner_user_id": str(setup.user_id),
                    "installation_id": str(setup.id),
                    "instance_id": str(setup.strategy_instance_id),
                    "new_status": setup.status,
                },
            )
        return _installation_public(
            db, setup, include_admin=True, include_source=True
        )


def list_installations(
    actor_id: uuid.UUID,
    *,
    admin: bool,
    owner_user_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    with session_scope() as db:
        query = select(models.TradingViewSetup).where(
            models.TradingViewSetup.pine_conversion_request_id.is_not(None)
        )
        if admin:
            if owner_user_id is not None:
                query = query.where(models.TradingViewSetup.user_id == owner_user_id)
        else:
            query = query.where(models.TradingViewSetup.user_id == actor_id)
        rows = db.scalars(query.order_by(models.TradingViewSetup.updated_at.desc())).all()
        return [
            _installation_public(
                db,
                row,
                include_admin=admin,
                include_source=admin or SETUP_TO_MODE.get(row.setup_type) == SELF,
            )
            for row in rows
        ]


def get_installation(
    actor_id: uuid.UUID,
    installation_id: uuid.UUID | str,
    *,
    admin: bool,
) -> dict[str, Any]:
    with session_scope() as db:
        setup = (
            _get_setup(db, installation_id)
            if admin
            else _owned_setup(db, actor_id, installation_id)
        )
        return _installation_public(
            db,
            setup,
            include_admin=admin,
            include_source=admin or SETUP_TO_MODE.get(setup.setup_type) == SELF,
        )


def c2_setup_for_instance(db, instance_id) -> models.TradingViewSetup | None:
    return _query_one(
        db,
        models.TradingViewSetup,
        models.TradingViewSetup.strategy_instance_id == uuid.UUID(str(instance_id)),
        models.TradingViewSetup.pine_conversion_request_id.is_not(None),
        lock=False,
    )


def validate_webhook_hold(auth: dict[str, Any], action: str | None) -> bool:
    """Return True when a C2 installation authorizes this exact HOLD.

    False means "not a C2 instance"; the caller retains its legacy lifecycle.
    """
    if not settings.C2_TRADINGVIEW_INSTALLATION_ENABLED:
        return False
    with session_scope() as db:
        setup = c2_setup_for_instance(db, auth["instance_id"])
        if setup is None:
            return False
        if action != "HOLD":
            raise C2Error(
                "C2 installation accepts HOLD verification only.",
                409,
                "C2_HOLD_ONLY",
            )
        if (
            setup.user_id != uuid.UUID(auth["user_id"])
            or setup.strategy_instance_id != uuid.UUID(auth["instance_id"])
            or setup.current_credential_id != uuid.UUID(auth["credential_id"])
            or setup.suspended_at is not None
            or setup.status == "INSTALLATION_SUSPENDED"
            or auth.get("execution_mode") not in SAFE_MODES
        ):
            raise C2Error("Installation is not eligible for HOLD.", 409, "INSTALLATION_INVALID")
        credential = _active_credential(db, setup.strategy_instance_id)
        if credential is None or credential.id != setup.current_credential_id:
            raise C2Error("Credential is inactive.", 401, "INVALID_CREDENTIAL")
        instance = db.get(models.StrategyInstance, setup.strategy_instance_id)
        if (
            instance is None
            or instance.user_id != setup.user_id
            or instance.strategy_version_id != setup.approved_version_id
            or instance.approved_candidate_sha256 != setup.approved_candidate_sha256
            or instance.installation_mode != SETUP_TO_MODE.get(setup.setup_type)
            or instance.execution_mode not in SAFE_MODES
        ):
            raise C2Error(
                "Strategy instance binding is invalid.",
                409,
                "INSTANCE_BINDING_INVALID",
            )
        context = _approved_context(db, setup.pine_conversion_request_id, lock=False)
        evidence = db.get(models.TradingViewCompileEvidence, setup.compile_evidence_id)
        if not _compile_matches(evidence, context):
            raise C2Error(
                "Compile evidence is invalid.",
                409,
                "COMPILE_EVIDENCE_INVALID",
            )
        if (
            setup.approved_version_id != context["candidate_version"].id
            or setup.approved_candidate_sha256 != context["binding"]["candidate_sha256"]
        ):
            raise C2Error(
                "Candidate integrity changed.",
                409,
                "CANDIDATE_INTEGRITY_INVALID",
            )
        return True


def record_hold_from_webhook(
    auth: dict[str, Any],
    *,
    signal_id: str,
    webhook_event_id: str | uuid.UUID | None,
) -> dict[str, Any] | None:
    """Pin one already-committed HOLD to its C2 installation, idempotently."""
    if not settings.C2_TRADINGVIEW_INSTALLATION_ENABLED:
        return None
    with session_scope() as db:
        setup = _query_one(
            db,
            models.TradingViewSetup,
            models.TradingViewSetup.strategy_instance_id
            == uuid.UUID(auth["instance_id"]),
            models.TradingViewSetup.pine_conversion_request_id.is_not(None),
            lock=True,
        )
        if setup is None:
            return None
        if (
            setup.user_id != uuid.UUID(auth["user_id"])
            or setup.current_credential_id != uuid.UUID(auth["credential_id"])
            or setup.suspended_at is not None
        ):
            return {"verified": False, "reason": "INSTALLATION_INVALID"}
        if (
            setup.hold_signal_id == signal_id
            and setup.hold_credential_id == uuid.UUID(auth["credential_id"])
        ):
            readiness = _readiness(db, setup)
            return {"verified": readiness["paper_eligible"], "duplicate": True}

        context = _approved_context(db, setup.pine_conversion_request_id, lock=True)
        evidence = db.get(models.TradingViewCompileEvidence, setup.compile_evidence_id)
        if not _compile_matches(evidence, context):
            return {"verified": False, "reason": "COMPILE_EVIDENCE_INVALID"}
        signal = _query_one(
            db,
            models.StrategySignal,
            models.StrategySignal.strategy_name
            == f"instance:{setup.strategy_instance_id}",
            models.StrategySignal.signal_id == signal_id,
            models.StrategySignal.status == "completed",
        )
        summary = signal.result_summary if signal and isinstance(signal.result_summary, dict) else {}
        if summary.get("action") != "HOLD" or summary.get("reason") != "HOLD":
            return {"verified": False, "reason": "HOLD_EVIDENCE_INVALID"}
        event = (
            db.get(models.WebhookEvent, uuid.UUID(str(webhook_event_id)))
            if webhook_event_id
            else _query_one(
                db,
                models.WebhookEvent,
                models.WebhookEvent.provider
                == f"instance-webhook:{setup.strategy_instance_id}",
                models.WebhookEvent.event_id == signal_id,
            )
        )
        metadata = event.event_metadata if event and isinstance(event.event_metadata, dict) else {}
        if (
            event is None
            or event.user_id != setup.user_id
            or metadata.get("credential_id") != auth["credential_id"]
            or metadata.get("strategy_instance_id") != auth["instance_id"]
            or metadata.get("action") != "HOLD"
        ):
            return {"verified": False, "reason": "WEBHOOK_EVIDENCE_INVALID"}

        now = _now()
        setup.hold_signal_id = signal_id
        setup.hold_verified_at = now
        setup.hold_credential_id = uuid.UUID(auth["credential_id"])
        setup.hold_webhook_event_id = event.id
        setup.status = "HOLD_VERIFIED"
        setup.blocking_reason = None
        setup.updated_at = now
        crud.add_audit_log(
            db,
            user_id=setup.user_id,
            action="STRATEGY_HOLD_VERIFIED",
            metadata={
                "owner_user_id": str(setup.user_id),
                "installation_id": str(setup.id),
                "instance_id": str(setup.strategy_instance_id),
                "credential_id": auth["credential_id"],
                "candidate_sha_ref": setup.approved_candidate_sha256[:12],
                "webhook_event_id": str(event.id),
                "new_status": setup.status,
            },
        )
        readiness = _readiness(db, setup)
        if readiness["paper_eligible"]:
            if setup.paper_eligible_at is None:
                setup.paper_eligible_at = now
                crud.add_audit_log(
                    db,
                    user_id=setup.user_id,
                    action="STRATEGY_PAPER_ELIGIBLE",
                    metadata={
                        "owner_user_id": str(setup.user_id),
                        "installation_id": str(setup.id),
                        "instance_id": str(setup.strategy_instance_id),
                        "candidate_sha_ref": setup.approved_candidate_sha256[:12],
                        "live_eligible": False,
                        "new_status": "PAPER_ELIGIBLE",
                    },
                )
            setup.status = "PAPER_ELIGIBLE"
        return {
            "verified": readiness["paper_eligible"],
            "installation_id": str(setup.id),
            "paper_eligible": readiness["paper_eligible"],
            "live_eligible": False,
        }
