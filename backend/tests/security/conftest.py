"""The two-organization world (docs/05 §2), built through the real API where the API exists.

Each organization is REGISTERED through POST /public/organizations, verified with the token
from the Mailpit email, which signs its Administrator in (D87: no authenticator). Hostels H1 and H2
with their structure, and an academic session, are created through the admin API. Since D75/D76 an
organization has exactly one Administrator (the one who registered it), and the world adds a student.
The Administrator works in every hostel, so the cross-tenant suites test "another organization's ID"
(MT-*) and the permission matrix tests "a student on an Administrator route".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.db import transaction
from app.main import create_app
from app.modules.identity.models import RoleAssignment, StaffMember
from app.modules.notifications import service as notifications
from tests.support import Org, Person, add_person, admit, link_token, make_hostel, pdf_bytes, upload

# person key -> (kind, roles, code, hostel: "h1" | "h2" | None = organization-wide)
ROLES: dict[str, tuple[str, tuple[str, ...], str, str | None]] = {
    "student": ("student", (), "STU-2026-9001", None),
}
PASSWORD = "lantern meadow 4 river"
ORG_WIDE = {"admin"}  # the one Administrator (D75, D76) sees every hostel


@dataclass
class SecWorld:
    orgs: dict[str, Org]
    resources: dict[str, dict[str, dict[str, uuid.UUID]]] = field(default_factory=dict)  # org -> type -> key -> id
    names: dict[str, list[str]] = field(default_factory=dict)  # org -> identifying strings (MT-8 scan)

    def ids(self, org: str, kind: str) -> list[uuid.UUID]:
        return list(self.resources[org][kind].values())


def _register(client: TestClient, label: str) -> Org:
    slug = f"sec-{label}-{uuid.uuid4().hex[:6]}"
    email = f"owner.{label}.{uuid.uuid4().hex[:6]}@example.com"
    r = client.post(
        "/api/v1/public/organizations",
        json={
            "organization": {"name": f"Security {label.upper()} Hostels", "slug": slug, "contact_email": email},
            "super_admin": {"full_name": f"Owner {label.upper()}", "email": email, "password": PASSWORD},
        },
    )
    assert r.status_code == 202, r.text
    body = client.post(f"/api/v1/o/{slug}/auth/verify-registration", json={"token": link_token(email)}).json()
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    org = Org(uuid.UUID(claims["org"]), slug)
    org.people["admin"] = Person(
        org.id,
        uuid.UUID(claims["sub"]),
        uuid.UUID(claims["sid"]),
        email,
        "staff",
        ("super_admin",),
        code="E-0001",
    )
    return org


def _build(client: TestClient, label: str) -> tuple[Org, dict[str, dict[str, uuid.UUID]], list[str]]:
    org = _register(client, label)
    admin = org.people["admin"]
    hostels = {"h1": make_hostel(client, admin, "H1"), "h2": make_hostel(client, admin, "H2", "girls")}
    session = client.post(
        "/api/v1/admin/academic-sessions",
        headers=admin.headers(),
        json={"name": "2026-27", "starts_on": "2026-06-01", "ends_on": "2027-05-31"},
    ).json()
    client.post(f"/api/v1/admin/academic-sessions/{session['id']}/make-current", headers=admin.headers())
    students = {
        "h1": admit(client, admin, hostels["h1"]["id"], full_name=f"Student H1 {label}"),
        "h2": admit(client, admin, hostels["h2"]["id"], full_name=f"Student H2 {label}", gender="female"),
    }
    documents: dict[str, uuid.UUID] = {}
    for h, st in students.items():
        f = upload(client, admin, "identity_document", pdf_bytes()).json()["file_id"]
        doc = client.post(
            f"/api/v1/admin/students/{st['id']}/documents",
            headers=admin.headers(),
            json={"file_id": f, "document_type": "identity", "id_type": "pan", "id_number_masked": "XXXXXX123A"},
        )
        documents[h] = uuid.UUID(doc.json()["id"])
    for key, (kind, roles, code, hostel) in ROLES.items():
        add_person(
            org,
            key,
            kind=kind,
            roles=roles,
            code=code,
            hostel_id=uuid.UUID(hostels[hostel]["id"]) if hostel else None,
        )
    res: dict[str, dict[str, uuid.UUID]] = {
        "organization": {"org": org.id},
        "academic_session": {"s": uuid.UUID(session["id"])},
        "session": {k: p.session_id for k, p in org.people.items()},
        "user": {k: p.user_id for k, p in org.people.items()},
    }
    for kind in ("building", "floor", "room", "bed", "room_type"):
        res[kind] = {h: uuid.UUID(hostels[h][kind]["id"]) for h in hostels}
    res["hostel"] = {h: uuid.UUID(hostels[h]["id"]) for h in hostels}
    res["student"] = {h: uuid.UUID(st["id"]) for h, st in students.items()}
    res["guardian"] = {h: uuid.UUID(st["guardians"][0]["id"]) for h, st in students.items()}
    res["file"] = {h: uuid.UUID(st["photo_file_id"]) for h, st in students.items()}
    res["document"] = documents
    since = str(date.today() - timedelta(days=3))
    res["allocation"] = {
        h: uuid.UUID(
            client.post(
                "/api/v1/admin/allocations",
                headers=admin.headers(),
                json={"student_id": st["id"], "bed_id": hostels[h]["bed"]["id"], "start_date": since},
            ).json()["id"]
        )
        for h, st in students.items()
    }
    res.update(_money(client, admin, org, hostels, students))
    res.update(_leave(client, admin, org, students))
    res.update(_phase8(client, org, admin, hostels, students))
    res["registration_request"] = _registration_requests(org, hostels)
    with transaction(org.id) as db:
        res["notification"] = {
            key: notifications.notify(
                db, org_id=org.id, user_id=p.user_id, category="leave", title=f"For {key}", body="x", send_email=False
            ).id
            for key, p in org.people.items()
        }
        by_user = {s.user_id: s.id for s in db.scalars(select(StaffMember))}
        res["staff"] = {k: by_user[p.user_id] for k, p in org.people.items() if p.user_id in by_user}
        roles = {r.user_id: r.id for r in db.scalars(select(RoleAssignment).where(RoleAssignment.revoked_at.is_(None)))}
        res["role_assignment"] = {k: roles[p.user_id] for k, p in org.people.items() if p.user_id in roles}
    names = [str(org.id), org.slug, *(p.email for p in org.people.values())]
    for kind, ids in res.items():
        if kind != "organization":
            names += [str(i) for i in ids.values()]
    return org, res, names


@pytest.fixture(scope="session")
def world(app_db: str) -> SecWorld:
    client = TestClient(create_app(run_checks=False), base_url="https://testserver")
    w = SecWorld(orgs={})
    for label in ("a", "b"):
        org, res, names = _build(client, label)
        w.orgs[label], w.resources[label], w.names[label] = org, res, names
    return w


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


def people(w: SecWorld, org: str = "a") -> list[tuple[str, Person]]:
    return sorted(w.orgs[org].people.items())


def person_hostel(key: str) -> str | None:
    """'h1' / 'h2' for hostel-scoped people, None for organization-wide ones."""
    return None if key in ORG_WIDE or key not in ROLES else ROLES[key][3]


Body = dict[str, Any]


def _money(
    client: TestClient, admin: Person, org: Org, hostels: dict[str, Any], students: dict[str, Any]
) -> dict[str, dict[str, uuid.UUID]]:
    """Phase 5 resources in both hostels: fee structure, charge, payment (with a deposit), refund
    request, UPI submission and a queued charge run."""
    out: dict[str, dict[str, uuid.UUID]] = {
        k: {} for k in ("fee_structure", "ledger_entry", "payment", "refund", "submission", "job")
    }
    h = admin.headers()
    for key, st in students.items():
        fs = client.post(
            "/api/v1/admin/fee-structures",
            headers=h,
            json={
                "hostel_id": hostels[key]["id"],
                "name": f"Rent {key}",
                "fee_kind": "rent",
                "amount": "3000.00",
                "billing_cycle": "monthly",
            },
        ).json()
        out["fee_structure"][key] = uuid.UUID(fs["id"])
        run = client.post(
            "/api/v1/admin/charge-runs",
            headers=h,
            json={"fee_structure_id": fs["id"], "period_start": str(date.today().replace(day=1))},
        ).json()
        out["job"][key] = uuid.UUID(run["job_id"])
        entry = client.post(
            f"/api/v1/admin/students/{st['id']}/charges",
            headers=h,
            json={"kind": "charge", "amount": "1000.00", "description": "Mess", "due_date": str(date.today())},
        ).json()
        out["ledger_entry"][key] = uuid.UUID(entry["id"])
        pay = client.post(
            "/api/v1/admin/payments",
            headers={**h, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "student_id": st["id"],
                "method": "cash",
                "received_on": str(date.today()),
                "dues_amount": "500.00",
                "deposit_amount": "2000.00",
            },
        ).json()
        out["payment"][key] = uuid.UUID(pay["id"])
        rr = client.post(
            "/api/v1/admin/refund-requests",
            headers=h,
            json={"student_id": st["id"], "account": "deposit", "amount": "100.00", "reason": "Security world"},
        ).json()
        out["refund"][key] = uuid.UUID(rr["id"])
        with transaction(org.id) as db:  # the student has no login yet: the submission is written directly
            out["submission"][key] = db.execute(
                text(
                    "INSERT INTO payment_submissions (org_id, hostel_id, student_id, amount, utr, paid_on, "
                    "submitted_by) VALUES (:o, :h, :s, 10, :utr, current_date, :u) RETURNING id"
                ),
                {
                    "o": org.id,
                    "h": st["hostel_id"],
                    "s": st["id"],
                    "utr": f"WORLD{uuid.uuid4().hex[:10]}",
                    "u": st["user_id"],
                },
            ).scalar_one()
    return out


def _leave(client: TestClient, admin: Person, org: Org, students: dict[str, Any]) -> dict[str, dict[str, uuid.UUID]]:
    """Leave in both hostels: an approved emergency leave (through the API) and a pending one."""
    out: dict[str, dict[str, uuid.UUID]] = {"leave": {}, "approved_leave": {}}
    now = datetime.now(UTC)
    for key, st in students.items():
        leave = client.post(
            "/api/v1/admin/leave-requests",
            headers=admin.headers(),
            json={
                "student_id": st["id"],
                "reason": "Security world",
                "starts_at": (now + timedelta(days=1)).isoformat(),
                "ends_at": (now + timedelta(days=2)).isoformat(),
            },
        )
        assert leave.status_code == 201, leave.text
        out["approved_leave"][key] = uuid.UUID(leave.json()["id"])
        with transaction(org.id) as db:
            out["leave"][key] = db.execute(
                text(
                    "INSERT INTO leave_requests (org_id, hostel_id, student_id, leave_type, reason, starts_at, "
                    "ends_at, requested_by) VALUES (:o, :h, :s, 'home', 'Pending one', :a, :b, :u) RETURNING id"
                ),
                {
                    "o": org.id,
                    "h": st["hostel_id"],
                    "s": st["id"],
                    "a": now + timedelta(days=5),
                    "b": now + timedelta(days=6),
                    "u": st["user_id"],
                },
            ).scalar_one()
    return out


def _registration_requests(org: Org, hostels: dict[str, Any]) -> dict[str, uuid.UUID]:
    """D74: a pending registration request per hostel (written directly: the public form is tested
    in test_student_registration.py)."""
    out: dict[str, uuid.UUID] = {}
    with transaction(org.id) as db:
        for key, h in hostels.items():
            out[key] = db.execute(
                text(
                    "INSERT INTO student_registration_requests (org_id, hostel_id, status, full_name, email, gender, "
                    "date_of_birth, guardian_name, guardian_relationship, guardian_phone, password_hash, photo, "
                    "photo_mime, email_verified_at) VALUES (:o, :h, 'pending', 'Applicant', :e, 'male', "
                    "'2007-01-01', 'Guardian', 'father', '+91 98765 00000', 'x', 'x', 'image/jpeg', now()) RETURNING id"
                ),
                {"o": org.id, "h": h["id"], "e": f"applicant.{uuid.uuid4().hex[:8]}@example.com"},
            ).scalar_one()
    return out


def _phase8(
    client: TestClient, org: Org, admin: Person, hostels: dict[str, Any], students: dict[str, Any]
) -> dict[str, dict[str, uuid.UUID]]:
    """Phase 8 resources: a complaint category; per hostel a complaint."""
    h = admin.headers()
    cat = client.post("/api/v1/admin/complaint-categories", headers=h, json={"name": "Plumbing"}).json()
    out: dict[str, dict[str, uuid.UUID]] = {
        "complaint_category": {"c": uuid.UUID(cat["id"])},
        "complaint": {},
    }
    for key, st in students.items():
        hid = hostels[key]["id"]
        with transaction(org.id) as db:  # the world's students have no login: written directly
            out["complaint"][key] = db.execute(
                text(
                    "INSERT INTO complaints (org_id, hostel_id, student_id, category_id, title, description, "
                    "priority, target_date) VALUES (:o, :h, :s, :c, 'Leaking tap', 'Bathroom tap leaks', 'medium', "
                    "current_date + 7) RETURNING id"
                ),
                {"o": org.id, "h": hid, "s": st["id"], "c": cat["id"]},
            ).scalar_one()
    return out
