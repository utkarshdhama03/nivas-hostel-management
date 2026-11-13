"""Background jobs: outbox, SKIP LOCKED, retries, dead jobs, scheduler, purge (docs/02 §4.4).

The jobs table is shared by the whole test database, so every test starts from an
empty queue (the `empty_queue` fixture) and the tests in this file run one by one.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import jobs
from app.core.db import transaction
from app.core.models import Job
from app.modules.identity.models import LoginAttempt
from app.modules.notifications.models import Notification
from tests.support import Org

SEEN: dict[str, Any] = {}


@jobs.handler("test.record_context")
def _record_context(db: Session, job: jobs.ClaimedJob) -> None:
    SEEN["org_setting"] = db.execute(text("SELECT current_setting('app.org_id', true)")).scalar()
    SEEN["visible_users_orgs"] = db.execute(text("SELECT count(DISTINCT org_id) FROM users")).scalar()
    SEEN["other_user_visible"] = db.execute(
        text("SELECT count(*) FROM users WHERE id = :id"), {"id": job.payload["other_user_id"]}
    ).scalar()


@jobs.handler("test.notify_then_fail")
def _notify_then_fail(db: Session, job: jobs.ClaimedJob) -> None:
    db.add(
        Notification(
            org_id=job.org_id,
            user_id=uuid.UUID(job.payload["user_id"]),
            category="security",
            title="from a failing job",
            body="must be rolled back",
        )
    )
    db.flush()
    raise RuntimeError("handler failed token=abc123")


@jobs.handler("test.notify")
def _notify(db: Session, job: jobs.ClaimedJob) -> None:
    db.add(
        Notification(
            org_id=job.org_id,
            user_id=uuid.UUID(job.payload["user_id"]),
            category="security",
            title="from a job",
            body="ok",
        )
    )


@jobs.handler("test.postpone")
def _postpone(db: Session, job: jobs.ClaimedJob) -> None:
    raise jobs.RetryLater(datetime.now(UTC) + timedelta(hours=1))


@pytest.fixture(autouse=True)
def empty_queue(app_db: str) -> Iterator[None]:
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
    yield


def _job(job_id: uuid.UUID) -> Job:
    with transaction(None, role="hms_worker") as db:
        job = db.get(Job, job_id)
        assert job is not None
        return job


def _enqueue(org: Org | None, kind: str, payload: dict[str, Any] | None = None, **kw: Any) -> uuid.UUID:
    with transaction(org.id if org else None, role="hms_app" if org else "hms_worker") as db:
        job_id = jobs.enqueue(db, kind, payload, org_id=org.id if org else None, **kw)
    assert job_id is not None
    return job_id


# --- enqueue -----------------------------------------------------------------------------------


def test_rolled_back_transaction_leaves_no_job(org_a: Org) -> None:
    with pytest.raises(RuntimeError), transaction(org_a.id) as db:
        jobs.enqueue(db, "test.notify", {"user_id": str(org_a.people["student"].user_id)}, org_id=org_a.id)
        raise RuntimeError("business change failed")
    with transaction(None, role="hms_worker") as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.parametrize(
    "payload",
    [{"email": "a@b.c"}, {"reset_token": "x"}, {"user_id": "x" * 65}, {"user_id": {"nested": 1}}, {"Bad-Key": 1}],
)
def test_payload_must_be_ids_only(org_a: Org, payload: dict[str, Any]) -> None:
    with pytest.raises(jobs.PayloadError), transaction(org_a.id) as db:
        jobs.enqueue(db, "test.notify", payload, org_id=org_a.id)


def test_app_cannot_enqueue_for_another_org(org_a: Org, org_b: Org) -> None:
    """RLS WITH CHECK on jobs: the tenant can only create its own organization's jobs."""
    with pytest.raises(Exception, match="row-level security"), transaction(org_a.id) as db:
        jobs.enqueue(db, "test.notify", {}, org_id=org_b.id)


def test_dedupe_key(org_a: Org) -> None:
    _enqueue(org_a, "test.notify", {"user_id": str(org_a.people["student"].user_id)}, dedupe_key="d-1")
    with transaction(org_a.id) as db:
        assert jobs.enqueue(db, "test.notify", {}, org_id=org_a.id, dedupe_key="d-1") is None


