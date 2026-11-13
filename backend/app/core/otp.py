"""Emailed one-time codes: the 6-digit password-reset code (D77).

6 digits, 10 minutes, single use, at most 5 wrong tries (the database CHECKs on email_otps and the
reset flow). A code is short, so it is hashed with Argon2 (slow) rather than plain SHA-256: an
offline guess against a stolen hash is impractical. Two-step verification (TOTP, recovery codes,
sign-in codes) was removed by D87.
"""

from __future__ import annotations

import functools
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _normalize(code: str) -> str:
    return code.strip().replace(" ", "").replace("-", "")


def hash_code(code: str) -> str:
    return _hasher.hash(_normalize(code))


def code_matches(code_hash: str, code: str) -> bool:
    try:
        return _hasher.verify(code_hash, _normalize(code))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


@functools.cache
def _dummy_hash() -> str:
    return _hasher.hash("000000")


def dummy_verify(code: str) -> None:
    """The same Argon2 work as checking a real code, for accounts that don't exist (05 AUTH-2)."""
    code_matches(_dummy_hash(), code)
