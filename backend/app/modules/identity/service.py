"""Authentication for organization users (docs/04 §5). Routes in router.py stay thin.

A pattern used throughout: failures that must be REMEMBERED (a wrong password counts
towards the lockout, a reused refresh token revokes its family) are written inside the
transaction, the transaction COMMITS, and only then is the error raised. Raising inside
the transaction would roll the counter back and make the limit useless.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core import audit, jobs, otp, passwords, ratelimit, request_state, tokens
from app.core.config import get_settings
from app.core.context import load_principal
from app.core.db import set_tenant, transaction
from app.core.errors import ProblemError
from app.core.permissions import SUPER_ADMIN, Principal, RoleGrant
from app.modules.identity import sessions
from app.modules.identity.models import (
    AuthToken,
    EmailOtp,
    LoginAttempt,
    RoleAssignment,
    StaffMember,
    User,
    UserSession,
)
from app.modules.notifications import service as notifications
from app.modules.organizations.models import Organization, OrganizationProfile, OrgCounter

LOCK_AFTER = 5  # wrong passwords -> account locked (Q72)
LOCK_FOR = timedelta(minutes=15)
IP_FAILURES = 20  # failed logins per IP per 15 minutes (Q72)
RESET_CODE_ATTEMPTS = 5  # wrong reset codes, then the code is cancelled (D77)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
RESERVED_SLUGS = frozenset({"admin", "api", "app", "platform", "portal", "static", "www"})
EMPLOYEE_COUNTER_SCOPE = "0000"  # employee codes run for the organization's whole life, not per year


# --- errors (04 §7) ------------------------------------------------------------------------------


def invalid_credentials() -> ProblemError:
    return ProblemError(401, "invalid_credentials", "Wrong identifier or password")


def too_many(retry_after: int) -> ProblemError:
    return ProblemError(
        429,
        "too_many_attempts",
        "Too many attempts. Try again later.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def session_invalid() -> ProblemError:
    return ProblemError(401, "session_invalid", "Please sign in again")


def link_expired() -> ProblemError:
    return ProblemError(410, "link_expired", "This link has expired or was already used")


def reset_code_invalid() -> ProblemError:
    """D77: one answer for a wrong, expired, used or cancelled code, and for an unknown account."""
    return ProblemError(
        400,
        "invalid_reset_code",
        "That code didn't work",
        detail="Check the code in the email. A code works once, for 10 minutes; after 5 wrong tries ask for a new one.",
    )


def weak_password(problems: list[passwords.PasswordProblem]) -> ProblemError:
    return ProblemError(
        422,
        "validation_error",
        "Choose a different password",
        errors=[{"field": "password", "code": p.code, "message": p.message} for p in problems],
    )


# --- small helpers --------------------------------------------------------------------------------


def _client_ip() -> str | None:
    return request_state.valid_client_ip()


def _roles(db: Session, user_id: uuid.UUID) -> tuple[RoleGrant, ...]:
    rows = db.scalars(
        select(RoleAssignment.role).where(RoleAssignment.user_id == user_id, RoleAssignment.revoked_at.is_(None))
    )
    return tuple(RoleGrant(role, None) for role in rows)  # organization-wide (D75)


def _actor(user: User, grants: tuple[RoleGrant, ...] = ()) -> Principal:
    """An audit actor for flows where the user isn't signed in yet (registration, reset)."""
    return Principal(user_id=user.id, org_id=user.org_id, session_id=uuid.UUID(int=0), kind=user.kind, grants=grants)


def _record_attempt(db: Session, org_id: uuid.UUID, user_id: uuid.UUID | None, key: str, succeeded: bool) -> None:
    db.add(
        LoginAttempt(
            org_id=org_id,
            user_id=user_id,
            identifier_hash=tokens.hash_secret(key),
            ip_address=_client_ip(),
            succeeded=succeeded,
        )
    )
    db.flush()


