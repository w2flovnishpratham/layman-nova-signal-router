"""R1B additive canonical evidence tables (evidence only, no writers wired).

Revision ID: 0015_r1b_persistence
Revises: 0014_verify_select

Creates the four dedicated R1B evidence tables:

    canonical_signal_decisions   - future immutable canonical interpretation
    canonical_signal_outcomes    - future append-only routing observations
    strategy_signal_rejections   - future safe ingress-rejection evidence
    pine_semantic_analyses       - immutable Pine semantic-analysis provenance
                                   (the only table with an R1B-1 writer)

EVIDENCE ONLY — NOT EXECUTION AUTHORITY. Nothing in the webhook, execution
router, risk manager, broker adapters or position store reads these tables.
No existing table gains a column; no existing row is touched; no backfill.

All enumerated columns are portable VARCHAR with named CHECK constraints
(no PostgreSQL ENUM). Hash CHECKs enforce length-64 lowercase portably on
both PostgreSQL and SQLite; the hexadecimal character set is additionally
guaranteed by the application writers, which only ever store hexdigest
output.

The revision id is kept <= 32 chars so it fits the deployed
``alembic_version.version_num VARCHAR(32)`` column. Table creation is
inspector-guarded because the 0001 baseline creates current models with
create_all() on fresh databases (recorded tech debt), so a fresh chain
reaches this revision with the tables already present.
"""
from alembic import op
import sqlalchemy as sa

from app.db import models


revision = "0015_r1b_persistence"
down_revision = "0014_verify_select"
branch_labels = None
depends_on = None


