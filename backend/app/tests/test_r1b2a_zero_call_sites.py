"""R1B-2A static guarantee: the three evidence writers have zero production
call sites and cannot reach execution machinery."""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"

WRITER_MODULES = {
    "canonical_signal_decision_persistence.py",
    "canonical_signal_outcome_persistence.py",
    "strategy_signal_rejection_persistence.py",
    "r1b_evidence_safety.py",
}
WRITER_FUNCTIONS = (
    "persist_canonical_signal_decision",
    "persist_canonical_signal_outcome",
    "persist_strategy_signal_rejection",
)
# Runtime files that must never reference the writers (superset guard: ALL
# runtime files outside the writer modules themselves are checked; these are
# called out to fail with a precise message).
FORBIDDEN_RUNTIME_FILES = (
    "private_webhook_service.py",
    "private_webhook.py",
    "private_webhook_execution.py",
    "strategy_job_worker.py",
    "execution_router.py",
    "risk_manager.py",
    "paper_broker.py",
    "dhan_client.py",
    "state_store.py",
    "position_reconciler.py",
    "option_position_monitor.py",
    "eod_squareoff.py",
    "main.py",
)
EVIDENCE_MODELS = (
    "CanonicalSignalDecision",
    "CanonicalSignalOutcome",
    "StrategySignalRejection",
)


def _runtime_sources() -> list[Path]:
    return [path for path in APP_ROOT.rglob("*.py") if "tests" not in path.parts]


def test_writers_have_zero_production_call_sites():
    for path in _runtime_sources():
        if path.name in WRITER_MODULES:
            continue
        text = path.read_text(encoding="utf-8")
        for name in WRITER_FUNCTIONS:
            assert name not in text, f"{name} referenced by runtime file {path}"
        for module in WRITER_MODULES:
            stem = module.removesuffix(".py")
            assert stem not in text, f"writer module {stem} referenced by runtime file {path}"
    # Explicit spot-check of the sensitive files (fails loudly if moved).
    for name in FORBIDDEN_RUNTIME_FILES:
        matches = [path for path in APP_ROOT.rglob(name) if "tests" not in path.parts]
        assert matches, f"expected runtime file missing: {name}"


def test_writers_import_no_execution_machinery():
    forbidden_tokens = (
        "execution_router", "strategy_fanout", "risk_manager", "paper_broker",
        "dhan", "state_store", "position_store", "position_operations",
        "broker", "live_engine", "fastapi", "atm_ltp", "security_id_resolver",
        "webhook_replay_store", "strategy_job_worker",
    )
    for module in WRITER_MODULES:
        path = APP_ROOT / "services" / module
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for name in imported:
            assert not any(token in name.lower() for token in forbidden_tokens), (
                module,
                name,
            )


def test_no_execution_service_reads_evidence_tables():
    allowed = WRITER_MODULES | {"models.py"}
    for path in _runtime_sources():
        if path.name in allowed or "alembic" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for model in EVIDENCE_MODELS:
            assert model not in text, f"{model} referenced by runtime file {path}"


def test_writer_flags_stay_confined_to_their_writers():
    flag_homes = {
        "R1B_CANONICAL_DECISION_PERSISTENCE": "canonical_signal_decision_persistence.py",
        "R1B_CANONICAL_OUTCOME_PERSISTENCE": "canonical_signal_outcome_persistence.py",
        "R1B_SIGNAL_REJECTION_PERSISTENCE": "strategy_signal_rejection_persistence.py",
    }
    for path in _runtime_sources():
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        for flag, home in flag_homes.items():
            if flag in text:
                assert path.name == home, f"{flag} referenced by {path}"
