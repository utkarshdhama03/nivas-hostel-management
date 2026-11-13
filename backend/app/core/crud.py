"""Small helpers every admin module uses: scoped lookups, optimistic locking, paging.

Scope rule (04 §6, §9): a record outside the caller's organization OR outside their hostels
is reported as 404 not_found, exactly like a record that doesn't exist, so nothing leaks.
RLS already hides other organizations; `in_scope` adds the hostel layer.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import ProblemError
from app.core.permissions import Principal


def not_found() -> ProblemError:
    return ProblemError(404, "not_found", "Not found")


def forbidden(detail: str | None = None) -> ProblemError:
    return ProblemError(403, "permission_denied", "Not allowed", detail=detail)


def allowed_hostels(p: Principal, permission: str) -> frozenset[uuid.UUID] | None:
    """None = every hostel. Raises 403 if the caller has the permission nowhere."""
    hostels = p.hostels_for(permission)
    if hostels is not None and not hostels:
        raise forbidden()
    return hostels


def check_hostel(p: Principal, permission: str, hostel_id: uuid.UUID) -> None:
    hostels = allowed_hostels(p, permission)
    if hostels is not None and hostel_id not in hostels:
        raise not_found()


def get_scoped[T](
    db: Session, model: type[T], record_id: uuid.UUID, p: Principal, permission: str, *, lock: bool = False
) -> T:
    """Load one hostel-scoped record (it has hostel_id) or raise 404."""
    q = select(model).where(model.id == record_id)  # type: ignore[attr-defined]
    obj = db.scalars(q.with_for_update() if lock else q).one_or_none()
    if obj is None:
        raise not_found()
    check_hostel(p, permission, obj.hostel_id)  # type: ignore[attr-defined]
    return obj


def scope_query[*Ts](
    q: Select[*Ts], model: Any, p: Principal, permission: str, column: str = "hostel_id"
) -> Select[*Ts]:
    hostels = allowed_hostels(p, permission)
    return q if hostels is None else q.where(getattr(model, column).in_(hostels))


def check_version(obj: Any, version: int) -> None:
    """Q70: the client sends back the version it read; anything else means someone else saved
    in between. (A save racing this check is still caught by the UPDATE ... WHERE version = n
    that SQLAlchemy issues, which raises StaleDataError -> 409 too.)"""
    if obj.version != version:
        raise ProblemError(
            409,
            "edit_conflict",
            "Someone else changed this record",
            detail="It was updated after you opened it. Reload to see the latest version, then reapply your change.",
            extra={"current_version": obj.version},
        )


def apply_patch(db: Session, obj: Any, body: BaseModel, *, exclude: set[str] | None = None) -> dict[str, Any]:
    """Set the fields the client actually sent. Returns {field: (old, new)} for the audit log."""
    changes: dict[str, Any] = {}
    for field, value in body.model_dump(exclude_unset=True, exclude={"version", *(exclude or set())}).items():
        old = getattr(obj, field)
        if old != value:
            changes[field] = (old, value)
            setattr(obj, field, value)
    return changes


def page(db: Session, q: Select[Any], number: int, size: int) -> tuple[list[Any], dict[str, int]]:
    total = int(db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0)
    items = list(db.scalars(q.offset((number - 1) * size).limit(size)).all())
    return items, {"number": number, "size": size, "total_items": total, "total_pages": max(1, -(-total // size))}
