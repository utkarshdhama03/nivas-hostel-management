"""The Platform Owner console (docs/04 §12.19; Q14, Q15, Q17, Q50, Q55; 05 MT-10, RBAC-10).

Runs as the database role hms_platform, which has NO BYPASSRLS. Its window into tenants is a
set of explicit policies plus COLUMN grants (03 §6), so these queries can only name the columns
it was granted: organization profile, statuses and dates, and audit rows without before/after
values, IP address or user agent. Anything else fails with "permission denied" in PostgreSQL,
whatever this code asks for. That is why every query below lists its columns explicitly.

The only tenant write: suspend/reactivate (organizations.status, by column grant and the status
guard trigger; the authenticator reset went with two-step verification, D87). Everything else writes only
platform tables and the platform's own audit rows.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core import audit, metrics
from app.core.audit import PlatformActor
from app.core.config import get_settings
from app.core.errors import ProblemError
from app.core.schemas import PageInfo, PageNumber, PageSize, Strict
from app.modules.platform.router import PCtx

router = APIRouter(prefix="/platform", tags=["platform"])


class OrgCounts(BaseModel):
    hostels: int
    beds: int
    students_active: int
    staff_active: int


class PlatformOrgItem(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    status: str
    created_at: datetime
    suspended_at: datetime | None
    counts: OrgCounts


class PlatformOrgPage(BaseModel):
    items: list[PlatformOrgItem]
    page: PageInfo


class PlatformOrgDetail(PlatformOrgItem):
    timezone: str
    suspension_reason: str | None
    legal_name: str | None
    contact_email: str | None
    contact_phone: str | None
    city: str | None
    state: str | None
    logins_last_7_days: int
    last_activity_at: datetime | None


class SuspendIn(Strict):
    reason: Annotated[str, Field(min_length=10, max_length=500)]


class HealthItem(BaseModel):
    name: str
    status: Literal["ok", "warning", "error", "unknown"]
    detail: str


class PlatformHealth(BaseModel):
    checked_at: datetime
    items: list[HealthItem]


class PlatformAuditRow(BaseModel):
    """Masked (Q55): actions and IDs only, never before/after values, IP address or user agent."""

    id: uuid.UUID
    org_id: uuid.UUID | None
    hostel_id: uuid.UUID | None
    occurred_at: datetime
    actor_type: str
    actor_user_id: uuid.UUID | None
    actor_platform_user_id: uuid.UUID | None
    actor_roles: list[str]
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    changed_fields: list[str]
    request_id: uuid.UUID | None


class PlatformAuditPage(BaseModel):
    items: list[PlatformAuditRow]
    next_cursor: str | None


SETTINGS: dict[str, tuple[type, Any, str]] = {  # key -> (type, default, meaning); decision D57
    "health.disk_warn_percent": (int, 80, "Disk use (%) at which the health page warns"),
    "health.error_rate_warn_percent": (int, 2, "Share of 5xx responses (%) in the last hour that warns"),
    "health.backup_max_age_hours": (int, 26, "Age of the last good backup (hours) that warns"),
    "health.restore_test_max_age_days": (int, 35, "Age of the last good restore test (days) that warns"),
}


class SettingOut(BaseModel):
    key: str
    value: Any
    description: str
    version: int | None  # None = never changed (default)
    updated_at: datetime | None


class SettingIn(Strict):
    key: str
    value: int
    version: int | None = None  # the version read; None when creating


COUNTS = """
  (SELECT count(*) FROM hostels h WHERE h.org_id = o.id AND h.is_active) AS hostels,
  (SELECT count(*) FROM beds b WHERE b.org_id = o.id AND b.status <> 'inactive') AS beds,
  (SELECT count(*) FROM students s WHERE s.org_id = o.id AND s.status = 'active') AS students_active,
  (SELECT count(*) FROM users u WHERE u.org_id = o.id AND u.kind = 'staff' AND u.status = 'active') AS staff_active
