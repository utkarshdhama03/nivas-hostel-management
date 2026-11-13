"""Regression tests for the time bugs found after Phase 10 (D66).

1. Complaint dates in analytics and the complaints CSV were the UTC date. Both were removed (D86).
2. The email daily cap: counted per UTC day and retried at 00:05 UTC. Consistent, but the count used
   the database session's zone implicitly; now it says UTC, so a session in another zone can't move it.
(The frontend's UTC "today" defaults are covered by frontend/src/lib/dates.test.ts.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import transaction
from app.modules.notifications import service as notifications
from tests.test_phase8 import W, client, complain, ok, w  # noqa: F401  (fixtures)

API = "/api/v1"


@pytest.mark.parametrize(
    ("now", "retry"),
    [
        ("2026-11-09T23:59:00+00:00", "2026-11-10T00:05:00+00:00"),  # last minute of a UTC day
        ("2026-11-10T00:01:00+00:00", "2026-11-11T00:05:00+00:00"),  # first minute of the next
        ("2026-11-10T05:00:00+05:30", "2026-11-10T00:05:00+00:00"),  # an IST time is converted first
    ],
)
def test_capped_email_retries_when_the_next_utc_cap_day_starts(now: str, retry: str) -> None:
    assert notifications.next_cap_day(datetime.fromisoformat(now)) == datetime.fromisoformat(retry)


def test_email_cap_counts_the_utc_day_whatever_the_session_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    def cap_reached_in(zone: str) -> bool:
        with transaction(None, role="hms_worker") as db:
            db.execute(text(f"SET LOCAL TIME ZONE '{zone}'"))
            return notifications._daily_cap_reached(db)

    with transaction(None, role="hms_worker") as db:
        sent_today: Any = db.scalar(
            text(
                "SELECT count(*) FROM jobs WHERE kind LIKE 'email.%' AND status = 'succeeded' "
                "AND finished_at >= date_trunc('day', now(), 'UTC')"
            )
        )
        midnight_utc = db.scalar(text("SELECT date_trunc('day', now(), 'UTC')"))
        assert midnight_utc.astimezone(UTC).hour == 0
    monkeypatch.setattr(notifications.get_settings(), "hms_email_daily_cap", sent_today + 1)
    with transaction(None, role="hms_worker") as db:  # one email just BEFORE the UTC day began
        db.execute(
            text(
                "INSERT INTO jobs (kind, status, payload, finished_at) "
                "VALUES ('email.test', 'succeeded', '{}', date_trunc('day', now(), 'UTC') - interval '1 minute')"
            )
        )
    # In an IST session, "today" would start 5.5 hours earlier and count that email: the old code did.
    assert not cap_reached_in("Asia/Kolkata") and not cap_reached_in("UTC")
