"""Users, sessions, one-time tokens and roles (docs/03 §5.4)."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, SmallInteger, Text, Uuid
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk, version_column


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(Text)  # 'staff' | 'student'
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = tz()
    phone: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending_activation")
    read_only_until: Mapped[date | None] = mapped_column(Date)
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    locked_until: Mapped[datetime | None] = tz()
    last_login_at: Mapped[datetime | None] = tz()
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_at("real")
    updated_at: Mapped[datetime] = updated_at("real")

    __mapper_args__ = {"version_id_col": version}


class UserSession(Base):
    """A login. The refresh token's hash lives here; each refresh rotates to a new row in the
    same family (docs/04 §5.6)."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    refresh_token_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at("real")
    last_used_at: Mapped[datetime | None] = tz()
    expires_at: Mapped[datetime] = tz(nullable=False)
    absolute_expires_at: Mapped[datetime] = tz(nullable=False)
    revoked_at: Mapped[datetime | None] = tz()
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class AuthToken(Base):
    """Activation, organization-registration and student-request (D74) links; only the hash is stored.
    (Password reset uses an emailed code since D77: email_otps with purpose password_reset.)"""

    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # None only for a student_request link (D74)
    registration_request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    purpose: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text)
    delivery: Mapped[str] = mapped_column(Text)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    expires_at: Mapped[datetime] = tz(nullable=False)
    used_at: Mapped[datetime | None] = tz()
    revoked_at: Mapped[datetime | None] = tz()
    created_at: Mapped[datetime] = created_at("real")


class EmailOtp(Base):
    __tablename__ = "email_otps"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    code_hash: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = tz(nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    consumed_at: Mapped[datetime | None] = tz()
    purpose: Mapped[str] = mapped_column(Text, server_default="password_reset")  # D77; sign-in codes went (D87)
    cancelled_at: Mapped[datetime | None] = tz()  # 5 wrong attempts, or a newer reset code was sent
    created_at: Mapped[datetime] = created_at("real")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    identifier_hash: Mapped[str] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    attempted_at: Mapped[datetime] = created_at("real")


class StaffMember(Base):
    __tablename__ = "staff_members"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    employee_code: Mapped[str] = mapped_column(Text)
    designation: Mapped[str | None] = mapped_column(Text)
    joined_on: Mapped[date | None] = mapped_column(Date)
    left_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class RoleAssignment(Base):
    """(user, role, hostel or NULL = organization-wide). Revoked rows stay as history."""

    __tablename__ = "role_assignments"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(Text)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    granted_at: Mapped[datetime] = created_at("real")
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    revoked_at: Mapped[datetime | None] = tz()
    revoke_reason: Mapped[str | None] = mapped_column(Text)  # 'role_removed_d75': revoked by migration 0008
