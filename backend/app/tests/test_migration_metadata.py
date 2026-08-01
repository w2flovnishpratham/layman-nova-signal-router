"""Alembic revision metadata guards.

The deployed ``alembic_version.version_num`` column is ``VARCHAR(32)``.
PostgreSQL rejects a longer revision id with StringDataRightTruncation while
recording it, but SQLite silently accepts any length — so a SQLite-only upgrade
test cannot catch this class of defect. These metadata checks can.
"""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Matches the deployed alembic_version.version_num definition.
ALEMBIC_VERSION_NUM_MAXLEN = 32


def _script_directory() -> ScriptDirectory:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config()
    config.set_main_option("script_location", str(backend_root / "alembic"))
    return ScriptDirectory.from_config(config)


def test_every_revision_id_fits_alembic_version_column():
    script = _script_directory()
    offenders = [rev.revision for rev in script.walk_revisions() if len(rev.revision) > ALEMBIC_VERSION_NUM_MAXLEN]
    assert not offenders, (
        f"Alembic revision id(s) exceed alembic_version.version_num "
        f"VARCHAR({ALEMBIC_VERSION_NUM_MAXLEN}): {offenders}"
    )


def test_single_head_is_user_preferences_off_admin_review_width():
    script = _script_directory()
    # One head, never a branch: two heads mean an ambiguous upgrade target.
    assert script.get_heads() == ["0022_versioned_risk"]
    head = script.get_revision("0022_versioned_risk")
    assert head.down_revision == "0021_cred_verify_wallet_origin"
    assert len(head.revision) <= ALEMBIC_VERSION_NUM_MAXLEN
    head = script.get_revision("0021_cred_verify_wallet_origin")
    assert head.down_revision == "0020_engine_start_entry"
    assert len(head.revision) <= ALEMBIC_VERSION_NUM_MAXLEN
    head = script.get_revision("0019_trading_config_revisions")
    assert head.down_revision == "0018_user_preferences"
    assert len(head.revision) <= ALEMBIC_VERSION_NUM_MAXLEN
    head = script.get_revision("0018_user_preferences")
    assert head.down_revision == "0017_expand_admin_review_status"
    assert len(head.revision) <= ALEMBIC_VERSION_NUM_MAXLEN
    head = script.get_revision("0017_expand_admin_review_status")
    assert head.down_revision == "0016_c2_tv_installation"
    assert len(head.revision) <= ALEMBIC_VERSION_NUM_MAXLEN
    c2 = script.get_revision("0016_c2_tv_installation")
    assert c2.down_revision == "0015_r1b_persistence"
    r1b = script.get_revision("0015_r1b_persistence")
    assert r1b.down_revision == "0014_verify_select"
    previous = script.get_revision("0014_verify_select")
    assert previous.down_revision == "0013_manual_tradingview_flow"
