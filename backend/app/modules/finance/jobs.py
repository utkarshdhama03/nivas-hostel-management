"""Finance background jobs: charge runs (Q24) and late fees (Q26). Both are safe to rerun."""

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core import jobs
from app.modules.allocations.service import local_today
from app.modules.finance import service


@jobs.handler("fees.charge_run")
def charge_run(db: Session, job: jobs.ClaimedJob) -> None:
    """Runs under the organization's tenant context (the job carries its org_id)."""
    service.run_charges(
        db,
        uuid.UUID(str(job.payload["fee_structure_id"])),
        date.fromisoformat(str(job.payload["period_start"])),
        uuid.UUID(str(job.payload["requested_by"])) if job.payload.get("requested_by") else None,
    )


@jobs.handler("fees.late_fees")
def late_fees(db: Session, job: jobs.ClaimedJob) -> None:
    """Platform-wide, every 6 hours; each active organization uses its own local date."""
    jobs.for_each_active_org(db, lambda d: service.apply_late_fees(d, local_today(d)))
