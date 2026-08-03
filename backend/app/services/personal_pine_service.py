"""Owner-scoped immutable Pine source, validation and admin review workflow."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any

from sqlalchemy import delete, func, or_, select, update

from app.config import settings
from app.db import crud, models
from app.db.engine import session_scope
from app.services import pine_semantic_analysis_persistence, pine_validation
from app.services.strategy_instance_evidence_cascade import (
    cascade_delete_evidence_for_strategy_instances,
    cascade_delete_pine_semantic_analysis_for_artifacts,
)

PINE_ARTIFACT = "pine_script"
SOURCE_JOURNEY = "nova_hosted_personal"
EXECUTION_KIND = "nova_runtime"
PAYLOAD_SPEC_VERSION = "nova.pine.v1"
CONTRACT_VERSION = pine_validation.CONTRACT_VERSION
REVIEW_STATUSES = {"submitted", "under_review", "changes_requested", "approved", "rejected"}
PACKAGE_SOURCE_ERROR = "The selected Pine source could not be verified. Create a new immutable version and retry."


class PineWorkflowError(ValueError):
    def __init__(self, message: str, status_code: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(source: str) -> str:
    source = pine_validation.canonicalize_source(source)
    try:
        encoded = source.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PineWorkflowError("Pine source must be valid UTF-8 text.", 422, "INVALID_UTF8") from exc
    if len(encoded) > max(int(settings.PERSONAL_PINE_MAX_SOURCE_BYTES), 1):
        raise PineWorkflowError("Pine source exceeds the configured size limit.", 413, "SOURCE_TOO_LARGE")
    if "\x00" in source:
        raise PineWorkflowError("Pine source must not contain null bytes.", 422, "BINARY_SOURCE")
    if pine_validation.contains_credential_like_text(source):
        raise PineWorkflowError(
            "Credential-like content is not accepted in Pine source. Remove it and rotate any exposed credential.",
            422,
            "CREDENTIAL_IN_SOURCE",
        )
    return source


def _filename(value: str) -> str:
    value = (value or "").strip()
    if not value or PurePath(value).name != value or "/" in value or "\\" in value:
        raise PineWorkflowError("Filename must not contain a path.", 422, "INVALID_FILENAME")
    if not value.lower().endswith((".pine", ".txt")):
        raise PineWorkflowError("Only .pine and .txt files are accepted.", 422, "INVALID_FILE_TYPE")
    if any(ord(char) < 32 for char in value):
        raise PineWorkflowError("Filename contains unsupported characters.", 422, "INVALID_FILENAME")
    return value[:120]


def _source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _owned_strategy(db, user_id: uuid.UUID, strategy_id: uuid.UUID | str, *, lock: bool = False):
    query = select(models.StrategyCatalog).where(
        models.StrategyCatalog.id == uuid.UUID(str(strategy_id)),
        models.StrategyCatalog.owner_user_id == user_id,
        models.StrategyCatalog.owner_type == "personal",
        models.StrategyCatalog.visibility == "private",
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise PineWorkflowError("Personal Pine strategy not found.", 404, "NOT_FOUND")
    return row


def _owned_version(
    db,
    user_id: uuid.UUID,
    strategy_id: uuid.UUID | str,
    version_id: uuid.UUID | str,
    *,
    lock: bool = False,
):
    strategy = _owned_strategy(db, user_id, strategy_id)
    query = select(models.StrategyVersion).where(
        models.StrategyVersion.id == uuid.UUID(str(version_id)),
        models.StrategyVersion.strategy_id == strategy.id,
    )
    version = db.scalar(query.with_for_update() if lock else query)
    if version is None:
        raise PineWorkflowError("Pine strategy version not found.", 404, "NOT_FOUND")
    return strategy, version


def _artifact(db, version_id: uuid.UUID):
    row = db.scalar(select(models.StrategySourceArtifact).where(
        models.StrategySourceArtifact.strategy_version_id == version_id,
        models.StrategySourceArtifact.artifact_type == PINE_ARTIFACT,
    ))
    if row is None:
        raise PineWorkflowError("Pine source artifact not found.", 404, "SOURCE_NOT_FOUND")
    return row


def _latest_report(db, version_id: uuid.UUID):
    return db.scalar(
        select(models.StrategyValidationReport)
        .where(models.StrategyValidationReport.strategy_version_id == version_id)
        .order_by(models.StrategyValidationReport.executed_at.desc())
    )


def _audit(db, user_id: uuid.UUID, action: str, **metadata: Any) -> None:
    try:
        crud.add_audit_log(db, user_id=user_id, action=action, metadata=metadata)
    except Exception:
        pass


def _version_public(version: models.StrategyVersion, report=None) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "strategy_id": str(version.strategy_id),
        "version": version.version,
        "status": version.status,
        "source_sha256": version.source_sha256,
        "pine_contract_version": version.pine_contract_version,
        "payload_spec_version": version.payload_spec_version,
        "source_journey": version.source_journey,
        "execution_kind": version.execution_kind,
        "changelog": version.changelog,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "approved_at": version.approved_at.isoformat() if version.approved_at else None,
        "validation": _report_public(report) if report else None,
    }


def _strategy_public(strategy: models.StrategyCatalog, *, latest=None, version_count: int | None = None) -> dict[str, Any]:
    return {
        "id": str(strategy.id),
        "name": strategy.display_name,
        "description": strategy.description,
        "visibility": strategy.visibility,
        "status": strategy.status,
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
        "updated_at": strategy.updated_at.isoformat() if strategy.updated_at else None,
        "version_count": version_count,
        "latest_version": _version_public(latest) if latest else None,
    }


def _report_public(report: models.StrategyValidationReport) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "status": report.status.upper(),
        "validator_version": report.validator_version,
        "contract_version": report.contract_version,
        "source_sha256": report.source_sha256,
        "validation_engine": report.validation_engine,
        "started_at": report.started_at.isoformat() if report.started_at else None,
        "completed_at": report.executed_at.isoformat() if report.executed_at else None,
        "duration_ms": report.duration_ms,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "info_count": report.info_count,
        "eligible_for_review": report.eligible_for_review,
        "source_changed_after_validation": False,
        "findings": report.findings,
    }


def _next_version(db, strategy_id: uuid.UUID) -> str:
    count = db.scalar(select(func.count(models.StrategyVersion.id)).where(
        models.StrategyVersion.strategy_id == strategy_id
    )) or 0
    return f"1.0.{count}"


def _create_version(db, user_id: uuid.UUID, strategy, source: str, filename: str, changelog: str | None):
    source = _canonical(source)
    filename = _filename(filename)
    digest = _source_hash(source)
    existing = db.scalar(select(models.StrategyVersion).where(
        models.StrategyVersion.strategy_id == strategy.id,
        models.StrategyVersion.source_sha256 == digest,
        models.StrategyVersion.pine_contract_version == CONTRACT_VERSION,
    ))
    if existing is not None:
        return existing, False
    version = models.StrategyVersion(
        strategy_id=strategy.id,
        version=_next_version(db, strategy.id),
        payload_spec_version=PAYLOAD_SPEC_VERSION,
        source_journey=SOURCE_JOURNEY,
        status="draft",
        execution_kind=EXECUTION_KIND,
        changelog=(changelog or "").strip() or None,
        source_sha256=digest,
        pine_contract_version=CONTRACT_VERSION,
        created_by_user_id=user_id,
    )
    db.add(version)
    db.flush()
    db.add(models.StrategySourceArtifact(
        strategy_version_id=version.id,
        artifact_type=PINE_ARTIFACT,
        content=source,
        content_sha256=digest,
        submitted_by_user_id=user_id,
        conversion_method="external_manual",
        original_filename=filename,
    ))
    _audit(db, user_id, "PERSONAL_PINE_VERSION_CREATED", strategy_id=str(strategy.id), version_id=str(version.id), source_sha256=digest)
    return version, True


def create_strategy(user_id: uuid.UUID, *, name: str, source: str, filename: str, description: str | None = None):
    name = name.strip()
    if not name:
        raise PineWorkflowError("Strategy name is required.", 422, "NAME_REQUIRED")
    with session_scope() as db:
        strategy = models.StrategyCatalog(
            code=f"pine-{uuid.uuid4().hex[:16]}",
            display_name=name,
            owner_type="personal",
            owner_user_id=user_id,
            visibility="private",
            status="active",
            description=(description or "").strip() or None,
        )
        db.add(strategy)
        db.flush()
        version, _ = _create_version(db, user_id, strategy, source, filename, "Initial imported Pine source")
        return {"strategy": _strategy_public(strategy, latest=version, version_count=1), "version": _version_public(version)}


def create_version(user_id: uuid.UUID, strategy_id, *, source: str, filename: str, changelog: str | None = None):
    with session_scope() as db:
        strategy = _owned_strategy(db, user_id, strategy_id, lock=True)
        version, created = _create_version(db, user_id, strategy, source, filename, changelog)
        return {"version": _version_public(version, _latest_report(db, version.id)), "created": created}


def list_strategies(user_id: uuid.UUID, *, limit: int = 50, offset: int = 0):
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    with session_scope() as db:
        base = select(models.StrategyCatalog).where(
            models.StrategyCatalog.owner_user_id == user_id,
            models.StrategyCatalog.owner_type == "personal",
            models.StrategyCatalog.visibility == "private",
        )
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(base.order_by(models.StrategyCatalog.updated_at.desc()).limit(limit).offset(offset)).all()
        payload = []
        for row in rows:
            versions = db.scalars(select(models.StrategyVersion).where(
                models.StrategyVersion.strategy_id == row.id
            ).order_by(models.StrategyVersion.created_at.desc())).all()
            payload.append(_strategy_public(row, latest=versions[0] if versions else None, version_count=len(versions)))
        return {"strategies": payload, "total": total, "limit": limit, "offset": offset}


def _review_history(db, version_id: uuid.UUID) -> list[dict[str, Any]]:
    """Owner-facing review decisions: the decision and reviewer's note, never
    the reviewer's identity or the admin-only source/audit-read surface."""
    events = db.scalars(select(models.StrategyAdminReview).where(
        models.StrategyAdminReview.strategy_version_id == version_id
    ).order_by(models.StrategyAdminReview.reviewed_at.asc())).all()
    return [{
        "decision": event.decision,
        "note": event.notes,
        "previous_status": event.previous_status,
        "new_status": event.new_status,
        "reviewed_at": event.reviewed_at.isoformat() if event.reviewed_at else None,
    } for event in events]


