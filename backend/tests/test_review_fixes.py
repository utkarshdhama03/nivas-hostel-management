"""Regression tests for the independent code review. Each test reproduced its
finding before the fix (it failed), and passes after it."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.db import transaction
from app.main import create_app
from app.modules.allocations.service import expire_reservations
from tests.support import add_person, admit, make_hostel, make_org
from tests.test_finance import (  # noqa: F401  (client and w are fixtures)
    TODAY,
    W,
    _parallel,
    _student_login,
    balance,
    charge,
    client,
    fee_structure,
    ok,
    pay,
    run_jobs,
    w,
)

API = "/api/v1"


def _waive(cl: TestClient, world: W, target: str, amount: str) -> Any:
    return cl.post(
        f"{API}/admin/students/{world.student['id']}/waivers",
        headers=world.acc.headers(),
        json={"target_entry_id": target, "amount": amount, "reason_code": "waiver", "reason_note": "Review test"},
    )


def _reverse(cl: TestClient, world: W, entry: str) -> Any:
    return cl.post(
        f"{API}/admin/ledger-entries/{entry}/reverse", headers=world.acc.headers(), json={"reason": "Wrong charge"}
    )


# --- Critical 1: reversing a waived charge must not leave the waiver as refundable credit -----------------------


def test_reversing_a_waived_charge_reverses_its_waiver(client: TestClient, w: W) -> None:  # noqa: F811
    c = charge(client, w, "1000.00", due=TODAY - timedelta(days=5))
    ok(_waive(client, w, c["id"], "300.00"), 201)
    ok(_reverse(client, w, c["id"]), 201)
    b = balance(client, w)
    # Nothing was ever paid: no dues, and above all no credit.
    assert b["dues"] == "0.00" and b["unallocated_credit"] == "0.00", b
    r = client.post(
        f"{API}/admin/refund-requests",
        headers=w.acc.headers(),
        json={"student_id": w.student["id"], "account": "dues", "amount": "300.00", "reason": "Fake credit"},
    )
    assert r.status_code in (409, 422), r.text  # there is no credit to refund


# --- Critical 2: one payment covering two charges must not block corrections -----------------------------------


def _two_charges_one_payment(cl: TestClient, world: W) -> tuple[dict[str, Any], dict[str, Any]]:
    c1 = charge(cl, world, "1000.00", due=TODAY - timedelta(days=20), desc="Rent month 1")
    c2 = charge(cl, world, "1000.00", due=TODAY - timedelta(days=10), desc="Rent month 2")
    ok(pay(cl, world, dues="1500.00"), 201)  # covers c1 fully and c2 half
    return c1, c2


def test_waiving_a_charge_paid_by_a_shared_payment(client: TestClient, w: W) -> None:  # noqa: F811
    c1, _ = _two_charges_one_payment(client, w)
    ok(_waive(client, w, c1["id"], "1000.00"), 201)
    b = balance(client, w)
    # 2000 charged - 1000 waived - 1500 paid = 500 credit, and the books agree with themselves.
    assert b["dues"] == "-500.00" and b["unallocated_credit"] == "500.00" and b["open_charges"] == [], b


def test_reversing_a_charge_paid_by_a_shared_payment(client: TestClient, w: W) -> None:  # noqa: F811
    c1, _ = _two_charges_one_payment(client, w)
    ok(_reverse(client, w, c1["id"]), 201)
    b = balance(client, w)
    assert b["dues"] == "-500.00" and b["unallocated_credit"] == "500.00" and b["open_charges"] == [], b


# --- Critical 3: term, annual and one-time fees are charged once per period -------------------------------------


def _run(cl: TestClient, world: W, fs: dict[str, Any], start: Any) -> Any:
    return cl.post(
        f"{API}/admin/charge-runs",
        headers=world.acc.headers(),
        json={"fee_structure_id": fs["id"], "period_start": str(start)},
    )


def _charges_for(cl: TestClient, world: W, fs: dict[str, Any]) -> int:
    with transaction(world.org.id) as db:
        return int(
            db.scalar(
                text(
                    "SELECT count(*) FROM ledger_entries WHERE entry_type = 'charge' AND student_id = :s "
                    "AND fee_structure_id = :f"
                ),
                {"s": world.student["id"], "f": fs["id"]},
            )
        )


def test_one_time_and_annual_fees_are_charged_once(client: TestClient, w: W) -> None:  # noqa: F811
    for cycle in ("one_time", "annual"):
        fs = fee_structure(client, w, name=f"Fee {cycle}", fee_kind="other", amount="500.00", billing_cycle=cycle)
        for start in (TODAY - timedelta(days=2), TODAY - timedelta(days=1)):  # "it didn't seem to work"
            ok(_run(client, w, fs, start), 202)
            run_jobs()
        assert _charges_for(client, w, fs) == 1, cycle


def test_term_fee_is_charged_once_per_term(client: TestClient, w: W) -> None:  # noqa: F811
    sessions = ok(client.get(f"{API}/admin/academic-sessions", headers=w.sa.headers()))
    current = next(s for s in sessions if s["is_current"])
    term_start = TODAY - timedelta(days=30)
    ok(
        client.post(
            f"{API}/admin/academic-sessions/{current['id']}/terms",
            headers=w.sa.headers(),
            json={"name": "Term 1", "starts_on": str(term_start), "ends_on": str(TODAY + timedelta(days=60))},
        ),
        201,
    )
    fs = fee_structure(client, w, name="Term fee", amount="9000.00", billing_cycle="term")
    for start in (term_start, term_start + timedelta(days=5)):
        ok(_run(client, w, fs, start), 202)
        run_jobs()
    assert _charges_for(client, w, fs) == 1


# --- Should fix 1: no payment dated in the future, whichever way it arrives ---------------------------------------


def test_student_cannot_submit_a_future_dated_upi_payment(client: TestClient, w: W) -> None:  # noqa: F811
    student = _student_login(client, w)
    r = client.post(
        f"{API}/portal/me/payment-submissions",
        headers=student,
        json={"amount": "1500.00", "utr": "412345678999", "paid_on": str(TODAY + timedelta(days=400))},
    )
    assert r.status_code == 422, r.text
    assert any(e["field"] == "paid_on" for e in r.json()["errors"]), r.text


def test_verifying_a_future_dated_submission_is_refused(client: TestClient, w: W) -> None:  # noqa: F811
    """A submission dated in the future (made before this check existed) can't become a receipt in a
    financial year that hasn't started (Q29): the service checks, not only the admin route."""
    with transaction(w.org.id) as db:
        sid = db.scalar(
            text(
                "INSERT INTO payment_submissions (org_id, hostel_id, student_id, amount, utr, paid_on, submitted_by) "
                "SELECT org_id, hostel_id, id, 1500, '412345678998', :d, user_id FROM students WHERE id = :s "
                "RETURNING id"
            ),
            {"d": TODAY + timedelta(days=400), "s": w.student["id"]},
        )
    r = client.post(f"{API}/admin/payment-submissions/{sid}/verify", headers=w.acc.headers())
    assert r.status_code == 422, r.text
    with transaction(w.org.id) as db:
        assert db.scalar(text("SELECT count(*) FROM payments WHERE student_id = :s"), {"s": w.student["id"]}) == 0


