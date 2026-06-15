from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def legacy_routes_use_dev_auth(monkeypatch):
    """Keep tests isolated from credentials and security flags in local .env."""
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_REQUIRED", False, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_HMAC_REQUIRED", False, raising=False)
