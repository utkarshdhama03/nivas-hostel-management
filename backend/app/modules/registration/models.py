"""Student registration requests (D74; docs/03 §5.11)."""

import uuid
from datetime import date, datetime

from sqlalchemy import LargeBinary, SmallInteger, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk


class StudentRegistrationRequest(Base):
    __tablename__ = "student_registration_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(Text, server_default="unverified")  # unverified|pending|approved|rejected
    full_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    gender: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[date | None] = mapped_column()
    program: Mapped[str | None] = mapped_column(Text)
    year_of_study: Mapped[int | None] = mapped_column(SmallInteger)
    guardian_name: Mapped[str | None] = mapped_column(Text)
    guardian_relationship: Mapped[str | None] = mapped_column(Text)
    guardian_phone: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    photo: Mapped[bytes | None] = mapped_column(LargeBinary)
    photo_mime: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = tz()
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = tz()
    reject_reason: Mapped[str | None] = mapped_column(Text)
    student_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    personal_data_deleted_at: Mapped[datetime | None] = tz()
    created_at: Mapped[datetime] = created_at("real")
    updated_at: Mapped[datetime] = updated_at("real")
