"""The organization's audit log (docs/04 §12.17): the Administrator only (Q17), newest first, cursor paging.

The rows are read under RLS like everything else, so only this organization's history appears.
Values were filtered by the allowlist when written (app/core/audit.py); secrets never reach here.
D89: each entry also comes in plain language (describe.py). That is presentation only: the stored
row is returned unchanged next to it, and nothing here writes.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.context import OrgContext, require, staff_only
from app.core.errors import ProblemError
from app.core.models import AuditLog
from app.core.permissions import AUDIT_READ
from app.modules.audit import describe as plain
from app.modules.leave.service import org_zone

router = APIRouter(prefix="/admin", tags=["audit"], dependencies=[Depends(staff_only)])
Read = Annotated[OrgContext, Depends(require(AUDIT_READ))]


class AuditDetail(BaseModel):
    field: str
    label: str
    before: str | None
    after: str | None


Category = Literal[
    "payments",
    "refunds",
    "students",
    "registration",
    "rooms",
    "leave",
    "complaints",
    "notices",
    "settings",
    "signin",
    "other",
]


class AuditLogOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    hostel_id: uuid.UUID | None
    actor_type: str
    actor_user_id: uuid.UUID | None
    actor_roles: list[str]
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    changed_fields: list[str]
    before_values: dict[str, Any] | None
    after_values: dict[str, Any] | None
    request_id: uuid.UUID | None
    ip_address: str | None
    # D89, plain language (read-time presentation; see describe.py)
    category: str
    sentence: str
    actor_name: str
    actor_kind: Literal["administrator", "student", "system", "platform"]
    summary: str | None
    details: list[AuditDetail]
    student_id: uuid.UUID | None
    student_name: str | None
    link_label: str | None
    link_to: str | None


class AuditLogPage(BaseModel):
    items: list[AuditLogOut]
    next_cursor: str | None


@router.get("/audit-logs")
def audit_logs(
    ctx: Read,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: str | None = None,
    start: date | None = None,
    end: date | None = None,
    q: Annotated[str | None, Query(max_length=50)] = None,
    category: Category | None = None,
    actor: Annotated[str | None, Query(description="'system', 'students' or a user ID")] = None,
) -> AuditLogPage:
    query = select(AuditLog)
    if cursor is not None:
        query = query.where(AuditLog.id < cursor)  # uuidv7 IDs are time-ordered
    if entity_type is not None:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        query = query.where(AuditLog.entity_id == entity_id)
    if actor_user_id is not None:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    if q:
        if len(q.strip()) < 2:
            raise ProblemError(422, "validation_error", "Search needs 2 to 50 characters")
        query = query.where(plain.search_condition(q.strip()))
    if category is not None:
        query = query.where(plain.category_condition(category))
    if actor is not None:
        if actor not in ("system", "students"):
            try:
                uuid.UUID(actor)
            except ValueError:
                raise ProblemError(422, "validation_error", "Who did it: 'system', 'students' or a user ID") from None
        query = query.where(plain.actor_condition(actor))
    zone = org_zone(ctx.db)
    if start is not None:
        query = query.where(AuditLog.occurred_at >= datetime.combine(start, time.min, zone))
    if end is not None:
        query = query.where(AuditLog.occurred_at < datetime.combine(end + timedelta(days=1), time.min, zone))
    rows = list(ctx.db.scalars(query.order_by(AuditLog.id.desc()).limit(limit + 1)))
    page = rows[:limit]
    lookups = plain.load(ctx.db, page)
    items = []
    for r in page:
        p = plain.describe(r, lookups, zone)
        items.append(
            AuditLogOut(
                id=r.id,
                occurred_at=r.occurred_at,
                hostel_id=r.hostel_id,
                actor_type=r.actor_type,
                actor_user_id=r.actor_user_id,
                actor_roles=list(r.actor_roles or []),
                action=r.action,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                changed_fields=list(r.changed_fields or []),
                before_values=r.before_values,
                after_values=r.after_values,
                request_id=r.request_id,
                ip_address=str(r.ip_address) if r.ip_address is not None else None,
                category=p.category,
                sentence=p.sentence,
                actor_name=p.actor_name,
                actor_kind=p.actor_kind,  # type: ignore[arg-type]
                summary=p.summary,
                details=[AuditDetail.model_validate(x) for x in p.details],
                student_id=p.student_id,
                student_name=p.student_name,
                link_label=p.link_label,
                link_to=p.link_to,
            )
        )
    return AuditLogPage(items=items, next_cursor=str(rows[limit - 1].id) if len(rows) > limit else None)
