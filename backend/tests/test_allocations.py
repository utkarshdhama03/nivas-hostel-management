"""Phase 4: bed allocation (docs/04 §12.8; 01 ALC-1..5; 02 B2; Q19, Q21; 03 §8 lock order;
05 MT-16). Race tests run real concurrent HTTP requests released together by a barrier."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.job_handlers  # noqa: F401  the same handlers the worker has
from app.core import jobs
from app.core.db import transaction
from app.main import create_app
from tests.support import Org, Person, activation_token, add_person, admit, make_hostel, make_org

Response = httpx.Response


@dataclass
class W:
    org: Org
    sa: Person
    hm: Person
    hm2: Person
    warden: Person
    h1: dict[str, Any]
    h2: dict[str, Any]
    beds: list[str]  # four beds in h1: room 101 A/B, room 102 A/B


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
            json={"name": "2026-27", "starts_on": "2026-06-01", "ends_on": "2027-05-31"},
        ),
        201,
    )
    ok(client.post(f"/api/v1/admin/academic-sessions/{session['id']}/make-current", headers=sa.headers()))
    csv = "building,floor,room,room_type,bed\nA,1,101,Double,B\nA,1,102,Double,A\nA,1,102,Double,B\n"
    ok(
        client.post(
            f"/api/v1/admin/hostels/{h1['id']}/imports/rooms-beds",
            headers=sa.headers(),
            files={"file": ("r.csv", csv.encode(), "text/csv")},
        )
    )
    board = ok(client.get(f"/api/v1/admin/hostels/{h1['id']}/bed-board", headers=sa.headers()))
    beds = [b["id"] for room in board["rooms"] for b in room["beds"]]
    hm = hm2 = warden = sa  # D76: one Administrator; the names say what each step stands for
    return W(org, sa, hm, hm2, warden, h1, h2, beds)


def allocate(client: TestClient, who: Person, student: str, bed: str, **kw: Any) -> Response:
    return client.post(
        "/api/v1/admin/allocations", headers=who.headers(), json={"student_id": student, "bed_id": bed, **kw}
    )


def board_counts(client: TestClient, w: W) -> dict[str, int]:
    return dict(ok(client.get(f"/api/v1/admin/hostels/{w.h1['id']}/bed-board", headers=w.hm.headers()))["counts"])


def test_assign_shows_on_board_and_in_portal(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    a = ok(allocate(client, w.hm, s["id"], w.beds[0]), 201)
    assert a["status"] == "active" and a["checked_in_at"] and a["end_date"] is None
    assert board_counts(client, w) == {"occupied": 1, "vacant": 3}
    link = {"activation_url": "#token=" + activation_token(client, w.hm.headers(), s)}
    ok(
        client.post(
            f"/api/v1/o/{w.org.slug}/auth/activate",
            json={"token": link["activation_url"].split("#token=")[1], "password": "a fine long passphrase"},
        ),
        204,
    )
    login = ok(
        client.post(
            f"/api/v1/o/{w.org.slug}/auth/login",
            json={"portal": "student", "identifier": s["student_code"], "password": "a fine long passphrase"},
        )
    )
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    # D79: the student's room view is gone, and so are the notices that linked to it.
    assert client.get("/api/v1/portal/me/room", headers=headers).status_code == 404
    notes = ok(client.get("/api/v1/me/notifications", headers=headers))["items"]
    assert not any(n["category"] == "allocation" or n["link"] == "/portal/room" for n in notes)
    history = ok(client.get(f"/api/v1/admin/students/{s['id']}/allocations", headers=w.warden.headers()))
    assert [h["id"] for h in history] == [a["id"]]
    assert [
        h["id"] for h in ok(client.get(f"/api/v1/admin/beds/{w.beds[0]}/allocations", headers=w.warden.headers()))
    ] == [a["id"]]


def test_board_says_who_is_in_each_bed(client: TestClient, w: W) -> None:
    """The bed map (12 November): each bed carries its occupant or reservation, its own status and version.
    Read-only; allocating still goes through POST /allocations with all its rules."""
    s1 = admit(client, w.hm, w.h1["id"], full_name="Asha Rao")
    s2 = admit(client, w.hm, w.h1["id"], full_name="Ravi Kumar")
    stay = ok(allocate(client, w.hm, s1["id"], w.beds[0]), 201)
    held = ok(allocate(client, w.hm, s2["id"], w.beds[1], mode="reserve"), 201)
    board = ok(client.get(f"/api/v1/admin/hostels/{w.h1['id']}/bed-board", headers=w.hm.headers()))
    beds = {b["id"]: b for room in board["rooms"] for b in room["beds"]}
    occupied, reserved = beds[w.beds[0]], beds[w.beds[1]]
    assert occupied["state"] == "occupied" and occupied["status"] == "available" and occupied["version"] >= 1
    assert occupied["occupant"] == {
        "allocation_id": stay["id"],
        "allocation_status": "active",
        "student_id": s1["id"],
        "student_code": s1["student_code"],
        "student_name": "Asha Rao",
        "since": stay["start_date"],
        "reservation_expires_at": None,
    }
    assert reserved["state"] == "reserved" and reserved["occupant"]["allocation_id"] == held["id"]
    assert (
        reserved["occupant"]["student_name"] == "Ravi Kumar" and reserved["occupant"]["allocation_status"] == "reserved"
    )
    assert all(beds[b]["occupant"] is None for b in w.beds[2:])  # vacant beds have nobody
    room = next(r for r in board["rooms"] if any(b["id"] == w.beds[0] for b in r["beds"]))
    assert room["building_id"] and room["floor_id"] and room["building"] == "A" and room["floor_level"] == 1


def test_list_view_search(client: TestClient, w: W) -> None:
    """The List view's search: student name, Student ID or room number."""
    s1 = admit(client, w.hm, w.h1["id"], full_name="Meera Iyer")
    s2 = admit(client, w.hm, w.h1["id"], full_name="Kabir Shah")
    ok(allocate(client, w.hm, s1["id"], w.beds[0]), 201)  # room 101
    ok(allocate(client, w.hm, s2["id"], w.beds[-1]), 201)  # room 102

    def names(q: str) -> set[str]:
        return {
            a["student_name"]
            for a in ok(client.get(f"/api/v1/admin/allocations?q={q}", headers=w.hm.headers()))["items"]
        }

    assert names("meera") == {"Meera Iyer"}
    assert names(s2["student_code"]) == {"Kabir Shah"}
    assert names("101") == {"Meera Iyer"}
    assert names("nobody-like-this") == set()
    s3 = admit(client, w.hm, w.h1["id"], full_name="Neha Bose")
    picker = ok(client.get(f"/api/v1/admin/students?without_bed=true&hostel_id={w.h1['id']}", headers=w.hm.headers()))
    assert {s["id"] for s in picker["items"]} == {s3["id"]}  # the bed map offers only students without a bed


