"""The organization's Administrator (docs/04 §12.6; D75 one role, D76 exactly one Administrator).

The one Administrator is created when the organization registers. Nobody can add another, nor
deactivate or re-activate a staff account here: the list shows the Administrator (and staff accounts
closed by D75/D76) for reference, and the Administrator may edit their own details. A lost
authenticator is recovered through the Platform Owner's reset (Q16)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.context import OrgContext, require, staff_only
from app.core.crud import check_version, not_found
from app.core.crud import page as paginate
from app.core.permissions import STAFF_MANAGE
from app.core.schemas import Out, Page, PageNumber, PageSize, Versioned
from app.modules.identity.models import StaffMember, User
from app.modules.staff import service

router = APIRouter(prefix="/admin", tags=["staff"], dependencies=[Depends(staff_only)])
Manage = Annotated[OrgContext, Depends(require(STAFF_MANAGE))]
Phone = Field(default=None, pattern=r"^\+?[0-9][0-9 -]{6,19}$")


class StaffOut(Out):
    id: uuid.UUID
    user_id: uuid.UUID
    employee_code: str
    full_name: str
    email: str | None
    phone: str | None
    status: str
    designation: str | None
    joined_on: date | None
    left_on: date | None
    version: int


class StaffPatch(Versioned):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Phone
    employee_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9/-]{1,29}$")
    designation: str | None = Field(default=None, max_length=80)
    joined_on: date | None = None
    left_on: date | None = None


USER_FIELDS = {"full_name", "email", "phone"}


def _out(db: Session, staff: StaffMember, user: User) -> StaffOut:
    return StaffOut(
        id=staff.id,
        user_id=user.id,
        employee_code=staff.employee_code,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        status=user.status,
        designation=staff.designation,
        joined_on=staff.joined_on,
        left_on=staff.left_on,
        version=staff.version,
    )


@router.get("/staff")
def list_staff(
    ctx: Manage,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    q: str | None = None,
    status: Literal["pending_activation", "active", "deactivated"] | None = None,
) -> Page[StaffOut]:
    query = select(StaffMember).join(User, User.id == StaffMember.user_id)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            or_(User.full_name.ilike(like), StaffMember.employee_code.ilike(like), User.email.ilike(like))
        )
    if status is not None:
        query = query.where(User.status == status)
    items, info = paginate(ctx.db, query.order_by(func.lower(User.full_name), StaffMember.id), page, page_size)
    return Page[StaffOut](items=[_out(ctx.db, s, ctx.db.get(User, s.user_id)) for s in items], page=info)  # type: ignore[arg-type]


@router.get("/staff/{staff_id}")
def get_staff(ctx: Manage, staff_id: uuid.UUID) -> StaffOut:
    staff, user = service.get_staff(ctx.db, ctx.principal, staff_id)
    return _out(ctx.db, staff, user)


@router.patch("/staff/{staff_id}")
def patch_staff(ctx: Manage, staff_id: uuid.UUID, body: StaffPatch) -> StaffOut:
    staff, user = service.get_staff(ctx.db, ctx.principal, staff_id, lock=True)
    check_version(staff, body.version)
    sent = body.model_dump(exclude_unset=True, exclude={"version"})
    if "employee_code" in sent and sent["employee_code"]:
        sent["employee_code"] = sent["employee_code"].upper()
    before = {k: getattr(user if k in USER_FIELDS else staff, k) for k in sent}
    for key, value in sent.items():
        setattr(user if key in USER_FIELDS else staff, key, value)
    staff.updated_at = func.app_now()  # one version for the whole staff record (users + staff_members)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "staff.updated", "user", user.id, before=before, after=sent)
    ctx.db.refresh(staff)
    ctx.db.refresh(user)
    return _out(ctx.db, staff, user)


def _user_in_scope(ctx: Manage, user_id: uuid.UUID) -> User:
    """A student or staff account of this organization (RLS: another organization's user is 404)."""
    user = ctx.db.get(User, user_id)
    if user is None:
        raise not_found()
    return user


@router.post("/users/{user_id}/activation-email", status_code=status.HTTP_202_ACCEPTED)
def resend_activation_email(ctx: Manage, user_id: uuid.UUID) -> None:
    """D80: send a student's activation email again (the in-person copied link and the Q69
    reactivation for students without email are gone: every student has an email)."""
    service.resend_activation_email(ctx.db, ctx.principal, _user_in_scope(ctx, user_id))
