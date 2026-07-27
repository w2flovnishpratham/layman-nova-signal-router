from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_strategy_instances import _create_instance, _seed_supertrend


def _client(user_model):
    from app.auth.dependencies import get_current_user
    from app.routers import personal_pine, strategy_instances
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(personal_pine.router)
    app.include_router(personal_pine.link_router)
    app.include_router(personal_pine.admin_router)
    app.include_router(strategy_instances.router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user_model)
    return TestClient(app)


VALID_PINE = '''//@version=6
indicator("NOVA Imported NIFTY", overlay=true)
confirmed = barstate.isconfirmed
eodExit = hour == 15 and minute >= 15
if confirmed
    alert("BUY_CE", alert.freq_once_per_bar_close)
if confirmed
    alert("BUY_PE", alert.freq_once_per_bar_close)
if confirmed or eodExit
    alert("EXIT", alert.freq_once_per_bar_close)
'''


def _create(client, source: str = VALID_PINE, name: str = "Imported Pine"):
    response = client.post("/api/personal-pine-strategies", json={
        "name": name, "source": source, "filename": "strategy.pine",
    })
    assert response.status_code == 200, response.text
    return response.json()


def _acceptance(version_id: str) -> dict:
    return {
        "original_version_id": version_id,
        "prompt_version_id": settings.PINE_CONVERSION_PROMPT_VERSION,
        "setup_type": "USER_MANAGED_TRADINGVIEW",
        "assumptions": [],
        "reviewed_strategy": True,
        "understands_static_validation": True,
        "understands_performance_risk": True,
        "accepts_paper_only": True,
    }


def test_validator_valid_v6_and_v5_warning():
    from app.services.pine_validation import validate_source

    valid = validate_source(VALID_PINE)
    assert valid["status"] == "PASSED"
    assert valid["eligible_for_review"] is True
    assert valid["emitted_actions"] == ["BUY_CE", "BUY_PE", "EXIT"]
    v5 = validate_source(VALID_PINE.replace("//@version=6", "//@version=5"))
    assert v5["status"] == "PASSED_WITH_WARNINGS"
    assert "PINE_V5_UPGRADE_RECOMMENDED" in {row["code"] for row in v5["findings"]}


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (VALID_PINE.replace("//@version=6\n", ""), "PINE_VERSION_MISSING"),
        (VALID_PINE.replace("//@version=6", "//@version=4"), "PINE_VERSION_UNSUPPORTED"),
        (VALID_PINE.replace('indicator("NOVA Imported NIFTY", overlay=true)\n', ""), "DECLARATION_MISSING"),
        (VALID_PINE.replace("indicator(", "indicator(\"one\")\nstrategy("), "DECLARATION_MULTIPLE"),
        (VALID_PINE.replace('alert("EXIT", alert.freq_once_per_bar_close)', 'plot(close, "exit")'), "EXIT_ACTION_MISSING"),
        (VALID_PINE + '\nalert("SELL", alert.freq_once_per_bar_close)\n', "ACTION_UNSUPPORTED"),
        (VALID_PINE.replace('alert("BUY_CE"', 'alert(\'{"action":"BUY_CE","qty":65}\''), "SERVER_AUTHORITY_FIELD"),
        (VALID_PINE.replace("NIFTY", "BANKNIFTY"), "UNDERLYING_UNSUPPORTED"),
        (VALID_PINE.replace("indicator(\"NOVA Imported NIFTY\", overlay=true)", "strategy(\"NOVA Imported NIFTY\", overlay=true, pyramiding=2)"), "PYRAMIDING_UNSUPPORTED"),
        (VALID_PINE + '\nx = request.security("NSE:A", "5", close)\ny = request.security("NSE:B", "5", close)\n', "MULTI_SYMBOL_RISK"),
        (VALID_PINE + "\nx = request.security(syminfo.tickerid, '5', close, lookahead=barmerge.lookahead_on)\n", "POTENTIAL_REPAINTING"),
        (VALID_PINE.replace("barstate.isconfirmed", "barstate.isrealtime").replace("alert.freq_once_per_bar_close", "alert.freq_all"), "BAR_CONFIRMATION_MISSING"),
        (VALID_PINE + '\n// <script>alert("xss")</script>\n', "HOLD_OPTIONAL"),
    ],
)
def test_validator_finds_stable_rules(source, code):
    from app.services.pine_validation import validate_source

    report = validate_source(source)
    assert code in {row["code"] for row in report["findings"]}
    assert all(len(row.get("excerpt") or "") <= 120 for row in report["findings"])


