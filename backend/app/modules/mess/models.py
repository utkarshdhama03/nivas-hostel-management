"""Mess menus, per hostel (docs/03 §5.12; Q41, Q42)."""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import updated_at, uuid_pk, version_column


class MessMenu(Base):
    __tablename__ = "mess_menus"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    menu_date: Mapped[date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(Text)  # breakfast | lunch | snacks | dinner
    items: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = updated_at()
