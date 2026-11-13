"""Authentication and /me routes (docs/04 §5, §12.3). Thin: all decisions live in service.py.

Four routers, one per audience (04 §2):
  public_router    /public/...            anyone
  org_auth_router  /o/{org_slug}/auth/... before login; the slug picks the organization
  auth_router      /auth/...              sign-in by email, refresh cookie or access token
  me_router        /me                    signed-in organization users
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from app.core import audit, ratelimit, request_state, tokens
from app.core.context import Ctx, PublicDb
from app.core.crud import check_version
from app.core.csrf import require_csrf
from app.core.errors import ProblemError
from app.modules.identity import schemas as s
from app.modules.identity import service, sessions
from app.modules.identity.models import User
from app.modules.organizations.models import Organization

public_router = APIRouter(prefix="/public", tags=["public"])
org_auth_router = APIRouter(prefix="/o/{org_slug}/auth", tags=["auth"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])


def _auth_out(result: service.AuthResult, response: Response) -> s.AuthOut:
    out = s.AuthOut(
        status=result.status,  # type: ignore[arg-type]
        user=s.UserBrief(id=result.user.id, full_name=result.user.full_name, kind=result.user.kind),
    )
    if result.issued is not None:
        out.access_token = sessions.access_token(result.issued.row)
        out.token_type = "bearer"  # noqa: S105 - the OAuth token type name, not a secret
        out.expires_in = int(tokens.ACCESS_TTL.total_seconds())
        sessions.set_cookie(response, result.issued)
    return out


# --- public -------------------------------------------------------------------------------------


@public_router.get("/organizations/{org_slug}")
def public_organization(org_slug: str, db: PublicDb) -> s.PublicOrgOut:
    """Name and status for the login page. Pending (unverified) organizations don't exist yet."""
    org = db.scalars(select(Organization).where(Organization.slug == org_slug.lower())).one_or_none()
    if org is None or org.status == "pending_verification":
        raise ProblemError(404, "not_found", "Not found")
    return s.PublicOrgOut(slug=org.slug, name=org.name, status=org.status)  # type: ignore[arg-type]


@public_router.get("/slug-availability")
def slug_availability(slug: str) -> s.SlugAvailabilityOut:
    wait = ratelimit.SLUG_CHECKS_PER_IP.take(request_state.client_ip.get() or "unknown")
    if wait:
        raise service.too_many(wait)
    available, reason = service.slug_available(slug.strip().lower())
    return s.SlugAvailabilityOut(available=available, reason=reason)


@public_router.post("/organizations", status_code=status.HTTP_202_ACCEPTED)
def register(body: s.RegistrationIn) -> s.AcceptedOut:
    service.register_organization(
        name=body.organization.name,
        slug=body.organization.slug,
        contact_email=str(body.organization.contact_email),
        admin_name=body.super_admin.full_name,
        admin_email=str(body.super_admin.email),
        password=body.super_admin.password,
    )
    return s.AcceptedOut()


# --- before login, inside one organization ------------------------------------------------------


@org_auth_router.post("/verify-registration", response_model_exclude_none=True)
def verify_registration(org_slug: str, body: s.TokenIn, response: Response) -> s.AuthOut:
    return _auth_out(service.verify_registration(org_slug, body.token), response)


@auth_router.post("/login", response_model_exclude_none=True)
def login_administrator(body: s.AdministratorLoginIn, response: Response) -> s.AuthOut:
    """D78/D80: Organization or Student sign-in with only the email and the password."""
    return _auth_out(service.login_administrator(body.email, body.password, body.portal), response)


@auth_router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def administrator_reset_request(body: s.AdministratorResetRequestIn) -> s.AcceptedOut:
    service.request_administrator_reset(body.email, body.portal)
    return s.AcceptedOut()


@auth_router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def administrator_reset_confirm(body: s.AdministratorResetConfirmIn) -> None:
    service.confirm_administrator_reset(body.email, body.code, body.new_password, body.portal)


