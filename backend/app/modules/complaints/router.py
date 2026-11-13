"""Complaints (docs/04 §12.12; 01 CMP; Q40).

Lifecycle: open -> in_progress (assigned) -> resolved (with a note) -> closed. A student can reopen
within 7 days of resolution (back to open); after that the auto-close job closes it. Every change
and comment is a row in complaint_updates (append-only). Complaints are never anonymous and are
visible only to the submitter and the hostel's staff who manage complaints (Q40). Priority sets a
target date; there is no automatic escalation in v1.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import audit
from app.core.context import Ctx, OrgContext, require, staff_only, student_only
from app.core.crud import apply_patch, check_hostel, check_version, not_found, scope_query
from app.core.errors import ProblemError
from app.core.permissions import COMPLAINTS_MANAGE, ORG_MANAGE, Principal
from app.core.schemas import Out, Page, PageInfo, PageNumber, PageSize, Strict, Versioned
from app.modules.complaints.models import Complaint, ComplaintAttachment, ComplaintCategory, ComplaintUpdate
from app.modules.files.models import File
from app.modules.files.service import unattached_clause
from app.modules.identity.models import User
from app.modules.leave.service import administrators_of, org_zone, student_of_user
from app.modules.notifications import service as notifications
from app.modules.staff.service import is_active_administrator
from app.modules.students.models import Student

router = APIRouter(prefix="/admin", tags=["complaints"], dependencies=[Depends(staff_only)])
portal = APIRouter(prefix="/portal/me", tags=["portal"], dependencies=[Depends(student_only)])
Manage = Annotated[OrgContext, Depends(require(COMPLAINTS_MANAGE))]
Categories = Annotated[OrgContext, Depends(require(ORG_MANAGE))]

REOPEN_DAYS = 7  # Q40
TARGET_DAYS = {"urgent": 1, "high": 3, "medium": 7, "low": 14}  # decision D51
Priority = Literal["low", "medium", "high", "urgent"]


class CategoryOut(Out):
    id: uuid.UUID
    version: int
    name: str
    is_active: bool


class CategoryIn(Strict):
    name: Annotated[str, Field(min_length=2, max_length=60)]


class CategoryPatch(Versioned):
    name: Annotated[str | None, Field(default=None, min_length=2, max_length=60)] = None
    is_active: bool | None = None


class ComplaintUpdateOut(Out):
    id: uuid.UUID
    author_id: uuid.UUID
    from_status: str | None
    to_status: str | None
    comment: str | None
    created_at: datetime


class ComplaintOut(Out):
    id: uuid.UUID
    version: int
    hostel_id: uuid.UUID
    student_id: uuid.UUID
    room_id: uuid.UUID | None
    category_id: uuid.UUID
    title: str
    description: str
    priority: str
    status: str
    target_date: date
    assigned_to: uuid.UUID | None
    resolved_at: datetime | None
    resolution_note: str | None
    closed_at: datetime | None
    reopen_count: int
    created_at: datetime


class ComplaintDetail(ComplaintOut):
    category: str
    student_code: str
    student_name: str
    assigned_to_name: str | None
    attachment_file_ids: list[uuid.UUID]
    updates: list[ComplaintUpdateOut]
    can_reopen_until: datetime | None


class ComplaintIn(Strict):
    category_id: uuid.UUID
    title: Annotated[str, Field(min_length=3, max_length=120)]
    description: Annotated[str, Field(min_length=3, max_length=4000)]
    priority: Priority = "medium"
    attachment_file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=3)


class AssignIn(Strict):
    user_id: uuid.UUID


class ResolveIn(Strict):
    note: Annotated[str, Field(min_length=3, max_length=2000)]


class CommentIn(Strict):
    comment: Annotated[str, Field(min_length=1, max_length=2000)]


class ReopenIn(Strict):
    comment: Annotated[str, Field(min_length=3, max_length=2000)]


# --- helpers ---------------------------------------------------------------------------------


def _history(
    db: Session, c: Complaint, author: uuid.UUID, frm: str | None, to: str | None, comment: str | None
) -> None:
    db.add(
        ComplaintUpdate(
            org_id=c.org_id, complaint_id=c.id, author_id=author, from_status=frm, to_status=to, comment=comment
        )
    )
    db.flush()


def _notify_student(db: Session, c: Complaint, title: str, body: str) -> None:
    s = db.get(Student, c.student_id)
    if s is not None:
        notifications.notify(
            db,
            org_id=c.org_id,
            user_id=s.user_id,
            category="complaints",
            title=title,
            body=body,
            link="/portal/complaints",
        )


def _detail(db: Session, c: Complaint) -> ComplaintDetail:
    student = db.get(Student, c.student_id)
    assert student is not None
    name = db.scalar(select(User.full_name).where(User.id == student.user_id)) or ""
    assignee = db.scalar(select(User.full_name).where(User.id == c.assigned_to)) if c.assigned_to else None
    category = db.scalar(select(ComplaintCategory.name).where(ComplaintCategory.id == c.category_id)) or ""
    files = list(db.scalars(select(ComplaintAttachment.file_id).where(ComplaintAttachment.complaint_id == c.id)))
    updates = db.scalars(
        select(ComplaintUpdate).where(ComplaintUpdate.complaint_id == c.id).order_by(ComplaintUpdate.created_at)
    )
    until = c.resolved_at + timedelta(days=REOPEN_DAYS) if c.status == "resolved" and c.resolved_at else None
    return ComplaintDetail.model_validate(
        {
            **ComplaintOut.model_validate(c).model_dump(),
            "category": category,
            "student_code": student.student_code,
            "student_name": name,
            "assigned_to_name": assignee,
            "attachment_file_ids": files,
            "updates": [ComplaintUpdateOut.model_validate(u) for u in updates],
            "can_reopen_until": until,
        }
    )


def _lock(db: Session, p: Principal, complaint_id: uuid.UUID) -> Complaint:
    c = db.get(Complaint, complaint_id)
    if c is None:
        raise not_found()
    check_hostel(p, COMPLAINTS_MANAGE, c.hostel_id)
    db.refresh(c, with_for_update=True)
    return c


def _wrong(c: Complaint, action: str) -> ProblemError:
    return ProblemError(409, "invalid_status", f"A {c.status.replace('_', ' ')} complaint can't be {action}")


# --- categories (Super Admin) ----------------------------------------------------------------


@router.get("/complaint-categories")
def list_categories(ctx: Ctx) -> list[CategoryOut]:
    """Every signed-in staff member may read the names (the complaints screen shows them)."""
    return [
        CategoryOut.model_validate(c)
        for c in ctx.db.scalars(select(ComplaintCategory).order_by(ComplaintCategory.name))
    ]


@router.post("/complaint-categories", status_code=status.HTTP_201_CREATED)
def create_category(ctx: Categories, body: CategoryIn) -> CategoryOut:
    c = ComplaintCategory(org_id=ctx.principal.org_id, name=body.name)
    ctx.db.add(c)
    ctx.db.flush()
    audit.record(
        ctx.db, ctx.principal, "complaint_category.created", "complaint_category", c.id, after={"name": c.name}
    )
    return CategoryOut.model_validate(c)


@router.patch("/complaint-categories/{category_id}")
def update_category(ctx: Categories, category_id: uuid.UUID, body: CategoryPatch) -> CategoryOut:
    c = ctx.db.get(ComplaintCategory, category_id)
    if c is None:
        raise not_found()
    check_version(c, body.version)
    changes = apply_patch(ctx.db, c, body)
    ctx.db.flush()
    audit.record(
        ctx.db,
        ctx.principal,
        "complaint_category.updated",
        "complaint_category",
        c.id,
        before={k: v[0] for k, v in changes.items()},
        after={k: v[1] for k, v in changes.items()},
    )
    return CategoryOut.model_validate(c)


# --- staff -----------------------------------------------------------------------------------


@router.get("/complaints")
def list_complaints(
    ctx: Manage,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    hostel_id: uuid.UUID | None = None,
    status: Literal["open", "in_progress", "resolved", "closed"] | None = None,
    priority: Priority | None = None,
    assigned_to: uuid.UUID | None = None,
) -> Page[ComplaintOut]:
    q = scope_query(select(Complaint), Complaint, ctx.principal, COMPLAINTS_MANAGE)
    for col, value in (
        (Complaint.hostel_id, hostel_id),
        (Complaint.status, status),
        (Complaint.priority, priority),
        (Complaint.assigned_to, assigned_to),
    ):
        if value is not None:
            q = q.where(col == value)
    total = ctx.db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
    rows = ctx.db.scalars(
        q.order_by(Complaint.target_date, Complaint.created_at).offset((page - 1) * page_size).limit(page_size)
    )
    return Page[ComplaintOut](
        items=[ComplaintOut.model_validate(c) for c in rows],
        page=PageInfo(number=page, size=page_size, total_items=total, total_pages=max(1, -(-total // page_size))),
    )


@router.get("/complaints/{complaint_id}")
def get_complaint(ctx: Manage, complaint_id: uuid.UUID) -> ComplaintDetail:
    c = ctx.db.get(Complaint, complaint_id)
    if c is None:
        raise not_found()
    check_hostel(ctx.principal, COMPLAINTS_MANAGE, c.hostel_id)
    return _detail(ctx.db, c)


@router.post("/complaints/{complaint_id}/assign")
def assign(ctx: Manage, complaint_id: uuid.UUID, body: AssignIn) -> ComplaintDetail:
    c = _lock(ctx.db, ctx.principal, complaint_id)
    if c.status not in ("open", "in_progress"):
        raise _wrong(c, "assigned")
    if not is_active_administrator(ctx.db, body.user_id):
        raise ProblemError(422, "invalid_assignee", "Assign it to an active administrator")
    c.assigned_to = body.user_id
    ctx.db.flush()
    name = ctx.db.scalar(select(User.full_name).where(User.id == body.user_id))
    _history(ctx.db, c, ctx.principal.user_id, None, None, f"Assigned to {name}")
    if body.user_id != ctx.principal.user_id:
        notifications.notify(
            ctx.db,
            org_id=c.org_id,
            user_id=body.user_id,
            category="complaints",
            title="Complaint assigned to you",
            body=c.title,
            link="/admin/complaints",
        )
    return _detail(ctx.db, c)


@router.post("/complaints/{complaint_id}/start")
def start(ctx: Manage, complaint_id: uuid.UUID) -> ComplaintDetail:
    c = _lock(ctx.db, ctx.principal, complaint_id)
    if c.status != "open":
        raise _wrong(c, "started")
    if c.assigned_to is None:
        raise ProblemError(409, "not_assigned", "Assign the complaint before starting work")
    c.status = "in_progress"
    ctx.db.flush()
    _history(ctx.db, c, ctx.principal.user_id, "open", "in_progress", None)
    _notify_student(ctx.db, c, "Work started on your complaint", c.title)
    return _detail(ctx.db, c)


@router.post("/complaints/{complaint_id}/resolve")
def resolve(ctx: Manage, complaint_id: uuid.UUID, body: ResolveIn) -> ComplaintDetail:
    c = _lock(ctx.db, ctx.principal, complaint_id)
    if c.status not in ("open", "in_progress"):
        raise _wrong(c, "resolved")
    before = c.status
    c.status, c.resolved_at, c.resolved_by, c.resolution_note = (
        "resolved",
        datetime.now(UTC),
        ctx.principal.user_id,
        body.note,
    )
    ctx.db.flush()
    _history(ctx.db, c, ctx.principal.user_id, before, "resolved", body.note)
    _notify_student(
        ctx.db,
        c,
        "Your complaint was resolved",
        f"{body.note} If it isn't fixed, you can reopen it within {REOPEN_DAYS} days.",
    )
    return _detail(ctx.db, c)


@router.post("/complaints/{complaint_id}/comments", status_code=status.HTTP_201_CREATED)
def comment(ctx: Manage, complaint_id: uuid.UUID, body: CommentIn) -> ComplaintDetail:
    c = _lock(ctx.db, ctx.principal, complaint_id)
    _history(ctx.db, c, ctx.principal.user_id, None, None, body.comment)
    _notify_student(ctx.db, c, "New comment on your complaint", body.comment[:200])
    return _detail(ctx.db, c)


# --- student ---------------------------------------------------------------------------------


@portal.get("/complaint-categories")
def my_categories(ctx: Ctx) -> list[CategoryOut]:
    """The active categories, for the student's complaint form."""
    rows = ctx.db.scalars(
        select(ComplaintCategory).where(ComplaintCategory.is_active.is_(True)).order_by(ComplaintCategory.name)
    )
    return [CategoryOut.model_validate(c) for c in rows]


