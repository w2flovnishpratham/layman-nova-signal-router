from __future__ import annotations

import uuid
import asyncio

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.domain.state_machine import SetupState
from app.services.user_context import CurrentUser


def _user(email: str) -> CurrentUser:
    return CurrentUser(id=uuid.uuid4(), email=email)


def test_legacy_trading_route_requires_login(monkeypatch):
    from app.auth.dependencies import get_current_user
    from app.config import settings
    from app.routers import control

    monkeypatch.setattr(settings, "AUTH_REQUIRED", True, raising=False)
    app = FastAPI()
    app.include_router(
        control.router,
        prefix="/api/control",
        dependencies=[Depends(get_current_user)],
    )

    response = TestClient(app).post("/api/control/emergency-stop")

    assert response.status_code == 401


def test_chat_session_is_owned_by_current_user(monkeypatch):
    from app.api import session as chat_session
    from app.auth.dependencies import get_current_user
    from app.config import settings
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True, raising=False)
    alice = _user("alice@example.com")
    current = {"user": alice}
    app = FastAPI()
    app.include_router(chat_session.router)
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(app)

    created = client.post("/api/session/start")
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    current["user"] = _user("bob@example.com")
    assert client.get(f"/api/session/{session_id}").status_code == 404

    current["user"] = alice
    assert client.get(f"/api/session/{session_id}").status_code == 200


def test_chat_websocket_rejects_a_different_user(monkeypatch):
    from app.api import ws as chat_ws
    from app.auth.dependencies import get_current_websocket_user
    from app.config import settings
    from app.store.redis_session import session_store
    from app.store.session_token import issue_session_token

    monkeypatch.setattr(settings, "AUTH_REQUIRED", True, raising=False)
    alice = _user("alice@example.com")
    bob = _user("bob@example.com")
    session = asyncio.run(
        session_store.create(user_id=alice.id_str, state=SetupState.IDLE)
    )
    token = issue_session_token(session.id)

    app = FastAPI()
    app.include_router(chat_ws.router)
    app.dependency_overrides[get_current_websocket_user] = lambda: bob

    with pytest.raises(WebSocketDisconnect) as exc:
        with TestClient(app).websocket_connect(
            f"/ws/session/{session.id}?token={token}"
        ):
            pass

    assert exc.value.code == 4404