# --- Should fix 2: a reversed UPI/bank payment can be recorded again with its real reference ----------------------


def test_reversed_payment_reference_can_be_recorded_again(client: TestClient, w: W) -> None:  # noqa: F811
    first = ok(pay(client, w, dues="500.00", method="upi", reference="UTR998877665"), 201)
    ok(
        client.post(
            f"{API}/admin/payments/{first['id']}/reverse", headers=w.acc.headers(), json={"reason": "Wrong student"}
        )
    )
    again = ok(pay(client, w, dues="500.00", method="upi", reference="UTR998877665"), 201)  # Q31: then record correctly
    assert again["id"] != first["id"]
    # A reference still in use is refused, as before.
    dup = pay(client, w, dues="500.00", method="upi", reference="UTR998877665")
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate_payment_reference", dup.text


def test_same_reference_racing_only_one_payment_succeeds(client: TestClient, w: W) -> None:  # noqa: F811
    """With the unique index replaced by a trigger, concurrent payments with one reference must still
    yield exactly one payment. Honest note: today the organization's receipt-counter row lock (Q29)
    already serializes all payments of an organization, so this passes even without the trigger's
    advisory lock (checked by removing it); the lock is defence in depth if numbering ever changes."""
    # Different students: payments for ONE student are already serialized by its row lock, which
    # would hide a missing reference lock.
    other = admit(client, w.sa, w.h1["id"])["id"]
    students = [w.student["id"], other]
    results = _parallel(
        4,
        lambda c, i: pay(c, w, dues=f"{20 + i}.00", method="upi", reference="UTR555000111", student=students[i % 2]),
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and all(c == 409 for c in codes if c != 201), codes
    assert all(r.json()["code"] == "duplicate_payment_reference" for r in results if r.status_code == 409)


# (Critical 5, the roll-call window's error message, went with attendance in D75.)


# --- Should fix 7: the payment queues say whose payment it is -----------------------------------------------------


def test_payment_queues_name_the_student(client: TestClient, w: W) -> None:  # noqa: F811
    student = _student_login(client, w)
    ok(
        client.post(
            f"{API}/portal/me/payment-submissions",
            headers=student,
            json={"amount": "700.00", "utr": "412345600001", "paid_on": str(TODAY)},
        ),
        201,
    )
    ok(pay(client, w, dues="900.00"), 201)  # credit to refund
    ok(
        client.post(
            f"{API}/admin/refund-requests",
            headers=w.acc.headers(),
            json={"student_id": w.student["id"], "account": "dues", "amount": "100.00", "reason": "Overpaid"},
        ),
        201,
    )
    code, name = w.student["student_code"], w.student["full_name"]
    subs = ok(client.get(f"{API}/admin/payment-submissions", headers=w.acc.headers()))
    refunds = ok(client.get(f"{API}/admin/refund-requests", headers=w.acc.headers()))
    for item in (*subs, *refunds):
        assert (item.get("student_code"), item.get("student_name")) == (code, name), item


# --- Should fix 11: the reservation-expiry job never overwrites a check-in that happened meanwhile ------------------


def test_reservation_expiry_never_overwrites_a_concurrent_check_in(client: TestClient, app_db: str) -> None:  # noqa: F811
    org = make_org()
    sa = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    h = make_hostel(client, sa, "H1")
    session = ok(
        client.post(
            f"{API}/admin/academic-sessions",
            headers=sa.headers(),
            json={
                "name": "2050-51",
                "starts_on": str(TODAY - timedelta(days=60)),
                "ends_on": str(TODAY + timedelta(days=300)),
            },
        ),
        201,
    )
    ok(client.post(f"{API}/admin/academic-sessions/{session['id']}/make-current", headers=sa.headers()))
    st = admit(client, sa, h["id"])
    res = ok(
        client.post(
            f"{API}/admin/allocations",
            headers=sa.headers(),
            json={
                "student_id": st["id"],
                "bed_id": h["bed"]["id"],
                "mode": "reserve",
                "reservation_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        ),
        201,
    )
    with transaction(org.id) as db:  # the reservation has just expired; the hourly job hasn't run yet
        db.execute(
            text("UPDATE bed_allocations SET reservation_expires_at = now() - interval '1 minute' WHERE id = :a"),
            {"a": res["id"]},
        )
    outcome: dict[str, int] = {}

    def check_in() -> None:
        r = TestClient(create_app(run_checks=False), base_url="https://testserver").post(
            f"{API}/admin/allocations/{res['id']}/check-in", headers=sa.headers()
        )
        outcome["status"] = r.status_code

    thread = threading.Thread(target=check_in)

    def race(*_: Any) -> None:
        # The manager checks the student in at the moment the job writes its decision. Fixed code holds
        # the student/bed/allocation locks by now, so the check-in waits (we give it 3 s) and then fails;
        # the old code held nothing, so the check-in committed and the job then overwrote it.
        thread.start()
        thread.join(timeout=3)

    with transaction(org.id, role="hms_worker") as db:
        event.listen(db, "before_flush", race, once=True)
        expire_reservations(db)
    thread.join(timeout=30)
    with transaction(org.id) as db:
        final = db.scalar(text("SELECT status FROM bed_allocations WHERE id = :a"), {"a": res["id"]})
    # Whatever wins, the screen must not lie: a check-in reported OK must still be active.
    assert (outcome.get("status"), final) in ((200, "active"), (409, "cancelled")), (outcome, final)
