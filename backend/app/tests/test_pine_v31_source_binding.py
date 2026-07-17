"""Owner-scoped V3.1 package source-lineage regression checks."""
from __future__ import annotations

import hashlib
import inspect

from sqlalchemy import select

from app.config import settings
from app.db import models
from app.db.engine import session_scope
from app.services import pine_conversion_service as service
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401
from app.tests.test_pine_conversion import _client, _create
from app.tests.test_pine_v3_package_assembly import LEGEND_SOURCE as ASSEMBLY_FIXTURE


LEGEND_5_13_REGRESSION_SOURCE = """//@version=6
indicator("Legend MACD ADX 5 13", overlay=true)
fast_length = input.int(5, "Fast Length")
slow_length = input.int(13, "Slow Length")
signal_length = input.int(9, "Signal Length")
dilen = input.int(10, "DI Length")
adxlen = input.int(14, "ADX Smoothing")
adx_threshold = input.float(25, "ADX Threshold")
dirmov(len) =>
    up = ta.change(high)
    down = -ta.change(low)
    truerange = ta.rma(ta.tr, len)
    plus = fixnan(100 * ta.rma(up > down and up > 0 ? up : 0, len) / truerange)
    minus = fixnan(100 * ta.rma(down > up and down > 0 ? down : 0, len) / truerange)
    [plus, minus]
adx(dilen, adxlen) =>
    [plus, minus] = dirmov(dilen)
    sum = plus + minus
    100 * ta.rma(math.abs(plus - minus) / (sum == 0 ? 1 : sum), adxlen)
fast_ma = ta.ema(close, fast_length)
slow_ma = ta.ema(close, slow_length)
macd = fast_ma - slow_ma
signal = ta.ema(macd, signal_length)
adx_value = adx(dilen, adxlen)
bullish = macd < 0 and ta.crossover(macd, signal) and adx_value > adx_threshold
bearish = macd > 0 and ta.crossunder(macd, signal) and adx_value > adx_threshold
plot(macd)
"""


def _package(client, created):
    path = f"/api/personal-pine-strategies/{created['strategy']['id']}/versions/{created['version']['id']}/conversion-package"
    return client.post(path)


def _enable_v31(monkeypatch):
    monkeypatch.setattr(settings, "PINE_CONVERSION_QUALIFICATION_PACKAGE_ENABLED", True)


def test_package_binds_exact_selected_source_and_hash(mu_db, monkeypatch):
    _enable_v31(monkeypatch)
    client = _client(make_user("v31-lineage@example.com"))
    created = _create(client, LEGEND_5_13_REGRESSION_SOURCE, name="Legend 5 13")
    response = _package(client, created)
    assert response.status_code == 200
    body, package = response.json(), response.json()["package"]
    expected_hash = hashlib.sha256(LEGEND_5_13_REGRESSION_SOURCE.encode()).hexdigest()
    assert body["source_sha256"] == created["version"]["source_sha256"] == expected_hash
    assert f"Selected source SHA-256: {expected_hash}" in package
    assert service._section(package, "BEGIN_UNTRUSTED_PINE_SOURCE", "END_UNTRUSTED_PINE_SOURCE") == LEGEND_5_13_REGRESSION_SOURCE
    assert package.count(LEGEND_5_13_REGRESSION_SOURCE) == 1
    for required in (
        "input.int(5", "input.int(13", "input.int(9", "input.int(10", "input.int(14",
        "input.float(25", "dirmov(len)", "adx(dilen, adxlen)", "macd < 0",
        "ta.crossover(macd, signal)", "macd > 0", "ta.crossunder(macd, signal)",
    ):
        assert required in package


def test_version_and_strategy_ids_cannot_cross_bind(mu_db, monkeypatch):
    _enable_v31(monkeypatch)
    client = _client(make_user("v31-versions@example.com"))
    first = _create(client, "//@version=6\nindicator(\"Version A\", overlay=true)\nplot(close)\n", name="A")
    second = _create(client, "//@version=6\nindicator(\"Version B\", overlay=true)\nplot(open)\n", name="B")
    crossed = client.post(
        f"/api/personal-pine-strategies/{first['strategy']['id']}/versions/{second['version']['id']}/conversion-package"
    )
    assert crossed.status_code == 404 and crossed.json()["reason"] == "NOT_FOUND"
    next_version = client.post(
        f"/api/personal-pine-strategies/{first['strategy']['id']}/versions",
        json={"source": "//@version=6\nindicator(\"Version A2\", overlay=true)\nplot(high)\n", "filename": "a2.pine"},
    ).json()
    next_version["strategy"] = first["strategy"]
    packaged_a = _package(client, first).json()["package"]
    packaged_a2 = _package(client, next_version).json()["package"]
    assert "Version A\"" in packaged_a and "Version A2" not in packaged_a
    assert "Version A2" in packaged_a2 and "Version A\"" not in packaged_a2
    assert "Version B" not in packaged_a and "Version B" not in packaged_a2


def test_foreign_owner_cannot_package_source(mu_db, monkeypatch):
    _enable_v31(monkeypatch)
    owner = _client(make_user("v31-owner@example.com"))
    foreign = _client(make_user("v31-foreign@example.com"))
    created = _create(owner, LEGEND_5_13_REGRESSION_SOURCE)
    response = _package(foreign, created)
    assert response.status_code == 404 and response.json()["reason"] == "NOT_FOUND"


def test_empty_selected_source_fails_closed(mu_db, monkeypatch):
    _enable_v31(monkeypatch)
    client = _client(make_user("v31-empty@example.com"))
    created = _create(client, LEGEND_5_13_REGRESSION_SOURCE)
    empty_hash = hashlib.sha256(b"").hexdigest()
    with session_scope() as db:
        version = db.get(models.StrategyVersion, created["version"]["id"])
        artifact = db.scalar(select(models.StrategySourceArtifact).where(models.StrategySourceArtifact.strategy_version_id == version.id))
        version.source_sha256 = empty_hash
        artifact.content = ""
        artifact.content_sha256 = empty_hash
    response = _package(client, created)
    assert response.status_code == 409 and response.json()["reason"] == "PINE_PACKAGE_SOURCE_INVALID"
    assert "BEGIN_UNTRUSTED_PINE_SOURCE" not in response.text


def test_stored_source_hash_mismatch_fails_closed(mu_db, monkeypatch):
    _enable_v31(monkeypatch)
    client = _client(make_user("v31-hash-mismatch@example.com"))
    created = _create(client, LEGEND_5_13_REGRESSION_SOURCE)
    with session_scope() as db:
        version = db.get(models.StrategyVersion, created["version"]["id"])
        version.source_sha256 = "0" * 64
    response = _package(client, created)
    assert response.status_code == 409 and response.json()["reason"] == "PINE_PACKAGE_SOURCE_INVALID"
    assert "Legend MACD" not in response.text


def test_runtime_package_path_has_no_fixture_fallback(mu_db, monkeypatch):
    _enable_v31(monkeypatch)
    client = _client(make_user("v31-no-fixture@example.com"))
    selected = "//@version=6\nindicator(\"Selected only\", overlay=true)\nplot(close)\n"
    package = _package(client, _create(client, selected)).json()["package"]
    assert selected in package and ASSEMBLY_FIXTURE not in package
    assert "app.tests" not in inspect.getsource(service)


def test_unicode_assembly_fixture_stays_test_only():
    assert "Preserve this comment and Unicode exactly" in ASSEMBLY_FIXTURE
    assert "Preserve this comment and Unicode exactly" not in inspect.getsource(service)