def _password_context(
    org: Organization | None, user: User, codes: tuple[str | None, ...] = ()
) -> tuple[str | None, ...]:
    """Words a password must not contain (04 §5.11): org name and slug, the user's name parts,
    email local part, employee/student code."""
    words: list[str | None] = [*(user.full_name or "").split(), (user.email or "").split("@")[0], *codes]
    if org is not None:
        words += [org.slug, *org.name.split()]
    return tuple(words)


def _set_user(db: Session, user_id: uuid.UUID, **values: Any) -> None:
    """Security columns are changed with a plain UPDATE that does NOT bump `version`: a login
    must never make an admin's open edit form fail with edit_conflict."""
    db.execute(update(User).where(User.id == user_id).values(**values, updated_at=func.now()))


def _org_by_slug(db: Session, slug: str) -> Organization | None:
    return db.scalars(select(Organization).where(Organization.slug == slug.strip().lower())).one_or_none()


def _code_for(db: Session, user: User) -> str | None:
    if user.kind == "staff":
        code = db.scalar(select(StaffMember.employee_code).where(StaffMember.user_id == user.id))
    else:
        code = db.scalar(text("SELECT student_code FROM students WHERE user_id = :u"), {"u": user.id})
    return str(code) if code else None


# --- login (04 §5.3) ------------------------------------------------------------------------------


@dataclass
class AuthResult:
    """A successful sign-in: the new session (access token + refresh cookie). D87: no second step."""

    status: str  # always authenticated
    user: User
    issued: sessions.IssuedSession


def _find_user(db: Session, portal: str, identifier: str) -> User | None:
    ident = identifier.strip()
    if "@" in ident:
        return db.scalars(select(User).where(func.lower(User.email) == ident.lower(), User.kind == portal)).first()
    code = ident.upper()
    if portal == "staff":
        return db.scalars(
            select(User)
            .join(StaffMember, StaffMember.user_id == User.id)
            .where(StaffMember.employee_code == code, User.kind == "staff")
        ).first()
    user_id = db.scalar(text("SELECT user_id FROM students WHERE student_code = :c"), {"c": code})
    return db.get(User, user_id) if user_id else None


def _registration_answer(db: Session, portal: str, identifier: str, password: str) -> tuple[ProblemError | None, bool]:
    """D74 (11 November): there is no status page. Someone who asked to register signs in on Student
    sign-in with the request's email and password; ONLY with the correct password do they learn how
    the request stands (not confirmed yet, waiting, or rejected with the reason). Approved requests
    have a real account, found before this. Returns (answer, whether a password hash was checked)."""
    ident = identifier.strip()
    if portal != "student" or "@" not in ident:
        return None, False
    row = db.execute(
        text(
            "SELECT status, password_hash, reject_reason FROM student_registration_requests "
            "WHERE lower(email) = lower(:e) AND password_hash IS NOT NULL "
            "AND status IN ('unverified', 'pending', 'rejected') ORDER BY created_at DESC LIMIT 1"
        ),
        {"e": ident},
    ).first()
    if row is None:
        return None, False
    if not passwords.verify_password(row.password_hash, password):
        return None, True
    if row.status == "unverified":
        return (
            ProblemError(
                403,
                "registration_unverified",
                "Confirm your email first",
                detail="Open the link in the email we sent you when you registered. It works for 24 hours.",
            ),
            True,
        )
    if row.status == "pending":
        return (
            ProblemError(
                403,
                "registration_pending",
                "Your registration request is waiting for the administrator's approval",
                detail="You can sign in here once it is approved. We'll email you.",
            ),
            True,
        )
    return (
        ProblemError(
            403,
            "registration_rejected",
            "Your registration request was rejected",
            detail=f"The administrator's reason: {row.reject_reason}",
        ),
        True,
    )


def administrator_org(db: Session, email: str) -> Organization | None:
    """D78: the organization of the Administrator with this email, through the narrow database function
    administrator_org() (the table behind it is readable only by the function; RLS stays as it is)."""
    org_id = db.scalar(text("SELECT administrator_org(:e)"), {"e": email})
    return db.get(Organization, org_id) if org_id else None


def login(slug: str, portal: str, identifier: str, password: str) -> AuthResult:
    """Sign-in with the organization's sign-in name (/o/{slug}/login and Student sign-in)."""
    return _login(lambda db: _org_by_slug(db, slug), portal, identifier, password)