def get_strategy(user_id: uuid.UUID, strategy_id):
    with session_scope() as db:
        strategy = _owned_strategy(db, user_id, strategy_id)
        versions = db.scalars(select(models.StrategyVersion).where(
            models.StrategyVersion.strategy_id == strategy.id
        ).order_by(models.StrategyVersion.created_at.desc())).all()
        version_payloads = []
        for version in versions:
            payload = _version_public(version, _latest_report(db, version.id))
            payload["review_history"] = _review_history(db, version.id)
            version_payloads.append(payload)
        return {
            "strategy": _strategy_public(strategy, latest=versions[0] if versions else None, version_count=len(versions)),
            "versions": version_payloads,
        }


def get_version(user_id: uuid.UUID, strategy_id, version_id):
    with session_scope() as db:
        _, version = _owned_version(db, user_id, strategy_id, version_id)
        artifact = _artifact(db, version.id)
        payload = _version_public(version, _latest_report(db, version.id))
        payload["filename"] = artifact.original_filename
        payload["review_history"] = _review_history(db, version.id)
        return payload


def get_source(user_id: uuid.UUID, strategy_id, version_id):
    with session_scope() as db:
        strategy, version = _owned_version(db, user_id, strategy_id, version_id)
        artifact = _artifact(db, version.id)
        return {
            "strategy_name": strategy.display_name,
            "version": version.version,
            "filename": artifact.original_filename or "strategy.pine",
            "source": artifact.content,
            "source_sha256": artifact.content_sha256,
            "approved": version.status == "approved",
        }


