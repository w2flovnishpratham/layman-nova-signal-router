from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth.db import init_auth_db, session_scope
from app.auth.models import AuthSession, User, utc_now_dt
from app.config import settings
from app.services import audit_logger, credential_vault, paper_portfolio, state_store
from app.store.session_token import issue_auth_token


def isolate_runtime(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(state_store, "APP_STATE_FILE", state_dir / "app_state.json")
    monkeypatch.setattr(state_store, "OPEN_POSITION_FILE", state_dir / "open_position.json")
    monkeypatch.setattr(state_store, "PAPER_POSITION_FILE", state_dir / "paper_position.json")
    monkeypatch.setattr(state_store, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(state_store, "EXTERNAL_POSITIONS_FILE", state_dir / "external_positions.json")
    monkeypatch.setattr(state_store, "SEEN_SIGNALS_FILE", state_dir / "seen_signals.json")
    monkeypatch.setattr(state_store, "SETTINGS_FILE", state_dir / "settings.json")
    monkeypatch.setattr(paper_portfolio, "PAPER_PORTFOLIO_FILE", state_dir / "paper_portfolio.json")
    monkeypatch.setattr(credential_vault, "CREDENTIALS_FILE", state_dir / "credentials.enc.json")
    log_files = {
        "webhook": log_dir / "webhook_events.jsonl",
        "order": log_dir / "order_events.jsonl",
        "audit": log_dir / "audit_events.jsonl",
        "error": log_dir / "errors.jsonl",
        "paper_orders": log_dir / "paper_orders.jsonl",
    }
    monkeypatch.setattr(state_store, "LOG_FILES", log_files)
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "DHAN_MODE", "MOCK")
    monkeypatch.setattr(settings, "DHAN_READ_ONLY_REAL_DATA", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_ORDERS", False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", False)
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_LEGACY_AUTH_LOCAL", True)
    monkeypatch.setattr(settings, "WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", 60)
    monkeypatch.setattr(settings, "REQUIRE_SIGNAL_ID_LIVE", False)
    monkeypatch.setattr(settings, "REQUIRE_INSTRUMENT_MASTER_VALIDATION_LIVE", False)
    monkeypatch.setattr(settings, "REQUIRE_FRESH_EXPIRY_LIVE", False)
    monkeypatch.setattr(settings, "WORKER_ROLE", "web")
    monkeypatch.setattr(settings, "ENABLE_TRADING_WORKERS", False)
    monkeypatch.setattr(settings, "TRADING_WORKER_DISTRIBUTED_LOCK_ENABLED", False)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "SESSION_TOKEN_SECRET", "s" * 32)
    monkeypatch.setattr(settings, "REQUIRE_MARKET_HOURS", False)
    monkeypatch.setattr("app.main.start_instrument_cache_warmup", lambda: None)
    monkeypatch.setattr("app.routers.setup.get_outgoing_ip", lambda **_kwargs: {"ok": False, "outgoing_ip": None})
    credential_vault._LOCAL_MEMORY_PAYLOADS.clear()


def create_authenticated_user(label: str) -> tuple[User, str]:
    init_auth_db()
    suffix = secrets.token_hex(8)
    user = User(
        id=f"u_{label}_{suffix}",
        email=f"{label}-{suffix}@example.test",
        google_sub=f"google-{label}-{suffix}",
    )
    auth_session = AuthSession(
        id=f"asid_{label}_{suffix}",
        user_id=user.id,
        created_at=utc_now_dt(),
        expires_at=utc_now_dt() + timedelta(hours=1),
    )
    with session_scope() as session:
        session.add(user)
        session.add(auth_session)
        session.commit()
        session.refresh(user)
        session.refresh(auth_session)
        session.expunge(user)
        session.expunge(auth_session)
    return user, issue_auth_token(user.id, auth_session_id=auth_session.id)


def authenticate_client(client: TestClient, auth_cookie: str, csrf_token: str) -> dict[str, str]:
    client.cookies.set(settings.AUTH_COOKIE_NAME, auth_cookie)
    client.cookies.set(settings.CSRF_COOKIE_NAME, csrf_token)
    return {settings.CSRF_HEADER_NAME: csrf_token}
