"""Boundary tests at fixed tricky moments (D67, D68). The clock is moved with time-machine; the
database follows through app.now. India is UTC+05:30, so between 18:30 and 23:59 UTC the
organization's date is already tomorrow: every check below states which date it expects and why.

  * 23:59 / 00:01 IST: the local-outing same-day rule; a roll-call window that crosses midnight.
  * 18:30-23:59 UTC: "today" (check-out end date, complaint target date).
  * 31 March -> 1 April: the receipt series restarts with the financial year (Q29).
  * Month end: monthly periods are calendar months (D68); late fees start on the local day after
    the due date, including when the month changes in India but not yet in UTC.
  * 31 December -> 1 January: receipts continue (April-March year); charge periods roll over.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import time_machine
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text

import app.job_handlers  # noqa: F401  the worker's handlers
from app.core import jobs
from app.core.db import transaction
from app.core.errors import ProblemError
from app.main import create_app
from app.modules.allocations.service import local_today
from app.modules.finance import service as finance
from app.modules.identity import sessions
from tests.support import Org, Person, activation_token, add_person, admit, make_hostel, make_org

API = "/api/v1"
IST = ZoneInfo("Asia/Kolkata")


def ist(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


def ok(r: Response, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


@dataclass
class World:
    org: Org
    sa: Person
    acc: Person  # the one Administrator again (D76), named for what it does here: records money
    hostel: dict[str, Any]
    student: dict[str, Any]  # admitted through the API, living in the hostel's bed since 40 days ago
    allocation_id: str


def build(client: TestClient) -> World:
    """An organization at the CURRENT (travelled) time: build it inside the time_machine block."""
    today = datetime.now(IST).date()
    org = make_org()
    sa = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    h = make_hostel(client, sa, "H1")
    session = ok(
        client.post(
            f"{API}/admin/academic-sessions",
            headers=sa.headers(),
            json={
                "name": f"S-{uuid.uuid4().hex[:6]}",
                "starts_on": str(today - timedelta(days=150)),
                "ends_on": str(today + timedelta(days=200)),
            },
        ),
        201,
    )
    ok(client.post(f"{API}/admin/academic-sessions/{session['id']}/make-current", headers=sa.headers()))

    student = admit(client, sa, h["id"])
    alloc = ok(
        client.post(
            f"{API}/admin/allocations",
            headers=sa.headers(),
            json={"student_id": student["id"], "bed_id": h["bed"]["id"], "start_date": str(today - timedelta(days=40))},
        ),
        201,
    )
    return World(
        org,
        sa,
        sa,  # acc: the one Administrator records the money (D76)
        h,
        student,
        alloc["id"],
    )


def org_today(w: World) -> date:
    with transaction(w.org.id) as db:
        return local_today(db)


def pay(client: TestClient, w: World, received_on: date, amount: str, who: Person | None = None) -> str:
    r = client.post(
        f"{API}/admin/payments",
        headers={**(who or w.acc).headers(), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "student_id": w.student["id"],
            "method": "cash",
            "reference": None,
            "received_on": str(received_on),
            "dues_amount": amount,
            "deposit_amount": "0",
        },
    )
    return str(ok(r, 201)["receipt_number"])


def fee_structure(client: TestClient, w: World, **kw: Any) -> dict[str, Any]:
    body = {"hostel_id": w.hostel["id"], "name": "Rent", "fee_kind": "rent", "amount": "4000.00"}
    return dict(ok(client.post(f"{API}/admin/fee-structures", headers=w.acc.headers(), json={**body, **kw}), 201))


def charge_period(w: World, fs: dict[str, Any], start: date) -> None:
    with transaction(w.org.id) as db:
        finance.run_charges(db, uuid.UUID(fs["id"]), start, None)


def charges(w: World) -> list[tuple[str, str, str]]:
    with transaction(w.org.id) as db:
        rows = db.execute(
            text(
                "SELECT period_start, period_end, due_date FROM ledger_entries "
                "WHERE student_id = :s AND entry_type = 'charge' ORDER BY period_start"
            ),
            {"s": w.student["id"]},
        ).all()
    return [(str(a), str(b), str(c)) for a, b, c in rows]


# --- 31 March -> 1 April: financial year and receipt counter ---------------------------------------------------------


def test_receipt_series_restarts_at_midnight_ist_on_1_april(client: TestClient) -> None:
    with time_machine.travel(ist(2051, 3, 31, 23, 59), tick=True):
        w = build(client)
        assert org_today(w) == date(2051, 3, 31)
        assert pay(client, w, org_today(w), "10.00") == "RCPT/2050-51/000001"
    with time_machine.travel(ist(2051, 4, 1, 0, 1), tick=True):
        assert datetime.now(UTC).date() == date(2051, 3, 31)  # UTC is still in the old financial year
        today = org_today(w)
        assert today == date(2051, 4, 1)
        assert pay(client, w, today, "11.00") == "RCPT/2051-52/000001"  # a new series starts at 1
        assert pay(client, w, today, "12.00") == "RCPT/2051-52/000002"
        # A late entry for a payment taken on 31 March keeps that year's series.
        assert pay(client, w, date(2051, 3, 31), "13.00") == "RCPT/2050-51/000002"


# --- 31 December -> 1 January ------------------------------------------------------------------------------------


def test_new_year_continues_receipts_and_rolls_charge_periods(client: TestClient) -> None:
    with time_machine.travel(ist(2050, 12, 31, 23, 59), tick=True):
        w = build(client)
        assert pay(client, w, org_today(w), "10.00") == "RCPT/2050-51/000001"
        fs = fee_structure(client, w, billing_cycle="monthly", due_days=10)
        charge_period(w, fs, date(2050, 12, 1))
    with time_machine.travel(ist(2051, 1, 1, 0, 1), tick=True):
        assert datetime.now(UTC).date() == date(2050, 12, 31)
        assert org_today(w) == date(2051, 1, 1)
        # The financial year runs April to March: 1 January continues the same series.
        assert pay(client, w, org_today(w), "11.00") == "RCPT/2050-51/000002"
        charge_period(w, fs, date(2051, 1, 1))
    assert charges(w) == [
        ("2050-12-01", "2050-12-31", "2050-12-11"),
        ("2051-01-01", "2051-01-31", "2051-01-11"),
    ]


# --- month end: billing periods (D68) and late fees ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2052, 2, 1), date(2052, 2, 29)),  # leap year
        (date(2051, 2, 1), date(2051, 2, 28)),
        (date(2051, 4, 1), date(2051, 4, 30)),
        (date(2051, 12, 1), date(2051, 12, 31)),
    ],
)
def test_monthly_period_is_the_calendar_month(start: date, end: date) -> None:
    monthly: Any = SimpleNamespace(billing_cycle="monthly")
    assert finance.period_end(None, monthly, start) == end  # type: ignore[arg-type]  # monthly needs no db


def test_monthly_period_must_start_on_the_first(client: TestClient) -> None:
    """D68: a run from the 15th used to bill a whole month for half of one, and a second run from the
    1st charged the same month twice (charges are unique per period START, not per month)."""
    w = build(client)
    fs = fee_structure(client, w, billing_cycle="monthly")
    first = org_today(w).replace(day=1)
    for bad in (first + timedelta(days=14), first.replace(day=28)):
        r = client.post(
            f"{API}/admin/charge-runs",
            headers=w.acc.headers(),
            json={"fee_structure_id": fs["id"], "period_start": str(bad)},
        )
        assert r.status_code == 422 and "1st of a month" in r.json()["title"], r.text
    with pytest.raises(ProblemError) as refused:
        charge_period(w, fs, first + timedelta(days=14))  # the job refuses too, not only the API
    assert "1st of a month" in refused.value.title
    ok(
        client.post(
            f"{API}/admin/charge-runs",
            headers=w.acc.headers(),
            json={"fee_structure_id": fs["id"], "period_start": str(first)},
        ),
        202,
    )


def _late_fee_run() -> None:
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs WHERE kind = 'fees.late_fees'"))
        jobs.enqueue(db, "fees.late_fees", org_id=None)
    while jobs.work_once("w") is not None:
        pass


def _late_fees(w: World) -> list[tuple[str, str]]:
    with transaction(w.org.id) as db:
        rows = db.execute(
            text(
                "SELECT late_fee_date, amount FROM ledger_entries WHERE entry_type = 'late_fee' AND student_id = :s "
                "ORDER BY late_fee_date"
            ),
            {"s": w.student["id"]},
        ).all()
    return [(str(d), f"{a:.2f}") for d, a in rows]


def test_late_fees_start_on_the_local_day_after_a_month_end_due_date(client: TestClient) -> None:
    with time_machine.travel(ist(2051, 2, 10, 12, 0), tick=True):
        w = build(client)
        fs = fee_structure(
            client,
            w,
            billing_cycle="monthly",
            due_days=27,
            grace_days=0,
            late_fee_type="per_day",
            late_fee_amount="50.00",
            late_fee_cap="175.00",
        )
        charge_period(w, fs, date(2051, 2, 1))
    assert charges(w) == [("2051-02-01", "2051-02-28", "2051-02-28")]  # due on the last day of February
    with time_machine.travel(ist(2051, 2, 28, 23, 59), tick=True):
        _late_fee_run()
        assert _late_fees(w) == []  # still the due date in India
    with time_machine.travel(ist(2051, 3, 1, 0, 1), tick=True):
        assert datetime.now(UTC).date() == date(2051, 2, 28)  # February in UTC, March in India
        _late_fee_run()
        _late_fee_run()  # a rerun adds nothing
        assert _late_fees(w) == [("2051-03-01", "50.00")]
    with time_machine.travel(ist(2051, 3, 4, 0, 1), tick=True):
        _late_fee_run()
        assert _late_fees(w) == [
            ("2051-03-01", "50.00"),
            ("2051-03-02", "50.00"),
            ("2051-03-03", "50.00"),
            ("2051-03-04", "25.00"),  # the cap (175) reached
        ]


# --- 23:59 / 00:01 IST ---------------------------------------------------------------------------------------------


def _portal_student(org: Org, code: str) -> Person:
    return add_person(org, f"st{code[-3:]}", kind="student", code=code)  # creates hostel T1 (tests/support.py)


def _outing(client: TestClient, st: Person, start: datetime, end: datetime) -> Response:
    return client.post(
        f"{API}/portal/me/leave-requests",
        headers=st.headers(),
        json={
            "leave_type": "local_outing",
            "reason": "Market",
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
        },
    )


def test_local_outing_same_day_rule_at_midnight_ist(client: TestClient) -> None:
    with time_machine.travel(ist(2050, 7, 1, 23, 50), tick=True):
        org = make_org()
        st = _portal_student(org, "STU-2050-0101")
        assert _outing(client, st, ist(2050, 7, 1, 23, 52), ist(2050, 7, 1, 23, 58)).status_code == 201
        crossing = _outing(client, st, ist(2050, 7, 1, 23, 59), ist(2050, 7, 2, 0, 5))
        assert crossing.status_code == 422 and "same day" in crossing.json()["title"], crossing.text
    with time_machine.travel(ist(2050, 7, 2, 0, 1), tick=True):
        # In UTC this is still 1 July (18:31); the rule uses the organization's day: a new day has begun.
        assert _outing(client, st, ist(2050, 7, 2, 0, 5), ist(2050, 7, 2, 0, 30)).status_code == 201


# --- 18:30-23:59 UTC: the organization's date is already tomorrow --------------------------------------------------


def test_between_1830_and_2359_utc_today_is_the_next_indian_day(client: TestClient) -> None:
    with time_machine.travel(datetime(2050, 7, 1, 20, 0, tzinfo=UTC), tick=True):  # 01:30 IST on 2 July
        w = build(client)
        assert datetime.now(UTC).date() == date(2050, 7, 1) and org_today(w) == date(2050, 7, 2)
        # Check-out ends the stay on the organization's date.
        out = ok(client.post(f"{API}/admin/allocations/{w.allocation_id}/check-out", headers=w.sa.headers()))
        assert out["end_date"] == "2050-07-02"
        # A complaint's target date counts from the organization's date (high: 3 days). (A second
        # organization: the portal-student helper creates its own current academic session.)
        other = make_org()
        admin = add_person(other, "admin", roles=("super_admin",), code="E-0001")
        st = _portal_student(other, "STU-2050-0201")
        cat = ok(client.post(f"{API}/admin/complaint-categories", headers=admin.headers(), json={"name": "Water"}), 201)
        c = ok(
            client.post(
                f"{API}/portal/me/complaints",
                headers=st.headers(),
                json={
                    "category_id": cat["id"],
                    "title": "No water",
                    "description": "Since midnight",
                    "priority": "high",
                },
            ),
            201,
        )
        assert c["target_date"] == "2050-07-05"


def test_backdated_payments_follow_their_payment_date_not_the_recording_date(client: TestClient) -> None:
    """Part 2: money received on 31 March but recorded on or after 1 April belongs to the old
    financial year's receipt series (Q29 follows the payment date), however late it is entered or verified."""
    with time_machine.travel(ist(2051, 3, 30, 12, 0), tick=True):
        w = build(client)
        assert pay(client, w, date(2051, 3, 30), "10.00") == "RCPT/2050-51/000001"
    with time_machine.travel(ist(2051, 3, 31, 22, 0), tick=True):
        student = _activated_login(client, w)  # the student pays by UPI on 31 March
        r = client.post(
            f"{API}/portal/me/payment-submissions",
            headers=student,
            json={"amount": "500.00", "utr": "412300000331", "paid_on": "2051-03-31"},
        )
        assert r.status_code == 201, r.text
        submission = r.json()["id"]
    with time_machine.travel(ist(2051, 4, 1, 0, 30), tick=True):  # 1 April, half past midnight in India
        # The exact case: dated 31 March, recorded on 1 April. It continues the OLD year's series,
        # and a payment dated today opens the new one at 1.
        assert org_today(w) == date(2051, 4, 1)
        assert pay(client, w, date(2051, 3, 31), "9.00") == "RCPT/2050-51/000002"
        assert pay(client, w, org_today(w), "11.00") == "RCPT/2051-52/000001"
    with time_machine.travel(ist(2051, 4, 2, 11, 0), tick=True):  # verified in April
        ok(client.post(f"{API}/admin/payment-submissions/{submission}/verify", headers=w.acc.headers()))
        with transaction(w.org.id) as db:
            number = db.scalar(text("SELECT receipt_number FROM payments WHERE submission_id = :s"), {"s": submission})
        assert number == "RCPT/2050-51/000003", number
    with time_machine.travel(ist(2051, 4, 8, 16, 0), tick=True):  # a cash payment entered a week late
        # (the Administrator's 7-day session has ended by now: they sign in again, a new session)
        with transaction(w.org.id) as db:
            fresh = sessions.start(db, org_id=w.org.id, user_id=w.sa.user_id)
        acc = Person(w.org.id, w.sa.user_id, fresh.row.id, w.sa.email, "staff")
        assert pay(client, w, date(2051, 3, 31), "12.00", acc) == "RCPT/2050-51/000004"
        assert pay(client, w, org_today(w), "13.00", acc) == "RCPT/2051-52/000002"  # both series stay gap-free


def _activated_login(cl: TestClient, world: World) -> dict[str, str]:
    """The admitted student activates the account and signs in, as a real student would."""
    link = {"activation_url": "#token=" + activation_token(cl, world.sa.headers(), world.student)}
    password = "a decent long passphrase"
    token = link["activation_url"].split("#token=")[1]
    ok(cl.post(f"{API}/o/{world.org.slug}/auth/activate", json={"token": token, "password": password}), 204)
    body = ok(
        cl.post(
            f"{API}/o/{world.org.slug}/auth/login",
            json={"portal": "student", "identifier": world.student["student_code"], "password": password},
        )
    )
    return {"Authorization": f"Bearer {body['access_token']}"}