def get_package_source(user_id: uuid.UUID, strategy_id, version_id):
    """Return only a verified, owner-scoped immutable version artifact."""
    with session_scope() as db:
        strategy, version = _owned_version(db, user_id, strategy_id, version_id)
        artifact = _artifact(db, version.id)
        source = artifact.content
        digest = _source_hash(source)
        if not source.strip() or digest != artifact.content_sha256 or digest != version.source_sha256:
            raise PineWorkflowError(PACKAGE_SOURCE_ERROR, 409, "PINE_PACKAGE_SOURCE_INVALID")
        return {
            "strategy_name": strategy.display_name,
            "version": version.version,
            "filename": artifact.original_filename or "strategy.pine",
            "source": source,
            "source_sha256": digest,
            "approved": version.status == "approved",
        }


def _persist_semantic_provenance(db, user_id: uuid.UUID, version) -> str | None:
    """R1B-1: flag-gated immutable semantic-analysis provenance (evidence only).

    With R1B_PINE_ANALYSIS_PERSISTENCE off (the default) this performs no
    database query and no insert, and the qualification response is unchanged.
    On persistence failure the whole validation transaction is aborted with a
    safe error so qualification provenance is never falsely recorded.
    """
    if not settings.R1B_PINE_ANALYSIS_PERSISTENCE:
        return None
    artifact = _artifact(db, version.id)
    try:
        row = pine_semantic_analysis_persistence.persist_semantic_analysis(
            db, artifact, artifact.content, owner_user_id=user_id
        )
    except pine_semantic_analysis_persistence.SemanticAnalysisPersistenceError as exc:
        raise PineWorkflowError(
            "Semantic-analysis provenance could not be recorded, so this validation was not saved. Retry later.",
            503,
            "SEMANTIC_PROVENANCE_UNAVAILABLE",
        ) from exc
    return str(row.id)


