"""Complaint background jobs."""

from sqlalchemy.orm import Session

from app.core import jobs
from app.modules.complaints.router import auto_close


@jobs.handler("complaints.auto_close")
def close_resolved(db: Session, job: jobs.ClaimedJob) -> None:
    """Hourly, platform-wide: resolved complaints close after the 7-day reopen window (Q40)."""
    jobs.for_each_active_org(db, auto_close)
