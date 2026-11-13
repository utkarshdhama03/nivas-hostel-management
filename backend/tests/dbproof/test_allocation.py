"""No double booking, guaranteed by exclusion constraints (docs/02 B2, docs/03 §5.7).

The race tests are the important ones: two transactions run at the same instant.
Application code that checks "is the bed free?" and then inserts would let both
through. Only the database can make exactly one of them win.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest

from tests.conftest import Connect

from .helpers import EXCLUSION_VIOLATION, expect_db_error, ins, race, tenant_tx
from .world import World, add_student

pytestmark = pytest.mark.dbproof


def allocate_bed(
    cur: psycopg.Cursor[Any],
    w: World,
    bed: uuid.UUID,
    student: uuid.UUID,
    start: date,
    end: date | None = None,
    status: str = "active",
) -> uuid.UUID:
    extra: dict[str, Any] = (
        {"checked_in_at": datetime.now(UTC)}
        if status == "active"
        else {"reservation_expires_at": datetime.now(UTC) + timedelta(days=7)}
    )
    return ins(
        cur,
        "bed_allocations",
        org_id=w.org,
        hostel_id=w.hostel,
        bed_id=bed,
        student_id=student,  # type: ignore[no-any-return]
        academic_session_id=w.session,
        status=status,
        start_date=start,
        end_date=end,
        created_by=w.hm,
        **extra,
    )


def test_bed_overlap_rejected(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        newcomer = add_student(cur, fresh, "STU-0100")
    # Bed A is occupied by s1 from 2026-07-01, open-ended.
    with expect_db_error(EXCLUSION_VIOLATION, "bed_no_overlap"), tenant_tx(app_conn, fresh.org) as cur:
        allocate_bed(cur, fresh, fresh.bed_A, newcomer, date(2026, 12, 1))


def test_student_overlap_rejected(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(EXCLUSION_VIOLATION, "student_no_overlap"), tenant_tx(app_conn, fresh.org) as cur:
        allocate_bed(cur, fresh, fresh.bed_D, fresh.s1, date(2026, 12, 1))


def test_adjacent_stays_are_allowed(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    """[start, end) ranges: a stay ending on 1 Dec and one starting on 1 Dec don't overlap (transfers)."""
    with tenant_tx(app_conn, fresh.org) as cur:
        a, b = add_student(cur, fresh, "STU-0101"), add_student(cur, fresh, "STU-0102")
        allocate_bed(cur, fresh, fresh.bed_D, a, date(2026, 10, 1), end=date(2026, 12, 1))
        allocate_bed(cur, fresh, fresh.bed_D, b, date(2026, 12, 1))