@portal.get("/complaints")
def my_complaints(ctx: Ctx) -> list[ComplaintDetail]:
    s = student_of_user(ctx.db, ctx.principal.user_id)
    rows = ctx.db.scalars(
        select(Complaint).where(Complaint.student_id == s.id).order_by(Complaint.created_at.desc()).limit(50)
    )
    return [_detail(ctx.db, c) for c in rows]


@portal.post("/complaints", status_code=status.HTTP_201_CREATED)
def create_complaint(ctx: Ctx, body: ComplaintIn) -> ComplaintDetail:
    s = student_of_user(ctx.db, ctx.principal.user_id)
    if s.status != "active":
        raise ProblemError(409, "student_exited", "Your stay has ended")
    category = ctx.db.get(ComplaintCategory, body.category_id)
    if category is None or not category.is_active:
        raise ProblemError(422, "invalid_category", "Choose one of the listed categories")
    files = []
    for fid in dict.fromkeys(body.attachment_file_ids):
        f = ctx.db.scalars(select(File).where(File.id == fid, File.deleted_at.is_(None), unattached_clause())).first()
        if f is None or f.category != "complaint_attachment" or f.uploaded_by != ctx.principal.user_id:
            raise ProblemError(422, "invalid_attachment", "Upload the photos first")
        files.append(f)
    room_id = ctx.db.scalar(
        text(
            "SELECT b.room_id FROM bed_allocations a JOIN beds b ON b.id = a.bed_id "
            "WHERE a.student_id = :s AND a.status = 'active' LIMIT 1"
        ),
        {"s": s.id},
    )
    today = datetime.now(org_zone(ctx.db)).date()
    c = Complaint(
        org_id=s.org_id,
        hostel_id=s.hostel_id,
        student_id=s.id,
        room_id=room_id,
        category_id=category.id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        target_date=today + timedelta(days=TARGET_DAYS[body.priority]),
    )
    ctx.db.add(c)
    ctx.db.flush()
    for f in files:
        ctx.db.add(ComplaintAttachment(org_id=s.org_id, complaint_id=c.id, file_id=f.id))
    _history(ctx.db, c, ctx.principal.user_id, None, "open", None)
    for user_id in administrators_of(ctx.db):
        notifications.notify(
            ctx.db,
            org_id=s.org_id,
            user_id=user_id,
            category="complaints",
            title=f"New {body.priority} complaint",
            body=f"{s.student_code}: {body.title}",
            link="/admin/complaints",
        )
    return _detail(ctx.db, c)


