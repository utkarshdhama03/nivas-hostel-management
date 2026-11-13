"""MT-2 (05 §3.1) for IDs in request BODIES (review Should fix 5): the generated suite attacks IDs in
URL paths; here organization A's Super Admin (every permission) puts organization B's IDs into the
bodies of create requests. Each must be refused as "not found", show nothing of B, and change nothing
of B's (the snapshot now includes the money, student and file tables)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.security.conftest import SecWorld
from tests.security.test_generated import _no_foreign_data, _snapshot
from tests.support import TEST_TODAY

API = "/api/v1"


def _cases(world: SecWorld) -> list[tuple[str, str, dict[str, Any]]]:
    a, b = (lambda kind: world.ids("a", kind)[0]), (lambda kind: world.ids("b", kind)[0])
    return [
        (
            "payment for B's student",
            "/admin/payments",
            {
                "student_id": str(b("student")),
                "method": "cash",
                "reference": None,
                "received_on": str(TEST_TODAY),
                "dues_amount": "10.00",
                "deposit_amount": "0",
            },
        ),
        (
            "refund for B's student",
            "/admin/refund-requests",
            {"student_id": str(b("student")), "account": "dues", "amount": "1.00", "reason": "Cross-tenant try"},
        ),
        (
            "allocate B's student to A's bed",
            "/admin/allocations",
            {"student_id": str(b("student")), "bed_id": str(a("bed"))},
        ),
        (
            "allocate A's student to B's bed",
            "/admin/allocations",
            {"student_id": str(a("student")), "bed_id": str(b("bed"))},
        ),
        (
            "waive B's ledger entry for A's student",
            f"/admin/students/{a('student')}/waivers",
            {
                "target_entry_id": str(b("ledger_entry")),
                "amount": "1.00",
                "reason_code": "waiver",
                "reason_note": "Try",
            },
        ),
        (
            "charge run of B's fee structure",
            "/admin/charge-runs",
            {"fee_structure_id": str(b("fee_structure")), "period_start": str(TEST_TODAY.replace(day=1))},
        ),
        (
            "fee structure in B's hostel",
            "/admin/fee-structures",
            {
                "hostel_id": str(b("hostel")),
                "name": "Cross",
                "fee_kind": "rent",
                "amount": "1.00",
                "billing_cycle": "monthly",
            },
        ),
        (
            "emergency leave for B's student",
            "/admin/leave-requests",
            {
                "student_id": str(b("student")),
                "reason": "Cross-tenant try",
                "starts_at": "2050-06-15T12:00:00+05:30",
                "ends_at": "2050-06-16T12:00:00+05:30",
            },
        ),
    ]


@pytest.mark.parametrize("case", range(8), ids=lambda i: f"case{i}")
def test_ids_of_another_organization_in_a_body_are_not_found(
    client: TestClient, world: SecWorld, su: Any, case: int
) -> None:
    name, path, body = _cases(world)[case]
    admin = world.orgs["a"].people["admin"]
    b_ids = {str(i) for kind in world.resources["b"] for i in world.ids("b", kind)}
    # The same request with B's ID replaced by an ID that exists nowhere.
    nowhere = {k: (str(uuid.uuid4()) if v in b_ids else v) for k, v in body.items()}
    nowhere_path = path
    before = _snapshot(su, world.orgs["b"].id)

    def send(p: str, payload: dict[str, Any]) -> Any:
        return client.post(f"{API}{p}", headers={**admin.headers(), "Idempotency-Key": str(uuid.uuid4())}, json=payload)

    r, control = send(path, body), send(nowhere_path, nowhere)
    # B's ID must behave exactly like an ID that doesn't exist: no existence oracle (MT-2, MT-8) ...
    assert (r.status_code, r.json().get("code")) == (control.status_code, control.json().get("code")), (name, r.text)
    # ... and be refused as unknown, not as a crash or a generic database conflict.
    assert r.status_code in (404, 422), (name, r.status_code, r.text)
    _no_foreign_data(world, r.text)
    assert _snapshot(su, world.orgs["b"].id) == before, f"{name}: org B changed"


# --- 05 MT-2 / review Should fix 10: the three tests 05 named that had no evidence of coverage --------------------

FILTERS = [
    ("/admin/students", "hostel_id", "hostel"),
    ("/admin/allocations", "hostel_id", "hostel"),
    ("/admin/complaints", "hostel_id", "hostel"),
    ("/admin/leave-requests", "student_id", "student"),
    ("/admin/payments", "student_id", "student"),
    ("/admin/audit-logs", "entity_id", "student"),
]


@pytest.mark.parametrize(("path", "param", "kind"), FILTERS, ids=[f[0] for f in FILTERS])
def test_cross_tenant_filter_param_returns_404(
    client: TestClient, world: SecWorld, path: str, param: str, kind: str
) -> None:
    """A list filtered by another organization's ID answers exactly as for an ID that exists nowhere (404 or an
    empty list, depending on the route), and never shows that organization's rows."""
    admin = world.orgs["a"].people["admin"]
    r = client.get(f"{API}{path}", headers=admin.headers(), params={param: str(world.ids("b", kind)[0])})
    control = client.get(f"{API}{path}", headers=admin.headers(), params={param: str(uuid.uuid4())})
    assert r.status_code == control.status_code, (path, r.text)
    if r.status_code == 200:
        body = r.json()
        items = body["items"] if isinstance(body, dict) and "items" in body else body
        assert items == [], (path, r.text)
    else:
        assert r.status_code == 404, (path, r.status_code, r.text)
    _no_foreign_data(world, r.text)


@pytest.mark.parametrize(
    "payload",
    [
        "%%",
        "__",
        "_%",
        "\\\\",
        "' OR '1'='1",
        "'; DROP TABLE students; --",
        "%' OR 1=1 --",
    ],  # search needs 2+ characters
)
def test_injection_payloads_in_filters_harmless(client: TestClient, world: SecWorld, payload: str) -> None:
    """Search text is a value, never SQL or a LIKE pattern: '%' must not match every student (05 WEB-1)."""
    admin = world.orgs["a"].people["admin"]
    every = client.get(f"{API}/admin/students", headers=admin.headers())
    assert every.status_code == 200 and every.json()["items"], "the world has students to leak"
    # Control: real search text does find them, so an empty answer below means "escaped", not "search broken".
    found = client.get(f"{API}/admin/students", headers=admin.headers(), params={"q": "STU"})
    assert found.status_code == 200 and found.json()["items"], found.text
    r = client.get(f"{API}/admin/students", headers=admin.headers(), params={"q": payload})
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [], (payload, [s["student_code"] for s in r.json()["items"]])
    _no_foreign_data(world, r.text)


def test_job_for_org_a_cannot_touch_org_b(world: SecWorld, su: Any) -> None:
    """A background job runs in its own organization's context (RLS), even when its payload names another
    organization's data: a charge run for org A pointing at org B's fee structure changes nothing in B."""
    from app.core import jobs
    from app.core.db import transaction

    before = _snapshot(su, world.orgs["b"].id)
    with transaction(None, role="hms_worker") as db:
        jobs.enqueue(
            db,
            "fees.charge_run",
            {
                "fee_structure_id": str(world.ids("b", "fee_structure")[0]),
                "period_start": str(TEST_TODAY.replace(day=1)),
                "requested_by": None,
            },
            org_id=world.orgs["a"].id,
            dedupe_key=f"cross-org-{uuid.uuid4()}",
        )
    while jobs.work_once("w") is not None:
        pass
    assert _snapshot(su, world.orgs["b"].id) == before, "a job of org A changed org B"
