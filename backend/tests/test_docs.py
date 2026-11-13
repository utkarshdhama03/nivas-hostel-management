"""The README must let someone set the project up from nothing (review Critical 4): every secret the
app reads is named in the setup steps, and the step that creates the first Platform Owner is there."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import SecretStr

from app.core.config import Settings

# The tools container mounts it at /repo (compose.yaml); outside Docker it is two folders up.
README = next(p for p in (Path("/repo/README.md"), Path(__file__).resolve().parents[2] / "README.md") if p.exists())


def test_readme_names_every_secret_the_app_reads() -> None:
    text = README.read_text(encoding="utf-8")
    secrets = [
        name.upper() for name, f in Settings.model_fields.items() if SecretStr in getattr(f.annotation, "__args__", ())
    ]
    assert "HMS_JWT_ORG_KEYS" in secrets  # the check itself sees the fields
    missing = [s for s in secrets if s not in text]
    assert missing == [], f"README setup doesn't mention: {missing}"


def test_readme_explains_how_to_create_the_platform_owner() -> None:
    assert "create-platform-owner" in README.read_text(encoding="utf-8")


# --- docs/05 names real tests (review Should fix 10) ------------------------------------------------------------

DOC05 = next(
    p
    for p in (
        Path("/repo/docs/05-security-analysis.md"),
        Path(__file__).resolve().parents[2] / "docs/05-security-analysis.md",
    )
    if p.exists()
)


NAME = r"test_[a-z0-9_]+(?![a-z0-9_.])"  # a test name, not a file name like test_finance.py


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for f in Path(__file__).resolve().parent.rglob("*.py"):
        names |= set(re.findall(r"def (test_[a-z0-9_]+)", f.read_text(encoding="utf-8")))
    return names


def test_every_test_named_in_05_exists_or_is_mapped_to_one() -> None:
    body, marker, appendix = DOC05.read_text(encoding="utf-8").partition("## Appendix A. Test traceability")
    assert marker, "05 has no Appendix A"
    defined = _defined_tests()
    rows = [r.split("|") for r in appendix.splitlines() if r.startswith("| `test_")]
    mapped = {r[1].strip().strip("`") for r in rows}
    unknown = sorted(n for n in set(re.findall(NAME, body)) if n not in defined and n not in mapped)
    assert unknown == [], f"05 names tests that neither exist nor appear in Appendix A: {unknown}"
    targets = {t for r in rows for t in re.findall(r"`(test_[a-z0-9_]+)`", r[2])}
    assert targets, "the appendix maps nothing"
    assert sorted(targets - defined) == [], "Appendix A points at tests that don't exist"
