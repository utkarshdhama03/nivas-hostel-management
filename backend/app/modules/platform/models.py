"""Platform Owner tables (docs/03 §5.1). Only the hms_platform role can touch these."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import SmallInteger, Text, Uuid
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk, version_column


class PlatformUser(Base):
    __tablename__ = "platform_users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    locked_until: Mapped[datetime | None] = tz()
    last_login_at: Mapped[datetime | None] = tz()
    created_at: Mapped[datetime] = created_at("real")
    updated_at: Mapped[datetime] = updated_at("real")


class PlatformSession(Base):
    __tablename__ = "platform_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    platform_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
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


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = version_column()
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = updated_at("real")

    __mapper_args__ = {"version_id_col": version}
