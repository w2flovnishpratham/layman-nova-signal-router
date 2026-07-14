"""Private strategy-instance webhook endpoint (Phase 3A).

POST /api/webhooks/private

The opaque JSON-body credential resolves credential -> StrategyInstance -> owner.
The body can never name a tenant, mode, quantity, or contract. The endpoint
authenticates, validates, persists the durable signal + execution job, and
returns 202 — it never places an order inline.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.services import private_webhook_service as service

router = APIRouter(prefix="/api/webhooks", tags=["Private Strategy Webhook"])

_RATE_BUCKETS: dict[str, list[float]] = {}
_RATE_LOCK = threading.RLock()


def _error(status_code: int, message: str, reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": message, "reason": reason},
    )


def _credential_rate_limited(token_hash: str) -> bool:
    """Per-credential limiter so one tenant cannot exhaust webhook workers."""
    limit = max(int(settings.PRIVATE_WEBHOOK_RATE_LIMIT_PER_MINUTE), 1)
    now = time.monotonic()
    cutoff = now - 60.0
    with _RATE_LOCK:
        recent = [item for item in _RATE_BUCKETS.get(token_hash, []) if item >= cutoff]
        if len(recent) >= limit:
            _RATE_BUCKETS[token_hash] = recent
            return True
        recent.append(now)
        _RATE_BUCKETS[token_hash] = recent
        # ponytail: unbounded bucket keys pruned lazily; add periodic sweep if
        # credential cardinality ever grows past a few thousand.
        return False


@router.post("/private")
async def private_instance_webhook(request: Request) -> JSONResponse:
    if not settings.PRIVATE_STRATEGY_WEBHOOK_EXECUTION_ENABLED:
        return _error(403, "Private strategy webhooks are disabled.", "DISABLED")

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return _error(415, "Content-Type must be application/json.", "INVALID_CONTENT_TYPE")

    raw_body = await request.body()
    if len(raw_body) > max(int(settings.PRIVATE_WEBHOOK_MAX_BODY_BYTES), 256):
        return _error(413, "Request body is too large.", "BODY_TOO_LARGE")

    try:
        data = json.loads(raw_body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return _error(400, "Invalid JSON.", "INVALID_JSON")
    if not isinstance(data, dict):
        return _error(400, "Payload must be a JSON object.", "INVALID_JSON")

    try:
        credential, payload = service.parse_request(data)
        if _credential_rate_limited(service.hash_credential(credential.strip())):
            return _error(429, "Rate limit exceeded for this webhook.", "RATE_LIMITED")
        auth = service.authenticate_credential(credential, correlation_id=uuid.uuid4().hex)
        service.validate_instance_lifecycle(auth)
        result = service.ingest(auth, payload)
    except service.PrivateWebhookError as exc:
        return _error(exc.status_code, str(exc), exc.reason)

    status_code = 200 if result.get("duplicate") else 202
    return JSONResponse(status_code=status_code, content=result)
