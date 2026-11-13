"""Bed allocations (docs/03 §5.7). Occupancy is derived from these rows, never stored on the bed."""

import uuid
from datetime import date, datetime

from sqlalchemy import Computed, Date, Text, Uuid
from sqlalchemy.dialects.postgresql import DATERANGE
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz, updated_at, uuid_pk


class BedAllocation(Base):
    __tablename__ = "bed_allocations"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    bed_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    academic_session_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(Text)  # reserved | active | ended | cancelled
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    # daterange(start_date, end_date, '[)'): the database's exclusion constraints work on it
    stay: Mapped[object] = mapped_column(DATERANGE, Computed("daterange(start_date, end_date, '[)')"), nullable=True)
    reservation_expires_at: Mapped[datetime | None] = tz()
    checked_in_at: Mapped[datetime | None] = tz()
    checked_out_at: Mapped[datetime | None] = tz()
    end_reason: Mapped[str | None] = mapped_column(Text)
    previous_allocation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
