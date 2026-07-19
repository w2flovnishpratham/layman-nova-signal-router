"""C2 migration metadata and reversible SQLite DDL checks."""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


BACKEND_ROOT = Path(__file__).resolve().parents[2]
C2_SETUP_COLUMNS = {
    "pine_conversion_request_id",
    "compile_evidence_id",
    "approved_candidate_sha256",
    "approved_source_sha256",
    "approved_strategy_layer_sha256",
    "installed_by_user_id",
    "current_credential_id",
    "hold_credential_id",
    "hold_webhook_event_id",
    "paper_eligible_at",
    "credential_revoked_at",
    "suspended_at",
}


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def test_c2_migration_up_down_up_cycle(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import engine as eng

    db_path = tmp_path / "c2_migration.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False)
    eng.reset_engine_for_tests()
    config = _config()

    try:
        command.upgrade(config, "head")
        engine = eng.get_engine()
        inspector = sa.inspect(engine)
        assert "tradingview_compile_evidence" in inspector.get_table_names()
        assert C2_SETUP_COLUMNS <= {
            column["name"] for column in inspector.get_columns("tradingview_setups")
        }
        assert {"approved_candidate_sha256", "installation_mode"} <= {
            column["name"] for column in inspector.get_columns("strategy_instances")
        }

        command.downgrade(config, "0015_r1b_persistence")
        inspector = sa.inspect(engine)
        assert "tradingview_compile_evidence" not in inspector.get_table_names()
        assert not C2_SETUP_COLUMNS & {
            column["name"] for column in inspector.get_columns("tradingview_setups")
        }
        assert not {"approved_candidate_sha256", "installation_mode"} & {
            column["name"] for column in inspector.get_columns("strategy_instances")
        }

        command.upgrade(config, "head")
        inspector = sa.inspect(engine)
        assert "tradingview_compile_evidence" in inspector.get_table_names()
        assert C2_SETUP_COLUMNS <= {
            column["name"] for column in inspector.get_columns("tradingview_setups")
        }
    finally:
        eng.reset_engine_for_tests()
