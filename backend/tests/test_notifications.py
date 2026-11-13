"""Notifications: API (cross-user and cross-org isolation), preferences, and email delivery
through the worker to Mailpit (docs/02 §10, 04 §12.3)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.core import jobs
from app.core.db import transaction
from app.core.models import AuditLog, Job
from app.main import create_app
from app.modules.notifications import service
from app.modules.notifications.models import NOTIFICATION_LINKS
from tests.support import Org, Person, add_person

MAILPIT = "http://mailpit:8025/api/v1"


@pytest.fixture(scope="module")
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False))


@pytest.fixture
def empty_queue(app_db: str) -> Iterator[None]:
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
    yield


def _notify(p: Person, title: str = "Hello", category: str = "leave", **kw: Any) -> uuid.UUID:
    with transaction(p.org_id) as db:
        return service.notify(
            db, org_id=p.org_id, user_id=p.user_id, category=category, title=title, body="Body text", **kw
        ).id


# --- API ---------------------------------------------------------------------------------------------


def test_list_unread_and_mark_read(client: TestClient, org_a: Org) -> None:
    p = add_person(org_a, "reader", kind="student")
    ids = [_notify(p, f"n{i}", send_email=False) for i in range(3)]
    assert client.get("/api/v1/me/notifications/unread-count", headers=p.headers()).json() == {"unread": 3}

    page = client.get("/api/v1/me/notifications?limit=2", headers=p.headers()).json()
    assert [i["title"] for i in page["items"]] == ["n2", "n1"]  # newest first
    rest = client.get(f"/api/v1/me/notifications?limit=2&cursor={page['next_cursor']}", headers=p.headers()).json()
    assert [i["title"] for i in rest["items"]] == ["n0"] and rest["next_cursor"] is None

    r = client.post(f"/api/v1/me/notifications/{ids[0]}/read", headers=p.headers())
    assert r.status_code == 200 and r.json()["read_at"] is not None
    assert client.get("/api/v1/me/notifications/unread-count", headers=p.headers()).json() == {"unread": 2}
    unread = client.get("/api/v1/me/notifications?unread_only=true", headers=p.headers()).json()
    assert {i["title"] for i in unread["items"]} == {"n1", "n2"}
    assert client.post("/api/v1/me/notifications/read-all", headers=p.headers()).json() == {"unread": 0}
    assert client.get("/api/v1/me/notifications/unread-count", headers=p.headers()).json() == {"unread": 0}


def test_other_users_notifications_are_invisible(client: TestClient, org_a: Org, org_b: Org) -> None:
    """Same organization, another user -> 404 and not listed; another organization -> 404 too."""
    me = add_person(org_a, "me", kind="student")
    colleague = add_person(org_a, "colleague", kind="student")
    stranger = org_b.people["student"]
    theirs = _notify(colleague, "colleague's", send_email=False)
    foreign = _notify(stranger, "other org's", send_email=False)

    titles = [i["title"] for i in client.get("/api/v1/me/notifications", headers=me.headers()).json()["items"]]
    assert "colleague's" not in titles and "other org's" not in titles
    for nid in (theirs, foreign, uuid.uuid4()):
        r = client.post(f"/api/v1/me/notifications/{nid}/read", headers=me.headers())
        assert r.status_code == 404 and r.json()["code"] == "not_found"
    client.post("/api/v1/me/notifications/read-all", headers=me.headers())
    assert client.get("/api/v1/me/notifications/unread-count", headers=colleague.headers()).json() == {"unread": 1}


def test_unread_count_is_the_true_count_not_a_page(client: TestClient, org_a: Org) -> None:
    """The badge's number (D84): every unread notification, however many, not the size of one page."""
    p = add_person(org_a, "many", kind="student")
    with transaction(p.org_id) as db:
        for i in range(130):
            service.notify(
                db, org_id=p.org_id, user_id=p.user_id, category="fees", title=f"n{i}", body="b", send_email=False
            )
    assert client.get("/api/v1/me/notifications/unread-count", headers=p.headers()).json() == {"unread": 130}
    page = client.get("/api/v1/me/notifications?limit=20", headers=p.headers()).json()
    assert len(page["items"]) == 20 and page["next_cursor"] is not None  # a page is a page, the count is not


def test_notifications_link_only_to_pages_that_exist(org_a: Org) -> None:
    """D84: a link is one of the known pages or nothing; the room view (D79), attendance and gate pages
    (D75) are gone, and so is the 'allocation' category. The application and the database both refuse."""
    p = add_person(org_a, "links", kind="student")
    for gone in ("/portal/room", "/portal/me/room", "/portal/attendance", "/portal/gate-passes", "/portal/qr"):
        with pytest.raises(ValueError, match="not a known page"), transaction(p.org_id) as db:
            service.notify(db, org_id=p.org_id, user_id=p.user_id, category="leave", title="t", body="b", link=gone)
    insert = text(
        "INSERT INTO notifications (org_id, user_id, category, title, body, link) VALUES (:o, :u, :c, 't', 'b', :l)"
    )
    with pytest.raises(Exception, match="notifications_link_known"), transaction(p.org_id) as db:
        db.execute(insert, {"o": p.org_id, "u": p.user_id, "c": "leave", "l": "/portal/room"})
    with pytest.raises(Exception, match="notifications_category_check"), transaction(p.org_id) as db:
        db.execute(insert, {"o": p.org_id, "u": p.user_id, "c": "allocation", "l": None})
    with transaction(p.org_id) as db:  # every known page, and no link, are accepted
        for link in (*NOTIFICATION_LINKS, None):
            db.execute(insert, {"o": p.org_id, "u": p.user_id, "c": "leave", "l": link})


