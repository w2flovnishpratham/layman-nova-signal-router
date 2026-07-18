"""Static proof of the R1B-2B3 runtime call graph."""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"
SERVICES = APP_ROOT / "services"
HOLD_HELPER = SERVICES / "hold_canonical_decision_evidence.py"
TRADING_HELPER = SERVICES / "trading_canonical_decision_evidence.py"
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


def test_decision_writer_has_exactly_two_helper_call_sites():
    matches = sorted(
        path
        for path in _runtime_sources()
        for name in _called_names(path)
        if name == "persist_canonical_signal_decision"
    )
    assert matches == sorted([HOLD_HELPER, TRADING_HELPER])


def test_each_helper_has_exactly_one_ingress_call_site():
    for helper_name, expected in (
        ("persist_hold_decision_best_effort", [INGRESS]),
        ("persist_trading_decision_best_effort", [INGRESS]),
    ):
        matches = [
            path
            for path in _runtime_sources()
            for name in _called_names(path)
            if name == helper_name
        ]
        assert matches == expected, helper_name


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


def test_trading_ingress_guard_is_fresh_non_duplicate_trading_only():
    tree = ast.parse(INGRESS.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "persist_trading_decision_best_effort"
    ]
    assert len(calls) == 1
    child = calls[0]
    ancestors: list[ast.AST] = []
    for parent in ast.walk(tree):
        if any(node is child for node in ast.walk(parent) if node is not parent):
            ancestors.append(parent)
    guards = [ast.unparse(node.test) for node in ancestors if isinstance(node, ast.If)]
    combined = " && ".join(guards)
    assert "status == 'fresh'" in combined
    assert "TRADING_DECISION_ACTIONS" in combined
    assert "result.get('duplicate')" in combined


def test_trading_helper_imports_no_execution_machinery():
    tree = ast.parse(TRADING_HELPER.read_text(encoding="utf-8"))
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
        "hold_canonical",
    )
    for name in imported:
        assert not any(token in name.lower() for token in forbidden), name


def test_hold_helper_is_byte_identical_to_the_reviewed_version():
    """R1B-2B3 must not touch the reviewed HOLD helper at all.

    The pinned hash is the newline-normalized SHA-256 of the helper exactly as
    approved by the R1B-2B review (commit 990385f). Any edit to the reviewed
    file — however small — fails this test and requires a new review.
    """
    import hashlib

    reviewed_sha256 = "b741c12cb6014f07a0e026fca5c4b88b5726374f4ec43f4cae0f69885898b0b2"
    current = HOLD_HELPER.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(current).hexdigest() == reviewed_sha256
