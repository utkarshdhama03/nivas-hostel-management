"""The Administrator's home page: a few counts, no charts (D86; analytics was removed).

Every count runs inside the caller's organization (RLS) and the hostel scope the permission layer
allows (since D75 the whole organization). Bed states come from the bed_states view (03 §9).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.context import OrgContext, require, staff_only
from app.core.crud import allowed_hostels
from app.core.permissions import STUDENTS_VIEW
from app.core.schemas import Out

router = APIRouter(prefix="/admin", tags=["overview"], dependencies=[Depends(staff_only)])
View = Annotated[OrgContext, Depends(require(STUDENTS_VIEW))]


class OverviewOut(Out):
    students: int  # active students
    beds_free: int  # vacant beds (not occupied, reserved, under maintenance or inactive)
    beds_occupied: int
    registration_requests_pending: int  # D74: verified, waiting for a decision
    leave_pending: int
    complaints_open: int  # open or in progress


# :h is the caller's hostel list, or NULL for every hostel (the same filter every list applies).
OVERVIEW_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM students
        WHERE status = 'active' AND (CAST(:h AS uuid[]) IS NULL OR hostel_id = ANY(CAST(:h AS uuid[])))),
      (SELECT count(*) FROM bed_states
        WHERE state = 'vacant' AND (CAST(:h AS uuid[]) IS NULL OR hostel_id = ANY(CAST(:h AS uuid[])))),
      (SELECT count(*) FROM bed_states
        WHERE state = 'occupied' AND (CAST(:h AS uuid[]) IS NULL OR hostel_id = ANY(CAST(:h AS uuid[])))),
      (SELECT count(*) FROM student_registration_requests WHERE status = 'pending'),
      (SELECT count(*) FROM leave_requests
        WHERE status = 'pending' AND (CAST(:h AS uuid[]) IS NULL OR hostel_id = ANY(CAST(:h AS uuid[])))),
      (SELECT count(*) FROM complaints
        WHERE status IN ('open', 'in_progress') AND (CAST(:h AS uuid[]) IS NULL OR hostel_id = ANY(CAST(:h AS uuid[]))))
    """
)


@router.get("/overview")
def overview(ctx: View) -> OverviewOut:
    hostels = allowed_hostels(ctx.principal, STUDENTS_VIEW)
    params = {"h": list(hostels) if hostels is not None else None}
    row = ctx.db.execute(OVERVIEW_SQL, params).one()
    students, free, occupied, requests, leave, complaints = row
    return OverviewOut(
        students=students,
        beds_free=free,
        beds_occupied=occupied,
        registration_requests_pending=requests,
        leave_pending=leave,
        complaints_open=complaints,
    )
