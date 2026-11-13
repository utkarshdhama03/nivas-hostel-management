"""Leave requests (docs/03 §5.10, Q35). Moved out of the gate module when gate passes were removed (D75)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    leave_type: Mapped[str] = mapped_column(Text)  # home | local_outing | emergency (Q35)
    reason: Mapped[str] = mapped_column(Text)
    destination: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = tz(nullable=False)
    ends_at: Mapped[datetime] = tz(nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = tz()
    auto_approved: Mapped[bool] = mapped_column(Boolean, server_default="false")
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
