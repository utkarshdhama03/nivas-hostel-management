"""Notification service (docs/02 §10): modules emit, this decides in-app + email.

    notify(db, org_id=..., user_id=..., category="leave", title=..., body=...)

1. Always writes an in-app notification (in-app can't be muted, Q49).
2. Unless the user muted that category's email (announcements, reminders only),
   enqueues an `email.notification` job carrying only the notification's ID.
Both happen in the caller's transaction (transactional outbox, 02 §4.4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import email, jobs
from app.core.config import get_settings
from app.core.models import Job
from app.modules.identity.models import User
from app.modules.notifications.models import (
    MUTABLE_EMAIL_CATEGORIES,
    NOTIFICATION_LINKS,
    Notification,
    NotificationPreference,
)
from app.modules.organizations.models import Organization

CATEGORY_PRIORITY = {
    "security": jobs.PRIORITY_SECURITY,
    "announcements": jobs.PRIORITY_BULK,
    "reminders": jobs.PRIORITY_BULK,
}


def email_enabled(db: Session, user_id: uuid.UUID, category: str) -> bool:
    if category not in MUTABLE_EMAIL_CATEGORIES:
        return True  # security and transactional emails can't be muted
    pref = db.scalar(
        select(NotificationPreference.email_enabled).where(
            NotificationPreference.user_id == user_id, NotificationPreference.category == category
        )
    )
    return True if pref is None else pref


def notify(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    category: str,
    title: str,
    body: str,
    link: str | None = None,
    send_email: bool = True,
) -> Notification:
    if link is not None and link not in NOTIFICATION_LINKS:  # the database refuses it too (D84)
        raise ValueError(f"notification link {link!r} is not a known page")
    n = Notification(org_id=org_id, user_id=user_id, category=category, title=title, body=body, link=link)
    db.add(n)
    db.flush()  # get the ID; also surfaces a CHECK violation (bad link, long title) right here
    if send_email and email_enabled(db, user_id, category):
        jobs.enqueue(
            db,
            "email.notification",
            {"notification_id": str(n.id)},
            org_id=org_id,
            priority=CATEGORY_PRIORITY.get(category, jobs.PRIORITY_TRANSACTIONAL),
        )
    return n


def next_cap_day(now: datetime) -> datetime:
    """00:05 UTC after `now`: shortly after the email cap's counting day (a UTC day) starts again."""
    return (now.astimezone(UTC) + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)


def _daily_cap_reached(db: Session) -> bool:
    cap = get_settings().hms_email_daily_cap
    if cap is None:
        return False
    sent = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.kind.like("email.%"),
            Job.status == "succeeded",
            Job.finished_at >= func.date_trunc("day", func.now(), "UTC"),  # the cap day is a UTC day
        )
    )
    return (sent or 0) >= cap


@jobs.handler("email.notification")
def send_notification_email(db: Session, job: jobs.ClaimedJob) -> None:
    """Runs in the worker, under the notification's tenant context."""
    if job.priority > jobs.PRIORITY_SECURITY and _daily_cap_reached(db):
        # The cap is platform-wide (the mail provider's daily limit), counted per UTC day, so the held
        # email is retried just after the next UTC day starts: 05:35 IST, on purpose.
        tomorrow = next_cap_day(datetime.now(UTC))
        raise jobs.RetryLater(tomorrow)  # security emails are never held back by the cap
    row = db.execute(
        select(Notification, User.email, User.status, Organization.name)
        .join(User, (User.org_id == Notification.org_id) & (User.id == Notification.user_id))
        .join(Organization, Organization.id == Notification.org_id)
        .where(Notification.id == uuid.UUID(str(job.payload["notification_id"])))
    ).one_or_none()
    if row is None:
        return  # not visible under this tenant: nothing to send
    n, address, status, org_name = row
    if not address or status not in ("active", "read_only") or not email_enabled(db, n.user_id, n.category):
        return  # nobody to send to, or muted since the job was queued
    link_url = get_settings().hms_public_url.rstrip("/") + n.link if n.link else None
    message = email.render("notification", n.title, title=n.title, body=n.body, link_url=link_url, org_name=org_name)
    email.get_sender().send(
        email.OutgoingEmail(
            to=address,
            subject=message.subject,
            text=message.text,
            html=message.html,
            message_id=email.message_id_for_job(job.id),
        )
    )
