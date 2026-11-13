"""Emails about student registration requests (D74), sent by the worker.

As for every link: the raw verification token is made HERE, at send time, so it exists only in this
function's memory and in the email; the database keeps its hash (auth_tokens, purpose student_request).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import jobs, tokens
from app.modules.identity import sessions
from app.modules.identity.emails import _send, _url
from app.modules.identity.models import AuthToken, User
from app.modules.organizations.models import Organization
from app.modules.registration.models import StudentRegistrationRequest as Request
from app.modules.students.models import Student

VERIFY_TTL = timedelta(hours=24)


def _request(db: Session, job: jobs.ClaimedJob) -> tuple[Request, Organization] | None:
    row = db.get(Request, uuid.UUID(str(job.payload["request_id"])))
    org = db.get(Organization, job.org_id) if job.org_id else None
    if row is None or org is None or not row.email:
        return None
    return row, org


def new_request_token(db: Session, row: Request) -> str:
    """Revoke the request's live link (one per request, database index) and make a new one."""
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.registration_request_id == row.id,
            AuthToken.used_at.is_(None),
            AuthToken.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )
    raw = tokens.new_secret()
    db.add(
        AuthToken(
            org_id=row.org_id,
            user_id=None,
            registration_request_id=row.id,
            purpose="student_request",
            token_hash=tokens.hash_secret(raw),
            delivery="email",
            expires_at=sessions.now() + VERIFY_TTL,
        )
    )
    db.flush()
    return raw


@jobs.handler("email.student_request_verify")
def send_verification(db: Session, job: jobs.ClaimedJob) -> None:
    loaded = _request(db, job)
    if loaded is None or loaded[0].status != "unverified" or loaded[1].status != "active":
        return
    row, org = loaded
    raw = new_request_token(db, row)
    _send(
        job,
        row.email or "",
        "student_request_verify",
        f"Confirm your registration request: {org.name}",
        name=row.full_name,
        org_name=org.name,
        link_url=_url(f"/o/{org.slug}/register/verify#token={raw}"),
        hours=int(VERIFY_TTL.total_seconds() // 3600),
    )


@jobs.handler("email.student_request_duplicate")
def send_duplicate_notice(db: Session, job: jobs.ClaimedJob) -> None:
    """Someone used an address that already has an account or an open request. The form's answer
    was the same as always; only the owner of the address learns this."""
    org = db.get(Organization, job.org_id) if job.org_id else None
    if org is None:
        return
    if "user_id" in job.payload:
        user = db.get(User, uuid.UUID(str(job.payload["user_id"])))
        if user is None or not user.email:
            return
        to, name, has = user.email, user.full_name, "account"
    else:
        row = db.get(Request, uuid.UUID(str(job.payload["request_id"])))
        if row is None or not row.email:
            return
        to, name, has = row.email, row.full_name or "", "request"
    _send(
        job,
        to,
        "student_request_duplicate",
        f"About your registration request: {org.name}",
        name=name,
        org_name=org.name,
        has=has,
        sign_in_url=_url("/sign-in/student"),
    )


@jobs.handler("email.student_request_decided")
def send_decision(db: Session, job: jobs.ClaimedJob) -> None:
    loaded = _request(db, job)
    if loaded is None or loaded[0].status not in ("approved", "rejected"):
        return
    row, org = loaded
    if row.status == "approved":
        code = db.scalar(select(Student.student_code).where(Student.id == row.student_id))
        _send(
            job,
            row.email or "",
            "student_request_approved",
            f"Welcome to {org.name}: your registration was approved",
            name=row.full_name,
            org_name=org.name,
            org_slug=org.slug,
            student_code=code,
            sign_in_url=_url("/sign-in/student"),
        )
    else:
        _send(
            job,
            row.email or "",
            "student_request_rejected",
            f"Your registration request at {org.name}",
            name=row.full_name,
            org_name=org.name,
            reason=row.reject_reason,
        )
