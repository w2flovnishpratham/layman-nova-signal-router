"""Add user_preferences.

Revision ID: 0018_user_preferences
Revises: 0017_expand_admin_review_status

Why a new table rather than an existing home
--------------------------------------------
* ``users`` holds identity only (google_sub, email, name, admin flag). Widening
  it with presentation settings mixes an auth record with UI state and makes
  every auth read carry columns it never uses.
* The owner-scoped JSON runtime settings file *would* hold these values, but it
  is the engine's settings file: ``risk_manager`` reads it on every entry
  decision, and it carries the optimistic-locking ``_version`` the risk API and
  the configuration revision depend on. A "table density" write must not bump a
  version that gates trading configuration.
* Runtime state is a file on the app server's disk. Execution-node routing is
  enabled, so a per-node file diverges between nodes while the user is one row
  in PostgreSQL. Preferences must follow the user, not the node.

Notification preferences are a JSON column on this same row rather than a second
table: they are a small, per-user set with no independent lifecycle, and NOVA
has no notification delivery channel to model relationships against yet.

Backward compatible: a new table only, nothing existing is altered. Absent rows
are treated as defaults by the service, so no backfill is required.
Reversible: downgrade drops the table.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_user_preferences"
down_revision = "0017_expand_admin_review_status"
branch_labels = None
depends_on = None

_TABLE = "user_preferences"


def _json_type():
    """JSONB on PostgreSQL, JSON elsewhere (the test database is SQLite)."""
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # Inspector-guarded, matching migration 0015: the 0001 baseline runs
    # Base.metadata.create_all, so a from-scratch upgrade already has this table
    # by the time 0018 runs, while a stamped production database does not.
    inspector = sa.inspect(op.get_bind())
    if _TABLE in set(inspector.get_table_names()):
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("reduced_motion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("table_density", sa.String(16), nullable=False, server_default="comfortable"),
        sa.Column("default_chart_timeframe", sa.String(16), nullable=False, server_default="5m"),
        sa.Column("notification_preferences", _json_type(), nullable=False, server_default="{}"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # One preference row per owner: the uniqueness *is* the owner scoping.
        sa.UniqueConstraint("user_id", name="uq_user_preferences_user"),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE in set(inspector.get_table_names()):
        op.drop_table(_TABLE)
