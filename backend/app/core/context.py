"""The request pipeline's dependencies: token -> tenant context -> database session -> principal.

Every organization route receives an `OrgContext` (docs/04 §4.1):

    org_context()                         FastAPI dependency, scope="function"
      1. bearer token from the Authorization header       -> 401 if missing
      2. tokens.verify(aud="hms-api", typ="access")       -> 401 token_expired / session_invalid
         gives sub (user), org (tenant), sid (session)
      3. db.transaction(org)                              BEGIN; set_config('app.org_id', org, true)
                                                          RLS is scoped from here on
      4. load_principal(): one query, under RLS:
         session not revoked/expired and owned by sub (no second step since D87);
         user active (read_only -> GET only); organization active; then the roles
      5. yield OrgContext(db, principal) to the route
      6. route returns  -> COMMIT  (here, before the response is sent)
         route raises   -> ROLLBACK, and the error handler builds the response

Why scope="function": with FastAPI's default ("request") scope, code after `yield`
runs AFTER the response has been sent. A COMMIT that fails there (a deferred
constraint, a deadlock) would leave the client holding a 200 for data that was
never saved. With scope="function", the commit happens before the response, and
its error becomes a proper problem response. tests/test_pipeline.py proves it.

The organization ID comes ONLY from the verified token. Nothing the client sends in
a header, path, query or body can change it (05 §3, tenant isolation).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import tokens
from app.core.db import transaction
from app.core.errors import ProblemError
from app.core.permissions import Principal, RoleGrant
from app.modules.identity.models import RoleAssignment, User, UserSession
from app.modules.organizations.models import Organization

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class OrgContext:
    db: Session
    principal: Principal


def _unauthorized(code: str = "session_invalid", title: str = "Please sign in again") -> ProblemError:
    return ProblemError(401, code, title, headers={"WWW-Authenticate": "Bearer"})


def bearer_token(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise _unauthorized()
    return value.strip()


def _claim_uuid(claims: dict[str, object], name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(claims[name]))
    except (KeyError, ValueError) as exc:
        raise _unauthorized() from exc


def load_principal(
    db: Session, *, user_id: uuid.UUID, org_id: uuid.UUID, session_id: uuid.UUID, method: str
) -> Principal:
    """Check the session, user and organization are all still valid, then load the roles.

    Runs inside the tenant transaction, so RLS already limits every table to `org_id`:
    a token naming another organization's session simply finds nothing.
    """
    row = db.execute(
        select(
            User.kind,
            User.status,
            Organization.status,
        )
        .select_from(UserSession)
        .join(User, (User.org_id == UserSession.org_id) & (User.id == UserSession.user_id))
        .join(Organization, Organization.id == UserSession.org_id)
        .where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.org_id == org_id,
            UserSession.revoked_at.is_(None),
            # Compared with the clock that WROTE these times (Python, identity/sessions.py), so a frozen
            # test clock and a real one never disagree (D67). In production both are the real time.
            UserSession.expires_at > datetime.now(UTC),
            UserSession.absolute_expires_at > datetime.now(UTC),
        )
    ).one_or_none()
    if row is None:
        raise _unauthorized()
    kind, user_status, org_status = row

    if org_status == "suspended":
        raise ProblemError(
            403,
            "organization_suspended",
            "This organization is suspended",
            detail="Contact the platform administrator.",
        )
    if org_status != "active":
        raise _unauthorized()
    if user_status not in ("active", "read_only"):
        raise ProblemError(403, "account_deactivated", "This account is not active")

    grants = tuple(  # organization-wide: since the schema cleanup a grant has no hostel (D75)
        RoleGrant(role, None)
        for role in db.scalars(
            select(RoleAssignment.role)
            .where(RoleAssignment.user_id == user_id, RoleAssignment.revoked_at.is_(None))
            .order_by(RoleAssignment.role)
        )
    )
    read_only = user_status == "read_only"
    if read_only and method.upper() not in SAFE_METHODS:
        raise ProblemError(
            403,
            "account_read_only",
            "This account is read-only",
            detail="You can still view your records and receipts.",
        )
    return Principal(
        user_id=user_id, org_id=org_id, session_id=session_id, kind=kind, read_only=read_only, grants=grants
    )


def org_context(request: Request) -> Iterator[OrgContext]:
    token = bearer_token(request)
    try:
        claims = tokens.verify(token, audience="hms-api", typ="access")
    except tokens.TokenExpired as exc:
        raise _unauthorized("token_expired", "Session token expired") from exc
    except tokens.TokenError as exc:
        raise _unauthorized() from exc
    org_id = _claim_uuid(claims, "org")
    user_id = _claim_uuid(claims, "sub")
    session_id = _claim_uuid(claims, "sid")

    with transaction(org_id) as db:
        principal = load_principal(db, user_id=user_id, org_id=org_id, session_id=session_id, method=request.method)
        yield OrgContext(db=db, principal=principal)
    # Leaving the `with` block COMMITs (or ROLLs BACK if the route raised).


def public_db() -> Iterator[Session]:
    """A transaction with NO tenant: tenant tables return zero rows (fail closed).
    For pre-login routes, which then switch to the organization named by the slug."""
    with transaction(None) as db:
        yield db


# Use these in route signatures. The same dependency object everywhere means FastAPI
# resolves it once per request, even when `require()` also depends on it.
Ctx = Annotated[OrgContext, Depends(org_context, scope="function")]
PublicDb = Annotated[Session, Depends(public_db, scope="function")]


def require(permission: str) -> Callable[[OrgContext], OrgContext]:
    """Route-level permission check: `ctx: Annotated[OrgContext, Depends(require(AUDIT_READ))]`."""

    def _check(ctx: Ctx) -> OrgContext:
        if not ctx.principal.has(permission):
            raise ProblemError(403, "permission_denied", "Not allowed")
        return ctx

    _check.required_permission = permission  # type: ignore[attr-defined]  # read by the security matrix (1.6)
    return _check


def staff_only(ctx: Ctx) -> OrgContext:
    """Router-level guard for /admin (docs/04 §2): a student gets 403 wrong_portal."""
    if ctx.principal.kind != "staff":
        raise ProblemError(403, "wrong_portal", "This area is for staff")
    return ctx


def student_only(ctx: Ctx) -> OrgContext:
    """Router-level guard for /portal."""
    if ctx.principal.kind != "student":
        raise ProblemError(403, "wrong_portal", "This area is for students")
    return ctx