def student_org(db: Session, email: str) -> Organization | None:
    """D80: the organization of the student with this email, or of the newest registration request
    that sign-in can still tell about (D74), through the narrow database function student_org()."""
    org_id = db.scalar(text("SELECT student_org(:e)"), {"e": email})
    return db.get(Organization, org_id) if org_id else None


def org_for_email(db: Session, email: str, portal: str) -> Organization | None:
    return student_org(db, email) if portal == "student" else administrator_org(db, email)


def login_administrator(email: str, password: str, portal: str = "staff") -> AuthResult:
    """D78 (Organization sign-in) and D80 (Student sign-in): only the email and the password. An unknown
    email gets exactly what a wrong password gets (05 AUTH-2), after the same Argon2 work; the same
    limits apply. A registered student's request state is still told after the correct password (D74)."""
    return _login(lambda db: org_for_email(db, email, portal), portal, email.strip(), password)


def _login(
    find_org: Callable[[Session], Organization | None], portal: str, identifier: str, password: str
) -> AuthResult:
    ip = request_state.client_ip.get() or "unknown"
    wait = ratelimit.LOGIN_FAILURES_PER_IP.retry_after(ip)
    if wait:
        passwords.dummy_verify(password)
        raise too_many(wait)

    outcome: AuthResult | ProblemError
    with transaction(None) as db:
        org = find_org(db)
        if org is None or org.status == "pending_verification":
            passwords.dummy_verify(password)  # same work and same answer as a wrong password
            outcome = invalid_credentials()
        else:
            set_tenant(db, org.id)  # from here on, only this organization's users exist (04 §4.2)
            outcome = _login_in_org(db, org, portal, identifier, password)
    if isinstance(outcome, ProblemError):
        if outcome.status == 401:
            ratelimit.LOGIN_FAILURES_PER_IP.hit(ip)
        raise outcome
    return outcome


def _login_in_org(
    db: Session, org: Organization, portal: str, identifier: str, password: str
) -> AuthResult | ProblemError:
    key = f"login:{identifier.strip().lower()}"
    ip = _client_ip()
    if ip is not None:
        recent_failures = db.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                LoginAttempt.ip_address == ip,
                LoginAttempt.succeeded.is_(False),
                LoginAttempt.attempted_at > func.now() - LOCK_FOR,
            )
        )
        if (recent_failures or 0) >= IP_FAILURES:
            passwords.dummy_verify(password)
            return too_many(int(LOCK_FOR.total_seconds()))

    user = _find_user(db, portal, identifier)
    if user is None:
        answer, checked = _registration_answer(db, portal, identifier, password)
        if answer is not None:
            _record_attempt(db, org.id, None, key, True)  # the right password: not a failure
            return answer
        if not checked:
            passwords.dummy_verify(password)
        _record_attempt(db, org.id, None, key, False)
        return invalid_credentials()

    now = sessions.now()
    if user.locked_until is not None and user.locked_until > now:
        passwords.dummy_verify(password)
        _record_attempt(db, org.id, user.id, key, False)
        return too_many(int((user.locked_until - now).total_seconds()) + 1)

    if user.password_hash is None or not passwords.verify_password(user.password_hash, password):
        if user.password_hash is None:
            passwords.dummy_verify(password)
        failures = user.failed_login_count + 1
        if failures >= LOCK_AFTER:
            _set_user(db, user.id, failed_login_count=0, locked_until=now + LOCK_FOR)
        else:
            _set_user(db, user.id, failed_login_count=failures)
        _record_attempt(db, org.id, user.id, key, False)
        return invalid_credentials()

    # Correct password. Only now may the answer reveal account or organization state.
    _record_attempt(db, org.id, user.id, key, True)
    if org.status == "suspended":
        return ProblemError(
            403,
            "organization_suspended",
            "This organization is suspended",
            detail="Contact the platform administrator.",
        )
    if user.status not in ("active", "read_only"):
        return ProblemError(403, "account_deactivated", "This account is not active")

    updates: dict[str, Any] = {"failed_login_count": 0, "locked_until": None, "last_login_at": now}
    if passwords.needs_rehash(user.password_hash):
        updates["password_hash"] = passwords.hash_password(password)
    _set_user(db, user.id, **updates)

    issued = sessions.start(db, org_id=org.id, user_id=user.id)  # always a NEW session
    return AuthResult("authenticated", user, issued=issued)


