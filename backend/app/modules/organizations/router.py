"""Organization settings and the academic calendar (docs/04 §12.4). Super Admin only."""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Annotated
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, status
from pydantic import AfterValidator, EmailStr, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import audit
from app.core.context import OrgContext, require, staff_only
from app.core.crud import apply_patch, check_version, not_found
from app.core.errors import ProblemError
from app.core.permissions import ORG_MANAGE
from app.core.schemas import Out, Strict, Versioned
from app.modules.organizations.models import AcademicSession, AcademicTerm, Organization, OrganizationProfile

router = APIRouter(prefix="/admin", tags=["organization"], dependencies=[Depends(staff_only)])
Admin = Annotated[OrgContext, Depends(require(ORG_MANAGE))]


def _timezone(value: str) -> str:
    if value not in available_timezones():
        raise ValueError("unknown timezone (use an IANA name such as Asia/Kolkata)")
    return value


def _upi(value: str | None) -> str | None:
    if value is not None and not re.fullmatch(r"[A-Za-z0-9._-]{2,255}@[A-Za-z]{2,64}", value):
        raise ValueError("a UPI ID looks like name@bank")
    return value


Timezone = Annotated[str, AfterValidator(_timezone)]
Phone = Annotated[str, Field(pattern=r"^\+?[0-9][0-9 -]{6,19}$")]


class ProfileOut(Out):
    legal_name: str | None
    contact_email: str
    contact_phone: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    upi_id: str | None
    upi_payee_name: str | None
    accepts_student_requests: bool


class OrganizationOut(Out):
    id: uuid.UUID
    slug: str
    name: str
    timezone: str
    status: str
    version: int
    profile: ProfileOut


class OrganizationPatch(Versioned):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    timezone: Timezone | None = None
    legal_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: Phone | None = None
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    upi_id: Annotated[str | None, AfterValidator(_upi)] = None
    upi_payee_name: str | None = Field(default=None, max_length=100)
    accepts_student_requests: bool | None = None  # D74: students may ask to register


ORG_FIELDS = {"name", "timezone"}


def _org_out(org: Organization, profile: OrganizationProfile) -> OrganizationOut:
    return OrganizationOut(
        id=org.id,
        slug=org.slug,
        name=org.name,
        timezone=org.timezone,
        status=org.status,
        version=org.version,
        profile=ProfileOut.model_validate(profile),
    )


@router.get("/organization")
def get_organization(ctx: Admin) -> OrganizationOut:
    org = ctx.db.get(Organization, ctx.principal.org_id)
    profile = ctx.db.get(OrganizationProfile, ctx.principal.org_id)
    assert org is not None and profile is not None
    return _org_out(org, profile)


@router.patch("/organization")
def patch_organization(ctx: Admin, body: OrganizationPatch) -> OrganizationOut:
    org = ctx.db.scalars(select(Organization).where(Organization.id == ctx.principal.org_id).with_for_update()).one()
    profile = ctx.db.get(OrganizationProfile, org.id)
    assert profile is not None
    check_version(org, body.version)
    sent = body.model_dump(exclude_unset=True, exclude={"version"})
    if "upi_id" in sent and sent["upi_id"] and not (sent.get("upi_payee_name") or profile.upi_payee_name):
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[
                {
                    "field": "upi_payee_name",
                    "code": "required_with_upi",
                    "message": "Add the payee name shown to students",
                }
            ],
        )
    before = {k: getattr(org if k in ORG_FIELDS else profile, k) for k in sent}
    for key, value in sent.items():
        setattr(org if key in ORG_FIELDS else profile, key, value)
    org.updated_at = func.app_now()  # always touch the organization row: one version for the whole record
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "organization.updated", "organization", org.id, before=before, after=sent)
    ctx.db.refresh(org)
    ctx.db.refresh(profile)
    return _org_out(org, profile)


# --- academic calendar ------------------------------------------------------------------------------


class TermOut(Out):
    id: uuid.UUID
    name: str
    starts_on: date
    ends_on: date
    version: int


class AcademicSessionOut(Out):
    id: uuid.UUID
    name: str
    starts_on: date
    ends_on: date
    is_current: bool
    version: int
    terms: list[TermOut] = []


class AcademicSessionIn(Strict):
    name: str = Field(min_length=2, max_length=40)
    starts_on: date
    ends_on: date


