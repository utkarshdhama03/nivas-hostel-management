"""Background jobs: transactional outbox, worker, scheduler (docs/02 §4.4, 04 §4.5).

ENQUEUE (API side)
    jobs.enqueue(db, "email.notification", {"notification_id": str(n.id)}, org_id=...)
  inserts a row in the SAME transaction as the business change. Roll back the change
  and the job never existed; commit it and the job can't be lost.
  Payloads carry IDs only (checked here): the handler loads data itself, under the
  job's tenant context, so no personal data or secret sits in the jobs table.

RUN (worker side), for each job:
  1. claim()      own short transaction, no tenant:
                  pick the next due job with FOR UPDATE SKIP LOCKED (two workers never
                  take the same row; neither waits for the other), mark it running,
                  attempts + 1, COMMIT.
  2. run_claimed() new transaction WITH the job's tenant context (set_config), so RLS
                  protects everything the handler touches. Handler + "succeeded" in the
                  same transaction: either both happen or neither.
  3. on error     rollback, then (own transaction) back to queued with exponential
                  backoff, or 'dead' after max_attempts. Dead jobs show on the platform
                  health page (Phase 6).
  A worker that crashes mid-job leaves it 'running'; the reaper puts it back after
  STUCK_AFTER. The success UPDATE checks locked_by, so a job taken over by another
  worker can't be completed twice.

SCHEDULER: inside the worker. Holds a PostgreSQL advisory lock on its own connection,
so with several workers exactly one schedules. It only ENQUEUES platform-wide jobs;
the normal worker loop runs them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, func, select, text, update
from sqlalchemy.orm import Session

from app.core.db import engine, rowcount, set_tenant, transaction
from app.core.logging import redact_text
from app.core.models import Job
from app.modules.organizations.models import Organization

log = logging.getLogger("hms.jobs")

PRIORITY_SECURITY, PRIORITY_TRANSACTIONAL, PRIORITY_BULK = 0, 1, 2
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_CAP = timedelta(hours=1)
STUCK_AFTER = timedelta(minutes=15)
SCHEDULER_LOCK_KEY = 0x484D53_5348  # "HMS" "SH": arbitrary constant, the same in every worker

# Payload keys that would mean someone is putting secrets or personal data in a job.
_FORBIDDEN_PAYLOAD_KEY = re.compile(r"(pass|token|secret|otp|code|email|phone|name|aadhaar)", re.IGNORECASE)
_PAYLOAD_KEY = re.compile(r"^[a-z_]{1,40}$")


class PayloadError(ValueError):
    """A job payload contained something other than IDs and small flags."""


class RetryLater(Exception):  # noqa: N818 - a signal, not an error
    """Raised by a handler to postpone itself (e.g. the daily email cap is reached).
    Doesn't count as a failed attempt."""

    def __init__(self, run_at: datetime) -> None:
        super().__init__(f"retry at {run_at.isoformat()}")
        self.run_at = run_at


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    org_id: uuid.UUID | None
    kind: str
    payload: dict[str, Any]
    priority: int
    attempts: int
    max_attempts: int