# --- refresh, logout, sessions (04 §5.6) --------------------------------------------------------


def refresh(cookie: str | None) -> AuthResult:
    parts = sessions.parse_cookie(cookie)
    if parts is None:
        raise session_invalid()
    org_id, session_id, secret = parts
    outcome: AuthResult | ProblemError
    with transaction(org_id) as db:
        row = db.scalars(select(UserSession).where(UserSession.id == session_id).with_for_update()).one_or_none()
        if row is None or not tokens.secrets_equal(row.refresh_token_hash, tokens.hash_secret(secret)):
            outcome = session_invalid()
        elif row.revoked_at is not None:
            if row.revoke_reason == "rotated" and sessions.now() - row.revoked_at <= sessions.ROTATION_GRACE:
                # Another tab refreshed a moment ago; the browser already has the newer cookie.
                outcome = ProblemError(401, "refresh_superseded", "Refreshed in another tab, retry")
            elif row.revoke_reason == "rotated":
                # An old secret came back: someone holds a copy. End the whole login (05 AUTH-4).
                sessions.revoke(db, "reuse_detected", UserSession.family_id == row.family_id)
                user = db.get(User, row.user_id)
                if user is not None:
                    audit.record(db, _actor(user, _roles(db, user.id)), "session.reuse_detected", "session", row.id)
                outcome = session_invalid()
            else:
                outcome = session_invalid()
        elif row.expires_at <= sessions.now() or row.absolute_expires_at <= sessions.now():
            outcome = session_invalid()
        else:
            # Same checks as every API request: organization active, user active.
            load_principal(db, user_id=row.user_id, org_id=row.org_id, session_id=row.id, method="GET")
            user = db.get(User, row.user_id)
            assert user is not None
            outcome = AuthResult("authenticated", user, issued=sessions.rotate(db, row))
    if isinstance(outcome, ProblemError):
        raise outcome
    return outcome


def logout(cookie: str | None) -> None:
    parts = sessions.parse_cookie(cookie)
    if parts is None:
        return
    org_id, session_id, secret = parts
    with transaction(org_id) as db:
        row = db.get(UserSession, session_id)
        if row is not None and tokens.secrets_equal(row.refresh_token_hash, tokens.hash_secret(secret)):
            sessions.revoke(db, "logout", UserSession.family_id == row.family_id)


def list_sessions(db: Session, p: Principal) -> list[UserSession]:
    """Sessions the user actually holds: not revoked and not past their absolute end."""
    q = select(UserSession).where(
        UserSession.user_id == p.user_id, UserSession.revoked_at.is_(None), UserSession.absolute_expires_at > func.now()
    )
    return list(db.scalars(q.order_by(UserSession.created_at.desc())).all())


def revoke_session(db: Session, p: Principal, session_id: uuid.UUID) -> None:
    row = db.get(UserSession, session_id)
    if row is None or row.user_id != p.user_id or row.revoked_at is not None:
        raise ProblemError(404, "not_found", "Not found")
    sessions.revoke(db, "logout", UserSession.family_id == row.family_id)


def revoke_other_sessions(db: Session, p: Principal) -> int:
    current = db.get(UserSession, p.session_id)
    family = current.family_id if current else p.session_id
    return sessions.revoke(db, "logout", UserSession.user_id == p.user_id, UserSession.family_id != family)


# --- passwords (04 §5.8, §5.11) -------------------------------------------------------------------


