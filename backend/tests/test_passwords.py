"""NIST SP 800-63B-4 password rules (Q71) and Argon2id hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core import passwords


def codes(pw: str, *, context: tuple[str | None, ...] = ()) -> set[str]:
    return {p.code for p in passwords.check_new_password(pw, context=context)}


def test_eight_characters_for_everyone() -> None:
    """D87: one minimum, 8 characters, for every account (there is no two-step verification any more)."""
    assert passwords.MIN_LENGTH == 8
    assert "too_short" in codes("seven c")  # 7
    assert codes("eight ch") == set()  # 8
    assert codes("monsoon4821") == set()  # an 8-character password like the demo ones
    assert "too_long" in codes("x" * 129)
    assert codes("x" * 64 + " long passphrases are welcome") == set()


def test_no_composition_rules_spaces_and_unicode_allowed() -> None:
    assert codes("all lowercase words no digits") == set()
    assert codes("पासवर्ड bahut lamba hai") == set()


def test_blocklist_whole_password_case_insensitive() -> None:
    assert "too_common" in codes("password")
    assert "too_common" in codes("PASSWORD")
    assert "too_common" in codes("qwertyuiop")


def test_context_words() -> None:
    assert "contains_personal_info" in codes("sunrise-hostels-rocks", context=("sunrise-hostels",))
    assert "contains_personal_info" in codes("I am Priya Sharma ok", context=("priya",))
    # Words shorter than 4 characters, None and "" are ignored (no false hits on "abc").
    assert codes("a perfectly fine passphrase", context=("abc", None, "")) == set()


def test_hash_and_verify() -> None:
    h = passwords.hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "correct horse battery staple")
    assert not passwords.verify_password(h, "correct horse battery stapler")
    assert not passwords.verify_password("not a hash", "x")
    assert passwords.hash_password("same") != passwords.hash_password("same")  # random salt


def test_unicode_normalisation() -> None:
    h = passwords.hash_password("café latte morning")  # composed e-acute
    assert passwords.verify_password(h, "café latte morning")  # e + combining accent


def test_blocklist_file_is_the_recorded_one() -> None:
    """Q80: the file in the repository is exactly the one documented in SOURCE.md."""
    data = Path(passwords.BLOCKLIST_FILE).read_bytes()
    assert hashlib.sha256(data).hexdigest() == "c2e5696882c603b76bb67a47ee970897e5a76fc4c3f5547abe3d0ca340c576e0"
    assert len(passwords.blocklist()) > 90_000