def test_source_safety_rejects_secret_binary_size_and_filename(mu_db, monkeypatch):
    from app.config import settings

    user = make_user("pine-safety@example.com")
    client = _client(user)
    for source, reason in (
        (VALID_PINE + "\n// nwk_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", "CREDENTIAL_IN_SOURCE"),
        (VALID_PINE + "\x00", "BINARY_SOURCE"),
    ):
        response = client.post("/api/personal-pine-strategies", json={"name": "unsafe", "source": source, "filename": "a.pine"})
        assert response.status_code == 422
        assert response.json()["reason"] == reason
        assert source not in response.text
    monkeypatch.setattr(settings, "PERSONAL_PINE_MAX_SOURCE_BYTES", 32)
    response = client.post("/api/personal-pine-strategies", json={"name": "large", "source": VALID_PINE, "filename": "a.pine"})
    assert response.status_code == 413
    assert response.json()["reason"] == "SOURCE_TOO_LARGE"
    monkeypatch.setattr(settings, "PERSONAL_PINE_MAX_SOURCE_BYTES", 262144)
    response = client.post("/api/personal-pine-strategies", json={"name": "path", "source": VALID_PINE, "filename": "../a.pine"})
    assert response.status_code == 422
    assert response.json()["reason"] == "INVALID_FILENAME"


def test_validator_bounds_malformed_text_and_excessive_findings(monkeypatch):
    from app.services import pine_validation

    invalid_utf8 = pine_validation.validate_source(VALID_PINE + "\ud800")
    assert "INVALID_UTF8" in {row["code"] for row in invalid_utf8["findings"]}
    long_line = pine_validation.validate_source(VALID_PINE + "\n" + "x" * 5000)
    assert "SOURCE_LINE_TOO_LONG" in {row["code"] for row in long_line["findings"]}
    monkeypatch.setattr(pine_validation, "MAX_FINDINGS", 2)
    bounded = pine_validation.validate_source("\x01\x02\x03")
    assert len(bounded["findings"]) == 2


def test_owner_versioning_validation_submission_and_source_isolation(mu_db):
    owner = make_user("pine-owner@example.com")
    foreign = make_user("pine-foreign@example.com")
    client = _client(owner)
    other = _client(foreign)
    created = _create(client)
    strategy_id = created["strategy"]["id"]
    version_id = created["version"]["id"]

    listing = client.get("/api/personal-pine-strategies").json()
    assert listing["total"] == 1
    assert VALID_PINE not in str(listing)
    assert other.get(f"/api/personal-pine-strategies/{strategy_id}").status_code == 404
    assert other.get(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/source").status_code == 404

    validated = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate")
    assert validated.status_code == 200
    assert validated.json()["report"]["eligible_for_review"] is True
    reused = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate").json()
    assert reused["reused"] is True
    submitted = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/submit", json=_acceptance(version_id))
    assert submitted.status_code == 200
    assert submitted.json()["version"]["status"] == "submitted"
    assert other.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/submit", json=_acceptance(version_id)).status_code == 404


def test_immutable_versions_dedupe_and_line_endings(mu_db):
    user = make_user("pine-version@example.com")
    client = _client(user)
    created = _create(client)
    strategy_id = created["strategy"]["id"]
    first_id = created["version"]["id"]
    same = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions", json={
        "source": VALID_PINE.replace("\n", "\r\n"), "filename": "same.txt",
    }).json()
    assert same["created"] is False
    assert same["version"]["id"] == first_id
    changed = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions", json={
        "source": VALID_PINE + "\nplot(close)\n", "filename": "changed.pine",
    }).json()
    assert changed["created"] is True
    assert changed["version"]["id"] != first_id