def test_double_booking_impossible(client: TestClient, w: W) -> None:
    s1, s2 = admit(client, w.hm, w.h1["id"]), admit(client, w.hm, w.h1["id"])
    ok(allocate(client, w.hm, s1["id"], w.beds[0]), 201)
    problem(allocate(client, w.hm, s2["id"], w.beds[0]), 409, "bed_already_allocated")
    problem(allocate(client, w.hm, s1["id"], w.beds[1]), 409, "student_already_allocated")  # ALC-5


def _race(calls: list[tuple[Person, str, Any]]) -> list[Response]:
    barrier = threading.Barrier(len(calls))
    results: list[Response | None] = [None] * len(calls)

    def run(i: int, who: Person, path: str, body: Any) -> None:
        c = TestClient(create_app(run_checks=False), base_url="https://testserver")
        barrier.wait()
        results[i] = c.post(path, headers=who.headers(), json=body)

    threads = [threading.Thread(target=run, args=(i, *call)) for i, call in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [r for r in results if r is not None]


def test_two_managers_one_bed_exactly_one_wins(client: TestClient, w: W) -> None:
    s1, s2 = admit(client, w.hm, w.h1["id"]), admit(client, w.hm, w.h1["id"])
    results = _race(
        [
            (w.hm, "/api/v1/admin/allocations", {"student_id": s1["id"], "bed_id": w.beds[0]}),
            (w.sa, "/api/v1/admin/allocations", {"student_id": s2["id"], "bed_id": w.beds[0]}),
        ]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 409)
    assert loser.json()["code"] == "bed_already_allocated"  # never a deadlock or a 500
    assert board_counts(client, w)["occupied"] == 1


def test_one_student_two_beds_exactly_one_wins(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    results = _race(
        [
            (w.hm, "/api/v1/admin/allocations", {"student_id": s["id"], "bed_id": w.beds[0]}),
            (w.sa, "/api/v1/admin/allocations", {"student_id": s["id"], "bed_id": w.beds[2]}),
        ]
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "student_already_allocated"


def test_crossing_transfers_never_deadlock(client: TestClient, w: W) -> None:
    """Lock order (03 §8): both beds in ascending ID order. Two students swapping beds at the
    same moment would deadlock with any other order; here both get a clean 409."""
    s1, s2 = admit(client, w.hm, w.h1["id"]), admit(client, w.hm, w.h1["id"])
    a1 = ok(allocate(client, w.hm, s1["id"], w.beds[0], start_date=str(date.today() - timedelta(days=3))), 201)
    a2 = ok(allocate(client, w.hm, s2["id"], w.beds[2], start_date=str(date.today() - timedelta(days=3))), 201)
    results = _race(
        [
            (w.hm, f"/api/v1/admin/allocations/{a1['id']}/transfer", {"bed_id": w.beds[2]}),
            (w.sa, f"/api/v1/admin/allocations/{a2['id']}/transfer", {"bed_id": w.beds[0]}),
        ]
    )
    assert [r.status_code for r in results] == [409, 409], [r.text for r in results]
    assert {r.json()["code"] for r in results} == {"bed_already_allocated"}


def test_transfer_keeps_history(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    first = ok(allocate(client, w.hm, s["id"], w.beds[0], start_date=str(date.today() - timedelta(days=10))), 201)
    moved = ok(
        client.post(
            f"/api/v1/admin/allocations/{first['id']}/transfer", headers=w.hm.headers(), json={"bed_id": w.beds[3]}
        ),
        201,
    )
    assert moved["previous_allocation_id"] == first["id"] and moved["status"] == "active"
    history = ok(client.get(f"/api/v1/admin/students/{s['id']}/allocations", headers=w.hm.headers()))
    old = next(h for h in history if h["id"] == first["id"])
    assert old["status"] == "ended" and old["end_reason"] == "transfer" and old["end_date"] == str(date.today())
    # Moved again on the same day it began: the zero-length stay is cancelled, not ended.
    again = ok(
        client.post(
            f"/api/v1/admin/allocations/{moved['id']}/transfer", headers=w.hm.headers(), json={"bed_id": w.beds[1]}
        ),
        201,
    )
    history = {
        h["id"]: h for h in ok(client.get(f"/api/v1/admin/students/{s['id']}/allocations", headers=w.hm.headers()))
    }
    assert history[moved["id"]]["status"] == "cancelled" and history[again["id"]]["status"] == "active"
    assert board_counts(client, w) == {"occupied": 1, "vacant": 3}


def test_reservation_lifecycle(client: TestClient, w: W) -> None:
    s1, s2 = admit(client, w.hm, w.h1["id"]), admit(client, w.hm, w.h1["id"])
    later = date.today() + timedelta(days=10)
    r = ok(allocate(client, w.hm, s1["id"], w.beds[0], mode="reserve", start_date=str(later)), 201)
    assert r["status"] == "reserved" and r["reservation_expires_at"]
    # Another stay that ends before the reservation starts fits; one that overlaps it doesn't.
    ok(allocate(client, w.hm, s2["id"], w.beds[0], end_date=str(later)), 201)
    s3 = admit(client, w.hm, w.h1["id"])
    problem(
        allocate(client, w.hm, s3["id"], w.beds[0], mode="reserve", start_date=str(later + timedelta(days=2))),
        409,
        "bed_already_allocated",
    )
    problem(allocate(client, w.hm, s3["id"], w.beds[1], start_date=str(later)), 422, "validation_error")
    ok(client.post(f"/api/v1/admin/allocations/{r['id']}/cancel", headers=w.hm.headers()))
    problem(
        client.post(f"/api/v1/admin/allocations/{r['id']}/check-in", headers=w.hm.headers()), 409, "allocation_state"
    )


def test_check_in_and_check_out(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    r = ok(allocate(client, w.hm, s["id"], w.beds[1], mode="reserve"), 201)
    assert board_counts(client, w)["reserved"] == 1
    active = ok(client.post(f"/api/v1/admin/allocations/{r['id']}/check-in", headers=w.hm.headers()))
    assert active["status"] == "active" and active["start_date"] == str(date.today())
    problem(
        client.post(
            f"/api/v1/admin/students/{s['id']}/exit",
            headers=w.hm.headers(),
            json={"exited_on": str(date.today()), "exit_reason": "graduated"},
        ),
        409,
        "allocation_open",
    )
    ended = ok(client.post(f"/api/v1/admin/allocations/{r['id']}/check-out", headers=w.hm.headers()))
    # Left on the arrival day: a zero-length stay, recorded as cancelled; the bed is free at once.
    assert ended["status"] == "cancelled" and ended["end_reason"] == "checkout"
    assert board_counts(client, w) == {"vacant": 4}
    ok(
        client.post(
            f"/api/v1/admin/students/{s['id']}/exit",
            headers=w.hm.headers(),
            json={"exited_on": str(date.today()), "exit_reason": "graduated"},
        )
    )
    s2 = admit(client, w.hm, w.h1["id"])
    ok(allocate(client, w.hm, s2["id"], w.beds[1]), 201)  # the bed really is free
    problem(allocate(client, w.hm, s["id"], w.beds[2]), 409, "student_exited")


def test_normal_check_out_frees_the_bed_from_that_day(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    a = ok(allocate(client, w.hm, s["id"], w.beds[0], start_date=str(date.today() - timedelta(days=30))), 201)
    ended = ok(client.post(f"/api/v1/admin/allocations/{a['id']}/check-out", headers=w.hm.headers()))
    assert ended["status"] == "ended" and ended["end_date"] == str(date.today()) and ended["checked_out_at"]
    assert board_counts(client, w) == {"vacant": 4}
    ok(allocate(client, w.hm, admit(client, w.hm, w.h1["id"])["id"], w.beds[0]), 201)


def test_reservation_expiry_job_releases_the_bed(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    r = ok(allocate(client, w.hm, s["id"], w.beds[2], mode="reserve"), 201)
    with transaction(w.org.id) as db:
        db.execute(
            text("UPDATE bed_allocations SET reservation_expires_at = now() - interval '1 minute' WHERE id = :a"),
            {"a": r["id"]},
        )
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
        jobs.enqueue(db, "allocations.expire_reservations", org_id=None)
    assert jobs.work_once("w") == "succeeded"
    history = ok(client.get(f"/api/v1/admin/students/{s['id']}/allocations", headers=w.hm.headers()))
    assert history[0]["status"] == "cancelled" and history[0]["end_reason"] == "reservation_expired"
    ok(allocate(client, w.hm, admit(client, w.hm, w.h1["id"])["id"], w.beds[2]), 201)


def test_maintenance_bed_and_gender_rules(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    bed = ok(client.get(f"/api/v1/admin/rooms/{w.h1['room']['id']}/beds", headers=w.hm.headers()))[0]
    ok(
        client.patch(
            f"/api/v1/admin/beds/{bed['id']}",
            headers=w.hm.headers(),
            json={"version": bed["version"], "status": "maintenance"},
        )
    )
    problem(allocate(client, w.hm, s["id"], bed["id"]), 409, "bed_unavailable")
    room = ok(client.get(f"/api/v1/admin/floors/{w.h1['floor']['id']}/rooms", headers=w.hm.headers()))
    r102 = next(r for r in room if r["number"] == "102")
    ok(
        client.patch(
            f"/api/v1/admin/rooms/{r102['id']}",
            headers=w.hm.headers(),
            json={"version": r102["version"], "status": "inactive"},
        )
    )
    problem(allocate(client, w.hm, s["id"], w.beds[2]), 409, "bed_unavailable")
    # Q19: the hostel became a girls' hostel after this male student's admission.
    hostel = ok(client.get(f"/api/v1/admin/hostels/{w.h1['id']}", headers=w.hm.headers()))
    ok(
        client.patch(
            f"/api/v1/admin/hostels/{w.h1['id']}",
            headers=w.hm.headers(),
            json={"version": hostel["version"], "resident_type": "girls"},
        )
    )
    other_bed = next(b for b in w.beds if b != bed["id"] and b not in (w.beds[2], w.beds[3]))
    problem(allocate(client, w.hm, s["id"], other_bed), 422, "gender_hostel_mismatch")


def test_scope_and_permissions(client: TestClient, w: W) -> None:
    """D75: every Administrator works in every hostel; a student allocates nothing."""
    s = admit(client, w.hm, w.h1["id"])
    student = add_person(w.org, "stu", kind="student", code="STU-2026-0950")
    problem(allocate(client, student, s["id"], w.beds[0]), 403, "wrong_portal")
    problem(allocate(client, w.sa, s["id"], w.h2["bed"]["id"]), 422, "different_hostel")  # ALC-6 not decided
    ok(allocate(client, w.hm2, s["id"], w.beds[0]), 201)  # "hm2" was once limited to H2: no longer
    assert len(ok(client.get("/api/v1/admin/allocations", headers=w.warden.headers()))["items"]) == 1


def test_suspended_org_jobs_not_run(client: TestClient, w: W) -> None:
    """MT-16: while an organization is suspended its jobs are postponed, not run, and not lost."""
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
    with transaction(w.org.id) as db:
        job_id = jobs.enqueue(db, "email.notification", {"notification_id": str(uuid.uuid4())}, org_id=w.org.id)
    with transaction(None, role="hms_platform") as db:
        db.execute(
            text(
                "UPDATE organizations SET status = 'suspended', suspended_at = now(), "
                "suspension_reason = 'test' WHERE id = :o"
            ),
            {"o": w.org.id},
        )
    assert jobs.work_once("w") == "postponed"
    with transaction(None, role="hms_worker") as db:
        row = db.execute(text("SELECT status, attempts, run_at > now() FROM jobs WHERE id = :j"), {"j": job_id}).one()
    assert tuple(row) == ("queued", 0, True)
