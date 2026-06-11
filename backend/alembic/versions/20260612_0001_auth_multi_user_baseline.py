"""auth multi user baseline

Revision ID: 20260612_0001
Revises:
Create Date: 2026-06-12
"""

from __future__ import annotations

from alembic import op


revision = "20260612_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _upgrade_postgresql()
        return

    # Local/test SQLite remains supported through SQLModel create_all. This
    # migration is intentionally production-focused for the deployed Postgres DB.


def downgrade() -> None:
    # Baseline migration: do not drop production auth/trading data on downgrade.
    pass


def _upgrade_postgresql() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR PRIMARY KEY,
            email VARCHAR NOT NULL UNIQUE,
            google_sub VARCHAR NOT NULL UNIQUE,
            name VARCHAR,
            avatar_url VARCHAR,
            created_at TIMESTAMP,
            last_login TIMESTAMP,
            plan_tier VARCHAR NOT NULL DEFAULT 'paper',
            status VARCHAR NOT NULL DEFAULT 'active'
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL REFERENCES users(id),
            created_at TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            revoked_at TIMESTAMP,
            last_used_at TIMESTAMP,
            client_ip VARCHAR,
            user_agent VARCHAR
        )
        """
    )
    op.execute("ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP")
    op.execute("ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP")
    op.execute("ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS client_ip VARCHAR")
    op.execute("ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS user_agent VARCHAR")
    op.execute("CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_runtime_profiles (
            user_id VARCHAR PRIMARY KEY REFERENCES users(id),
            webhook_secret_hash VARCHAR UNIQUE,
            webhook_secret_set_at TIMESTAMP,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_runtime_profiles_webhook_secret_hash "
        "ON user_runtime_profiles (webhook_secret_hash)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dhan_accounts (
            id VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL REFERENCES users(id),
            dhan_client_id_hash VARCHAR NOT NULL UNIQUE,
            dhan_client_id_masked VARCHAR,
            status VARCHAR NOT NULL DEFAULT 'connected',
            access_token_present BOOLEAN NOT NULL DEFAULT false,
            connected_at TIMESTAMP,
            last_validated_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_dhan_accounts_user_id ON dhan_accounts (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dhan_accounts_status ON dhan_accounts (status)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_dhan_accounts_dhan_client_id_hash "
        "ON dhan_accounts (dhan_client_id_hash)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS egress_nodes (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL UNIQUE,
            provider VARCHAR NOT NULL DEFAULT 'vultr',
            region VARCHAR NOT NULL,
            public_ip VARCHAR NOT NULL UNIQUE,
            internal_base_url VARCHAR,
            status VARCHAR NOT NULL DEFAULT 'ready',
            capacity INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_egress_nodes_name ON egress_nodes (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_egress_nodes_provider ON egress_nodes (provider)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_egress_nodes_region ON egress_nodes (region)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_egress_nodes_public_ip ON egress_nodes (public_ip)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_egress_nodes_status ON egress_nodes (status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_egress_assignments (
            id VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL REFERENCES users(id),
            egress_node_id VARCHAR NOT NULL REFERENCES egress_nodes(id),
            is_active BOOLEAN NOT NULL DEFAULT true,
            assigned_at TIMESTAMP,
            released_at TIMESTAMP,
            release_reason VARCHAR
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_egress_assignments_user_id ON user_egress_assignments (user_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_egress_assignments_egress_node_id "
        "ON user_egress_assignments (egress_node_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_egress_assignments_is_active ON user_egress_assignments (is_active)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_egress_assignment_active_user "
        "ON user_egress_assignments (user_id) WHERE is_active = true"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_egress_assignment_active_node "
        "ON user_egress_assignments (egress_node_id) WHERE is_active = true"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS order_route_audit (
            id VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL REFERENCES users(id),
            dhan_account_id VARCHAR REFERENCES dhan_accounts(id),
            egress_node_id VARCHAR REFERENCES egress_nodes(id),
            route_kind VARCHAR NOT NULL DEFAULT 'order',
            status VARCHAR NOT NULL,
            signal_id VARCHAR,
            request_id VARCHAR,
            order_id VARCHAR,
            source_ip VARCHAR,
            message VARCHAR,
            metadata_json JSON,
            created_at TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_user_id ON order_route_audit (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_dhan_account_id ON order_route_audit (dhan_account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_egress_node_id ON order_route_audit (egress_node_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_route_kind ON order_route_audit (route_kind)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_status ON order_route_audit (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_signal_id ON order_route_audit (signal_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_request_id ON order_route_audit (request_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_order_id ON order_route_audit (order_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_order_route_audit_created_at ON order_route_audit (created_at)")
