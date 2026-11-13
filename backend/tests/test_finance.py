"""Phase 5: fees, payments and the ledger (docs/04 §12.9; 02 B3; 05 FIN-1..17; Q24-Q31, Q60, Q64).

The database proof suite (tests/dbproof/test_ledger.py) already proves the triggers at SQL
level. These tests drive the same rules through the API, add the concurrency cases, and a
property-style test that runs random sequences of money operations and checks every invariant.
"""

from __future__ import annotations

import random
import threading
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.job_handlers  # noqa: F401  the same handlers the worker has
from app.core import jobs
from app.core.db import transaction
from app.main import create_app
from tests.support import TEST_TODAY, Org, Person, activation_token, add_person, admit, make_hostel, make_org, png_bytes

Response = httpx.Response
TODAY = TEST_TODAY  # the frozen test clock's local date (D67), not the wall clock
# Late-fee tests charge last calendar month: a monthly period starts on the 1st (D68), and this one is
# always at least 28 days old, so its due date has passed whatever the day of the month.
LAST_MONTH = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)


@dataclass
class W:
    org: Org
    sa: Person
    # D75: everyone below is an Administrator. The names say what each one does in the tests.
    acc: Person  # records money
    acc2: Person  # the second person who approves refunds (maker-checker, Q30)
    hm: Person
    warden: Person
    acc_h2: Person
    h1: dict[str, Any]
    h2: dict[str, Any]
    student: dict[str, Any]  # admitted to h1 and living in its bed


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


