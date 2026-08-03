"""Mirrors the schema's own ON DELETE CASCADE for R1B evidence rows.

CanonicalSignalDecision's FK to strategy_instances, and PineSemanticAnalysis's
FK to strategy_source_artifacts, are both declared `ondelete="CASCADE"` (see
app/db/models.py) -- the database itself is designed to remove evidence whose
parent no longer exists. This is the one sanctioned exception to the R1B-2A
insert-only guarantee (see app/tests/test_r1b2a_insert_only.py,
test_r1b2a_zero_call_sites.py): evidence is immutable and undeletable on its
own, but does not outlive the row it evidences.

This lives in its own module, not inline in the caller, so the exception
stays narrow and auditable: the only thing this file may ever do is delete
evidence rows in the same operation that deletes their true parent rows.
SQLite (used in tests) does not enforce ON DELETE CASCADE unless PRAGMA
foreign_keys=ON is set per connection, which this codebase does not do, so
relying on Postgres's real cascade here would be untested; every table is
deleted explicitly instead.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import delete, select

from app.db import models


def cascade_delete_evidence_for_strategy_instances(
    db, instance_ids: Iterable[uuid.UUID]
) -> None:
    """Delete CanonicalSignalOutcome/CanonicalSignalDecision/
    StrategySignalRejection rows for the given strategy instances.

    Caller must delete the owning StrategyInstance rows in the same
    transaction -- this only mirrors that cascade, never an independent
    evidence purge.
    """
    instance_ids = list(instance_ids)
    if not instance_ids:
        return
    decision_ids = list(db.scalars(
        select(models.CanonicalSignalDecision.id).where(
            models.CanonicalSignalDecision.strategy_instance_id.in_(instance_ids)
        )
    ))
    if decision_ids:
        db.execute(delete(models.CanonicalSignalOutcome).where(
            models.CanonicalSignalOutcome.canonical_decision_id.in_(decision_ids)
        ))
    db.execute(delete(models.CanonicalSignalDecision).where(
        models.CanonicalSignalDecision.strategy_instance_id.in_(instance_ids)
    ))
    db.execute(delete(models.StrategySignalRejection).where(
        models.StrategySignalRejection.strategy_instance_id.in_(instance_ids)
    ))


def cascade_delete_pine_semantic_analysis_for_artifacts(
    db, artifact_ids: Iterable[uuid.UUID]
) -> None:
    """Delete PineSemanticAnalysis rows for the given source artifacts.

    Caller must delete the owning StrategySourceArtifact rows in the same
    transaction -- this only mirrors that cascade, never an independent
    evidence purge.
    """
    artifact_ids = list(artifact_ids)
    if not artifact_ids:
        return
    db.execute(delete(models.PineSemanticAnalysis).where(
        models.PineSemanticAnalysis.source_artifact_id.in_(artifact_ids)
    ))