def validate_version(user_id: uuid.UUID, strategy_id, version_id):
    with session_scope() as db:
        _, version = _owned_version(db, user_id, strategy_id, version_id, lock=True)
        if version.status not in {"draft", "validation_failed", "ready_for_review"}:
            raise PineWorkflowError(
                "Submitted and reviewed source versions are immutable; create a new version to revalidate.",
                409,
                "VERSION_IMMUTABLE",
            )
        artifact = _artifact(db, version.id)
        source, digest = artifact.content, artifact.content_sha256
    started = _now()
    try:
        result = pine_validation.validate_source(source, mode=pine_validation.detect_source_type(source))
    except Exception:
        # The source is untrusted. Persist a bounded, non-sensitive failure and
        # never leak parser internals or source text through an exception.
        result = {
            "validator_version": pine_validation.VALIDATOR_VERSION,
            "contract_version": CONTRACT_VERSION,
            "validation_engine": pine_validation.VALIDATION_ENGINE,
            "status": "VALIDATOR_ERROR",
            "error_count": 1,
            "warning_count": 0,
            "info_count": 0,
            "eligible_for_review": False,
            "findings": [{
                "code": "VALIDATOR_ERROR",
                "severity": "ERROR",
                "title": "Static validation could not complete",
                "explanation": "The validator safely rejected this input.",
                "remediation": "Check the source encoding and syntax, then create a corrected version.",
                "blocks_review": True,
                "line": None,
                "column": None,
                "excerpt": None,
            }],
            "duration_ms": 0,
        }
    with session_scope() as db:
        _, version = _owned_version(db, user_id, strategy_id, version_id, lock=True)
        semantic_analysis_id = _persist_semantic_provenance(db, user_id, version)
        existing = db.scalar(select(models.StrategyValidationReport).where(
            models.StrategyValidationReport.strategy_version_id == version.id,
            models.StrategyValidationReport.stage == "compatibility",
            models.StrategyValidationReport.validator_version == pine_validation.VALIDATOR_VERSION,
            models.StrategyValidationReport.contract_version == CONTRACT_VERSION,
            models.StrategyValidationReport.source_sha256 == digest,
        ))
        if existing is not None:
            reused = {"report": _report_public(existing), "reused": True, "version": _version_public(version, existing)}
            if semantic_analysis_id is not None:
                reused["semantic_analysis_id"] = semantic_analysis_id
            return reused
        report = models.StrategyValidationReport(
            strategy_version_id=version.id,
            stage="compatibility",
            status=result["status"].lower(),
            findings=result["findings"],
            validator_version=result["validator_version"],
            contract_version=result["contract_version"],
            source_sha256=digest,
            validation_engine=result["validation_engine"],
            started_at=started,
            error_count=result["error_count"],
            warning_count=result["warning_count"],
            info_count=result["info_count"],
            eligible_for_review=result["eligible_for_review"],
            duration_ms=result["duration_ms"],
        )
        db.add(report)
        version.status = "ready_for_review" if result["eligible_for_review"] else "validation_failed"
        version.updated_at = _now()
        db.flush()
        _audit(db, user_id, "PERSONAL_PINE_VALIDATED", strategy_id=str(strategy_id), version_id=str(version.id), report_id=str(report.id), source_sha256=digest, status=report.status)
        response = {"report": _report_public(report), "reused": False, "version": _version_public(version, report)}
        if semantic_analysis_id is not None:
            response["semantic_analysis_id"] = semantic_analysis_id
        return response


def get_validation(user_id: uuid.UUID, strategy_id, version_id):
    with session_scope() as db:
        _, version = _owned_version(db, user_id, strategy_id, version_id)
        report = _latest_report(db, version.id)
        if report is None:
            raise PineWorkflowError("Validation report not found.", 404, "REPORT_NOT_FOUND")
        return {"report": _report_public(report)}


def submit_version(user_id: uuid.UUID, strategy_id, version_id):
    with session_scope() as db:
        _, version = _owned_version(db, user_id, strategy_id, version_id, lock=True)
        if _artifact(db, version.id).conversion_method == "ai_conversion_pending":
            raise PineWorkflowError("Accept the AI-generated candidate before submitting it.", 409, "CANDIDATE_NOT_ACCEPTED")
        report = _latest_report(db, version.id)
        if version.status != "ready_for_review" or report is None or not report.eligible_for_review:
            raise PineWorkflowError("Only a passing exact source version can be submitted.", 409, "NOT_READY_FOR_REVIEW")
        if report.source_sha256 != version.source_sha256 or report.contract_version != CONTRACT_VERSION:
            raise PineWorkflowError("Validation does not match this source version.", 409, "VALIDATION_STALE")
        version.status = "submitted"
        version.updated_at = _now()
        _audit(db, user_id, "PERSONAL_PINE_SUBMITTED", strategy_id=str(strategy_id), version_id=str(version.id), report_id=str(report.id), source_sha256=version.source_sha256)
        return {"version": _version_public(version, report)}


def delete_strategy(user_id: uuid.UUID, strategy_id):
    """Withdraw an owned strategy and every draft version on it.

    Only reachable while no version has ever been approved and no trading
    instance links to it — an approved/linked strategy is not a "draft" and
    must never be deletable out from under a running setup.
    """
    with session_scope() as db:
        strategy = _owned_strategy(db, user_id, strategy_id, lock=True)
        versions = db.scalars(select(models.StrategyVersion).where(
            models.StrategyVersion.strategy_id == strategy.id
        )).all()
        if any(v.status == "approved" for v in versions):
            raise PineWorkflowError(
                "A strategy with an approved version cannot be withdrawn.",
                409,
                "APPROVED_VERSION_EXISTS",
            )
        linked = db.scalar(select(models.StrategyInstance.id).where(
            models.StrategyInstance.strategy_id == strategy.id
        ))
        if linked is not None:
            raise PineWorkflowError(
                "This strategy is linked to a trading instance and cannot be withdrawn.",
                409,
                "INSTANCE_LINKED",
            )
        version_ids = [v.id for v in versions]
        # input_version_id is ondelete="RESTRICT" (preserves conversion-request
        # provenance), so a submitted-for-conversion strategy would otherwise
        # hit an unhandled IntegrityError/500 here instead of a clean error.
        has_conversion_request = bool(version_ids) and db.scalar(
            select(models.PineConversionRequest.id).where(
                models.PineConversionRequest.input_version_id.in_(version_ids)
            )
        ) is not None
        if has_conversion_request:
            raise PineWorkflowError(
                "This strategy has been submitted for admin conversion and cannot be withdrawn.",
                409,
                "CONVERSION_REQUEST_EXISTS",
            )
        _audit(db, user_id, "PERSONAL_PINE_STRATEGY_DELETED", strategy_id=str(strategy.id))
        db.delete(strategy)
        return {"deleted": True, "strategy_id": str(strategy.id)}


