"""C2 TradingView compile evidence and installation bindings.

Revision ID: 0016_c2_tv_installation
Revises: 0015_r1b_persistence

This is additive control-plane state only. It does not alter R1B evidence,
broker, order, position, or execution-authority tables.
"""
from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0016_c2_tv_installation"
down_revision = "0015_r1b_persistence"
branch_labels = None
depends_on = None

_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "tradingview_compile_evidence" not in tables:
        op.create_table(
            "tradingview_compile_evidence",
            sa.Column("id", models.GUID(), primary_key=True),
            sa.Column(
                "pine_conversion_request_id",
                models.GUID(),
                sa.ForeignKey(
                    "pine_conversion_requests.id",
                    name="fk_tv_compile_evidence_conversion",
                    ondelete="RESTRICT",
                ),
                nullable=False,
            ),
            sa.Column(
                "candidate_version_id",
                models.GUID(),
                sa.ForeignKey(
                    "strategy_versions.id",
                    name="fk_tv_compile_evidence_candidate",
                    ondelete="RESTRICT",
                ),
                nullable=False,
            ),
            sa.Column("result", sa.String(20), nullable=False),
            sa.Column("source_sha256", sa.String(64), nullable=False),
            sa.Column("strategy_layer_sha256", sa.String(64), nullable=False),
            sa.Column("candidate_sha256", sa.String(64), nullable=False),
            sa.Column("prompt_version", sa.String(20), nullable=False),
            sa.Column("prompt_sha256", sa.String(64), nullable=False),
            sa.Column("transport_version", sa.String(30), nullable=False),
            sa.Column("transport_sha256", sa.String(64), nullable=False),
            sa.Column("compiler_error_summary", sa.Text(), nullable=True),
            sa.Column("setup_notes", sa.Text(), nullable=True),
            sa.Column(
                "admin_user_id",
                models.GUID(),
                sa.ForeignKey(
                    "users.id",
                    name="fk_tv_compile_evidence_admin",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
            sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "pine_conversion_request_id",
                name="uq_tv_compile_evidence_conversion",
            ),
        )
        op.create_index(
            "ix_tv_compile_evidence_pine_conversion_request_id",
            "tradingview_compile_evidence",
            ["pine_conversion_request_id"],
        )
        op.create_index(
            "ix_tv_compile_evidence_candidate",
            "tradingview_compile_evidence",
            ["candidate_version_id", "created_at"],
        )

    instance_columns = _columns("strategy_instances")
    with op.batch_alter_table(
        "strategy_instances", naming_convention=_NAMING_CONVENTION
    ) as batch:
        if "approved_candidate_sha256" not in instance_columns:
            batch.add_column(sa.Column("approved_candidate_sha256", sa.String(64), nullable=True))
        if "installation_mode" not in instance_columns:
            batch.add_column(sa.Column("installation_mode", sa.String(20), nullable=True))

    setup_columns = _columns("tradingview_setups")
    additions = {
        "pine_conversion_request_id": sa.Column(
            "pine_conversion_request_id",
            models.GUID(),
            sa.ForeignKey(
                "pine_conversion_requests.id",
                name="fk_tv_setup_c2_conversion",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        "compile_evidence_id": sa.Column(
            "compile_evidence_id",
            models.GUID(),
            sa.ForeignKey(
                "tradingview_compile_evidence.id",
                name="fk_tv_setup_c2_compile_evidence",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        "approved_candidate_sha256": sa.Column(
            "approved_candidate_sha256", sa.String(64), nullable=True
        ),
        "approved_source_sha256": sa.Column(
            "approved_source_sha256", sa.String(64), nullable=True
        ),
        "approved_strategy_layer_sha256": sa.Column(
            "approved_strategy_layer_sha256", sa.String(64), nullable=True
        ),
        "installed_by_user_id": sa.Column(
            "installed_by_user_id",
            models.GUID(),
            sa.ForeignKey(
                "users.id",
                name="fk_tv_setup_c2_installed_by",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        "current_credential_id": sa.Column(
            "current_credential_id",
            models.GUID(),
            sa.ForeignKey(
                "strategy_instance_webhook_credentials.id",
                name="fk_tv_setup_c2_current_credential",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        "hold_credential_id": sa.Column(
            "hold_credential_id",
            models.GUID(),
            sa.ForeignKey(
                "strategy_instance_webhook_credentials.id",
                name="fk_tv_setup_c2_hold_credential",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        "hold_webhook_event_id": sa.Column(
            "hold_webhook_event_id",
            models.GUID(),
            sa.ForeignKey(
                "webhook_events.id",
                name="fk_tv_setup_c2_hold_webhook_event",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        "paper_eligible_at": sa.Column(
            "paper_eligible_at", sa.DateTime(timezone=True), nullable=True
        ),
        "credential_revoked_at": sa.Column(
            "credential_revoked_at", sa.DateTime(timezone=True), nullable=True
        ),
        "suspended_at": sa.Column(
            "suspended_at", sa.DateTime(timezone=True), nullable=True
        ),
    }
    with op.batch_alter_table(
        "tradingview_setups", naming_convention=_NAMING_CONVENTION
    ) as batch:
        for name, column in additions.items():
            if name not in setup_columns:
                batch.add_column(column)
        if "pine_conversion_request_id" not in setup_columns:
            batch.create_unique_constraint(
                "uq_c2_installation_owner_candidate",
                ["user_id", "approved_version_id", "pine_conversion_request_id"],
            )
            batch.create_index(
                "ix_tradingview_setups_conversion",
                ["pine_conversion_request_id"],
            )


def downgrade() -> None:
    setup_columns = _columns("tradingview_setups")
    c2_setup_columns = (
        "suspended_at",
        "credential_revoked_at",
        "paper_eligible_at",
        "hold_webhook_event_id",
        "hold_credential_id",
        "current_credential_id",
        "installed_by_user_id",
        "approved_strategy_layer_sha256",
        "approved_source_sha256",
        "approved_candidate_sha256",
        "compile_evidence_id",
        "pine_conversion_request_id",
    )
    if "pine_conversion_request_id" in setup_columns:
        with op.batch_alter_table(
            "tradingview_setups", naming_convention=_NAMING_CONVENTION
        ) as batch:
            batch.drop_index("ix_tradingview_setups_conversion")
            batch.drop_constraint("uq_c2_installation_owner_candidate", type_="unique")
            for column in c2_setup_columns:
                if column in setup_columns:
                    batch.drop_column(column)

    instance_columns = _columns("strategy_instances")
    with op.batch_alter_table(
        "strategy_instances", naming_convention=_NAMING_CONVENTION
    ) as batch:
        if "installation_mode" in instance_columns:
            batch.drop_column("installation_mode")
        if "approved_candidate_sha256" in instance_columns:
            batch.drop_column("approved_candidate_sha256")

    if "tradingview_compile_evidence" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("tradingview_compile_evidence")
