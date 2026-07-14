"""Safe hosted Strategy IR and paper runtime.

Revision ID: 0013_hosted_strategy_runtime
Revises: 0012_pine_conversion_requests
"""
from alembic import op
import sqlalchemy as sa

from app.db import models

revision = "0013_hosted_strategy_runtime"
down_revision = "0012_pine_conversion_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "hosted_strategy_ir_versions" in sa.inspect(op.get_bind()).get_table_names():
        return
    guid, json = models.GUID(), models.JSONType()
    op.create_table(
        "hosted_strategy_ir_versions",
        sa.Column("id", guid, primary_key=True),
        sa.Column("strategy_id", models.GUID(), sa.ForeignKey("strategy_catalog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_strategy_version_id", models.GUID(), sa.ForeignKey("strategy_versions.id", ondelete="SET NULL")),
        sa.Column("owner_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("ir_version", sa.Integer(), nullable=False),
        sa.Column("canonical_sha256", sa.String(64), nullable=False),
        sa.Column("ir_document", json, nullable=False),
        sa.Column("creation_source", sa.String(40), nullable=False),
        sa.Column("validator_version", sa.String(20), nullable=False),
        sa.Column("validation_report", models.JSONType(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_by_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ir_version = 1", name="ck_hosted_ir_version_v1"),
        sa.UniqueConstraint("strategy_id", "canonical_sha256", name="uq_hosted_ir_strategy_hash"),
    )
    op.create_index("ix_hosted_ir_owner_created", "hosted_strategy_ir_versions", ["owner_user_id", "created_at"])
    op.create_table(
        "hosted_strategy_runtimes",
        sa.Column("id", models.GUID(), primary_key=True),
        sa.Column("strategy_instance_id", models.GUID(), sa.ForeignKey("strategy_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ir_version_id", models.GUID(), sa.ForeignKey("hosted_strategy_ir_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_mode", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_evaluated_candle_at", sa.DateTime(timezone=True)),
        sa.Column("last_emitted_action", sa.String(20)),
        sa.Column("last_signal_id", sa.String(255)),
        sa.Column("warmup_status", sa.String(30), nullable=False),
        sa.Column("consecutive_error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safe_error_code", sa.String(50)),
        sa.Column("paused_reason", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("execution_mode = 'paper_live_data'", name="ck_hosted_runtime_paper_only"),
        sa.CheckConstraint("status IN ('READY','ACTIVE','PAUSED','ERROR','STOPPED')", name="ck_hosted_runtime_status"),
        sa.UniqueConstraint("strategy_instance_id", name="uq_hosted_runtime_instance"),
    )
    op.create_index("ix_hosted_runtime_owner_status", "hosted_strategy_runtimes", ["owner_user_id", "status"])
    op.create_table(
        "hosted_strategy_evaluation_jobs",
        sa.Column("id", models.GUID(), primary_key=True),
        sa.Column("hosted_runtime_id", models.GUID(), sa.ForeignKey("hosted_strategy_runtimes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ir_version_id", models.GUID(), sa.ForeignKey("hosted_strategy_ir_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candle_close_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_history", models.JSONType(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("safe_error_code", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("hosted_runtime_id", "ir_version_id", "candle_close_timestamp", name="uq_hosted_evaluation_identity"),
    )
    op.create_index("ix_hosted_evaluation_jobs_claim", "hosted_strategy_evaluation_jobs", ["status", "available_at"])
    op.create_table(
        "hosted_strategy_evaluations",
        sa.Column("id", models.GUID(), primary_key=True),
        sa.Column("hosted_runtime_id", models.GUID(), sa.ForeignKey("hosted_strategy_runtimes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ir_version_id", models.GUID(), sa.ForeignKey("hosted_strategy_ir_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_user_id", models.GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candle_close_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("position_state", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("action", sa.String(20)),
        sa.Column("signal_id", sa.String(255)),
        sa.Column("indicator_fingerprint", sa.String(64)),
        sa.Column("result_fingerprint", sa.String(64)),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safe_error_code", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("hosted_runtime_id", "ir_version_id", "candle_close_timestamp", name="uq_hosted_evaluation_audit_identity"),
    )
    op.create_index("ix_hosted_evaluations_runtime_candle", "hosted_strategy_evaluations", ["hosted_runtime_id", "candle_close_timestamp"])


def downgrade() -> None:
    op.drop_index("ix_hosted_evaluations_runtime_candle", table_name="hosted_strategy_evaluations")
    op.drop_table("hosted_strategy_evaluations")
    op.drop_index("ix_hosted_evaluation_jobs_claim", table_name="hosted_strategy_evaluation_jobs")
    op.drop_table("hosted_strategy_evaluation_jobs")
    op.drop_index("ix_hosted_runtime_owner_status", table_name="hosted_strategy_runtimes")
    op.drop_table("hosted_strategy_runtimes")
    op.drop_index("ix_hosted_ir_owner_created", table_name="hosted_strategy_ir_versions")
    op.drop_table("hosted_strategy_ir_versions")