def force_delete_strategy(
    actor_id: uuid.UUID,
    strategy_id: uuid.UUID | str,
    *,
    is_admin: bool,
) -> dict[str, Any]:
    """Permanently delete a strategy and everything under it -- approved
    versions, publish/review history, conversion requests, compile
    evidence, instances, positions, signals -- regardless of state. No
    undo, unlike unpublish_shared(). An admin may force-delete any
    strategy (personal or nova_shared); a non-admin may only force-delete
    their own personal strategy.

    The one guardrail this keeps: a strategy with a currently RUNNING
    engine instance cannot be force-deleted out from under it (would risk
    an orphaned broker position with no NOVA record left to reconcile it)
    -- stop the engine first, then delete.
    """
    with session_scope() as db:
        query = select(models.StrategyCatalog).where(
            models.StrategyCatalog.id == uuid.UUID(str(strategy_id))
        )
        if not is_admin:
            query = query.where(
                models.StrategyCatalog.owner_user_id == actor_id,
                models.StrategyCatalog.owner_type == "personal",
                models.StrategyCatalog.visibility == "private",
            )
        strategy = db.scalar(query.with_for_update())
        if strategy is None:
            raise PineWorkflowError("Strategy not found.", 404, "NOT_FOUND")

        instances = list(db.scalars(
            select(models.StrategyInstance).where(models.StrategyInstance.strategy_id == strategy.id)
        ))
        if any(instance.status == "active" for instance in instances):
            raise PineWorkflowError(
                "This strategy has a running engine instance. Stop it before deleting.",
                409,
                "INSTANCE_RUNNING",
            )
        instance_ids = [instance.id for instance in instances]

        version_ids = list(db.scalars(
            select(models.StrategyVersion.id).where(models.StrategyVersion.strategy_id == strategy.id)
        ))
        conversion_request_ids = list(db.scalars(
            select(models.PineConversionRequest.id).where(models.PineConversionRequest.strategy_id == strategy.id)
        ))
        code = strategy.code
        counts = {
            "instances": len(instance_ids),
            "versions": len(version_ids),
            "conversion_requests": len(conversion_request_ids),
        }

        # Deepest-first, entirely explicit -- SQLite (used in tests) doesn't
        # enforce FK actions unless PRAGMA foreign_keys=ON is set per
        # connection, which this codebase doesn't do, so relying on ON
        # DELETE CASCADE/SET NULL here would be untested and silently wrong
        # the moment it actually mattered. Every table is deleted explicitly
        # instead, verified against Postgres's real constraint graph.
        if instance_ids:
            position_ids = list(db.scalars(
                select(models.StrategyInstancePosition.id).where(
                    models.StrategyInstancePosition.strategy_instance_id.in_(instance_ids)
                )
            ))
            revision_ids = list(db.scalars(
                select(models.StrategyConfigurationRevision.id).where(
                    models.StrategyConfigurationRevision.strategy_instance_id.in_(instance_ids)
                )
            ))
            if revision_ids:
                # Durable history rows that outlive a single run/trade/job --
                # never deleted, just detached from the revision being removed.
                db.execute(update(models.UserRun).where(
                    models.UserRun.configuration_revision_id.in_(revision_ids)
                ).values(configuration_revision_id=None, configuration_revision=None))
                db.execute(update(models.StrategyExecutionJob).where(
                    models.StrategyExecutionJob.configuration_revision_id.in_(revision_ids)
                ).values(configuration_revision_id=None, configuration_revision=None))
                db.execute(update(models.PortfolioTrade).where(
                    models.PortfolioTrade.configuration_revision_id.in_(revision_ids)
                ).values(configuration_revision_id=None))
                db.execute(update(models.StrategyConfigurationRevision).where(
                    models.StrategyConfigurationRevision.supersedes_revision_id.in_(revision_ids)
                ).values(supersedes_revision_id=None))
            if position_ids:
                db.execute(delete(models.PositionEvent).where(
                    models.PositionEvent.position_id.in_(position_ids)
                ))
            # Mirrors the schema's own cascade-delete for R1B evidence rows tied to
            # these strategy instances -- see strategy_instance_evidence_cascade.py
            # for why this lives in its own narrowly-scoped module.
            cascade_delete_evidence_for_strategy_instances(db, instance_ids)
            db.execute(update(models.UserEngineConfig).where(
                models.UserEngineConfig.selected_strategy_instance_id.in_(instance_ids)
            ).values(selected_strategy_instance_id=None, selected_configuration_revision_id=None))
            db.execute(delete(models.EngineStartOperation).where(
                models.EngineStartOperation.strategy_instance_id.in_(instance_ids)
            ))
            db.execute(delete(models.StrategyInstancePosition).where(
                models.StrategyInstancePosition.strategy_instance_id.in_(instance_ids)
            ))
            db.execute(delete(models.StrategyInstanceWebhookCredential).where(
                models.StrategyInstanceWebhookCredential.strategy_instance_id.in_(instance_ids)
            ))
            db.execute(delete(models.StrategyConfigurationRevision).where(
                models.StrategyConfigurationRevision.strategy_instance_id.in_(instance_ids)
            ))
            db.execute(delete(models.TradingViewSetup).where(
                models.TradingViewSetup.strategy_instance_id.in_(instance_ids)
            ))
            db.execute(delete(models.StrategyInstance).where(
                models.StrategyInstance.id.in_(instance_ids)
            ))
        if version_ids:
            db.execute(update(models.UserRun).where(
                models.UserRun.strategy_version_id.in_(version_ids)
            ).values(strategy_version_id=None))
            db.execute(delete(models.EngineStartOperation).where(
                models.EngineStartOperation.strategy_version_id.in_(version_ids)
            ))
            db.execute(delete(models.TradingViewCompileEvidence).where(
                or_(
                    models.TradingViewCompileEvidence.pine_conversion_request_id.in_(conversion_request_ids),
                    models.TradingViewCompileEvidence.candidate_version_id.in_(version_ids),
                )
            ))
            db.execute(delete(models.PineUserAcceptance).where(
                or_(
                    models.PineUserAcceptance.original_version_id.in_(version_ids),
                    models.PineUserAcceptance.candidate_version_id.in_(version_ids),
                )
            ))
            db.execute(delete(models.PinePromptQualificationTrial).where(
                or_(
                    models.PinePromptQualificationTrial.original_version_id.in_(version_ids),
                    models.PinePromptQualificationTrial.candidate_version_id.in_(version_ids),
                )
            ))
            artifact_ids = list(db.scalars(
                select(models.StrategySourceArtifact.id).where(
                    models.StrategySourceArtifact.strategy_version_id.in_(version_ids)
                )
            ))
            # Mirrors the schema's own cascade-delete for Pine analysis evidence
            # tied to these source artifacts -- see strategy_instance_evidence_cascade.py.
            cascade_delete_pine_semantic_analysis_for_artifacts(db, artifact_ids)
            db.execute(delete(models.StrategySourceArtifact).where(
                models.StrategySourceArtifact.strategy_version_id.in_(version_ids)
            ))
            db.execute(delete(models.StrategyValidationReport).where(
                models.StrategyValidationReport.strategy_version_id.in_(version_ids)
            ))
            db.execute(delete(models.StrategyAdminReview).where(
                models.StrategyAdminReview.strategy_version_id.in_(version_ids)
            ))
        if conversion_request_ids:
            db.execute(delete(models.PineConversionRequest).where(
                models.PineConversionRequest.id.in_(conversion_request_ids)
            ))
        if version_ids:
            db.execute(delete(models.StrategyVersion).where(models.StrategyVersion.id.in_(version_ids)))

        subscriber_count = 0
        if code:
            subscriber_count = db.scalar(
                select(func.count()).select_from(models.StrategySubscription).where(
                    models.StrategySubscription.strategy_name == code
                )
            ) or 0
            db.execute(delete(models.StrategySubscription).where(
                models.StrategySubscription.strategy_name == code
            ))

        _audit(
            db,
            actor_id,
            "STRATEGY_FORCE_DELETED",
            strategy_id=str(strategy.id),
            catalog_code=code,
            owner_type=strategy.owner_type,
            visibility=strategy.visibility,
            is_admin=is_admin,
            **counts,
            deleted_subscriptions=subscriber_count,
        )
        db.delete(strategy)
        return {"deleted": True, "strategy_id": str(strategy.id), **counts, "deleted_subscriptions": subscriber_count}


