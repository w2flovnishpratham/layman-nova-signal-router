"""R1B-2A insert-only guarantee: no update/delete path exists for evidence."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from app.tests.conftest_multiuser import mu_db  # noqa: F401
from app.tests.test_canonical_signal_decision_persistence import (  # noqa: F401
    NOW,
    persist as persist_decision,
    seed_ingress,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"
EVIDENCE_MODELS = (
    "CanonicalSignalDecision",
    "CanonicalSignalOutcome",
    "StrategySignalRejection",
    "PineSemanticAnalysis",
)
WRITER_MODULES = (
    "canonical_signal_decision_persistence.py",
    "canonical_signal_outcome_persistence.py",
    "strategy_signal_rejection_persistence.py",
    "pine_semantic_analysis_persistence.py",
    "r1b_evidence_safety.py",
)


def _runtime_sources() -> list[Path]:
    return [path for path in APP_ROOT.rglob("*.py") if "tests" not in path.parts]


def test_no_core_update_or_delete_targets_evidence_models():
    patterns = [
        re.compile(rf"(?:sqlalchemy\.)?update\(\s*(?:models\.)?{model}\b")
        for model in EVIDENCE_MODELS
    ] + [
        re.compile(rf"(?:sqlalchemy\.)?delete\(\s*(?:models\.)?{model}\b")
        for model in EVIDENCE_MODELS
    ] + [
        re.compile(rf"query\(\s*(?:models\.)?{model}\s*\)\s*\.\s*(?:update|delete)\(")
        for model in EVIDENCE_MODELS
    ]
    for path in _runtime_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not pattern.search(text), (path, pattern.pattern)


def test_writer_modules_expose_no_update_delete_or_merge_api():
    for module in WRITER_MODULES:
        path = APP_ROOT / "services" / module
        text = path.read_text(encoding="utf-8")
        for token in ("session.merge(", ".merge(", "def update", "def delete", "def upsert"):
            assert token not in text, (module, token)
        import importlib

        loaded = importlib.import_module(f"app.services.{module.removesuffix('.py')}")
        public = [name for name in dir(loaded) if not name.startswith("_")]
        for name in public:
            lowered = name.lower()
            assert "update" not in lowered and "delete" not in lowered and "upsert" not in lowered, (
                module,
                name,
            )


def test_orm_before_update_guard_still_rejects_evidence_mutation(mu_db, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope

    monkeypatch.setattr(settings, "R1B_CANONICAL_DECISION_PERSISTENCE", True, raising=False)
    with session_scope() as db:
        ctx = seed_ingress(db)
        decision = persist_decision(db, ctx)
        db.flush()
        decision_id = decision.id

    with pytest.raises(ValueError, match="immutable R1B evidence"):
        with session_scope() as db:
            row = db.get(models.CanonicalSignalDecision, decision_id)
            row.wire_action = "EXIT"
    with session_scope() as db:
        assert db.get(models.CanonicalSignalDecision, decision_id).wire_action == "BUY_CE"