def test_validator_upgrade_creates_report_not_source_version_and_parser_failure_is_safe(mu_db, monkeypatch):
    from app.services import pine_validation

    user = make_user("pine-validator-upgrade@example.com")
    client = _client(user)
    created = _create(client)
    strategy_id, version_id = created["strategy"]["id"], created["version"]["id"]
    first = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate").json()
    monkeypatch.setattr(pine_validation, "VALIDATOR_VERSION", "1.1.0")
    second = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate").json()
    assert second["report"]["id"] != first["report"]["id"]
    assert client.get(f"/api/personal-pine-strategies/{strategy_id}").json()["strategy"]["version_count"] == 1

    monkeypatch.setattr(pine_validation, "VALIDATOR_VERSION", "1.2.0")
    monkeypatch.setattr(pine_validation, "validate_source", lambda _source: (_ for _ in ()).throw(RuntimeError("private parser detail")))
    failed = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate")
    assert failed.status_code == 200
    assert failed.json()["report"]["status"] == "VALIDATOR_ERROR"
    assert failed.json()["report"]["findings"][0]["code"] == "VALIDATOR_ERROR"
    assert "private parser detail" not in failed.text
    assert VALID_PINE not in failed.text


def test_admin_review_exact_report_decisions_and_link_are_paper_only(mu_db):
    owner = make_user("pine-review-owner@example.com")
    admin = make_user("pine-review-admin@example.com", is_admin=True)
    non_admin = make_user("pine-review-user@example.com")
    client, admin_client = _client(owner), _client(admin)
    created = _create(client)
    strategy_id, version_id = created["strategy"]["id"], created["version"]["id"]
    assert admin_client.get(f"/api/admin/pine-reviews/{version_id}").status_code == 404
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate")
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/submit", json=_acceptance(version_id))

    assert _client(non_admin).get("/api/admin/pine-reviews").status_code == 403
    queue = admin_client.get("/api/admin/pine-reviews").json()
    assert queue["total"] == 1
    detail = admin_client.get(f"/api/admin/pine-reviews/{version_id}").json()["review"]
    assert detail["source"] == VALID_PINE
    assert admin_client.post(f"/api/admin/pine-reviews/{version_id}/start", json={"note": "Review started"}).status_code == 200
    approved = admin_client.post(f"/api/admin/pine-reviews/{version_id}/approve", json={
        "note": "Static contract reviewed", "acknowledge_warnings": True,
    })
    assert approved.status_code == 200
    assert approved.json()["version"]["status"] == "approved"
    assert admin_client.post(f"/api/admin/pine-reviews/{version_id}/reject", json={}).status_code == 409
    immutable = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate")
    assert immutable.status_code == 409
    assert immutable.json()["reason"] == "VERSION_IMMUTABLE"
    assert client.get(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}").json()["version"]["status"] == "approved"

    _seed_supertrend()
    instance = _create_instance(client, source_journey="PERSONAL_TRADINGVIEW", execution_mode="paper_live_data")
    linked = client.post(f"/api/personal-strategies/{instance['id']}/link-version", json={
        "strategy_id": strategy_id, "version_id": version_id,
    })
    assert linked.status_code == 200
    assert linked.json()["link"]["hosted_execution_enabled"] is False
    assert linked.json()["link"]["live_private_webhook_execution_enabled"] is False

    foreign = make_user("pine-link-foreign@example.com")
    assert _client(foreign).post(f"/api/personal-strategies/{instance['id']}/link-version", json={
        "strategy_id": strategy_id, "version_id": version_id,
    }).status_code == 404


