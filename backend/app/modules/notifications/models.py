"""In-app notifications and email preferences (docs/03 §5.14)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk

# Categories a user may mute (Q49). The database CHECK allows exactly these two;
# security emails can never be muted.
MUTABLE_EMAIL_CATEGORIES = ("announcements", "reminders")

# The pages a notification may open (D84). The database CHECK `notifications_link_known` allows exactly
# these (or no link); the frontend test src/components/notification-links.test.ts checks each is a route.
NOTIFICATION_LINKS = (
    "/portal/fees",
    "/portal/leave",
    "/portal/complaints",
    "/portal/announcements",
    "/admin/leave",
    "/admin/complaints",
    "/admin/registration-requests",
    "/admin/announcements",
)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    category: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    read_at: Mapped[datetime | None] = tz()


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = updated_at()
