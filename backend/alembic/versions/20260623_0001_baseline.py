"""Baseline current multi-user schema.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-23 00:00:00 UTC

This adopts Alembic without changing the production schema. Existing databases
created by SQLAlchemy `create_all()` should be marked with:

    alembic stamp 0001_baseline

New databases can run `alembic upgrade head`.
"""
from __future__ import annotations

from alembic import op

from app.db.models import Base


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
