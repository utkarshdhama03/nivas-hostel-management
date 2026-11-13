"""Files and identity documents, notifications (docs/05 DOC-*, docs/03 §5.5-5.14).
The gate pass, QR and attendance proofs went with their tables (schema cleanup after D75)."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from .helpers import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    expect_db_error,
    ins,
    tenant_tx,
)
from .world import KEY_ID, World, add_file

pytestmark = pytest.mark.dbproof

# --- Files and identity documents (Q62, Q65, Q66) ----------------------------------------------


def test_storage_key_format_enforced_by_db(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "files",
            org_id=fresh.org,
            category="admission_document",
            storage_key="../../etc/passwd",
            original_name="x.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            sha256=hashlib.sha256(b"x").digest(),
            uploaded_by=fresh.hm,
        )


def test_db_rejects_unencrypted_identity_document(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    fid = uuid.uuid4()
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "files",
            id=fid,
            org_id=fresh.org,
            category="identity_document",
            storage_key=f"{fresh.org}/identity_document/2026/11/{fid}",
            original_name="id.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            sha256=hashlib.sha256(b"x").digest(),
            uploaded_by=fresh.hm,
            encryption_key_id=None,
        )


def test_identity_files_are_restricted(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("SELECT access_level, encryption_key_id FROM files WHERE id = %s", (fresh.id_file,))
        assert cur.fetchone() == ("restricted", KEY_ID)


def doc(cur: psycopg.Cursor[Any], w: World, file_id: uuid.UUID, mime: str, **extra: Any) -> None:
    values: dict[str, Any] = {
        "org_id": w.org,
        "student_id": w.s2,
        "file_id": file_id,
        "file_category": "identity_document",
        "file_mime_type": mime,
        "document_type": "identity",
        "id_type": "aadhaar",
        "id_number_masked": "XXXXXXXX9876",
        "masked_attested_by": w.hm,
        "masked_attested_at": datetime.now(UTC),
        "uploaded_by": w.hm,
    }
    values.update(extra)
    ins(cur, "student_documents", **values)


def test_db_rejects_aadhaar_image(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        image = add_file(cur, fresh.org, "identity_document", "image/jpeg", fresh.hm)  # a scanned card
        doc(cur, fresh, image, "image/jpeg")


def test_db_rejects_unattested_aadhaar(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        doc(cur, fresh, fresh.spare_id, "application/pdf", masked_attested_by=None, masked_attested_at=None)


def test_db_rejects_unmasked_aadhaar_number(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        doc(cur, fresh, fresh.spare_id, "application/pdf", id_number_masked="123412341234")


def test_document_cannot_misstate_file_type(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    """The copied file_mime_type must be the file's real type (composite FK to files)."""
    with expect_db_error(FOREIGN_KEY_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        image = add_file(cur, fresh.org, "identity_document", "image/jpeg", fresh.hm)
        doc(cur, fresh, image, "application/pdf")


def test_identity_document_type_forces_restricted_category(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        doc(cur, fresh, fresh.spare_adm, "application/pdf", file_category="admission_document")


def test_valid_masked_aadhaar_accepted(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        doc(cur, fresh, fresh.spare_id, "application/pdf")


# --- Notifications ------------------------------------------------------------


def test_notification_link_must_be_relative(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "notifications",
            org_id=fresh.org,
            user_id=fresh.stu1,
            category="fees",
            title="Pay now",
            body="Click",
            link="https://evil.example/login",
        )


def test_security_emails_cannot_be_muted(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(CHECK_VIOLATION), tenant_tx(app_conn, fresh.org) as cur:
        ins(
            cur,
            "notification_preferences",
            returning="user_id",
            org_id=fresh.org,
            user_id=fresh.stu1,
            category="security",
            email_enabled=False,
        )


def test_bed_states_view(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("SELECT bed_id, state FROM bed_states WHERE hostel_id = %s", (fresh.hostel,))
        states = dict(cur.fetchall())
    assert states[fresh.bed_A] == "occupied"
    assert states[fresh.bed_C] == "occupied"
    assert states[fresh.bed_D] == "vacant"
