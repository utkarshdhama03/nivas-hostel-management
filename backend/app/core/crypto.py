"""AES-256-GCM encryption at rest with key IDs (Q65, docs/03 §5.5.1).

Used for identity-document files (TOTP secrets went with D87).

    blob = nonce (12 random bytes) || ciphertext || 16-byte authentication tag

* GCM is "authenticated": decrypting tampered data fails loudly instead of
  returning garbage.
* A fresh random nonce per encryption: never reuse a nonce with the same key.
* Associated data (AAD) binds the ciphertext to its record (e.g. "<org>|<file>|<category>").
  Copying one user's encrypted secret onto another user fails to decrypt.
* The key ID is returned and must be stored next to the blob, so that after a key
  rotation old data still decrypts with its old key.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import ConfigError, KeySet

NONCE_BYTES = 12


class DecryptionError(Exception):
    """Wrong key, unknown key ID, wrong associated data, or tampered ciphertext."""


def encrypt(keys: KeySet, plaintext: bytes, aad: bytes) -> tuple[str, bytes]:
    nonce = os.urandom(NONCE_BYTES)
    return keys.active, nonce + AESGCM(keys.active_key()).encrypt(nonce, plaintext, aad)


def decrypt(keys: KeySet, key_id: str, blob: bytes, aad: bytes) -> bytes:
    key = keys.keys.get(key_id)
    if key is None:
        raise DecryptionError(f"unknown key id {key_id!r}")
    if len(blob) < NONCE_BYTES + 16:
        raise DecryptionError("ciphertext too short")
    try:
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
    except InvalidTag as exc:
        raise DecryptionError("authentication failed") from exc


def self_test(keys: KeySet) -> None:
    """Start-up check (docs/05 OPS-4): the active key really encrypts and decrypts."""
    probe = os.urandom(16)
    kid, blob = encrypt(keys, probe, b"self-test")
    try:
        ok = decrypt(keys, kid, blob, b"self-test") == probe
    except DecryptionError:
        ok = False
    if not ok:
        raise ConfigError("data keyring self-test failed")
