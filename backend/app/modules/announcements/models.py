"""Announcements (docs/03 §5.14)."""

import uuid
from datetime import datetime

from sqlalchemy import Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import APP_NOW, created_at, tz, uuid_pk


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # None = whole organization
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(Text)  # students | staff | everyone
    published_at: Mapped[datetime] = tz(nullable=False, server_default=APP_NOW)
    expires_at: Mapped[datetime | None] = tz()
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_at()
