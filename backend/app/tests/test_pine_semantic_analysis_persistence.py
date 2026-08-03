"""R1B-1 immutable Pine semantic-analysis provenance writer."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401

VALID_SOURCE = '//@version=6\nstrategy("r1b-writer")\nstrategy.entry("L", strategy.long)\n'
PARTIAL_SOURCE = (
    '//@version=6\nstrategy("r1b-partial")\nlvl = close - ta.atr(14)\n'
    'strategy.entry("L", strategy.long, limit=lvl)\n'
)
MALFORMED_SOURCE = '//@version=6\nindicator("r1b-broken")\nplot(close\n'


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_artifact(db, source: str, *, owner=None):
    from app.db import models

    owner = owner or models.User(email=f"w-{uuid.uuid4().hex[:10]}@example.com")
    if owner.id is None:
        db.add(owner)
        db.flush()
    strategy = models.StrategyCatalog(
        code=f"w-{uuid.uuid4().hex[:10]}", display_name="W", owner_type="personal",
        owner_user_id=owner.id, visibility="private", status="active",
    )
    db.add(strategy)
    db.flush()
    version = models.StrategyVersion(
        strategy_id=strategy.id, version="1.0", payload_spec_version="nova.pine.v1",
        source_journey="nova_hosted_personal", execution_kind="nova_runtime",
    )
    db.add(version)
    db.flush()
    artifact = models.StrategySourceArtifact(
        strategy_version_id=version.id, artifact_type="pine_script",
        content=source, content_sha256=_sha(source),
    )
    db.add(artifact)
    db.flush()
    return owner, artifact


def _analysis_count(db) -> int:
    from app.db import models

    return db.scalar(select(func.count()).select_from(models.PineSemanticAnalysis))


@pytest.fixture
def analysis_flag_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "R1B_PINE_ANALYSIS_PERSISTENCE", True, raising=False)


@pytest.fixture
def db(mu_db):  # noqa: F811
    from app.db.engine import session_scope

    with session_scope() as session:
        yield session


def test_flag_off_refuses_and_writes_nothing(db):
    from app.config import settings
    from app.services import pine_semantic_analysis_persistence as svc

    assert settings.R1B_PINE_ANALYSIS_PERSISTENCE is False
    _, artifact = _seed_artifact(db, VALID_SOURCE)
    with pytest.raises(svc.SemanticAnalysisPersistenceError) as excinfo:
        svc.persist_semantic_analysis(db, artifact, VALID_SOURCE)
    assert excinfo.value.code == "PERSISTENCE_DISABLED"
    assert _analysis_count(db) == 0


def test_happy_path_binds_exact_provenance(db, analysis_flag_on):
    from app.domain.pine_capabilities import load_registry
    from app.services import pine_semantic_analysis_persistence as svc
    from app.services.pine_semantic_preanalyzer import ANALYZER_VERSION, analyze_source

    owner, artifact = _seed_artifact(db, VALID_SOURCE)
    row = svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)
    registry = load_registry()
    result = analyze_source(VALID_SOURCE)

    assert row.source_artifact_id == artifact.id
    assert row.source_sha256 == _sha(VALID_SOURCE) == artifact.content_sha256
    assert row.analyzer_version == ANALYZER_VERSION
    assert row.registry_id == registry.registry_id
    assert row.registry_version == registry.registry_version
    assert row.registry_sha256 == registry.sha256
    assert row.analysis_schema_version == svc.ANALYSIS_SCHEMA_VERSION
    assert row.effective_capability_level == result.effective_capability_level.value
    assert row.confidence == result.confidence.value

    payload = row.analysis_payload
    assert set(payload) == {
        "matched_capabilities", "temporal_classes", "blocker_codes",
        "disclosure_codes", "admin_review_points",
    }
    for values in payload.values():
        assert values == sorted(set(values))
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert len(encoded) <= svc.MAX_ANALYSIS_PAYLOAD_BYTES
    assert row.analysis_payload_sha256 == hashlib.sha256(encoded).hexdigest()

    # Deterministic payload hash across a second serialization.
    _, encoded_again = svc.canonical_analysis_payload(result)
    assert hashlib.sha256(encoded_again).hexdigest() == row.analysis_payload_sha256

    # The Pine source never lands in the row or payload.
    for value in (payload, row.analysis_payload_sha256, row.registry_id, row.confidence):
        assert "strategy.entry" not in json.dumps(value)
    assert VALID_SOURCE not in json.dumps(payload)


def test_identical_provenance_reuses_the_same_row(db, analysis_flag_on):
    from app.services import pine_semantic_analysis_persistence as svc

    owner, artifact = _seed_artifact(db, VALID_SOURCE)
    first = svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)
    second = svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)
    assert first.id == second.id
    assert _analysis_count(db) == 1


def test_changed_provenance_inserts_new_immutable_rows(db, analysis_flag_on, monkeypatch):
    from app.services import pine_semantic_analysis_persistence as svc
    from app.services import pine_semantic_preanalyzer

    owner, artifact = _seed_artifact(db, VALID_SOURCE)
    first = svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)

    # Different analyzer version → new row.
    with monkeypatch.context() as patch:
        patch.setattr(pine_semantic_preanalyzer, "ANALYZER_VERSION", "nova.pine-semantic-preanalyzer.v2")
        second = svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)
    assert second.id != first.id

    # Different registry SHA → new row.
    real_analyze = svc.analyze_source

    def _other_registry(source):
        return dataclasses.replace(real_analyze(source), registry_sha256="b" * 64)

    with monkeypatch.context() as patch:
        patch.setattr(svc, "analyze_source", _other_registry)
        third = svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)
    assert third.id not in {first.id, second.id}

    # Different persistence schema version → new row, supersedes the first.
    with monkeypatch.context() as patch:
        patch.setattr(svc, "ANALYSIS_SCHEMA_VERSION", "nova.pine-semantic-analysis-persistence.v2")
        fourth = svc.persist_semantic_analysis(
            db, artifact, VALID_SOURCE, owner_user_id=owner.id, supersedes_analysis_id=first.id
        )
    assert fourth.id not in {first.id, second.id, third.id}
    assert fourth.supersedes_analysis_id == first.id
    assert _analysis_count(db) == 4


def test_source_hash_verification_fails_closed(db, analysis_flag_on, monkeypatch):
    from app.services import pine_semantic_analysis_persistence as svc

    owner, artifact = _seed_artifact(db, VALID_SOURCE)

    # Text that does not match the pinned artifact hash.
    with pytest.raises(svc.SemanticAnalysisPersistenceError) as excinfo:
        svc.persist_semantic_analysis(db, artifact, VALID_SOURCE + "// tampered\n", owner_user_id=owner.id)
    assert excinfo.value.code == "SOURCE_HASH_MISMATCH"

    # The analyzer reporting a different source hash (mutation between the two
    # checks) also fails closed.
    real_analyze = svc.analyze_source
    monkeypatch.setattr(
        svc,
        "analyze_source",
        lambda source: dataclasses.replace(real_analyze(source), source_sha256="0" * 64),
    )
    with pytest.raises(svc.SemanticAnalysisPersistenceError) as excinfo:
        svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)
    assert excinfo.value.code == "SOURCE_HASH_MISMATCH"
    assert _analysis_count(db) == 0


def test_invalid_registry_provenance_creates_no_row(db, analysis_flag_on, monkeypatch, tmp_path):
    from app.services import pine_semantic_analysis_persistence as svc
    from app.services.pine_semantic_preanalyzer import SCHEMA_PATH, analyze_source

    owner, artifact = _seed_artifact(db, VALID_SOURCE)
    monkeypatch.setattr(
        svc,
        "analyze_source",
        lambda source: analyze_source(source, registry_path=tmp_path / "missing.json", schema_path=SCHEMA_PATH),
    )
    with pytest.raises(svc.SemanticAnalysisPersistenceError) as excinfo:
        svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)
    assert excinfo.value.code == "REGISTRY_PROVENANCE_INVALID"
    assert _analysis_count(db) == 0


def test_indeterminate_and_partial_results_persist_truthfully(db, analysis_flag_on):
    from app.services import pine_semantic_analysis_persistence as svc

    owner, malformed = _seed_artifact(db, MALFORMED_SOURCE)
    row = svc.persist_semantic_analysis(db, malformed, MALFORMED_SOURCE, owner_user_id=owner.id)
    assert row.confidence == "ANALYSIS_INDETERMINATE"
    assert row.effective_capability_level == "L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE"

    owner2, partial = _seed_artifact(db, PARTIAL_SOURCE)
    partial_row = svc.persist_semantic_analysis(db, partial, PARTIAL_SOURCE, owner_user_id=owner2.id)
    assert partial_row.confidence == "PARTIAL_MATCH"
    assert partial_row.effective_capability_level == "L3_REQUIRES_BACKEND_CAPABILITY"


class _RaceSession:
    """First provenance lookup sees nothing, simulating a concurrent insert
    that is not yet visible; the unique tuple must still yield one row."""

    def __init__(self, real):
        self._real = real
        self._scalar_calls = 0

    def scalar(self, statement):
        self._scalar_calls += 1
        if self._scalar_calls == 1:
            return None
        return self._real.scalar(statement)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_concurrent_identical_inserts_create_one_row(db, analysis_flag_on):
    from app.services import pine_semantic_analysis_persistence as svc

    _, artifact = _seed_artifact(db, VALID_SOURCE)
    first = svc.persist_semantic_analysis(db, artifact, VALID_SOURCE)
    winner = svc.persist_semantic_analysis(_RaceSession(db), artifact, VALID_SOURCE)
    assert winner.id == first.id
    assert _analysis_count(db) == 1


def test_cross_owner_artifact_misuse_fails(db, analysis_flag_on):
    from app.db import models
    from app.services import pine_semantic_analysis_persistence as svc

    _, artifact = _seed_artifact(db, VALID_SOURCE)
    intruder = models.User(email=f"intruder-{uuid.uuid4().hex[:8]}@example.com")
    db.add(intruder)
    db.flush()
    with pytest.raises(svc.SemanticAnalysisPersistenceError) as excinfo:
        svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=intruder.id)
    assert excinfo.value.code == "ARTIFACT_OWNERSHIP_MISMATCH"
    assert _analysis_count(db) == 0


class _FailingSession:
    def __init__(self, real):
        self._real = real

    def scalar(self, statement):
        raise OperationalError("SELECT secret", {}, Exception("boom"))

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_database_failures_become_safe_internal_errors(db, analysis_flag_on, caplog):
    from app.services import pine_semantic_analysis_persistence as svc

    _, artifact = _seed_artifact(db, VALID_SOURCE)
    with caplog.at_level("DEBUG"):
        with pytest.raises(svc.SemanticAnalysisPersistenceError) as excinfo:
            svc.persist_semantic_analysis(_FailingSession(db), artifact, VALID_SOURCE)
    assert excinfo.value.code == "PERSISTENCE_UNAVAILABLE"
    assert "boom" not in str(excinfo.value)
    assert "strategy.entry" not in caplog.text  # raw Pine source is never logged
    assert VALID_SOURCE not in caplog.text


def test_writer_exposes_no_update_and_imports_no_network(db):
    import ast

    from app.services import pine_semantic_analysis_persistence as svc

    public = [name for name in dir(svc) if not name.startswith("_")]
    assert not [name for name in public if "update" in name.lower() or "mutate" in name.lower()]

    tree = ast.parse(Path(svc.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    forbidden = (
        "requests", "httpx", "urllib", "socket", "aiohttp", "websocket",
        "anthropic", "openai", "tradingview", "dhan", "broker",
        "pine_conversion_provider", "dhan_client", "paper_broker",
    )
    for name in imported:
        assert not any(token in name.lower() for token in forbidden), name


def test_owner_query_db_failure_is_translated_r1b2a(db, analysis_flag_on):
    """R1B-2A envelope fix (review finding 1): a database failure during the
    owner-chain query becomes the closed PERSISTENCE_UNAVAILABLE code."""
    from sqlalchemy.exc import OperationalError

    from app.services import pine_semantic_analysis_persistence as svc

    owner, artifact = _seed_artifact(db, VALID_SOURCE)

    class _OwnerQueryDead:
        def scalar(self, *_a, **_k):
            raise OperationalError("connect to server at 10.0.0.1 failed", {}, Exception("down"))

        def __getattr__(self, name):
            return getattr(db, name)

    with pytest.raises(svc.SemanticAnalysisPersistenceError) as excinfo:
        svc.persist_semantic_analysis(_OwnerQueryDead(), artifact, VALID_SOURCE, owner_user_id=owner.id)
    assert excinfo.value.code == "PERSISTENCE_UNAVAILABLE"
    assert "10.0.0.1" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert _analysis_count(db) == 0


def test_analyzer_exception_is_translated_r1b2a(db, analysis_flag_on, monkeypatch):
    """R1B-2A envelope fix (review finding 2): an unexpected analyzer raise
    becomes the closed ANALYSIS_UNAVAILABLE code with no leaked content."""
    from app.services import pine_semantic_analysis_persistence as svc

    owner, artifact = _seed_artifact(db, VALID_SOURCE)

    def _explode(source):
        raise RuntimeError("analyzer blew up at C:\\secret\\path with nwk_leaked_token")

    monkeypatch.setattr(svc, "analyze_source", _explode)
    with pytest.raises(svc.SemanticAnalysisPersistenceError) as excinfo:
        svc.persist_semantic_analysis(db, artifact, VALID_SOURCE, owner_user_id=owner.id)
    assert excinfo.value.code == "ANALYSIS_UNAVAILABLE"
    message = str(excinfo.value)
    assert "nwk_leaked_token" not in message
    assert "secret" not in message
    assert excinfo.value.__cause__ is None
    assert _analysis_count(db) == 0


def test_owner_db_failure_yields_closed_503_at_api_r1b2a(mu_db, monkeypatch):  # noqa: F811
    """End-to-end: the previously-leaking owner-query failure now surfaces as
    the closed 503 SEMANTIC_PROVENANCE_UNAVAILABLE with nothing recorded."""
    from sqlalchemy.exc import OperationalError

    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import pine_semantic_analysis_persistence as svc
    from app.tests.test_personal_pine import _client, _create

    user = make_user(f"env-{uuid.uuid4().hex[:8]}@example.com")
    client = _client(user)
    created = _create(client)
    monkeypatch.setattr(settings, "R1B_PINE_ANALYSIS_PERSISTENCE", True, raising=False)

    real_verify = svc._verify_owner

    def _dead_verify(session, artifact, owner_user_id):
        raise OperationalError("connect to server at 10.0.0.1 failed", {}, Exception("down"))

    monkeypatch.setattr(svc, "_verify_owner", _dead_verify)
    response = _validate(client, created)
    monkeypatch.setattr(svc, "_verify_owner", real_verify)
    assert response.status_code == 503
    assert response.json()["reason"] == "SEMANTIC_PROVENANCE_UNAVAILABLE"
    assert "10.0.0.1" not in response.text and "OperationalError" not in response.text
    with session_scope() as db:
        assert _analysis_count(db) == 0
        assert db.scalar(select(func.count()).select_from(models.StrategyValidationReport)) == 0
        version = db.get(models.StrategyVersion, uuid.UUID(created["version"]["id"]))
        assert version.status == "draft"


# ---------------------------------------------------------------------------
# Qualification-flow integration (owner-scoped validate endpoint)
# ---------------------------------------------------------------------------


def _validate(client, created):
    return client.post(
        f"/api/personal-pine-strategies/{created['strategy']['id']}/versions/{created['version']['id']}/validate"
    )


def test_qualification_flow_off_and_on(mu_db, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.tests.test_personal_pine import _client, _create

    user = make_user(f"q-{uuid.uuid4().hex[:8]}@example.com")
    client = _client(user)
    created = _create(client)

    # Flag off: no analysis row, no new response key, behavior unchanged.
    response = _validate(client, created)
    assert response.status_code == 200, response.text
    assert "semantic_analysis_id" not in response.json()
    with session_scope() as db:
        assert _analysis_count(db) == 0

    # Flag on: provenance is reused-or-inserted and referenced by ID only.
    monkeypatch.setattr(settings, "R1B_PINE_ANALYSIS_PERSISTENCE", True, raising=False)
    response = _validate(client, created)
    assert response.status_code == 200, response.text
    body = response.json()
    analysis_id = body["semantic_analysis_id"]
    assert uuid.UUID(analysis_id)
    assert "analysis_payload" not in json.dumps(body)
    with session_scope() as db:
        row = db.get(models.PineSemanticAnalysis, uuid.UUID(analysis_id))
        assert row is not None
        assert _analysis_count(db) == 1

    # Re-validation reuses the same provenance row.
    again = _validate(client, created)
    assert again.status_code == 200
    assert again.json()["semantic_analysis_id"] == analysis_id
    with session_scope() as db:
        assert _analysis_count(db) == 1


def test_qualification_is_not_recorded_when_persistence_fails(mu_db, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.db import models
    from app.db.engine import session_scope
    from app.services import pine_semantic_analysis_persistence as svc
    from app.tests.test_personal_pine import _client, _create

    user = make_user(f"qf-{uuid.uuid4().hex[:8]}@example.com")
    client = _client(user)
    created = _create(client)
    monkeypatch.setattr(settings, "R1B_PINE_ANALYSIS_PERSISTENCE", True, raising=False)

    def _boom(*args, **kwargs):
        raise svc.SemanticAnalysisPersistenceError("PERSISTENCE_UNAVAILABLE")

    monkeypatch.setattr(svc, "persist_semantic_analysis", _boom)
    response = _validate(client, created)
    assert response.status_code == 503
    assert response.json()["reason"] == "SEMANTIC_PROVENANCE_UNAVAILABLE"

    with session_scope() as db:
        assert _analysis_count(db) == 0
        assert db.scalar(select(func.count()).select_from(models.StrategyValidationReport)) == 0
        version = db.get(models.StrategyVersion, uuid.UUID(created["version"]["id"]))
        assert version.status == "draft"  # not falsely marked validated/approved
