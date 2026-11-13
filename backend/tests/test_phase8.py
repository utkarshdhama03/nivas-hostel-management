"""Phase 8: complaints, mess and announcements (docs/04 §12.12-12.15; Q40-Q42). Inventory went with D85."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.job_handlers  # noqa: F401
from app.core import jobs
from app.core.db import transaction
from app.main import create_app
from tests.support import Org, Person, add_person, jpeg_bytes, make_hostel, make_org, upload

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
    h1: dict[str, Any]
    h2: dict[str, Any]
    st1: Person
    st2: Person
    st3: Person  # in H2
    # D75: all six are Administrators (one role, every hostel). The names only tell them apart.
    warden: Person
    warden2: Person
    hm: Person
    hm2: Person
    guard: Person
    accountant: Person
    category: str


def _student(org: Org, key: str, code: str, hostel_id: str) -> Person:
    p = add_person(org, key, kind="student", code=code)
    with transaction(org.id) as db:
        db.execute(text("UPDATE students SET hostel_id = :h WHERE user_id = :u"), {"h": hostel_id, "u": p.user_id})
    return p


@pytest.fixture
def w(client: TestClient, app_db: str) -> W:
    org = make_org()
    sa = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    h1, h2 = make_hostel(client, sa, "H1"), make_hostel(client, sa, "H2")
    cat = ok(client.post(f"{API}/admin/complaint-categories", headers=sa.headers(), json={"name": "Plumbing"}), 201)
    return W(
        org,
        sa,
        h1,
        h2,
        _student(org, "st1", "STU-2026-0201", h1["id"]),
        _student(org, "st2", "STU-2026-0202", h1["id"]),
        _student(org, "st3", "STU-2026-0203", h2["id"]),
        sa,  # warden: the one Administrator (D76)
        sa,  # warden2: the one Administrator (D76)
        sa,  # hm: the one Administrator (D76)
        sa,  # hm2: the one Administrator (D76)
        sa,  # guard: the one Administrator (D76)
        sa,  # acc: the one Administrator (D76)
        cat["id"],
    )


def titles(w: W, p: Person) -> list[str]:
    with transaction(w.org.id) as db:
        return list(db.scalars(text("SELECT title FROM notifications WHERE user_id = :u"), {"u": p.user_id}))


def run(kind: str) -> None:
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs WHERE org_id IS NULL"))
        jobs.enqueue(db, kind, org_id=None)
    while jobs.work_once("w") is not None:
        pass


def drain() -> None:
    while jobs.work_once("w") is not None:
        pass


# --- complaints ------------------------------------------------------------------------------


def complain(client: TestClient, w: W, who: Person | None = None, **kw: Any) -> dict[str, Any]:
    body = {"category_id": w.category, "title": "Leaking tap", "description": "The tap drips all night", **kw}
    return dict(ok(client.post(f"{API}/portal/me/complaints", headers=(who or w.st1).headers(), json=body), 201))


def test_complaint_lifecycle_with_reopen_and_auto_close(client: TestClient, w: W) -> None:
    photo = ok(upload(client, w.st1, "complaint_attachment", jpeg_bytes()), 201)["file_id"]
    c = complain(client, w, priority="high", attachment_file_ids=[photo])
    assert c["status"] == "open" and c["attachment_file_ids"] == [photo] and c["student_code"] == "STU-2026-0201"
    assert "New high complaint" in titles(w, w.warden)
    assert "New high complaint" in titles(w, w.warden2)  # every Administrator is told (D75)
    url = f"{API}/admin/complaints/{c['id']}"
    problem(client.post(f"{url}/start", headers=w.warden.headers()), 409, "not_assigned")
    ok(client.post(f"{url}/assign", headers=w.warden.headers(), json={"user_id": str(w.hm.user_id)}))
    assert "Complaint assigned to you" not in titles(w, w.hm)  # D76: assigned to oneself, so no notice
    ok(client.post(f"{url}/start", headers=w.warden.headers()))
    problem(client.post(f"{url}/resolve", headers=w.warden.headers(), json={}), 422)
    done = ok(client.post(f"{url}/resolve", headers=w.warden.headers(), json={"note": "Washer replaced"}))
    assert done["status"] == "resolved" and done["can_reopen_until"]
    assert "Your complaint was resolved" in titles(w, w.st1)
    # The student reopens within 7 days (Q40).
    reopened = ok(
        client.post(
            f"{API}/portal/me/complaints/{c['id']}/reopen", headers=w.st1.headers(), json={"comment": "Still drips"}
        )
    )
    assert reopened["status"] == "open" and reopened["reopen_count"] == 1 and reopened["resolved_at"] is None
    assert [(u["from_status"], u["to_status"]) for u in reopened["updates"]] == [
        (None, "open"),
        (None, None),
        ("open", "in_progress"),
        ("in_progress", "resolved"),
        ("resolved", "open"),
    ]
    ok(client.post(f"{url}/resolve", headers=w.warden.headers(), json={"note": "Tap replaced"}))
    with transaction(w.org.id) as db:
        db.execute(text("UPDATE complaints SET resolved_at = now() - interval '8 days' WHERE id = :c"), {"c": c["id"]})
    problem(
        client.post(f"{API}/portal/me/complaints/{c['id']}/reopen", headers=w.st1.headers(), json={"comment": "Again"}),
        409,
        "reopen_window_closed",
    )
    run("complaints.auto_close")
    assert ok(client.get(url, headers=w.warden.headers()))["status"] == "closed"


def test_complaint_visibility(client: TestClient, w: W) -> None:
    """Q40 since D75: the submitter and the Administrators; no other student."""
    photo = ok(upload(client, w.st1, "complaint_attachment", jpeg_bytes()), 201)["file_id"]
    c = complain(client, w, attachment_file_ids=[photo])
    assert ok(client.get(f"{API}/portal/me/complaints", headers=w.st2.headers())) == []
    problem(
        client.post(f"{API}/portal/me/complaints/{c['id']}/reopen", headers=w.st2.headers(), json={"comment": "x" * 5}),
        404,
    )
    ok(client.get(f"{API}/admin/complaints/{c['id']}", headers=w.warden2.headers()))
    ok(client.get(f"{API}/admin/complaints/{c['id']}", headers=w.accountant.headers()))
    assert [x["id"] for x in ok(client.get(f"{API}/admin/complaints", headers=w.warden2.headers()))["items"]] == [
        c["id"]
    ]
    problem(client.get(f"{API}/admin/complaints/{c['id']}", headers=w.st2.headers()), 403, "wrong_portal")
    ok(client.get(f"{API}/files/{photo}/link", headers=w.st1.headers()))
    ok(client.get(f"{API}/files/{photo}/link", headers=w.warden.headers()))
    ok(client.get(f"{API}/files/{photo}/link", headers=w.warden2.headers()))
    problem(client.get(f"{API}/files/{photo}/link", headers=w.st2.headers()), 404)


def test_complaint_rules(client: TestClient, w: W) -> None:
    c = complain(client, w)
    url = f"{API}/admin/complaints/{c['id']}"
    problem(
        client.post(f"{url}/assign", headers=w.warden.headers(), json={"user_id": str(w.st2.user_id)}),
        422,
        "invalid_assignee",  # only an active Administrator can be given the work
    )
    other = ok(upload(client, w.st2, "complaint_attachment", jpeg_bytes()), 201)["file_id"]
    problem(
        client.post(
            f"{API}/portal/me/complaints",
            headers=w.st1.headers(),
            json={
                "category_id": w.category,
                "title": "Fan",
                "description": "Broken fan",
                "attachment_file_ids": [other],
            },
        ),
        422,
        "invalid_attachment",
    )
    ok(
        client.patch(
            f"{API}/admin/complaint-categories/{w.category}",
            headers=w.sa.headers(),
            json={"version": 1, "is_active": False},
        )
    )
    problem(
        client.post(
            f"{API}/portal/me/complaints",
            headers=w.st1.headers(),
            json={"category_id": w.category, "title": "Fan", "description": "Broken fan"},
        ),
        422,
        "invalid_category",
    )
    ok(client.post(f"{API}/admin/complaint-categories", headers=w.warden.headers(), json={"name": "Electrical"}), 201)
    with transaction(w.org.id) as db:
        target = db.execute(
            text("SELECT target_date - created_at::date FROM complaints WHERE id = :c"), {"c": c["id"]}
        ).scalar()
    assert target in (6, 7, 8)  # medium: 7 days (local date vs UTC date may differ by one)


# --- mess ------------------------------------------------------------------------------------


def test_mess_menu_permissions_copy_and_portal(client: TestClient, w: W) -> None:
    """Q41 since D75: every Administrator edits every hostel's menu; Q42: per hostel with copy."""
    week = [{"menu_date": f"2026-11-{9 + d:02d}", "meal": "lunch", "items": f"Rice and dal {d}"} for d in range(7)]
    url1, url2 = f"{API}/admin/hostels/{w.h1['id']}/mess-menu", f"{API}/admin/hostels/{w.h2['id']}/mess-menu"
    saved = ok(client.post(f"{url1}/entries", headers=w.warden.headers(), json={"entries": week}))
    assert len(saved) == 7
    ok(client.post(f"{url1}/entries", headers=w.hm.headers(), json={"entries": [{**week[0], "items": "Biryani"}]}))
    problem(client.post(f"{url1}/entries", headers=w.st1.headers(), json={"entries": week}), 403, "wrong_portal")
    # Copy H1's week to H2 (a Super Admin can act in both hostels).
    ok(client.post(f"{url2}/entries", headers=w.warden2.headers(), json={"entries": [{**week[1], "items": "Keep me"}]}))
    copy = {"from_hostel_id": w.h1["id"], "from_week_start": "2026-11-09", "to_week_start": "2026-11-09"}
    r = ok(client.post(f"{url2}/copy", headers=w.sa.headers(), json=copy), 201)
    assert r == {"copied": 6, "skipped": 1}  # existing slot kept without overwrite
    menu2 = ok(client.get(f"{url2}?start=2026-11-09&end=2026-11-15", headers=w.warden2.headers()))
    assert {m["menu_date"]: m["items"] for m in menu2}["2026-11-10"] == "Keep me"
    r = ok(client.post(f"{url2}/copy", headers=w.sa.headers(), json={**copy, "overwrite": True}), 201)
    assert r == {"copied": 7, "skipped": 0}
    assert ok(client.post(f"{url2}/copy", headers=w.warden2.headers(), json=copy), 201) == {"copied": 0, "skipped": 7}
    problem(client.get(f"{API}/admin/hostels/{uuid.uuid4()}/mess-menu", headers=w.sa.headers()), 404)  # unknown hostel
    # Next week from this week, same hostel.
    r = ok(
        client.post(
            f"{url1}/copy",
            headers=w.warden.headers(),
            json={"from_week_start": "2026-11-09", "to_week_start": "2026-11-16"},
        ),
        201,
    )
    assert r["copied"] == 7
    mine = ok(client.get(f"{API}/portal/me/mess-menu?start=2026-11-09&end=2026-11-15", headers=w.st1.headers()))
    assert mine[0]["items"] == "Biryani" and len(mine) == 7
    assert (
        ok(client.get(f"{API}/portal/me/mess-menu?start=2026-11-09&end=2026-11-15", headers=w.st3.headers()))[1][
            "items"
        ]
        == "Rice and dal 1"
    )


