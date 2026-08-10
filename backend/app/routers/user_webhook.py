"""Per-user webhook with HMAC verification + replay protection.

Endpoint:
    POST /api/webhook/user/{user_id}

The signed payload also carries user_id so the signature binds the signal to a
specific user. The HMAC is verified with THAT user's saved webhook secret, so one
user's webhook can never trigger another user's order.

Payload:
    {
      "user_id": "...", "strategy": "...", "symbol": "NIFTY",
      "signal": "BUY" | "SELL" | "EXIT",
      "timestamp": <unix seconds>, "nonce": "...", "signature": "<hex>"
    }

Replay protection:
    * timestamp must be within +/- WEBHOOK_TIMESTAMP_WINDOW_SECONDS
    * (user_id, nonce) must not have been seen before (TTL cache)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import crud
from app.db.engine import database_configured, session_scope
from app.services import user_credential_vault as vault
from app.services import webhook_replay_store
from app.services.user_context import CurrentUser, current_user_from_model

router = APIRouter(prefix="/api/webhook", tags=["Webhook (per-user)"])

WEBHOOK_TIMESTAMP_WINDOW_SECONDS = 300  # reject signals older/newer than 5 min
_SEEN_NONCES: dict[str, float] = {}  # "user_id:nonce" -> expiry epoch
_NONCE_LOCK = threading.RLock()
_VALID_SIGNALS = {"BUY", "SELL", "EXIT"}


def build_signature(secret: str, *, user_id: str, strategy: str, symbol: str, signal: str, timestamp: Any, nonce: str) -> str:
    """Canonical HMAC-SHA256 signature (also used by clients/tests)."""
    message = f"{user_id}|{strategy}|{symbol}|{signal}|{timestamp}|{nonce}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _prune_nonces(now: float) -> None:
    expired = [k for k, exp in _SEEN_NONCES.items() if exp < now]
    for k in expired:
        _SEEN_NONCES.pop(k, None)


def _check_and_record_nonce(user_id: str, nonce: str) -> bool:
    """Return True if nonce is fresh (and record it); False if a replay."""
    now = time.time()
    key = f"{user_id}:{nonce}"
    with _NONCE_LOCK:
        _prune_nonces(now)
        if key in _SEEN_NONCES:
            return False

    try:
        durable_status = webhook_replay_store.record_user_nonce(user_id, nonce)
    except Exception:
        return False
    if durable_status == "duplicate":
        return False
    if durable_status == "fresh":
        with _NONCE_LOCK:
            _SEEN_NONCES[key] = now + WEBHOOK_TIMESTAMP_WINDOW_SECONDS * 2
        return True

    # Local/dev fallback only. Production requires DATABASE_URL and uses the DB
    # unique constraint above as the source of truth.
    with _NONCE_LOCK:
        if key in _SEEN_NONCES:
            return False
        _SEEN_NONCES[key] = now + WEBHOOK_TIMESTAMP_WINDOW_SECONDS * 2
        return True


def _record_accepted_signal(
    *,
    user: CurrentUser,
    user_id: str,
    nonce: str,
    signal: str,
    symbol: str,
    strategy: str,
) -> None:
    """Best-effort audit + replay-status persistence for an accepted signal.

    Both writes are already non-fatal (a failure here must not reject a
    signal that authenticated cleanly); grouped into one function so the
    caller can push all of it off the event loop in a single hop.
    """
    try:
        with session_scope() as db:
            crud.add_audit_log(
                db,
                user_id=user.id,
                action="WEBHOOK_SIGNAL_RECEIVED",
                metadata={"signal": signal, "symbol": symbol, "strategy": strategy},
            )
    except Exception:
        pass
    try:
        webhook_replay_store.update_webhook_event(
            provider="user",
            event_id=f"{user_id}:{nonce}",
            processed_status="accepted",
        )
    except Exception:
        pass


def _resolve_user(user_id: str) -> CurrentUser | None:
    if not database_configured():
        return None
    try:
        with session_scope() as db:
            user = crud.get_user_by_id(db, user_id)
            return current_user_from_model(user) if user else None
    except Exception:
        return None


@router.post("/user/{user_id}")
async def user_webhook(user_id: str, request: Request) -> JSONResponse:
    if not settings.WEBHOOK_TRADING_ENABLED:
        return JSONResponse(status_code=403, content={"ok": False, "error": "Webhook trading is disabled."})

    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode("utf-8", errors="replace"))
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid JSON."})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Payload must be an object."})

    # user_id in the path must match the signed payload.
    body_user_id = str(payload.get("user_id") or "").strip()
    if body_user_id and body_user_id != str(user_id):
        return JSONResponse(status_code=400, content={"ok": False, "error": "user_id mismatch."})

    try:
        uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid user_id."})

    signal = str(payload.get("signal") or "").strip().upper()
    strategy = str(payload.get("strategy") or "").strip()
    symbol = str(payload.get("symbol") or "").strip()
    timestamp = payload.get("timestamp")
    nonce = str(payload.get("nonce") or "").strip()
    signature = str(payload.get("signature") or "").strip()

    if signal not in _VALID_SIGNALS:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid signal."})
    if not nonce or not signature or timestamp is None:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Missing nonce/signature/timestamp."})

    # Timestamp window (replay protection).
    try:
        ts = float(timestamp)
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid timestamp."})
    if abs(time.time() - ts) > WEBHOOK_TIMESTAMP_WINDOW_SECONDS:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Stale timestamp."})

    # Resolve the user and their secret.
    # Sync SQLAlchemy: off the event loop, like every blocking call below.
    user = await asyncio.to_thread(_resolve_user, user_id)
    if user is None:
        # Do not reveal whether the user exists.
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized."})

    secret = None
    try:
        secret = vault.get_user_webhook_secret(user.id)
    except Exception:
        secret = None
    if settings.WEBHOOK_HMAC_REQUIRED and not secret:
        return JSONResponse(status_code=401, content={"ok": False, "error": "No webhook secret for user."})
    if not secret:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized."})

    expected = build_signature(
        secret,
        user_id=str(user_id),
        strategy=strategy,
        symbol=symbol,
        signal=signal,
        timestamp=timestamp,
        nonce=nonce,
    )
    if not hmac.compare_digest(expected, signature):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Invalid signature."})

    if not _check_and_record_nonce(str(user_id), nonce):
        return JSONResponse(status_code=409, content={"ok": False, "error": "Duplicate nonce (replay)."})

    if database_configured():
        try:
            event_claim = await asyncio.to_thread(
                webhook_replay_store.claim_webhook_event,
                provider="user",
                event_id=f"{user_id}:{nonce}",
                raw_body=raw_body,
                user_id=user.id,
                signature_ok=True,
                metadata={"signal": signal, "symbol": symbol, "strategy": strategy},
            )
        except Exception:
            return JSONResponse(status_code=503, content={"ok": False, "error": "Webhook replay store unavailable."})
        if event_claim.get("status") in {"duplicate", "tampered"}:
            return JSONResponse(status_code=409, content={"ok": False, "error": "Duplicate webhook event."})

    # Signal authenticated and bound to this user. Record it (audit + per-user).
    # NOTE: actual order routing happens only inside a user's active real_orders
    # run after the live-engine safety gates pass — this endpoint never places an
    # order for another user.
    if database_configured() and not user.is_dev:
        await asyncio.to_thread(
            _record_accepted_signal,
            user=user,
            user_id=user_id,
            nonce=nonce,
            signal=signal,
            symbol=symbol,
            strategy=strategy,
        )

    return JSONResponse(
        status_code=202,
        content={"ok": True, "accepted": True, "user_id": str(user_id), "signal": signal, "symbol": symbol},
    )