def change_password(db: Session, p: Principal, current: str, new: str) -> None:
    user = db.get(User, p.user_id)
    assert user is not None
    if user.password_hash is None or not passwords.verify_password(user.password_hash, current):
        raise invalid_credentials()
    problems = passwords.check_new_password(
        new, context=_password_context(db.get(Organization, p.org_id), user, (_code_for(db, user),))
    )
    if problems:
        raise weak_password(problems)
    _set_user(db, user.id, password_hash=passwords.hash_password(new))
    current_session = db.get(UserSession, p.session_id)
    family = current_session.family_id if current_session else p.session_id
    sessions.revoke(db, "password_changed", UserSession.user_id == user.id, UserSession.family_id != family)
    audit.record(db, p, "user.password_changed", "user", user.id)
    notifications.notify(
        db,
        org_id=p.org_id,
        user_id=user.id,
        category="security",
        title="Your password was changed",
        body="If this wasn't you, reset your password now and contact your administrator.",
    )


def _reset_target(db: Session, identifier: str, kind: str | None = None) -> User | None:
    """The account a reset is for: by email (any kind) or by Employee ID / Student ID. Only an
    active (or read-only) account with an email address can get a code."""
    ident = identifier.strip()
    if "@" in ident:
        q = select(User).where(func.lower(User.email) == ident.lower())
        user = db.scalars(q.where(User.kind == kind) if kind else q).first()
    else:
        user = _find_user(db, "staff", ident) or _find_user(db, "student", ident)
    if user is None or not user.email or user.status not in ("active", "read_only"):
        return None
    return user


def request_password_reset(slug: str, identifier: str, kind: str | None = None) -> None:
    """Always 'accepted' (04 §5.8), whether or not the account exists (05 AUTH-2). D77: the worker
    makes a 6-digit code and emails it; the code never sits in the jobs table.

    Limits: per IP 20 an hour (429, which says nothing about accounts); per identifier 3 an hour and
    per account 1 a minute and 3 an hour (silently dropped: the answer must not differ)."""
    from app.modules.identity.emails import RESET_CODES_PER_HOUR, reset_codes_since

    ip = request_state.client_ip.get() or "unknown"
    wait = ratelimit.RESET_PER_IP.take(ip)
    if wait:
        raise too_many(wait)
    if ratelimit.RESET_PER_IDENTIFIER.take(f"{slug}:{identifier.strip().lower()}"):
        return  # silently dropped: the response must not differ
    with transaction(None) as db:
        org = _org_by_slug(db, slug)
        if org is None or org.status != "active":
            return
        set_tenant(db, org.id)
        user = _reset_target(db, identifier, kind)
        if user is None:
            return
        if (
            reset_codes_since(db, user.id, timedelta(minutes=1)) >= 1
            or reset_codes_since(db, user.id, timedelta(hours=1)) >= RESET_CODES_PER_HOUR
        ):
            return  # per account, however it is named (email or ID): silently dropped
        jobs.enqueue(
            db,
            "email.password_reset_code",
            {"user_id": str(user.id)},
            org_id=org.id,
            priority=jobs.PRIORITY_SECURITY,
            dedupe_key=f"reset:{user.id}",
        )


def slug_for_email(email: str, portal: str = "staff") -> str:
    """D78/D80: the organization sign-in name for an email, or "" (which no organization has)."""
    with transaction(None) as db:
        org = org_for_email(db, email, portal)
        return org.slug if org is not None else ""


def request_administrator_reset(email: str, portal: str = "staff") -> None:
    """Forgot password on Organization or Student sign-in: only the email. Always accepted (05 AUTH-2)."""
    request_password_reset(slug_for_email(email, portal), email, kind=portal)


def confirm_administrator_reset(email: str, code: str, new: str, portal: str = "staff") -> None:
    confirm_password_reset(slug_for_email(email, portal), email, code, new, kind=portal)


def _live_reset_code(db: Session, user_id: uuid.UUID) -> EmailOtp | None:
    return db.scalars(
        select(EmailOtp)
        .where(
            EmailOtp.user_id == user_id,
            EmailOtp.purpose == "password_reset",
            EmailOtp.consumed_at.is_(None),
            EmailOtp.cancelled_at.is_(None),
            EmailOtp.expires_at > func.now(),
        )
        .with_for_update()
    ).one_or_none()


