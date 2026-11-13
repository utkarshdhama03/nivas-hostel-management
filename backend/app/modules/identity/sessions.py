"""Login sessions, refresh cookies and the tokens a successful login returns (docs/04 §5.6).

A session row = one login on one device. Its refresh secret is random (256 bits);
only its SHA-256 hash is stored. The browser holds it in an httpOnly cookie:

    hms_refresh = <org_id>.<session_id>.<secret>
                  HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth

  HttpOnly        page JavaScript can't read it, so an XSS bug can't steal it
  Secure          only sent over HTTPS (browsers treat http://localhost as secure too)
  SameSite=Strict never sent on requests started by another site (CSRF)
  Path            only sent to /api/v1/auth/*, nowhere else

Rotation: every refresh revokes the presented row ('rotated') and creates a new row in
the same family with a new secret. Presenting a rotated secret again means someone
else holds a copy; see refresh() in service.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Response
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core import request_state, tokens
from app.core.db import rowcount
from app.modules.identity.models import UserSession

REFRESH_COOKIE = "hms_refresh"
REFRESH_PATH = "/api/v1/auth"
SLIDING = timedelta(days=7)  # Q67
ABSOLUTE = timedelta(days=30)
ROTATION_GRACE = timedelta(seconds=10)  # two tabs refreshing at once (04 §5.6)


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class IssuedSession:
    row: UserSession
    secret: str

    @property
    def cookie_value(self) -> str:
        return f"{self.row.org_id}.{self.row.id}.{self.secret}"


def start(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    absolute: timedelta = ABSOLUTE,
) -> IssuedSession:
    """A brand-new login (new family). Always a new row: never reuse a session (fixation)."""
    started = now()
    absolute_expires = started + absolute
    return _insert(
        db,
        org_id=org_id,
        user_id=user_id,
        family_id=uuid.uuid4(),
        absolute_expires_at=absolute_expires,
    )


def rotate(db: Session, old: UserSession) -> IssuedSession:
    old.revoked_at, old.revoke_reason = now(), "rotated"
    db.flush()
    return _insert(
        db,
        org_id=old.org_id,
        user_id=old.user_id,
        family_id=old.family_id,
        absolute_expires_at=old.absolute_expires_at,
    )


def _insert(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    absolute_expires_at: datetime,
) -> IssuedSession:
    secret = tokens.new_secret()
    row = UserSession(
        org_id=org_id,
        user_id=user_id,
        family_id=family_id,
        refresh_token_hash=tokens.hash_secret(secret),
        last_used_at=now(),
        expires_at=min(now() + SLIDING, absolute_expires_at),
        absolute_expires_at=absolute_expires_at,
        ip_address=request_state.valid_client_ip(),
        user_agent=request_state.user_agent.get(),
    )
    db.add(row)
    db.flush()
    return IssuedSession(row, secret)


def revoke(db: Session, reason: str, *conditions: Any) -> int:
    """Revoke every still-active session matching the conditions."""
    result = db.execute(
        update(UserSession)
        .where(UserSession.revoked_at.is_(None), *conditions)
        .values(revoked_at=now(), revoke_reason=reason)
    )
    return rowcount(result)


def parse_cookie(value: str | None) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    """'<org>.<session>.<secret>' -> parts, or None if malformed. The org ID is untrusted input:
    it only chooses the tenant context; without the matching secret it achieves nothing."""
    if not value:
        return None
    parts = value.split(".")
    if len(parts) != 3 or not parts[2]:
        return None
    try:
        return uuid.UUID(parts[0]), uuid.UUID(parts[1]), parts[2]
    except ValueError:
        return None


def set_cookie(response: Response, issued: IssuedSession) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        issued.cookie_value,
        max_age=int((issued.row.absolute_expires_at - now()).total_seconds()),
        path=REFRESH_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH, secure=True, httponly=True, samesite="strict")


def access_token(row: UserSession) -> str:
    return tokens.issue(audience="hms-api", typ="access", sub=row.user_id, sid=row.id, org=row.org_id)
