"""Background worker entry point (`python -m app.worker`), docs/02 §4.4.

Loop: let the scheduler enqueue anything due (only the worker holding the advisory
lock does), then claim and run one job; when the queue is empty, sleep briefly.
Stops cleanly on SIGTERM (docker compose stop) after the current job.
"""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

import app.job_handlers  # noqa: F401  registers every job handler
from app.core import crypto, jobs
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.startup import check_database_role

log = logging.getLogger("hms.worker")
IDLE_SLEEP_SECONDS = 1.0


class _Stop:
    requested = False


def _request_stop(signum: int, frame: FrameType | None) -> None:
    _Stop.requested = True


def main() -> None:
    configure_logging()
    check_database_role("hms_worker")
    crypto.self_test(get_settings().data_keyring())
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    me = jobs.worker_id()
    scheduler = jobs.Scheduler()
    log.info("worker started", extra={"worker": me, "handlers": sorted(jobs.HANDLERS)})
    try:
        while not _Stop.requested:
            try:
                scheduler.tick()
                outcome = jobs.work_once(me)
            except Exception:
                log.exception("worker loop error")  # e.g. database restarting; try again shortly
                scheduler.close()
                outcome = None
            if outcome is None:
                time.sleep(IDLE_SLEEP_SECONDS)
    finally:
        scheduler.close()
        log.info("worker stopped", extra={"worker": me})


if __name__ == "__main__":
    main()
