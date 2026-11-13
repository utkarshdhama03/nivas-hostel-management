"""Phase 10: data-key rotation and keyring recovery (docs/03 §5.5.1, docs/runbooks/keys.md).

Every test works on a COPY of the keyring in a temporary folder, never the real file. Because
re-encryption covers the whole test database, each test that re-encrypts rotates back to the
original key at the end, so later tests can still decrypt everything."""

from __future__ import annotations

import base64
import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import cli
from app.core import keyrotation
from app.core.config import get_settings, load_data_keyring
from app.core.db import transaction
from app.core.storage import get_storage
from app.main import create_app
from app.modules.files.service import purge_unattached
from tests.support import Person, add_person, admit, make_hostel, make_org, pdf_bytes, upload

API = "/api/v1"


@pytest.fixture
def client(app_db: str) -> TestClient:
    return TestClient(create_app(run_checks=False), base_url="https://testserver")


@pytest.fixture
def keyring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A copy of the real keyring that the app uses for the duration of the test."""
    copy = tmp_path / "data-keys.json"
    shutil.copy(get_settings().hms_data_keys_file, copy)
    monkeypatch.setattr(get_settings(), "hms_data_keys_file", str(copy))
    yield copy


def _document(client: TestClient) -> tuple[Person, str, str, bytes]:
    org = make_org()
    sa = add_person(org, "admin", roles=("super_admin",), code="E-0001")
    ok = client.post(
        f"{API}/admin/academic-sessions",
        headers=sa.headers(),
        json={"name": "2026-27", "starts_on": "2026-06-01", "ends_on": "2027-05-31"},
    ).json()
    client.post(f"{API}/admin/academic-sessions/{ok['id']}/make-current", headers=sa.headers())
    h = make_hostel(client, sa, "H1")
    st = admit(client, sa, h["id"])
    data = pdf_bytes(f"masked aadhaar {uuid.uuid4().hex}")
    fid = upload(client, sa, "identity_document", data).json()["file_id"]
    doc = client.post(
        f"{API}/admin/students/{st['id']}/documents",
        headers=sa.headers(),
        json={"file_id": fid, "document_type": "identity", "id_type": "pan", "id_number_masked": "XXXXXX123A"},
    )
    assert doc.status_code == 201, doc.text
    return sa, f"{API}/admin/students/{st['id']}/documents/{doc.json()['id']}/content", fid, data


def _file(org_id: uuid.UUID, fid: str) -> Any:
    with transaction(org_id) as db:
        return db.execute(text("SELECT storage_key, encryption_key_id FROM files WHERE id = :f"), {"f": fid}).one()


def _status_line(out: str, kid: str) -> str:
    return next(line for line in out.splitlines() if line.startswith(f"{kid}: "))


def _ids_under(kid: str) -> set[uuid.UUID]:
    """Every file that still uses this key (purged files have no bytes and don't). D87: no TOTP secrets."""
    ids: set[uuid.UUID] = set()
    for org_id in keyrotation._org_ids():
        with transaction(org_id, role="hms_worker") as db:
            ids |= set(
                db.scalars(
                    text("SELECT id FROM files WHERE encryption_key_id = :k AND purged_at IS NULL"),
                    {"k": kid},
                )
            )
    return ids


def _rotate_back(path: Path, original: str) -> None:
    ring = load_data_keyring(str(path))
    keyrotation._write_keyring(path, original, ring.keys)
    keyrotation.reencrypt_all()
    assert all(keyrotation.items_using(k) == 0 for k in ring.keys if k != original)


def test_decrypt_with_old_key_id_after_new_active_key(client: TestClient, keyring: Path) -> None:
    sa, url, fid, data = _document(client)
    old = load_data_keyring(str(keyring)).active
    new = keyrotation.add_key(keyring)
    try:
        assert new != old and load_data_keyring(str(keyring)).active == new
        assert set(load_data_keyring(str(keyring)).keys) >= {old, new}  # the old key is kept
        r = client.get(url, headers=sa.headers())
        assert r.status_code == 200 and r.content == data  # still under the old key, still readable
        assert _file(sa.org_id, fid).encryption_key_id == old
        fid2 = upload(client, sa, "identity_document", pdf_bytes("second")).json()["file_id"]
        assert _file(sa.org_id, fid2).encryption_key_id == new  # new items use the new key
    finally:
        _rotate_back(keyring, old)  # the second file must not stay under a key that leaves with the test


def test_rotation_keeps_every_item_readable(client: TestClient, keyring: Path) -> None:
    sa, url, fid, data = _document(client)
    old = load_data_keyring(str(keyring)).active
    old_object = _file(sa.org_id, fid).storage_key
    new = keyrotation.add_key(keyring)
    with pytest.raises(keyrotation.RotationError, match="still use"):
        keyrotation.retire_key(keyring, old)
    try:
        report = keyrotation.reencrypt_all()
        assert report.files >= 1
        row = _file(sa.org_id, fid)
        assert row.encryption_key_id == new and row.storage_key != old_object
        assert not get_storage().exists(old_object)  # the old ciphertext is gone
        r = client.get(url, headers=sa.headers())
        assert r.status_code == 200 and r.content == data
        assert keyrotation.items_using(old) == 0
        assert keyrotation.reencrypt_all().files == 0  # rerun: nothing left to do
        retired = keyring.parent / "retired.json"
        shutil.copy(keyring, retired)
        keyrotation.retire_key(retired, old)
        assert set(json.loads(retired.read_text())["keys"]) == {new}
        with pytest.raises(keyrotation.RotationError, match="active"):
            keyrotation.retire_key(retired, new)
    finally:
        _rotate_back(keyring, old)


def test_document_opens_with_keyring_restored_from_copy(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drill 2 in code: a fresh environment with the keyring restored from the offline copy opens
    an identity document; with any other keyring it can't."""
    sa, url, _, data = _document(client)
    offline = tmp_path / "usb" / "data-keys.json"
    offline.parent.mkdir()
    shutil.copy(get_settings().hms_data_keys_file, offline)
    fresh = tmp_path / "fresh-env" / "data-keys.json"
    fresh.parent.mkdir()
    shutil.copy(offline, fresh)
    monkeypatch.setattr(get_settings(), "hms_data_keys_file", str(fresh))
    r = client.get(url, headers=sa.headers())
    assert r.status_code == 200 and r.content == data
    ring = load_data_keyring(str(fresh))
    wrong = tmp_path / "wrong.json"
    wrong.write_text(
        json.dumps({"active": ring.active, "keys": {ring.active: base64.b64encode(b"x" * 32).decode()}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "hms_data_keys_file", str(wrong))
    assert client.get(url, headers=sa.headers()).status_code >= 500


def test_key_status_flags_items_under_a_key_missing_from_the_keyring(
    client: TestClient, keyring: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _document(client)  # at least one identity document and one TOTP secret under the active key
    old = load_data_keyring(str(keyring)).active
    cli.main(["data-key-status"])  # exit code depends on other tests' data (the dbproof placeholder key)
    line = _status_line(capsys.readouterr().out, old)
    assert "(active)" in line and "MISSING" not in line and keyrotation.key_usage()[old] >= 2
    # A keyring that lost the old key (no reencrypt was done): the command must say so and fail.
    new = keyrotation.add_key(keyring)
    ring = load_data_keyring(str(keyring))
    keyrotation._write_keyring(keyring, new, {new: ring.keys[new]})
    assert cli.main(["data-key-status"]) == 1
    out = capsys.readouterr()
    assert "MISSING FROM KEYRING" in _status_line(out.out, old) and "keys.md" in out.err


def test_cli_prints_key_ids_never_key_material(keyring: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["add-data-key", "--keyring", str(keyring)]) == 0
    out = capsys.readouterr()
    ring = json.loads(keyring.read_text())
    assert ring["active"] in out.out
    for material in ring["keys"].values():
        assert material not in out.out and material not in out.err
    assert cli.main(["retire-data-key", "--keyring", str(keyring), "--kid", ring["active"]]) == 1


def test_purged_and_missing_files_do_not_block_rotation(client: TestClient, keyring: Path) -> None:
    """Found by the Phase 10 quality gate: an identity upload nobody attached is purged after 24 hours
    (bytes deleted, row kept with its key ID). Rotation must skip it, not crash on it; and a row whose
    bytes vanished some other way is reported, skipped, and keeps the old key from being retired."""
    sa, url, fid, data = _document(client)
    old = load_data_keyring(str(keyring)).active
    purged = upload(client, sa, "identity_document", pdf_bytes("abandoned upload")).json()["file_id"]
    with transaction(sa.org_id, role="hms_worker") as db:
        db.execute(text("UPDATE files SET created_at = now() - interval '2 days' WHERE id = :f"), {"f": purged})
        assert purge_unattached(db) >= 1
    lost = upload(client, sa, "identity_document", pdf_bytes("bytes lost")).json()["file_id"]
    get_storage().delete(_file(sa.org_id, lost).storage_key)
    # a file whose bytes no longer decrypt (tampered with on disk)
    damaged = upload(client, sa, "identity_document", pdf_bytes("tampered")).json()["file_id"]
    damaged_key = _file(sa.org_id, damaged).storage_key
    get_storage().delete(damaged_key)
    get_storage().save(damaged_key, b"\xab" * 64)
    assert keyrotation.key_usage().get(old, 0) >= 3  # the purged row no longer counts; fid, lost, damaged do
    new = keyrotation.add_key(keyring)
    try:
        report = keyrotation.reencrypt_all()
        assert uuid.UUID(lost) in report.missing_files and uuid.UUID(purged) not in report.missing_files
        assert uuid.UUID(damaged) in report.unreadable  # reported and skipped; the rest still rotated
        assert _file(sa.org_id, fid).encryption_key_id == new  # everything else moved on
        r = client.get(url, headers=sa.headers())
        assert r.status_code == 200 and r.content == data
        # Nothing readable stays behind: whatever still uses the old key was reported as skipped.
        left = _ids_under(old)
        assert {uuid.UUID(lost), uuid.UUID(damaged)} <= left <= set(report.missing_files) | set(report.unreadable)
        with pytest.raises(keyrotation.RotationError, match="still use"):
            keyrotation.retire_key(keyring, old)
    finally:
        with transaction(sa.org_id, role="hms_worker") as db:  # don't leave a bytes-less row for later tests
            for f in (lost, damaged):
                db.execute(text("UPDATE files SET deleted_at = now(), purged_at = now() WHERE id = :f"), {"f": f})
        _rotate_back(keyring, old)
