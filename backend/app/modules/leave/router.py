"""Leave routes (docs/04 §12.11 and the student's own, 04 §12.18; D75: no gate passes)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import AwareDatetime, Field
from sqlalchemy import func, select

from app.core.context import Ctx, OrgContext, require, staff_only, student_only
from app.core.crud import scope_query
from app.core.permissions import LEAVE_MANAGE
from app.core.schemas import Out, Page, PageInfo, PageNumber, PageSize, Strict
from app.modules.identity.models import User
from app.modules.leave import service
from app.modules.leave.models import LeaveRequest
from app.modules.students.models import Student

router = APIRouter(prefix="/admin", tags=["leave"], dependencies=[Depends(staff_only)])
portal = APIRouter(prefix="/portal/me", tags=["portal"], dependencies=[Depends(student_only)])
Leave = Annotated[OrgContext, Depends(require(LEAVE_MANAGE))]

Reason = Annotated[str, Field(min_length=3, max_length=500)]
Destination = Annotated[str | None, Field(default=None, max_length=200)]


class LeaveOut(Out):
    id: uuid.UUID
    hostel_id: uuid.UUID
    student_id: uuid.UUID
    leave_type: str
    reason: str
    destination: str | None
    starts_at: datetime
    ends_at: datetime
    status: str
    auto_approved: bool
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


class LeaveListItem(LeaveOut):
    student_code: str
    student_name: str


class LeaveIn(Strict):
    leave_type: Literal["home", "local_outing"]
    reason: Reason
    destination: Destination = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class EmergencyLeaveIn(Strict):
    student_id: uuid.UUID
    reason: Reason
    destination: Destination = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class LeaveDecisionIn(Strict):
    note: Annotated[str | None, Field(default=None, max_length=500)] = None


def _paged(page: int, size: int, total: int) -> PageInfo:
    return PageInfo(number=page, size=size, total_items=total, total_pages=max(1, -(-total // size)))


# --- Administrators ------------------------------------------------------------------------------


@router.get("/leave-requests")
def list_leave(
    ctx: Leave,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = "pending",
    hostel_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
) -> Page[LeaveListItem]:
    q = (
        select(LeaveRequest, Student.student_code, User.full_name)
        .join(Student, Student.id == LeaveRequest.student_id)
        .join(User, User.id == Student.user_id)
    )
    q = scope_query(q, LeaveRequest, ctx.principal, LEAVE_MANAGE)
    if hostel_id is not None:
        q = q.where(LeaveRequest.hostel_id == hostel_id)
    if student_id is not None:
        q = q.where(LeaveRequest.student_id == student_id)
    if status is not None:
        q = q.where(LeaveRequest.status == status)
    total = ctx.db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
    rows = ctx.db.execute(
        q.order_by(LeaveRequest.starts_at, LeaveRequest.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        LeaveListItem.model_validate(
            {**LeaveOut.model_validate(leave).model_dump(), "student_code": code, "student_name": name}
        )
        for leave, code, name in rows
    ]
    return Page[LeaveListItem](items=items, page=_paged(page, page_size, total))


@router.post("/leave-requests", status_code=status.HTTP_201_CREATED)
def emergency_leave(ctx: Leave, body: EmergencyLeaveIn) -> LeaveOut:
    leave = service.create_emergency_leave(
        ctx.db,
        ctx.principal,
        student_id=body.student_id,
        reason=body.reason,
        destination=body.destination,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
    )
    return LeaveOut.model_validate(leave)


@router.post("/leave-requests/{leave_id}/approve")
def approve_leave(ctx: Leave, leave_id: uuid.UUID, body: LeaveDecisionIn) -> LeaveOut:
    return LeaveOut.model_validate(service.decide_leave(ctx.db, ctx.principal, leave_id, approve=True, note=body.note))


@router.post("/leave-requests/{leave_id}/reject")
def reject_leave(ctx: Leave, leave_id: uuid.UUID, body: LeaveDecisionIn) -> LeaveOut:
    return LeaveOut.model_validate(service.decide_leave(ctx.db, ctx.principal, leave_id, approve=False, note=body.note))


# --- the student ---------------------------------------------------------------------------------


@portal.get("/leave-requests")
def my_leave(ctx: Ctx) -> list[LeaveOut]:
    s = service.student_of_user(ctx.db, ctx.principal.user_id)
    rows = ctx.db.scalars(
        select(LeaveRequest).where(LeaveRequest.student_id == s.id).order_by(LeaveRequest.starts_at.desc()).limit(100)
    )
    return [LeaveOut.model_validate(leave) for leave in rows]


@portal.post("/leave-requests", status_code=status.HTTP_201_CREATED)
def request_leave(ctx: Ctx, body: LeaveIn) -> LeaveOut:
    leave = service.request_leave(
        ctx.db,
        ctx.principal,
        leave_type=body.leave_type,
        reason=body.reason,
        destination=body.destination,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
    )
    return LeaveOut.model_validate(leave)


@portal.post("/leave-requests/{leave_id}/cancel")
def cancel_leave(ctx: Ctx, leave_id: uuid.UUID) -> LeaveOut:
    return LeaveOut.model_validate(service.cancel_leave(ctx.db, ctx.principal, leave_id))