def test_reservation_blocks_overlapping_allocation(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        a, b = add_student(cur, fresh, "STU-0103"), add_student(cur, fresh, "STU-0104")
        allocate_bed(cur, fresh, fresh.bed_D, a, date(2027, 1, 1), status="reserved")
    with expect_db_error(EXCLUSION_VIOLATION, "bed_no_overlap"), tenant_tx(app_conn, fresh.org) as cur:
        allocate_bed(cur, fresh, fresh.bed_D, b, date(2026, 12, 1))  # open-ended: runs into the reservation


def test_cancelled_allocation_does_not_block(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        a, b = add_student(cur, fresh, "STU-0105"), add_student(cur, fresh, "STU-0106")
        alloc = allocate_bed(cur, fresh, fresh.bed_D, a, date(2027, 1, 1), status="reserved")
        cur.execute("UPDATE bed_allocations SET status = 'cancelled', end_reason = 'cancelled' WHERE id = %s", (alloc,))
        allocate_bed(cur, fresh, fresh.bed_D, b, date(2027, 1, 1))


def test_overlapping_leave_rejected(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(EXCLUSION_VIOLATION, "leave_no_overlap"), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "leave_requests",
            org_id=fresh.org,
            hostel_id=fresh.hostel,
            student_id=fresh.s1,
            leave_type="home",
            reason="Again",
            starts_at=datetime(2026, 10, 24, tzinfo=UTC),
            ends_at=datetime(2026, 10, 27, tzinfo=UTC),
            requested_by=fresh.stu1,
        )


def test_overlapping_terms_rejected(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(EXCLUSION_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "academic_terms",
            org_id=fresh.org,
            academic_session_id=fresh.session,
            name="Overlap",
            starts_on=date(2026, 11, 1),
            ends_on=date(2027, 2, 28),
        )


DEADLOCK_DETECTED = "40P01"


def lock_then_allocate(
    cur: psycopg.Cursor[Any], w: World, bed: uuid.UUID, student: uuid.UUID, start: date
) -> uuid.UUID:
    """What the allocation service does (docs/02 B2): lock the student row, then the bed row, then insert.

    A fixed lock order (student, then bed) means two allocations can never each hold one lock while
    waiting for the other. The second transaction simply waits for the first to commit, then hits the
    exclusion constraint against a COMMITTED row, so it gets a clean 23P01 the API can map to a 409.
    """
    cur.execute("SELECT 1 FROM students WHERE id = %s FOR UPDATE", (student,))
    cur.execute("SELECT 1 FROM beds WHERE id = %s FOR UPDATE", (bed,))
    return allocate_bed(cur, w, bed, student, start)


def live_allocations(su: psycopg.Connection[Any], column: str, value: uuid.UUID) -> int:
    row = su.execute(
        f"SELECT count(*) FROM bed_allocations WHERE {column} = %s AND status IN ('reserved', 'active')", (value,)
    ).fetchone()
    assert row is not None
    return int(row[0])


# --- Database alone: exactly one wins, no matter how the loser is aborted --------------------------
# Two conflicting INSERTs at the same instant each see the other's uncommitted row in the exclusion
# index and wait for it; PostgreSQL detects the mutual wait and aborts one with 40P01 (deadlock)
# instead of 23P01. Either way the loser is rolled back and the guarantee holds; the row count proves it.


@pytest.mark.race
def test_bed_double_booking_race_database_alone(connect: Connect, fresh: World, su: psycopg.Connection[Any]) -> None:
    """Two managers assign the same free bed to two different students at the same instant."""
    app = connect("hms_app")
    with tenant_tx(app, fresh.org) as cur:
        a, b = add_student(cur, fresh, "STU-0200"), add_student(cur, fresh, "STU-0201")
    result = race(
        lambda: connect("hms_app"),
        fresh.org,
        lambda cur: allocate_bed(cur, fresh, fresh.bed_D, a, date(2026, 12, 1)),
        lambda cur: allocate_bed(cur, fresh, fresh.bed_D, b, date(2026, 12, 1)),
    )
    assert len(result.ok) == 1, result
    assert [e.sqlstate for e in result.errors] in ([EXCLUSION_VIOLATION], [DEADLOCK_DETECTED])
    assert live_allocations(su, "bed_id", fresh.bed_D) == 1


@pytest.mark.race
def test_student_double_allocation_race_database_alone(
    connect: Connect, fresh: World, su: psycopg.Connection[Any]
) -> None:
    """Two managers put the same student into two different free beds at the same instant."""
    app = connect("hms_app")
    with tenant_tx(app, fresh.org) as cur:
        student = add_student(cur, fresh, "STU-0202")
        bed_e = ins(cur, "beds", org_id=fresh.org, hostel_id=fresh.hostel, room_id=fresh.room, label="E")
    result = race(
        lambda: connect("hms_app"),
        fresh.org,
        lambda cur: allocate_bed(cur, fresh, fresh.bed_D, student, date(2026, 12, 1)),
        lambda cur: allocate_bed(cur, fresh, bed_e, student, date(2026, 12, 1)),
    )
    assert len(result.ok) == 1, result
    assert [e.sqlstate for e in result.errors] in ([EXCLUSION_VIOLATION], [DEADLOCK_DETECTED])
    assert live_allocations(su, "student_id", student) == 1


# --- With the service's row locks: the loser always gets the clean constraint error ----------------


@pytest.mark.race
def test_bed_double_booking_race_with_service_locks(
    connect: Connect, fresh: World, su: psycopg.Connection[Any]
) -> None:
    app = connect("hms_app")
    with tenant_tx(app, fresh.org) as cur:
        a, b = add_student(cur, fresh, "STU-0203"), add_student(cur, fresh, "STU-0204")
    result = race(
        lambda: connect("hms_app"),
        fresh.org,
        lambda cur: lock_then_allocate(cur, fresh, fresh.bed_D, a, date(2026, 12, 1)),
        lambda cur: lock_then_allocate(cur, fresh, fresh.bed_D, b, date(2026, 12, 1)),
    )
    assert len(result.ok) == 1, result
    assert [e.sqlstate for e in result.errors] == [EXCLUSION_VIOLATION]
    assert result.errors[0].diag.constraint_name == "bed_no_overlap"
    assert live_allocations(su, "bed_id", fresh.bed_D) == 1


@pytest.mark.race
def test_student_double_allocation_race_with_service_locks(
    connect: Connect, fresh: World, su: psycopg.Connection[Any]
) -> None:
    app = connect("hms_app")
    with tenant_tx(app, fresh.org) as cur:
        student = add_student(cur, fresh, "STU-0205")
        bed_e = ins(cur, "beds", org_id=fresh.org, hostel_id=fresh.hostel, room_id=fresh.room, label="E")
    result = race(
        lambda: connect("hms_app"),
        fresh.org,
        lambda cur: lock_then_allocate(cur, fresh, fresh.bed_D, student, date(2026, 12, 1)),
        lambda cur: lock_then_allocate(cur, fresh, bed_e, student, date(2026, 12, 1)),
    )
    assert len(result.ok) == 1, result
    assert [e.sqlstate for e in result.errors] == [EXCLUSION_VIOLATION]
    assert result.errors[0].diag.constraint_name == "student_no_overlap"
    assert live_allocations(su, "student_id", student) == 1
