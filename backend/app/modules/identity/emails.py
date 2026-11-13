"""Security emails sent by the worker: registration and activation links and the password-reset
code (D77).

The one-time secret is created HERE, inside the worker, at send time (Phase 1 design):
the API only queues "send user X a reset code". So the raw secret exists only in this
function's memory and in the email itself; it is never in the jobs table, the logs or
the audit log. Only its hash is stored.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import email, jobs, otp, tokens
from app.core.config import get_settings
from app.modules.identity import sessions
from app.modules.identity.models import AuthToken, EmailOtp, User
from app.modules.organizations.models import Organization

RESET_CODE_TTL = timedelta(minutes=10)  # D77
RESET_CODES_PER_HOUR = 3  # per account (D77)
REGISTRATION_TTL = timedelta(hours=24)
ACTIVATION_TTL = timedelta(days=7)  # D13


def _load(db: Session, job: jobs.ClaimedJob) -> tuple[User, Organization] | None:
    user = db.get(User, uuid.UUID(str(job.payload["user_id"])))
    org = db.get(Organization, job.org_id) if job.org_id else None
    if user is None or org is None or not user.email:
        return None
    return user, org


def _send(job: jobs.ClaimedJob, to: str, template: str, subject: str, **context: object) -> None:
    message = email.render(template, subject, **context)
    email.get_sender().send(
        email.OutgoingEmail(
            to=to, subject=subject, text=message.text, html=message.html, message_id=email.message_id_for_job(job.id)
        )
    )


def _new_link_token(db: Session, user: User, purpose: str, ttl: timedelta) -> str:
    """Revoke the user's live link of this purpose (one live link per user, DB index), make a new one."""
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
            AuthToken.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )
    raw = tokens.new_secret()
    db.add(
        AuthToken(
            org_id=user.org_id,
            user_id=user.id,
            purpose=purpose,
            token_hash=tokens.hash_secret(raw),
            delivery="email",
            expires_at=sessions.now() + ttl,
        )
    )
    db.flush()
    return raw


def _url(path: str) -> str:
    return get_settings().hms_public_url.rstrip("/") + path


@jobs.handler("email.org_registration")
def send_registration_link(db: Session, job: jobs.ClaimedJob) -> None:
    loaded = _load(db, job)
    if loaded is None or loaded[1].status != "pending_verification":
        return
    user, org = loaded
    raw = _new_link_token(db, user, "org_registration", REGISTRATION_TTL)
    # The token goes in the URL FRAGMENT (#...): browsers never send it to a server or in Referer.
    _send(
        job,
        user.email or "",
        "org_registration",
        f"Confirm your organization: {org.name}",
        name=user.full_name,
        org_name=org.name,
        link_url=_url(f"/o/{org.slug}/verify#token={raw}"),
        hours=int(REGISTRATION_TTL.total_seconds() // 3600),
    )


@jobs.handler("email.activation")
def send_activation_link(db: Session, job: jobs.ClaimedJob) -> None:
    loaded = _load(db, job)
    if loaded is None:
        return
    user, org = loaded
    db.refresh(user, with_for_update=True)  # two sends for one person can't both make a link
    if org.status != "active" or user.status != "pending_activation":
        return
    # D80: links are only emailed (no copied links), so a new email replaces the previous link.
    raw = _new_link_token(db, user, "activation", ACTIVATION_TTL)
    _send(
        job,
        user.email or "",
        "activation",
        f"Activate your account at {org.name}",
        name=user.full_name,
        org_name=org.name,
        link_url=_url(f"/o/{org.slug}/activate#token={raw}"),
        days=int(ACTIVATION_TTL.total_seconds() // 86400),
    )


def reset_codes_since(db: Session, user_id: uuid.UUID, delta: timedelta) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(EmailOtp)
            .where(
                EmailOtp.user_id == user_id,
                EmailOtp.purpose == "password_reset",
                EmailOtp.created_at > func.now() - delta,
            )
        )
        or 0
    )


@jobs.handler("email.password_reset_code")
def send_reset_code(db: Session, job: jobs.ClaimedJob) -> None:
    """D77: a 6-digit code, valid 10 minutes, single use. A new code cancels the previous one
    (one live reset code per user: database index email_otps_one_live_reset)."""
    loaded = _load(db, job)
    if loaded is None or loaded[1].status != "active" or loaded[0].status not in ("active", "read_only"):
        return
    user, org = loaded
    db.refresh(user, with_for_update=True)  # two jobs for the same person can't both make a code
    if reset_codes_since(db, user.id, timedelta(hours=1)) >= RESET_CODES_PER_HOUR:
        return  # per account (the API checks too; this covers jobs queued before a code existed)
    db.execute(
        update(EmailOtp)
        .where(
            EmailOtp.user_id == user.id,
            EmailOtp.purpose == "password_reset",
            EmailOtp.consumed_at.is_(None),
            EmailOtp.cancelled_at.is_(None),
        )
        .values(cancelled_at=func.now())
    )
    code = otp.new_code()
    db.add(
        EmailOtp(
            org_id=user.org_id,
            user_id=user.id,
            purpose="password_reset",
            code_hash=otp.hash_code(code),
            expires_at=sessions.now() + RESET_CODE_TTL,
        )
    )
    db.flush()
    _send(
        job,
        user.email or "",
        "password_reset_code",
        "Your password reset code",  # not the code itself: subjects show on locked phone screens
        name=user.full_name,
        org_name=org.name,
        code=code,
        minutes=int(RESET_CODE_TTL.total_seconds() // 60),
    )
