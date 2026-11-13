"""AES-256-GCM with key IDs and associated data (Q65, docs/05 OPS-4)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from app.core import crypto
from app.core.config import ConfigError, KeySet, get_settings, load_data_keyring


def keyset(active: str = "k-new") -> KeySet:
    return KeySet(active=active, keys={active: os.urandom(32)})


def test_round_trip_and_nonce_is_random() -> None:
    ks = keyset()
    kid, a = crypto.encrypt(ks, b"secret", b"aad")
    _, b = crypto.encrypt(ks, b"secret", b"aad")
    assert kid == "k-new" and a != b  # same plaintext, different ciphertext
    assert crypto.decrypt(ks, kid, a, b"aad") == b"secret"


def test_wrong_associated_data_fails() -> None:
    """An encrypted item copied onto another record doesn't decrypt (the associated data differs)."""
    ks = keyset()
    kid, blob = crypto.encrypt(ks, b"secret", f"{uuid.uuid4()}|document".encode())
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(ks, kid, blob, f"{uuid.uuid4()}|document".encode())


def test_tampering_is_detected() -> None:
    ks = keyset()
    kid, blob = crypto.encrypt(ks, b"secret", b"aad")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(ks, kid, blob[:-1] + bytes([blob[-1] ^ 1]), b"aad")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(ks, kid, blob[:10], b"aad")


def test_rotation_old_data_still_decrypts() -> None:
    old = keyset("k-old")
    kid, blob = crypto.encrypt(old, b"secret", b"aad")
    rotated = KeySet(active="k-new", keys={"k-new": os.urandom(32), "k-old": old.keys["k-old"]})
    assert crypto.decrypt(rotated, kid, blob, b"aad") == b"secret"
    assert crypto.encrypt(rotated, b"x", b"aad")[0] == "k-new"
    with pytest.raises(crypto.DecryptionError, match="unknown key id"):
        crypto.decrypt(keyset("k-new"), kid, blob, b"aad")


def test_keyring_validation(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text('{"active": "a", "keys": {"a": "c2hvcnQ="}}')
    with pytest.raises(ConfigError, match="32 bytes"):
        load_data_keyring(str(path))
    path.write_text('{"active": "b", "keys": {}}')
    with pytest.raises(ConfigError):
        load_data_keyring(str(path))
    with pytest.raises(ConfigError, match="not found"):
        load_data_keyring(str(tmp_path / "missing.json"))


def test_real_keyring_self_test() -> None:
    """OPS-4: the keyring mounted from outside the project loads and works."""
    crypto.self_test(get_settings().data_keyring())
