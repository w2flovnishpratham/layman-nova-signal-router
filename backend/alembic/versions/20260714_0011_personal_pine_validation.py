"""Personal Pine immutable validation and review metadata.

Revision ID: 0011_personal_pine_validation
Revises: 0010_position_ownership
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0011_personal_pine_validation"
down_revision = "0010_position_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    batch_options = {"recreate": "always"} if sqlite else {}
    position_after = (lambda column: {"insert_after": column}) if sqlite else (lambda _column: {})
    inspector = sa.inspect(op.get_bind())

    def columns(table: str) -> set[str]:
        return {item["name"] for item in inspector.get_columns(table)}

    def uniques(table: str) -> set[str]:
        return {item["name"] for item in inspector.get_unique_constraints(table) if item.get("name")}

    version_columns = columns("strategy_versions")
    if {"source_sha256", "pine_contract_version"} - version_columns or "uq_strategy_version_source_contract" not in uniques("strategy_versions"):
        with op.batch_alter_table("strategy_versions", **batch_options) as batch:
            if "source_sha256" not in version_columns:
                batch.add_column(sa.Column("source_sha256", sa.String(length=64), nullable=True), **position_after("changelog"))
            if "pine_contract_version" not in version_columns:
                batch.add_column(sa.Column("pine_contract_version", sa.Integer(), nullable=True), **position_after("source_sha256"))
            if "uq_strategy_version_source_contract" not in uniques("strategy_versions"):
                batch.create_unique_constraint("uq_strategy_version_source_contract", ["strategy_id", "source_sha256", "pine_contract_version"])

    artifact_columns = columns("strategy_source_artifacts")
    if "original_filename" not in artifact_columns or "uq_strategy_source_artifact_version_type" not in uniques("strategy_source_artifacts"):
        with op.batch_alter_table("strategy_source_artifacts", **batch_options) as batch:
            if "original_filename" not in artifact_columns:
                batch.add_column(sa.Column("original_filename", sa.String(length=120), nullable=True), **position_after("conversion_method"))
            if "uq_strategy_source_artifact_version_type" not in uniques("strategy_source_artifacts"):
                batch.create_unique_constraint("uq_strategy_source_artifact_version_type", ["strategy_version_id", "artifact_type"])

    report_columns = columns("strategy_validation_reports")
    report_specs = [
        ("validator_version", sa.Column("validator_version", sa.String(length=20), nullable=True), "findings"),
        ("contract_version", sa.Column("contract_version", sa.Integer(), nullable=True), "validator_version"),
        ("source_sha256", sa.Column("source_sha256", sa.String(length=64), nullable=True), "contract_version"),
        ("validation_engine", sa.Column("validation_engine", sa.String(length=50), nullable=True), "source_sha256"),
        ("started_at", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True), "validation_engine"),
        ("error_count", sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"), "started_at"),
        ("warning_count", sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"), "error_count"),
        ("info_count", sa.Column("info_count", sa.Integer(), nullable=False, server_default="0"), "warning_count"),
        ("eligible_for_review", sa.Column("eligible_for_review", sa.Boolean(), nullable=False, server_default=sa.false()), "info_count"),
    ]
    if {name for name, _, _ in report_specs} - report_columns or "uq_strategy_validation_identity" not in uniques("strategy_validation_reports"):
        with op.batch_alter_table("strategy_validation_reports", **batch_options) as batch:
            for name, column, after in report_specs:
                if name not in report_columns:
                    batch.add_column(column, **position_after(after))
            if "uq_strategy_validation_identity" not in uniques("strategy_validation_reports"):
                batch.create_unique_constraint("uq_strategy_validation_identity", ["strategy_version_id", "stage", "validator_version", "contract_version", "source_sha256"])

    review_columns = columns("strategy_admin_reviews")
    if {"validation_report_id", "previous_status", "new_status", "source_sha256"} - review_columns:
        with op.batch_alter_table("strategy_admin_reviews", **batch_options) as batch:
            if "validation_report_id" not in review_columns:
                batch.add_column(sa.Column(
                "validation_report_id",
                models.GUID(),
                sa.ForeignKey(
                    "strategy_validation_reports.id",
                    name="fk_strategy_admin_reviews_validation_report",
                    ondelete="SET NULL",
                ),
                nullable=True,
                ), **position_after("notes"))
            if "previous_status" not in review_columns:
                batch.add_column(sa.Column("previous_status", sa.String(length=20), nullable=True), **position_after("validation_report_id"))
            if "new_status" not in review_columns:
                batch.add_column(sa.Column("new_status", sa.String(length=20), nullable=True), **position_after("previous_status"))
            if "source_sha256" not in review_columns:
                batch.add_column(sa.Column("source_sha256", sa.String(length=64), nullable=True), **position_after("new_status"))


def downgrade() -> None:
    batch_options = {"recreate": "always"} if op.get_bind().dialect.name == "sqlite" else {}
    with op.batch_alter_table("strategy_admin_reviews", **batch_options) as batch:
        batch.drop_column("source_sha256")
        batch.drop_column("new_status")
        batch.drop_column("previous_status")
        batch.drop_column("validation_report_id")

    with op.batch_alter_table("strategy_validation_reports", **batch_options) as batch:
        batch.drop_constraint("uq_strategy_validation_identity", type_="unique")
        batch.drop_column("eligible_for_review")
        batch.drop_column("info_count")
        batch.drop_column("warning_count")
        batch.drop_column("error_count")
        batch.drop_column("started_at")
        batch.drop_column("validation_engine")
        batch.drop_column("source_sha256")
        batch.drop_column("contract_version")
        batch.drop_column("validator_version")

    with op.batch_alter_table("strategy_source_artifacts", **batch_options) as batch:
        batch.drop_constraint("uq_strategy_source_artifact_version_type", type_="unique")
        batch.drop_column("original_filename")

    with op.batch_alter_table("strategy_versions", **batch_options) as batch:
        batch.drop_constraint("uq_strategy_version_source_contract", type_="unique")
        batch.drop_column("pine_contract_version")
        batch.drop_column("source_sha256")