def test_bad_cursor(client: TestClient, org_a: Org) -> None:
    r = client.get("/api/v1/me/notifications?cursor=%%%", headers=org_a.people["student"].headers())
    assert r.status_code == 400 and r.json()["code"] == "invalid_cursor"


def test_preferences(client: TestClient, org_a: Org) -> None:
    p = add_person(org_a, "prefs", kind="student")
    url = "/api/v1/me/notification-preferences"
    assert client.get(url, headers=p.headers()).json() == {
        "preferences": [
            {"category": "announcements", "email_enabled": True},
            {"category": "reminders", "email_enabled": True},
        ]
    }
    body = {"preferences": [{"category": "reminders", "email_enabled": False}]}
    r = client.patch(url, json=body, headers=p.headers())
    assert r.status_code == 200
    assert {"category": "reminders", "email_enabled": False} in r.json()["preferences"]

    with transaction(org_a.id) as db:
        audited = db.scalars(select(AuditLog).where(AuditLog.actor_user_id == p.user_id)).all()
    assert [a.action for a in audited] == ["notification_preference.updated"]

    # Security emails can't be muted; unknown fields are rejected (no mass assignment).
    for bad in (
        {"preferences": [{"category": "security", "email_enabled": False}]},
        {"preferences": [], "user_id": str(uuid.uuid4())},
    ):
        r = client.patch(url, json=bad, headers=p.headers())
        assert r.status_code == 422, r.text


def test_requires_login(client: TestClient) -> None:
    assert client.get("/api/v1/me/notifications").status_code == 401


# --- email via the worker -----------------------------------------------------------------------


def _mailpit_messages(address: str) -> list[dict[str, Any]]:
    r = httpx.get(f"{MAILPIT}/search", params={"query": f'to:"{address}"'}, timeout=5)
    r.raise_for_status()
    messages: list[dict[str, Any]] = r.json()["messages"]
    return messages


def test_notification_email_reaches_mailpit(org_a: Org, empty_queue: None) -> None:
    p = add_person(org_a, "mailee", kind="student")
    _notify(p, "Leave approved <b>now</b>", link="/portal/leave")
    with transaction(None, role="hms_worker") as db:
        payload = db.scalars(select(Job.payload).where(Job.kind == "email.notification")).one()
    assert set(payload) == {"notification_id"}  # an ID only: no address, no text

    with transaction(None, role="hms_worker") as db:
        job_id = db.scalars(select(Job.id).where(Job.kind == "email.notification")).one()
    assert jobs.work_once("w1") == "succeeded"
    messages = _mailpit_messages(p.email)
    assert messages[0]["MessageID"] == f"job-{job_id}@hms.local"  # stable per job: duplicates identifiable
    assert len(messages) == 1 and messages[0]["Subject"] == "Leave approved <b>now</b>"
    full = httpx.get(f"{MAILPIT}/message/{messages[0]['ID']}", timeout=5).json()
    assert "Leave approved &lt;b&gt;now&lt;/b&gt;" in full["HTML"]  # escaped in HTML
    assert "http://localhost:5173/portal/leave" in full["Text"]


def test_muted_category_sends_no_email(client: TestClient, org_a: Org, empty_queue: None) -> None:
    p = add_person(org_a, "muted", kind="student")
    client.patch(
        "/api/v1/me/notification-preferences",
        headers=p.headers(),
        json={"preferences": [{"category": "announcements", "email_enabled": False}]},
    )
    _notify(p, "Water cut tomorrow", category="announcements")
    _notify(p, "Password changed", category="security")  # can't be muted
    with transaction(None, role="hms_worker") as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "email.notification")) == 1


def test_email_priority_by_category(org_a: Org, empty_queue: None) -> None:
    p = org_a.people["student"]
    _notify(p, "security", category="security")
    _notify(p, "reminder", category="reminders")
    _notify(p, "fees", category="fees")
    with transaction(None, role="hms_worker") as db:
        priorities = sorted(db.scalars(select(Job.priority)).all())
    assert priorities == [0, 1, 2]


def test_daily_cap_postpones_bulk_but_not_security(
    org_a: Org, empty_queue: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hms_email_daily_cap", 0)
    p = add_person(org_a, "capped", kind="student")
    _notify(p, "bulk", category="reminders")
    _notify(p, "urgent", category="security")
    outcomes = sorted(o for o in (jobs.work_once("w1"), jobs.work_once("w1")) if o)
    assert outcomes == ["postponed", "succeeded"]
    assert [m["Subject"] for m in _mailpit_messages(p.email)] == ["urgent"]
