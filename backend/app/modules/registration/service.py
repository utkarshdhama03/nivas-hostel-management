"""Student registration requests (D74, updated 11 November; docs/04 §12.7a).

A student asks to join an organization that accepts requests; the Administrator approves or rejects.

  submit (public form, multipart with the photo) -> 'unverified' + an email-verification link
  verify (the link, 24 hours)                     -> 'pending'   + an in-app notice to the Administrator
  approve                                         -> 'approved'  : the SAME admission code as the office's
                                                                   admission (students.service.admit) makes
                                                                   the user (with the request's password),
                                                                   the student (with a Student ID), the
                                                                   guardian and the photo file
  reject (reason 5-500 characters)                -> 'rejected'

No status page (the 11 November update): the student finds out on Student sign-in. With the email
and the CORRECT password of the request, sign-in says "waiting for approval" or "rejected: <reason>"
(identity.service). With a wrong password it says what any wrong password says, so nobody can
check another person's request.

Spam protection without a CAPTCHA: 5 requests an hour per IP, 50 a day per organization, one open
request per email (database index), a honeypot field, unverified requests deleted after 24 hours.
The answer to the form is the same whether or not the email is already known: if it is, the email
says so instead of the person being told on screen.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import BinaryIO

from PIL import Image
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.core import audit, jobs, passwords, ratelimit, request_state
from app.core.context import OrgContext
from app.core.crud import check_hostel, not_found
from app.core.db import set_tenant, transaction
from app.core.errors import ProblemError
from app.core.permissions import STUDENTS_MANAGE
from app.modules.allocations.service import local_today
from app.modules.files import service as files
from app.modules.hostels.models import Hostel
from app.modules.identity import sessions
from app.modules.identity.models import AuthToken, User
from app.modules.leave.service import notify_administrators
from app.modules.organizations.models import Organization, OrganizationProfile
from app.modules.registration.models import StudentRegistrationRequest as Request
from app.modules.students import service as students
from app.modules.students.models import Student

REQUESTS_PER_ORG_PER_DAY = 50
UNVERIFIED_KEPT = timedelta(hours=24)
REJECTED_KEPT = timedelta(days=30)
PHOTO_TYPES = {"image/jpeg", "image/png"}
PHOTO_SIDE = 800  # pixels, longest side; stored as JPEG (about 50-200 KB)
OPEN = ("unverified", "pending")


def too_many(retry_after: int) -> ProblemError:
    return ProblemError(
        429,
        "too_many_attempts",
        "Too many attempts. Try again later.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def not_accepting() -> ProblemError:
    """The same answer for an organization that doesn't exist and one that doesn't take requests."""
    return ProblemError(
        404,
        "not_accepting_requests",
        "This organization doesn't take registration requests",
        detail="Check the organization sign-in name, or ask your hostel office.",
    )


def _field_error(field: str, code: str, message: str) -> ProblemError:
    return ProblemError(
        422, "validation_error", "Invalid request", errors=[{"field": field, "code": code, "message": message}]
    )


def _accepting_org(db: Session, slug: str) -> Organization | None:
    org = db.scalars(select(Organization).where(Organization.slug == slug.strip().lower())).one_or_none()
    if org is None or org.status != "active":
        return None
    set_tenant(db, org.id)
    profile = db.get(OrganizationProfile, org.id)
    return org if profile is not None and profile.accepts_student_requests else None


# --- public ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HostelChoice:
    id: uuid.UUID
    name: str
    resident_type: str | None


@dataclass(frozen=True)
class RegistrationInfo:
    accepts_requests: bool
    organization_name: str | None
    hostels: list[HostelChoice]


def registration_info(slug: str) -> RegistrationInfo:
    """Whether the form can be shown. An unknown organization looks like one that doesn't accept."""
    with transaction(None) as db:
        org = _accepting_org(db, slug)
        if org is None:
            return RegistrationInfo(False, None, [])
        hostels = db.scalars(select(Hostel).where(Hostel.is_active).order_by(Hostel.name)).all()
        return RegistrationInfo(True, org.name, [HostelChoice(h.id, h.name, h.resident_type) for h in hostels])


@dataclass(frozen=True)
class RequestForm:
    hostel_id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    gender: str
    date_of_birth: date
    program: str | None
    year_of_study: int | None
    guardian_name: str
    guardian_relationship: str
    guardian_phone: str
    password: str


def read_photo(stream: BinaryIO) -> bytes:
    """Checked like every upload (size while reading, the real type, re-encoded so metadata is
    dropped), JPEG or PNG only, then made small: at most 800 px, stored as JPEG."""
    data = files.read_limited(stream, files.PHOTO_MAX_BYTES)
    mime = files.sniff(data)
    if mime not in PHOTO_TYPES:
        raise files.unsupported("The photo must be a JPEG or PNG image")
    clean = files.reencode_image(data, mime)
    with Image.open(io.BytesIO(clean)) as img:
        img.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=85)
    return out.getvalue()


