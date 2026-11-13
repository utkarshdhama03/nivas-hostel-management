"""Leave since D75/D76 (docs/04 §12.11, Q35): asked by the student, decided by the Administrator, no gate pass."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import transaction
from app.main import create_app
from tests.support import Org, Person, add_person, make_org

API = "/api/v1"


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


def ok(r: httpx.Response, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


def _world() -> tuple[Org, Person, Person, Person]:
    org = make_org()
    a = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    st = add_person(org, "st", kind="student", code="STU-2026-0301")
    other = add_person(org, "other", kind="student", code="STU-2026-0302")
    return org, a, st, other


def _ask(client: TestClient, st: Person) -> dict[str, Any]:
    now = datetime.now(UTC)
    body = {
        "leave_type": "home",
        "reason": "Sister's wedding",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=3)).isoformat(),
    }
    return dict(ok(client.post(f"{API}/portal/me/leave-requests", headers=st.headers(), json=body), 201))


def _titles(org: Org, p: Person) -> list[str]:
    with transaction(org.id) as db:
        return list(db.scalars(text("SELECT title FROM notifications WHERE user_id = :u"), {"u": p.user_id}))


def test_the_administrator_is_told_and_approval_makes_no_pass(client: TestClient, app_db: str) -> None:
    org, a, st, _ = _world()
    leave = _ask(client, st)
    assert leave["status"] == "pending" and "gate_pass" not in leave
    assert "New leave request" in _titles(org, a)
    approved = ok(client.post(f"{API}/admin/leave-requests/{leave['id']}/approve", headers=a.headers(), json={}))
    assert approved["status"] == "approved" and "gate_pass" not in approved
    assert "Leave approved" in _titles(org, st)
    with transaction(org.id) as db:  # the gate pass table is gone (schema cleanup)
        assert db.scalar(text("SELECT to_regclass('gate_passes')")) is None


def test_student_cannot_approve_leave(client: TestClient, app_db: str) -> None:
    """05 GATE-*/LEAVE: approving is an Administrator's job; the portal has no such route."""
    _, _, st, other = _world()
    leave = _ask(client, st)
    for who in (st, other):
        r = client.post(f"{API}/admin/leave-requests/{leave['id']}/approve", headers=who.headers(), json={})
        assert r.status_code == 403 and r.json()["code"] == "wrong_portal", r.text


def test_gate_attendance_and_qr_routes_are_gone(client: TestClient, app_db: str) -> None:
    """D75: the API routes are disabled (the tables stay, unused)."""
    _, a, st, _ = _world()
    for path in ("/admin/gate/scans", "/admin/attendance/sessions", "/admin/staff/x/roles"):
        assert client.post(f"{API}{path}", headers=a.headers(), json={}).status_code in (404, 405), path
    assert client.get(f"{API}/portal/me/qr-token", headers=st.headers()).status_code == 404
    assert client.get(f"{API}/portal/me/attendance", headers=st.headers()).status_code == 404
