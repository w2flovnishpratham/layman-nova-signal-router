"""Shared safety utilities for the R1B-2A insert-only evidence writers.

EVIDENCE ONLY — NOT EXECUTION AUTHORITY. This module is pure glue for the
three evidence writer libraries: canonical hashing, closed-value validation,
secret-taint checks and database-error translation. It imports no FastAPI
request machinery, no router, no broker, no state store, and it never stores
raw payloads, credentials, exception text or Pine source.

Every evidence hash is generated internally with hashlib.sha256(...) —
caller-supplied hashes are accepted only as opaque, shape-validated trusted
ingress artifacts (payload fingerprints) and never as writer-generated keys.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.domain.secret_taint import is_credential_shaped

HEX64_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
# Version-ish closed identifiers (adapter/contract versions): bounded, no
# whitespace, no controls, cannot carry a URL, header or path.
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,59}\Z")


class EvidenceWriterError(RuntimeError):
    """Closed evidence-writer failure. Carries only a closed code — never SQL
    text, constraint internals, paths, payloads, credentials or stack info."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CanonicalDecisionPersistenceError(EvidenceWriterError):
    pass


class CanonicalOutcomePersistenceError(EvidenceWriterError):
    pass


class SignalRejectionPersistenceError(EvidenceWriterError):
    pass


def evidence_key(namespace: str, parts: Iterable[object]) -> str:
    """Deterministic internal idempotency/dedupe key: canonical '|' joining,
    UTF-8, SHA-256 hexdigest (always 64 lowercase hex)."""
    canonical = "|".join([namespace, *("NONE" if part is None else str(part) for part in parts)])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_hex64(value: object, error: EvidenceWriterError) -> str:
    """Accept an opaque trusted server hash by exact shape only."""
    if not isinstance(value, str) or not HEX64_PATTERN.fullmatch(value):
        raise error
    return value


def require_closed(value: object, allowed: frozenset[str], error: EvidenceWriterError) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise error
    return value


def require_identifier(value: object, error: EvidenceWriterError) -> str:
    """Bounded identifier-shaped string (versions); refuses free text, URLs,
    headers, paths, whitespace and credential-shaped values."""
    if (
        not isinstance(value, str)
        or not _IDENTIFIER_PATTERN.fullmatch(value)
        or is_credential_shaped(value)
    ):
        raise error
    return value


def require_aware_utc(value: object, error: EvidenceWriterError) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise error
    return value.astimezone(timezone.utc)


def reject_tainted(values: Iterable[object], error: EvidenceWriterError, known_secrets: Iterable[str] = ()) -> None:
    """Suppress the whole evidence row when any free-text input is
    credential-shaped or matches a request-local known secret."""
    secrets = tuple(known_secrets)
    for value in values:
        if is_credential_shaped(value, secrets):
            raise error


def translate_db_error(exc: SQLAlchemyError, error: EvidenceWriterError) -> EvidenceWriterError:
    """Translate any SQLAlchemy/database failure to one closed code. The raw
    exception (which may echo SQL text and parameters) is dropped, never
    chained, never stored."""
    del exc
    return error


__all__ = [
    "CanonicalDecisionPersistenceError",
    "CanonicalOutcomePersistenceError",
    "EvidenceWriterError",
    "HEX64_PATTERN",
    "IntegrityError",
    "SignalRejectionPersistenceError",
    "SQLAlchemyError",
    "evidence_key",
    "reject_tainted",
    "require_aware_utc",
    "require_closed",
    "require_hex64",
    "require_identifier",
    "sha256_text",
    "translate_db_error",
]
