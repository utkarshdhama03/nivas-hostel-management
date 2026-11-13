"""Leave (docs/04 §12.11, Q35; D75).

A student asks for leave (home or local outing); an Administrator approves or rejects it. A local outing
is approved automatically when the hostel says so (Q35). An Administrator may record emergency leave for
a student; it is approved at once. Since D75 there are no gate passes: approved leave is a record the
student and the office can see, nothing more.

Lock order (03 §8): the student row first, then the leave request.

Also home to small helpers other modules share (the organization's time zone, the signed-in student,
the organization's Administrators), which used to live in the removed gate module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import audit
from app.core.crud import check_hostel, not_found
from app.core.errors import ProblemError
from app.core.permissions import LEAVE_MANAGE, SUPER_ADMIN, Principal
from app.modules.hostels.models import Hostel
from app.modules.leave.models import LeaveRequest
from app.modules.notifications import service as notifications
from app.modules.students import service as students
from app.modules.students.models import Student

HOME_LEAVE_MAX = timedelta(days=90)  # decision D38


# --- shared helpers ----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def org_zone(db: Session) -> ZoneInfo:
    name = db.scalar(text("SELECT timezone FROM organizations WHERE id = current_setting('app.org_id')::uuid"))
    return ZoneInfo(name or "Asia/Kolkata")


def administrators_of(db: Session) -> list[uuid.UUID]:
    """The organization's active Administrators (RLS scopes the query to it), in a stable order."""
    rows = db.scalars(
        text(
            "SELECT DISTINCT ra.user_id FROM role_assignments ra JOIN users u ON u.id = ra.user_id "
            "WHERE ra.role = :r AND ra.revoked_at IS NULL AND u.status = 'active' ORDER BY ra.user_id"
        ),
        {"r": SUPER_ADMIN},
    )
    return list(rows)


def notify_administrators(db: Session, org_id: uuid.UUID, *, category: str, title: str, body: str, link: str) -> None:
    for user_id in administrators_of(db):
        notifications.notify(db, org_id=org_id, user_id=user_id, category=category, title=title, body=body, link=link)


def _notify_student(db: Session, s: Student, title: str, body: str) -> None:
    notifications.notify(
        db, org_id=s.org_id, user_id=s.user_id, category="leave", title=title, body=body, link="/portal/leave"
    )