# --- running ----------------------------------------------------------------------------------


def test_job_runs_under_its_own_tenant_context(org_a: Org, org_b: Org) -> None:
    job_id = _enqueue(org_a, "test.record_context", {"other_user_id": str(org_b.people["admin"].user_id)})
    assert jobs.work_once("w1") == "succeeded"
    assert SEEN == {"org_setting": str(org_a.id), "visible_users_orgs": 1, "other_user_visible": 0}
    job = _job(job_id)
    assert job.status == "succeeded" and job.finished_at is not None and job.locked_at is None


def test_success_commits_handler_work(org_a: Org) -> None:
    user_id = org_a.people["student"].user_id
    _enqueue(org_a, "test.notify", {"user_id": str(user_id)})
    assert jobs.work_once("w1") == "succeeded"
    with transaction(org_a.id) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, Notification.title == "from a job")
            )
            >= 1
        )


def test_failure_rolls_back_and_retries_with_backoff(org_a: Org) -> None:
    user_id = org_a.people["student"].user_id
    job_id = _enqueue(org_a, "test.notify_then_fail", {"user_id": str(user_id)}, max_attempts=2)
    assert jobs.work_once("w1") == "retry"
    job = _job(job_id)
    assert job.status == "queued" and job.attempts == 1 and job.locked_by is None
    assert job.last_error is not None and "abc123" not in job.last_error  # redacted
    with transaction(None, role="hms_worker") as db:
        delay = db.scalar(select(Job.run_at - func.now()).where(Job.id == job_id))
    assert timedelta(seconds=20) < delay <= timedelta(seconds=30)
    with transaction(org_a.id) as db:  # the handler's notification was rolled back
        assert (
            db.scalar(select(func.count()).select_from(Notification).where(Notification.title == "from a failing job"))
            == 0
        )

    assert jobs.work_once("w1") is None  # not due yet
    with transaction(None, role="hms_worker") as db:
        db.execute(text("UPDATE jobs SET run_at = now() WHERE id = :id"), {"id": job_id})
    assert jobs.work_once("w1") == "dead"
    job = _job(job_id)
    assert job.status == "dead" and job.attempts == 2 and job.finished_at is not None


def test_backoff_schedule() -> None:
    assert [jobs.backoff(n).total_seconds() for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert jobs.backoff(20) == timedelta(hours=1)


def test_unknown_kind_goes_dead_eventually(org_a: Org) -> None:
    job_id = _enqueue(org_a, "test.nobody_handles_this", max_attempts=1)
    assert jobs.work_once("w1") == "dead"
    assert "no handler" in (_job(job_id).last_error or "")


def test_retry_later_does_not_use_an_attempt(org_a: Org) -> None:
    job_id = _enqueue(org_a, "test.postpone")
    assert jobs.work_once("w1") == "postponed"
    job = _job(job_id)
    assert job.status == "queued" and job.attempts == 0


def test_priority_order(org_a: Org) -> None:
    uid = str(org_a.people["student"].user_id)
    bulk = _enqueue(org_a, "test.notify", {"user_id": uid}, priority=jobs.PRIORITY_BULK)
    security = _enqueue(org_a, "test.notify", {"user_id": uid}, priority=jobs.PRIORITY_SECURITY)
    first = jobs.claim("w1")
    assert first is not None and first.id == security
    jobs.run_claimed(first, "w1")
    second = jobs.claim("w1")
    assert second is not None and second.id == bulk


def test_skip_locked_two_workers_never_share_a_job(org_a: Org, settings: Any) -> None:
    """While worker 1 holds job A's row lock, worker 2 neither waits nor takes A: it gets B."""
    uid = str(org_a.people["student"].user_id)
    a = _enqueue(org_a, "test.notify", {"user_id": uid})
    b = _enqueue(org_a, "test.notify", {"user_id": uid})
    with psycopg.connect(settings.conninfo("hms_worker", settings.hms_db_name)) as w1:
        w1.execute("SELECT id FROM jobs WHERE id = %s FOR UPDATE", (a,))
        got: list[Any] = []
        t = threading.Thread(target=lambda: got.append(jobs.claim("w2")))
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "worker 2 was blocked by worker 1's lock"
        assert got[0] is not None and got[0].id == b
        assert jobs.claim("w2") is None  # A is still locked, nothing else queued
        w1.rollback()


def test_job_taken_over_is_not_completed_twice(org_a: Org) -> None:
    """A job reaped from a 'dead' worker and claimed by another: the first worker's late
    success must not mark it done or keep its side effects."""
    uid = org_a.people["student"].user_id
    job_id = _enqueue(org_a, "test.notify", {"user_id": str(uid)})
    claimed = jobs.claim("slow-worker")
    assert claimed is not None
    with transaction(None, role="hms_worker") as db:
        db.execute(text("UPDATE jobs SET locked_by = 'other-worker' WHERE id = :id"), {"id": job_id})
    before = _count_from_job(org_a, uid)
    assert jobs.run_claimed(claimed, "slow-worker") != "succeeded"
    assert _count_from_job(org_a, uid) == before
    assert _job(job_id).locked_by == "other-worker"


def _count_from_job(org: Org, uid: uuid.UUID) -> int:
    with transaction(org.id) as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == uid, Notification.title == "from a job")
            )
            or 0
        )


