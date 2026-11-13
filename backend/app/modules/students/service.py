"""Student rules (docs/04 §12.7, Q19, Q22, Q39, Q44, Q46, Q62).

Since D75 the only staff account is the Administrator, who sees the full record (05 RBAC-8 retired).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core import audit
from app.core.crud import check_hostel, not_found
from app.core.errors import ProblemError
from app.core.permissions import STUDENTS_VIEW, Principal
from app.modules.files.models import File
from app.modules.files.service import unattached_clause
from app.modules.identity.models import User
from app.modules.organizations.models import AcademicSession, OrgCounter
from app.modules.students.models import Student, StudentGuardian

READ_ONLY_DAYS = 90  # Q22


def get_student(
    db: Session, p: Principal, student_id: uuid.UUID, permission: str = STUDENTS_VIEW, *, lock: bool = False
) -> Student:
    """Lock order (03 §8): the student row is always the FIRST row locked."""
    q = select(Student).where(Student.id == student_id)
    s = db.scalars(q.with_for_update() if lock else q).one_or_none()
    if s is None:
        raise not_found()
    check_hostel(p, permission, s.hostel_id)
    return s


def current_session(db: Session) -> AcademicSession:
    session = db.scalars(select(AcademicSession).where(AcademicSession.is_current)).first()
    if session is None:
        raise ProblemError(
            409,
            "no_current_session",
            "Set a current academic session first",
            detail="Organization > Academic sessions > Make current.",
        )
    return session


def next_student_code(db: Session, org_id: uuid.UUID, session: AcademicSession) -> str:
    """STU-<admission year>-<0001>, gap-free per year (org_counters, updated in this transaction)."""
    scope = f"{session.starts_on.year:04d}"
    value = db.execute(
        update(OrgCounter)
        .where(
            OrgCounter.org_id == org_id,
            OrgCounter.name == "student_code",
            OrgCounter.scope_key == scope,
        )
        .values(last_value=OrgCounter.last_value + 1)
        .returning(OrgCounter.last_value)
    ).scalar_one_or_none()
    if value is None:
        db.add(OrgCounter(org_id=org_id, name="student_code", scope_key=scope, last_value=1))
        db.flush()
        value = 1
    return f"STU-{scope}-{value:04d}"


def check_student_email_free(db: Session, email: str) -> None:
    """D80: a student email is unique across the platform (the database refuses it too)."""
    if db.scalar(text("SELECT student_email_taken(:e)"), {"e": email}):
        raise ProblemError(409, "student_email_taken", "This email already belongs to a student")


def check_gender_fits_hostel(db: Session, hostel_id: uuid.UUID, gender: str) -> None:
    """Q19: a boys' hostel takes male students, a girls' hostel female students; co-ed or unset
    takes anyone (decision D17: checked at admission too, not only at allocation)."""
    resident = db.scalar(text("SELECT resident_type FROM hostels WHERE id = :h"), {"h": hostel_id})
    if (resident == "boys" and gender != "male") or (resident == "girls" and gender != "female"):
        raise ProblemError(
            422,
            "gender_hostel_mismatch",
            "This hostel doesn't take this student",
            detail=f"The hostel is for {resident}.",
        )


def claim_photo(db: Session, p: Principal, file_id: uuid.UUID) -> File:
    """A new photo must be an unattached student_photo uploaded in this organization (RLS)."""
    f = db.scalars(select(File).where(File.id == file_id, File.deleted_at.is_(None), unattached_clause())).first()
    if f is None or f.category != "student_photo":
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "photo_file_id", "code": "invalid_photo", "message": "Upload a student photo first"}],
        )
    return f


def admit(
    db: Session,
    p: Principal,
    *,
    session: AcademicSession,
    full_name: str,
    email: str,
    phone: str | None,
    student_fields: dict[str, object],
    guardians: list[dict[str, object]],
    password_hash: str | None = None,
) -> tuple[User, Student]:
    """THE admission: the office's form (students.router) and an approved registration request (D74)
    both come here. The user, the student with the next Student ID, the guardians, one audit row.
    Without a password the account waits for activation; with one (from a request) it is active."""
    user = User(
        org_id=p.org_id,
        kind="student",
        full_name=full_name,
        email=email,
        phone=phone,
        status="active" if password_hash else "pending_activation",
        password_hash=password_hash,
        created_by=p.user_id,
    )
    db.add(user)
    db.flush()
    s = Student(
        org_id=p.org_id,
        user_id=user.id,
        admission_session_id=session.id,
        created_by=p.user_id,
        student_code=next_student_code(db, p.org_id, session),
        **student_fields,
    )
    db.add(s)
    db.flush()
    for g in guardians:
        db.add(StudentGuardian(org_id=p.org_id, student_id=s.id, **g))
    db.flush()
    audit.record(
        db,
        p,
        "student.admitted",
        "student",
        s.id,
        hostel_id=s.hostel_id,
        after={"student_code": s.student_code, "hostel_id": s.hostel_id, "full_name": user.full_name},
    )
    return user, s


def has_open_allocation(db: Session, student_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM bed_allocations WHERE student_id = :s "
                "AND status IN ('reserved', 'active'))"
            ),
            {"s": student_id},
        )
    )


def read_only_until(exited_on: date) -> date:
    return exited_on + timedelta(days=READ_ONLY_DAYS)


def end_read_only_windows(db: Session) -> int:
    """Worker, per organization: Q22's 90-day read-only window is over -> deactivate."""
    rows = db.execute(
        text(
            "UPDATE users SET status = 'deactivated', read_only_until = NULL, updated_at = now() "
            # users.updated_at is a security timestamp (real now()); the exit window is a business date.
            "WHERE status = 'read_only' AND read_only_until < (app_now() AT TIME ZONE "
            "(SELECT timezone FROM organizations WHERE id = users.org_id))::date RETURNING id"
        )
    ).all()
    for (user_id,) in rows:
        db.execute(
            text(
                "UPDATE sessions SET revoked_at = now(), revoke_reason = 'deactivated' "
                "WHERE user_id = :u AND revoked_at IS NULL"
            ),
            {"u": user_id},
        )
    return len(rows)
