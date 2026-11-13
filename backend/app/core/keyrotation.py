"""Data-key rotation (docs/03 §5.5.1, docs/runbooks/keys.md; 05 CRY tests).

The data keyring is a JSON file outside the project: {"active": "<kid>", "keys": {"<kid>": b64}}.
Every encrypted item stores the key ID it was encrypted with, so rotation is three steps:

  1. add_key(): generate a new key, make it active, KEEP the old ones. From now on new items use
     the new key; old items still decrypt with theirs.
  2. reencrypt_all(): re-encrypt every item still under an old key ID (identity documents; the
     TOTP secrets that also used these keys went with D87).
       * A file is re-encrypted to a NEW storage key; the row (storage key + key ID) is switched
         in one transaction; only after the commit is the old object deleted. A crash at any
         point leaves the row pointing at bytes that match its key ID.
  3. retire_key(): remove an old key from the file, refused while anything still uses it.

The tool never prints key material; only key IDs.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from app.core import crypto
from app.core.config import KeySet, get_settings, load_data_keyring
from app.core.db import transaction
from app.core.storage import StorageError, get_storage


class RotationError(Exception):
    pass


def _write_keyring(path: Path, active: str, keys: dict[str, bytes]) -> None:
    """Atomic replace in the same folder, so a crash never leaves a half-written keyring."""
    body = json.dumps({"active": active, "keys": {k: base64.b64encode(v).decode() for k, v in keys.items()}}, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".keyring-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def add_key(path: str | Path) -> str:
    """Generate a new 32-byte key, make it active, keep the old ones. Returns the new key ID."""
    p = Path(path)
    ring = load_data_keyring(str(p))
    kid = f"k{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(2)}"
    while kid in ring.keys:
        kid = f"k{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(2)}"
    _write_keyring(p, kid, {**ring.keys, kid: secrets.token_bytes(32)})
    return kid


@dataclass
class RotationReport:
    files: int = 0
    deleted_old_objects: list[str] = field(default_factory=list)
    missing_files: list[uuid.UUID] = field(default_factory=list)  # row present, bytes gone: left as they are
    unreadable: list[uuid.UUID] = field(default_factory=list)  # files that don't decrypt: left as they are


def _org_ids() -> list[uuid.UUID]:
    with transaction(None, role="hms_worker") as db:
        return list(db.scalars(text("SELECT id FROM organizations ORDER BY id")))


def reencrypt_all(keys: KeySet | None = None) -> RotationReport:
    """Re-encrypt everything not under the active key. Safe to rerun.

    An item that can't be read (bytes missing, or it doesn't decrypt) is reported and skipped, so one
    damaged item never stops the rest; it keeps its old key ID, so retire_key() refuses that key."""
    ring = keys or get_settings().data_keyring()
    report = RotationReport()
    storage = get_storage()
    from app.modules.files.models import File
    from app.modules.files.service import file_aad

    for org_id in _org_ids():
        with transaction(org_id, role="hms_worker") as db:
            file_ids = list(
                db.scalars(
                    select(File.id).where(
                        File.encryption_key_id.is_not(None),
                        File.encryption_key_id != ring.active,
                        File.purged_at.is_(None),  # purged uploads have no bytes left to re-encrypt
                    )
                )
            )
        for file_id in file_ids:
            with transaction(org_id, role="hms_worker") as db:
                f = db.scalars(select(File).where(File.id == file_id).with_for_update()).one()
                if f.encryption_key_id in (None, ring.active):
                    continue  # someone else did it
                aad = file_aad(f.org_id, f.id, f.category)
                try:
                    stored = storage.open(f.storage_key)
                except StorageError:
                    # The row survives but its bytes don't (lost storage, a restore without the uploads).
                    # Re-encrypting the rest must not stop here; retire_key still refuses the old key.
                    report.missing_files.append(f.id)
                    continue
                try:
                    plain = crypto.decrypt(ring, f.encryption_key_id, stored, aad)
                except crypto.DecryptionError:
                    report.unreadable.append(f.id)  # tampered, or its key isn't in this keyring
                    continue
                kid, blob = crypto.encrypt(ring, plain, aad)
                now = datetime.now(UTC)
                new_key = f"{f.org_id}/{f.category}/{now:%Y}/{now:%m}/{uuid.uuid4()}"
                storage.save(new_key, blob)
                old_key = f.storage_key
                f.storage_key, f.encryption_key_id = new_key, kid
            storage.delete(old_key)  # only after the commit above
            report.files += 1
            report.deleted_old_objects.append(old_key)
    return report


def key_usage() -> dict[str, int]:
    """How many encrypted items use each key ID, across every organization and the platform.
    A key ID here that isn't in the keyring means unreadable items (03 §5.5.1)."""
    usage: dict[str, int] = {}
    for org_id in _org_ids():
        with transaction(org_id, role="hms_worker") as db:
            rows = db.execute(
                text(
                    "SELECT k, count(*) FROM ("
                    " SELECT encryption_key_id AS k FROM files"
                    "  WHERE encryption_key_id IS NOT NULL AND purged_at IS NULL) x GROUP BY k"  # purged: no bytes
                )
            ).all()
        for kid, n in rows:
            usage[kid] = usage.get(kid, 0) + n
    return usage


def items_using(kid: str) -> int:
    return key_usage().get(kid, 0)


def retire_key(path: str | Path, kid: str) -> None:
    p = Path(path)
    ring = load_data_keyring(str(p))
    if kid not in ring.keys:
        raise RotationError(f"key {kid!r} is not in the keyring")
    if kid == ring.active:
        raise RotationError("the active key can't be retired; add a new key first")
    in_use = items_using(kid)
    if in_use:
        raise RotationError(f"{in_use} item(s) still use key {kid!r}; run reencrypt-data first")
    _write_keyring(p, ring.active, {k: v for k, v in ring.keys.items() if k != kid})
