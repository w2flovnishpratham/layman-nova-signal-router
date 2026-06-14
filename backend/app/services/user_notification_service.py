from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.auth.db import session_scope
from app.auth.models import UserNotification, utc_now_dt
from app.services.chat_event_publisher import publish_user_event_from_sync
from app.services.queue_service import JOB_USER_NOTIFICATION, enqueue_job


def create_user_notification(
    *,
    user_id: str,
    event_type: str,
    payload: dict[str, Any],
    dedupe_key: str,
) -> UserNotification:
    with session_scope() as session:
        notification = UserNotification(
            user_id=user_id,
            event_type=event_type,
            payload_json=payload,
            dedupe_key=dedupe_key,
        )
        session.add(notification)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(UserNotification).where(UserNotification.dedupe_key == dedupe_key)
            ).first()
            if existing is None:
                raise
            notification = existing
        else:
            session.refresh(notification)
        notification_id = int(notification.id)
        session.expunge(notification)

    enqueue_job(
        job_type=JOB_USER_NOTIFICATION,
        payload={"user_notification_id": notification_id},
        dedupe_key=f"user-notification:{notification_id}",
        priority=200,
    )
    return notification


def deliver_user_notification(notification_id: int) -> UserNotification:
    with session_scope() as session:
        notification = session.get(UserNotification, notification_id)
        if notification is None:
            raise LookupError("User notification not found.")
        if notification.status == "sent":
            session.expunge(notification)
            return notification
        user_id = notification.user_id
        event_type = notification.event_type
        payload = dict(notification.payload_json)

    publish_user_event_from_sync(user_id, event_type, payload)

    with session_scope() as session:
        notification = session.get(UserNotification, notification_id)
        if notification is None:
            raise LookupError("User notification not found.")
        notification.status = "sent"
        notification.sent_at = utc_now_dt()
        session.add(notification)
        session.commit()
        session.refresh(notification)
        session.expunge(notification)
        return notification
