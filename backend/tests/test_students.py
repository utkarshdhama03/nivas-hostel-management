"""Phase 3: uploads, identity documents, students, portal (docs/04 §11, §12.7; 05 DOC-1..10,
MT-13, RBAC-3, RBAC-8, AUTH-9; Q19, Q22, Q39, Q46, Q62, Q65, Q66)."""

from __future__ import annotations

import io
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, text, update

from app.core import crypto, jobs
from app.core.config import KeySet, get_settings
from app.core.db import transaction
from app.core.storage import get_storage
from app.main import create_app
from app.modules.files import service as files
from app.modules.files.models import File
from app.modules.identity.models import User, UserSession
from tests.support import (
    TEST_PASSWORD,
    Org,
    Person,
    activation_token,
    add_person,
    admit,
    jpeg_bytes,
    make_hostel,
    make_org,
    pdf_bytes,
    png_bytes,
    upload,
)

Response = httpx.Response


@dataclass
class W:
    org: Org
    sa: Person
    # D75: all of these are Administrators (one role). The names only tell the tests apart.
    hm: Person
    hm2: Person
    warden: Person
    accountant: Person
    guard: Person
    h1: dict[str, Any]
    h2: dict[str, Any]


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


@pytest.fixture
def w(client: TestClient, app_db: str) -> W:
    org = make_org()
    sa = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    h1, h2 = make_hostel(client, sa, "H1"), make_hostel(client, sa, "H2", "girls")
    session = client.post(
        "/api/v1/admin/academic-sessions",
        headers=sa.headers(),
        json={"name": "2026-27", "starts_on": "2026-06-01", "ends_on": "2027-05-31"},
    ).json()
    client.post(f"/api/v1/admin/academic-sessions/{session['id']}/make-current", headers=sa.headers())

    def staff(key: str, code: str) -> Person:
        return sa  # D76: one Administrator; the names say what each step stands for

    return W(
        org,
        sa,
        staff("hm", "E-0002"),
        staff("hm2", "E-0003"),
        staff("warden", "E-0004"),
        staff("acc", "E-0005"),
        staff("guard", "E-0006"),
        h1,
        h2,
    )


