"""Organization tables (docs/03 §5.2)."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk, version_column


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending_verification")
    timezone: Mapped[str] = mapped_column(Text, server_default="Asia/Kolkata")
    email_verified_at: Mapped[datetime | None] = tz()
    suspended_at: Mapped[datetime | None] = tz()
    suspension_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class OrganizationProfile(Base):
    __tablename__ = "organization_profiles"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[int] = version_column()
    legal_name: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str] = mapped_column(Text)
    contact_phone: Mapped[str | None] = mapped_column(Text)
    address_line1: Mapped[str | None] = mapped_column(Text)
    address_line2: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    upi_id: Mapped[str | None] = mapped_column(Text)
    upi_payee_name: Mapped[str | None] = mapped_column(Text)
    accepts_student_requests: Mapped[bool] = mapped_column(Boolean, server_default="false")  # D74
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class OrgCounter(Base):
    """Gap-free numbering (receipts, student and employee codes). Incremented with
    UPDATE ... RETURNING inside the transaction that uses the number."""

    __tablename__ = "org_counters"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_key: Mapped[str] = mapped_column(Text, primary_key=True)
    last_value: Mapped[int] = mapped_column(BigInteger, server_default="0")


class AcademicSession(Base):
    """An academic year, e.g. '2026-27' (Q18). At most one is current per organization."""

    __tablename__ = "academic_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = created_at()

    __mapper_args__ = {"version_id_col": version}


class AcademicTerm(Base):
    """A term inside a session (for 'term' billing, Q24). Terms of one session never overlap."""

    __tablename__ = "academic_terms"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    academic_session_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = created_at()

    __mapper_args__ = {"version_id_col": version}
