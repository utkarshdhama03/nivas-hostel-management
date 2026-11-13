"""Complaints (docs/03 §5.11; Q40)."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, SmallInteger, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk, version_column


class ComplaintCategory(Base):
    __tablename__ = "complaint_categories"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = created_at()

    __mapper_args__ = {"version_id_col": version}


class Complaint(Base):
    __tablename__ = "complaints"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    room_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    category_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    target_date: Mapped[date] = mapped_column(Date)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    resolved_at: Mapped[datetime | None] = tz()
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = tz()
    reopen_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class ComplaintUpdate(Base):
    """Append-only history and comments."""

    __tablename__ = "complaint_updates"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    complaint_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class ComplaintAttachment(Base):
    __tablename__ = "complaint_attachments"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    complaint_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_at()
