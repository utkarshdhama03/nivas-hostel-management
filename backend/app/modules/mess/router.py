"""Mess menus (docs/04 §12.13; Administrators edit, D75; Q42 per hostel, with copy).

A menu is one row per (hostel, date, meal). Saving is an upsert of a list of rows; copying takes
one week (7 days from a start date) of another hostel, or of another week, and writes it to the
target week, either filling only empty slots or overwriting.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.core import audit
from app.core.context import Ctx, OrgContext, require, staff_only, student_only
from app.core.crud import check_hostel, not_found
from app.core.errors import ProblemError
from app.core.permissions import MESS_MANAGE
from app.core.schemas import Out, Strict
from app.modules.hostels.models import Hostel
from app.modules.leave.service import org_zone, student_of_user
from app.modules.mess.models import MessMenu

router = APIRouter(prefix="/admin", tags=["mess"], dependencies=[Depends(staff_only)])
portal = APIRouter(prefix="/portal/me", tags=["portal"], dependencies=[Depends(student_only)])
Manage = Annotated[OrgContext, Depends(require(MESS_MANAGE))]
Meal = Literal["breakfast", "lunch", "snacks", "dinner"]
MAX_RANGE = 31


class MenuEntryOut(Out):
    id: uuid.UUID
    version: int
    hostel_id: uuid.UUID
    menu_date: date
    meal: str
    items: str
    notes: str | None
    updated_at: datetime


class MenuEntryIn(Strict):
    menu_date: date
    meal: Meal
    items: Annotated[str, Field(min_length=1, max_length=1000)]
    notes: Annotated[str | None, Field(default=None, max_length=300)] = None


class MenuEntriesIn(Strict):
    entries: list[MenuEntryIn] = Field(min_length=1, max_length=100)


class MenuCopyIn(Strict):
    from_hostel_id: uuid.UUID | None = None  # default: the same hostel (another week)
    from_week_start: date
    to_week_start: date
    overwrite: bool = False


class MenuCopyOut(BaseModel):
    copied: int
    skipped: int


def _range(db_zone_today: date, start: date | None, end: date | None) -> tuple[date, date]:
    start = start or db_zone_today - timedelta(days=db_zone_today.weekday())  # this week's Monday
    end = end or start + timedelta(days=6)
    if end < start or (end - start).days > MAX_RANGE:
        raise ProblemError(422, "validation_error", f"Choose a range of at most {MAX_RANGE} days")
    return start, end


def _menu(ctx: OrgContext, hostel_id: uuid.UUID, start: date, end: date) -> list[MenuEntryOut]:
    order = text("array_position(ARRAY['breakfast','lunch','snacks','dinner'], meal)")
    rows = ctx.db.scalars(
        select(MessMenu)
        .where(MessMenu.hostel_id == hostel_id, MessMenu.menu_date >= start, MessMenu.menu_date <= end)
        .order_by(MessMenu.menu_date, order)
    )
    return [MenuEntryOut.model_validate(m) for m in rows]


UPSERT = text(
    "INSERT INTO mess_menus (org_id, hostel_id, menu_date, meal, items, notes, updated_by) "
    "VALUES (:o, :h, :d, :m, :i, :n, :u) "
    "ON CONFLICT (org_id, hostel_id, menu_date, meal) DO UPDATE SET items = EXCLUDED.items, notes = EXCLUDED.notes, "
    "updated_by = EXCLUDED.updated_by, updated_at = app_now(), version = mess_menus.version + 1 "
    "WHERE :overwrite"
)


@router.get("/hostels/{hostel_id}/mess-menu")
def hostel_menu(
    ctx: Manage, hostel_id: uuid.UUID, start: date | None = None, end: date | None = None
) -> list[MenuEntryOut]:
    check_hostel(ctx.principal, MESS_MANAGE, hostel_id)
    if ctx.db.get(Hostel, hostel_id) is None:
        raise not_found()  # another organization's hostel looks like no hostel at all
    a, b = _range(datetime.now(org_zone(ctx.db)).date(), start, end)
    return _menu(ctx, hostel_id, a, b)


@router.post("/hostels/{hostel_id}/mess-menu/entries")
def save_entries(ctx: Manage, hostel_id: uuid.UUID, body: MenuEntriesIn) -> list[MenuEntryOut]:
    check_hostel(ctx.principal, MESS_MANAGE, hostel_id)
    if ctx.db.get(Hostel, hostel_id) is None:
        raise not_found()
    seen = set()
    for e in body.entries:
        if (e.menu_date, e.meal) in seen:
            raise ProblemError(422, "validation_error", f"{e.menu_date:%d-%m-%Y} {e.meal} appears twice")
        seen.add((e.menu_date, e.meal))
        ctx.db.execute(
            UPSERT,
            {
                "o": ctx.principal.org_id,
                "h": hostel_id,
                "d": e.menu_date,
                "m": e.meal,
                "i": e.items,
                "n": e.notes,
                "u": ctx.principal.user_id,
                "overwrite": True,
            },
        )
    dates = sorted(d for d, _ in seen)
    audit.record(
        ctx.db,
        ctx.principal,
        "mess_menu.saved",
        "mess_menu",
        hostel_id,
        hostel_id=hostel_id,
        after={"entries": len(seen)},
    )
    return _menu(ctx, hostel_id, dates[0], dates[-1])


@router.post("/hostels/{hostel_id}/mess-menu/copy", status_code=status.HTTP_201_CREATED)
def copy_menu(ctx: Manage, hostel_id: uuid.UUID, body: MenuCopyIn) -> MenuCopyOut:
    """Q42: from another hostel's week or from another week of this hostel."""
    source = body.from_hostel_id or hostel_id
    check_hostel(ctx.principal, MESS_MANAGE, hostel_id)
    check_hostel(ctx.principal, MESS_MANAGE, source)
    if source == hostel_id and body.from_week_start == body.to_week_start:
        raise ProblemError(422, "validation_error", "Copy from another hostel or another week")
    if ctx.db.get(Hostel, hostel_id) is None or ctx.db.get(Hostel, source) is None:
        raise not_found()
    shift = body.to_week_start - body.from_week_start
    rows = ctx.db.scalars(
        select(MessMenu).where(
            MessMenu.hostel_id == source,
            MessMenu.menu_date >= body.from_week_start,
            MessMenu.menu_date <= body.from_week_start + timedelta(days=6),
        )
    ).all()
    copied = 0
    for m in rows:
        result = ctx.db.execute(
            UPSERT,
            {
                "o": ctx.principal.org_id,
                "h": hostel_id,
                "d": m.menu_date + shift,
                "m": m.meal,
                "i": m.items,
                "n": m.notes,
                "u": ctx.principal.user_id,
                "overwrite": body.overwrite,
            },
        )
        copied += result.rowcount  # type: ignore[attr-defined]
    audit.record(
        ctx.db,
        ctx.principal,
        "mess_menu.copied",
        "mess_menu",
        hostel_id,
        hostel_id=hostel_id,
        after={
            "from_hostel_id": str(source),
            "from_week_start": str(body.from_week_start),
            "to_week_start": str(body.to_week_start),
            "copied": copied,
        },
    )
    return MenuCopyOut(copied=copied, skipped=len(rows) - copied)


@portal.get("/mess-menu")
def my_menu(ctx: Ctx, start: date | None = None, end: date | None = None) -> list[MenuEntryOut]:
    s = student_of_user(ctx.db, ctx.principal.user_id)
    a, b = _range(datetime.now(org_zone(ctx.db)).date(), start, end)
    return _menu(ctx, s.hostel_id, a, b)
