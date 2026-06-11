from __future__ import annotations

import re
from contextvars import ContextVar, Token
from pathlib import Path

from app.config import RUNTIME_LOG_DIR, RUNTIME_STATE_DIR


_CURRENT_USER_ID: ContextVar[str | None] = ContextVar("current_user_id", default=None)
_SAFE_USER_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def current_user_id() -> str | None:
    return _CURRENT_USER_ID.get()


def set_current_user_id(user_id: str | None) -> Token[str | None]:
    normalized = str(user_id or "").strip() or None
    return _CURRENT_USER_ID.set(normalized)


def reset_current_user_id(token: Token[str | None]) -> None:
    _CURRENT_USER_ID.reset(token)


def safe_user_path_segment(user_id: str) -> str:
    safe = _SAFE_USER_ID_RE.sub("_", user_id.strip())
    if not safe or safe in {".", ".."}:
        raise ValueError("Invalid user id for runtime scoping.")
    return safe


def scoped_runtime_dir(user_id: str | None = None) -> Path:
    user_id = user_id or current_user_id()
    if not user_id:
        return RUNTIME_STATE_DIR
    return RUNTIME_STATE_DIR / "users" / safe_user_path_segment(user_id)


def scoped_log_dir(user_id: str | None = None) -> Path:
    user_id = user_id or current_user_id()
    if not user_id:
        return RUNTIME_LOG_DIR
    return RUNTIME_LOG_DIR / "users" / safe_user_path_segment(user_id)


def scoped_file(base_path: Path) -> Path:
    user_id = current_user_id()
    if not user_id:
        return base_path
    return base_path.parent / "users" / safe_user_path_segment(user_id) / base_path.name