Handler = Callable[[Session, ClaimedJob], None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Register a job handler: @jobs.handler("email.notification")."""

    def register(fn: Handler) -> Handler:
        if kind in HANDLERS:
            raise RuntimeError(f"duplicate job handler for {kind!r}")
        HANDLERS[kind] = fn
        return fn

    return register


def _check_payload(payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if not _PAYLOAD_KEY.match(key) or _FORBIDDEN_PAYLOAD_KEY.search(key):
            raise PayloadError(f"payload key {key!r} is not allowed (IDs only)")
        if not (value is None or isinstance(value, bool | int) or (isinstance(value, str) and len(value) <= 64)):
            raise PayloadError(f"payload value for {key!r} must be an ID, number, flag or short string")


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    org_id: uuid.UUID | None,
    priority: int = PRIORITY_TRANSACTIONAL,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> uuid.UUID | None:
    """Add a job to the caller's transaction. Returns its ID, or None when a job with the same
    dedupe_key is already queued or running (then nothing is added)."""
    payload = payload or {}
    _check_payload(payload)
    # Written as SQL so the ON CONFLICT predicate is literal text: PostgreSQL must be able to
    # match it to the partial unique index jobs_dedupe_uq, which it can't do with parameters.
    return db.execute(
        text(
            """
            INSERT INTO jobs (org_id, kind, payload, priority, run_at, dedupe_key, max_attempts)
            VALUES (:org_id, :kind, CAST(:payload AS jsonb), :priority, COALESCE(:run_at, now()),
                    :dedupe_key, :max_attempts)
            ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')
            DO NOTHING
            RETURNING id
            """
        ),
        {
            "org_id": org_id,
            "kind": kind,
            "payload": json.dumps(payload),
            "priority": priority,
            "run_at": run_at,
            "dedupe_key": dedupe_key,
            "max_attempts": max_attempts,
        },
    ).scalar_one_or_none()


def backoff(attempts: int) -> timedelta:
    """30 s, 1 min, 2 min, 4 min, ... capped at 1 hour."""
    delay: timedelta = BACKOFF_BASE * (2 ** max(0, attempts - 1))
    return min(BACKOFF_CAP, delay)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def claim(worker: str, database: str | None = None) -> ClaimedJob | None:
    with transaction(None, role="hms_worker", database=database) as db:
        row = db.execute(
            text(
                """
                UPDATE jobs SET status = 'running', locked_by = :worker, locked_at = now(),
                                attempts = attempts + 1
                WHERE id = (SELECT id FROM jobs
                            WHERE status = 'queued' AND run_at <= now()
                            ORDER BY priority, run_at, id
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED)
                RETURNING id, org_id, kind, payload, priority, attempts, max_attempts
                """
            ),
            {"worker": worker},
        ).one_or_none()
    if row is None:
        return None
    return ClaimedJob(row.id, row.org_id, row.kind, row.payload, row.priority, row.attempts, row.max_attempts)


def _error_text(exc: BaseException) -> str:
    return redact_text(f"{type(exc).__name__}: {exc}")[:500]


def run_claimed(job: ClaimedJob, worker: str, database: str | None = None) -> str:
    """Run one claimed job. Returns the outcome: 'succeeded', 'retry', 'postponed' or 'dead'."""
    fn = HANDLERS.get(job.kind)
    try:
        if fn is None:
            raise LookupError(f"no handler for job kind {job.kind!r}")
        if job.org_id is not None and _org_suspended(job.org_id, database):
            # Q14: a suspended organization's jobs and emails stop; nothing is lost, they run after
            # reactivation. Re-checked hourly.
            raise RetryLater(datetime.now(UTC) + timedelta(hours=1))
        with transaction(job.org_id, role="hms_worker", database=database) as db:
            fn(db, job)
            done = db.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == "running", Job.locked_by == worker)
                .values(status="succeeded", finished_at=func.now(), locked_at=None, locked_by=None, last_error=None)
            )
            if rowcount(done) != 1:
                # Reaped and taken by another worker meanwhile: undo our work.
                raise RuntimeError("job is no longer ours")
        return "succeeded"
    except RetryLater as later:
        with transaction(None, role="hms_worker", database=database) as db:
            db.execute(
                update(Job)
                .where(Job.id == job.id, Job.locked_by == worker)
                .values(status="queued", run_at=later.run_at, attempts=Job.attempts - 1, locked_at=None, locked_by=None)
            )
        return "postponed"
    except Exception as exc:
        dead = job.attempts >= job.max_attempts
        log.warning(
            "job failed",
            extra={
                "job_id": str(job.id),
                "kind": job.kind,
                "attempt": job.attempts,
                "dead": dead,
                "error": _error_text(exc),
            },
        )
        values: dict[str, Any] = {"locked_at": None, "locked_by": None, "last_error": _error_text(exc)}
        if dead:
            values |= {"status": "dead", "finished_at": func.now()}
        else:
            values |= {"status": "queued", "run_at": func.now() + backoff(job.attempts)}
        with transaction(None, role="hms_worker", database=database) as db:
            db.execute(update(Job).where(Job.id == job.id, Job.locked_by == worker).values(**values))
        return "dead" if dead else "retry"


def _org_suspended(org_id: uuid.UUID, database: str | None) -> bool:
    with transaction(None, role="hms_worker", database=database) as db:
        return db.scalar(select(Organization.status).where(Organization.id == org_id)) == "suspended"


def work_once(worker: str, database: str | None = None) -> str | None:
    """Claim and run at most one job. Returns the outcome, or None if nothing was due."""
    job = claim(worker, database)
    return None if job is None else run_claimed(job, worker, database)


# --- Maintenance ----------------------------------------------------------------------------

_REAP_SQL = text(
    """
    UPDATE jobs SET
      status      = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
      finished_at = CASE WHEN attempts >= max_attempts THEN now() END,
      last_error  = 'worker stopped while running the job',
      locked_at = NULL, locked_by = NULL
    WHERE status = 'running' AND id <> :me AND locked_at < now() - make_interval(secs => :secs)
    """
)


def reap_stuck(db: Session, me: uuid.UUID | None = None) -> int:
    """Put jobs whose worker died back in the queue (or mark them dead if out of attempts)."""
    result = db.execute(_REAP_SQL, {"me": me or uuid.UUID(int=0), "secs": STUCK_AFTER.total_seconds()})
    return rowcount(result)


# How long expiring security data is kept. PROPOSAL values (Checkpoint A report).
PURGE_RULES: tuple[tuple[str, str], ...] = (
    ("login_attempts", "attempted_at < now() - interval '30 days'"),
    ("email_otps", "expires_at < now() - interval '1 day'"),
    ("auth_tokens", "expires_at < now() - interval '30 days'"),
)


@handler("maintenance.purge")
def purge(db: Session, job: ClaimedJob) -> None:
    """Delete expired security data. The tenant tables are under RLS and the worker has no
    bypass, so it walks the organizations, switching tenant context for each one."""
    db.execute(text("DELETE FROM jobs WHERE status = 'succeeded' AND finished_at < now() - interval '14 days'"))
    db.execute(text("DELETE FROM jobs WHERE status = 'dead' AND finished_at < now() - interval '30 days'"))
    from app.modules.files.service import purge_unattached
    from app.modules.registration.service import purge as purge_student_requests
    from app.modules.students.service import end_read_only_windows

    for org_id in db.scalars(select(Organization.id).order_by(Organization.id)).all():
        set_tenant(db, org_id)
        for table, condition in PURGE_RULES:  # fixed strings above, never user input
            db.execute(text(f"DELETE FROM {table} WHERE {condition}"))  # noqa: S608
        purge_unattached(db)  # uploads nobody attached within 24 hours (05 DOC-8)
        end_read_only_windows(db)  # Q22: 90 days after exit the account is deactivated
        purge_student_requests(db)  # D74: unverified after 24 hours; rejected personal data after 30 days
    set_tenant(db, None)


def for_each_active_org(db: Session, fn: Callable[[Session], object]) -> None:
    """Run `fn` once per ACTIVE organization, each under its own tenant context. Suspended
    organizations are skipped: their scheduled work stops while suspended (Q14, 05 MT-16)."""
    for org_id in db.scalars(
        select(Organization.id).where(Organization.status == "active").order_by(Organization.id)
    ).all():
        set_tenant(db, org_id)
        fn(db)
    set_tenant(db, None)


@handler("maintenance.reap_stuck")
def _reap(db: Session, job: ClaimedJob) -> None:
    reap_stuck(db, me=job.id)


# --- Scheduler ------------------------------------------------------------------------------


@dataclass
class Periodic:
    kind: str
    every: timedelta


@dataclass
class Scheduler:
    """Enqueues periodic platform-wide jobs, but only while holding the advisory lock."""

    tasks: list[Periodic] = field(
        default_factory=lambda: [
            Periodic("maintenance.reap_stuck", timedelta(minutes=5)),
            Periodic("maintenance.purge", timedelta(hours=6)),
            Periodic("allocations.expire_reservations", timedelta(hours=1)),
            Periodic("fees.late_fees", timedelta(hours=6)),
            Periodic("complaints.auto_close", timedelta(hours=1)),
        ]
    )
    database: str | None = None
    _conn: Connection | None = None
    _last: dict[str, float] = field(default_factory=dict)

    def try_lead(self) -> bool:
        """Session-level advisory lock on a connection we keep open. If this process dies,
        PostgreSQL releases the lock and another worker's scheduler takes over."""
        if self._conn is None:
            self._conn = engine("hms_worker", self.database).connect()
            self._conn.commit()
        got = bool(self._conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LOCK_KEY}).scalar())
        self._conn.commit()
        if not got:
            self.close()
        return got

    def is_leader(self) -> bool:
        return self._conn is not None

    def tick(self, now: float | None = None) -> list[str]:
        """Enqueue every task that is due. Returns the kinds enqueued."""
        if not self.is_leader() and not self.try_lead():
            return []
        now = time.time() if now is None else now
        due = [t for t in self.tasks if now - self._last.get(t.kind, 0.0) >= t.every.total_seconds()]
        if not due:
            return []
        with transaction(None, role="hms_worker", database=self.database) as db:
            for task in due:
                slot = int(now // task.every.total_seconds())
                enqueue(db, task.kind, org_id=None, priority=PRIORITY_BULK, dedupe_key=f"sched:{task.kind}:{slot}")
        for task in due:
            self._last[task.kind] = now
        return [t.kind for t in due]

    def close(self) -> None:
        if self._conn is not None:
            # A session-level advisory lock lives as long as the database SESSION. Returning the
            # connection to the pool would keep the session (and the lock) alive, so the
            # connection is invalidated: really closed, and the lock released with it.
            self._conn.invalidate()
            self._conn.close()
            self._conn = None
