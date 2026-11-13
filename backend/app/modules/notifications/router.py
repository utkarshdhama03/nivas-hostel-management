"""In-app notifications for the signed-in user (docs/04 §12.3).

Every query is filtered by BOTH layers:
  * RLS: only rows of the caller's organization exist in this transaction;
  * the WHERE clause: only rows whose user_id is the caller (from the token, never the URL).
Another user's notification ID therefore gives 404, exactly like a missing one (04 §6).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from app.core import audit
from app.core.context import Ctx
from app.core.errors import ProblemError
from app.modules.notifications.models import MUTABLE_EMAIL_CATEGORIES, Notification, NotificationPreference

router = APIRouter(prefix="/me", tags=["notifications"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationOut(BaseModel):
    id: uuid.UUID
    category: str
    title: str
    body: str
    link: str | None
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None


class UnreadCount(BaseModel):
    unread: int


class Preference(BaseModel):
    category: Literal["announcements", "reminders"]
    email_enabled: bool


class PreferencesOut(BaseModel):
    preferences: list[Preference]


class PreferencesIn(Strict):
    preferences: list[Preference]


def _encode_cursor(created_at: datetime, id_: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{id_}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        ts, _, id_ = raw.partition("|")
        return datetime.fromisoformat(ts), uuid.UUID(id_)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ProblemError(400, "invalid_cursor", "Invalid cursor") from exc


def _out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        category=n.category,
        title=n.title,
        body=n.body,
        link=n.link,
        created_at=n.created_at,
        read_at=n.read_at,
    )


@router.get("/notifications")
def list_notifications(
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    unread_only: bool = False,
) -> NotificationPage:
    """Newest first. Keyset pagination on (created_at, id) (04 §8)."""
    q = select(Notification).where(Notification.user_id == ctx.principal.user_id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    if cursor:
        q = q.where(tuple_(Notification.created_at, Notification.id) < tuple_(*_decode_cursor(cursor)))
    rows = ctx.db.scalars(q.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return NotificationPage(
        items=[_out(n) for n in rows],
        next_cursor=_encode_cursor(rows[-1].created_at, rows[-1].id) if more else None,
    )


@router.get("/notifications/unread-count")
def unread_count(ctx: Ctx) -> UnreadCount:
    count = ctx.db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == ctx.principal.user_id, Notification.read_at.is_(None))
    )
    return UnreadCount(unread=count or 0)


@router.post("/notifications/{notification_id}/read")
def mark_read(ctx: Ctx, notification_id: uuid.UUID) -> NotificationOut:
    n = ctx.db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == ctx.principal.user_id)
    )
    if n is None:
        raise ProblemError(404, "not_found", "Not found")
    if n.read_at is None:
        n.read_at = ctx.db.scalar(select(func.app_now()))
    return _out(n)


@router.post("/notifications/read-all")
def mark_all_read(ctx: Ctx) -> UnreadCount:
    ctx.db.execute(
        update(Notification)
        .where(Notification.user_id == ctx.principal.user_id, Notification.read_at.is_(None))
        .values(read_at=func.app_now())
    )
    return UnreadCount(unread=0)


def _preferences(ctx: Ctx) -> PreferencesOut:
    stored: dict[str, bool] = {
        category: enabled
        for category, enabled in ctx.db.execute(
            select(NotificationPreference.category, NotificationPreference.email_enabled).where(
                NotificationPreference.user_id == ctx.principal.user_id
            )
        )
    }
    return PreferencesOut(
        preferences=[
            Preference(category=c, email_enabled=stored.get(c, True))  # type: ignore[arg-type]
            for c in MUTABLE_EMAIL_CATEGORIES
        ]
    )


@router.get("/notification-preferences")
def get_preferences(ctx: Ctx) -> PreferencesOut:
    """Only announcement and reminder emails can be muted (Q49). Default: on."""
    return _preferences(ctx)


@router.patch("/notification-preferences")
def update_preferences(ctx: Ctx, body: PreferencesIn) -> PreferencesOut:
    before = {p.category: p.email_enabled for p in _preferences(ctx).preferences}
    for pref in body.preferences:
        ctx.db.execute(
            insert(NotificationPreference)
            .values(
                org_id=ctx.principal.org_id,
                user_id=ctx.principal.user_id,
                category=pref.category,
                email_enabled=pref.email_enabled,
            )
            .on_conflict_do_update(
                index_elements=["org_id", "user_id", "category"],
                set_={"email_enabled": pref.email_enabled, "updated_at": func.app_now()},
            )
        )
        if before.get(pref.category) != pref.email_enabled:
            audit.record(
                ctx.db,
                ctx.principal,
                "notification_preference.updated",
                "notification_preference",
                ctx.principal.user_id,
                before={"category": pref.category, "email_enabled": before.get(pref.category)},
                after={"category": pref.category, "email_enabled": pref.email_enabled},
            )
    return _preferences(ctx)
