"""Immutable Pine semantic-analysis provenance persistence (R1B-1).

EVIDENCE ONLY — NOT EXECUTION AUTHORITY. This writer binds the exact source
hash, deterministic analyzer version, registry identity/hash and a canonical
closed payload hash into one immutable pine_semantic_analyses row so future
qualification decisions can be independently reproduced and audited.

Boundaries:

- Gated behind settings.R1B_PINE_ANALYSIS_PERSISTENCE (default false); when
  the flag is off this module performs no database query and no insert.
- Called only from the owner-scoped Pine qualification flow. Never from the
  webhook path, execution worker/router, brokers, startup or schedulers.
- Rows are never updated; reanalysis inserts a new row and may point
  supersedes_analysis_id at the earlier one. No update API exists here.
- Never persists or logs Pine source, feature vectors, credentials or raw
  exceptions. Failures surface as SemanticAnalysisPersistenceError with a
  closed safe code only.
- No AI, TradingView, broker or other network access.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import settings
from app.db import models
from app.services.pine_semantic_preanalyzer import analyze_source

ANALYSIS_SCHEMA_VERSION = "nova.pine-semantic-analysis-persistence.v1"
MAX_ANALYSIS_PAYLOAD_BYTES = 64 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class SemanticAnalysisPersistenceError(RuntimeError):
    """Safe internal qualification error. Carries a closed code, never source
    text, credentials or raw database exception detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_analysis_payload(result) -> tuple[dict, bytes]:
    """Closed payload: five sorted, deduplicated string lists — nothing else."""
    payload = {
        "matched_capabilities": sorted({str(item) for item in result.matched_capabilities}),
        "temporal_classes": sorted({str(item) for item in result.temporal_classes}),
        "blocker_codes": sorted({str(item) for item in result.blocker_codes}),
        "disclosure_codes": sorted({str(item) for item in result.disclosure_codes}),
        "admin_review_points": sorted({str(item) for item in result.admin_review_points}),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return payload, encoded


def _verify_owner(session, source_artifact, owner_user_id: uuid.UUID) -> None:
    owner = session.scalar(
        select(models.StrategyCatalog.owner_user_id)
        .join(
            models.StrategyVersion,
            models.StrategyVersion.strategy_id == models.StrategyCatalog.id,
        )
        .where(models.StrategyVersion.id == source_artifact.strategy_version_id)
    )
    if owner != owner_user_id:
        raise SemanticAnalysisPersistenceError("ARTIFACT_OWNERSHIP_MISMATCH")


def _provenance_filter(row_values: dict):
    model = models.PineSemanticAnalysis
    return (
        model.source_artifact_id == row_values["source_artifact_id"],
        model.source_sha256 == row_values["source_sha256"],
        model.analyzer_version == row_values["analyzer_version"],
        model.registry_id == row_values["registry_id"],
        model.registry_version == row_values["registry_version"],
        model.registry_sha256 == row_values["registry_sha256"],
        model.analysis_schema_version == row_values["analysis_schema_version"],
    )


def persist_semantic_analysis(
    session,
    source_artifact: models.StrategySourceArtifact,
    source_text: str,
    *,
    owner_user_id: uuid.UUID | None = None,
    supersedes_analysis_id: uuid.UUID | None = None,
) -> models.PineSemanticAnalysis:
    """Reuse or insert exactly one immutable provenance row for this analysis.

    Fails closed with SemanticAnalysisPersistenceError; never returns a row
    whose provenance was not fully verified.
    """
    if not settings.R1B_PINE_ANALYSIS_PERSISTENCE:
        raise SemanticAnalysisPersistenceError("PERSISTENCE_DISABLED")
    if not isinstance(source_text, str) or not source_text:
        raise SemanticAnalysisPersistenceError("INVALID_SOURCE")

    first_hash = _sha256(source_text)
    if first_hash != source_artifact.content_sha256:
        raise SemanticAnalysisPersistenceError("SOURCE_HASH_MISMATCH")
    if owner_user_id is not None:
        _verify_owner(session, source_artifact, owner_user_id)

    result = analyze_source(source_text)

    # Recompute after analysis: the exact bytes we hash must be the exact
    # bytes that were analyzed and the exact bytes the artifact pinned.
    second_hash = _sha256(source_text)
    if second_hash != first_hash or result.source_sha256 != first_hash:
        raise SemanticAnalysisPersistenceError("SOURCE_HASH_MISMATCH")

    if (
        not result.registry_id
        or result.registry_id == "UNAVAILABLE"
        or not result.registry_version
        or result.registry_version == "UNAVAILABLE"
        or not _HEX64.fullmatch(result.registry_sha256 or "")
        or not result.analyzer_version
    ):
        raise SemanticAnalysisPersistenceError("REGISTRY_PROVENANCE_INVALID")

    payload, encoded = canonical_analysis_payload(result)
    if len(encoded) > MAX_ANALYSIS_PAYLOAD_BYTES:
        raise SemanticAnalysisPersistenceError("PAYLOAD_TOO_LARGE")

    row_values = {
        "source_artifact_id": source_artifact.id,
        "source_sha256": result.source_sha256,
        "analyzer_version": result.analyzer_version,
        "registry_id": result.registry_id,
        "registry_version": result.registry_version,
        "registry_sha256": result.registry_sha256,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
    }
    try:
        existing = session.scalar(
            select(models.PineSemanticAnalysis).where(*_provenance_filter(row_values))
        )
        if existing is not None:
            return existing
        row = models.PineSemanticAnalysis(
            **row_values,
            effective_capability_level=result.effective_capability_level.value,
            confidence=result.confidence.value,
            analysis_payload=payload,
            analysis_payload_sha256=hashlib.sha256(encoded).hexdigest(),
            supersedes_analysis_id=supersedes_analysis_id,
        )
        try:
            with session.begin_nested():
                session.add(row)
        except IntegrityError:
            # Concurrent identical insert: the unique provenance tuple
            # guarantees one row — return the winner. The failed pending row
            # is usually expunged by the savepoint rollback already.
            try:
                session.expunge(row)
            except Exception:
                pass
            existing = session.scalar(
                select(models.PineSemanticAnalysis).where(*_provenance_filter(row_values))
            )
            if existing is not None:
                return existing
            raise SemanticAnalysisPersistenceError("PERSISTENCE_UNAVAILABLE") from None
        return row
    except SemanticAnalysisPersistenceError:
        raise
    except SQLAlchemyError:
        # Translate database failures to one closed safe code; the raw
        # exception (which could echo statement parameters) never propagates.
        raise SemanticAnalysisPersistenceError("PERSISTENCE_UNAVAILABLE") from None
