"""Password hashing and the NIST SP 800-63B-4 password rules (Q71, docs/04 §5.11).

Hashing: Argon2id via argon2-cffi. Deliberately slow and memory-hard, so a stolen
database can't be brute-forced quickly. The salt is random per password and
stored inside the hash string.

Rules (checked when a password is set, never at login):
  * at least 8 characters, for everyone (D87: there is no two-step verification any more);
  * at most 128 (we must accept at least 64; the cap stops huge-input abuse);
  * any characters (spaces, Unicode); NO "must contain a symbol" rules; NO expiry;
  * not in the common-password blocklist (whole password, case-insensitive);
  * must not contain context words: organization name/slug, the user's name,
    email local part or ID (only words of 4+ characters, to avoid false hits).
Passwords are Unicode-normalised (NFKC) first, so visually identical input from
different keyboards hashes the same.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 8  # D87, for every account
MAX_LENGTH = 128
BLOCKLIST_FILE = Path(__file__).parent / "data" / "common-passwords.txt"

_hasher = PasswordHasher()  # Argon2id, library defaults (RFC 9106 recommended profile)


def normalize(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def hash_password(password: str) -> str:
    return _hasher.hash(normalize(password))


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, normalize(password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hashing parameters were raised since this hash was made."""
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("dummy password used only to equalise timing")


def dummy_verify(password: str) -> None:
    """Spend the same time as a real check when the account doesn't exist, so response
    time doesn't reveal which identifiers are registered (docs/05 AUTH-2)."""
    verify_password(_dummy_hash(), password)


@lru_cache(maxsize=1)
def blocklist() -> frozenset[str]:
    with BLOCKLIST_FILE.open(encoding="utf-8") as fh:
        return frozenset(line.strip().lower() for line in fh if line.strip())


@dataclass(frozen=True)
class PasswordProblem:
    code: str
    message: str


def check_new_password(password: str, *, context: Iterable[str | None] = ()) -> list[PasswordProblem]:
    """Return the reasons a new password is rejected (empty list = acceptable)."""
    value = normalize(password)
    lowered = value.lower()
    problems: list[PasswordProblem] = []
    if len(value) < MIN_LENGTH:
        problems.append(PasswordProblem("too_short", f"Use at least {MIN_LENGTH} characters."))
    if len(value) > MAX_LENGTH:
        problems.append(PasswordProblem("too_long", f"Use at most {MAX_LENGTH} characters."))
    if lowered in blocklist():
        problems.append(
            PasswordProblem("too_common", "This password is too common. Choose something less predictable.")
        )
    for word in context:
        w = (word or "").strip().lower()
        if len(w) >= 4 and w in lowered:
            problems.append(
                PasswordProblem(
                    "contains_personal_info", "Don't include your name, email, ID or your organization's name."
                )
            )
            break
    return problems
