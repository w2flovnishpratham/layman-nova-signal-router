from __future__ import annotations

import secrets
import string
from datetime import timedelta

from sqlmodel import Session, select

from app.auth.models import AuthSession, User, utc_now_dt
from app.config import settings


_NANO_ALPHABET = string.ascii_letters + string.digits


def new_user_id() -> str:
    return "u_" + "".join(secrets.choice(_NANO_ALPHABET) for _ in range(12))


def admin_emails() -> set[str]:
    return {
        item.strip().lower()
        for item in settings.ADMIN_EMAILS.split(",")
        if item.strip()
    }


def email_allowed(email: str) -> bool:
    normalized = email.strip().lower()
    if not normalized:
        return False
    # Open registration is an explicit opt-in. The email is still verified by the
    # OAuth callback (email_verified) before this is reached.
    if settings.ALLOW_PUBLIC_SIGNUP:
        return True
    # Fail closed: only the configured allowlist may log in.
    allowed = admin_emails()
    return bool(allowed) and normalized in allowed


def find_user_by_id(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def upsert_google_user(
    session: Session,
    *,
    email: str,
    google_sub: str,
    name: str | None,
    avatar_url: str | None,
) -> User:
    normalized_email = email.strip().lower()
    user = session.exec(select(User).where(User.google_sub == google_sub)).first()
    if user is None:
        existing_email = session.exec(select(User).where(User.email == normalized_email)).first()
        if existing_email is not None:
            raise ValueError("Email is already linked to another Google identity.")

    now = utc_now_dt()
    if user is None:
        user = User(
            id=_new_unique_user_id(session),
            email=normalized_email,
            google_sub=google_sub,
            name=name,
            avatar_url=avatar_url,
            last_login=now,
        )
        session.add(user)
    else:
        user.email = normalized_email
        user.google_sub = google_sub
        user.name = name or user.name
        user.avatar_url = avatar_url or user.avatar_url
        user.last_login = now

    session.commit()
    session.refresh(user)
    return user


def create_auth_session(session: Session, user: User) -> AuthSession:
    now = utc_now_dt()
    auth_session = AuthSession(
        id=secrets.token_urlsafe(24),
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.AUTH_COOKIE_TTL_SECONDS),
    )
    session.add(auth_session)
    session.commit()
    session.refresh(auth_session)
    return auth_session


def _new_unique_user_id(session: Session) -> str:
    for _ in range(10):
        candidate = new_user_id()
        if session.get(User, candidate) is None:
            return candidate
    raise RuntimeError("Could not allocate a unique user id.")