def ok(r: Response, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


def problem(r: Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    assert body["code"] == code, body
    return body


@pytest.fixture
def w(client: TestClient, app_db: str) -> W:
    org = make_org()
    sa = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    h1, h2 = make_hostel(client, sa, "H1"), make_hostel(client, sa, "H2", "girls")
    session = ok(
        client.post(
            "/api/v1/admin/academic-sessions",
            headers=sa.headers(),
            json={
                "name": "2026-27",
                "starts_on": str(TODAY - timedelta(days=150)),
                "ends_on": str(TODAY + timedelta(days=200)),
            },
        ),
        201,
    )
    ok(client.post(f"/api/v1/admin/academic-sessions/{session['id']}/make-current", headers=sa.headers()))

    def person(key: str, code: str) -> Person:
        return sa  # D76: one Administrator; the names say what each step stands for

    student = admit(client, sa, h1["id"])
    ok(
        client.post(
            "/api/v1/admin/allocations",
            headers=sa.headers(),
            json={
                "student_id": student["id"],
                "bed_id": h1["bed"]["id"],
                "start_date": str(TODAY - timedelta(days=60)),
            },
        ),
        201,
    )
    return W(
        org,
        sa,
        person("acc", "E-0002"),
        person("acc2", "E-0003"),
        person("hm", "E-0004"),
        person("warden", "E-0005"),
        person("acch2", "E-0006"),
        h1,
        h2,
        student,
    )


def charge(
    client: TestClient,
    w: W,
    amount: str,
    *,
    due: date = TODAY,
    kind: str = "charge",
    student: str | None = None,
    desc: str = "Rent",
) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": kind, "amount": amount, "description": desc, "due_date": str(due)}
    if kind == "fine":
        body |= {"reason_code": "damage", "reason_note": "Broken chair"}
    return dict(
        ok(
            client.post(
                f"/api/v1/admin/students/{student or w.student['id']}/charges", headers=w.acc.headers(), json=body
            ),
            201,
        )
    )


def pay(
    client: TestClient,
    w: W,
    dues: str = "0",
    deposit: str = "0",
    *,
    method: str = "cash",
    reference: str | None = None,
    key: uuid.UUID | None = None,
    student: str | None = None,
    who: Person | None = None,
    received_on: date = TODAY,
) -> Response:
    return client.post(
        "/api/v1/admin/payments",
        headers={**(who or w.acc).headers(), "Idempotency-Key": str(key or uuid.uuid4())},
        json={
            "student_id": student or w.student["id"],
            "method": method,
            "reference": reference,
            "received_on": str(received_on),
            "dues_amount": dues,
            "deposit_amount": deposit,
        },
    )


def balance(client: TestClient, w: W, student: str | None = None) -> dict[str, Any]:
    return dict(ok(client.get(f"/api/v1/admin/students/{student or w.student['id']}/balance", headers=w.acc.headers())))


# --- money format (FIN-10) -------------------------------------------------------------------------


def test_money_is_a_string_both_ways(client: TestClient, w: W) -> None:
    r = client.post(
        "/api/v1/admin/payments",
        headers={**w.acc.headers(), "Idempotency-Key": str(uuid.uuid4())},
        json={"student_id": w.student["id"], "method": "cash", "received_on": str(TODAY), "dues_amount": 1250.5},
    )
    body = problem(r, 422, "validation_error")
    assert body["errors"][0]["code"] == "decimal_string_required"
    problem(pay(client, w, dues="12.345"), 422, "validation_error")
    p = ok(pay(client, w, dues="1250.5"), 201)
    assert p["amount"] == "1250.50"
    assert balance(client, w)["dues"] == "-1250.50"


# --- charges, oldest-first allocation, waivers (Q60, Q64, FIN-16) --------------------------------------


def test_part_payment_is_allocated_oldest_first(client: TestClient, w: W) -> None:
    newer = charge(client, w, "2000.00", due=TODAY - timedelta(days=5), desc="Mess")
    older = charge(client, w, "3000.00", due=TODAY - timedelta(days=20), desc="Rent")
    ok(pay(client, w, dues="4000.00"), 201)
    b = balance(client, w)
    assert b["dues"] == "1000.00" and b["unallocated_credit"] == "0.00"
    assert [(c["entry_id"], c["outstanding"]) for c in b["open_charges"]] == [(newer["id"], "1000.00")]
    entries = ok(client.get(f"/api/v1/admin/students/{w.student['id']}/ledger", headers=w.acc.headers()))
    rent = next(e for e in entries if e["id"] == older["id"])
    assert [a["amount"] for a in rent["allocations"]] == ["3000.00"]


def test_new_charge_consumes_existing_credit(client: TestClient, w: W) -> None:
    ok(pay(client, w, dues="500.00"), 201)
    assert balance(client, w)["unallocated_credit"] == "500.00"
    charge(client, w, "800.00")
    b = balance(client, w)
    assert b["unallocated_credit"] == "0.00" and b["open_charges"][0]["outstanding"] == "300.00"


def test_waiver_targets_its_charge(client: TestClient, w: W) -> None:
    rent = charge(client, w, "3000.00", due=TODAY - timedelta(days=30))
    fine = charge(client, w, "500.00", kind="fine", desc="Damage")
    url = f"/api/v1/admin/students/{w.student['id']}/waivers"
    problem(
        client.post(
            url, headers=w.acc.headers(), json={"amount": "100.00", "reason_code": "waiver", "reason_note": "x"}
        ),
        422,
        "validation_error",
    )
    problem(
        client.post(
            url,
            headers=w.acc.headers(),
            json={
                "target_entry_id": fine["id"],
                "amount": "600.00",
                "reason_code": "waiver",
                "reason_note": "Too much",
            },
        ),
        409,
        "waiver_exceeds_outstanding",
    )
    ok(
        client.post(
            url,
            headers=w.acc.headers(),
            json={
                "target_entry_id": fine["id"],
                "amount": "500.00",
                "reason_code": "waiver",
                "reason_note": "First offence",
            },
        ),
        201,
    )
    b = balance(client, w)
    assert [(c["entry_id"], c["outstanding"]) for c in b["open_charges"]] == [(rent["id"], "3000.00")]
    assert b["dues"] == "3000.00"  # the waiver reduced the fine, not the older rent (Q64)


def test_waiver_on_a_paid_charge_moves_the_payment(client: TestClient, w: W) -> None:
    rent = charge(client, w, "1000.00", due=TODAY - timedelta(days=10))
    ok(pay(client, w, dues="600.00"), 201)
    ok(
        client.post(
            f"/api/v1/admin/students/{w.student['id']}/waivers",
            headers=w.acc.headers(),
            json={
                "target_entry_id": rent["id"],
                "amount": "1000.00",
                "reason_code": "scholarship",
                "reason_note": "Merit",
            },
        ),
        201,
    )
    b = balance(client, w)
    assert b["dues"] == "-600.00" and b["unallocated_credit"] == "600.00" and b["open_charges"] == []


def test_fine_requires_reason(client: TestClient, w: W) -> None:
    problem(
        client.post(
            f"/api/v1/admin/students/{w.student['id']}/charges",
            headers=w.acc.headers(),
            json={"kind": "fine", "amount": "100.00", "description": "Noise", "due_date": str(TODAY)},
        ),
        422,
        "validation_error",
    )


# --- payments: idempotency, references, receipts (FIN-2, FIN-9) ---------------------------------------------


def test_payment_retry_with_same_key_returns_original(client: TestClient, w: W) -> None:
    key = uuid.uuid4()
    first = ok(pay(client, w, dues="700.00", key=key), 201)
    again = ok(pay(client, w, dues="700.00", key=key), 200)
    assert again["id"] == first["id"] and again["receipt_number"] == first["receipt_number"]
    problem(pay(client, w, dues="701.00", key=key), 422, "idempotency_key_reused")
    r = client.post(
        "/api/v1/admin/payments",
        headers=w.acc.headers(),
        json={"student_id": w.student["id"], "method": "cash", "received_on": str(TODAY), "dues_amount": "1.00"},
    )
    problem(r, 422, "validation_error")  # the header is required


def _parallel(n: int, fn: Any) -> list[Response]:
    barrier = threading.Barrier(n)
    out: list[Response | None] = [None] * n

    def run(i: int) -> None:
        c = TestClient(create_app(run_checks=False), base_url="https://testserver")
        barrier.wait()
        out[i] = fn(c, i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [r for r in out if r is not None]


def test_concurrent_identical_payments_only_one_succeeds(client: TestClient, w: W) -> None:
    key = uuid.uuid4()
    results = _parallel(3, lambda c, i: pay(c, w, dues="900.00", key=key))
    assert all(r.status_code in (200, 201) for r in results), [r.text for r in results]
    assert len({r.json()["id"] for r in results}) == 1 and sorted(r.status_code for r in results) == [200, 200, 201]
    assert balance(client, w)["dues"] == "-900.00"  # recorded once


def test_same_reference_twice_rejected(client: TestClient, w: W) -> None:
    ok(pay(client, w, dues="100.00", method="upi", reference="UTR123456789"), 201)
    problem(pay(client, w, dues="100.00", method="upi", reference="UTR123456789"), 409, "duplicate_payment_reference")
    problem(pay(client, w, dues="100.00", method="upi"), 422, "validation_error")  # UPI needs a reference


def _seqs(w: W, fy: str) -> list[int]:
    with transaction(w.org.id) as db:
        return list(
            db.scalars(text("SELECT receipt_seq FROM payments WHERE financial_year = :fy ORDER BY 1"), {"fy": fy})
        )


def test_receipt_numbers_gap_free_when_payment_fails(client: TestClient, w: W) -> None:
    first = ok(pay(client, w, dues="100.00", method="upi", reference="REF0001"), 201)
    # Fails AFTER the receipt counter moved (duplicate reference): the whole transaction, counter
    # included, rolls back, so the next payment reuses the number.
    problem(pay(client, w, dues="100.00", method="upi", reference="REF0001"), 409, "duplicate_payment_reference")
    second = ok(pay(client, w, dues="100.00"), 201)
    fy = first["receipt_number"].split("/")[1]
    assert first["receipt_number"] == f"RCPT/{fy}/000001" and second["receipt_number"] == f"RCPT/{fy}/000002"


def test_receipt_numbers_unique_under_concurrency(client: TestClient, w: W) -> None:
    results = _parallel(6, lambda c, i: pay(c, w, dues=f"{10 + i}.00"))
    assert [r.status_code for r in results].count(201) + [r.status_code for r in results].count(409) == 6
    created = [r.json()["receipt_number"] for r in results if r.status_code == 201]
    assert len(created) == len(set(created))
    fy = created[0].split("/")[1]
    assert _seqs(w, fy) == list(range(1, len(created) + 1))  # gap-free (a 409 here would be retry_conflict)


def test_financial_year_follows_april(client: TestClient, w: W) -> None:
    y = TODAY.year - 1  # last year's 31 March and 1 April: both in the past, never skipped
    march = ok(pay(client, w, dues="10.00", received_on=date(y, 3, 31)), 201)
    april = ok(pay(client, w, dues="10.00", received_on=date(y, 4, 1)), 201)
    assert march["receipt_number"] == f"RCPT/{y - 1}-{y % 100:02d}/000001"
    assert april["receipt_number"] == f"RCPT/{y}-{(y + 1) % 100:02d}/000001"  # a new series starts at 1


# --- reversals (Q31, FIN-5, FIN-13) --------------------------------------------------------------------------


def test_payment_reversal(client: TestClient, w: W) -> None:
    rent = charge(client, w, "1000.00", due=TODAY - timedelta(days=5))
    p = ok(pay(client, w, dues="1000.00", deposit="2000.00"), 201)
    problem(
        client.post(f"/api/v1/admin/payments/{p['id']}/reverse", headers=w.acc.headers(), json={"reason": "x"}),
        422,
        "validation_error",
    )
    entries = ok(client.get(f"/api/v1/admin/students/{w.student['id']}/ledger", headers=w.acc.headers()))
    pay_entry = next(e for e in entries if e["payment_id"] == p["id"] and e["entry_type"] == "payment")
    problem(
        client.post(
            f"/api/v1/admin/ledger-entries/{pay_entry['id']}/reverse",
            headers=w.acc.headers(),
            json={"reason": "Wrong student"},
        ),
        409,
        "reverse_the_payment",
    )
    rev = ok(
        client.post(
            f"/api/v1/admin/payments/{p['id']}/reverse", headers=w.acc.headers(), json={"reason": "Cheque bounced"}
        )
    )
    assert rev["reversed"] is True
    b = balance(client, w)
    assert b["dues"] == "1000.00" and b["deposit_held"] == "0.00"
    assert [c["entry_id"] for c in b["open_charges"]] == [rent["id"]]  # the allocation was released
    problem(
        client.post(
            f"/api/v1/admin/payments/{p['id']}/reverse", headers=w.acc.headers(), json={"reason": "Again please"}
        ),
        409,
        "already_reversed",
    )
    with transaction(w.org.id) as db:
        assert (
            db.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action = 'payment.reversed' AND entity_id = :p"),
                {"p": p["id"]},
            )
            == 1
        )


def test_charge_reversal_moves_the_credit(client: TestClient, w: W) -> None:
    a = charge(client, w, "500.00", due=TODAY - timedelta(days=10), desc="Wrong")
    b_ = charge(client, w, "500.00", due=TODAY, desc="Right")
    ok(pay(client, w, dues="500.00"), 201)  # pays the older (wrong) charge
    ok(
        client.post(
            f"/api/v1/admin/ledger-entries/{a['id']}/reverse",
            headers=w.acc.headers(),
            json={"reason": "Charged by mistake"},
        ),
        201,
    )
    b = balance(client, w)
    assert b["dues"] == "0.00" and b["open_charges"] == []
    entries = ok(client.get(f"/api/v1/admin/students/{w.student['id']}/ledger", headers=w.acc.headers()))
    right = next(e for e in entries if e["id"] == b_["id"])
    assert [x["amount"] for x in right["allocations"] if not x["released"]] == ["500.00"]
    problem(
        client.post(
            f"/api/v1/admin/ledger-entries/{a['id']}/reverse",
            headers=w.acc.headers(),
            json={"reason": "Twice by mistake"},
        ),
        409,
        "already_reversed",
    )


# --- deposits and refunds (Q22, Q30, FIN-7, FIN-8) ----------------------------------------------------------


def test_deposit_application_at_exit(client: TestClient, w: W) -> None:
    charge(client, w, "3000.00")
    ok(pay(client, w, deposit="5000.00"), 201)
    b = ok(
        client.post(
            f"/api/v1/admin/students/{w.student['id']}/deposit/apply",
            headers=w.acc.headers(),
            json={"amount": "3000.00"},
        )
    )
    assert b["dues"] == "0.00" and b["deposit_held"] == "2000.00" and b["open_charges"] == []
    problem(
        client.post(
            f"/api/v1/admin/students/{w.student['id']}/deposit/apply",
            headers=w.acc.headers(),
            json={"amount": "2500.00"},
        ),
        409,
        "insufficient_deposit",
    )


def refund(client: TestClient, w: W, who: Person, account: str, amount: str) -> dict[str, Any]:
    return dict(
        ok(
            client.post(
                "/api/v1/admin/refund-requests",
                headers=who.headers(),
                json={
                    "student_id": w.student["id"],
                    "account": account,
                    "amount": amount,
                    "reason": "Leaving the hostel",
                },
            ),
            201,
        )
    )


def test_the_administrators_own_refund_is_self_approved_and_audited(client: TestClient, w: W) -> None:
    """Q30 with D75/D76: the one Administrator approves their own request; it is marked self-approved and
    audited as such. They can't REJECT their own request (they withdraw it), and the database refuses a
    self-decision that isn't marked."""
    ok(pay(client, w, deposit="4000.00"), 201)
    mine = refund(client, w, w.sa, "deposit", "1000.00")
    problem(
        client.post(
            f"/api/v1/admin/refund-requests/{mine['id']}/mark-paid", headers=w.sa.headers(), json={"method": "cash"}
        ),
        409,
        "refund_not_approved",
    )
    problem(
        client.post(
            f"/api/v1/admin/refund-requests/{mine['id']}/reject", headers=w.sa.headers(), json={"note": "Changed mind"}
        ),
        403,
        "second_person_required",
    )
    with pytest.raises(IntegrityError) as refused, transaction(w.org.id) as db:  # backstop: unmarked
        db.execute(
            text(
                "UPDATE refund_requests SET status = 'approved', decided_by = requested_by, "
                "decided_at = now() WHERE id = :r"
            ),
            {"r": mine["id"]},
        )
    assert refused.value.orig.diag.constraint_name == "refund_requests_second_person"  # type: ignore[union-attr]
    approved = ok(client.post(f"/api/v1/admin/refund-requests/{mine['id']}/approve", headers=w.sa.headers(), json={}))
    assert approved["status"] == "approved" and approved["self_approved"] is True
    with transaction(w.org.id) as db:
        actions = db.scalars(text("SELECT action FROM audit_logs WHERE entity_id = :r"), {"r": mine["id"]}).all()
    assert "refund.self_approved_sole_administrator" in actions
    paid = ok(
        client.post(
            f"/api/v1/admin/refund-requests/{mine['id']}/mark-paid", headers=w.sa.headers(), json={"method": "cash"}
        )
    )
    assert paid["status"] == "paid" and balance(client, w)["deposit_held"] == "3000.00"
    problem(
        client.post(
            f"/api/v1/admin/refund-requests/{mine['id']}/mark-paid", headers=w.sa.headers(), json={"method": "cash"}
        ),
        409,
        "refund_not_approved",
    )  # paid once


def test_a_request_by_a_closed_account_is_decided_normally(client: TestClient, w: W) -> None:
    """A refund asked for by a staff account that D75/D76 closed is an ordinary second-person decision."""
    ok(pay(client, w, deposit="2000.00"), 201)
    closed = add_person(w.org, "closed", status="deactivated")
    with transaction(w.org.id) as db:
        rid = db.scalar(
            text(
                "INSERT INTO refund_requests (org_id, hostel_id, student_id, account, amount, reason, requested_by) "
                "SELECT org_id, hostel_id, id, 'deposit', 500, 'Asked before D76', :u FROM students WHERE id = :s "
                "RETURNING id"
            ),
            {"u": closed.user_id, "s": w.student["id"]},
        )
    approved = ok(client.post(f"/api/v1/admin/refund-requests/{rid}/approve", headers=w.sa.headers(), json={}))
    assert approved["status"] == "approved" and approved["self_approved"] is False


def test_self_approval_backstops_in_the_database(client: TestClient, w: W) -> None:
    """Migration 0008: no row is BORN self-approved, and only the requester's own decision may carry the mark."""
    ok(pay(client, w, deposit="2000.00"), 201)
    mine = refund(client, w, w.sa, "deposit", "500.00")
    with pytest.raises(IntegrityError) as refused, transaction(w.org.id) as db:
        db.execute(
            text(
                "INSERT INTO refund_requests (org_id, hostel_id, student_id, account, amount, reason, requested_by, "
                "status, decided_by, decided_at, self_approved) SELECT org_id, hostel_id, student_id, account, "
                "amount, reason, requested_by, 'approved', requested_by, now(), true FROM refund_requests "
                "WHERE id = :r"
            ),
            {"r": mine["id"]},
        )
    assert refused.value.orig.diag.constraint_name == "refund_requests_second_person"  # type: ignore[union-attr]
    closed = add_person(w.org, "closed", status="deactivated")
    with pytest.raises(IntegrityError, match="refund_requests_self_approved_check"), transaction(w.org.id) as db:
        db.execute(
            text(
                "UPDATE refund_requests SET status = 'approved', decided_by = :u, decided_at = now(), "
                "self_approved = true WHERE id = :r"
            ),
            {"u": closed.user_id, "r": mine["id"]},
        )


def test_concurrent_deposit_refunds_only_one_succeeds(client: TestClient, w: W) -> None:
    ok(pay(client, w, deposit="2000.00"), 201)
    r1 = refund(client, w, w.acc, "deposit", "2000.00")
    # Since review Minor 12 the API refuses a second request for money already claimed, so the second
    # request is written directly (as one made before that rule). This test proves the DATABASE guard:
    # however two approved requests came to exist, paying both at once can't overdraw the deposit.
    with transaction(w.org.id) as db:
        r2 = {
            "id": str(
                db.scalar(
                    text(
                        "INSERT INTO refund_requests (org_id, hostel_id, student_id, account, amount, reason, "
                        "requested_by) SELECT org_id, hostel_id, student_id, account, amount, reason, requested_by "
                        "FROM refund_requests WHERE id = :r RETURNING id"
                    ),
                    {"r": r1["id"]},
                )
            )
        }
    for r in (r1, r2):
        ok(client.post(f"/api/v1/admin/refund-requests/{r['id']}/approve", headers=w.acc2.headers(), json={}))
    results = _parallel(
        2,
        lambda c, i: c.post(
            f"/api/v1/admin/refund-requests/{(r1, r2)[i]['id']}/mark-paid",
            headers=w.acc.headers(),
            json={"method": "cash"},
        ),
    )
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "insufficient_deposit"
    assert balance(client, w)["deposit_held"] == "0.00"


def test_dues_refund_of_a_credit(client: TestClient, w: W) -> None:
    ok(pay(client, w, dues="800.00"), 201)
    problem(
        client.post(
            "/api/v1/admin/refund-requests",
            headers=w.acc.headers(),
            json={"student_id": w.student["id"], "account": "dues", "amount": "900.00", "reason": "Too much"},
        ),
        409,
        "refund_exceeds_credit",
    )
    rr = refund(client, w, w.acc, "dues", "800.00")
    ok(client.post(f"/api/v1/admin/refund-requests/{rr['id']}/approve", headers=w.sa.headers(), json={}))
    ok(
        client.post(
            f"/api/v1/admin/refund-requests/{rr['id']}/mark-paid",
            headers=w.acc.headers(),
            json={"method": "bank_transfer", "reference": "NEFT001"},
        )
    )
    b = balance(client, w)
    assert b["dues"] == "0.00" and b["unallocated_credit"] == "0.00" and b["open_charges"] == []


# --- UPI submissions (Q28, FIN-12) ----------------------------------------------------------------------------


def _student_login(client: TestClient, w: W) -> dict[str, str]:
    link = {"activation_url": "#token=" + activation_token(client, w.sa.headers(), w.student)}
    ok(
        client.post(
            f"/api/v1/o/{w.org.slug}/auth/activate",
            json={"token": link["activation_url"].split("#token=")[1], "password": "a decent long passphrase"},
        ),
        204,
    )
    r = ok(
        client.post(
            f"/api/v1/o/{w.org.slug}/auth/login",
            json={"portal": "student", "identifier": w.student["student_code"], "password": "a decent long passphrase"},
        )
    )
    return {"Authorization": f"Bearer {r['access_token']}"}


def test_upi_submission_flow(client: TestClient, w: W) -> None:
    org = ok(client.get("/api/v1/admin/organization", headers=w.sa.headers()))
    ok(
        client.patch(
            "/api/v1/admin/organization",
            headers=w.sa.headers(),
            json={"version": org["version"], "upi_id": "hostel@okbank", "upi_payee_name": "Hostel Office"},
        )
    )
    charge(client, w, "1500.00")
    student = _student_login(client, w)
    fees = ok(client.get("/api/v1/portal/me/fees", headers=student))
    assert fees["upi"]["link"].startswith("upi://pay?pa=hostel%40okbank&pn=Hostel%20Office&am=1500.00&cu=INR")
    proof = ok(
        client.post(
            "/api/v1/files",
            headers=student,
            data={"category": "payment_proof"},
            files={"file": ("p.png", png_bytes(), "image/png")},
        ),
        201,
    )
    sub = ok(
        client.post(
            "/api/v1/portal/me/payment-submissions",
            headers=student,
            json={"amount": "1500.00", "utr": "412345678901", "paid_on": str(TODAY), "proof_file_id": proof["file_id"]},
        ),
        201,
    )
    problem(
        client.post(
            "/api/v1/portal/me/payment-submissions",
            headers=student,
            json={"amount": "1500.00", "utr": "412345678901", "paid_on": str(TODAY)},
        ),
        409,
        "utr_already_submitted",
    )
    pending = ok(client.get("/api/v1/admin/payment-submissions", headers=w.acc.headers()))
    assert [s["id"] for s in pending] == [sub["id"]]
    assert client.get(f"/api/v1/files/{proof['file_id']}/link", headers=w.acc.headers()).status_code == 200
    ok(client.get(f"/api/v1/files/{proof['file_id']}/link", headers=w.warden.headers()))  # any Administrator (D75)
    ok(client.post(f"/api/v1/admin/payment-submissions/{sub['id']}/verify", headers=w.acc.headers()))
    b = balance(client, w)
    assert b["dues"] == "0.00"
    payments = ok(client.get(f"/api/v1/admin/payments?student_id={w.student['id']}", headers=w.acc.headers()))
    assert payments[0]["method"] == "upi" and payments[0]["reference"] == "412345678901"
    problem(
        client.post(f"/api/v1/admin/payment-submissions/{sub['id']}/verify", headers=w.acc.headers()),
        409,
        "submission_state",
    )
    other = ok(
        client.post(
            "/api/v1/portal/me/payment-submissions",
            headers=student,
            json={"amount": "10.00", "utr": "999999999999", "paid_on": str(TODAY)},
        ),
        201,
    )
    problem(
        client.post(f"/api/v1/admin/payment-submissions/{other['id']}/reject", headers=w.acc.headers(), json={}),
        422,
        "validation_error",
    )  # a rejection needs a note
    ok(
        client.post(
            f"/api/v1/admin/payment-submissions/{other['id']}/reject",
            headers=w.acc.headers(),
            json={"note": "Not on the bank statement"},
        )
    )
    ok(
        client.post(
            "/api/v1/portal/me/payment-submissions",
            headers=student,
            json={"amount": "10.00", "utr": "999999999999", "paid_on": str(TODAY)},
        ),
        201,
    )  # a rejected UTR may be re-sent
    receipt = client.get(f"/api/v1/portal/me/payments/{payments[0]['id']}/receipt.pdf", headers=student)
    assert receipt.status_code == 200 and receipt.content.startswith(b"%PDF-")


# --- receipts, permissions, scope --------------------------------------------------------------------------------


def test_receipt_pdf(client: TestClient, w: W) -> None:
    p = ok(pay(client, w, dues="1250.00", deposit="500.00"), 201)
    r = client.get(f"/api/v1/admin/payments/{p['id']}/receipt.pdf", headers=w.acc.headers())
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-") and b"%%EOF" in r.content[-64:]
    assert p["receipt_number"].replace("/", "-") in r.headers["content-disposition"]


def test_permissions_and_scope(client: TestClient, w: W) -> None:
    """D75: every Administrator takes money in every hostel; a student never reaches the admin routes."""
    for who in (w.warden, w.hm, w.acc_h2):
        ok(pay(client, w, dues="1.00", who=who), 201)
    ok(client.get(f"/api/v1/admin/students/{w.student['id']}/balance", headers=w.acc_h2.headers()))
    student = _student_login(client, w)
    problem(client.get(f"/api/v1/admin/students/{w.student['id']}/balance", headers=student), 403, "wrong_portal")


def test_payments_export_is_audited_and_csv_safe(client: TestClient, w: W) -> None:
    ok(pay(client, w, dues="100.00"), 201)
    r = client.get("/api/v1/admin/payments/export.csv", headers=w.acc.headers())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("receipt_number,") and len(lines) == 2 and ",no," in lines[1]
    with transaction(w.org.id) as db:
        assert db.scalar(text("SELECT count(*) FROM audit_logs WHERE action = 'payments.exported'")) == 1


# --- fee structures, charge runs, late fees (FIN-11, FIN-14, FIN-17) ---------------------------------------------


def fee_structure(client: TestClient, w: W, **kw: Any) -> dict[str, Any]:
    body = {
        "hostel_id": w.h1["id"],
        "name": "Monthly rent",
        "fee_kind": "rent",
        "amount": "4000.00",
        "billing_cycle": "monthly",
        **kw,
    }
    return dict(ok(client.post("/api/v1/admin/fee-structures", headers=w.acc.headers(), json=body), 201))


def run_jobs() -> None:
    while jobs.work_once("w") is not None:
        pass


def test_charge_run_is_idempotent(client: TestClient, w: W) -> None:
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
    fs = fee_structure(client, w)
    start = TODAY.replace(day=1)
    body = {"fee_structure_id": fs["id"], "period_start": str(start)}
    ok(client.post("/api/v1/admin/charge-runs", headers=w.acc.headers(), json=body), 202)
    run_jobs()
    ok(client.post("/api/v1/admin/charge-runs", headers=w.acc.headers(), json=body), 202)
    run_jobs()
    b = balance(client, w)
    assert b["dues"] == "4000.00" and len(b["open_charges"]) == 1  # charged once (FIN-11)
    deposit = fee_structure(client, w, name="Deposit", fee_kind="deposit", billing_cycle="one_time", amount="5000.00")
    problem(
        client.post(
            "/api/v1/admin/charge-runs",
            headers=w.acc.headers(),
            json={"fee_structure_id": deposit["id"], "period_start": str(start)},
        ),
        422,
        "validation_error",
    )
    assert balance(client, w)["deposit_due"] == "5000.00"


def test_fee_structure_amount_locked_after_use(client: TestClient, w: W) -> None:
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
    fs = fee_structure(client, w)
    renamed = ok(
        client.patch(
            f"/api/v1/admin/fee-structures/{fs['id']}",
            headers=w.acc.headers(),
            json={"version": fs["version"], "name": "Rent (monthly)"},
        )
    )
    problem(
        client.patch(
            f"/api/v1/admin/fee-structures/{fs['id']}",
            headers=w.acc2.headers(),
            json={"version": fs["version"], "grace_days": 3},
        ),
        409,
        "edit_conflict",
    )
    ok(
        client.post(
            "/api/v1/admin/charge-runs",
            headers=w.acc.headers(),
            json={"fee_structure_id": fs["id"], "period_start": str(TODAY.replace(day=1))},
        ),
        202,
    )
    run_jobs()
    problem(
        client.patch(
            f"/api/v1/admin/fee-structures/{fs['id']}",
            headers=w.acc.headers(),
            json={"version": renamed["version"], "amount": "4500.00"},
        ),
        409,
        "fee_structure_in_use",
    )


def _late_fee_run() -> None:
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
        jobs.enqueue(db, "fees.late_fees", org_id=None)
    assert jobs.work_once("w") == "succeeded"


def _late_fees(w: W) -> list[str]:
    with transaction(w.org.id) as db:
        return [
            f"{a:.2f}"
            for a in db.scalars(
                text(
                    "SELECT amount FROM ledger_entries WHERE entry_type = 'late_fee' AND student_id = :s "
                    "ORDER BY late_fee_date"
                ),
                {"s": w.student["id"]},
            )
        ]


def _structure_charge(client: TestClient, w: W, fs: dict[str, Any], start: date) -> None:
    with transaction(w.org.id) as db:
        from app.modules.finance import service

        service.run_charges(db, uuid.UUID(fs["id"]), start, None)


def test_late_fee_flat_once(client: TestClient, w: W) -> None:
    fs = fee_structure(client, w, due_days=0, grace_days=2, late_fee_type="flat", late_fee_amount="200.00")
    _structure_charge(client, w, fs, LAST_MONTH)
    _late_fee_run()
    _late_fee_run()
    assert _late_fees(w) == ["200.00"]


def test_late_fee_respects_cap(client: TestClient, w: W) -> None:
    fs = fee_structure(
        client, w, due_days=0, grace_days=0, late_fee_type="per_day", late_fee_amount="50.00", late_fee_cap="175.00"
    )
    _structure_charge(client, w, fs, LAST_MONTH)
    _late_fee_run()
    _late_fee_run()
    assert _late_fees(w) == ["50.00", "50.00", "50.00", "25.00"]  # capped at 175, once per day


def test_paid_charge_gets_no_late_fee(client: TestClient, w: W) -> None:
    fs = fee_structure(client, w, due_days=0, late_fee_type="flat", late_fee_amount="200.00")
    _structure_charge(client, w, fs, LAST_MONTH)
    ok(pay(client, w, dues="4000.00"), 201)
    _late_fee_run()
    assert _late_fees(w) == []


# --- property-style: random money operations keep every invariant (02 B3 "Proof") ---------------------------------


def _check_invariants(w: W) -> None:
    with transaction(w.org.id) as db:
        sid = w.student["id"]
        dues, deposit = db.execute(
            text(
                "SELECT coalesce(sum(amount) FILTER (WHERE account = 'dues'), 0), "
                "coalesce(sum(amount) FILTER (WHERE account = 'deposit'), 0) FROM ledger_entries WHERE student_id = :s"
            ),
            {"s": sid},
        ).one()
        open_total, open_count = db.execute(
            text(
                """SELECT coalesce(sum(e.amount - coalesce(a.used, 0)), 0), count(*) FROM ledger_entries e
               LEFT JOIN (SELECT debit_entry_id, sum(amount) used FROM ledger_allocations WHERE released_at IS NULL
                          GROUP BY debit_entry_id) a ON a.debit_entry_id = e.id
               WHERE e.student_id = :s AND e.account = 'dues' AND e.entry_type IN ('charge','late_fee','fine','refund')
                 AND NOT EXISTS (SELECT 1 FROM ledger_entries r WHERE r.reverses_entry_id = e.id)
                 AND e.amount - coalesce(a.used, 0) > 0"""
            ),
            {"s": sid},
        ).one()
        credit_total, auto_credit_count = db.execute(
            text(
                """SELECT coalesce(sum(-e.amount - coalesce(a.used, 0)), 0),
                      count(*) FILTER (WHERE e.entry_type IN ('payment', 'deposit_applied'))
               FROM ledger_entries e
               LEFT JOIN (SELECT credit_entry_id, sum(amount) used FROM ledger_allocations WHERE released_at IS NULL
                          GROUP BY credit_entry_id) a ON a.credit_entry_id = e.id
               WHERE e.student_id = :s AND e.account = 'dues' AND e.entry_type IN ('payment','waiver','deposit_applied')
                 AND NOT EXISTS (SELECT 1 FROM ledger_entries r WHERE r.reverses_entry_id = e.id)
                 AND -e.amount - coalesce(a.used, 0) > 0"""
            ),
            {"s": sid},
        ).one()
        over = db.scalar(
            text(
                """SELECT count(*) FROM ledger_entries e WHERE e.student_id = :s AND (
                 (e.amount > 0 AND e.account = 'dues' AND (SELECT coalesce(sum(amount), 0) FROM ledger_allocations
                    WHERE debit_entry_id = e.id AND released_at IS NULL) > e.amount)
              OR (e.amount < 0 AND e.account = 'dues' AND (SELECT coalesce(sum(amount), 0) FROM ledger_allocations
                    WHERE credit_entry_id = e.id AND released_at IS NULL) > -e.amount))"""
            ),
            {"s": sid},
        )
        loose_waivers = db.scalar(  # review Should fix 4: a standing waiver is always fully allocated (Q64)
            text(
                """SELECT count(*) FROM ledger_entries e WHERE e.student_id = :s AND e.entry_type = 'waiver'
                 AND NOT EXISTS (SELECT 1 FROM ledger_entries r WHERE r.reverses_entry_id = e.id)
                 AND (SELECT coalesce(sum(amount), 0) FROM ledger_allocations
                      WHERE credit_entry_id = e.id AND released_at IS NULL) <> -e.amount"""
            ),
            {"s": sid},
        )
        seqs = db.execute(
            text(
                "SELECT financial_year, array_agg(receipt_seq ORDER BY receipt_seq) FROM payments "
                "GROUP BY financial_year"
            )
        ).all()
    assert Decimal(dues) == Decimal(open_total) - Decimal(credit_total), "balance != outstanding - unallocated"
    assert not (open_count and auto_credit_count), "an open charge and unallocated credit at the same time"
    assert Decimal(deposit) >= 0, "deposit held went negative"
    assert over == 0, "an allocation exceeds its entry"
    assert loose_waivers == 0, "a waiver is not fully allocated: credit nobody paid"
    for _, s in seqs:
        assert s == list(range(1, len(s) + 1)), "receipt numbers have a gap"


def _standing_debits(client: TestClient, w: W) -> list[dict[str, Any]]:
    """Charges and fines not reversed, paid or not."""
    ledger = ok(client.get(f"/api/v1/admin/students/{w.student['id']}/ledger", headers=w.acc.headers()))
    return [e for e in ledger if e["entry_type"] in ("charge", "fine") and not e["reversed"]]


@pytest.mark.parametrize("seed", list(range(1, 11)))
def test_random_money_operations_keep_every_invariant(client: TestClient, w: W, seed: int) -> None:
    rnd = random.Random(seed)
    payments: list[str] = []
    for _ in range(30):
        op = rnd.choice(
            [
                "charge",
                "fine",
                "pay",
                "pay_deposit",
                "waive",
                "reverse_payment",
                "reverse_charge",
                "apply_deposit",
                "refund_credit",
            ]
        )
        amount = f"{rnd.randint(1, 40) * 50}.00"
        if op == "charge":
            charge(client, w, amount, due=TODAY - timedelta(days=rnd.randint(0, 60)))
        elif op == "fine":
            charge(client, w, amount, kind="fine")
        elif op in ("pay", "pay_deposit"):
            r = pay(client, w, dues=amount if op == "pay" else "0", deposit=amount if op == "pay_deposit" else "0")
            payments.append(ok(r, 201)["id"])
        elif op == "waive":
            standing = _standing_debits(client, w)  # paid ones too: that's where the review's bugs were
            if standing:
                target = rnd.choice(standing)
                value = min(Decimal(target["amount"]), Decimal(amount))
                r = client.post(
                    f"/api/v1/admin/students/{w.student['id']}/waivers",
                    headers=w.acc.headers(),
                    json={
                        "target_entry_id": target["id"],
                        "amount": f"{value:.2f}",
                        "reason_code": "discount",
                        "reason_note": "Random",
                    },
                )
                # The only acceptable refusal: earlier waivers on that charge leave less than `value`.
                assert r.status_code == 201 or r.json()["code"] == "waiver_exceeds_outstanding", r.text
        elif op == "reverse_payment" and payments:
            r = client.post(
                f"/api/v1/admin/payments/{payments.pop(rnd.randrange(len(payments)))}/reverse",
                headers=w.acc.headers(),
                json={"reason": "Random reversal"},
            )
            # The only acceptable refusal: the deposit part was already used (not a generic 409).
            assert r.status_code == 200 or r.json()["code"] == "insufficient_deposit", r.text
        elif op == "reverse_charge":
            standing = _standing_debits(client, w)
            if standing:
                ok(
                    client.post(
                        f"/api/v1/admin/ledger-entries/{rnd.choice(standing)['id']}/reverse",
                        headers=w.acc.headers(),
                        json={"reason": "Random reversal"},
                    ),
                    201,
                )
        elif op == "apply_deposit":
            held = Decimal(balance(client, w)["deposit_held"])
            if held > 0:
                ok(
                    client.post(
                        f"/api/v1/admin/students/{w.student['id']}/deposit/apply",
                        headers=w.acc.headers(),
                        json={"amount": f"{min(held, Decimal(amount)):.2f}"},
                    )
                )
        elif op == "refund_credit":
            credit = -Decimal(balance(client, w)["dues"])
            if credit > 0:
                rr = ok(
                    client.post(
                        "/api/v1/admin/refund-requests",
                        headers=w.acc.headers(),
                        json={
                            "student_id": w.student["id"],
                            "account": "dues",
                            "amount": f"{credit:.2f}",
                            "reason": "Random refund",
                        },
                    ),
                    201,
                )
                ok(client.post(f"/api/v1/admin/refund-requests/{rr['id']}/approve", headers=w.acc2.headers(), json={}))
                ok(
                    client.post(
                        f"/api/v1/admin/refund-requests/{rr['id']}/mark-paid",
                        headers=w.acc.headers(),
                        json={"method": "cash"},
                    )
                )
        _check_invariants(w)
