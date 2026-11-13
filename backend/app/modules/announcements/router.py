"""Announcements (docs/04 §12.15). Administrators post to one hostel or to the whole organization (D75).
Audience: students, staff or everyone. Each targeted person also gets a
notification (category "announcements", whose email they can mute); the fan-out runs as a
background job so a large hostel doesn't slow down the request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import AwareDatetime, Field
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core import audit, jobs
from app.core.context import Ctx, OrgContext, require, staff_only, student_only
from app.core.crud import allowed_hostels, check_hostel, not_found
from app.core.errors import ProblemError
from app.core.permissions import ANNOUNCEMENTS_MANAGE
from app.core.schemas import Out, Strict
from app.modules.announcements.models import Announcement
from app.modules.hostels.models import Hostel
from app.modules.leave.service import student_of_user
from app.modules.notifications import service as notifications

router = APIRouter(prefix="/admin", tags=["announcements"], dependencies=[Depends(staff_only)])
portal = APIRouter(prefix="/portal/me", tags=["portal"], dependencies=[Depends(student_only)])
Manage = Annotated[OrgContext, Depends(require(ANNOUNCEMENTS_MANAGE))]


class AnnouncementOut(Out):
    id: uuid.UUID
    hostel_id: uuid.UUID | None
    title: str
    body: str
    audience: str
    published_at: datetime
    expires_at: datetime | None
    created_by: uuid.UUID


class AnnouncementIn(Strict):
    hostel_id: uuid.UUID | None = None  # None = whole organization (Super Admin)
    title: Annotated[str, Field(min_length=3, max_length=120)]
    body: Annotated[str, Field(min_length=1, max_length=4000)]
    audience: Literal["students", "staff", "everyone"] = "everyone"
    expires_at: AwareDatetime | None = None


def _live() -> object:
    return or_(Announcement.expires_at.is_(None), Announcement.expires_at > datetime.now(UTC))


@router.get("/announcements")
def list_announcements(ctx: Manage, include_expired: bool = False) -> list[AnnouncementOut]:
    hostels = allowed_hostels(ctx.principal, ANNOUNCEMENTS_MANAGE)
    q = select(Announcement)
    if hostels is not None:
        q = q.where(or_(Announcement.hostel_id.is_(None), Announcement.hostel_id.in_(hostels)))
    if not include_expired:
        q = q.where(_live())  # type: ignore[arg-type]
    rows = ctx.db.scalars(q.order_by(Announcement.published_at.desc()).limit(100))
    return [AnnouncementOut.model_validate(a) for a in rows]


@router.post("/announcements", status_code=status.HTTP_201_CREATED)
def create_announcement(ctx: Manage, body: AnnouncementIn) -> AnnouncementOut:
    p = ctx.principal
    if body.hostel_id is None:
        if not p.is_super_admin:
            raise ProblemError(403, "forbidden", "Only an Administrator can post to the whole organization")
    else:
        check_hostel(p, ANNOUNCEMENTS_MANAGE, body.hostel_id)
        if ctx.db.get(Hostel, body.hostel_id) is None:
            raise not_found()
    if body.expires_at is not None and body.expires_at <= datetime.now(UTC):
        raise ProblemError(422, "validation_error", "The expiry must be in the future")
    a = Announcement(org_id=p.org_id, created_by=p.user_id, **body.model_dump())
    ctx.db.add(a)
    ctx.db.flush()
    ctx.db.refresh(a)
    audit.record(
        ctx.db,
        p,
        "announcement.published",
        "announcement",
        a.id,
        hostel_id=a.hostel_id,
        after={"title": a.title, "audience": a.audience},
    )
    jobs.enqueue(ctx.db, "announcements.fanout", {"announcement_id": str(a.id)}, org_id=p.org_id)
    return AnnouncementOut.model_validate(a)


@portal.get("/announcements")
def my_announcements(ctx: Ctx) -> list[AnnouncementOut]:
    s = student_of_user(ctx.db, ctx.principal.user_id)
    rows = ctx.db.scalars(
        select(Announcement)
        .where(
            or_(Announcement.hostel_id.is_(None), Announcement.hostel_id == s.hostel_id),
            Announcement.audience.in_(("students", "everyone")),
            _live(),  # type: ignore[arg-type]
        )
        .order_by(Announcement.published_at.desc())
        .limit(50)
    )
    return [AnnouncementOut.model_validate(a) for a in rows]


def recipients(db: Session, a: Announcement) -> dict[uuid.UUID, str]:
    """Active students of the hostel (or organization) and/or its active staff, each with the page the
    notification opens: their own Announcements page (D84; before, "everyone" gave no link at all)."""
    found: dict[uuid.UUID, str] = {}
    if a.audience in ("students", "everyone"):
        for user_id in db.scalars(
            text(
                "SELECT u.id FROM students s JOIN users u ON u.id = s.user_id "
                "WHERE s.status = 'active' AND u.status = 'active' AND (CAST(:h AS uuid) IS NULL OR s.hostel_id = :h)"
            ),
            {"h": a.hostel_id},
        ):
            found.setdefault(user_id, "/portal/announcements")
    if a.audience in ("staff", "everyone"):
        for user_id in db.scalars(
            text(
                # Every grant is organization-wide (D75; the schema cleanup dropped the hostel column).
                "SELECT DISTINCT u.id FROM role_assignments ra JOIN users u ON u.id = ra.user_id "
                "WHERE ra.revoked_at IS NULL AND u.status = 'active'"
            ),
        ):
            found.setdefault(user_id, "/admin/announcements")
    found.pop(a.created_by, None)
    return found


@jobs.handler("announcements.fanout")
def fanout(db: Session, job: jobs.ClaimedJob) -> None:
    """One notification per recipient. Safe to rerun: people who already have it are skipped."""
    a = db.get(Announcement, uuid.UUID(str(job.payload["announcement_id"])))
    if a is None:
        return
    done = set(
        db.scalars(
            text(
                "SELECT user_id FROM notifications WHERE category = 'announcements' AND title = :t AND created_at >= :p"
            ),
            {"t": a.title[:120], "p": a.published_at},
        )
    )
    for user_id, link in recipients(db, a).items():
        if user_id not in done:
            notifications.notify(
                db,
                org_id=a.org_id,
                user_id=user_id,
                category="announcements",
                title=a.title[:120],
                body=a.body[:1000],
                link=link,
            )
