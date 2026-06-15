"""Neon/Postgres DATABASE_URL normalization."""
from __future__ import annotations

from app.db.engine import normalize_database_url


def test_postgresql_scheme_gets_psycopg_driver():
    url = "postgresql://user:pass@host/db?sslmode=require&channel_binding=require"
    out = normalize_database_url(url)
    assert out.startswith("postgresql+psycopg://")
    # Query params (sslmode, channel_binding) must be preserved exactly.
    assert "sslmode=require" in out
    assert "channel_binding=require" in out


def test_postgres_short_scheme_normalized():
    assert normalize_database_url("postgres://u:p@h/db").startswith("postgresql+psycopg://")


def test_psycopg2_is_upgraded_to_psycopg3():
    assert normalize_database_url("postgresql+psycopg2://u:p@h/db").startswith("postgresql+psycopg://")


def test_already_psycopg_unchanged():
    url = "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert normalize_database_url(url) == url


def test_non_postgres_passthrough():
    assert normalize_database_url("sqlite:///./x.db") == "sqlite:///./x.db"


def test_empty_passthrough():
    assert normalize_database_url("") == ""
    assert normalize_database_url("   ") == ""