def update_strategy_metadata(
    actor_id: uuid.UUID,
    strategy_id: uuid.UUID | str,
    *,
    is_admin: bool,
    display_name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Rename/redescribe a strategy -- for a published nova_shared strategy
    this is visible to every user immediately, same live-read pattern as
    publish_as_shared(). Does not touch catalog_code: that's the live
    webhook path (/api/webhook/strategy/{code}) for every subscriber's
    TradingView alert, so changing it is a separate, deliberate action
    (publish_as_shared's own conflict-checked flow), not a casual rename.
    An admin may edit any strategy; a non-admin may only edit their own
    personal strategy.
    """
    if display_name is None and description is None:
        raise PineWorkflowError("Nothing to update.", 422, "NO_FIELDS_GIVEN")
    if display_name is not None:
        display_name = display_name.strip()
        if not display_name:
            raise PineWorkflowError("Display name cannot be empty.", 422, "DISPLAY_NAME_REQUIRED")
        if len(display_name) > 160:
            raise PineWorkflowError("Display name is too long.", 422, "DISPLAY_NAME_TOO_LONG")
    if description is not None and len(description) > 4000:
        raise PineWorkflowError("Description is too long.", 422, "DESCRIPTION_TOO_LONG")

    with session_scope() as db:
        query = select(models.StrategyCatalog).where(
            models.StrategyCatalog.id == uuid.UUID(str(strategy_id))
        )
        if not is_admin:
            query = query.where(
                models.StrategyCatalog.owner_user_id == actor_id,
                models.StrategyCatalog.owner_type == "personal",
                models.StrategyCatalog.visibility == "private",
            )
        strategy = db.scalar(query.with_for_update())
        if strategy is None:
            raise PineWorkflowError("Strategy not found.", 404, "NOT_FOUND")

        changes: dict[str, Any] = {}
        if display_name is not None and display_name != strategy.display_name:
            changes["display_name"] = {"from": strategy.display_name, "to": display_name}
            strategy.display_name = display_name
        if description is not None and description != (strategy.description or ""):
            changes["description"] = True
            strategy.description = description or None
        if changes:
            strategy.updated_at = _now()
            _audit(
                db,
                actor_id,
                "STRATEGY_METADATA_UPDATED",
                strategy_id=str(strategy.id),
                is_admin=is_admin,
                changes=changes,
            )
        db.flush()
        return _strategy_public(strategy)


def list_reviews(*, limit: int = 50, offset: int = 0):
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    with session_scope() as db:
        query = (
            select(models.StrategyVersion, models.StrategyCatalog)
            .join(models.StrategyCatalog, models.StrategyCatalog.id == models.StrategyVersion.strategy_id)
            .where(
                models.StrategyCatalog.owner_type == "personal",
                models.StrategyCatalog.visibility == "private",
                models.StrategyVersion.status.in_(REVIEW_STATUSES),
            )
        )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(query.order_by(models.StrategyVersion.updated_at.desc()).limit(limit).offset(offset)).all()
        return {
            "reviews": [{"strategy": _strategy_public(strategy), "version": _version_public(version, _latest_report(db, version.id))} for version, strategy in rows],
            "total": total, "limit": limit, "offset": offset,
        }


def get_review(reviewer_id: uuid.UUID, version_id):
    with session_scope() as db:
        version = db.get(models.StrategyVersion, uuid.UUID(str(version_id)))
        if version is None:
            raise PineWorkflowError("Pine review not found.", 404, "NOT_FOUND")
        strategy = db.get(models.StrategyCatalog, version.strategy_id)
        if (
            strategy is None
            or strategy.owner_type != "personal"
            or strategy.visibility != "private"
            or version.status not in REVIEW_STATUSES
        ):
            raise PineWorkflowError("Pine review not found.", 404, "NOT_FOUND")
        artifact = _artifact(db, version.id)
        _audit(
            db,
            reviewer_id,
            "PERSONAL_PINE_ADMIN_SOURCE_READ",
            strategy_id=str(strategy.id),
            version_id=str(version.id),
            source_sha256=version.source_sha256,
        )
        report = _latest_report(db, version.id)
        events = db.scalars(select(models.StrategyAdminReview).where(
            models.StrategyAdminReview.strategy_version_id == version.id
        ).order_by(models.StrategyAdminReview.reviewed_at.asc())).all()
        payload = {
            "strategy": _strategy_public(strategy),
            "version": _version_public(version, report),
            "source": artifact.content,
            "filename": artifact.original_filename,
            "history": [{
                "id": str(event.id), "decision": event.decision, "note": event.notes,
                "previous_status": event.previous_status, "new_status": event.new_status,
                "reviewer_user_id": str(event.reviewer_user_id) if event.reviewer_user_id else None,
                "reviewed_at": event.reviewed_at.isoformat() if event.reviewed_at else None,
            } for event in events],
        }
        acceptance = db.scalar(select(models.PineUserAcceptance).where(
            models.PineUserAcceptance.candidate_version_id == version.id
        ))
        payload["acceptance"] = ({
            "id": str(acceptance.id),
            "user_id": str(acceptance.user_id),
            "original_version_id": str(acceptance.original_version_id),
            "candidate_version_id": str(acceptance.candidate_version_id),
            "prompt_version_id": acceptance.prompt_version_id,
            "setup_type": acceptance.setup_type,
            "validation_report_id": str(acceptance.validation_report_id),
            "validation_report_sha256": acceptance.validation_report_sha256,
            "assumptions": acceptance.assumptions or [],
            "accepted_at": acceptance.accepted_at.isoformat(),
        } if acceptance else None)
        return payload


def _review_transition(reviewer_id: uuid.UUID, version_id, target: str, *, note: str | None = None, acknowledge_warnings: bool = False):
    target = target.lower()
    transitions = {
        "under_review": ({"submitted"}, "started"),
        "approved": ({"under_review"}, "approved"),
        "changes_requested": ({"under_review"}, "changes_requested"),
        "rejected": ({"under_review"}, "rejected"),
    }
    if target not in transitions:
        raise PineWorkflowError("Unsupported review transition.", 400)
    allowed, decision = transitions[target]
    note = (note or "").strip() or None
    with session_scope() as db:
        query = select(models.StrategyVersion).where(
            models.StrategyVersion.id == uuid.UUID(str(version_id))
        ).with_for_update()
        version = db.scalar(query)
        if version is None:
            raise PineWorkflowError("Pine review not found.", 404, "NOT_FOUND")
        strategy = db.get(models.StrategyCatalog, version.strategy_id)
        if strategy is None or strategy.owner_type != "personal" or strategy.owner_user_id is None or strategy.visibility != "private":
            raise PineWorkflowError("Pine review not found.", 404, "NOT_FOUND")
        if version.status not in allowed:
            raise PineWorkflowError("Review state changed; refresh before deciding.", 409, "REVIEW_STATE_CONFLICT")
        report = _latest_report(db, version.id)
        if report is None or not report.eligible_for_review or report.source_sha256 != version.source_sha256:
            raise PineWorkflowError("The exact source version lacks an eligible validation report.", 409, "VALIDATION_STALE")
        from app.services.tradingview_setup_service import SetupError, require_acceptance
        try:
            require_acceptance(db, version.id)
        except SetupError as exc:
            raise PineWorkflowError(str(exc), exc.status_code, exc.code) from exc
        if target == "approved" and report.warning_count and not acknowledge_warnings:
            raise PineWorkflowError("Explicit warning acknowledgement is required before approval.", 409, "WARNINGS_NOT_ACKNOWLEDGED")
        previous = version.status
        version.status = target
        version.updated_at = _now()
        if target == "approved":
            version.approved_by_user_id = reviewer_id
            version.approved_at = _now()
        event = models.StrategyAdminReview(
            strategy_version_id=version.id,
            reviewer_user_id=reviewer_id,
            decision=decision,
            notes=note,
            validation_report_id=report.id,
            previous_status=previous,
            new_status=target,
            source_sha256=version.source_sha256,
        )
        db.add(event)
        _audit(db, reviewer_id, "PERSONAL_PINE_REVIEW", strategy_id=str(strategy.id), version_id=str(version.id), report_id=str(report.id), source_sha256=version.source_sha256, previous_status=previous, new_status=target)
        return {"version": _version_public(version, report)}


def start_review(reviewer_id: uuid.UUID, version_id, *, note: str | None = None):
    return _review_transition(reviewer_id, version_id, "under_review", note=note)


def decide_review(reviewer_id: uuid.UUID, version_id, decision: str, *, note: str | None = None, acknowledge_warnings: bool = False):
    return _review_transition(reviewer_id, version_id, decision, note=note, acknowledge_warnings=acknowledge_warnings)


def link_version(user_id: uuid.UUID, instance_id, strategy_id, version_id):
    from app.services.strategy_instance_service import _owned_instance

    with session_scope() as db:
        instance = _owned_instance(db, user_id, instance_id, for_update=True)
        strategy, version = _owned_version(db, user_id, strategy_id, version_id)
        if version.status != "approved" or version.pine_contract_version != CONTRACT_VERSION:
            raise PineWorkflowError("Only an approved supported Pine version can be linked.", 409, "VERSION_NOT_APPROVED")
        if instance.execution_mode == "real_orders":
            raise PineWorkflowError("Imported Pine versions may be linked only to paper strategies.", 409, "PAPER_ONLY")
        previous_version_id = instance.strategy_version_id
        instance.strategy_id = strategy.id
        instance.strategy_version_id = version.id
        instance.updated_at = _now()
        if previous_version_id != version.id:
            setup = db.scalar(select(models.TradingViewSetup).where(
                models.TradingViewSetup.strategy_instance_id == instance.id
            ))
            if setup is not None:
                setup.approved_version_id = version.id
                setup.status = "SETUP_PENDING"
                setup.user_reported_compiled_at = setup.installation_confirmed_at = None
                setup.installation_metadata = None
                setup.hold_signal_id = setup.paper_entry_signal_id = setup.paper_exit_signal_id = setup.reversal_signal_id = None
                setup.hold_verified_at = setup.paper_entry_verified_at = setup.paper_exit_verified_at = setup.reversal_verified_at = None
                setup.blocking_reason = "Approved Pine version changed; TradingView verification was reset."
                setup.reset_count += 1
                setup.updated_at = _now()
        _audit(db, user_id, "PERSONAL_PINE_VERSION_LINKED", instance_id=str(instance.id), strategy_id=str(strategy.id), version_id=str(version.id), source_sha256=version.source_sha256)
        return {
            "instance_id": str(instance.id),
            "strategy_id": str(strategy.id),
            "version_id": str(version.id),
            "execution_mode": instance.execution_mode,
            "hosted_execution_enabled": False,
            "live_private_webhook_execution_enabled": False,
            "message": "Internal NOVA-hosted execution is not available yet. Your approved strategy is saved for the upcoming hosted-strategy phase.",
        }
