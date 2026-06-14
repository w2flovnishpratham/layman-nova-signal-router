from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sign_executor_request(secret: str, timestamp: int, nonce: str, raw_body: bytes) -> str:
    message = str(timestamp).encode() + b"." + nonce.encode() + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_executor_signature(
    *,
    secret: str,
    timestamp: int,
    nonce: str,
    raw_body: bytes,
    signature: str,
) -> bool:
    expected = sign_executor_request(secret, timestamp, nonce, raw_body)
    return hmac.compare_digest(expected, signature.strip().lower())


def signed_executor_headers(executor_code: str, secret: str, raw_body: bytes) -> dict[str, str]:
    timestamp = int(time.time())
    nonce = secrets.token_urlsafe(24)
    return {
        "Content-Type": "application/json",
        "X-Nova-Executor-Timestamp": str(timestamp),
        "X-Nova-Executor-Nonce": nonce,
        "X-Nova-Executor-Code": executor_code,
        "X-Nova-Executor-Signature": sign_executor_request(secret, timestamp, nonce, raw_body),
    }