def _live_token(db: Session, purpose: str, raw: str) -> AuthToken | None:
    return db.scalars(
        select(AuthToken)
        .where(
            AuthToken.token_hash == tokens.hash_secret(raw),
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
            AuthToken.revoked_at.is_(None),
            AuthToken.expires_at > func.now(),
        )
        .with_for_update()
    ).one_or_none()


def confirm_password_reset(slug: str, identifier: str, code: str, new: str, kind: str | None = None) -> None:
    """D77: the 6-digit code from the email plus a new password. A wrong code counts against the
    code (5 wrong -> cancelled) and against the IP; an unknown account or organization gets the
    same answer after the same Argon2 work (05 AUTH-2). A weak password is refused without using
    up the code. The failure is written, the transaction commits, and only then is it raised."""
    ip = request_state.client_ip.get() or "unknown"
    wait = ratelimit.RESET_CODE_FAILURES_PER_IP.retry_after(ip)
    if wait:
        otp.dummy_verify(code)
        raise too_many(wait)
    outcome: ProblemError | None = None
    with transaction(None) as db:
        org = _org_by_slug(db, slug)
        user = None
        if org is not None and org.status == "active":
            set_tenant(db, org.id)
            user = _reset_target(db, identifier, kind)
        code_row = _live_reset_code(db, user.id) if user is not None else None
        if org is None or user is None or code_row is None:
            otp.dummy_verify(code)
            outcome = reset_code_invalid()
        elif not otp.code_matches(code_row.code_hash, code):
            code_row.attempts += 1
            if code_row.attempts >= RESET_CODE_ATTEMPTS:
                code_row.cancelled_at = sessions.now()  # the database also insists (email_otps_cancelled_after_five)
            db.flush()
            outcome = reset_code_invalid()
        else:
            problems = passwords.check_new_password(new, context=_password_context(org, user, (_code_for(db, user),)))
            if problems:
                outcome = weak_password(problems)  # the code stays usable for a better password
            else:
                code_row.consumed_at = sessions.now()
                _set_user(
                    db, user.id, password_hash=passwords.hash_password(new), failed_login_count=0, locked_until=None
                )
                sessions.revoke(db, "password_changed", UserSession.user_id == user.id)  # every session (05 AUTH-9)
                audit.record(db, _actor(user, _roles(db, user.id)), "user.password_reset", "user", user.id)
                notifications.notify(
                    db,
                    org_id=org.id,
                    user_id=user.id,
                    category="security",
                    title="Your password was reset",
                    body="If this wasn't you, contact your administrator immediately.",
                )
    if outcome is not None:
        if outcome.code == "invalid_reset_code":
            ratelimit.RESET_CODE_FAILURES_PER_IP.hit(ip)
        raise outcome


# --- organization self-registration (04 §5.2) -----------------------------------------------------


def slug_problem(slug: str) -> str | None:
    if not SLUG_RE.match(slug):
        return "3 to 40 characters: lower-case letters, digits and hyphens, not starting or ending with a hyphen"
    if slug in RESERVED_SLUGS:
        return "This address is reserved"
    return None


def slug_available(slug: str) -> tuple[bool, str | None]:
    problem = slug_problem(slug)
    if problem:
        return False, problem
    with transaction(None) as db:
        taken = _org_by_slug(db, slug) is not None
    return (False, "This address is taken") if taken else (True, None)


