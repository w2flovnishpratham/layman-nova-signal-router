"""Error responses must never leak internals, and must stay useful for 4xx.

Motivating incident: a database outage rendered a bare "Internal Server
Error" page on the OAuth callback, and an earlier 502 echoed the upstream
Google token endpoint URL and httpx error text straight to the browser.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_handlers import (
    GENERIC_SERVER_ERROR,
    http_exception_handler,
    unhandled_exception_handler,
)

SECRET_TEXT = "postgresql://user:hunter2@db.internal:5432/nova"


def _client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/boom")
    def boom():
        raise RuntimeError(f"connection failed: {SECRET_TEXT}")

    @app.get("/upstream")
    def upstream():
        raise HTTPException(
            status_code=502,
            detail=f"Google token exchange failed: 400 for url {SECRET_TEXT}",
        )

    @app.get("/bad-request")
    def bad_request():
        raise HTTPException(status_code=400, detail="Duplicate webhook signal.")

    # raise_server_exceptions=False so the handler's response is returned
    # rather than the exception being re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_exception_never_returns_the_exception_text():
    response = _client().get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == GENERIC_SERVER_ERROR
    assert SECRET_TEXT not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text


def test_unhandled_exception_returns_a_traceable_error_id():
    body = _client().get("/boom").json()

    assert body["errorId"].startswith("ERR-")


def test_error_ids_are_unique_per_occurrence():
    client = _client()
    first = client.get("/boom").json()["errorId"]
    second = client.get("/boom").json()["errorId"]

    assert first != second


def test_5xx_http_exception_detail_is_replaced_not_forwarded():
    response = _client().get("/upstream")

    assert response.status_code == 502
    assert response.json()["detail"] == GENERIC_SERVER_ERROR
    assert SECRET_TEXT not in response.text
    assert "Google" not in response.text


def test_4xx_detail_is_preserved_because_the_ui_renders_it():
    """api.ts reads body.detail -- sanitising 4xx would replace every
    actionable message ("Duplicate webhook signal.") with noise."""
    response = _client().get("/bad-request")

    assert response.status_code == 400
    assert response.json()["detail"] == "Duplicate webhook signal."


def test_unknown_route_still_returns_a_plain_404():
    response = _client().get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
