"""Bed allocation (docs/02 B2, 03 §5.7, §8; 01 ALC-1..6; Q19, Q21).

Who decides "is this bed free?": the DATABASE. Two exclusion constraints forbid overlapping
stays for one bed (bed_no_overlap) and for one student (student_no_overlap); a check in
Python could always race. What the service adds:

  Lock order (03 §8): the student row, then bed rows in ascending ID order, then
  the allocation row. With the locks taken, two managers giving the same bed at the same
  moment are SERIALIZED: the second waits, then hits the constraint against a committed row and
  gets a clean 409 bed_already_allocated (not a deadlock).

  Under the locks: the bed and its room must be available (not maintenance/inactive), the
  student must be active, and the student's gender must fit the hostel (Q19).

Dates are the organization's local dates; stays are half-open [start_date, end_date).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import audit
from app.core.crud import check_hostel, not_found
from app.core.errors import ProblemError
from app.core.permissions import ALLOCATIONS_MANAGE, Principal
from app.modules.allocations.models import BedAllocation
from app.modules.hostels.models import Bed, Room
from app.modules.students import service as students
from app.modules.students.models import Student

RESERVATION_DEFAULT = timedelta(days=7)  # decision D23


def local_today(db: Session) -> date:
    """The organization's date right now (Q48): stays are counted in local days."""
    value = db.scalar(
        text(
            "SELECT (app_now() AT TIME ZONE timezone)::date FROM organizations "
            "WHERE id = current_setting('app.org_id')::uuid"
        )
    )
    assert value is not None
    return value  # type: ignore[no-any-return]


def lock_student(db: Session, p: Principal, student_id: uuid.UUID) -> Student:
    """FIRST lock in every allocation operation."""
    return students.get_student(db, p, student_id, ALLOCATIONS_MANAGE, lock=True)


def lock_beds(db: Session, bed_ids: list[uuid.UUID]) -> dict[uuid.UUID, Bed]:
    """SECOND: beds, always in ascending ID order (a transfer locks two)."""
    beds: dict[uuid.UUID, Bed] = {}
    for bed_id in sorted(set(bed_ids)):
        bed = db.scalars(select(Bed).where(Bed.id == bed_id).with_for_update()).one_or_none()
        if bed is None:
            raise not_found()
        beds[bed_id] = bed
    return beds


def check_bed_usable(db: Session, p: Principal, bed: Bed, student: Student) -> None:
    check_hostel(p, ALLOCATIONS_MANAGE, bed.hostel_id)
    room = db.get(Room, bed.room_id)
    if bed.status != "available" or room is None or room.status != "available":
        raise ProblemError(409, "bed_unavailable", "This bed is under maintenance or inactive")
    if bed.hostel_id != student.hostel_id:
        # Inter-hostel moves aren't decided yet (ALC-6); the schema already allows them later.
        raise ProblemError(422, "different_hostel", "The bed is in another hostel than the student's")
    students.check_gender_fits_hostel(db, bed.hostel_id, student.gender)


def check_student_active(student: Student) -> None:
    if student.status != "active":
        raise ProblemError(409, "student_exited", "This student has exited")


# D79: students no longer get allocation notices; they linked to the removed My room page.


def allocate(
    db: Session,
    p: Principal,
    *,
    student_id: uuid.UUID,
    bed_id: uuid.UUID,
    mode: str,
    start_date: date | None,
    end_date: date | None,
    reservation_expires_at: datetime | None,
) -> BedAllocation:
    student = lock_student(db, p, student_id)
    bed = lock_beds(db, [bed_id])[bed_id]
    check_student_active(student)
    check_bed_usable(db, p, bed, student)
    today = local_today(db)
    start = start_date or today
    session_id = students.current_session(db).id
    now = datetime.now(UTC)
    if mode == "assign":
        if start > today:
            raise ProblemError(
                422,
                "validation_error",
                "A stay starting later is a reservation",
                errors=[{"field": "start_date", "code": "future_start", "message": "Use 'reserve' for a future start"}],
            )
        a = BedAllocation(status="active", checked_in_at=now)
    else:
        expires = reservation_expires_at or now + RESERVATION_DEFAULT
        if expires <= now:
            raise ProblemError(422, "validation_error", "The reservation must expire in the future")
        a = BedAllocation(status="reserved", reservation_expires_at=expires)
    if end_date is not None and end_date <= start:
        raise ProblemError(422, "validation_error", "The end date must be after the start date")
    a.org_id, a.hostel_id, a.bed_id, a.student_id = student.org_id, bed.hostel_id, bed.id, student.id
    a.academic_session_id, a.start_date, a.end_date, a.created_by = session_id, start, end_date, p.user_id
    db.add(a)
    db.flush()  # bed_no_overlap / student_no_overlap -> 409 (04 §7)
    audit.record(
        db,
        p,
        "allocation.created" if mode == "assign" else "allocation.reserved",
        "bed_allocation",
        a.id,
        hostel_id=a.hostel_id,
        after={
            "status": a.status,
            "bed_id": bed.id,
            "student_id": student.id,
            "start_date": start,
            "end_date": end_date,
        },
    )
    return a