@portal.post("/complaints/{complaint_id}/reopen")
def reopen(ctx: Ctx, complaint_id: uuid.UUID, body: ReopenIn) -> ComplaintDetail:
    """Q40: within 7 days of resolution; back to open, keeping the assignee."""
    s = student_of_user(ctx.db, ctx.principal.user_id)
    c = ctx.db.get(Complaint, complaint_id)
    if c is None or c.student_id != s.id:
        raise not_found()
    ctx.db.refresh(c, with_for_update=True)
    if c.status != "resolved" or c.resolved_at is None:
        raise _wrong(c, "reopened")
    if datetime.now(UTC) > c.resolved_at + timedelta(days=REOPEN_DAYS):
        raise ProblemError(409, "reopen_window_closed", f"Complaints can be reopened within {REOPEN_DAYS} days")
    c.status, c.resolved_at, c.resolved_by, c.resolution_note = "open", None, None, None
    c.reopen_count += 1
    ctx.db.flush()
    _history(ctx.db, c, ctx.principal.user_id, "resolved", "open", body.comment)
    for user_id in {*administrators_of(ctx.db), *([c.assigned_to] if c.assigned_to else [])}:
        notifications.notify(
            ctx.db,
            org_id=c.org_id,
            user_id=user_id,
            category="complaints",
            title="Complaint reopened",
            body=f"{s.student_code}: {c.title}",
            link="/admin/complaints",
        )
    return _detail(ctx.db, c)


# --- job -------------------------------------------------------------------------------------


def auto_close(db: Session) -> int:
    """Per organization: resolved complaints close once the reopen window has passed."""
    rows = db.scalars(
        select(Complaint)
        .where(Complaint.status == "resolved", Complaint.resolved_at < datetime.now(UTC) - timedelta(days=REOPEN_DAYS))
        .with_for_update(skip_locked=True)
    ).all()
    for c in rows:
        c.status, c.closed_at = "closed", datetime.now(UTC)
    db.flush()
    for c in rows:
        db.add(
            ComplaintUpdate(
                org_id=c.org_id,
                complaint_id=c.id,
                author_id=c.resolved_by,
                from_status="resolved",
                to_status="closed",
                comment="Closed automatically after the reopen window",
            )
        )
    db.flush()
    return len(rows)