"""


def _counts(r: Any) -> OrgCounts:
    return OrgCounts(hostels=r.hostels, beds=r.beds, students_active=r.students_active, staff_active=r.staff_active)


@router.get("/organizations")
def organizations(
    ctx: PCtx,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    status: Literal["pending_verification", "active", "suspended"] | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> PlatformOrgPage:
    where = " WHERE true"
    params: dict[str, Any] = {"lim": page_size, "off": (page - 1) * page_size}
    if status is not None:
        where += " AND o.status = :st"
        params["st"] = status
    if q:
        where += " AND (o.name ILIKE :q OR o.slug ILIKE :q)"
        params["q"] = f"%{q.replace('%', '').replace('_', '')}%"
    total = ctx.db.scalar(text(f"SELECT count(*) FROM organizations o{where}"), params) or 0  # noqa: S608
    rows = ctx.db.execute(
        text(
            f"SELECT o.id, o.slug, o.name, o.status, o.created_at, o.suspended_at, {COUNTS} "  # noqa: S608
            f"FROM organizations o{where} ORDER BY o.created_at DESC, o.id LIMIT :lim OFFSET :off"
        ),
        params,
    ).all()
    return PlatformOrgPage(
        items=[
            PlatformOrgItem(
                id=r.id,
                slug=r.slug,
                name=r.name,
                status=r.status,
                created_at=r.created_at,
                suspended_at=r.suspended_at,
                counts=_counts(r),
            )
            for r in rows
        ],
        page=PageInfo(number=page, size=page_size, total_items=total, total_pages=max(1, -(-total // page_size))),
    )


def _detail(ctx: PCtx, org_id: uuid.UUID) -> PlatformOrgDetail:
    r = ctx.db.execute(
        text(
            f"""
        SELECT o.id, o.slug, o.name, o.status, o.created_at, o.suspended_at, o.timezone, o.suspension_reason,
               p.legal_name, p.contact_email, p.contact_phone, p.city, p.state,
               (SELECT count(*) FROM login_attempts la WHERE la.org_id = o.id AND la.succeeded
                  AND la.attempted_at > now() - interval '7 days') AS logins,
               (SELECT max(u.last_login_at) FROM users u WHERE u.org_id = o.id) AS last_activity,
               {COUNTS}
        FROM organizations o LEFT JOIN organization_profiles p ON p.org_id = o.id
        WHERE o.id = :o
        """  # noqa: S608
        ),
        {"o": org_id},
    ).first()
    if r is None:
        raise ProblemError(404, "not_found", "Not found")
    return PlatformOrgDetail(
        id=r.id,
        slug=r.slug,
        name=r.name,
        status=r.status,
        created_at=r.created_at,
        suspended_at=r.suspended_at,
        counts=_counts(r),
        timezone=r.timezone,
        suspension_reason=r.suspension_reason,
        legal_name=r.legal_name,
        contact_email=r.contact_email,
        contact_phone=r.contact_phone,
        city=r.city,
        state=r.state,
        logins_last_7_days=r.logins,
        last_activity_at=r.last_activity,
    )


@router.get("/organizations/{org_id}")
def organization(ctx: PCtx, org_id: uuid.UUID) -> PlatformOrgDetail:
    return _detail(ctx, org_id)


def _set_status(ctx: PCtx, org_id: uuid.UUID, frm: str, to: str, reason: str) -> PlatformOrgDetail:
    current = ctx.db.execute(text("SELECT status FROM organizations WHERE id = :o FOR UPDATE"), {"o": org_id}).scalar()
    if current is None:
        raise ProblemError(404, "not_found", "Not found")
    if current != frm:
        raise ProblemError(409, "invalid_status", f"The organization is {current.replace('_', ' ')}")
    ctx.db.execute(
        text(
            "UPDATE organizations SET status = :to, suspended_at = CASE WHEN :to = 'suspended' THEN now() END, "
            "suspension_reason = CASE WHEN :to = 'suspended' THEN :r END, updated_at = now(), version = version + 1 "
            "WHERE id = :o"
        ),
        {"to": to, "r": reason, "o": org_id},
    )
    audit.record(
        ctx.db,
        PlatformActor(ctx.platform_user_id),
        f"organization.{'suspended' if to == 'suspended' else 'reactivated'}",
        "organization",
        org_id,
        org_id=org_id,
        before={"status": frm},
        after={"status": to},
    )
    return _detail(ctx, org_id)


@router.post("/organizations/{org_id}/suspend")
def suspend(ctx: PCtx, org_id: uuid.UUID, body: SuspendIn) -> PlatformOrgDetail:
    """Q14: every request of that organization is refused from now on (checked per request),
    its jobs and emails stop, and its data stays as it is."""
    return _set_status(ctx, org_id, "active", "suspended", body.reason)


@router.post("/organizations/{org_id}/reactivate")
def reactivate(ctx: PCtx, org_id: uuid.UUID, body: SuspendIn) -> PlatformOrgDetail:
    return _set_status(ctx, org_id, "suspended", "active", body.reason)


def _setting(ctx: PCtx, key: str) -> Any:
    row = ctx.db.execute(text("SELECT value FROM platform_settings WHERE key = :k"), {"k": key}).scalar()
    return SETTINGS[key][1] if row is None else row


@router.get("/health")
def health(ctx: PCtx) -> PlatformHealth:
    """Q50: database, job queue, email vs cap, backups, restore tests, disk, error rate."""
    # Backups and jobs are written on the database's real clock, so their ages use it too (D67).
    now = ctx.db.scalar(text("SELECT now()"))
    items: list[HealthItem] = []
    version = ctx.db.scalar(text("SELECT app_schema_version()"))
    items.append(HealthItem(name="Database", status="ok", detail=f"Connected; schema {version}"))
    queued, dead, oldest = ctx.db.execute(
        text(
            "SELECT count(*) FILTER (WHERE status = 'queued' AND run_at <= now()), "
            "count(*) FILTER (WHERE status = 'dead' AND finished_at > now() - interval '1 day'), "
            "min(run_at) FILTER (WHERE status = 'queued' AND run_at <= now()) FROM jobs"
        )
    ).one()
    late = oldest is not None and now - oldest > timedelta(minutes=15)
    items.append(
        HealthItem(
            name="Job queue",
            status="error" if dead else "warning" if late else "ok",
            detail=f"{queued} due, {dead} failed permanently in the last day"
            + (f"; oldest waiting since {oldest:%H:%M} UTC" if oldest else ""),
        )
    )
    sent = ctx.db.scalar(
        text(
            "SELECT count(*) FROM jobs WHERE kind LIKE 'email.%' AND status = 'succeeded' "
            "AND finished_at >= date_trunc('day', now(), 'UTC')"
        )
    )
    cap = get_settings().hms_email_daily_cap
    items.append(
        HealthItem(
            name="Email today",
            status="ok" if cap is None or sent < 0.9 * cap else "warning" if sent < cap else "error",
            detail=f"{sent} sent" + (f" of a daily cap of {cap}" if cap else " (no daily cap set)"),
        )
    )
    for name, kinds, max_age, unit in (
        (
            "Last backup",
            ("full", "differential", "logical_dump"),
            int(_setting(ctx, "health.backup_max_age_hours")),
            "hours",
        ),
        ("Last restore test", ("restore_test",), int(_setting(ctx, "health.restore_test_max_age_days")), "days"),
    ):
        last = ctx.db.execute(
            text(
                "SELECT finished_at, status FROM backup_runs WHERE kind = ANY(:k) AND status <> 'running' "
                "ORDER BY started_at DESC LIMIT 1"
            ),
            {"k": list(kinds)},
        ).first()
        if last is None:
            items.append(HealthItem(name=name, status="warning", detail="None recorded yet"))
            continue
        age = now - last.finished_at
        limit = timedelta(hours=max_age) if unit == "hours" else timedelta(days=max_age)
        items.append(
            HealthItem(
                name=name,
                status="error" if last.status == "failed" else "warning" if age > limit else "ok",
                detail=f"{last.status} at {last.finished_at:%Y-%m-%d %H:%M} UTC",
            )
        )
    mode, archived, failed, last_ok, last_fail = ctx.db.execute(
        text(
            "SELECT current_setting('archive_mode'), archived_count, failed_count, last_archived_time, "
            "last_failed_time FROM pg_stat_archiver"
        )
    ).one()
    if mode != "on":
        items.append(
            HealthItem(name="WAL archiving", status="error", detail="archive_mode is off: no point-in-time recovery")
        )
    else:
        failing = last_fail is not None and (last_ok is None or last_fail > last_ok)
        items.append(
            HealthItem(
                name="WAL archiving",
                status="error" if failing else "ok",
                detail=f"{archived} segments archived, {failed} failures"
                + (f"; last success {last_ok:%Y-%m-%d %H:%M} UTC" if last_ok else ""),
            )
        )
    location = ctx.db.scalar(
        text(
            "SELECT details->>'location' FROM backup_runs WHERE status = 'succeeded' AND details ? 'location' "
            "ORDER BY started_at DESC LIMIT 1"
        )
    )
    if location == "default_volume":
        items.append(
            HealthItem(
                name="Backup location",
                status="warning",
                detail="Backups are on the same disk as the database: no protection against disk failure. "
                "Set HMS_BACKUP_DIR to an external drive (docs/runbooks/restore.md).",
            )
        )
    elif location:
        items.append(HealthItem(name="Backup location", status="ok", detail="A separate path (HMS_BACKUP_DIR)"))
    try:
        du = shutil.disk_usage(get_settings().hms_upload_dir)
        pct = round(100 * du.used / du.total, 1)
        warn = int(_setting(ctx, "health.disk_warn_percent"))
        items.append(
            HealthItem(
                name="Disk (uploads)",
                status="warning" if pct >= warn else "ok",
                detail=f"{pct}% used, {du.free // 2**30} GB free",
            )
        )
    except OSError:
        items.append(HealthItem(name="Disk (uploads)", status="unknown", detail="Upload folder not reachable"))
    total, errors = metrics.last_hour()
    rate = round(100 * errors / total, 2) if total else 0.0
    items.append(
        HealthItem(
            name="Error rate (this API process, last hour)",
            status="warning" if rate >= int(_setting(ctx, "health.error_rate_warn_percent")) else "ok",
            detail=f"{errors} of {total} responses were server errors ({rate}%)",
        )
    )
    return PlatformHealth(checked_at=now, items=items)


@router.get("/audit-logs")
def audit_logs(
    ctx: PCtx,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
) -> PlatformAuditPage:
    """Masked columns only (Q55). Viewing is itself audited (04 §12.19)."""
    where = " WHERE true"
    params: dict[str, Any] = {"lim": limit + 1}
    if cursor is not None:
        where += " AND id < :c"
        params["c"] = cursor
    if org_id is not None:
        where += " AND org_id = :o"
        params["o"] = org_id
    if action:
        where += " AND action = :a"
        params["a"] = action
    rows = ctx.db.execute(
        text(
            "SELECT id, org_id, hostel_id, occurred_at, actor_type, actor_user_id, actor_platform_user_id, "  # noqa: S608
            "actor_roles, action, entity_type, entity_id, changed_fields, request_id "
            f"FROM audit_logs{where} ORDER BY id DESC LIMIT :lim"
        ),
        params,
    ).all()
    audit.record(
        ctx.db,
        PlatformActor(ctx.platform_user_id),
        "platform.audit_viewed",
        "audit_log",
        None,
        org_id=org_id,
    )
    items = [
        PlatformAuditRow(
            id=r.id,
            org_id=r.org_id,
            hostel_id=r.hostel_id,
            occurred_at=r.occurred_at,
            actor_type=r.actor_type,
            actor_user_id=r.actor_user_id,
            actor_platform_user_id=r.actor_platform_user_id,
            actor_roles=list(r.actor_roles or []),
            action=r.action,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            changed_fields=list(r.changed_fields or []),
            request_id=r.request_id,
        )
        for r in rows[:limit]
    ]
    return PlatformAuditPage(items=items, next_cursor=str(rows[limit - 1].id) if len(rows) > limit else None)


@router.get("/settings")
def settings(ctx: PCtx) -> list[SettingOut]:
    stored = {
        r.key: r for r in ctx.db.execute(text("SELECT key, value, version, updated_at FROM platform_settings")).all()
    }
    return [
        SettingOut(
            key=k,
            value=stored[k].value if k in stored else default,
            description=desc,
            version=stored[k].version if k in stored else None,
            updated_at=stored[k].updated_at if k in stored else None,
        )
        for k, (_, default, desc) in SETTINGS.items()
    ]


@router.patch("/settings", status_code=status.HTTP_200_OK)
def update_setting(ctx: PCtx, body: SettingIn) -> list[SettingOut]:
    if body.key not in SETTINGS:
        raise ProblemError(422, "unknown_setting", "Unknown setting")
    if not 0 <= body.value <= 10_000:
        raise ProblemError(422, "validation_error", "Value out of range")
    current = ctx.db.execute(
        text("SELECT version FROM platform_settings WHERE key = :k FOR UPDATE"), {"k": body.key}
    ).scalar()
    if current != body.version:
        raise ProblemError(
            409, "edit_conflict", "Someone else changed this setting", extra={"current_version": current}
        )
    if current is None:
        ctx.db.execute(
            text("INSERT INTO platform_settings (key, value, updated_by) VALUES (:k, to_jsonb(CAST(:v AS int)), :u)"),
            {"k": body.key, "v": body.value, "u": ctx.platform_user_id},
        )
    else:
        ctx.db.execute(
            text(
                "UPDATE platform_settings SET value = to_jsonb(CAST(:v AS int)), version = version + 1, "
                "updated_by = :u, updated_at = now() WHERE key = :k"
            ),
            {"k": body.key, "v": body.value, "u": ctx.platform_user_id},
        )
    audit.record(
        ctx.db,
        PlatformActor(ctx.platform_user_id),
        "platform.setting_changed",
        "platform_setting",
        None,
        after={"key": body.key, "value": body.value},
    )
    return settings(ctx)