def test_announcements_reach_the_right_people(client: TestClient, w: W) -> None:
    body = {"hostel_id": w.h1["id"], "title": "Water off on Sunday", "body": "From 9 to 12.", "audience": "students"}
    a = ok(client.post(f"{API}/admin/announcements", headers=w.warden.headers(), json=body), 201)
    drain()
    assert "Water off on Sunday" in titles(w, w.st1) and "Water off on Sunday" in titles(w, w.st2)
    assert "Water off on Sunday" not in titles(w, w.st3)  # other hostel
    assert "Water off on Sunday" not in titles(w, w.guard)  # staff not in the audience
    assert [x["id"] for x in ok(client.get(f"{API}/portal/me/announcements", headers=w.st1.headers()))] == [a["id"]]
    assert ok(client.get(f"{API}/portal/me/announcements", headers=w.st3.headers())) == []
    problem(client.post(f"{API}/admin/announcements", headers=w.st1.headers(), json=body), 403, "wrong_portal")
    org_wide = ok(
        client.post(
            f"{API}/admin/announcements",
            headers=w.sa.headers(),
            json={"title": "Holiday on Monday", "body": "Office closed.", "audience": "everyone"},
        ),
        201,
    )
    drain()
    for p in (w.st1, w.st3):  # the students; the Administrator posted it (D76)
        assert "Holiday on Monday" in titles(w, p)
    with transaction(w.org.id) as db:  # D84: each reader's own Announcements page, never no link
        links = set(
            db.execute(
                text(
                    "SELECT u.kind, n.link FROM notifications n JOIN users u ON u.id = n.user_id "
                    "WHERE n.title = 'Holiday on Monday'"
                )
            ).all()
        )
    assert ("student", "/portal/announcements") in links
    assert links <= {("student", "/portal/announcements"), ("staff", "/admin/announcements")}
    assert {x["id"] for x in ok(client.get(f"{API}/admin/announcements", headers=w.warden2.headers()))} == {
        a["id"],
        org_wide["id"],
    }  # every Administrator sees every hostel's announcements (D75)
    with transaction(w.org.id) as db:
        db.execute(
            text(
                "UPDATE announcements SET published_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day' WHERE id = :a"
            ),
            {"a": a["id"]},
        )
    assert [x["id"] for x in ok(client.get(f"{API}/portal/me/announcements", headers=w.st1.headers()))] == [
        org_wide["id"]
    ]
    jobs_before = titles(w, w.st1).count("Holiday on Monday")
    with transaction(w.org.id) as db:  # rerunning the fan-out doesn't notify twice
        jobs.enqueue(db, "announcements.fanout", {"announcement_id": org_wide["id"]}, org_id=w.org.id)
    drain()
    assert titles(w, w.st1).count("Holiday on Monday") == jobs_before