def ok(r: Response, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


def problem(r: Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    assert body["code"] == code, body
    return body


def fetch_link(client: TestClient, who: Person, file_id: str) -> Response:
    link = ok(client.get(f"/api/v1/files/{file_id}/link", headers=who.headers()))
    return client.get(link["url"])


# --- the upload pipeline (DOC-1, DOC-2, DOC-3) -----------------------------------------------------


def test_images_reencoded_and_exif_stripped(client: TestClient, w: W) -> None:
    original = jpeg_bytes()
    assert b"SecretCameraMaker" in original
    f = ok(upload(client, w.hm, "student_photo", original, "me.jpg"), 201)
    assert f["mime_type"] == "image/jpeg"
    with transaction(w.org.id) as db:
        row = db.get(File, uuid.UUID(f["file_id"]))
        assert row is not None
        stored = get_storage().open(row.storage_key)
    assert b"SecretCameraMaker" not in stored and stored != original
    with Image.open(io.BytesIO(stored)) as img:
        assert img.size == (40, 30) and not img.getexif()


@pytest.mark.parametrize(
    ("name", "data", "category"),
    [
        ("html_as_jpg", b"<html><script>alert(1)</script></html>", "student_photo"),
        ("svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "admission_document"),
        ("exe_as_pdf", b"MZ\x90\x00\x03" + os.urandom(200), "admission_document"),
        ("pdf_as_photo", None, "student_photo"),
        ("jpeg_header_garbage", b"\xff\xd8\xff\xe0" + os.urandom(300), "student_photo"),
    ],
)
def test_upload_rejects_disguised_types(client: TestClient, w: W, name: str, data: bytes | None, category: str) -> None:
    problem(
        upload(client, w.hm, category, data if data is not None else pdf_bytes(), f"{name}.jpg"),
        415,
        "unsupported_file_type",
    )


def test_fake_pdf_without_header_or_trailer_rejected(client: TestClient, w: W) -> None:
    problem(upload(client, w.hm, "admission_document", b"%PDF-1.4\n no trailer here"), 415, "unsupported_file_type")
    problem(upload(client, w.hm, "admission_document", b"just text\n%%EOF"), 415, "unsupported_file_type")
    ok(upload(client, w.hm, "admission_document", pdf_bytes()), 201)


def test_upload_over_limit_returns_413(client: TestClient, w: W) -> None:
    big = b"\xff\xd8\xff" + b"\x00" * (5 * 1024 * 1024)
    problem(upload(client, w.hm, "student_photo", big), 413, "file_too_large")


def test_db_rejects_bad_file_rows(w: W) -> None:
    """The database is the backstop: size, type, key format and encryption are CHECKed."""
    base = {"o": w.org.id, "u": w.hm.user_id, "sha": b"\x00" * 32}
    bad = [
        (
            "files_size_bytes_check",
            "admission_document",
            f"{w.org.id}/admission_document/2026/11/{uuid.uuid4()}",
            "application/pdf",
            11 * 1024 * 1024,
            None,
        ),
        ("files_storage_key_check", "admission_document", f"{w.org.id}/../../etc/passwd", "application/pdf", 10, None),
        (
            "files_mime_type_check",
            "admission_document",
            f"{w.org.id}/admission_document/2026/11/{uuid.uuid4()}",
            "text/html",
            10,
            None,
        ),
        (
            "files_check2",
            "identity_document",
            f"{w.org.id}/identity_document/2026/11/{uuid.uuid4()}",
            "application/pdf",
            10,
            None,
        ),  # an identity document must be encrypted (key ID required)
    ]
    for constraint, category, key, mime, size, key_id in bad:
        with pytest.raises(Exception, match=constraint), transaction(w.org.id) as db:
            db.execute(
                text(
                    "INSERT INTO files (id, org_id, category, storage_key, original_name, mime_type, "
                    "size_bytes, sha256, uploaded_by, encryption_key_id) VALUES (:id, :o, :c, :k, 'x', :m, "
                    ":s, :sha, :u, :kid)"
                ),
                {**base, "id": uuid.uuid4(), "c": category, "k": key, "m": mime, "s": size, "kid": key_id},
            )


def test_malicious_original_name_sanitized(client: TestClient, w: W) -> None:
    f = ok(upload(client, w.hm, "admission_document", pdf_bytes(), '../../etc/pa"ss\r\nX-Evil: 1.pdf'), 201)
    with transaction(w.org.id) as db:
        row = db.get(File, uuid.UUID(f["file_id"]))
        assert row is not None and "/" not in row.original_name and "\n" not in row.original_name
        assert row.storage_key.startswith(f"{w.org.id}/admission_document/")
    r = fetch_link(client, w.hm, f["file_id"])
    assert r.status_code == 200 and "\n" not in r.headers["content-disposition"]
    assert r.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")


def test_who_may_upload_what(client: TestClient, w: W) -> None:
    ok(upload(client, w.warden, "student_photo", jpeg_bytes()), 201)  # any Administrator (D75)
    problem(upload(client, w.hm, "payment_proof", png_bytes()), 403, "permission_denied")  # students send those


# --- admission, activation, portal --------------------------------------------------------------------


def test_admission_activation_and_portal(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"], full_name="Asha Nair", gender="male")
    assert s["student_code"] == "STU-2026-0001" and s["guardians"][0]["is_primary"]
    second = admit(client, w.hm, w.h1["id"])
    assert second["student_code"] == "STU-2026-0002"
    link = {"activation_url": "#token=" + activation_token(client, w.hm.headers(), s)}
    token = link["activation_url"].split("#token=")[1]
    ok(
        client.post(
            f"/api/v1/o/{w.org.slug}/auth/activate", json={"token": token, "password": "my first hostel passphrase"}
        ),
        204,
    )
    login = ok(
        client.post(
            f"/api/v1/o/{w.org.slug}/auth/login",
            json={"portal": "student", "identifier": "stu-2026-0001", "password": "my first hostel passphrase"},
        )
    )
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    profile = ok(client.get("/api/v1/portal/me/profile", headers=headers))
    assert profile["student_code"] == "STU-2026-0001" and profile["guardians"][0]["full_name"] == "Sunil Kumar"
    # Q46: phone and emergency contact are editable by the student; nothing else is.
    me = ok(client.get("/api/v1/me", headers=headers))
    ok(client.patch("/api/v1/me", headers=headers, json={"version": me["version"], "phone": "+91 99999 11111"}))
    problem(
        client.patch("/api/v1/me", headers=headers, json={"version": me["version"] + 1, "full_name": "X"}),
        422,
        "validation_error",
    )
    updated = ok(
        client.post(
            "/api/v1/portal/me/emergency-contact",
            headers=headers,
            json={"full_name": "Meena Nair", "relationship": "mother", "phone": "+91 98765 00002"},
        )
    )
    primaries = [g for g in updated["guardians"] if g["is_primary"]]
    assert [g["full_name"] for g in primaries] == ["Meena Nair"]
    # The student's own photo opens; the portal has no student IDs in its URLs (RBAC-3).
    assert (
        fetch_link(
            client,
            Person(w.org.id, uuid.UUID(s["user_id"]), uuid.UUID(_sid(login)), "", "student"),
            profile["photo_file_id"],
        ).status_code
        == 200
    )
    problem(client.get("/api/v1/admin/students", headers=headers), 403, "wrong_portal")


def _sid(login: dict[str, Any]) -> str:
    import jwt

    return str(jwt.decode(login["access_token"], options={"verify_signature": False})["sid"])


def test_admission_rules(client: TestClient, w: W) -> None:
    photo = ok(upload(client, w.hm, "student_photo", jpeg_bytes()), 201)["file_id"]
    base = {
        "full_name": "X Y",
        "email": f"x.{uuid.uuid4().hex[:8]}@example.com",  # D80: required
        "hostel_id": w.h1["id"],
        "gender": "female",
        "date_of_birth": "2007-01-01",
        "photo_file_id": photo,
        "admission_date": "2026-06-15",
        "send_activation_email": False,
    }
    problem(
        client.post("/api/v1/admin/students", headers=w.hm.headers(), json=base), 422, "gender_hostel_mismatch"
    )  # Q19: H1 is a boys' hostel
    doc = ok(upload(client, w.hm, "admission_document", pdf_bytes()), 201)["file_id"]
    problem(
        client.post(
            "/api/v1/admin/students", headers=w.hm.headers(), json={**base, "gender": "male", "photo_file_id": doc}
        ),
        422,
        "validation_error",
    )  # the photo must be a student photo
    ok(client.post("/api/v1/admin/students", headers=w.hm.headers(), json={**base, "gender": "male"}), 201)
    again = {**base, "gender": "male", "email": f"x2.{uuid.uuid4().hex[:8]}@example.com"}
    problem(
        client.post("/api/v1/admin/students", headers=w.hm.headers(), json=again),
        422,
        "validation_error",
    )  # the same photo can't be claimed twice


def test_student_response_fields_by_role(client: TestClient, w: W) -> None:
    """RBAC-8 since D75: every Administrator sees the full record in every hostel; students see none."""
    s = admit(client, w.hm, w.h1["id"], address_line1="12 Lake Road", email="ravi.k@example.com")
    for who in (w.warden, w.accountant, w.guard, w.hm2):
        full = ok(client.get(f"/api/v1/admin/students/{s['id']}", headers=who.headers()))
        assert "guardians" in full and full["address_line1"] == "12 Lake Road" and "date_of_birth" in full
    ok(client.get(f"/api/v1/admin/students/{s['id']}/guardians", headers=w.accountant.headers()))
    assert [x["id"] for x in ok(client.get("/api/v1/admin/students", headers=w.hm2.headers()))["items"]] == [s["id"]]
    other = add_person(w.org, "otherstudent", kind="student", code="STU-2026-0901")
    problem(client.get(f"/api/v1/admin/students/{s['id']}", headers=other.headers()), 403, "wrong_portal")


def test_patch_student_version_conflict(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    ok(
        client.patch(
            f"/api/v1/admin/students/{s['id']}",
            headers=w.hm.headers(),
            json={"version": s["version"], "program": "B.Tech"},
        )
    )
    problem(
        client.patch(
            f"/api/v1/admin/students/{s['id']}", headers=w.sa.headers(), json={"version": s["version"], "city": "Pune"}
        ),
        409,
        "edit_conflict",
    )
    problem(
        client.patch(
            f"/api/v1/admin/students/{s['id']}",
            headers=w.hm.headers(),
            json={"version": s["version"] + 1, "status": "exited"},
        ),
        422,
        "validation_error",
    )


# --- identity documents (DOC-6, DOC-7, Q62, Q65) ------------------------------------------------------------


def add_aadhaar(client: TestClient, w: W, student_id: str) -> tuple[dict[str, Any], str]:
    f = ok(upload(client, w.hm, "identity_document", pdf_bytes("XXXXXXXX1234 masked")), 201)["file_id"]
    d = ok(
        client.post(
            f"/api/v1/admin/students/{student_id}/documents",
            headers=w.hm.headers(),
            json={
                "file_id": f,
                "document_type": "identity",
                "id_type": "aadhaar",
                "id_number_masked": "XXXXXXXX1234",
                "masked_aadhaar_attested": True,
            },
        ),
        201,
    )
    return d, f


def test_aadhaar_upload_requires_pdf_masked_number_and_attestation(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    url = f"/api/v1/admin/students/{s['id']}/documents"
    jpeg_id = ok(upload(client, w.hm, "identity_document", jpeg_bytes()), 201)["file_id"]
    pdf_id = ok(upload(client, w.hm, "identity_document", pdf_bytes()), 201)["file_id"]
    aadhaar = {
        "document_type": "identity",
        "id_type": "aadhaar",
        "id_number_masked": "XXXXXXXX1234",
        "masked_aadhaar_attested": True,
    }
    body = problem(
        client.post(url, headers=w.hm.headers(), json={**aadhaar, "file_id": jpeg_id}), 422, "validation_error"
    )
    assert body["errors"][0]["code"] == "aadhaar_pdf_required"
    problem(
        client.post(url, headers=w.hm.headers(), json={**aadhaar, "file_id": pdf_id, "id_number_masked": None}),
        422,
        "validation_error",
    )
    problem(
        client.post(url, headers=w.hm.headers(), json={**aadhaar, "file_id": pdf_id, "masked_aadhaar_attested": False}),
        422,
        "validation_error",
    )
    problem(
        client.post(
            url, headers=w.hm.headers(), json={**aadhaar, "file_id": pdf_id, "id_number_masked": "123456781234"}
        ),
        422,
        "validation_error",
    )  # only the last 4 characters may be stored
    adm = ok(upload(client, w.hm, "admission_document", pdf_bytes()), 201)["file_id"]
    problem(
        client.post(url, headers=w.hm.headers(), json={**aadhaar, "file_id": adm}), 422, "validation_error"
    )  # DOC-10: identity documents must be in the restricted category
    d = ok(client.post(url, headers=w.hm.headers(), json={**aadhaar, "file_id": pdf_id}), 201)
    assert d["id_number_masked"] == "XXXXXXXX1234" and not d["verified"]
    assert ok(client.post(f"{url}/{d['id']}/verify", headers=w.hm.headers()))["verified"]


def test_db_rejects_aadhaar_image_or_unattested(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    pdf_id = ok(upload(client, w.hm, "identity_document", pdf_bytes()), 201)["file_id"]
    with pytest.raises(Exception, match="student_documents_check"), transaction(w.org.id) as db:
        db.execute(
            text(
                "INSERT INTO student_documents (org_id, student_id, file_id, file_category, file_mime_type, "
                "document_type, id_type, id_number_masked, uploaded_by) VALUES (:o, :s, :f, "
                "'identity_document', 'application/pdf', 'identity', 'aadhaar', 'XXXXXXXX1234', :u)"
            ),
            {"o": w.org.id, "s": s["id"], "f": pdf_id, "u": w.hm.user_id},
        )


def test_identity_document_encrypted_at_rest(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    _, file_id = add_aadhaar(client, w, s["id"])
    with transaction(w.org.id) as db:
        row = db.get(File, uuid.UUID(file_id))
        assert row is not None
        on_disk = get_storage().open(row.storage_key)
        assert row.encryption_key_id == get_settings().data_keyring().active  # key ID recorded
        assert not on_disk.startswith(b"%PDF") and b"XXXXXXXX1234" not in on_disk
        assert files.read_content(row) == pdf_bytes("XXXXXXXX1234 masked")
        # The ciphertext is bound to its record: under another file's identity it doesn't decrypt.
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(
                get_settings().data_keyring(),
                row.encryption_key_id,
                on_disk,
                files.file_aad(row.org_id, uuid.uuid4(), row.category),
            )


def test_decrypt_with_old_key_id_after_new_active_key(
    client: TestClient, w: W, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = admit(client, w.hm, w.h1["id"])
    _, file_id = add_aadhaar(client, w, s["id"])
    old = get_settings().data_keyring()
    rotated = KeySet(active="2099-01-z", keys={"2099-01-z": os.urandom(32), **old.keys})
    monkeypatch.setattr(type(get_settings()), "data_keyring", lambda self: rotated)
    with transaction(w.org.id) as db:
        row = db.get(File, uuid.UUID(file_id))
        assert row is not None and files.read_content(row) == pdf_bytes("XXXXXXXX1234 masked")
    new = ok(upload(client, w.hm, "identity_document", pdf_bytes("new")), 201)["file_id"]
    with transaction(w.org.id) as db:
        assert db.get(File, uuid.UUID(new)).encryption_key_id == "2099-01-z"  # type: ignore[union-attr]


def test_identity_document_access_only_administrators(client: TestClient, w: W) -> None:
    """DOC-6 since D75: every Administrator may open it (always audited); a student never through this route."""
    s = admit(client, w.hm, w.h1["id"])
    d, file_id = add_aadhaar(client, w, s["id"])
    url = f"/api/v1/admin/students/{s['id']}/documents/{d['id']}/content"
    for who in (w.sa, w.hm):
        r = client.get(url, headers=who.headers())
        assert r.status_code == 200 and r.content == pdf_bytes("XXXXXXXX1234 masked")
        assert r.headers["cache-control"] == "private, no-store" and r.headers["x-content-type-options"] == "nosniff"
    other = add_person(w.org, "otherstudent", kind="student", code="STU-2026-0902")
    problem(client.get(url, headers=other.headers()), 403, "wrong_portal")
    problem(client.get(f"/api/v1/files/{file_id}/link", headers=w.sa.headers()), 404, "not_found")  # no links ever
    with transaction(w.org.id) as db:
        downloads = db.scalar(
            text("SELECT count(*) FROM audit_logs WHERE action = 'student_document.downloaded' AND entity_id = :s"),
            {"s": s["id"]},
        )
    assert downloads == 2  # every download audited


# --- signed links and file access (DOC-4, DOC-5, DOC-8, MT-13) --------------------------------------


def test_signed_links(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    link = ok(client.get(f"/api/v1/files/{s['photo_file_id']}/link", headers=w.warden.headers()))
    r = client.get(link["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-security-policy"] == "sandbox" and r.headers["x-content-type-options"] == "nosniff"
    parts = {k: v[0] for k, v in parse_qs(urlsplit(link["url"]).query).items()}
    tampered = link["url"].replace(parts["sig"], "0" * 64)
    problem(client.get(tampered), 404, "not_found")
    other_user = link["url"].replace(parts["u"], str(uuid.uuid4()))  # any other user
    problem(client.get(other_user), 404, "not_found")  # bound to the user
    with transaction(w.org.id) as db:  # bound to the session: sign out -> the link dies
        db.execute(
            update(UserSession)
            .where(UserSession.id == w.warden.session_id)
            .values(revoked_at=text("now()"), revoke_reason="logout")
        )
    problem(client.get(link["url"]), 404, "not_found")


def test_signed_link_expires(client: TestClient, w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    s = admit(client, w.hm, w.h1["id"])
    link = ok(client.get(f"/api/v1/files/{s['photo_file_id']}/link", headers=w.hm.headers()))
    real = time.time
    monkeypatch.setattr(files.time, "time", lambda: real() + files.LINK_TTL_SECONDS + 5)
    problem(client.get(link["url"]), 404, "not_found")


def test_photo_access_follows_the_student(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    for who in (w.sa, w.hm, w.hm2, w.warden, w.accountant, w.guard):  # every Administrator (D75)
        assert client.get(f"/api/v1/files/{s['photo_file_id']}/link", headers=who.headers()).status_code == 200
    other = add_person(w.org, "otherstudent", kind="student", code="STU-2026-0900")
    problem(client.get(f"/api/v1/files/{s['photo_file_id']}/link", headers=other.headers()), 404, "not_found")


def test_unattached_file_visible_only_to_uploader_and_purged(client: TestClient, w: W) -> None:
    f = ok(upload(client, w.hm, "admission_document", pdf_bytes()), 201)["file_id"]
    assert client.get(f"/api/v1/files/{f}/link", headers=w.hm.headers()).status_code == 200
    other = add_person(w.org, "otherstudent", kind="student", code="STU-2026-0903")  # D76: one Administrator
    problem(client.get(f"/api/v1/files/{f}/link", headers=other.headers()), 404, "not_found")
    attached = admit(client, w.hm, w.h1["id"])
    with transaction(w.org.id) as db:
        db.execute(text("UPDATE files SET created_at = now() - interval '25 hours'"))
        key = db.get(File, uuid.UUID(f)).storage_key  # type: ignore[union-attr]
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
        jobs.enqueue(db, "maintenance.purge", org_id=None)
    assert jobs.work_once("w") == "succeeded"
    with transaction(w.org.id) as db:
        purged = db.get(File, uuid.UUID(f))
        kept = db.get(File, uuid.UUID(attached["photo_file_id"]))
        assert purged is not None and purged.purged_at is not None and not get_storage().exists(key)
        assert kept is not None and kept.deleted_at is None and get_storage().exists(kept.storage_key)


def test_file_link_cross_tenant_404(client: TestClient, w: W, org_b: Org) -> None:
    s = admit(client, w.hm, w.h1["id"])
    problem(
        client.get(f"/api/v1/files/{s['photo_file_id']}/link", headers=org_b.people["admin"].headers()),
        404,
        "not_found",
    )


# --- exit (Q22) -------------------------------------------------------------------------------------------


def test_exit_makes_portal_read_only_then_deactivates(client: TestClient, w: W) -> None:
    s = admit(client, w.hm, w.h1["id"])
    link = {"activation_url": "#token=" + activation_token(client, w.hm.headers(), s)}
    ok(
        client.post(
            f"/api/v1/o/{w.org.slug}/auth/activate",
            json={"token": link["activation_url"].split("#token=")[1], "password": TEST_PASSWORD},
        ),
        204,
    )
    exited = ok(
        client.post(
            f"/api/v1/admin/students/{s['id']}/exit",
            headers=w.hm.headers(),
            json={"exited_on": "2026-11-01", "exit_reason": "graduated"},
        )
    )
    assert exited["status"] == "exited" and exited["user_status"] == "read_only"
    problem(
        client.post(
            f"/api/v1/admin/students/{s['id']}/exit",
            headers=w.hm.headers(),
            json={"exited_on": "2026-11-01", "exit_reason": "graduated"},
        ),
        409,
        "already_exited",
    )
    login = ok(
        client.post(
            f"/api/v1/o/{w.org.slug}/auth/login",
            json={"portal": "student", "identifier": s["student_code"], "password": TEST_PASSWORD},
        )
    )
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    assert ok(client.get("/api/v1/portal/me/profile", headers=headers))["read_only"] is True
    problem(
        client.post(
            "/api/v1/portal/me/emergency-contact",
            headers=headers,
            json={"full_name": "A", "relationship": "mother", "phone": "+91 98765 00002"},
        ),
        403,
        "account_read_only",
    )
    with transaction(w.org.id) as db:  # 90 days later
        db.execute(
            update(User).where(User.id == uuid.UUID(s["user_id"])).values(read_only_until=text("current_date - 1"))
        )
    with transaction(None, role="hms_worker") as db:
        db.execute(text("DELETE FROM jobs"))
        jobs.enqueue(db, "maintenance.purge", org_id=None)
    assert jobs.work_once("w") == "succeeded"
    problem(client.get("/api/v1/portal/me/profile", headers=headers), 401, "session_invalid")  # sessions ended
    with transaction(w.org.id) as db:
        status = db.scalar(select(User.status).where(User.id == uuid.UUID(s["user_id"])))
    assert status == "deactivated"
