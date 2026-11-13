"""Cross-cutting tables owned by core: background jobs and the audit log (docs/03 §5.15-5.16)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, SmallInteger, Text, Uuid
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import NOW, created_at, tz, uuid_pk


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # NULL = platform-wide job
    kind: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # IDs only, never secrets
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    run_at: Mapped[datetime] = tz(nullable=False, server_default=NOW)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    max_attempts: Mapped[int] = mapped_column(SmallInteger, server_default="5")
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = tz()
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at("real")
    finished_at: Mapped[datetime | None] = tz()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = tz(nullable=False, server_default=NOW)
    actor_type: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_platform_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    changed_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    before_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
