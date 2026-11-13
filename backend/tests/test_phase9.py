"""Phase 9: the Platform Owner console (docs/04 §12.19; 05 MT-10, MT-15, MT-16, RBAC-10), and the
Administrator's home overview that replaced analytics (D86), checked against hand-computed fixtures."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import metrics
from app.core.db import transaction
from app.main import create_app
from tests.support import Org, Person, add_person, admit, make_hostel, make_org
from tests.test_platform_auth import bearer, new_owner, sign_in

Response = httpx.Response
API = "/api/v1"


def ok(r: Response, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


def problem(r: Response, status: int, code: str | None = None) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    if code is not None:
        assert body["code"] == code, body
    return body


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


@dataclass
class W:
    org: Org
    sa: Person
    hm: Person
    hm2: Person
    acc: Person
    warden: Person
    h1: dict[str, Any]
    h2: dict[str, Any]
    s1: dict[str, Any]
    s2: dict[str, Any]
    today: date


def build(client: TestClient) -> W:
    """H1 has 2 beds, H2 has 1. s1 lived in H1 from today-10 until today (checked out today);
    s2 has lived in H1 since today-2. s2 was charged 1000, paid 500 dues + 2000 deposit in cash,
    and a second payment of 300 was reversed."""
    org = make_org()
    sa = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    session = ok(
        client.post(
            f"{API}/admin/academic-sessions",
            headers=sa.headers(),
            json={"name": "2026-27", "starts_on": "2026-06-01", "ends_on": "2027-05-31"},
        ),
        201,
    )
    ok(client.post(f"{API}/admin/academic-sessions/{session['id']}/make-current", headers=sa.headers()))
    h1, h2 = make_hostel(client, sa, "H1"), make_hostel(client, sa, "H2")
    bed2 = ok(client.post(f"{API}/admin/rooms/{h1['room']['id']}/beds", headers=sa.headers(), json={"label": "B"}), 201)
    with transaction(org.id) as db:
        tz = db.execute(text("SELECT timezone FROM organizations WHERE id = :o"), {"o": org.id}).scalar_one()
    today = datetime.now(ZoneInfo(tz)).date()
    s1, s2 = admit(client, sa, h1["id"]), admit(client, sa, h1["id"])
    a1 = ok(
        client.post(
            f"{API}/admin/allocations",
            headers=sa.headers(),
            json={"student_id": s1["id"], "bed_id": h1["bed"]["id"], "start_date": str(today - timedelta(days=10))},
        ),
        201,
    )
    ok(client.post(f"{API}/admin/allocations/{a1['id']}/check-out", headers=sa.headers()))
    ok(
        client.post(
            f"{API}/admin/allocations",
            headers=sa.headers(),
            json={"student_id": s2["id"], "bed_id": bed2["id"], "start_date": str(today - timedelta(days=2))},
        ),
        201,
    )
    ok(
        client.post(
            f"{API}/admin/students/{s2['id']}/charges",
            headers=sa.headers(),
            json={"kind": "charge", "amount": "1000.00", "description": "Rent", "due_date": str(today)},
        ),
        201,
    )
    pay = {"student_id": s2["id"], "method": "cash", "received_on": str(today)}
    ok(
        client.post(
            f"{API}/admin/payments",
            headers={**sa.headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={**pay, "dues_amount": "500.00", "deposit_amount": "2000.00"},
        ),
        201,
    )
    second = ok(
        client.post(
            f"{API}/admin/payments",
            headers={**sa.headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={**pay, "dues_amount": "300.00"},
        ),
        201,
    )
    ok(
        client.post(
            f"{API}/admin/payments/{second['id']}/reverse", headers=sa.headers(), json={"reason": "Entered twice"}
        )
    )
    return W(
        org,
        sa,
        sa,  # hm: the one Administrator (D76)
        sa,  # hm2: the one Administrator (D76)
        sa,  # acc: the one Administrator (D76)
        sa,  # warden: the one Administrator (D76)
        h1,
        h2,
        s1,
        s2,
        today,
    )


@pytest.fixture
def w(client: TestClient, app_db: str) -> W:
    return build(client)


# --- the home overview (D86: analytics and its reports were removed) ---------------------------------


def test_overview_counts_match_the_fixture(client: TestClient, w: W) -> None:
    """Students, free and occupied beds, waiting requests, waiting leave and open complaints."""
    o = ok(client.get(f"{API}/admin/overview", headers=w.sa.headers()))
    with transaction(w.org.id) as db:
        expected = db.execute(
            text(
                "SELECT (SELECT count(*) FROM students WHERE status = 'active'), "
                "(SELECT count(*) FROM bed_states WHERE state = 'vacant'), "
                "(SELECT count(*) FROM bed_states WHERE state = 'occupied'), "
                "(SELECT count(*) FROM leave_requests WHERE status = 'pending'), "
                "(SELECT count(*) FROM complaints WHERE status IN ('open', 'in_progress'))"
            )
        ).one()
    assert (o["students"], o["beds_free"], o["beds_occupied"], o["leave_pending"], o["complaints_open"]) == tuple(
        expected
    )
    assert o["beds_occupied"] >= 1 and o["registration_requests_pending"] == 0


def test_overview_is_for_the_organization_only(client: TestClient, w: W) -> None:
    """Another organization's students never count; a student can't read it; the old routes are gone."""
    before = ok(client.get(f"{API}/admin/overview", headers=w.sa.headers()))
    other = build(client)
    assert ok(client.get(f"{API}/admin/overview", headers=w.sa.headers())) == before
    assert ok(client.get(f"{API}/admin/overview", headers=other.sa.headers()))["students"] == before["students"]
    student = add_person(w.org, "overviewer", kind="student")
    problem(client.get(f"{API}/admin/overview", headers=student.headers()), 403)
    for gone in ("/admin/analytics/organization", "/admin/analytics/finance", "/admin/reports/dues.csv"):
        assert client.get(f"{API}{gone}", headers=w.sa.headers()).status_code == 404


# --- the Platform Owner console --------------------------------------------------------------


@pytest.fixture
def po(client: TestClient) -> dict[str, str]:
    return bearer(sign_in(client, new_owner()))


def test_platform_sees_counts_not_people(client: TestClient, w: W, po: dict[str, str]) -> None:
    page = ok(client.get(f"{API}/platform/organizations?q={w.org.slug}", headers=po))
    assert [o["id"] for o in page["items"]] == [str(w.org.id)]
    assert page["items"][0]["counts"] == {
        "hostels": 2,
        "beds": 3,
        "students_active": 2,
        "staff_active": 1,
    }  # the one Administrator (D76)
    detail = ok(client.get(f"{API}/platform/organizations/{w.org.id}", headers=po))
    assert detail["status"] == "active"
    blob = str(detail) + str(page)
    for secret in (w.s1["student_code"], "Ravi Kumar", w.sa.email):
        assert secret not in blob
    problem(client.get(f"{API}/platform/organizations/{uuid.uuid4()}", headers=po), 404)


def test_suspension_stops_open_sessions_at_the_next_request(client: TestClient, w: W, po: dict[str, str]) -> None:
    """MT-16, Q14."""
    ok(client.get(f"{API}/me", headers=w.hm.headers()))
    problem(client.post(f"{API}/platform/organizations/{w.org.id}/suspend", headers=po, json={"reason": "short"}), 422)
    done = ok(
        client.post(
            f"{API}/platform/organizations/{w.org.id}/suspend",
            headers=po,
            json={"reason": "Unpaid invoice, per contract"},
        )
    )
    assert done["status"] == "suspended" and done["suspension_reason"] == "Unpaid invoice, per contract"
    problem(client.get(f"{API}/me", headers=w.hm.headers()), 403, "organization_suspended")
    problem(
        client.post(
            f"{API}/platform/organizations/{w.org.id}/suspend", headers=po, json={"reason": "Unpaid invoice again"}
        ),
        409,
    )
    with transaction(w.org.id) as db:  # data unchanged
        assert db.execute(text("SELECT count(*) FROM students")).scalar() == 2
    ok(
        client.post(
            f"{API}/platform/organizations/{w.org.id}/reactivate", headers=po, json={"reason": "Invoice paid in full"}
        )
    )
    ok(client.get(f"{API}/me", headers=w.hm.headers()))
    page = ok(client.get(f"{API}/platform/audit-logs?org_id={w.org.id}&action=organization.suspended", headers=po))
    assert len(page["items"]) == 1 and page["items"][0]["actor_type"] == "platform_user"


def test_platform_audit_view_masks_values(client: TestClient, w: W, po: dict[str, str]) -> None:
    """MT-10, Q55: actions and IDs only; a direct query for the values as hms_platform fails;
    viewing the log is itself audited."""
    page = ok(client.get(f"{API}/platform/audit-logs?org_id={w.org.id}&limit=5", headers=po))
    assert page["items"] and page["next_cursor"]
    for row in page["items"]:
        assert not {"before_values", "after_values", "ip_address", "user_agent"} & set(row)
    nxt = ok(
        client.get(f"{API}/platform/audit-logs?org_id={w.org.id}&limit=5&cursor={page['next_cursor']}", headers=po)
    )
    assert not {r["id"] for r in nxt["items"]} & {r["id"] for r in page["items"]}
    with pytest.raises(Exception, match="permission denied"), transaction(None, role="hms_platform") as db:
        db.execute(text("SELECT before_values FROM audit_logs LIMIT 1"))
    with transaction(None, role="hms_platform") as db:
        viewed = db.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'platform.audit_viewed'")).scalar()
    assert viewed and viewed >= 2


def test_platform_routes_write_only_suspension() -> None:
    """RBAC-10: the database role behind every platform route can write tenant data in exactly two
    ways: organizations' status columns (suspension) and its own audit rows. Nothing else is writable
    (the authenticator reset function went with D87)."""
    with transaction(None, role="hms_platform") as db:
        tables = set(
            db.execute(
                text(
                    "SELECT table_name, privilege_type FROM information_schema.table_privileges "
                    "WHERE grantee = 'hms_platform' AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')"
                )
            ).all()
        )
        columns = set(
            db.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.column_privileges "
                    "WHERE grantee = 'hms_platform' AND privilege_type = 'UPDATE' AND table_name NOT LIKE 'platform_%'"
                )
            ).all()
        )
    own = {"platform_users", "platform_sessions", "platform_settings"}
    assert tables == {(t, p) for t in own for p in ("INSERT", "UPDATE")} | {("audit_logs", "INSERT")}
    assert columns == {
        ("organizations", c) for c in ("status", "suspended_at", "suspension_reason", "updated_at", "version")
    }


def test_platform_health_and_settings(client: TestClient, w: W, po: dict[str, str]) -> None:
    metrics.reset()
    metrics.record(500)
    with transaction(None, role="hms_backup") as db:  # a fresh, successful backup
        db.execute(
            text(
                "INSERT INTO backup_runs (kind, status, started_at, finished_at, size_bytes, details) "
                "VALUES ('logical_dump', 'succeeded', now() - interval '1 minute', now(), 1024, "
                '\'{"location": "default_volume"}\')'
            )
        )
    health = ok(client.get(f"{API}/platform/health", headers=po))
    names = {i["name"]: i for i in health["items"]}
    assert names["Database"]["status"] == "ok"
    assert names["WAL archiving"]["status"] == "ok" and "segments archived" in names["WAL archiving"]["detail"]
    # The user's requirement: the same-disk default must be visible, not silent.
    assert names["Backup location"]["status"] == "warning" and "same disk" in names["Backup location"]["detail"]
    assert names["Last backup"]["status"] == "ok" and names["Last backup"]["detail"].startswith("succeeded")
    assert "server errors" in names["Error rate (this API process, last hour)"]["detail"]
    assert names["Error rate (this API process, last hour)"]["status"] == "warning"
    assert client.get(f"{API}/platform/analytics", headers=po).status_code == 404  # D86: no growth charts
    settings = ok(client.get(f"{API}/platform/settings", headers=po))
    disk = next(s for s in settings if s["key"] == "health.disk_warn_percent")
    assert disk["value"] == 80 and disk["version"] is None
    changed = ok(
        client.patch(f"{API}/platform/settings", headers=po, json={"key": "health.disk_warn_percent", "value": 70})
    )
    assert next(s for s in changed if s["key"] == "health.disk_warn_percent")["version"] == 1
    problem(
        client.patch(f"{API}/platform/settings", headers=po, json={"key": "health.disk_warn_percent", "value": 60}),
        409,
        "edit_conflict",
    )
    problem(client.patch(f"{API}/platform/settings", headers=po, json={"key": "nope", "value": 1}), 422)
    ok(
        client.patch(
            f"{API}/platform/settings", headers=po, json={"key": "health.disk_warn_percent", "value": 80, "version": 1}
        )
    )
