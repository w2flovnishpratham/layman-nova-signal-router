"""Durable opt-in Pine conversion requests.

Revision ID: 0012_pine_conversion_requests
Revises: 0011_personal_pine_validation
"""
from alembic import op
import sqlalchemy as sa

from app.db import models

revision = "0012_pine_conversion_requests"
down_revision = "0011_personal_pine_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001_baseline uses current metadata.create_all() on a fresh database.
    if "pine_conversion_requests" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "pine_conversion_requests",
        sa.Column("id", models.GUID(), primary_key=True),
        sa.Column("owner_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", models.GUID(), sa.ForeignKey("strategy_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("input_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("input_source_sha256", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("options", models.JSONType(), nullable=False),
        sa.Column("options_sha256", sa.String(64), nullable=False),
        sa.Column("identity_sha256", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("provider_request_id", sa.String(120)),
        sa.Column("candidate_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id", ondelete="SET NULL")),
        sa.Column("validation_report_id", models.GUID(), sa.ForeignKey("strategy_validation_reports.id", ondelete="SET NULL")),
        sa.Column("safe_error_code", sa.String(50)),
        sa.Column("usage_summary", models.JSONType()),
        sa.Column("estimated_cost_micros", sa.BigInteger()),
        sa.Column("conversion_summary", sa.Text()),
        sa.Column("assumptions", models.JSONType()),
        sa.Column("unsupported_features", models.JSONType()),
        sa.Column("warnings", models.JSONType()),
        sa.Column("action_mapping", models.JSONType()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("worker_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("identity_sha256", "attempt", name="uq_pine_conversion_identity_attempt"),
    )
    op.create_index("ix_pine_conversion_status_available", "pine_conversion_requests", ["status", "available_at"])
    op.create_index("ix_pine_conversion_owner_created", "pine_conversion_requests", ["owner_user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pine_conversion_owner_created", table_name="pine_conversion_requests")
    op.drop_index("ix_pine_conversion_status_available", table_name="pine_conversion_requests")
    op.drop_table("pine_conversion_requests")
