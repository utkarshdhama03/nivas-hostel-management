"""Rooms-and-beds CSV import (Q23, docs/06 Phase 2).

    building,floor,room,room_type,bed
    A,1,101,Double,A
    A,1,101,Double,B

One row per bed. Buildings, floors and rooms are created when they don't exist yet; room
types must already exist (they carry the fee targeting, Q20). The whole file is validated
first and applied in the SAME transaction: either every row is imported or none is, and every
problem is reported with its row number (06 Phase 2: "nothing half-imported").

Runs inside the request, not as a background job (decision D11): job payloads may
only carry IDs, and there's no table to stage an uploaded CSV in. Files are capped at 1 MB /
2,000 rows, which a single transaction handles in well under a second.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ProblemError
from app.modules.hostels.models import Bed, Building, Floor, Hostel, Room, RoomType

MAX_BYTES = 1_000_000
MAX_ROWS = 2_000
COLUMNS = ["building", "floor", "room", "room_type", "bed"]


class ImportResult(BaseModel):
    rows: int
    created: dict[str, int]


@dataclass
class _Plan:
    rows: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def error(self, row: int, column: str, message: str) -> None:
        self.errors.append({"field": f"row {row}: {column}", "code": "invalid_row", "message": message})


def _parse(raw: bytes) -> _Plan:
    if len(raw) > MAX_BYTES:
        raise ProblemError(413, "file_too_large", "The CSV file is larger than 1 MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProblemError(422, "validation_error", "The file must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(content))
    if [c.strip().lower() for c in (reader.fieldnames or [])] != COLUMNS:
        raise ProblemError(
            422,
            "validation_error",
            "Invalid CSV header",
            errors=[
                {"field": "header", "code": "invalid_header", "message": "The first line must be: " + ",".join(COLUMNS)}
            ],
        )
    plan = _Plan()
    for line, raw_row in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
        if len(plan.rows) >= MAX_ROWS:
            raise ProblemError(422, "validation_error", f"At most {MAX_ROWS} rows per file")
        if not 1 <= len(row["building"]) <= 60:
            plan.error(line, "building", "1 to 60 characters")
        if not re.fullmatch(r"-?[0-9]{1,3}", row["floor"]) or not -5 <= int(row["floor"]) <= 200:
            plan.error(line, "floor", "a floor number between -5 and 200")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,10}", row["room"]):
            plan.error(line, "room", "1 to 10 letters, digits or hyphens")
        if not re.fullmatch(r"[A-Za-z0-9]{1,4}", row["bed"]):
            plan.error(line, "bed", "1 to 4 letters or digits")
        row["_line"] = str(line)
        plan.rows.append(row)
    if not plan.rows:
        raise ProblemError(422, "validation_error", "The CSV file has no rows")
    return plan


def import_rooms_beds(db: Session, hostel: Hostel, raw: bytes) -> ImportResult:
    plan = _parse(raw)
    types = {rt.name.lower(): rt for rt in db.scalars(select(RoomType).where(RoomType.hostel_id == hostel.id))}
    buildings = {b.name: b for b in db.scalars(select(Building).where(Building.hostel_id == hostel.id))}
    floors = {(f.building_id, f.level): f for f in db.scalars(select(Floor).where(Floor.hostel_id == hostel.id))}
    rooms = {(r.floor_id, r.number): r for r in db.scalars(select(Room).where(Room.hostel_id == hostel.id))}
    beds = {(b.room_id, b.label) for b in db.scalars(select(Bed).where(Bed.hostel_id == hostel.id))}
    bed_counts: dict[object, int] = {}
    for room_id, _ in beds:
        bed_counts[room_id] = bed_counts.get(room_id, 0) + 1

    seen: set[tuple[str, str, str, str]] = set()
    planned_per_room: dict[tuple[str, str, str], tuple[int, RoomType | None, int]] = {}
    for row in plan.rows:
        line = int(row["_line"])
        rt = types.get(row["room_type"].lower())
        if rt is None:
            plan.error(line, "room_type", f"no room type named {row['room_type']!r} in this hostel")
        key = (row["building"], row["floor"], row["room"], row["bed"].upper())
        if key in seen:
            plan.error(line, "bed", "the same bed appears twice in the file")
        seen.add(key)
        room_key = key[:3]
        count, first_rt, first_line = planned_per_room.get(room_key, (0, rt, line))
        if rt is not None and first_rt is not None and rt.id != first_rt.id:
            plan.error(line, "room_type", f"row {first_line} gives this room a different type")
        planned_per_room[room_key] = (count + 1, first_rt, first_line)
    if plan.errors:
        raise ProblemError(
            422, "validation_error", "Nothing was imported: fix these rows and upload again", errors=plan.errors
        )

    created = {"buildings": 0, "floors": 0, "rooms": 0, "beds": 0}
    for row in plan.rows:
        line = int(row["_line"])
        rt = types[row["room_type"].lower()]
        building = buildings.get(row["building"])
        if building is None:
            building = Building(org_id=hostel.org_id, hostel_id=hostel.id, name=row["building"])
            db.add(building)
            db.flush()
            buildings[building.name] = building
            created["buildings"] += 1
        level = int(row["floor"])
        floor = floors.get((building.id, level))
        if floor is None:
            floor = Floor(org_id=hostel.org_id, hostel_id=hostel.id, building_id=building.id, level=level)
            db.add(floor)
            db.flush()
            floors[(building.id, level)] = floor
            created["floors"] += 1
        room = rooms.get((floor.id, row["room"]))
        if room is None:
            room = Room(
                org_id=hostel.org_id, hostel_id=hostel.id, floor_id=floor.id, room_type_id=rt.id, number=row["room"]
            )
            db.add(room)
            db.flush()
            rooms[(floor.id, room.number)] = room
            created["rooms"] += 1
        elif room.room_type_id != rt.id:
            plan.error(line, "room_type", f"room {room.number} already exists with another room type")
            continue
        label = row["bed"].upper()
        if (room.id, label) in beds:
            plan.error(line, "bed", f"bed {label} already exists in room {room.number}")
            continue
        if bed_counts.get(room.id, 0) >= rt.beds_per_room:
            plan.error(line, "bed", f"room {room.number} would have more than {rt.beds_per_room} bed(s)")
            continue
        db.add(Bed(org_id=hostel.org_id, hostel_id=hostel.id, room_id=room.id, label=label))
        beds.add((room.id, label))
        bed_counts[room.id] = bed_counts.get(room.id, 0) + 1
        created["beds"] += 1
    if plan.errors:
        # Raising rolls back the whole transaction, including rows created above.
        raise ProblemError(
            422, "validation_error", "Nothing was imported: fix these rows and upload again", errors=plan.errors
        )
    db.flush()
    return ImportResult(rows=len(plan.rows), created=created)