@org_auth_router.post("/login", response_model_exclude_none=True)
def login(org_slug: str, body: s.LoginIn, response: Response) -> s.AuthOut:
    return _auth_out(service.login(org_slug, body.portal, body.identifier, body.password), response)


@org_auth_router.post("/activate/preview")
def activation_preview(org_slug: str, body: s.TokenIn) -> s.ActivationPreviewOut:
    preview = service.preview_activation(org_slug, body.token)
    return s.ActivationPreviewOut(full_name=preview.full_name, expires_at=preview.expires_at)


@org_auth_router.post("/activate", status_code=status.HTTP_204_NO_CONTENT)
def activate(org_slug: str, body: s.ActivateIn) -> None:
    service.activate(org_slug, body.token, body.password)


@org_auth_router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(org_slug: str, body: s.ResetRequestIn) -> s.AcceptedOut:
    service.request_password_reset(org_slug, body.identifier)
    return s.AcceptedOut()


@org_auth_router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(org_slug: str, body: s.ResetConfirmIn) -> None:
    service.confirm_password_reset(org_slug, body.identifier, body.code, body.new_password)


# --- refresh and logout (the only cookie endpoints) ---------------------------------------------


@auth_router.post("/refresh", response_model_exclude_none=True)
def refresh(request: Request, response: Response) -> s.AuthOut:
    require_csrf(request)
    return _auth_out(service.refresh(request.cookies.get(sessions.REFRESH_COOKIE)), response)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    require_csrf(request)
    service.logout(request.cookies.get(sessions.REFRESH_COOKIE))
    sessions.clear_cookie(response)


# --- sessions and password (signed in) ----------------------------------------------------------


@auth_router.get("/sessions")
def list_sessions(ctx: Ctx) -> s.SessionsOut:
    current = ctx.principal.session_id
    return s.SessionsOut(
        sessions=[
            s.SessionOut(
                id=r.id,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                ip_address=str(r.ip_address) if r.ip_address else None,
                user_agent=r.user_agent,
                current=r.id == current,
            )
            for r in service.list_sessions(ctx.db, ctx.principal)
        ]
    )


@auth_router.post("/sessions/{session_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(ctx: Ctx, session_id: uuid.UUID) -> None:
    service.revoke_session(ctx.db, ctx.principal, session_id)


@auth_router.post("/sessions/revoke-others")
def revoke_other_sessions(ctx: Ctx) -> s.RevokedOut:
    return s.RevokedOut(revoked=service.revoke_other_sessions(ctx.db, ctx.principal))


@auth_router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT)
def change_password(ctx: Ctx, body: s.PasswordChangeIn) -> None:
    service.change_password(ctx.db, ctx.principal, body.current_password, body.new_password)


# --- me -----------------------------------------------------------------------------------------


def _me_out(ctx: Ctx) -> s.MeOut:
    user = ctx.db.get(User, ctx.principal.user_id)
    org = ctx.db.get(Organization, ctx.principal.org_id)
    assert user is not None and org is not None
    return s.MeOut(
        id=user.id,
        version=user.version,
        kind=user.kind,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        status=user.status,
        code=service._code_for(ctx.db, user),
        organization=s.OrgBrief(id=org.id, slug=org.slug, name=org.name, timezone=org.timezone),
        roles=[s.RoleOut(role=g.role, hostel_id=g.hostel_id) for g in ctx.principal.grants],
        permissions=sorted(ctx.principal.permissions()),
    )


@me_router.get("")
def me(ctx: Ctx) -> s.MeOut:
    return _me_out(ctx)


@me_router.patch("")
def patch_me(ctx: Ctx, body: s.MePatch) -> s.MeOut:
    """Q46: people may change their own phone number; everything else is admin-only."""
    user = ctx.db.scalars(select(User).where(User.id == ctx.principal.user_id).with_for_update()).one()
    check_version(user, body.version)
    before = {"phone": user.phone}
    user.phone = body.phone
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "user.updated", "user", user.id, before=before, after={"phone": body.phone})
    return _me_out(ctx)
