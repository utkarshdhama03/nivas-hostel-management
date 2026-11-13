"""The audit log in plain language (D89): sentences, categories, details, links and filters.

Presentation only: reading the log changes no stored row and writes nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import transaction
from app.core.models import AuditLog
from app.modules.audit import describe as plain
from tests.test_finance import W, client, ok, pay, problem, refund, w  # noqa: F401  (client and w are fixtures)
from tests.test_leave import _ask, _world

API = "/api/v1"
APP = Path(plain.__file__).resolve().parents[2]

# Codes built at runtime (f-strings or a variable), with every value they can take.
RUNTIME_CODES = {
    'f"ledger.{kind}_added"': {"ledger.charge_added", "ledger.fine_added"},
    'f"payment_submission.{sub.status}"': {"payment_submission.verified", "payment_submission.rejected"},
    "action": {"refund.self_approved_sole_administrator", "refund.approved", "refund.rejected"},
    "f\"organization.{'suspended' if to == 'suspended' else 'reactivated'}\"": {
        "organization.suspended",
        "organization.reactivated",
    },
}
_CALL = re.compile(r"(?:audit\.record|\b_audit)\(\s*[^,()]+,\s*[^,()]+,\s*(f?\"[^\"]*\"|f\"[^\n]*?\"|action)\s*,", re.S)


def recorded_codes() -> set[str]:
    """Every action code the application can write, read from its source."""
    found: set[str] = set()
    for f in APP.rglob("*.py"):
        for raw in _CALL.findall(f.read_text(encoding="utf-8")):
            if raw in RUNTIME_CODES:
                found |= RUNTIME_CODES[raw]
            else:
                assert not raw.startswith("f") and raw != "action", f"{f.name}: a new runtime code {raw}; add it above"
                found.add(raw.strip('"'))
    return found


def test_every_recorded_action_has_a_sentence_and_a_category() -> None:
    codes = recorded_codes()
    assert {"payment.recorded", "refund.paid", "student_request.approved", "mess_menu.saved"} <= codes  # the scan works
    assert sorted(codes - set(plain.SENTENCES)) == [], "codes without a sentence"
    assert sorted(c for c in codes if plain.category_of(c) == "other") == [], "codes without a category"


def test_unknown_codes_still_read_as_a_sentence() -> None:
    assert (
        plain.render(plain.fallback_sentence("foo.bar_baz"), {"actor": "Kavita Deshpande"})
        == "Kavita Deshpande: foo bar baz"
    )
    assert plain.category_of("foo.bar_baz") == "other"
    # a missing detail drops its [optional] part instead of leaving a gap
    assert (
        plain.render("{actor} approved a refund[ of {amount}]{for_student}", {"actor": "Kavita"})
        == "Kavita approved a refund"
    )


def test_money_and_dates_read_the_indian_way() -> None:
    assert plain.rupees("2500.00") == "₹2,500"
    assert plain.rupees("250000") == "₹2,50,000"
    assert plain.rupees("1234567.5") == "₹12,34,567.50"
    assert plain.rupees("-800") == "₹800"  # a waiver's sign is in the sentence ("waived")
    assert plain.day("2026-11-09") == "09-11-2026"


def _log(client: TestClient, w: W, query: str = "") -> list[dict[str, Any]]:  # noqa: F811
    return list(ok(client.get(f"{API}/admin/audit-logs?limit=200{query}", headers=w.sa.headers()))["items"])


def test_a_refund_reads_as_a_sentence_with_amount_student_and_link(client: TestClient, w: W) -> None:  # noqa: F811
    ok(pay(client, w, deposit="4000.00"), 201)
    mine = refund(client, w, w.sa, "deposit", "2500.00")
    ok(client.post(f"{API}/admin/refund-requests/{mine['id']}/approve", headers=w.sa.headers(), json={}))
    with transaction(w.org.id) as db:
        stored_before = db.execute(
            text("SELECT id, before_values, after_values, changed_fields FROM audit_logs ORDER BY id")
        ).all()
        count = len(stored_before)

    items = _log(client, w, "&category=refunds")
    assert [i["action"] for i in items] == ["refund.self_approved_sole_administrator", "refund.requested"]
    approved = items[0]
    assert approved["sentence"] == "Admin approved their own refund request of ₹2,500 for Ravi Kumar"
    assert approved["actor_name"] == "Admin" and approved["actor_kind"] == "administrator"
    assert approved["category"] == "refunds"
    assert approved["student_name"] == "Ravi Kumar"
    assert approved["link_to"] == f"/admin/students/{w.student['id']}" and approved["link_label"] == "Open student"
    assert {d["label"]: d["after"] for d in approved["details"]} == {"Status": "approved", "Self-approved": "Yes"}
    # the stored values are returned untouched next to the sentence
    assert approved["after_values"] == {"status": "approved", "self_approved": True}

    payment = next(i for i in _log(client, w, "&category=payments") if i["action"] == "payment.recorded")
    assert payment["sentence"] == "Admin recorded a cash payment of ₹4,000 from Ravi Kumar"
    assert payment["details"] == [] and payment["summary"] is None  # nothing kept, nothing shown

    with transaction(w.org.id) as db:  # reading wrote nothing and changed nothing
        after = db.execute(
            text("SELECT id, before_values, after_values, changed_fields FROM audit_logs ORDER BY id")
        ).all()
    assert len(after) == count and after == stored_before


def test_edits_show_only_what_changed_in_readable_form(client: TestClient, w: W) -> None:  # noqa: F811
    org = ok(client.get(f"{API}/admin/organization", headers=w.sa.headers()))
    ok(
        client.patch(
            f"{API}/admin/organization",
            headers=w.sa.headers(),
            json={"version": org["version"], "city": "Pune", "upi_id": "hostel@upi", "upi_payee_name": "Test Hostels"},
        )
    )
    item = next(i for i in _log(client, w, "&category=settings") if i["action"] == "organization.updated")
    assert item["sentence"] == "Admin changed the organization's details"
    assert {d["label"]: d["after"] for d in item["details"]} == {
        "City": "Pune",
        "UPI ID": "hostel@upi",
        "UPI payee name": "Test Hostels",
    }
    assert item["link_to"] == "/admin/organization"

    # unchanged fields are hidden; money in rupees, dates as DD-MM-YYYY, fields the log didn't keep named
    row = AuditLog(
        action="student.updated",
        entity_type="student",
        entity_id=None,
        actor_type="system",
        before_values={"city": "Pune", "deposit_held": "1000.00", "exited_on": None},
        after_values={"city": "Pune", "deposit_held": "250000.00", "exited_on": "2026-11-09"},
        changed_fields=["deposit_held", "exited_on", "program"],
    )
    d = plain.describe(row, plain.Lookups(rows={k: {} for k in ("bed", "room")}), ZoneInfo("Asia/Kolkata"))
    assert [(x["label"], x["before"], x["after"]) for x in d.details] == [
        ("Exit date", "—", "09-11-2026"),
        ("Deposit held", "₹1,000", "₹2,50,000"),
        ("Program", None, "not recorded"),
    ]
    assert d.summary == "Changed exit date, deposit held and program"
    assert d.actor_name == "System" and d.actor_kind == "system"
    assert d.sentence == "The system changed a student's details"


def test_filters_by_category_name_and_who(client: TestClient, app_db: str) -> None:  # noqa: F811
    org, a, st, other = _world()
    leave = _ask(client, st)
    _ask(client, other)
    ok(client.post(f"{API}/admin/leave-requests/{leave['id']}/approve", headers=a.headers(), json={}))

    def log(query: str) -> list[dict[str, Any]]:
        return list(ok(client.get(f"{API}/admin/audit-logs?limit=200{query}", headers=a.headers()))["items"])

    everything = log("")
    for category in plain.CATEGORIES:  # SQL and Python agree on every row's category
        assert {i["id"] for i in log(f"&category={category}")} == {
            i["id"] for i in everything if i["category"] == category
        }

    by_st = log("&q=STU-2026-0301")  # their own request and the approval about them; not the other student's
    assert {i["action"] for i in by_st} == {"leave.requested", "leave.approved"}
    assert all(i["student_name"] == "St" for i in by_st)
    assert by_st[0]["sentence"].startswith("Admin approved home leave from ") and by_st[0]["sentence"].endswith(
        " for St"
    )
    assert by_st[1]["sentence"].startswith("St requested home leave from ")
    by_name = log("&q=Other")  # by name: only the other student's request
    assert [(i["action"], i["actor_name"]) for i in by_name] == [("leave.requested", "Other")]

    students = log("&actor=students")
    assert len(students) == 2 and {i["actor_kind"] for i in students} == {"student"}
    assert {i["action"] for i in log(f"&actor={a.user_id}")} == {"leave.approved"}
    assert log("&actor=system") == []

    problem(client.get(f"{API}/admin/audit-logs?q=a", headers=a.headers()), 422, "validation_error")
    problem(client.get(f"{API}/admin/audit-logs?actor=nobody", headers=a.headers()), 422, "validation_error")
    problem(client.get(f"{API}/admin/audit-logs?category=gate", headers=a.headers()), 422, "validation_error")
    assert ok(client.get(f"{API}/admin/audit-logs?q=%25_", headers=a.headers()))["items"] == []  # wildcards are literal


def test_students_cannot_read_the_audit_log(client: TestClient, app_db: str) -> None:  # noqa: F811
    _org, _a, st, _other = _world()
    r = client.get(f"{API}/admin/audit-logs", headers=st.headers())
    assert r.status_code == 403
