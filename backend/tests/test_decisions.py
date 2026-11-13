"""Decisions after the review (Q91-Q93) and the Part 2 checks.

Q93: exit is never blocked by money, but with dues or a held deposit it needs a reason: the warning
shows the amounts, the reason and amounts are audited, and the dues stay on the student's account.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import transaction
from tests.test_finance import TODAY, W, balance, charge, client, ok, pay, w  # noqa: F401  (fixtures)

API = "/api/v1"


def _check_out(cl: TestClient, world: W) -> None:
    allocs = ok(cl.get(f"{API}/admin/students/{world.student['id']}/allocations", headers=world.sa.headers()))
    active = next(a for a in allocs if a["status"] == "active")
    ok(cl.post(f"{API}/admin/allocations/{active['id']}/check-out", headers=world.sa.headers()))


def _exit(cl: TestClient, world: W, **extra: Any) -> Any:
    body = {"exited_on": str(TODAY), "exit_reason": "withdrawn", **extra}
    return cl.post(f"{API}/admin/students/{world.student['id']}/exit", headers=world.sa.headers(), json=body)


def test_exit_with_outstanding_dues_warns_with_amounts_and_needs_a_reason(client: TestClient, w: W) -> None:  # noqa: F811
    charge(client, w, "1500.00", due=TODAY - timedelta(days=3))
    ok(pay(client, w, deposit="2000.00"), 201)
    _check_out(client, w)
    warned = _exit(client, w)
    assert warned.status_code == 409 and warned.json()["code"] == "outstanding_balance", warned.text
    assert (warned.json()["dues"], warned.json()["deposit_held"]) == ("1500.00", "2000.00")
    assert "1,500.00" in warned.json()["detail"] and "2,000.00" in warned.json()["detail"]
    too_short = _exit(client, w, outstanding_note="ok")
    assert too_short.status_code == 422, too_short.text
    done = _exit(client, w, outstanding_note="Left mid-term; family will pay the dues by post")
    assert done.status_code == 200 and done.json()["status"] == "exited", done.text
    b = balance(client, w)
    assert (b["dues"], b["deposit_held"]) == ("1500.00", "2000.00")  # the dues stay on the account
    with transaction(w.org.id) as db:
        after = db.scalar(
            text("SELECT after_values FROM audit_logs WHERE action = 'student.exited' AND entity_id = :s"),
            {"s": w.student["id"]},
        )
    assert after["outstanding_dues"] == "1500.00" and after["deposit_held"] == "2000.00", after
    assert after["outstanding_note"].startswith("Left mid-term") and after["exit_reason"] == "withdrawn", after


def test_exit_with_nothing_outstanding_needs_no_reason(client: TestClient, w: W) -> None:  # noqa: F811
    _check_out(client, w)
    done = _exit(client, w)
    assert done.status_code == 200, done.text


# --- review Minor 12: refund requests count other open requests, and can be withdrawn -------------------------------


def _refund(cl: TestClient, world: W, account: str, amount: str) -> Any:
    return cl.post(
        f"{API}/admin/refund-requests",
        headers=world.acc.headers(),
        json={"student_id": world.student["id"], "account": account, "amount": amount, "reason": "Leaving the hostel"},
    )


def test_refund_requests_cannot_together_exceed_the_deposit(client: TestClient, w: W) -> None:  # noqa: F811
    ok(pay(client, w, deposit="1000.00"), 201)
    ok(_refund(client, w, "deposit", "700.00"), 201)
    over = _refund(client, w, "deposit", "700.00")  # 700 + 700 > 1000 held
    assert over.status_code == 409 and "300.00" in over.json()["detail"], over.text
    ok(_refund(client, w, "deposit", "300.00"), 201)  # exactly what's left


def test_a_refund_request_can_be_withdrawn(client: TestClient, w: W) -> None:  # noqa: F811
    ok(pay(client, w, deposit="1000.00"), 201)
    first = ok(_refund(client, w, "deposit", "1000.00"), 201)
    withdrawn = ok(client.post(f"{API}/admin/refund-requests/{first['id']}/cancel", headers=w.acc.headers()))
    assert withdrawn["status"] == "cancelled"
    again = client.post(f"{API}/admin/refund-requests/{first['id']}/cancel", headers=w.acc.headers())
    assert again.status_code == 409, again.text  # only a waiting request can be withdrawn
    ok(_refund(client, w, "deposit", "1000.00"), 201)  # the money is available again


# --- review Minor 13: list sizes are capped (05 WEB-6) ---------------------------------------------------------------


def test_page_size_capped(client: TestClient, w: W) -> None:  # noqa: F811
    for path in ("/admin/refund-requests", "/admin/payment-submissions"):
        assert client.get(f"{API}{path}?limit=10000", headers=w.acc.headers()).status_code == 422, path
        assert client.get(f"{API}{path}?limit=50", headers=w.acc.headers()).status_code == 200, path