class AcademicSessionPatch(Versioned):
    name: str | None = Field(default=None, min_length=2, max_length=40)
    starts_on: date | None = None
    ends_on: date | None = None


class TermIn(Strict):
    name: str = Field(min_length=2, max_length=40)
    starts_on: date
    ends_on: date


def _dates_ok(starts: date, ends: date) -> None:
    if ends <= starts:
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "ends_on", "code": "before_start", "message": "Must be after the start"}],
        )


def _session_out(db: Session, s: AcademicSession) -> AcademicSessionOut:
    terms = db.scalars(
        select(AcademicTerm).where(AcademicTerm.academic_session_id == s.id).order_by(AcademicTerm.starts_on)
    ).all()
    return AcademicSessionOut(
        id=s.id,
        name=s.name,
        starts_on=s.starts_on,
        ends_on=s.ends_on,
        is_current=s.is_current,
        version=s.version,
        terms=[TermOut.model_validate(t) for t in terms],
    )


def _session(ctx: Admin, session_id: uuid.UUID, lock: bool = False) -> AcademicSession:
    q = select(AcademicSession).where(AcademicSession.id == session_id)
    s = ctx.db.scalars(q.with_for_update() if lock else q).one_or_none()
    if s is None:
        raise not_found()
    return s


@router.get("/academic-sessions")
def list_sessions(ctx: Admin) -> list[AcademicSessionOut]:
    rows = ctx.db.scalars(select(AcademicSession).order_by(AcademicSession.starts_on.desc())).all()
    return [_session_out(ctx.db, s) for s in rows]


@router.post("/academic-sessions", status_code=status.HTTP_201_CREATED)
def create_session(ctx: Admin, body: AcademicSessionIn) -> AcademicSessionOut:
    _dates_ok(body.starts_on, body.ends_on)
    s = AcademicSession(org_id=ctx.principal.org_id, **body.model_dump())
    ctx.db.add(s)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "academic_session.created", "academic_session", s.id, after=body.model_dump())
    return _session_out(ctx.db, s)


@router.patch("/academic-sessions/{session_id}")
def patch_session(ctx: Admin, session_id: uuid.UUID, body: AcademicSessionPatch) -> AcademicSessionOut:
    s = _session(ctx, session_id, lock=True)
    check_version(s, body.version)
    changes = apply_patch(ctx.db, s, body)
    _dates_ok(s.starts_on, s.ends_on)
    ctx.db.flush()
    audit.record(
        ctx.db,
        ctx.principal,
        "academic_session.updated",
        "academic_session",
        s.id,
        before={k: v[0] for k, v in changes.items()},
        after={k: v[1] for k, v in changes.items()},
    )
    return _session_out(ctx.db, s)


@router.post("/academic-sessions/{session_id}/make-current")
def make_current(ctx: Admin, session_id: uuid.UUID) -> AcademicSessionOut:
    s = _session(ctx, session_id, lock=True)
    # Two statements, in this order, because the database allows only one current session.
    ctx.db.execute(
        update(AcademicSession)
        .where(AcademicSession.is_current, AcademicSession.id != s.id)
        .values(is_current=False, version=AcademicSession.version + 1)
    )
    if not s.is_current:
        s.is_current = True
        ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "academic_session.made_current", "academic_session", s.id)
    return _session_out(ctx.db, s)


@router.get("/academic-sessions/{session_id}/terms")
def list_terms(ctx: Admin, session_id: uuid.UUID) -> list[TermOut]:
    return _session_out(ctx.db, _session(ctx, session_id)).terms


@router.post("/academic-sessions/{session_id}/terms", status_code=status.HTTP_201_CREATED)
def create_term(ctx: Admin, session_id: uuid.UUID, body: TermIn) -> TermOut:
    s = _session(ctx, session_id)
    _dates_ok(body.starts_on, body.ends_on)
    if body.starts_on < s.starts_on or body.ends_on > s.ends_on:
        raise ProblemError(
            422,
            "validation_error",
            "Invalid request",
            errors=[{"field": "starts_on", "code": "outside_session", "message": "A term must lie inside its session"}],
        )
    t = AcademicTerm(org_id=ctx.principal.org_id, academic_session_id=s.id, **body.model_dump())
    ctx.db.add(t)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "academic_term.created", "academic_term", t.id, after=body.model_dump())
    return TermOut.model_validate(t)
