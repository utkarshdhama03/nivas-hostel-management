"""Platform Owner routes (docs/04 §5.9, §12.19): sign-in (email and password, D87) and /me."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import tokens
from app.core.context import bearer_token
from app.core.csrf import require_csrf
from app.core.db import transaction
from app.core.errors import ProblemError
from app.modules.platform import service
from app.modules.platform.models import PlatformSession, PlatformUser

auth_router = APIRouter(prefix="/platform/auth", tags=["platform"])
router = APIRouter(prefix="/platform", tags=["platform"])


# --- the platform request context (04 §4.4) -------------------------------------------------------


@dataclass(frozen=True)
class PlatformContext:
    db: Session
    platform_user_id: uuid.UUID
    session_id: uuid.UUID


def platform_context(request: Request) -> Iterator[PlatformContext]:
    """Like org_context, but: platform audience and key, hms_platform role, NO tenant context."""
    try:
        claims = tokens.verify(bearer_token(request), audience="hms-platform", typ="access")
        user_id, sid = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except tokens.TokenExpired as exc:
        raise ProblemError(
            401, "token_expired", "Session token expired", headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    except (tokens.TokenError, KeyError, ValueError) as exc:
        raise ProblemError(
            401, "session_invalid", "Please sign in again", headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    with transaction(None, role="hms_platform") as db:
        ok = db.scalar(
            select(func.count())
            .select_from(PlatformSession)
            .join(PlatformUser, PlatformUser.id == PlatformSession.platform_user_id)
            .where(
                PlatformSession.id == sid,
                PlatformSession.platform_user_id == user_id,
                PlatformSession.revoked_at.is_(None),
                PlatformSession.expires_at > datetime.now(UTC),  # the clock that wrote it (D67)
                PlatformSession.absolute_expires_at > datetime.now(UTC),
                PlatformUser.status == "active",
            )
        )
        if not ok:
            raise ProblemError(401, "session_invalid", "Please sign in again", headers={"WWW-Authenticate": "Bearer"})
        yield PlatformContext(db, user_id, sid)


PCtx = Annotated[PlatformContext, Depends(platform_context, scope="function")]


# --- bodies ----------------------------------------------------------------------------------------


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlatformLoginIn(Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class PlatformAuthOut(BaseModel):
    status: Literal["authenticated"]
    access_token: str | None = None
    token_type: Literal["bearer"] | None = None
    expires_in: int | None = None


class PlatformMeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str


def _out(result: service.PlatformAuth, response: Response) -> PlatformAuthOut:
    service.set_cookie(response, result.session, result.secret)
    return PlatformAuthOut(
        status="authenticated",
        access_token=service.access_token(result.session),
        token_type="bearer",  # noqa: S106 - the OAuth token type name, not a secret
        expires_in=int(tokens.ACCESS_TTL.total_seconds()),
    )


# --- auth ------------------------------------------------------------------------------------------


@auth_router.post("/login", response_model_exclude_none=True)
def login(body: PlatformLoginIn, response: Response) -> PlatformAuthOut:
    return _out(service.login(str(body.email), body.password), response)


@auth_router.post("/refresh", response_model_exclude_none=True)
def refresh(request: Request, response: Response) -> PlatformAuthOut:
    require_csrf(request)
    return _out(service.refresh(request.cookies.get(service.COOKIE)), response)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    require_csrf(request)
    service.logout(request.cookies.get(service.COOKIE))
    service.clear_cookie(response)


# --- signed in -------------------------------------------------------------------------------------


@router.get("/me")
def platform_me(ctx: PCtx) -> PlatformMeOut:
    user = ctx.db.get(PlatformUser, ctx.platform_user_id)
    assert user is not None
    return PlatformMeOut(id=user.id, email=user.email, full_name=user.full_name)
