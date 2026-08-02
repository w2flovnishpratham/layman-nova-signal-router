"""update_strategy_metadata: rename/redescribe a strategy -- personal or
nova_shared, admin or owner -- without touching catalog_code (the live
webhook path), which stays a separate, deliberate publish_as_shared() action."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.engine import session_scope
from app.services import personal_pine_service
from app.tests.conftest_multiuser import make_user, mu_db  # noqa: F401

pytest_plugins = ("app.tests.conftest_multiuser",)


def _make_strategy(user, *, owner_type: str = "personal", visibility: str = "private", code: str | None = None) -> uuid.UUID:
    with session_scope() as db:
        strategy = models.StrategyCatalog(
            code=code or f"pine-{uuid.uuid4().hex[:10]}",
            display_name="Original Name",
            description="Original description.",
            owner_type=owner_type,
            owner_user_id=user.id if owner_type == "personal" else None,
            visibility=visibility,
            status="active",
        )
        db.add(strategy)
        db.flush()
        return strategy.id


def _owner_client(user_model):
    from app.auth.dependencies import get_current_user
    from app.routers import personal_pine
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(personal_pine.router)
    app.dependency_overrides[get_current_user] = lambda: current_user_from_model(user_model)
    return TestClient(app)


def _admin_router_client(user_model):
    from app.auth.dependencies import require_admin
    from app.routers import admin as admin_router_module
    from app.services.user_context import current_user_from_model

    app = FastAPI()
    app.include_router(admin_router_module.router)
    app.dependency_overrides[require_admin] = lambda: current_user_from_model(user_model)
    return TestClient(app)


def test_owner_renames_their_own_personal_strategy(mu_db):
    user = make_user("edit-owner@example.com")
    strategy_id = _make_strategy(user)

    result = personal_pine_service.update_strategy_metadata(
        user.id, strategy_id, is_admin=False, display_name="New Name",
    )

    assert result["name"] == "New Name"
    with session_scope() as db:
        strategy = db.get(models.StrategyCatalog, strategy_id)
        assert strategy.display_name == "New Name"
        assert strategy.description == "Original description."  # untouched


def test_owner_cannot_rename_another_users_strategy(mu_db):
    owner = make_user("edit-real-owner@example.com")
    stranger = make_user("edit-stranger@example.com")
    strategy_id = _make_strategy(owner)

    with pytest.raises(personal_pine_service.PineWorkflowError) as exc_info:
        personal_pine_service.update_strategy_metadata(
            stranger.id, strategy_id, is_admin=False, display_name="Hijacked",
        )
    assert exc_info.value.code == "NOT_FOUND"


def test_admin_renames_a_published_shared_strategy_owned_by_no_one(mu_db):
    """The exact scenario this exists for: a NOVA_SHARED strategy has no
    owner_user_id at all -- only an admin path can touch it."""
    admin = make_user("edit-admin@example.com", is_admin=True)
    strategy_id = _make_strategy(admin, owner_type="nova", visibility="nova_shared", code="pivotext")

    result = personal_pine_service.update_strategy_metadata(
        admin.id, strategy_id, is_admin=True,
        display_name="Pivot Extension v2", description="Updated for all users.",
    )

    assert result["name"] == "Pivot Extension v2"
    assert result["description"] == "Updated for all users."
    with session_scope() as db:
        strategy = db.get(models.StrategyCatalog, strategy_id)
        assert strategy.code == "pivotext"  # catalog_code / webhook path untouched


def test_update_rejects_empty_display_name(mu_db):
    user = make_user("edit-empty-name@example.com")
    strategy_id = _make_strategy(user)

    with pytest.raises(personal_pine_service.PineWorkflowError) as exc_info:
        personal_pine_service.update_strategy_metadata(user.id, strategy_id, is_admin=False, display_name="   ")
    assert exc_info.value.code == "DISPLAY_NAME_REQUIRED"


def test_update_rejects_no_fields_given(mu_db):
    user = make_user("edit-no-fields@example.com")
    strategy_id = _make_strategy(user)

    with pytest.raises(personal_pine_service.PineWorkflowError) as exc_info:
        personal_pine_service.update_strategy_metadata(user.id, strategy_id, is_admin=False)
    assert exc_info.value.code == "NO_FIELDS_GIVEN"


def test_owner_router_endpoint_renames_strategy(mu_db):
    user = make_user("edit-router-owner@example.com")
    strategy_id = _make_strategy(user)

    response = _owner_client(user).patch(
        f"/api/personal-pine-strategies/{strategy_id}", json={"display_name": "Router Renamed"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Router Renamed"


def test_admin_router_endpoint_renames_another_users_strategy(mu_db):
    owner = make_user("edit-router-target-owner@example.com")
    admin = make_user("edit-router-admin@example.com", is_admin=True)
    strategy_id = _make_strategy(owner)

    response = _admin_router_client(admin).patch(
        f"/api/admin/strategies/{strategy_id}", json={"display_name": "Admin Renamed"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Admin Renamed"


def test_no_op_rename_does_not_write_an_audit_log(mu_db):
    """Setting display_name to its current value is a valid, harmless
    no-op -- must not create audit noise for every unchanged save."""
    user = make_user("edit-noop@example.com")
    strategy_id = _make_strategy(user)

    personal_pine_service.update_strategy_metadata(
        user.id, strategy_id, is_admin=False, display_name="Original Name",
    )

    with session_scope() as db:
        count = db.scalar(
            select(models.AuditLog).where(models.AuditLog.action == "STRATEGY_METADATA_UPDATED")
        )
        assert count is None
