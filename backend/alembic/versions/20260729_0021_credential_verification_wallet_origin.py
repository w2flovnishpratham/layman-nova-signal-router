"""Add credential verification status, durable wallet-fetch outcome, and trade origin/exit_trigger.

Revision ID: 0021_cred_verify_wallet_origin
Revises: 0020_engine_start_entry
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021_cred_verify_wallet_origin"
down_revision = "0020_engine_start_entry"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    if table not in set(sa.inspect(op.get_bind()).get_table_names()):
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    vault_columns = _columns("user_credential_vaults")
    with op.batch_alter_table("user_credential_vaults") as batch:
        if "connection_status" not in vault_columns:
            batch.add_column(
                sa.Column("connection_status", sa.String(30), nullable=False, server_default="NOT_CONFIGURED")
            )
        if "last_verified_at" not in vault_columns:
            batch.add_column(sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
        if "last_verification_error" not in vault_columns:
            batch.add_column(sa.Column("last_verification_error", sa.String(255), nullable=True))
        if "last_wallet_snapshot_at" not in vault_columns:
            batch.add_column(sa.Column("last_wallet_snapshot_at", sa.DateTime(timezone=True), nullable=True))

    trade_columns = _columns("portfolio_trades")
    with op.batch_alter_table("portfolio_trades") as batch:
        if "origin" not in trade_columns:
            batch.add_column(sa.Column("origin", sa.String(30), nullable=True))
        if "exit_trigger" not in trade_columns:
            batch.add_column(sa.Column("exit_trigger", sa.String(30), nullable=True))

    snapshot_columns = _columns("portfolio_snapshots")
    with op.batch_alter_table("portfolio_snapshots") as batch:
        if "fetch_status" not in snapshot_columns:
            batch.add_column(sa.Column("fetch_status", sa.String(20), nullable=False, server_default="ok"))
        if "safe_error_code" not in snapshot_columns:
            batch.add_column(sa.Column("safe_error_code", sa.String(60), nullable=True))


def downgrade() -> None:
    snapshot_columns = _columns("portfolio_snapshots")
    with op.batch_alter_table("portfolio_snapshots") as batch:
        if "safe_error_code" in snapshot_columns:
            batch.drop_column("safe_error_code")
        if "fetch_status" in snapshot_columns:
            batch.drop_column("fetch_status")

    trade_columns = _columns("portfolio_trades")
    with op.batch_alter_table("portfolio_trades") as batch:
        if "exit_trigger" in trade_columns:
            batch.drop_column("exit_trigger")
        if "origin" in trade_columns:
            batch.drop_column("origin")

    vault_columns = _columns("user_credential_vaults")
    with op.batch_alter_table("user_credential_vaults") as batch:
        if "last_wallet_snapshot_at" in vault_columns:
            batch.drop_column("last_wallet_snapshot_at")
        if "last_verification_error" in vault_columns:
            batch.drop_column("last_verification_error")
        if "last_verified_at" in vault_columns:
            batch.drop_column("last_verified_at")
        if "connection_status" in vault_columns:
            batch.drop_column("connection_status")
