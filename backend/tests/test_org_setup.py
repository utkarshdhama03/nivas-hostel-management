"""Phase 2: organization settings, academic calendar, hostel structure, CSV import, the Administrator,
activation links (docs/04 §12.4-12.6; 05 RBAC-1, 2, 7; AUTH-9; Q69, Q70; D75 one role, D76 one Administrator)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import transaction
from app.main import create_app
from tests.support import Org, Person, add_person, admit, jpeg_bytes, link_token, make_hostel, make_org, upload

Response = httpx.Response


@dataclass
class Setup:
    org: Org
    sa: Person
    h1: dict[str, Any]
    h2: dict[str, Any]
    student: Person  # for "not an Administrator" checks


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
def s(client: TestClient, app_db: str) -> Setup:
    org = make_org()
    sa = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    h1, h2 = make_hostel(client, sa, "H1"), make_hostel(client, sa, "H2", "girls")
    student = add_person(org, "student", kind="student")  # no student record: that would add a hostel
    with transaction(org.id) as db:
        db.execute(
            text(
                "INSERT INTO org_counters (org_id, name, scope_key, last_value) VALUES (:o, 'employee_code', '0000', 3)"
            ),
            {"o": org.id},
        )
    return Setup(org, sa, h1, h2, student)


# --- organization settings and optimistic locking (Q70) --------------------------------------------


def test_organization_settings_and_edit_conflict(client: TestClient, s: Setup) -> None:
    org = ok(client.get("/api/v1/admin/organization", headers=s.sa.headers()))
    v = org["version"]
    updated = ok(
        client.patch(
            "/api/v1/admin/organization",
            headers=s.sa.headers(),
            json={
                "version": v,
                "name": "Renamed Hostels",
                "upi_id": "hostel@okbank",
                "upi_payee_name": "Hostel Office",
                "timezone": "Asia/Kolkata",
            },
        )
    )
    assert updated["name"] == "Renamed Hostels" and updated["version"] == v + 1
    assert updated["profile"]["upi_id"] == "hostel@okbank"
    # A second window still holding the old version is refused (the 04 §10 example).
    body = problem(
        client.patch("/api/v1/admin/organization", headers=s.sa.headers(), json={"version": v, "name": "Overwrite"}),
        409,
        "edit_conflict",
    )
    assert body["current_version"] == v + 1
    problem(
        client.patch(
            "/api/v1/admin/organization", headers=s.sa.headers(), json={"version": v + 1, "timezone": "Mars/Base"}
        ),
        422,
        "validation_error",
    )
    problem(
        client.patch(
            "/api/v1/admin/organization", headers=s.sa.headers(), json={"version": v + 1, "upi_id": "not-a-upi"}
        ),
        422,
        "validation_error",
    )
    problem(
        client.patch("/api/v1/admin/organization", headers=s.sa.headers(), json={"version": v + 1, "status": "active"}),
        422,
        "validation_error",
    )  # RBAC-2
    problem(client.get("/api/v1/admin/organization", headers=s.student.headers()), 403, "wrong_portal")


def test_patch_without_version_rejected(client: TestClient, s: Setup) -> None:
    problem(
        client.patch(
            f"/api/v1/admin/rooms/{s.h1['room']['id']}", headers=s.sa.headers(), json={"status": "maintenance"}
        ),
        422,
        "validation_error",
    )


def test_two_windows_editing_the_same_room(client: TestClient, s: Setup) -> None:
    room = s.h1["room"]
    first = client.patch(
        f"/api/v1/admin/rooms/{room['id']}",
        headers=s.sa.headers(),
        json={"version": room["version"], "notes": "Fan repaired"},
    )
    second = client.patch(
        f"/api/v1/admin/rooms/{room['id']}",
        headers=s.sa.headers(),
        json={"version": room["version"], "status": "maintenance"},
    )
    assert ok(first)["version"] == room["version"] + 1
    problem(second, 409, "edit_conflict")


def test_academic_calendar(client: TestClient, s: Setup) -> None:
    session = ok(
        client.post(
            "/api/v1/admin/academic-sessions",
            headers=s.sa.headers(),
            json={"name": "2026-27", "starts_on": "2026-06-01", "ends_on": "2027-05-31"},
        ),
        201,
    )
    other = ok(
        client.post(
            "/api/v1/admin/academic-sessions",
            headers=s.sa.headers(),
            json={"name": "2027-28", "starts_on": "2027-06-01", "ends_on": "2028-05-31"},
        ),
        201,
    )
    url = f"/api/v1/admin/academic-sessions/{session['id']}/terms"
    ok(
        client.post(
            url, headers=s.sa.headers(), json={"name": "Term 1", "starts_on": "2026-07-01", "ends_on": "2026-11-30"}
        ),
        201,
    )
    problem(
        client.post(
            url, headers=s.sa.headers(), json={"name": "Term X", "starts_on": "2026-11-01", "ends_on": "2026-12-31"}
        ),
        409,
        "term_overlaps",
    )
    problem(
        client.post(
            url, headers=s.sa.headers(), json={"name": "Term 9", "starts_on": "2027-07-01", "ends_on": "2027-08-01"}
        ),
        422,
        "validation_error",
    )
    ok(client.post(f"/api/v1/admin/academic-sessions/{session['id']}/make-current", headers=s.sa.headers()))
    ok(client.post(f"/api/v1/admin/academic-sessions/{other['id']}/make-current", headers=s.sa.headers()))
    current = [
        x["name"] for x in ok(client.get("/api/v1/admin/academic-sessions", headers=s.sa.headers())) if x["is_current"]
    ]
    assert current == ["2027-28"]
    problem(
        client.post(
            "/api/v1/admin/academic-sessions",
            headers=s.sa.headers(),
            json={"name": "2026-27", "starts_on": "2030-06-01", "ends_on": "2031-05-31"},
        ),
        409,
        "session_name_taken",
    )


# --- hostel structure and scope --------------------------------------------------------------------


def test_every_administrator_works_in_every_hostel(client: TestClient, s: Setup) -> None:
    """D75: no hostel scoping any more; a student gets nothing."""
    listed = {h["code"] for h in ok(client.get("/api/v1/admin/hostels", headers=s.sa.headers()))}
    assert listed == {"H1", "H2"}
    ok(client.post(f"/api/v1/admin/hostels/{s.h2['id']}/buildings", headers=s.sa.headers(), json={"name": "B"}), 201)
    ok(client.post("/api/v1/admin/hostels", headers=s.sa.headers(), json={"code": "H9", "name": "Nine"}), 201)
    problem(client.get("/api/v1/admin/hostels", headers=s.student.headers()), 403, "wrong_portal")
    patched = ok(
        client.patch(
            f"/api/v1/admin/hostels/{s.h1['id']}",
            headers=s.sa.headers(),
            json={"version": s.h1["version"], "local_outing_requires_approval": False},
        )
    )
    assert patched["local_outing_requires_approval"] is False
    # The roll-call, gate and curfew settings went with attendance and gate passes (D75, schema cleanup).
    problem(
        client.patch(
            f"/api/v1/admin/hostels/{s.h1['id']}",
            headers=s.sa.headers(),
            json={"version": patched["version"], "gate_return_grace_minutes": 45},
        ),
        422,
        "validation_error",
    )
    assert "attendance_window_start" not in patched and "curfew_time" not in patched


def test_rooms_beds_rules(client: TestClient, s: Setup) -> None:
    room = s.h1["room"]
    ok(client.post(f"/api/v1/admin/rooms/{room['id']}/beds", headers=s.sa.headers(), json={"label": "B"}), 201)
    problem(
        client.post(f"/api/v1/admin/rooms/{room['id']}/beds", headers=s.sa.headers(), json={"label": "C"}),
        409,
        "room_full",
    )
    problem(
        client.post(
            f"/api/v1/admin/floors/{s.h1['floor']['id']}/rooms",
            headers=s.sa.headers(),
            json={"number": "101", "room_type_id": s.h1["room_type"]["id"]},
        ),
        409,
        "room_number_taken",
    )
    problem(
        client.post(
            f"/api/v1/admin/floors/{s.h1['floor']['id']}/rooms",
            headers=s.sa.headers(),
            json={"number": "102", "room_type_id": s.h2["room_type"]["id"]},
        ),
        422,
        "validation_error",
    )


def test_bed_board(client: TestClient, s: Setup) -> None:
    board = ok(client.get(f"/api/v1/admin/hostels/{s.h1['id']}/bed-board", headers=s.sa.headers()))
    assert board["counts"] == {"vacant": 1}
    bed = s.h1["bed"]
    ok(
        client.patch(
            f"/api/v1/admin/beds/{bed['id']}",
            headers=s.sa.headers(),
            json={"version": bed["version"], "status": "maintenance"},
        )
    )
    board = ok(client.get(f"/api/v1/admin/hostels/{s.h1['id']}/bed-board", headers=s.sa.headers()))
    assert board["counts"] == {"maintenance": 1}
    assert board["rooms"][0]["beds"][0]["state"] == "maintenance"


CSV_OK = "building,floor,room,room_type,bed\nB,1,201,Double,A\nB,1,201,Double,B\nB,2,301,Double,A\nA,1,102,Double,A\n"


def _import(client: TestClient, s: Setup, content: str, who: Person | None = None) -> Response:
    return client.post(
        f"/api/v1/admin/hostels/{s.h1['id']}/imports/rooms-beds",
        headers=(who or s.sa).headers(),
        files={"file": ("rooms.csv", content.encode(), "text/csv")},
    )


def _bed_count(client: TestClient, s: Setup) -> int:
    board = ok(client.get(f"/api/v1/admin/hostels/{s.h1['id']}/bed-board", headers=s.sa.headers()))
    return sum(board["counts"].values())


def test_csv_import(client: TestClient, s: Setup) -> None:
    result = ok(_import(client, s, CSV_OK))
    assert result == {"rows": 4, "created": {"buildings": 1, "floors": 2, "rooms": 3, "beds": 4}}
    assert _bed_count(client, s) == 5


def test_csv_import_bad_rows_import_nothing(client: TestClient, s: Setup) -> None:
    before = _bed_count(client, s)
    bad = (
        "building,floor,room,room_type,bed\n"
        "B,1,201,Double,A\n"  # fine
        "B,1,201,Suite,B\n"  # unknown room type
        "B,x,202,Double,A\n"  # bad floor
        "B,1,201,Double,A\n"  # duplicate bed
        "A,1,101,Double,A\n"
    )  # already exists
    body = problem(_import(client, s, bad), 422, "validation_error")
    fields = [e["field"] for e in body["errors"]]
    assert "row 3: room_type" in fields and "row 4: floor" in fields and "row 5: bed" in fields
    assert _bed_count(client, s) == before  # nothing half-imported
    body = problem(_import(client, s, "building,floor,room,room_type,bed\nA,1,101,Double,A\n"), 422, "validation_error")
    assert body["errors"][0]["field"] == "row 2: bed"
    assert _bed_count(client, s) == before
    problem(_import(client, s, "room,bed\n1,A\n"), 422, "validation_error")
    problem(_import(client, s, CSV_OK, s.student), 403, "wrong_portal")


# --- the Administrator (D75 one role, D76 one Administrator; 05 RBAC-1, RBAC-7) -----------------------


def _staff_id(client: TestClient, s: Setup, p: Person) -> str:
    items = ok(client.get("/api/v1/admin/staff?page_size=100", headers=s.sa.headers()))["items"]
    return next(i["id"] for i in items if i["user_id"] == str(p.user_id))


def test_the_registering_administrator_is_the_only_one(client: TestClient, s: Setup) -> None:
    """D76: no route adds an Administrator, or deactivates or re-activates a staff account."""
    items = ok(client.get("/api/v1/admin/staff?page_size=100", headers=s.sa.headers()))["items"]
    assert [i["user_id"] for i in items] == [str(s.sa.user_id)] and "roles" not in items[0]
    staff_id = items[0]["id"]
    body = {"full_name": "Second Admin", "email": None}
    assert client.post("/api/v1/admin/staff", headers=s.sa.headers(), json=body).status_code == 405
    for action in ("deactivate", "reactivate"):
        r = client.post(f"/api/v1/admin/staff/{staff_id}/{action}", headers=s.sa.headers())
        assert r.status_code in (404, 405), (action, r.text)
    with transaction(s.org.id) as db:
        assert db.scalar(text("SELECT count(*) FROM role_assignments WHERE revoked_at IS NULL")) == 1


def test_students_have_no_staff_access(client: TestClient, s: Setup) -> None:
    problem(client.get("/api/v1/admin/staff", headers=s.student.headers()), 403, "wrong_portal")


def test_database_refuses_old_roles_and_self_grants(client: TestClient, s: Setup) -> None:
    """D75 (migration 0008): only the Administrator role can be active; nobody grants themselves a role."""
    closed = add_person(s.org, "closed", status="deactivated")  # a staff account closed by D75/D76
    for role in ("hostel_manager", "warden", "accountant", "security_guard"):
        with pytest.raises(Exception, match="role_assignments_role_check"), transaction(s.org.id) as db:
            db.execute(
                text("INSERT INTO role_assignments (org_id, user_id, role, granted_by) VALUES (:o, :u, :r, :g)"),
                {"o": s.org.id, "u": closed.user_id, "r": role, "g": s.sa.user_id},
            )
    with pytest.raises(Exception, match="role_assignments_check1"), transaction(s.org.id) as db:
        db.execute(
            text("INSERT INTO role_assignments (org_id, user_id, role, granted_by) VALUES (:o, :u, 'super_admin', :u)"),
            {"o": s.org.id, "u": closed.user_id},
        )


def test_database_refuses_a_second_administrator(client: TestClient, s: Setup) -> None:
    """D76 (migration 0009): at most one active Administrator grant per organization, whoever writes it."""
    closed = add_person(s.org, "closed", status="deactivated")
    with pytest.raises(IntegrityError) as refused, transaction(s.org.id) as db:
        db.execute(
            text("INSERT INTO role_assignments (org_id, user_id, role, granted_by) VALUES (:o, :u, 'super_admin', :g)"),
            {"o": s.org.id, "u": closed.user_id, "g": s.sa.user_id},
        )
    assert refused.value.orig.diag.constraint_name == "role_assignments_one_administrator"  # type: ignore[union-attr]
    other = make_org()  # another organization has its own one Administrator: no conflict
    add_person(other, "admin", roles=("super_admin",))


def test_patch_staff_with_version(client: TestClient, s: Setup) -> None:
    """The Administrator edits their own details."""
    staff_id = _staff_id(client, s, s.sa)
    current = ok(client.get(f"/api/v1/admin/staff/{staff_id}", headers=s.sa.headers()))
    updated = ok(
        client.patch(
            f"/api/v1/admin/staff/{staff_id}",
            headers=s.sa.headers(),
            json={"version": current["version"], "phone": "+91 98765 43210", "designation": "Head"},
        )
    )
    assert updated["phone"] == "+91 98765 43210" and updated["version"] == current["version"] + 1
    problem(
        client.patch(
            f"/api/v1/admin/staff/{staff_id}",
            headers=s.sa.headers(),
            json={"version": current["version"], "full_name": "Other"},
        ),
        409,
        "edit_conflict",
    )
    problem(
        client.patch(
            f"/api/v1/admin/staff/{staff_id}",
            headers=s.sa.headers(),
            json={"version": updated["version"], "status": "active"},
        ),
        422,
        "validation_error",
    )


# --- activation links (04 §5.7, AUTH-9): for students since D76 -------------------------------------------


def _student(client: TestClient, s: Setup, email: str | None = None, send: bool = False) -> dict[str, Any]:
    email = email or f"student.{uuid.uuid4().hex[:8]}@example.com"  # D80: every student has one
    """A newly admitted student, waiting for activation (a current academic session is made first)."""
    with transaction(s.org.id) as db:
        has_current = db.scalar(text("SELECT count(*) FROM academic_sessions WHERE is_current"))
    if not has_current:
        body = {"name": "2026-27", "starts_on": "2026-06-01", "ends_on": "2027-05-31"}
        made = ok(client.post("/api/v1/admin/academic-sessions", headers=s.sa.headers(), json=body), 201)
        ok(client.post(f"/api/v1/admin/academic-sessions/{made['id']}/make-current", headers=s.sa.headers()))
    return admit(client, s.sa, s.h1["id"], email=email, send_activation_email=send)


def test_emailed_activation_then_login(client: TestClient, s: Setup) -> None:
    email = f"newstudent.{uuid.uuid4().hex[:6]}@example.com"
    student = _student(client, s, email, True)
    token = link_token(email)
    preview = ok(client.post(f"/api/v1/o/{s.org.slug}/auth/activate/preview", json={"token": token}))
    assert preview["full_name"] == student["full_name"]
    problem(
        client.post(f"/api/v1/o/{s.org.slug}/auth/activate", json={"token": token, "password": "short"}),
        422,
        "validation_error",
    )  # 15 characters without 2FA
    ok(
        client.post(
            f"/api/v1/o/{s.org.slug}/auth/activate", json={"token": token, "password": "a long new passphrase"}
        ),
        204,
    )
    problem(
        client.post(f"/api/v1/o/{s.org.slug}/auth/activate", json={"token": token, "password": "x" * 20}),
        410,
        "link_expired",
    )  # single use
    r = client.post(
        f"/api/v1/o/{s.org.slug}/auth/login",
        json={"portal": "student", "identifier": student["student_code"], "password": "a long new passphrase"},
    )
    assert r.status_code == 200 and r.json()["status"] == "authenticated"


def test_the_activation_email_can_be_sent_again(client: TestClient, s: Setup) -> None:
    """D80: no copied links and no in-person reactivation; a new activation email replaces the old link."""
    email = f"again.{uuid.uuid4().hex[:6]}@example.com"
    student = _student(client, s, email)
    url = f"/api/v1/admin/users/{student['user_id']}/activation-email"
    assert client.post(url, headers=s.sa.headers()).status_code == 202
    first = link_token(email)
    assert client.post(url, headers=s.sa.headers()).status_code == 202
    second = link_token(email)
    assert first != second
    problem(
        client.post(f"/api/v1/o/{s.org.slug}/auth/activate/preview", json={"token": first}), 410, "link_expired"
    )  # the new email revoked the old link
    ok(
        client.post(
            f"/api/v1/o/{s.org.slug}/auth/activate", json={"token": second, "password": "a long new passphrase"}
        ),
        204,
    )
    problem(client.post(url, headers=s.sa.headers()), 409, "already_activated")  # an active account gets none
    problem(
        client.post(f"/api/v1/admin/users/{s.sa.user_id}/activation-email", headers=s.sa.headers()),
        409,
        "already_activated",
    )
    for gone in ("activation-link", "reactivate"):  # the in-person paths are gone
        r = client.post(f"/api/v1/admin/users/{student['user_id']}/{gone}", headers=s.sa.headers(), json={})
        assert r.status_code in (404, 405)
    # Copied links exist only for activation (database CHECK, Q2); the application no longer makes any.
    with pytest.raises(Exception, match="auth_tokens_check3"), transaction(s.org.id) as db:
        db.execute(
            text(
                "INSERT INTO auth_tokens (org_id, user_id, purpose, token_hash, delivery, issued_by, "
                "expires_at) VALUES (:o, :u, 'org_registration', :h, 'copied_link', :i, now() + interval '1 day')"
            ),
            {"o": s.org.id, "u": student["user_id"], "h": uuid.uuid4().hex, "i": s.sa.user_id},
        )


def test_admission_needs_an_email_unique_across_the_platform(client: TestClient, s: Setup) -> None:
    """D80: every student has an email, and no two students anywhere share one."""
    _student(client, s)  # a current session
    photo = upload(client, s.sa, "student_photo", jpeg_bytes()).json()["file_id"]
    body = {
        "full_name": "No Mail",
        "hostel_id": s.h1["id"],
        "gender": "male",
        "date_of_birth": "2007-04-01",
        "photo_file_id": photo,
        "admission_date": "2026-06-15",
        "send_activation_email": False,
    }
    problem(client.post("/api/v1/admin/students", headers=s.sa.headers(), json=body), 422, "validation_error")
    first = _student(client, s)
    other = make_org()
    other_sa = add_person(other, "admin", roles=("super_admin",), code="E-0001")
    other_hostel = make_hostel(client, other_sa, "O1")
    made = ok(
        client.post(
            "/api/v1/admin/academic-sessions",
            headers=other_sa.headers(),
            json={"name": "2026-27", "starts_on": "2026-06-01", "ends_on": "2027-05-31"},
        ),
        201,
    )
    ok(client.post(f"/api/v1/admin/academic-sessions/{made['id']}/make-current", headers=other_sa.headers()))
    photo = upload(client, other_sa, "student_photo", jpeg_bytes()).json()["file_id"]
    taken = client.post(
        "/api/v1/admin/students",
        headers=other_sa.headers(),
        json={**body, "hostel_id": other_hostel["id"], "photo_file_id": photo, "email": first["email"].upper()},
    )
    problem(taken, 409, "student_email_taken")
    with pytest.raises(Exception, match="users_student_email_required"), transaction(s.org.id) as db:
        db.execute(
            text("INSERT INTO users (org_id, kind, full_name) VALUES (:o, 'student', 'No Mail')"), {"o": s.org.id}
        )