def _hash64(column: str, *, nullable: bool) -> str:
    check = f"(length({column}) = 64 AND {column} = lower({column}))"
    if nullable:
        return f"({column} IS NULL OR {check})"
    return check


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())

    if "canonical_signal_decisions" not in existing:
        op.create_table(
            "canonical_signal_decisions",
            sa.Column("id", models.GUID(), primary_key=True),
            sa.Column(
                "strategy_signal_id",
                models.GUID(),
                sa.ForeignKey("strategy_signals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "webhook_event_id",
                models.GUID(),
                sa.ForeignKey("webhook_events.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("user_id", models.GUID(), nullable=False),
            sa.Column("strategy_instance_id", models.GUID(), nullable=False),
            sa.Column("contract_version", sa.String(40), nullable=False),
            sa.Column("adapter_version", sa.String(50), nullable=False),
            sa.Column("wire_action", sa.String(20), nullable=False),
            sa.Column("event_type", sa.String(30), nullable=False),
            sa.Column("desired_state", sa.String(20), nullable=False),
            sa.Column("intent_reason", sa.String(40), nullable=False),
            sa.Column("signal_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("compatibility_action", sa.String(20), nullable=True),
            sa.Column("compatibility_side", sa.String(10), nullable=True),
            sa.Column("compatibility_option_side", sa.String(8), nullable=True),
            sa.Column("source", sa.String(50), nullable=False),
            sa.Column("safe_metadata", models.JSONType(), nullable=True),
            sa.Column("payload_fingerprint", sa.CHAR(64), nullable=False),
            sa.Column("provenance_kind", sa.String(20), nullable=False),
            sa.Column("backfill_version", sa.String(30), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["strategy_instance_id", "user_id"],
                ["strategy_instances.id", "strategy_instances.user_id"],
                name="fk_canonical_decision_instance_owner",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("strategy_signal_id", name="uq_canonical_decision_signal"),
            sa.CheckConstraint(
                "wire_action IN ('BUY_CE', 'BUY_PE', 'EXIT', 'HOLD')",
                name="ck_canonical_decision_wire_action",
            ),
            sa.CheckConstraint(
                "event_type IN ('STRATEGY_SIGNAL', 'CONNECTIVITY_TEST')",
                name="ck_canonical_decision_event_type",
            ),
            sa.CheckConstraint(
                "desired_state IN ('BULLISH', 'BEARISH', 'FLAT', 'NONE')",
                name="ck_canonical_decision_desired_state",
            ),
            sa.CheckConstraint(
                "intent_reason IN ('DIRECTIONAL_SIGNAL', 'EXPLICIT_EXIT', 'CONNECTIVITY_TEST')",
                name="ck_canonical_decision_intent_reason",
            ),
            sa.CheckConstraint(
                "compatibility_action IS NULL OR compatibility_action IN ('ENTRY', 'EXIT')",
                name="ck_canonical_decision_compat_action",
            ),
            sa.CheckConstraint(
                "compatibility_side IS NULL OR compatibility_side IN ('BUY', 'SELL')",
                name="ck_canonical_decision_compat_side",
            ),
            sa.CheckConstraint(
                "compatibility_option_side IS NULL OR compatibility_option_side IN ('CE', 'PE')",
                name="ck_canonical_decision_compat_option_side",
            ),
            sa.CheckConstraint(
                "provenance_kind IN ('LIVE', 'BACKFILL')",
                name="ck_canonical_decision_provenance_kind",
            ),
            sa.CheckConstraint(
                _hash64("payload_fingerprint", nullable=False),
                name="ck_canonical_decision_payload_fp_hash",
            ),
            sa.CheckConstraint(
                "wire_action != 'HOLD' OR ("
                "event_type = 'CONNECTIVITY_TEST'"
                " AND desired_state = 'NONE'"
                " AND intent_reason = 'CONNECTIVITY_TEST'"
                " AND compatibility_action IS NULL"
                " AND compatibility_side IS NULL"
                " AND compatibility_option_side IS NULL)",
                name="ck_canonical_decision_hold_consistency",
            ),
            sa.CheckConstraint(
                "wire_action != 'BUY_CE' OR ("
                "event_type = 'STRATEGY_SIGNAL'"
                " AND desired_state = 'BULLISH'"
                " AND compatibility_action = 'ENTRY'"
                " AND compatibility_side = 'BUY'"
                " AND compatibility_option_side = 'CE')",
                name="ck_canonical_decision_buy_ce_consistency",
            ),
            sa.CheckConstraint(
                "wire_action != 'BUY_PE' OR ("
                "event_type = 'STRATEGY_SIGNAL'"
                " AND desired_state = 'BEARISH'"
                " AND compatibility_action = 'ENTRY'"
                " AND compatibility_side = 'BUY'"
                " AND compatibility_option_side = 'PE')",
                name="ck_canonical_decision_buy_pe_consistency",
            ),
            sa.CheckConstraint(
                "wire_action != 'EXIT' OR ("
                "event_type = 'STRATEGY_SIGNAL'"
                " AND desired_state = 'FLAT'"
                " AND compatibility_action = 'EXIT'"
                " AND compatibility_side = 'SELL'"
                " AND compatibility_option_side IS NULL)",
                name="ck_canonical_decision_exit_consistency",
            ),
        )
        op.create_index(
            "ix_canonical_decisions_user_received",
            "canonical_signal_decisions",
            ["user_id", "received_at"],
        )
        op.create_index(
            "ix_canonical_decisions_instance_received",
            "canonical_signal_decisions",
            ["strategy_instance_id", "received_at"],
        )
        op.create_index(
            "ix_canonical_decisions_webhook_event",
            "canonical_signal_decisions",
            ["webhook_event_id"],
        )

    if "canonical_signal_outcomes" not in existing:
        op.create_table(
            "canonical_signal_outcomes",
            sa.Column("id", models.GUID(), primary_key=True),
            sa.Column(
                "canonical_decision_id",
                models.GUID(),
                sa.ForeignKey("canonical_signal_decisions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "execution_job_id",
                models.GUID(),
                sa.ForeignKey("strategy_execution_jobs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("attempt", sa.Integer(), nullable=True),
            sa.Column("phase", sa.String(30), nullable=False),
            sa.Column("current_state", sa.String(20), nullable=True),
            sa.Column("routing_result", sa.String(30), nullable=False),
            sa.Column("no_op_reason", sa.String(40), nullable=True),
            sa.Column("exit_reason", sa.String(40), nullable=True),
            sa.Column("safe_detail_code", sa.String(60), nullable=True),
            sa.Column("result_sha256", sa.CHAR(64), nullable=True),
            sa.Column("idempotency_key", sa.CHAR(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_canonical_outcome_idempotency"),
            sa.CheckConstraint(
                "phase IN ('STATE_EVALUATED', 'ROUTING_COMPLETED', 'ROUTING_FAILED')",
                name="ck_canonical_outcome_phase",
            ),
            sa.CheckConstraint(
                "current_state IS NULL OR current_state IN ('UNKNOWN', 'FLAT', 'BULLISH', 'BEARISH')",
                name="ck_canonical_outcome_current_state",
            ),
            sa.CheckConstraint(
                "routing_result IN ("
                "'NOT_EVALUATED', 'CONNECTIVITY_NO_JOB', 'STATE_NO_OP', 'ENTRY_ROUTED',"
                " 'EXIT_ROUTED', 'REVERSAL_ROUTED', 'BLOCKED', 'FAILED', 'PENDING', 'PARTIAL')",
                name="ck_canonical_outcome_routing_result",
            ),
            sa.CheckConstraint(
                "no_op_reason IS NULL OR no_op_reason IN ("
                "'CONNECTIVITY_TEST', 'ALREADY_FLAT', 'ALREADY_BULLISH',"
                " 'ALREADY_BEARISH', 'INSTANCE_PAUSED')",
                name="ck_canonical_outcome_no_op_reason",
            ),
            sa.CheckConstraint(
                "exit_reason IS NULL OR exit_reason IN ("
                "'EXPLICIT_EXIT', 'REVERSAL_EXIT', 'STOP_LOSS', 'TAKE_PROFIT',"
                " 'TRAILING_STOP', 'SESSION_EXIT', 'MANUAL_EXIT', 'RISK_EXIT')",
                name="ck_canonical_outcome_exit_reason",
            ),
            sa.CheckConstraint("attempt IS NULL OR attempt > 0", name="ck_canonical_outcome_attempt_positive"),
            sa.CheckConstraint(
                _hash64("result_sha256", nullable=True),
                name="ck_canonical_outcome_result_hash",
            ),
            sa.CheckConstraint(
                _hash64("idempotency_key", nullable=False),
                name="ck_canonical_outcome_idempotency_hash",
            ),
        )
        op.create_index(
            "ix_canonical_outcomes_decision_created",
            "canonical_signal_outcomes",
            ["canonical_decision_id", "created_at"],
        )
        op.create_index(
            "ix_canonical_outcomes_execution_job",
            "canonical_signal_outcomes",
            ["execution_job_id"],
        )

    if "strategy_signal_rejections" not in existing:
        op.create_table(
            "strategy_signal_rejections",
            sa.Column("id", models.GUID(), primary_key=True),
            sa.Column("user_id", models.GUID(), nullable=False),
            sa.Column("strategy_instance_id", models.GUID(), nullable=False),
            sa.Column(
                "webhook_event_id",
                models.GUID(),
                sa.ForeignKey("webhook_events.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("signal_id_safe", sa.String(128), nullable=True),
            sa.Column("signal_id_sha256", sa.CHAR(64), nullable=True),
            sa.Column("payload_fingerprint", sa.CHAR(64), nullable=True),
            sa.Column("stage", sa.String(40), nullable=False),
            sa.Column("rejection_code", sa.String(60), nullable=False),
            sa.Column("safe_detail", sa.String(160), nullable=True),
            sa.Column("adapter_version", sa.String(50), nullable=True),
            sa.Column("contract_version", sa.String(40), nullable=True),
            sa.Column("dedupe_key", sa.CHAR(64), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["strategy_instance_id", "user_id"],
                ["strategy_instances.id", "strategy_instances.user_id"],
                name="fk_signal_rejection_instance_owner",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("dedupe_key", name="uq_signal_rejection_dedupe"),
            sa.CheckConstraint(
                "stage IN ('LIFECYCLE', 'SIGNAL_TIME', 'REPLAY_CONFLICT',"
                " 'CANONICAL_NORMALIZATION', 'SEMANTIC_POLICY', 'JOB_CREATION')",
                name="ck_signal_rejection_stage",
            ),
            sa.CheckConstraint(
                "rejection_code IN ("
                "'INVALID_ACTION', 'MISSING_TIMEZONE', 'STALE_SIGNAL', 'INACTIVE_INSTANCE',"
                " 'LIVE_EXECUTION_SAFETY_BLOCK', 'CONFLICTING_DUPLICATE', 'STORE_UNAVAILABLE',"
                " 'JOB_PERSISTENCE_FAILED')",
                name="ck_signal_rejection_code",
            ),
            sa.CheckConstraint(
                _hash64("signal_id_sha256", nullable=True),
                name="ck_signal_rejection_signal_id_hash",
            ),
            sa.CheckConstraint(
                _hash64("payload_fingerprint", nullable=True),
                name="ck_signal_rejection_payload_fp_hash",
            ),
            sa.CheckConstraint(
                _hash64("dedupe_key", nullable=False),
                name="ck_signal_rejection_dedupe_hash",
            ),
        )
        op.create_index(
            "ix_signal_rejections_instance_received",
            "strategy_signal_rejections",
            ["strategy_instance_id", "received_at"],
        )
        op.create_index(
            "ix_signal_rejections_user_received",
            "strategy_signal_rejections",
            ["user_id", "received_at"],
        )
        op.create_index(
            "ix_signal_rejections_stage_code_received",
            "strategy_signal_rejections",
            ["stage", "rejection_code", "received_at"],
        )
        op.create_index(
            "ix_signal_rejections_webhook_event",
            "strategy_signal_rejections",
            ["webhook_event_id"],
        )

    if "pine_semantic_analyses" not in existing:
        op.create_table(
            "pine_semantic_analyses",
            sa.Column("id", models.GUID(), primary_key=True),
            sa.Column(
                "source_artifact_id",
                models.GUID(),
                sa.ForeignKey("strategy_source_artifacts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_sha256", sa.CHAR(64), nullable=False),
            sa.Column("analyzer_version", sa.String(50), nullable=False),
            sa.Column("registry_id", sa.String(60), nullable=False),
            sa.Column("registry_version", sa.String(30), nullable=False),
            sa.Column("registry_sha256", sa.CHAR(64), nullable=False),
            # VARCHAR(50), not the drafted 30: the closed schema version
            # "nova.pine-semantic-analysis-persistence.v1" is 42 characters.
            sa.Column("analysis_schema_version", sa.String(50), nullable=False),
            sa.Column("effective_capability_level", sa.String(60), nullable=False),
            sa.Column("confidence", sa.String(40), nullable=False),
            sa.Column("analysis_payload", models.JSONType(), nullable=False),
            sa.Column("analysis_payload_sha256", sa.CHAR(64), nullable=False),
            sa.Column(
                "supersedes_analysis_id",
                models.GUID(),
                sa.ForeignKey("pine_semantic_analyses.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "source_artifact_id",
                "source_sha256",
                "analyzer_version",
                "registry_id",
                "registry_version",
                "registry_sha256",
                "analysis_schema_version",
                name="uq_pine_analysis_provenance",
            ),
            sa.CheckConstraint(
                _hash64("source_sha256", nullable=False),
                name="ck_pine_analysis_source_hash",
            ),
            sa.CheckConstraint(
                _hash64("registry_sha256", nullable=False),
                name="ck_pine_analysis_registry_hash",
            ),
            sa.CheckConstraint(
                _hash64("analysis_payload_sha256", nullable=False),
                name="ck_pine_analysis_payload_hash",
            ),
            sa.CheckConstraint(
                "confidence IN ('HIGH_CONFIDENCE_MATCH', 'PARTIAL_MATCH', 'ANALYSIS_INDETERMINATE')",
                name="ck_pine_analysis_confidence",
            ),
            sa.CheckConstraint(
                "effective_capability_level IN ("
                "'L0_DIRECTLY_SUPPORTED', 'L1_NORMALIZED_WITHOUT_MATERIAL_CHANGE',"
                " 'L2_SUPPORTED_WITH_DISCLOSED_CHANGE', 'L3_REQUIRES_BACKEND_CAPABILITY',"
                " 'L4_BLOCKED_UNSAFE_OR_UNREPRESENTABLE')",
                name="ck_pine_analysis_level",
            ),
        )
        op.create_index(
            "ix_pine_analyses_artifact_created",
            "pine_semantic_analyses",
            ["source_artifact_id", "created_at"],
        )
        op.create_index(
            "ix_pine_analyses_level",
            "pine_semantic_analyses",
            ["effective_capability_level"],
        )
        op.create_index(
            "ix_pine_analyses_supersedes",
            "pine_semantic_analyses",
            ["supersedes_analysis_id"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    # Exact reverse dependency order.
    for table in (
        "pine_semantic_analyses",
        "strategy_signal_rejections",
        "canonical_signal_outcomes",
        "canonical_signal_decisions",
    ):
        if table in existing:
            op.drop_table(table)