def submit(slug: str, form: RequestForm, photo: BinaryIO, honeypot: str | None) -> None:
    ip = request_state.client_ip.get() or "unknown"
    wait = ratelimit.STUDENT_REQUESTS_PER_IP.take(ip)
    if wait:
        raise too_many(wait)
    if honeypot:
        return  # a field people never see was filled in: a bot. Same answer, nothing stored.
    with transaction(None) as db:
        org = _accepting_org(db, slug)
        if org is None:
            raise not_accepting()
        today = db.scalar(
            select(func.count()).select_from(Request).where(Request.created_at > func.now() - timedelta(days=1))
        )
        if (today or 0) >= REQUESTS_PER_ORG_PER_DAY:
            raise too_many(3600)
        hostel = db.get(Hostel, form.hostel_id)
        if hostel is None or not hostel.is_active:
            raise _field_error("hostel_id", "unknown_hostel", "Choose one of the hostels")
        students.check_gender_fits_hostel(db, hostel.id, form.gender)
        if form.date_of_birth >= local_today(db):
            raise _field_error("date_of_birth", "not_in_past", "The date of birth must be in the past")
        email = form.email.strip()
        problems = passwords.check_new_password(
            form.password,
            context=(*form.full_name.split(), email.split("@")[0], org.slug, *org.name.split()),
        )
        if problems:
            raise ProblemError(
                422,
                "validation_error",
                "Choose a different password",
                errors=[{"field": "password", "code": p.code, "message": p.message} for p in problems],
            )
        picture = read_photo(photo)

        # D80: an email any student already uses, in any organization, is refused (rate-limited above).
        students.check_student_email_free(db, email)
        # Already known here? The answer on screen stays the same; the email tells the owner of the address.
        known_user = db.scalars(select(User.id).where(func.lower(User.email) == email.lower())).first()
        open_request = db.scalars(
            select(Request.id).where(func.lower(Request.email) == email.lower(), Request.status.in_(OPEN))
        ).first()
        if known_user is not None or open_request is not None:
            payload = {"user_id": str(known_user)} if known_user else {"request_id": str(open_request)}
            jobs.enqueue(db, "email.student_request_duplicate", payload, org_id=org.id)
            return

        row = Request(
            org_id=org.id,
            hostel_id=hostel.id,
            full_name=form.full_name.strip(),
            email=email,
            phone=form.phone,
            gender=form.gender,
            date_of_birth=form.date_of_birth,
            program=form.program,
            year_of_study=form.year_of_study,
            guardian_name=form.guardian_name.strip(),
            guardian_relationship=form.guardian_relationship,
            guardian_phone=form.guardian_phone,
            password_hash=passwords.hash_password(form.password),
            photo=picture,
            photo_mime="image/jpeg",
        )
        db.add(row)
        db.flush()  # a second open request for the email, at the same moment, fails here (database index)
        audit.record(
            db, audit.SYSTEM, "student_request.submitted", "student_request", row.id, org_id=org.id, hostel_id=hostel.id
        )
        jobs.enqueue(
            db,
            "email.student_request_verify",
            {"request_id": str(row.id)},
            org_id=org.id,
            priority=jobs.PRIORITY_SECURITY,
        )


def _live_request_token(db: Session, raw: str) -> AuthToken | None:
    from app.core import tokens

    return db.scalars(
        select(AuthToken)
        .where(
            AuthToken.token_hash == tokens.hash_secret(raw),
            AuthToken.purpose == "student_request",
            AuthToken.used_at.is_(None),
            AuthToken.revoked_at.is_(None),
            AuthToken.expires_at > func.now(),
        )
        .with_for_update()
    ).one_or_none()


def verify(slug: str, raw_token: str) -> None:
    """The emailed link: the request becomes 'pending' and the Administrator is told in the app."""
    expired = ProblemError(410, "link_expired", "This link has expired or was already used")
    with transaction(None) as db:
        org = db.scalars(select(Organization).where(Organization.slug == slug.strip().lower())).one_or_none()
        if org is None or org.status != "active":
            raise expired
        set_tenant(db, org.id)
        token = _live_request_token(db, raw_token)
        row = (
            db.scalars(select(Request).where(Request.id == token.registration_request_id).with_for_update()).first()
            if token is not None
            else None
        )
        if token is None or row is None or row.status != "unverified":
            raise expired
        token.used_at = sessions.now()
        row.status = "pending"
        row.email_verified_at = sessions.now()
        db.flush()
        audit.record(
            db,
            audit.SYSTEM,
            "student_request.verified",
            "student_request",
            row.id,
            org_id=org.id,
            hostel_id=row.hostel_id,
        )
        hostel = db.get(Hostel, row.hostel_id)
        notify_administrators(
            db,
            org.id,
            category="admission",
            title="New student registration request",
            body=f"{row.full_name} asks to join {hostel.name if hostel else 'a hostel'}. Approve or reject it.",
            link="/admin/registration-requests",
        )


