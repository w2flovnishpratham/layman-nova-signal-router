"""Focused C2F security, kill-switch, mutation, gate, and concurrency evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.services.untrusted_text_sanitizer import sanitize_untrusted_operator_text
from app.tests.conftest_multiuser import make_user
from app.tests.test_c2_tradingview_integration import (
    _approved,
    _compile,
    _credential,
    _hold,
    _install,
    c2_app as _c2_app,  # noqa: F401 - imported fixture
)

pytest_plugins = ("app.tests.conftest_multiuser",)


@pytest.fixture(name="c2_app")
def c2_app_fixture(request):
    """Expose the imported vertical-slice fixture under its public test name."""
    return request.getfixturevalue("_c2_app")


SECRET_MARKERS = (
    "BEARER_C2F_MARKER",
    "BASIC_C2F_MARKER",
    "TOTP_C2F_MARKER",
    "OTP_C2F_MARKER",
    "ACCESS_C2F_MARKER",
    "REFRESH_C2F_MARKER",
    "APIKEY_C2F_MARKER",
    "PASSWORD_C2F_MARKER",
    "COOKIE_C2F_MARKER",
    "SESSION_C2F_MARKER",
    "POSTGRES_C2F_MARKER",
    "REDIS_C2F_MARKER",
    "WINDOWS_C2F_MARKER",
    "UNC_C2F_MARKER",
    "UNIX_C2F_MARKER",
    "PRIVATEKEY_C2F_MARKER",
    "DHAN_C2F_MARKER",
    "ANTHROPIC_C2F_MARKER",
    "WEBHOOK_C2F_MARKER",
)


def _secret_text() -> str:
    return "\n".join(
        (
            "line 21 column 8 unexpected identifier; preserve this context",
            "Authorization: Bearer BEARER_C2F_MARKER",
            "authorization: Basic BASIC_C2F_MARKER",
            "TOTP=TOTP_C2F_MARKER OTP: OTP_C2F_MARKER",
            'access_token="ACCESS_C2F_MARKER" refresh_token=REFRESH_C2F_MARKER',
            "X-API-Key: APIKEY_C2F_MARKER",
            "password PASSWORD_C2F_MARKER",
            "Cookie: sid=COOKIE_C2F_MARKER",
            "session_id=SESSION_C2F_MARKER",
            "postgresql://user:POSTGRES_C2F_MARKER@db.example/nova",
            "redis://:REDIS_C2F_MARKER@cache.example/0",
            r"C:\secret\WINDOWS_C2F_MARKER",
            r"\\server\share\UNC_C2F_MARKER",
            "/home/user/UNIX_C2F_MARKER",
            "-----BEGIN PRIVATE KEY-----",
            "PRIVATEKEY_C2F_MARKER",
            "-----END PRIVATE KEY-----",
            "dhan_auth_token=DHAN_C2F_MARKER",
            "Anthropic-API-Key: ANTHROPIC_C2F_MARKER",
            "private_webhook_secret=WEBHOOK_C2F_MARKER",
        )
    )


def _assert_markers_absent(value: object) -> None:
    serialized = value if isinstance(value, str) else json.dumps(value, default=str)
    for marker in SECRET_MARKERS:
        assert marker not in serialized


def test_operator_text_sanitizer_is_bounded_deterministic_and_idempotent():
    text = _secret_text()
    sanitized = sanitize_untrusted_operator_text(text)

    assert sanitize_untrusted_operator_text(None) is None
    assert sanitize_untrusted_operator_text("") is None
    assert sanitized is not None
    assert "line 21 column 8 unexpected identifier" in sanitized
    _assert_markers_absent(sanitized)
    assert sanitize_untrusted_operator_text(sanitized) == sanitized

    controls = sanitize_untrusted_operator_text("line\x00\x07 21\tcolumn 8")
    assert controls == "line 21 column 8"
    malformed = sanitize_untrusted_operator_text("context \ud800 token=MALFORMED_MARKER")
    assert malformed is not None
    assert "MALFORMED_MARKER" not in malformed

    long_text = (
        "useful compiler context " * 80
        + " authorization: Bearer LONG_SECRET_MARKER"
    )
    bounded = sanitize_untrusted_operator_text(long_text)
    assert bounded is not None and len(bounded) == 1000
    assert "LONG_SECRET_MARKER" not in bounded


def test_compile_error_and_setup_notes_are_safe_before_persistence(
    c2_app, caplog
):
    client, _, _, owner, _ = c2_app
    caplog.set_level("DEBUG")
    secret_text = _secret_text()

    failed = _approved(client, name="C2F failure sanitizer")
    response = client.post(
        f"/api/admin/pine-conversions/{failed['id']}/compile-failure",
        json={"compiler_error_summary": secret_text},
    )
    assert response.status_code == 200, response.text
    failure_compile = response.json()["compile"]
    assert "line 21 column 8 unexpected identifier" in failure_compile[
        "compiler_error_summary"
    ]
    _assert_markers_absent(response.text)

    succeeded = _approved(client, name="C2F setup-note sanitizer")
    response = client.post(
        f"/api/admin/pine-conversions/{succeeded['id']}/compile-success",
        json={"setup_notes": secret_text},
    )
    assert response.status_code == 200, response.text
    success_compile = response.json()["compile"]
    _assert_markers_absent(response.text)
    installation = _install(client, succeeded, owner.id)
    suspension_markers = ("SUSPEND_BEARER_MARKER", "SUSPEND_TOTP_MARKER")
    suspended = client.post(
        f"/api/admin/strategy-installations/{installation['id']}/suspend",
        json={
            "reason": (
                "line 4 controlled suspension\n"
                "Authorization: Bearer SUSPEND_BEARER_MARKER\n"
                "TOTP=SUSPEND_TOTP_MARKER"
            )
        },
    )
    assert suspended.status_code == 200, suspended.text
    assert all(marker not in suspended.text for marker in suspension_markers)

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        failed_row = db.get(
            models.TradingViewCompileEvidence, uuid.UUID(failure_compile["id"])
        )
        success_row = db.get(
            models.TradingViewCompileEvidence, uuid.UUID(success_compile["id"])
        )
        setup = db.get(models.TradingViewSetup, uuid.UUID(installation["id"]))
        audit = [
            {"action": row.action, "metadata": row.audit_metadata}
            for row in db.scalars(select(models.AuditLog)).all()
        ]
        assert failed_row.compiler_error_summary == failure_compile[
            "compiler_error_summary"
        ]
        assert success_row.setup_notes == success_compile["setup_notes"]
        _assert_markers_absent(failed_row.compiler_error_summary)
        _assert_markers_absent(success_row.setup_notes)
        _assert_markers_absent(setup.installation_metadata)
        _assert_markers_absent(audit)
        assert all(
            marker not in (setup.blocking_reason or "")
            for marker in suspension_markers
        )
    _assert_markers_absent(caplog.text)
    assert all(marker not in caplog.text for marker in suspension_markers)


def _counts() -> dict[str, int]:
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        return {
            "compile": db.query(models.TradingViewCompileEvidence).count(),
            "setup": db.query(models.TradingViewSetup).count(),
            "instance": db.query(models.StrategyInstance).count(),
            "credential": db.query(
                models.StrategyInstanceWebhookCredential
            ).count(),
            "jobs": db.query(models.StrategyExecutionJob).count(),
            "orders": db.query(models.LiveOrderIntent).count(),
            "positions": db.query(models.StrategyInstancePosition).count(),
            "signals": db.query(models.StrategySignal).count(),
        }


MUTATIONS = (
    "source_content",
    "source_sha",
    "strategy_layer_content",
    "strategy_layer_sha",
    "candidate_content",
    "candidate_sha",
    "validation_eligibility",
    "validation_binding",
    "review_decision",
    "review_binding",
    "prompt_sha",
    "transport_sha",
    "candidate_reference",
)


def _mutate_c1_binding(conversion: dict, mutation: str) -> None:
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        request = db.get(
            models.PineConversionRequest, uuid.UUID(conversion["id"])
        )
        source = db.scalar(
            select(models.StrategySourceArtifact).where(
                models.StrategySourceArtifact.strategy_version_id
                == request.input_version_id,
                models.StrategySourceArtifact.artifact_type == "pine_script",
            )
        )
        candidate = db.scalar(
            select(models.StrategySourceArtifact).where(
                models.StrategySourceArtifact.strategy_version_id
                == request.candidate_version_id,
                models.StrategySourceArtifact.artifact_type == "pine_script",
            )
        )
        layer = db.scalar(
            select(models.StrategySourceArtifact).where(
                models.StrategySourceArtifact.strategy_version_id
                == request.candidate_version_id,
                models.StrategySourceArtifact.artifact_type
                == "master_prompt_output",
            )
        )
        report = db.get(models.StrategyValidationReport, request.validation_report_id)
        review = db.scalar(
            select(models.StrategyAdminReview)
            .where(
                models.StrategyAdminReview.strategy_version_id
                == request.candidate_version_id
            )
            .order_by(models.StrategyAdminReview.reviewed_at.desc())
        )

        if mutation == "source_content":
            source.content += "\n// C2F source mutation"
        elif mutation == "source_sha":
            source.content_sha256 = "1" * 64
        elif mutation == "strategy_layer_content":
            layer.content += "\n// C2F layer mutation"
        elif mutation == "strategy_layer_sha":
            layer.content_sha256 = "2" * 64
        elif mutation == "candidate_content":
            candidate.content += "\n// C2F candidate mutation"
        elif mutation == "candidate_sha":
            candidate.content_sha256 = "3" * 64
        elif mutation == "validation_eligibility":
            report.eligible_for_review = False
        elif mutation == "validation_binding":
            report.source_sha256 = "4" * 64
        elif mutation == "review_decision":
            review.decision = "rejected"
        elif mutation == "review_binding":
            review.source_sha256 = "5" * 64
        elif mutation in {"prompt_sha", "transport_sha"}:
            key = (
                "prompt_sha256"
                if mutation == "prompt_sha"
                else "transport_sha256"
            )
            summary = copy.deepcopy(request.usage_summary)
            summary["provenance"][key] = "6" * 64
            request.usage_summary = summary
        elif mutation == "candidate_reference":
            request.candidate_version_id = request.input_version_id
        else:  # pragma: no cover - parameter list is closed above
            raise AssertionError(mutation)


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_compile_binding_mutation_matrix_fails_without_partial_rows(
    c2_app, mutation
):
    client, _, _, _, _ = c2_app
    approved = _approved(client, name=f"C2F compile {mutation}")
    _mutate_c1_binding(approved, mutation)

    response = client.post(
        f"/api/admin/pine-conversions/{approved['id']}/compile-success",
        json={},
    )
    assert response.status_code == 409, response.text
    assert response.json()["reason"] in {
        "C1_APPROVAL_REQUIRED",
        "CANDIDATE_INTEGRITY_INVALID",
    }
    assert _counts() == {
        "compile": 0,
        "setup": 0,
        "instance": 0,
        "credential": 0,
        "jobs": 0,
        "orders": 0,
        "positions": 0,
        "signals": 0,
    }


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_installation_binding_mutation_matrix_fails_without_partial_rows(
    c2_app, mutation
):
    client, _, _, owner, _ = c2_app
    approved = _approved(client, name=f"C2F install {mutation}")
    _compile(client, approved)
    _mutate_c1_binding(approved, mutation)

    response = client.post(
        "/api/admin/strategy-installations",
        json={
            "conversion_id": approved["id"],
            "owner_user_id": str(owner.id),
            "mode": "SELF",
            "instance_label": "C2F mutation blocked",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["reason"] in {
        "C1_APPROVAL_REQUIRED",
        "CANDIDATE_INTEGRITY_INVALID",
    }
    assert _counts() == {
        "compile": 1,
        "setup": 0,
        "instance": 0,
        "credential": 0,
        "jobs": 0,
        "orders": 0,
        "positions": 0,
        "signals": 0,
    }


def _paper_ready(c2_app):
    client, current, admin, owner, other = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    current["user"] = owner
    credential = _credential(client, installation["id"], admin=False)
    accepted = client.post("/api/webhooks/private", json=_hold(credential["token"]))
    assert accepted.status_code == 202, accepted.text
    return client, current, admin, owner, other, approved, installation, credential


def test_feature_flag_is_runtime_kill_switch_and_reenable_is_non_destructive(
    c2_app, monkeypatch
):
    (
        client,
        current,
        _,
        owner,
        _,
        _,
        installation,
        credential,
    ) = _paper_ready(c2_app)
    instance_id = installation["strategy_instance_id"]

    before = client.get("/api/engine/strategies").json()["strategies"]
    assert next(row for row in before if row["instance_id"] == instance_id)[
        "selectable"
    ]
    assert client.put(
        "/api/engine/selection",
        json={"strategy_instance_id": instance_id},
    ).status_code == 200
    stored_before = _counts()

    monkeypatch.setattr(settings, "C2_TRADINGVIEW_INSTALLATION_ENABLED", False)
    detail = client.get(
        f"/api/strategies/my-installations/{installation['id']}"
    ).json()["installation"]
    assert detail["gates"]["feature_enabled"] is False
    assert detail["paper_eligible"] is False
    assert detail["status"] == "FEATURE_DISABLED"
    assert detail["live_eligible"] is False

    entry = next(
        row
        for row in client.get("/api/engine/strategies").json()["strategies"]
        if row["instance_id"] == instance_id
    )
    assert entry["selectable"] is False
    assert entry["blocking_reason"] == "C2_FEATURE_DISABLED"
    assert entry["selected"] is True
    selection = client.put(
        "/api/engine/selection",
        json={"strategy_instance_id": instance_id},
    )
    assert selection.status_code == 409
    assert selection.json()["reason"] == "C2_FEATURE_DISABLED"
    webhook = client.post(
        "/api/webhooks/private",
        json=_hold(credential["token"], signal_id="c2f-disabled-hold"),
    )
    assert webhook.status_code == 409
    assert webhook.json()["reason"] == "C2_FEATURE_DISABLED"
    assert _counts() == stored_before

    monkeypatch.setattr(settings, "C2_TRADINGVIEW_INSTALLATION_ENABLED", True)
    reenabled = client.get(
        f"/api/strategies/my-installations/{installation['id']}"
    ).json()["installation"]
    assert reenabled["gates"]["feature_enabled"] is True
    assert reenabled["paper_eligible"] is True
    assert next(
        row
        for row in client.get("/api/engine/strategies").json()["strategies"]
        if row["instance_id"] == instance_id
    )["selectable"]
    assert current["user"].id == owner.id


GATE_CASES = (
    ("c1_approval", "C1_APPROVAL_INVALID"),
    ("compile_success", "TRADINGVIEW_COMPILE_REQUIRED"),
    ("installation_active", "INSTALLATION_INACTIVE"),
    ("owner_bound", "INSTALLATION_OWNER_INVALID"),
    ("paper_safe_mode", "PAPER_MODE_REQUIRED"),
    ("credential_active", "CREDENTIAL_INACTIVE"),
    ("current_credential_binding", "CREDENTIAL_BINDING_INVALID"),
    ("hold_verified", "HOLD_NOT_VERIFIED"),
    ("candidate_integrity", "CANDIDATE_INTEGRITY_INVALID"),
    ("source_integrity", "SOURCE_INTEGRITY_INVALID"),
    ("strategy_layer_integrity", "STRATEGY_LAYER_INTEGRITY_INVALID"),
    ("installation_not_suspended", "INSTALLATION_SUSPENDED"),
)


def _break_readiness_gate(installation: dict, gate: str, other_user_id) -> None:
    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        setup = db.get(models.TradingViewSetup, uuid.UUID(installation["id"]))
        instance = db.get(models.StrategyInstance, setup.strategy_instance_id)
        credential = db.get(
            models.StrategyInstanceWebhookCredential, setup.current_credential_id
        )
        evidence = db.get(
            models.TradingViewCompileEvidence, setup.compile_evidence_id
        )
        conversion = db.get(
            models.PineConversionRequest, setup.pine_conversion_request_id
        )
        review = db.scalar(
            select(models.StrategyAdminReview)
            .where(
                models.StrategyAdminReview.strategy_version_id
                == conversion.candidate_version_id
            )
            .order_by(models.StrategyAdminReview.reviewed_at.desc())
        )

        if gate == "c1_approval":
            review.decision = "rejected"
        elif gate == "compile_success":
            evidence.result = "FAILURE"
        elif gate == "installation_active":
            setup.installation_confirmed_at = None
        elif gate == "owner_bound":
            instance.user_id = other_user_id
        elif gate == "paper_safe_mode":
            instance.execution_mode = "real_orders"
        elif gate == "credential_active":
            credential.revoked_at = datetime.now(timezone.utc)
        elif gate == "current_credential_binding":
            setup.current_credential_id = None
        elif gate == "hold_verified":
            setup.hold_verified_at = None
        elif gate == "candidate_integrity":
            setup.approved_candidate_sha256 = "7" * 64
            instance.approved_candidate_sha256 = "7" * 64
        elif gate == "source_integrity":
            setup.approved_source_sha256 = "8" * 64
        elif gate == "strategy_layer_integrity":
            setup.approved_strategy_layer_sha256 = "9" * 64
        elif gate == "installation_not_suspended":
            setup.suspended_at = datetime.now(timezone.utc)
        else:  # pragma: no cover - parameter list is closed above
            raise AssertionError(gate)


@pytest.mark.parametrize(("gate", "blocker"), GATE_CASES)
def test_each_paper_gate_fails_closed_independently(c2_app, gate, blocker):
    (
        client,
        _,
        _,
        _,
        other,
        _,
        installation,
        _,
    ) = _paper_ready(c2_app)
    _break_readiness_gate(installation, gate, other.id)

    detail = client.get(
        f"/api/strategies/my-installations/{installation['id']}"
    ).json()["installation"]
    assert detail["gates"][gate] is False
    assert detail["paper_eligible"] is False
    assert detail["live_eligible"] is False
    assert detail["blocking_reasons"]

    entries = client.get("/api/engine/strategies").json()["strategies"]
    entry = next(
        (
            row
            for row in entries
            if row["instance_id"] == installation["strategy_instance_id"]
        ),
        None,
    )
    if gate == "owner_bound":
        assert entry is None
    else:
        assert entry is not None
        assert entry["selectable"] is False
        assert entry["blocking_reason"] == blocker
    selection = client.put(
        "/api/engine/selection",
        json={"strategy_instance_id": installation["strategy_instance_id"]},
    )
    assert selection.status_code in ({404} if gate == "owner_bound" else {409})
    if gate != "owner_bound":
        assert selection.json()["reason"] == blocker
    counts = _counts()
    assert counts["jobs"] == counts["orders"] == counts["positions"] == 0


def _concurrent_requests(callables):
    barrier = threading.Barrier(len(callables))

    def run(callable_):
        barrier.wait(timeout=10)
        return callable_()

    with ThreadPoolExecutor(max_workers=len(callables)) as pool:
        return list(pool.map(run, callables))


def test_concurrent_installation_is_complete_and_duplicate_free(c2_app):
    client, _, _, owner, other = c2_app
    approved = _approved(client)
    _compile(client, approved)

    def request(owner_id):
        return client.post(
            "/api/admin/strategy-installations",
            json={
                "conversion_id": approved["id"],
                "owner_user_id": str(owner_id),
                "mode": "SELF",
                "instance_label": "C2F concurrent installation",
            },
        )

    same_owner = _concurrent_requests(
        (lambda: request(owner.id), lambda: request(owner.id))
    )
    assert all(response.status_code in {200, 409} for response in same_owner)
    assert all(response.status_code != 500 for response in same_owner)
    assert sum(response.status_code == 200 for response in same_owner) >= 1

    third = make_user("c2-concurrency-third@example.com")
    distinct_owners = _concurrent_requests(
        (lambda: request(other.id), lambda: request(third.id))
    )
    assert [response.status_code for response in distinct_owners] == [200, 200]

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        setups = db.scalars(
            select(models.TradingViewSetup).where(
                models.TradingViewSetup.pine_conversion_request_id
                == uuid.UUID(approved["id"])
            )
        ).all()
        assert len(setups) == 3
        assert len({row.user_id for row in setups}) == 3
        assert all(row.strategy_instance_id for row in setups)
        assert db.query(models.StrategyInstance).count() == 3


def test_concurrent_credential_generation_and_rotation_leave_one_active(
    c2_app, caplog
):
    client, current, _, owner, _ = c2_app
    approved = _approved(client)
    _compile(client, approved)
    installation = _install(client, approved, owner.id)
    current["user"] = owner

    path = (
        f"/api/strategies/my-installations/{installation['id']}/self-credential"
    )
    generated = _concurrent_requests(
        (lambda: client.post(path), lambda: client.post(path))
    )
    assert all(response.status_code in {200, 409} for response in generated)
    assert all(response.status_code != 500 for response in generated)
    tokens = [
        response.json()["credential"]["token"]
        for response in generated
        if response.status_code == 200
    ]
    assert tokens

    rotate_path = (
        f"/api/strategies/my-installations/{installation['id']}/credential/rotate"
    )
    raced = _concurrent_requests(
        (lambda: client.post(path), lambda: client.post(rotate_path))
    )
    assert all(response.status_code in {200, 404, 409} for response in raced)
    assert all(response.status_code != 500 for response in raced)
    tokens.extend(
        response.json()["credential"]["token"]
        for response in raced
        if response.status_code == 200
    )

    from app.db import models
    from app.db.engine import session_scope

    with session_scope() as db:
        rows = db.scalars(
            select(models.StrategyInstanceWebhookCredential).where(
                models.StrategyInstanceWebhookCredential.strategy_instance_id
                == uuid.UUID(installation["strategy_instance_id"])
            )
        ).all()
        active = [row for row in rows if row.revoked_at is None]
        assert len(active) == 1
        active_hash = active[0].token_hash
        assert sum(
            hashlib.sha256(token.encode()).hexdigest() == active_hash
            for token in tokens
        ) == 1
    for token in tokens:
        assert token not in caplog.text
