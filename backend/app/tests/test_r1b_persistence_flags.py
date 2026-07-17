"""R1B-1 persistence flags: safe defaults, zero call sites, zero writes."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_private_webhook import (  # noqa: F401
    _make_instance,
    _payload,
    _post,
    client,
    webhook_enabled,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"
FRONTEND_SRC = BACKEND_ROOT.parent / "frontend" / "src"

R1B_FLAGS = (
    "R1B_CANONICAL_DECISION_PERSISTENCE",
    "R1B_CANONICAL_OUTCOME_PERSISTENCE",
    "R1B_SIGNAL_REJECTION_PERSISTENCE",
    "R1B_PINE_ANALYSIS_PERSISTENCE",
)
DECLARED_ONLY_FLAGS = R1B_FLAGS[:3]
EMPTY_EVIDENCE_MODELS = (
    "CanonicalSignalDecision",
    "CanonicalSignalOutcome",
    "StrategySignalRejection",
)


def test_all_four_flags_default_false():
    from app.config import Settings, settings

    for name in R1B_FLAGS:
        assert Settings.model_fields[name].default is False, name
        assert getattr(settings, name) is False, name


@pytest.mark.parametrize("value", ["banana", "2", "TRUEISH", None, 3.5, [], {}])
def test_invalid_flag_values_resolve_false(value):
    from app.config import Settings

    assert Settings._safe_shadow_flag(value) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("on", True),
     ("0", False), ("false", False), ("", False), ("off", False)],
)
def test_string_flag_values_parse_safely(value, expected):
    from app.config import Settings

    assert Settings._safe_shadow_flag(value) is expected


def _runtime_sources() -> list[Path]:
    return [
        path
        for path in APP_ROOT.rglob("*.py")
        if "tests" not in path.parts and path.name != "config.py"
    ]


def test_declared_only_flags_have_zero_runtime_call_sites():
    for path in _runtime_sources():
        text = path.read_text(encoding="utf-8")
        for flag in DECLARED_ONLY_FLAGS:
            assert flag not in text, f"{flag} referenced by {path}"


def test_analysis_flag_is_confined_to_the_qualification_flow():
    # models.py only documents the flag in the PineSemanticAnalysis docstring.
    allowed = {"personal_pine_service.py", "pine_semantic_analysis_persistence.py", "models.py"}
    referencing = {
        path.name
        for path in _runtime_sources()
        if "R1B_PINE_ANALYSIS_PERSISTENCE" in path.read_text(encoding="utf-8")
    }
    assert referencing == allowed


def test_flags_have_no_api_webhook_or_frontend_exposure():
    routers = list((APP_ROOT / "routers").rglob("*.py"))
    for path in routers:
        text = path.read_text(encoding="utf-8")
        for flag in R1B_FLAGS:
            assert flag not in text, f"{flag} exposed by router {path.name}"
    if FRONTEND_SRC.exists():
        for path in list(FRONTEND_SRC.rglob("*.ts")) + list(FRONTEND_SRC.rglob("*.tsx")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for flag in R1B_FLAGS:
                assert flag not in text, f"{flag} exposed by frontend {path.name}"


def _evidence_counts() -> dict[str, int]:
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        counts = {}
        for name in EMPTY_EVIDENCE_MODELS + ("PineSemanticAnalysis",):
            model = getattr(models, name)
            counts[name] = db.scalar(select(func.count()).select_from(model))
        return counts


@pytest.mark.parametrize("flags_forced_true", [False, True])
def test_webhook_flows_write_no_evidence_rows(client, monkeypatch, flags_forced_true):  # noqa: F811
    """Even with the three declared-only flags forced true, no writer exists,
    so every ingress flow leaves all evidence tables empty."""
    from app.config import settings

    if flags_forced_true:
        for flag in R1B_FLAGS:
            monkeypatch.setattr(settings, flag, True, raising=False)

    user = make_user(f"zero-{uuid.uuid4().hex[:8]}@example.com")
    active = _make_instance(user, label="active")
    paused = _make_instance(user, label="paused", status="paused")
    from app.tests.test_private_webhook import _issue_token

    token = _issue_token(user, active)
    paused_token = _issue_token(user, paused)

    # HOLD connectivity, accepted entry, exact duplicate, conflicting
    # duplicate, invalid action, stale signal, paused instance. Exact status
    # codes are asserted by test_private_webhook; here every flow just has to
    # run for real without ever touching an evidence table.
    responses = {
        "hold": _post(client, token, _payload(action="HOLD")),
    }
    entry = _payload(action="BUY_CE")
    responses["entry"] = _post(client, token, entry)
    responses["duplicate"] = _post(client, token, entry)
    responses["conflicting"] = _post(client, token, dict(entry, action="BUY_PE"))
    responses["invalid_action"] = _post(client, token, _payload(action="SELL_EVERYTHING"))
    from datetime import datetime, timedelta, timezone

    stale = _payload(signal_time=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    responses["stale"] = _post(client, token, stale)
    responses["paused"] = _post(client, paused_token, _payload(action="BUY_CE"))

    assert responses["entry"].status_code == 202, responses["entry"].text
    assert responses["conflicting"].status_code == 409, responses["conflicting"].text
    assert responses["invalid_action"].status_code == 422
    assert responses["stale"].status_code == 422
    for name, response in responses.items():
        assert response.status_code < 500, (name, response.text)

    counts = _evidence_counts()
    assert counts == {name: 0 for name in counts}, counts
