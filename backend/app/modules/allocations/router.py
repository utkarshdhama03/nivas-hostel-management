"""Bed allocation routes (docs/04 §12.8). The student's "my room" route was removed by D79."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select, text

from app.core.context import OrgContext, require, staff_only, student_only
from app.core.crud import get_scoped, not_found
from app.core.permissions import ALLOCATIONS_MANAGE, HOSTEL_VIEW, STUDENTS_VIEW
from app.core.schemas import Out, Page, PageInfo, PageNumber, PageSize, Strict
from app.modules.allocations import service
from app.modules.allocations.models import BedAllocation
from app.modules.hostels.models import Bed, Room
from app.modules.identity.models import User
from app.modules.students import service as students
from app.modules.students.models import Student

router = APIRouter(prefix="/admin", tags=["allocations"], dependencies=[Depends(staff_only)])
portal = APIRouter(prefix="/portal/me", tags=["portal"], dependencies=[Depends(student_only)])
View = Annotated[OrgContext, Depends(require(STUDENTS_VIEW))]
Manage = Annotated[OrgContext, Depends(require(ALLOCATIONS_MANAGE))]
BedView = Annotated[OrgContext, Depends(require(HOSTEL_VIEW))]


class AllocationOut(Out):
    id: uuid.UUID
    hostel_id: uuid.UUID
    bed_id: uuid.UUID
    student_id: uuid.UUID
    status: str
    start_date: date
    end_date: date | None
    reservation_expires_at: datetime | None
    checked_in_at: datetime | None
    checked_out_at: datetime | None
    end_reason: str | None
    previous_allocation_id: uuid.UUID | None
    created_at: datetime


class AllocationListItem(AllocationOut):
    student_code: str
    student_name: str
    bed: str


class AllocateIn(Strict):
    student_id: uuid.UUID
    bed_id: uuid.UUID
    mode: Literal["assign", "reserve"] = "assign"
    start_date: date | None = None
    end_date: date | None = None
    reservation_expires_at: datetime | None = None


class TransferIn(Strict):
    bed_id: uuid.UUID


BED_LABEL = (
    "SELECT bu.name || '-' || r.number || '/' || b.label FROM beds b JOIN rooms r ON r.id = b.room_id "
    "JOIN floors f ON f.id = r.floor_id JOIN buildings bu ON bu.id = f.building_id WHERE b.id = :b"
)


@router.get("/allocations")
def list_allocations(
    ctx: View,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    hostel_id: uuid.UUID | None = None,
    status: Literal["reserved", "active", "ended", "cancelled"] | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
) -> Page[AllocationListItem]:
    hostels = ctx.principal.hostels_for(STUDENTS_VIEW)
    query = (
        select(BedAllocation, Student.student_code, User.full_name)
        .join(Student, Student.id == BedAllocation.student_id)
        .join(User, User.id == Student.user_id)
    )
    if hostels is not None:
        query = query.where(BedAllocation.hostel_id.in_(hostels))
    if hostel_id is not None:
        if hostels is not None and hostel_id not in hostels:
            raise not_found()
        query = query.where(BedAllocation.hostel_id == hostel_id)
    if status is not None:
        query = query.where(BedAllocation.status == status)
    if q:  # the List view's search: student name, Student ID or room number (the bed map's companion)
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                User.full_name.ilike(like),
                Student.student_code.ilike(like),
                BedAllocation.bed_id.in_(
                    select(Bed.id).join(Room, Room.id == Bed.room_id).where(Room.number.ilike(like))
                ),
            )
        )
    total = ctx.db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = ctx.db.execute(
        query.order_by(BedAllocation.created_at.desc(), BedAllocation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        AllocationListItem.model_validate(
            {
                **AllocationOut.model_validate(a).model_dump(),
                "student_code": code,
                "student_name": name,
                "bed": ctx.db.scalar(text(BED_LABEL), {"b": a.bed_id}),
            }
        )
        for a, code, name in rows
    ]
    return Page[AllocationListItem](
        items=items,
        page=PageInfo(number=page, size=page_size, total_items=total, total_pages=max(1, -(-total // page_size))),
    )


@router.post("/allocations", status_code=status.HTTP_201_CREATED)
def create_allocation(ctx: Manage, body: AllocateIn) -> AllocationOut:
    a = service.allocate(
        ctx.db,
        ctx.principal,
        student_id=body.student_id,
        bed_id=body.bed_id,
        mode=body.mode,
        start_date=body.start_date,
        end_date=body.end_date,
        reservation_expires_at=body.reservation_expires_at,
    )
    return AllocationOut.model_validate(a)


@router.post("/allocations/{allocation_id}/check-in")
def check_in(ctx: Manage, allocation_id: uuid.UUID) -> AllocationOut:
    return AllocationOut.model_validate(service.check_in(ctx.db, ctx.principal, allocation_id))


@router.post("/allocations/{allocation_id}/check-out")
def check_out(ctx: Manage, allocation_id: uuid.UUID) -> AllocationOut:
    return AllocationOut.model_validate(service.check_out(ctx.db, ctx.principal, allocation_id))


@router.post("/allocations/{allocation_id}/cancel")
def cancel(ctx: Manage, allocation_id: uuid.UUID) -> AllocationOut:
    return AllocationOut.model_validate(service.cancel(ctx.db, ctx.principal, allocation_id))


@router.post("/allocations/{allocation_id}/transfer", status_code=status.HTTP_201_CREATED)
def transfer(ctx: Manage, allocation_id: uuid.UUID, body: TransferIn) -> AllocationOut:
    return AllocationOut.model_validate(service.transfer(ctx.db, ctx.principal, allocation_id, body.bed_id))


@router.get("/students/{student_id}/allocations")
def student_allocations(ctx: View, student_id: uuid.UUID) -> list[AllocationOut]:
    s = students.get_student(ctx.db, ctx.principal, student_id)
    rows = ctx.db.scalars(
        select(BedAllocation)
        .where(BedAllocation.student_id == s.id)
        .order_by(BedAllocation.start_date.desc(), BedAllocation.created_at.desc())
    )
    return [AllocationOut.model_validate(a) for a in rows]


@router.get("/beds/{bed_id}/allocations")
def bed_allocations(ctx: BedView, bed_id: uuid.UUID) -> list[AllocationOut]:
    """ALC-3: the history of one bed."""
    bed = get_scoped(ctx.db, Bed, bed_id, ctx.principal, HOSTEL_VIEW)
    rows = ctx.db.scalars(
        select(BedAllocation)
        .where(BedAllocation.bed_id == bed.id)
        .order_by(BedAllocation.start_date.desc(), BedAllocation.created_at.desc())
    )
    return [AllocationOut.model_validate(a) for a in rows]
