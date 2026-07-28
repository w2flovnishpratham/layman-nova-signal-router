# ruff: noqa: F811
"""One coherent revision for strategy setup + risk settings.

The failure these tests exist for: two separate saves could leave the strategy
half applied with the old limits, or new (looser) limits applied with the old
sizing. Every test here asserts that a failure changes *nothing*.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import setup_configuration, state_store
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_runtime_reliability import runtime  # noqa: F401

SETUP = {"direction": "BOTH", "lots": 2, "stop_loss_percent": 8.5, "take_profit_percent": 17}
RISK = {"max_daily_loss": 10000, "max_trades_per_day": 3}


def _client(user) -> TestClient:
    from app.auth.dependencies import get_current_user
    from app.routers import strategies
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(strategies.router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user)
    return TestClient(app)


def _seed() -> None:
    from app.services.strategy_registry import backfill_supertrend

    backfill_supertrend()


def _ready(email: str):
    _seed()
    user = make_user(email)
    client = _client(user)
    assert client.put("/api/strategies/catalog/selection", json={"strategy_key": "nova-supertrend"}).status_code == 200
    return user, client


def _save(client, *, setup=None, risk=None, revision=None):
    body = {
        "strategy_key": "nova-supertrend",
        "mode": "paper",
        "setup": SETUP if setup is None else setup,
        "risk": RISK if risk is None else risk,
    }
    if revision is not None:
        body["expected_revision"] = revision
    return client.put("/api/setup/configuration", json=body)


def _saved_setup(client) -> dict:
    catalog = client.get("/api/strategies/catalog").json()
    entry = next(s for s in catalog["strategies"] if s["strategy_key"] == "nova-supertrend")
    return entry["saved_setup"].get("paper") or {}


def test_a_successful_save_commits_both_sections_under_one_revision(mu_db, runtime):
    _, client = _ready("atomic-ok@example.com")
    response = _save(client, revision=0)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["configuration_revision"] == 1
    assert body["setup"]["lots"] == 2
    assert body["risk"]["max_daily_loss"] == 10000
    # Both halves carry the same revision.
    assert _saved_setup(client)["configuration_revision"] == 1
    assert state_store.get_runtime_settings()["configuration_revision"] == 1
    assert state_store.get_runtime_settings()["max_trades_per_day"] == 3


def test_strategy_validation_failure_changes_nothing(mu_db, runtime):
    _, client = _ready("atomic-bad-setup@example.com")
    before = dict(state_store.get_runtime_settings())

    response = _save(client, setup={**SETUP, "lots": 99}, revision=0)
    assert response.status_code == 422

    after = state_store.get_runtime_settings()
    assert after["max_daily_loss"] == before["max_daily_loss"]
    assert after["configuration_revision"] == 0
    assert _saved_setup(client) == {}  # the strategy half never landed either


def test_risk_validation_failure_changes_nothing(mu_db, runtime):
    _, client = _ready("atomic-bad-risk@example.com")

    # A malformed cutoff must not commit the strategy half.
    response = _save(client, risk={**RISK, "entry_cutoff_ist": "25:00"}, revision=0)
    assert response.status_code == 422, response.text

    assert _saved_setup(client) == {}
    assert state_store.get_runtime_settings()["configuration_revision"] == 0


def test_a_commit_failure_after_the_settings_write_is_compensated(mu_db, runtime, monkeypatch):
    """The residual window: settings landed, then the DB commit failed.

    A loosening change must not survive on its own - the previous settings are
    restored, so the engine never runs new limits against old sizing.
    """
    import contextlib

    from app.routers.setup import normalize_risk_values

    user, client = _ready("atomic-persist@example.com")
    assert _save(client, revision=0).status_code == 200
    assert state_store.get_runtime_settings()["max_daily_loss"] == 10000

    real_scope = setup_configuration.session_scope

    @contextlib.contextmanager
    def failing_commit():
        with real_scope() as db:
            yield db
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(setup_configuration, "session_scope", failing_commit)

    loose = {"max_daily_loss": 25000, "max_trades_per_day": 8}
    with pytest.raises(RuntimeError):
        setup_configuration.save_configuration(
            user.id,
            strategy_key="nova-supertrend",
            mode="paper",
            setup_values=SETUP,
            risk_values=loose,
            expected_revision=1,
            normalize_risk=normalize_risk_values,
        )

    after = state_store.get_runtime_settings()
    assert after["max_daily_loss"] == 10000, "a failed save must not loosen the daily loss cap"
    assert after["max_trades_per_day"] == 3
    assert after["configuration_revision"] == 1


def test_a_stale_revision_returns_conflict_and_does_not_overwrite(mu_db, runtime):
    _, client = _ready("atomic-stale@example.com")
    assert _save(client, revision=0).status_code == 200

    # A second client still holding revision 0.
    stale = _save(client, setup={**SETUP, "lots": 5}, revision=0)
    assert stale.status_code == 409
    body = stale.json()
    assert body["code"] == "REVISION_CONFLICT"
    assert body["expected_revision"] == 0
    assert body["current_revision"] == 1

    assert _saved_setup(client)["lots"] == 2  # the newer configuration survived


def test_each_successful_save_returns_exactly_one_new_revision(mu_db, runtime):
    _, client = _ready("atomic-seq@example.com")
    first = _save(client, revision=0).json()["configuration_revision"]
    second = _save(client, setup={**SETUP, "lots": 3}, revision=first).json()["configuration_revision"]
    assert (first, second) == (1, 2)
    assert _saved_setup(client)["configuration_revision"] == 2


def test_a_repeated_save_with_the_same_stale_revision_is_rejected_not_duplicated(mu_db, runtime):
    """A double submit cannot apply the same change twice."""
    _, client = _ready("atomic-double@example.com")
    assert _save(client, revision=0).status_code == 200
    repeat = _save(client, revision=0)
    assert repeat.status_code == 409
    assert state_store.get_runtime_settings()["configuration_revision"] == 1


def test_the_read_endpoint_reports_the_revision_a_save_must_echo(mu_db, runtime):
    _, client = _ready("atomic-read@example.com")
    assert client.get("/api/setup/configuration").json()["revision"] == 0
    _save(client, revision=0)
    state = client.get("/api/setup/configuration").json()
    assert state["revision"] == 1
    assert state["coherent"] is True
    assert state["complete"] is True


def test_start_engine_refuses_a_torn_configuration(mu_db, runtime):
    """The gate the residual crash window relies on."""
    from app.services import strategy_catalog_service

    user, client = _ready("atomic-torn@example.com")
    assert _save(client, revision=0).status_code == 200

    # Simulate a crash between the settings write and the DB commit: the
    # settings moved on, the strategy half did not.
    settings = state_store.get_runtime_settings()
    settings["configuration_revision"] = 2
    state_store.set_runtime_settings(settings)

    selection = strategy_catalog_service.instances.get_engine_selection(user.id)
    with pytest.raises(Exception) as excinfo:
        strategy_catalog_service.apply_selected_setup(user.id, selection["selected"])
    assert "different revisions" in str(excinfo.value)

    with pytest.raises(setup_configuration.ConfigurationConflict):
        setup_configuration.committed_revision(user.id)


def test_a_coherent_configuration_starts_normally(mu_db, runtime):
    from app.services import strategy_catalog_service

    user, client = _ready("atomic-coherent@example.com")
    assert _save(client, revision=0).status_code == 200
    selection = strategy_catalog_service.instances.get_engine_selection(user.id)
    applied = strategy_catalog_service.apply_selected_setup(user.id, selection["selected"])
    assert applied["lots"] == 2
    assert setup_configuration.committed_revision(user.id) == 1


def test_a_runtime_file_write_failure_rolls_the_database_back(mu_db, runtime, monkeypatch):
    """The settings write is inside the transaction, so its failure undoes the DB half."""
    from app.routers.setup import normalize_risk_values

    user, client = _ready("atomic-filefail@example.com")

    def exploding_write(_data):
        raise OSError("simulated runtime-settings write failure")

    original_write = setup_configuration.set_runtime_settings
    monkeypatch.setattr(setup_configuration, "set_runtime_settings", exploding_write)

    with pytest.raises(OSError):
        setup_configuration.save_configuration(
            user.id,
            strategy_key="nova-supertrend",
            mode="paper",
            setup_values=SETUP,
            risk_values=RISK,
            expected_revision=0,
            normalize_risk=normalize_risk_values,
        )

    monkeypatch.setattr(setup_configuration, "set_runtime_settings", original_write)
    assert _saved_setup(client) == {}, "the strategy half must not survive a failed settings write"
    assert state_store.get_runtime_settings()["configuration_revision"] == 0


def test_a_database_failure_before_the_settings_write_changes_nothing(mu_db, runtime, monkeypatch):
    from app.routers.setup import normalize_risk_values

    user, client = _ready("atomic-dbfail@example.com")
    before = dict(state_store.get_runtime_settings())

    def exploding_now():
        raise RuntimeError("simulated mid-transaction database failure")

    original_now = setup_configuration.strategy_catalog_service._now
    monkeypatch.setattr(setup_configuration.strategy_catalog_service, "_now", exploding_now)

    with pytest.raises(RuntimeError):
        setup_configuration.save_configuration(
            user.id,
            strategy_key="nova-supertrend",
            mode="paper",
            setup_values=SETUP,
            risk_values=RISK,
            expected_revision=0,
            normalize_risk=normalize_risk_values,
        )

    monkeypatch.setattr(setup_configuration.strategy_catalog_service, "_now", original_now)
    after = state_store.get_runtime_settings()
    assert after["configuration_revision"] == before["configuration_revision"] == 0
    assert after["max_daily_loss"] == before["max_daily_loss"]
    assert _saved_setup(client) == {}


def test_a_failed_compensating_restore_is_audited_and_still_raises(mu_db, runtime, monkeypatch):
    """Worst case: the commit failed AND the restore failed.

    The original error must still surface, and the failure must be recorded, so
    the torn state is never silent. The Start Engine gate is what protects
    trading from here.
    """
    import contextlib

    from app.routers.setup import normalize_risk_values

    user, client = _ready("atomic-restorefail@example.com")
    assert _save(client, revision=0).status_code == 200

    real_scope = setup_configuration.session_scope
    real_set = setup_configuration.set_runtime_settings
    real_log = setup_configuration.log_audit_event
    audited: list[str] = []

    @contextlib.contextmanager
    def failing_commit():
        with real_scope() as db:
            yield db
            # Raise before the real session scope exits so its rollback path
            # runs. Raising after this block would simulate a post-commit
            # caller failure, not a failed commit.
            raise RuntimeError("simulated commit failure")

    calls = {"n": 0}

    def set_then_fail(data):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_set(data)
        raise OSError("simulated restore failure")

    monkeypatch.setattr(setup_configuration, "session_scope", failing_commit)
    monkeypatch.setattr(setup_configuration, "set_runtime_settings", set_then_fail)
    monkeypatch.setattr(setup_configuration, "log_audit_event",
                        lambda event, *a, **k: audited.append(event))

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        setup_configuration.save_configuration(
            user.id,
            strategy_key="nova-supertrend",
            mode="paper",
            setup_values={**SETUP, "lots": 4},
            risk_values={"max_daily_loss": 25000, "max_trades_per_day": 8},
            expected_revision=1,
            normalize_risk=normalize_risk_values,
        )

    assert "CONFIGURATION_ROLLBACK_FAILED" in audited
    # Restore only this test's service patches. ``monkeypatch.undo()`` would
    # also tear down the database/runtime isolation supplied by the fixtures,
    # causing the coherence assertion below to inspect unrelated stores.
    monkeypatch.setattr(setup_configuration, "session_scope", real_scope)
    monkeypatch.setattr(setup_configuration, "set_runtime_settings", real_set)
    monkeypatch.setattr(setup_configuration, "log_audit_event", real_log)
    # The resulting state is torn, and that is exactly what the gate detects.
    state = setup_configuration.current_revision(user.id)
    assert state["coherent"] is False
    with pytest.raises(setup_configuration.ConfigurationConflict):
        setup_configuration.committed_revision(user.id)


def test_a_restart_with_matching_revisions_starts_normally(mu_db, runtime):
    """Simulates a process restart: state is re-read from disk and the database."""
    from app.services import strategy_catalog_service

    user, client = _ready("atomic-restart-ok@example.com")
    assert _save(client, revision=0).status_code == 200

    # Nothing cached: both halves are read fresh, as they would be after a restart.
    state = setup_configuration.current_revision(user.id)
    assert (state["settings_revision"], state["setup_revision"], state["coherent"]) == (1, 1, True)
    selection = strategy_catalog_service.instances.get_engine_selection(user.id)
    assert strategy_catalog_service.apply_selected_setup(user.id, selection["selected"])["lots"] == 2