def load_for_update(
    db: Session, p: Principal, allocation_id: uuid.UUID, extra_beds: list[uuid.UUID] | None = None
) -> tuple[BedAllocation, Student, dict[uuid.UUID, Bed]]:
    """Read the allocation (no lock) to learn its student and bed, then lock in the fixed order
    (student, beds ascending, allocation) and re-read it."""
    first = db.get(BedAllocation, allocation_id)
    if first is None:
        raise not_found()
    check_hostel(p, ALLOCATIONS_MANAGE, first.hostel_id)
    student = lock_student(db, p, first.student_id)
    beds = lock_beds(db, [first.bed_id, *(extra_beds or [])])
    a = db.scalars(
        select(BedAllocation)
        .where(BedAllocation.id == allocation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    return a, student, beds


def _wrong_status(a: BedAllocation, action: str) -> ProblemError:
    return ProblemError(409, "allocation_state", f"Can't {action} an allocation that is {a.status}")


def check_in(db: Session, p: Principal, allocation_id: uuid.UUID) -> BedAllocation:
    a, student, beds = load_for_update(db, p, allocation_id)
    if a.status != "reserved":
        raise _wrong_status(a, "check in")
    check_student_active(student)
    check_bed_usable(db, p, beds[a.bed_id], student)
    today = local_today(db)
    a.status, a.checked_in_at, a.reservation_expires_at = "active", datetime.now(UTC), a.reservation_expires_at
    a.start_date = today  # the stay starts on the real arrival day; the constraint re-checks overlaps
    if a.end_date is not None and a.end_date <= today:
        a.end_date = None
    db.flush()
    audit.record(
        db,
        p,
        "allocation.checked_in",
        "bed_allocation",
        a.id,
        hostel_id=a.hostel_id,
        after={"status": "active", "start_date": today},
    )
    return a


def check_out(db: Session, p: Principal, allocation_id: uuid.UUID) -> BedAllocation:
    """Stays are [start, end): checking out on day D frees the bed from D on. Leaving on the
    arrival day leaves a zero-length stay, which is recorded as cancelled (end_reason 'checkout')
    so the bed is free at once and the bed board agrees (decision D24)."""
    a, student, _ = load_for_update(db, p, allocation_id)
    if a.status != "active":
        raise _wrong_status(a, "check out")
    today = local_today(db)
    if a.start_date >= today:
        a.status, a.end_reason = "cancelled", "checkout"
    else:
        a.status, a.end_reason, a.checked_out_at, a.end_date = "ended", "checkout", datetime.now(UTC), today
    db.flush()
    audit.record(
        db,
        p,
        "allocation.checked_out",
        "bed_allocation",
        a.id,
        hostel_id=a.hostel_id,
        after={"status": a.status, "end_date": a.end_date},
    )
    return a


def cancel(db: Session, p: Principal, allocation_id: uuid.UUID) -> BedAllocation:
    a, student, _ = load_for_update(db, p, allocation_id)
    if a.status != "reserved":
        raise _wrong_status(a, "cancel")
    a.status, a.end_reason = "cancelled", "cancelled"
    db.flush()
    audit.record(
        db, p, "allocation.cancelled", "bed_allocation", a.id, hostel_id=a.hostel_id, after={"status": "cancelled"}
    )
    return a


def transfer(db: Session, p: Principal, allocation_id: uuid.UUID, new_bed_id: uuid.UUID) -> BedAllocation:
    """ALC-4: end the current stay and start the new one in ONE transaction. Both beds are locked
    in ascending ID order, whichever is old or new (03 §8)."""
    a, student, beds = load_for_update(db, p, allocation_id, [new_bed_id])
    if a.status != "active":
        raise _wrong_status(a, "transfer")
    if new_bed_id == a.bed_id:
        raise ProblemError(422, "validation_error", "Choose a different bed")
    check_bed_usable(db, p, beds[new_bed_id], student)
    today = local_today(db)
    if a.start_date >= today:
        # Moved on the day they arrived: the first stay never really began.
        a.status, a.end_reason = "cancelled", "transfer"
    else:
        a.status, a.end_reason, a.checked_out_at, a.end_date = "ended", "transfer", datetime.now(UTC), today
    db.flush()
    new = BedAllocation(
        org_id=a.org_id,
        hostel_id=beds[new_bed_id].hostel_id,
        bed_id=new_bed_id,
        student_id=student.id,
        academic_session_id=a.academic_session_id,
        status="active",
        start_date=today,
        checked_in_at=datetime.now(UTC),
        previous_allocation_id=a.id,
        created_by=p.user_id,
    )
    db.add(new)
    db.flush()
    audit.record(
        db,
        p,
        "allocation.transferred",
        "bed_allocation",
        new.id,
        hostel_id=new.hostel_id,
        before={"bed_id": a.bed_id},
        after={"bed_id": new_bed_id, "previous_allocation_id": a.id},
    )
    return new


def expire_reservations(db: Session) -> int:
    """Worker, per organization (Q21): reservations past their expiry release the bed."""
    candidates = db.execute(
        select(BedAllocation.id, BedAllocation.student_id, BedAllocation.bed_id).where(
            BedAllocation.status == "reserved", BedAllocation.reservation_expires_at < datetime.now(UTC)
        )
    ).all()
    rows: list[BedAllocation] = []
    for allocation_id, student_id, bed_id in candidates:
        # The fixed lock order (student, bed, allocation; 03 §8), then re-read: a manager may have
        # checked the student in since the list above was read. Without this the job overwrote that
        # check-in with "cancelled" while the screen had said it worked (review Should fix 11).
        db.execute(select(Student.id).where(Student.id == student_id).with_for_update())
        db.execute(select(Bed.id).where(Bed.id == bed_id).with_for_update())
        a = db.scalars(
            select(BedAllocation)
            .where(BedAllocation.id == allocation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        if a.status != "reserved" or a.reservation_expires_at is None or a.reservation_expires_at >= datetime.now(UTC):
            continue
        rows.append(a)
        a.status, a.end_reason = "cancelled", "reservation_expired"
        db.flush()
        audit.record(
            db,
            audit.SYSTEM,
            "allocation.reservation_expired",
            "bed_allocation",
            a.id,
            org_id=a.org_id,
            hostel_id=a.hostel_id,
        )
    return len(rows)
