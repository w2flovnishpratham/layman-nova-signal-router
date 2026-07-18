"""Static proof of the narrow R1B-2B runtime call graph."""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"
SERVICES = APP_ROOT / "services"
HELPER = SERVICES / "hold_canonical_decision_evidence.py"
INGRESS = SERVICES / "private_webhook_service.py"


def _runtime_sources() -> list[Path]:
    return [path for path in APP_ROOT.rglob("*.py") if "tests" not in path.parts]


def _called_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)
    return names


def test_decision_writer_call_sites_are_exactly_the_two_evidence_helpers():
    # R1B-2B3 added the trading helper as the second authorized caller; the
    # exhaustive graph (guards included) is asserted by
    # test_r1b2b3_runtime_call_sites.py.
    trading_helper = SERVICES / "trading_canonical_decision_evidence.py"
    matches = sorted(
        path
        for path in _runtime_sources()
        for name in _called_names(path)
        if name == "persist_canonical_signal_decision"
    )
    assert matches == sorted([HELPER, trading_helper])


def test_outcome_and_rejection_writers_remain_disconnected():
    for writer in (
        "persist_canonical_signal_outcome",
        "persist_strategy_signal_rejection",
    ):
        assert [
            path
            for path in _runtime_sources()
            for name in _called_names(path)
            if name == writer
        ] == []


def test_hold_helper_has_one_ingress_call_and_no_trading_actions():
    matches = [
        path
        for path in _runtime_sources()
        for name in _called_names(path)
        if name == "persist_hold_decision_best_effort"
    ]
    assert matches == [INGRESS]
    text = HELPER.read_text(encoding="utf-8")
    for action in ("BUY_CE", "BUY_PE", "EXIT"):
        assert action not in text


def test_helper_imports_no_execution_or_broker_machinery():
    tree = ast.parse(HELPER.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = (
        "execution",
        "router",
        "risk",
        "broker",
        "dhan",
        "position",
        "state_store",
        "worker",
        "credential",
        "webhook_replay_store",
    )
    for name in imported:
        assert not any(token in name.lower() for token in forbidden), name


def test_ingress_guard_is_fresh_hold_only():
    tree = ast.parse(INGRESS.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "persist_hold_decision_best_effort"
    ]
    assert len(calls) == 1
    child = calls[0]
    ancestors: list[ast.AST] = []
    for parent in ast.walk(tree):
        if any(node is child for node in ast.walk(parent) if node is not parent):
            ancestors.append(parent)
    guards = [
        ast.unparse(node.test)
        for node in ancestors
        if isinstance(node, ast.If)
    ]
    assert any("action == 'HOLD'" in guard for guard in guards)
    assert any("status == 'fresh'" in guard for guard in guards)
