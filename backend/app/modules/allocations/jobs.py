"""Allocation background jobs."""

from sqlalchemy.orm import Session

from app.core import jobs
from app.modules.allocations import service


@jobs.handler("allocations.expire_reservations")
def expire_reservations(db: Session, job: jobs.ClaimedJob) -> None:
    """Hourly, platform-wide (Q21): release reservations past their expiry, organization by organization."""
    jobs.for_each_active_org(db, service.expire_reservations)
