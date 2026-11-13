"""Hostel structure (docs/04 §12.5): hostels, buildings, floors, room types, rooms, beds,
the bed board and the rooms-and-beds CSV import.

Scope: every route checks the caller's permission (route level) and then that the record's
hostel is one of theirs (`get_scoped` / `check_hostel`); anything outside is 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, UploadFile, status
from pydantic import Field
from sqlalchemy import select, text

from app.core import audit
from app.core.context import OrgContext, require, staff_only
from app.core.crud import apply_patch, check_hostel, check_version, get_scoped, scope_query
from app.core.errors import ProblemError
from app.core.permissions import BED_BOARD_VIEW, HOSTEL_CREATE, HOSTEL_MANAGE, HOSTEL_VIEW
from app.core.schemas import Out, Strict, Versioned
from app.modules.hostels import imports
from app.modules.hostels.models import Bed, Building, Floor, Hostel, Room, RoomType

router = APIRouter(prefix="/admin", tags=["hostels"], dependencies=[Depends(staff_only)])
View = Annotated[OrgContext, Depends(require(HOSTEL_VIEW))]
Manage = Annotated[OrgContext, Depends(require(HOSTEL_MANAGE))]
Create = Annotated[OrgContext, Depends(require(HOSTEL_CREATE))]
Board = Annotated[OrgContext, Depends(require(BED_BOARD_VIEW))]

Code = Field(pattern=r"^[A-Z0-9-]{1,12}$")
Status = Literal["available", "maintenance", "inactive"]


# --- hostels ---------------------------------------------------------------------------------------


class HostelOut(Out):
    id: uuid.UUID
    code: str
    name: str
    resident_type: str | None
    address: str | None
    local_outing_requires_approval: bool
    is_active: bool
    version: int


class HostelIn(Strict):
    code: str = Code
    name: str = Field(min_length=2, max_length=120)
    resident_type: Literal["boys", "girls", "coed"] | None = None
    address: str | None = Field(default=None, max_length=300)
    local_outing_requires_approval: bool = True

    def columns(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class HostelPatch(Versioned):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    resident_type: Literal["boys", "girls", "coed"] | None = None
    address: str | None = Field(default=None, max_length=300)
    local_outing_requires_approval: bool | None = None
    is_active: bool | None = None


def _hostel_out(h: Hostel) -> HostelOut:
    return HostelOut(
        id=h.id,
        code=h.code,
        name=h.name,
        resident_type=h.resident_type,
        address=h.address,
        local_outing_requires_approval=h.local_outing_requires_approval,
        is_active=h.is_active,
        version=h.version,
    )


def _hostel(ctx: View, hostel_id: uuid.UUID, permission: str, lock: bool = False) -> Hostel:
    q = select(Hostel).where(Hostel.id == hostel_id)
    h = ctx.db.scalars(q.with_for_update() if lock else q).one_or_none()
    if h is None:
        raise ProblemError(404, "not_found", "Not found")
    check_hostel(ctx.principal, permission, h.id)
    return h


@router.get("/hostels")
def list_hostels(ctx: View) -> list[HostelOut]:
    q = scope_query(select(Hostel), Hostel, ctx.principal, HOSTEL_VIEW, column="id").order_by(Hostel.name)
    return [_hostel_out(h) for h in ctx.db.scalars(q)]


@router.post("/hostels", status_code=status.HTTP_201_CREATED)
def create_hostel(ctx: Create, body: HostelIn) -> HostelOut:
    h = Hostel(org_id=ctx.principal.org_id, **body.columns())
    ctx.db.add(h)
    ctx.db.flush()
    ctx.db.refresh(h)
    audit.record(
        ctx.db,
        ctx.principal,
        "hostel.created",
        "hostel",
        h.id,
        hostel_id=h.id,
        after={"code": h.code, "name": h.name, "resident_type": h.resident_type},
    )
    return _hostel_out(h)


@router.get("/hostels/{hostel_id}")
def get_hostel(ctx: View, hostel_id: uuid.UUID) -> HostelOut:
    return _hostel_out(_hostel(ctx, hostel_id, HOSTEL_VIEW))


@router.patch("/hostels/{hostel_id}")
def patch_hostel(ctx: Manage, hostel_id: uuid.UUID, body: HostelPatch) -> HostelOut:
    h = _hostel(ctx, hostel_id, HOSTEL_MANAGE, lock=True)
    check_version(h, body.version)
    data = body.model_dump(exclude_unset=True, exclude={"version"})
    before = {k: getattr(h, k) for k in data}
    for key, value in data.items():
        setattr(h, key, value)
    ctx.db.flush()
    ctx.db.refresh(h)
    audit.record(ctx.db, ctx.principal, "hostel.updated", "hostel", h.id, hostel_id=h.id, before=before, after=data)
    return _hostel_out(h)


# --- buildings, floors, room types, rooms, beds --------------------------------------------------------


class BuildingOut(Out):
    id: uuid.UUID
    hostel_id: uuid.UUID
    name: str
    is_active: bool
    version: int


class BuildingIn(Strict):
    name: str = Field(min_length=1, max_length=60)


class FloorOut(Out):
    id: uuid.UUID
    building_id: uuid.UUID
    level: int
    name: str | None
    version: int


class FloorIn(Strict):
    level: int = Field(ge=-5, le=200)
    name: str | None = Field(default=None, max_length=60)


class RoomTypeOut(Out):
    id: uuid.UUID
    hostel_id: uuid.UUID
    name: str
    description: str | None
    beds_per_room: int
    is_active: bool
    version: int


class RoomTypeIn(Strict):
    name: str = Field(min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    beds_per_room: int = Field(ge=1, le=20)


class RoomTypePatch(Versioned):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    is_active: bool | None = None


class RoomOut(Out):
    id: uuid.UUID
    hostel_id: uuid.UUID
    floor_id: uuid.UUID
    room_type_id: uuid.UUID
    number: str
    status: str
    notes: str | None
    version: int


class RoomIn(Strict):
    number: str = Field(pattern=r"^[A-Za-z0-9-]{1,10}$")
    room_type_id: uuid.UUID
    notes: str | None = Field(default=None, max_length=300)


class RoomPatch(Versioned):
    status: Status | None = None
    notes: str | None = Field(default=None, max_length=300)
    room_type_id: uuid.UUID | None = None


class BedOut(Out):
    id: uuid.UUID
    hostel_id: uuid.UUID
    room_id: uuid.UUID
    label: str
    status: str
    version: int


class BedIn(Strict):
    label: str = Field(pattern=r"^[A-Za-z0-9]{1,4}$")


class BedPatch(Versioned):
    status: Status | None = None


@router.get("/hostels/{hostel_id}/buildings")
def list_buildings(ctx: View, hostel_id: uuid.UUID) -> list[BuildingOut]:
    _hostel(ctx, hostel_id, HOSTEL_VIEW)
    rows = ctx.db.scalars(select(Building).where(Building.hostel_id == hostel_id).order_by(Building.name))
    return [BuildingOut.model_validate(b) for b in rows]


@router.post("/hostels/{hostel_id}/buildings", status_code=status.HTTP_201_CREATED)
def create_building(ctx: Manage, hostel_id: uuid.UUID, body: BuildingIn) -> BuildingOut:
    h = _hostel(ctx, hostel_id, HOSTEL_MANAGE)
    b = Building(org_id=h.org_id, hostel_id=h.id, name=body.name)
    ctx.db.add(b)
    ctx.db.flush()
    ctx.db.refresh(b)
    return BuildingOut.model_validate(b)


@router.get("/buildings/{building_id}/floors")
def list_floors(ctx: View, building_id: uuid.UUID) -> list[FloorOut]:
    b = get_scoped(ctx.db, Building, building_id, ctx.principal, HOSTEL_VIEW)
    return [
        FloorOut.model_validate(f)
        for f in ctx.db.scalars(select(Floor).where(Floor.building_id == b.id).order_by(Floor.level))
    ]


@router.post("/buildings/{building_id}/floors", status_code=status.HTTP_201_CREATED)
def create_floor(ctx: Manage, building_id: uuid.UUID, body: FloorIn) -> FloorOut:
    b = get_scoped(ctx.db, Building, building_id, ctx.principal, HOSTEL_MANAGE)
    f = Floor(org_id=b.org_id, hostel_id=b.hostel_id, building_id=b.id, level=body.level, name=body.name)
    ctx.db.add(f)
    ctx.db.flush()
    ctx.db.refresh(f)
    return FloorOut.model_validate(f)


@router.get("/hostels/{hostel_id}/room-types")
def list_room_types(ctx: View, hostel_id: uuid.UUID) -> list[RoomTypeOut]:
    _hostel(ctx, hostel_id, HOSTEL_VIEW)
    rows = ctx.db.scalars(select(RoomType).where(RoomType.hostel_id == hostel_id).order_by(RoomType.name))
    return [RoomTypeOut.model_validate(r) for r in rows]


@router.post("/hostels/{hostel_id}/room-types", status_code=status.HTTP_201_CREATED)
def create_room_type(ctx: Manage, hostel_id: uuid.UUID, body: RoomTypeIn) -> RoomTypeOut:
    h = _hostel(ctx, hostel_id, HOSTEL_MANAGE)
    rt = RoomType(org_id=h.org_id, hostel_id=h.id, **body.model_dump())
    ctx.db.add(rt)
    ctx.db.flush()
    ctx.db.refresh(rt)
    audit.record(
        ctx.db, ctx.principal, "room_type.created", "room_type", rt.id, hostel_id=h.id, after=body.model_dump()
    )
    return RoomTypeOut.model_validate(rt)


@router.patch("/room-types/{room_type_id}")
def patch_room_type(ctx: Manage, room_type_id: uuid.UUID, body: RoomTypePatch) -> RoomTypeOut:
    rt = get_scoped(ctx.db, RoomType, room_type_id, ctx.principal, HOSTEL_MANAGE, lock=True)
    check_version(rt, body.version)
    changes = apply_patch(ctx.db, rt, body)
    ctx.db.flush()
    ctx.db.refresh(rt)
    audit.record(
        ctx.db,
        ctx.principal,
        "room_type.updated",
        "room_type",
        rt.id,
        hostel_id=rt.hostel_id,
        before={k: v[0] for k, v in changes.items()},
        after={k: v[1] for k, v in changes.items()},
    )
    return RoomTypeOut.model_validate(rt)


@router.get("/floors/{floor_id}/rooms")
def list_rooms(ctx: View, floor_id: uuid.UUID) -> list[RoomOut]:
    f = get_scoped(ctx.db, Floor, floor_id, ctx.principal, HOSTEL_VIEW)
    return [
        RoomOut.model_validate(r)
        for r in ctx.db.scalars(select(Room).where(Room.floor_id == f.id).order_by(Room.number))
    ]


def _room_type_in_hostel(ctx: Manage, room_type_id: uuid.UUID, hostel_id: uuid.UUID) -> RoomType:
    rt = ctx.db.get(RoomType, room_type_id)
    if rt is None or rt.hostel_id != hostel_id:  # another hostel's (or organization's) room type
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[
                {"field": "room_type_id", "code": "unknown_room_type", "message": "Choose a room type of this hostel"}
            ],
        )
    return rt


@router.post("/floors/{floor_id}/rooms", status_code=status.HTTP_201_CREATED)
def create_room(ctx: Manage, floor_id: uuid.UUID, body: RoomIn) -> RoomOut:
    f = get_scoped(ctx.db, Floor, floor_id, ctx.principal, HOSTEL_MANAGE)
    _room_type_in_hostel(ctx, body.room_type_id, f.hostel_id)
    r = Room(org_id=f.org_id, hostel_id=f.hostel_id, floor_id=f.id, **body.model_dump())
    ctx.db.add(r)
    ctx.db.flush()
    ctx.db.refresh(r)
    return RoomOut.model_validate(r)


@router.patch("/rooms/{room_id}")
def patch_room(ctx: Manage, room_id: uuid.UUID, body: RoomPatch) -> RoomOut:
    r = get_scoped(ctx.db, Room, room_id, ctx.principal, HOSTEL_MANAGE, lock=True)
    check_version(r, body.version)
    if body.room_type_id is not None:
        _room_type_in_hostel(ctx, body.room_type_id, r.hostel_id)
    changes = apply_patch(ctx.db, r, body)
    ctx.db.flush()
    ctx.db.refresh(r)
    audit.record(
        ctx.db,
        ctx.principal,
        "room.updated",
        "room",
        r.id,
        hostel_id=r.hostel_id,
        before={k: v[0] for k, v in changes.items()},
        after={k: v[1] for k, v in changes.items()},
    )
    return RoomOut.model_validate(r)


@router.get("/rooms/{room_id}/beds")
def list_beds(ctx: View, room_id: uuid.UUID) -> list[BedOut]:
    r = get_scoped(ctx.db, Room, room_id, ctx.principal, HOSTEL_VIEW)
    return [
        BedOut.model_validate(b) for b in ctx.db.scalars(select(Bed).where(Bed.room_id == r.id).order_by(Bed.label))
    ]


@router.post("/rooms/{room_id}/beds", status_code=status.HTTP_201_CREATED)
def create_bed(ctx: Manage, room_id: uuid.UUID, body: BedIn) -> BedOut:
    r = get_scoped(ctx.db, Room, room_id, ctx.principal, HOSTEL_MANAGE, lock=True)
    rt = ctx.db.get(RoomType, r.room_type_id)
    count = len(ctx.db.scalars(select(Bed.id).where(Bed.room_id == r.id)).all())
    if rt is not None and count >= rt.beds_per_room:
        raise ProblemError(
            409,
            "room_full",
            "This room already has as many beds as its room type allows",
            detail=f"{rt.name} rooms have {rt.beds_per_room} bed(s).",
        )
    b = Bed(org_id=r.org_id, hostel_id=r.hostel_id, room_id=r.id, label=body.label)
    ctx.db.add(b)
    ctx.db.flush()
    ctx.db.refresh(b)
    return BedOut.model_validate(b)


@router.patch("/beds/{bed_id}")
def patch_bed(ctx: Manage, bed_id: uuid.UUID, body: BedPatch) -> BedOut:
    b = get_scoped(ctx.db, Bed, bed_id, ctx.principal, HOSTEL_MANAGE, lock=True)
    check_version(b, body.version)
    changes = apply_patch(ctx.db, b, body)
    ctx.db.flush()
    ctx.db.refresh(b)
    audit.record(
        ctx.db,
        ctx.principal,
        "bed.updated",
        "bed",
        b.id,
        hostel_id=b.hostel_id,
        before={k: v[0] for k, v in changes.items()},
        after={k: v[1] for k, v in changes.items()},
    )
    return BedOut.model_validate(b)


# --- bed board (03 §9) ------------------------------------------------------------------------------------


class BoardOccupant(Out):
    """Who holds the bed: the active stay (occupied) or the reservation (reserved). Read-only (bed map)."""

    allocation_id: uuid.UUID
    allocation_status: str  # active | reserved
    student_id: uuid.UUID
    student_code: str
    student_name: str
    since: date  # the stay's (or reservation's) start date
    reservation_expires_at: datetime | None


class BoardBed(Out):
    id: uuid.UUID
    label: str
    state: str  # vacant | occupied | reserved | maintenance | inactive
    status: str  # the bed's own status: available | maintenance | inactive (the room's can also block it)
    version: int  # for PATCH /beds/{id} (back in service)
    occupant: BoardOccupant | None = None


class BoardRoom(Out):
    id: uuid.UUID
    number: str
    status: str
    room_type: str
    building_id: uuid.UUID
    building: str
    floor_id: uuid.UUID
    floor_level: int
    beds: list[BoardBed]


class BedBoard(Out):
    hostel_id: uuid.UUID
    counts: dict[str, int]
    rooms: list[BoardRoom]
    generated_at: datetime


@router.get("/hostels/{hostel_id}/bed-board")
def bed_board(ctx: Board, hostel_id: uuid.UUID) -> BedBoard:
    _hostel(ctx, hostel_id, BED_BOARD_VIEW)
    rows = ctx.db.execute(
        text(
            """
        SELECT r.id AS room_id, r.number, r.status, rt.name AS room_type, bu.id AS building_id, bu.name AS building,
               f.id AS floor_id, f.level, b.id AS bed_id, b.label, b.status AS bed_status, b.version AS bed_version,
               s.state, o.allocation_id, o.allocation_status, o.student_id, o.student_code, o.student_name, o.since,
               o.reservation_expires_at, app_now() AS at
        FROM rooms r
        JOIN floors f ON f.id = r.floor_id
        JOIN buildings bu ON bu.id = f.building_id
        JOIN room_types rt ON rt.id = r.room_type_id
        LEFT JOIN beds b ON b.room_id = r.id
        LEFT JOIN bed_states s ON s.bed_id = b.id
        -- The allocation behind an occupied or reserved bed (the state already says which kind exists).
        LEFT JOIN LATERAL (
          SELECT a.id AS allocation_id, a.status AS allocation_status, st.id AS student_id,
                 st.student_code, u.full_name AS student_name, a.start_date AS since, a.reservation_expires_at
          FROM bed_allocations a
          JOIN students st ON st.id = a.student_id
          JOIN users u ON u.id = st.user_id
          WHERE a.bed_id = b.id
            AND a.status = CASE s.state WHEN 'occupied' THEN 'active' WHEN 'reserved' THEN 'reserved' END
          ORDER BY a.start_date, a.id
          LIMIT 1
        ) o ON true
        WHERE r.hostel_id = :h
        ORDER BY bu.name, f.level, r.number, b.label
        """
        ),
        {"h": hostel_id},
    ).all()
    rooms: dict[uuid.UUID, BoardRoom] = {}
    counts: dict[str, int] = {}
    generated = datetime.now(UTC)  # an instant (time-zone aware); replaced by the database's time below
    for row in rows:
        generated = row.at
        room = rooms.setdefault(
            row.room_id,
            BoardRoom(
                id=row.room_id,
                number=row.number,
                status=row.status,
                room_type=row.room_type,
                building_id=row.building_id,
                building=row.building,
                floor_id=row.floor_id,
                floor_level=row.level,
                beds=[],
            ),
        )
        if row.bed_id is not None:
            occupant = (
                BoardOccupant(
                    allocation_id=row.allocation_id,
                    allocation_status=row.allocation_status,
                    student_id=row.student_id,
                    student_code=row.student_code,
                    student_name=row.student_name,
                    since=row.since,
                    reservation_expires_at=row.reservation_expires_at,
                )
                if row.allocation_id is not None
                else None
            )
            room.beds.append(
                BoardBed(
                    id=row.bed_id,
                    label=row.label,
                    state=row.state,
                    status=row.bed_status,
                    version=row.bed_version,
                    occupant=occupant,
                )
            )
            counts[row.state] = counts.get(row.state, 0) + 1
    return BedBoard(hostel_id=hostel_id, counts=counts, rooms=list(rooms.values()), generated_at=generated)


# --- CSV import (Q23) -------------------------------------------------------------------------------------


@router.post("/hostels/{hostel_id}/imports/rooms-beds")
def import_rooms_beds(ctx: Manage, hostel_id: uuid.UUID, file: UploadFile) -> imports.ImportResult:
    h = _hostel(ctx, hostel_id, HOSTEL_MANAGE, lock=True)
    raw = file.file.read(imports.MAX_BYTES + 1)  # never more than the limit + 1 byte into memory
    result = imports.import_rooms_beds(ctx.db, h, raw)
    audit.record(
        ctx.db, ctx.principal, "hostel.rooms_beds_imported", "hostel", h.id, hostel_id=h.id, after=result.created
    )
    return result