def test_owner_sees_the_rejection_reason_but_not_the_reviewers_identity(mu_db):
    owner = make_user("pine-reject-owner@example.com")
    admin = make_user("pine-reject-admin@example.com", is_admin=True)
    client, admin_client = _client(owner), _client(admin)
    created = _create(client)
    strategy_id, version_id = created["strategy"]["id"], created["version"]["id"]
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate")
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/submit", json=_acceptance(version_id))
    admin_client.post(f"/api/admin/pine-reviews/{version_id}/start", json={})
    rejected = admin_client.post(f"/api/admin/pine-reviews/{version_id}/reject", json={"note": "Unsupported repainting indicator."})
    assert rejected.status_code == 200

    view = client.get(f"/api/personal-pine-strategies/{strategy_id}").json()
    history = view["versions"][0]["review_history"]
    assert [event["decision"] for event in history] == ["started", "rejected"]
    assert history[-1]["note"] == "Unsupported repainting indicator."
    assert "reviewer_user_id" not in history[-1]
    assert str(admin.id) not in str(history)


def test_owner_can_withdraw_a_draft_but_not_an_approved_strategy(mu_db):
    owner = make_user("pine-withdraw-owner@example.com")
    admin = make_user("pine-withdraw-admin@example.com", is_admin=True)
    client, admin_client = _client(owner), _client(admin)

    draft = _create(client, name="Draft to withdraw")
    draft_id = draft["strategy"]["id"]
    foreign = _client(make_user("pine-withdraw-foreign@example.com"))
    assert foreign.delete(f"/api/personal-pine-strategies/{draft_id}").status_code == 404
    withdrawn = client.delete(f"/api/personal-pine-strategies/{draft_id}")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["deleted"] is True
    assert client.get(f"/api/personal-pine-strategies/{draft_id}").status_code == 404

    approved_strategy = _create(client, name="Approved, not withdrawable")
    strategy_id, version_id = approved_strategy["strategy"]["id"], approved_strategy["version"]["id"]
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/validate")
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{version_id}/submit", json=_acceptance(version_id))
    admin_client.post(f"/api/admin/pine-reviews/{version_id}/start", json={})
    admin_client.post(f"/api/admin/pine-reviews/{version_id}/approve", json={"acknowledge_warnings": True})
    blocked = client.delete(f"/api/personal-pine-strategies/{strategy_id}")
    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "APPROVED_VERSION_EXISTS"
    assert client.get(f"/api/personal-pine-strategies/{strategy_id}").status_code == 200


def test_failing_version_cannot_submit_and_changes_require_new_version(mu_db):
    owner = make_user("pine-changes@example.com")
    admin = make_user("pine-changes-admin@example.com", is_admin=True)
    client, admin_client = _client(owner), _client(admin)
    invalid = _create(client, VALID_PINE.replace('alert("EXIT", alert.freq_once_per_bar_close)', "plot(close)"))
    strategy_id, invalid_id = invalid["strategy"]["id"], invalid["version"]["id"]
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{invalid_id}/validate")
    assert client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{invalid_id}/submit", json=_acceptance(invalid_id)).status_code == 409

    corrected = client.post(f"/api/personal-pine-strategies/{strategy_id}/versions", json={
        "source": VALID_PINE, "filename": "corrected.pine",
    }).json()["version"]
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{corrected['id']}/validate")
    client.post(f"/api/personal-pine-strategies/{strategy_id}/versions/{corrected['id']}/submit", json=_acceptance(invalid_id))
    admin_client.post(f"/api/admin/pine-reviews/{corrected['id']}/start", json={})
    changed = admin_client.post(f"/api/admin/pine-reviews/{corrected['id']}/request-changes", json={"note": "Clarify exit"})
    assert changed.status_code == 200
    assert changed.json()["version"]["status"] == "changes_requested"
    source = client.get(f"/api/personal-pine-strategies/{strategy_id}/versions/{corrected['id']}/source").json()
    assert source["source"] == VALID_PINE
