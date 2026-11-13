"""Hostel structure (docs/03 §5.3): hostel -> building -> floor -> room -> bed, plus room types.

Every child row carries (org_id, hostel_id) and points at its parent through a composite
foreign key, so a room can never hang under another organization's or another hostel's floor.
Bed states (vacant/occupied/reserved/maintenance/inactive) are derived by the bed_states
view, never stored (03 §9).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, SmallInteger, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, updated_at, uuid_pk, version_column


class Hostel(Base):
    __tablename__ = "hostels"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    resident_type: Mapped[str | None] = mapped_column(Text)  # boys | girls | coed (Q19)
    address: Mapped[str | None] = mapped_column(Text)
    local_outing_requires_approval: Mapped[bool] = mapped_column(Boolean, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = created_at()

    __mapper_args__ = {"version_id_col": version}


class Floor(Base):
    __tablename__ = "floors"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    building_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    level: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    __mapper_args__ = {"version_id_col": version}


class RoomType(Base):
    __tablename__ = "room_types"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    beds_per_room: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = created_at()

    __mapper_args__ = {"version_id_col": version}


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    floor_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    room_type_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    number: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="available")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}


class Bed(Base):
    __tablename__ = "beds"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = version_column()
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hostel_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    room_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    label: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="available")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __mapper_args__ = {"version_id_col": version}