# --- the Administrator ---------------------------------------------------------------------------------


def get_request(ctx: OrgContext, request_id: uuid.UUID, *, lock: bool = False) -> Request:
    q = select(Request).where(Request.id == request_id, Request.status != "unverified")
    row = ctx.db.scalars(q.with_for_update() if lock else q).one_or_none()
    if row is None:
        raise not_found()
    check_hostel(ctx.principal, STUDENTS_MANAGE, row.hostel_id)
    return row


def _not_pending(row: Request) -> ProblemError:
    return ProblemError(
        409, "request_already_decided", "This request was already decided", detail=f"It is {row.status}."
    )


def approve(ctx: OrgContext, request_id: uuid.UUID) -> Student:
    """One transaction, the office's own admission code: user (with the request's password, the
    email already verified, active), student with a generated Student ID, guardian, photo file."""
    db, p = ctx.db, ctx.principal
    row = get_request(ctx, request_id, lock=True)
    if row.status != "pending":
        raise _not_pending(row)
    assert row.photo is not None and row.full_name and row.gender and row.date_of_birth and row.email
    session = students.current_session(db)
    students.check_gender_fits_hostel(db, row.hostel_id, row.gender)
    photo = files.store_upload(db, p, "student_photo", io.BytesIO(row.photo), "registration-photo.jpg")
    user, student = students.admit(
        db,
        p,
        session=session,
        full_name=row.full_name,
        email=row.email,
        phone=row.phone,
        password_hash=row.password_hash,
        student_fields={
            "hostel_id": row.hostel_id,
            "gender": row.gender,
            "date_of_birth": row.date_of_birth,
            "photo_file_id": photo.id,
            "admission_date": local_today(db),
            "program": row.program,
            "year_of_study": row.year_of_study,
        },
        guardians=[
            {
                "full_name": row.guardian_name,
                "relationship": row.guardian_relationship,
                "phone": row.guardian_phone,
                "is_primary": True,
            }
        ],
    )
    db.execute(
        update(User).where(User.id == user.id).values(email_verified_at=row.email_verified_at, updated_at=func.now())
    )
    row.status = "approved"
    row.decided_by = p.user_id
    row.decided_at = sessions.now()
    row.student_id = student.id
    row.password_hash = None  # it moved to the user
    row.photo = None  # it became the student's photo file
    db.flush()
    audit.record(
        db,
        p,
        "student_request.approved",
        "student_request",
        row.id,
        hostel_id=row.hostel_id,
        after={"status": "approved", "student_code": student.student_code},
    )
    jobs.enqueue(db, "email.student_request_decided", {"request_id": str(row.id)}, org_id=p.org_id)
    return student


def reject(ctx: OrgContext, request_id: uuid.UUID, reason: str) -> Request:
    db, p = ctx.db, ctx.principal
    row = get_request(ctx, request_id, lock=True)
    if row.status != "pending":
        raise _not_pending(row)
    row.status = "rejected"
    row.decided_by = p.user_id
    row.decided_at = sessions.now()
    row.reject_reason = reason.strip()
    db.flush()
    audit.record(
        db,
        p,
        "student_request.rejected",
        "student_request",
        row.id,
        hostel_id=row.hostel_id,
        after={"status": "rejected", "reject_reason": row.reject_reason},
    )
    jobs.enqueue(db, "email.student_request_decided", {"request_id": str(row.id)}, org_id=p.org_id)
    return row


# --- the worker ----------------------------------------------------------------------------------------


def purge(db: Session) -> None:
    """Per organization (tenant context set by the caller): unverified requests are deleted after
    24 hours, with their links; a rejected request's personal data is deleted 30 days after the
    decision (the row keeps the status, hostel, dates, decider and reason)."""
    stale = select(Request.id).where(Request.status == "unverified", Request.created_at < func.now() - UNVERIFIED_KEPT)
    db.execute(delete(AuthToken).where(AuthToken.registration_request_id.in_(stale)))
    db.execute(delete(Request).where(Request.id.in_(stale)))
    db.execute(
        text(
            "UPDATE student_registration_requests SET full_name = NULL, email = NULL, phone = NULL, gender = NULL, "
            "date_of_birth = NULL, program = NULL, year_of_study = NULL, guardian_name = NULL, "
            "guardian_relationship = NULL, guardian_phone = NULL, password_hash = NULL, photo = NULL, "
            "photo_mime = NULL, personal_data_deleted_at = now(), updated_at = now() "
            "WHERE status = 'rejected' AND personal_data_deleted_at IS NULL AND decided_at < now() - :keep"
        ),
        {"keep": REJECTED_KEPT},
    )
