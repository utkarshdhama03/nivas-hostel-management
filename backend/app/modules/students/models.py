"""Students, guardians (emergency contacts) and student documents (docs/03 §5.6)."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, SmallInteger, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk, version_column


class Student(Base):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_code: Mapped[str] = mapped_column(Text)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    admission_session_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    gender: Mapped[str] = mapped_column(Text)
    date_of_birth: Mapped[date] = mapped_column(Date)
    photo_file_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    admission_date: Mapped[date] = mapped_column(Date)
    institution_roll_no: Mapped[str | None] = mapped_column(Text)
    program: Mapped[str | None] = mapped_column(Text)
    year_of_study: Mapped[int | None] = mapped_column(SmallInteger)
    address_line1: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    exited_on: Mapped[date | None] = mapped_column(Date)
    exit_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class StudentGuardian(Base):
    __tablename__ = "student_guardians"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    full_name: Mapped[str] = mapped_column(Text)
    relationship: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text)
    alt_phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class StudentDocument(Base):
    __tablename__ = "student_documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    file_category: Mapped[str] = mapped_column(Text)
    file_mime_type: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(Text)
    id_type: Mapped[str | None] = mapped_column(Text)
    id_number_masked: Mapped[str | None] = mapped_column(Text)
    masked_attested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    masked_attested_at: Mapped[datetime | None] = tz()
    uploaded_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    verified_at: Mapped[datetime | None] = tz()
    created_at: Mapped[datetime] = created_at()