def test_reaper_requeues_stuck_jobs(org_a: Org) -> None:
    job_id = _enqueue(org_a, "test.notify", {"user_id": str(org_a.people["student"].user_id)})
    assert jobs.claim("crashed-worker") is not None
    with transaction(None, role="hms_worker") as db:
        db.execute(text("UPDATE jobs SET locked_at = now() - interval '1 hour' WHERE id = :id"), {"id": job_id})
        assert jobs.reap_stuck(db) == 1
    job = _job(job_id)
    assert job.status == "queued" and job.locked_by is None and job.last_error


# --- scheduler and purge -------------------------------------------------------------------------


def test_only_one_scheduler_leads() -> None:
    s1, s2 = jobs.Scheduler(), jobs.Scheduler()
    try:
        assert sorted(s1.tick(now=1_000_000.0)) == [
            "allocations.expire_reservations",
            "complaints.auto_close",
            "fees.late_fees",
            "maintenance.purge",
            "maintenance.reap_stuck",
        ]
        assert s2.tick(now=1_000_000.0) == []  # lock held by s1
        assert s1.tick(now=1_000_010.0) == []  # nothing due yet
        s1.close()  # e.g. that worker stopped: the lock is released
        assert s2.tick(now=1_000_000.0) != []
    finally:
        s1.close()
        s2.close()
    with transaction(None, role="hms_worker") as db:
        kinds = db.scalars(select(Job.kind).where(Job.org_id.is_(None))).all()
    assert sorted(kinds) == [
        "allocations.expire_reservations",
        "complaints.auto_close",
        "fees.late_fees",
        "maintenance.purge",
        "maintenance.reap_stuck",
    ]  # second batch deduplicated


def test_purge_walks_every_organization(org_a: Org, org_b: Org) -> None:
    ids: dict[str, uuid.UUID] = {}
    for org in (org_a, org_b):
        with transaction(org.id) as db:
            # Login attempts are security data on the database's real clock (D67), like the purge.
            real_now = db.scalar(select(func.now()))
            for label, when in (("old", real_now - timedelta(days=31)), ("new", real_now)):
                row = LoginAttempt(org_id=org.id, identifier_hash="h" * 64, succeeded=False, attempted_at=when)
                db.add(row)
                db.flush()
                ids[f"{org.slug}-{label}"] = row.id
    _enqueue(None, "maintenance.purge", priority=jobs.PRIORITY_BULK)
    assert jobs.work_once("w1") == "succeeded"
    for org in (org_a, org_b):
        with transaction(org.id) as db:
            assert db.get(LoginAttempt, ids[f"{org.slug}-old"]) is None
            assert db.get(LoginAttempt, ids[f"{org.slug}-new"]) is not None
