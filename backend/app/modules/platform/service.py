"""Platform Owner authentication (docs/04 §5.9).

Same shape as organization login, with these differences:
  * database role hms_platform, never a tenant context (04 §4.4);
  * tokens with aud="hms-platform", signed with a DIFFERENT key;
  * cookie hms_platform_refresh = <session_id>.<secret>, Path=/api/v1/platform/auth;
  * email and password only (D87: no two-step verification); 8-hour absolute sessions (Q67);
  * wrong passwords lock the account: 5 -> 15 minutes (platform users have no login_attempts table).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import passwords, tokens
from app.core.db import transaction
from app.core.errors import ProblemError
from app.core.request_state import user_agent, valid_client_ip
from app.modules.platform.models import PlatformSession, PlatformUser

COOKIE = "hms_platform_refresh"
COOKIE_PATH = "/api/v1/platform/auth"
LIFETIME = timedelta(hours=8)  # Q67: sliding and absolute
LOCK_AFTER = 5
LOCK_FOR = timedelta(minutes=15)
ROTATION_GRACE = timedelta(seconds=10)


def _now() -> datetime:
    return datetime.now(UTC)


def _tx() -> Any:
    return transaction(None, role="hms_platform")


def invalid_credentials() -> ProblemError:
    return ProblemError(401, "invalid_credentials", "Wrong email or password")


def session_invalid() -> ProblemError:
    return ProblemError(401, "session_invalid", "Please sign in again")


@dataclass
class PlatformAuth:
    status: str  # always authenticated (D87)
    user: PlatformUser
    session: PlatformSession
    secret: str


def _set_user(db: Session, user_id: uuid.UUID, **values: Any) -> None:
    db.execute(update(PlatformUser).where(PlatformUser.id == user_id).values(**values, updated_at=func.now()))


def _new_session(
    db: Session,
    user_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
    absolute: datetime | None = None,
) -> tuple[PlatformSession, str]:
    secret = tokens.new_secret()
    absolute = absolute or _now() + LIFETIME
    row = PlatformSession(
        platform_user_id=user_id,
        family_id=family_id or uuid.uuid4(),
        refresh_token_hash=tokens.hash_secret(secret),
        last_used_at=_now(),
        expires_at=min(_now() + LIFETIME, absolute),
        absolute_expires_at=absolute,
        ip_address=valid_client_ip(),
        user_agent=user_agent.get(),
    )
    db.add(row)
    db.flush()
    return row, secret


def _rotate(db: Session, old: PlatformSession) -> tuple[PlatformSession, str]:
    old.revoked_at, old.revoke_reason = _now(), "rotated"
    db.flush()
    return _new_session(db, old.platform_user_id, old.family_id, old.absolute_expires_at)


def _revoke(db: Session, reason: str, *conditions: Any) -> None:
    db.execute(
        update(PlatformSession)
        .where(PlatformSession.revoked_at.is_(None), *conditions)
        .values(revoked_at=_now(), revoke_reason=reason)
    )


def access_token(row: PlatformSession) -> str:
    return tokens.issue(audience="hms-platform", typ="access", sub=row.platform_user_id, sid=row.id, org=None)


def set_cookie(response: Response, row: PlatformSession, secret: str) -> None:
    response.set_cookie(
        COOKIE,
        f"{row.id}.{secret}",
        max_age=int((row.absolute_expires_at - _now()).total_seconds()),
        path=COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=True, httponly=True, samesite="strict")


def _parse_cookie(value: str | None) -> tuple[uuid.UUID, str] | None:
    if not value or value.count(".") != 1:
        return None
    sid, secret = value.split(".")
    try:
        return uuid.UUID(sid), secret
    except ValueError:
        return None


# --- login ---------------------------------------------------------------------------------


def login(email: str, password: str) -> PlatformAuth:
    outcome: PlatformAuth | ProblemError
    with _tx() as db:
        user = db.scalars(select(PlatformUser).where(func.lower(PlatformUser.email) == email.strip().lower())).first()
        if user is None:
            passwords.dummy_verify(password)
            outcome = invalid_credentials()
        elif user.locked_until is not None and user.locked_until > _now():
            passwords.dummy_verify(password)
            outcome = ProblemError(
                429,
                "too_many_attempts",
                "Too many attempts. Try again later.",
                headers={"Retry-After": str(int((user.locked_until - _now()).total_seconds()) + 1)},
            )
        elif not passwords.verify_password(user.password_hash, password):
            _count_failure(db, user)
            outcome = invalid_credentials()
        elif user.status != "active":
            outcome = ProblemError(403, "account_deactivated", "This account is not active")
        else:
            _set_user(db, user.id, failed_login_count=0, locked_until=None, last_login_at=func.now())
            row, secret = _new_session(db, user.id)
            outcome = PlatformAuth("authenticated", user, session=row, secret=secret)
    if isinstance(outcome, ProblemError):
        raise outcome
    return outcome


def _count_failure(db: Session, user: PlatformUser) -> None:
    failures = user.failed_login_count + 1
    if failures >= LOCK_AFTER:
        _set_user(db, user.id, failed_login_count=0, locked_until=_now() + LOCK_FOR)
    else:
        _set_user(db, user.id, failed_login_count=failures)


# --- refresh and logout ----------------------------------------------------------------------------


def refresh(cookie: str | None) -> PlatformAuth:
    parts = _parse_cookie(cookie)
    if parts is None:
        raise session_invalid()
    sid, secret = parts
    outcome: PlatformAuth | ProblemError
    with _tx() as db:
        row = db.scalars(select(PlatformSession).where(PlatformSession.id == sid).with_for_update()).one_or_none()
        user = db.get(PlatformUser, row.platform_user_id) if row else None
        if row is None or user is None or not tokens.secrets_equal(row.refresh_token_hash, tokens.hash_secret(secret)):
            outcome = session_invalid()
        elif row.revoked_at is not None:
            if row.revoke_reason == "rotated" and _now() - row.revoked_at <= ROTATION_GRACE:
                outcome = ProblemError(401, "refresh_superseded", "Refreshed in another tab, retry")
            else:
                if row.revoke_reason == "rotated":
                    _revoke(db, "reuse_detected", PlatformSession.family_id == row.family_id)
                outcome = session_invalid()
        elif user.status != "active" or row.expires_at <= _now() or row.absolute_expires_at <= _now():
            outcome = session_invalid()
        else:
            new_row, new_secret = _rotate(db, row)
            outcome = PlatformAuth("authenticated", user, session=new_row, secret=new_secret)
    if isinstance(outcome, ProblemError):
        raise outcome
    return outcome


def logout(cookie: str | None) -> None:
    parts = _parse_cookie(cookie)
    if parts is None:
        return
    sid, secret = parts
    with _tx() as db:
        row = db.get(PlatformSession, sid)
        if row is not None and tokens.secrets_equal(row.refresh_token_hash, tokens.hash_secret(secret)):
            _revoke(db, "logout", PlatformSession.family_id == row.family_id)