def register_organization(
    *, name: str, slug: str, contact_email: str, admin_name: str, admin_email: str, password: str
) -> None:
    ratelimit.REGISTRATION_PER_IP.limit = get_settings().hms_registrations_per_ip_hour
    wait = ratelimit.REGISTRATION_PER_IP.take(request_state.client_ip.get() or "unknown")
    if wait:
        raise too_many(wait)
    slug = slug.strip().lower()
    problem = slug_problem(slug)
    if problem:
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "organization.slug", "code": "invalid_slug", "message": problem}],
        )
    words = (name, slug, *admin_name.split(), admin_email.split("@")[0])
    problems = passwords.check_new_password(password, context=words)
    if problems:
        raise weak_password(problems)

    with transaction(None) as db:
        org = Organization(slug=slug, name=name.strip())  # pending_verification (RLS insert rule)
        db.add(org)
        db.flush()  # a taken slug fails here: organizations_slug_key -> 409 slug_taken
        set_tenant(db, org.id)
        db.add(OrganizationProfile(org_id=org.id, contact_email=contact_email))
        db.add(OrgCounter(org_id=org.id, name="employee_code", scope_key=EMPLOYEE_COUNTER_SCOPE, last_value=1))
        user = User(
            org_id=org.id,
            kind="staff",
            full_name=admin_name.strip(),
            email=admin_email,
            password_hash=passwords.hash_password(password),
            status="pending_activation",
        )
        db.add(user)
        db.flush()
        db.add(StaffMember(org_id=org.id, user_id=user.id, employee_code="E-0001"))  # Q85
        db.add(RoleAssignment(org_id=org.id, user_id=user.id, role=SUPER_ADMIN))
        db.flush()
        audit.record(
            db,
            _actor(user, (RoleGrant(SUPER_ADMIN, None),)),
            "organization.registered",
            "organization",
            org.id,
            after={"name": org.name, "slug": slug, "status": org.status},
        )
        jobs.enqueue(
            db, "email.org_registration", {"user_id": str(user.id)}, org_id=org.id, priority=jobs.PRIORITY_SECURITY
        )


def verify_registration(slug: str, raw_token: str) -> AuthResult:
    with transaction(None) as db:
        org = _org_by_slug(db, slug)
        if org is None or org.status != "pending_verification":
            raise link_expired()
        set_tenant(db, org.id)
        token = _live_token(db, "org_registration", raw_token)
        user = db.get(User, token.user_id) if token else None
        if token is None or user is None:
            raise link_expired()
        token.used_at = sessions.now()
        db.execute(
            update(Organization)
            .where(Organization.id == org.id)
            .values(
                status="active", email_verified_at=func.now(), updated_at=func.now(), version=Organization.version + 1
            )
        )
        _set_user(db, user.id, status="active", email_verified_at=func.now())
        grants = _roles(db, user.id)
        audit.record(
            db,
            _actor(user, grants),
            "organization.verified",
            "organization",
            org.id,
            before={"status": "pending_verification"},
            after={"status": "active"},
        )
        # The confirmed email signs the new Administrator in (D87: no authenticator to set up).
        return AuthResult("authenticated", user, issued=sessions.start(db, org_id=org.id, user_id=user.id))


# --- activation (04 §5.7) ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivationPreview:
    full_name: str
    expires_at: Any


def _activation_target(db: Session, slug: str, raw_token: str) -> tuple[Organization, AuthToken, User]:
    org = _org_by_slug(db, slug)
    if org is None or org.status != "active":
        raise link_expired()
    set_tenant(db, org.id)
    token = _live_token(db, "activation", raw_token)
    user = db.get(User, token.user_id) if token else None
    if token is None or user is None or user.status != "pending_activation":
        raise link_expired()
    return org, token, user


def preview_activation(slug: str, raw_token: str) -> ActivationPreview:
    with transaction(None) as db:
        _, token, user = _activation_target(db, slug, raw_token)
        return ActivationPreview(user.full_name, token.expires_at)


def activate(slug: str, raw_token: str, password: str) -> None:
    """Set the first password. No automatic sign-in (04 §5.7): the person signs in normally."""
    with transaction(None) as db:
        org, token, user = _activation_target(db, slug, raw_token)
        grants = _roles(db, user.id)
        problems = passwords.check_new_password(password, context=_password_context(org, user, (_code_for(db, user),)))
        if problems:
            raise weak_password(problems)
        token.used_at = sessions.now()
        values: dict[str, Any] = {"status": "active", "password_hash": passwords.hash_password(password)}
        if token.delivery == "email":
            values["email_verified_at"] = func.now()  # the link proved the address works
        _set_user(db, user.id, **values)
        audit.record(
            db,
            _actor(user, grants),
            "account.activated",
            "user",
            user.id,
            after={"status": "active", "delivery": token.delivery},
        )