def student_of_user(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> Student:
    q = select(Student).where(Student.user_id == user_id)
    s = db.scalars(q.with_for_update() if lock else q).one_or_none()
    if s is None:
        raise not_found()
    return s


def _check_active(s: Student) -> None:
    if s.status != "active":
        raise ProblemError(409, "student_exited", "This student has exited")


def _validate_window(db: Session, leave_type: str, starts_at: datetime, ends_at: datetime) -> None:
    if ends_at <= starts_at:
        raise ProblemError(422, "validation_error", "The leave must end after it starts")
    if ends_at <= _now():
        raise ProblemError(422, "validation_error", "The leave has already ended")
    if leave_type == "local_outing":
        zone = org_zone(db)
        if starts_at.astimezone(zone).date() != ends_at.astimezone(zone).date():
            raise ProblemError(422, "validation_error", "A local outing starts and ends on the same day (Q35)")
    elif ends_at - starts_at > HOME_LEAVE_MAX:
        raise ProblemError(422, "validation_error", "A leave can last at most 90 days")


# --- leave -------------------------------------------------------------------------------------


def request_leave(
    db: Session,
    p: Principal,
    *,
    leave_type: str,
    reason: str,
    destination: str | None,
    starts_at: datetime,
    ends_at: datetime,
) -> LeaveRequest:
    """A student's own request. Local outings are auto-approved where the hostel allows (Q35)."""
    s = student_of_user(db, p.user_id, lock=True)
    _check_active(s)
    _validate_window(db, leave_type, starts_at, ends_at)
    hostel = db.get(Hostel, s.hostel_id)
    assert hostel is not None
    auto = leave_type == "local_outing" and not hostel.local_outing_requires_approval
    leave = LeaveRequest(
        org_id=s.org_id,
        hostel_id=s.hostel_id,
        student_id=s.id,
        leave_type=leave_type,
        reason=reason,
        destination=destination,
        starts_at=starts_at,
        ends_at=ends_at,
        requested_by=p.user_id,
        status="approved" if auto else "pending",
        auto_approved=auto,
    )
    db.add(leave)
    db.flush()
    audit.record(
        db,
        p,
        "leave.requested",
        "leave_request",
        leave.id,
        org_id=s.org_id,
        hostel_id=s.hostel_id,
        after={"leave_type": leave_type, "status": leave.status},
    )
    if not auto:
        notify_administrators(
            db,
            s.org_id,
            category="leave",
            title="New leave request",
            body=f"Student {s.student_code} asked for {leave_type.replace('_', ' ')} leave.",
            link="/admin/leave",
        )
    return leave


def create_emergency_leave(
    db: Session,
    p: Principal,
    *,
    student_id: uuid.UUID,
    reason: str,
    destination: str | None,
    starts_at: datetime,
    ends_at: datetime,
) -> LeaveRequest:
    """An Administrator records emergency leave on the student's behalf; approved at once (Q35)."""
    s = students.get_student(db, p, student_id, LEAVE_MANAGE, lock=True)
    _check_active(s)
    _validate_window(db, "emergency", starts_at, ends_at)
    leave = LeaveRequest(
        org_id=s.org_id,
        hostel_id=s.hostel_id,
        student_id=s.id,
        leave_type="emergency",
        reason=reason,
        destination=destination,
        starts_at=starts_at,
        ends_at=ends_at,
        requested_by=p.user_id,
        status="approved",
        decided_by=p.user_id,
        decided_at=_now(),
    )
    db.add(leave)
    db.flush()
    audit.record(db, p, "leave.emergency_created", "leave_request", leave.id, org_id=s.org_id, hostel_id=s.hostel_id)
    _notify_student(db, s, "Emergency leave approved", "The hostel office recorded emergency leave for you.")
    return leave


def _load_leave(
    db: Session, leave_id: uuid.UUID, check: Callable[[LeaveRequest], None]
) -> tuple[Student, LeaveRequest]:
    """Authorize first (`check` raises 404 for a leave the caller may not touch), then lock in
    the fixed order: the student row, then the leave."""
    leave = db.get(LeaveRequest, leave_id)
    if leave is None:
        raise not_found()
    check(leave)
    s = db.scalars(select(Student).where(Student.id == leave.student_id).with_for_update()).one()
    db.refresh(leave, with_for_update=True)
    return s, leave


def decide_leave(db: Session, p: Principal, leave_id: uuid.UUID, *, approve: bool, note: str | None) -> LeaveRequest:
    s, leave = _load_leave(db, leave_id, lambda lv: check_hostel(p, LEAVE_MANAGE, lv.hostel_id))
    if leave.status != "pending":
        raise ProblemError(409, "leave_not_pending", f"This leave is already {leave.status}")
    if not approve and not (note and note.strip()):
        raise ProblemError(422, "decision_note_required", "Give a reason for the rejection")
    if approve and leave.ends_at <= _now():
        raise ProblemError(409, "leave_already_ended", "This leave has already ended")
    leave.status = "approved" if approve else "rejected"
    leave.decided_by, leave.decided_at, leave.decision_note = p.user_id, _now(), note
    db.flush()
    audit.record(
        db,
        p,
        "leave.approved" if approve else "leave.rejected",
        "leave_request",
        leave.id,
        org_id=leave.org_id,
        hostel_id=leave.hostel_id,
        before={"status": "pending"},
        after={"status": leave.status},
    )
    _notify_student(
        db,
        s,
        "Leave approved" if approve else "Leave rejected",
        "Your leave is approved." if approve else f"Reason: {note}",
    )
    return leave


def cancel_leave(db: Session, p: Principal, leave_id: uuid.UUID) -> LeaveRequest:
    """The student's own pending or approved leave, until it has ended."""
    me = student_of_user(db, p.user_id)

    def mine(lv: LeaveRequest) -> None:
        if lv.student_id != me.id:
            raise not_found()

    s, leave = _load_leave(db, leave_id, mine)
    if leave.status not in ("pending", "approved"):
        raise ProblemError(409, "leave_not_cancellable", f"This leave is already {leave.status}")
    if leave.ends_at <= _now():
        raise ProblemError(409, "leave_not_cancellable", "This leave has already ended")
    leave.status = "cancelled"
    db.flush()
    audit.record(db, p, "leave.cancelled", "leave_request", leave.id, org_id=s.org_id, hostel_id=s.hostel_id)
    return leave
