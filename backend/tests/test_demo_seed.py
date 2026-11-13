"""The demo seed (tests/demo/seed.py): its reset deletes rows as the superuser, so its limits are tested.

Reset must delete ONLY organizations on the demo addresses that carry the demo marker, plus the demo
Platform Owner, and refuse anything else on those addresses. A full seed run takes minutes, so it is
not part of the gate; it is run and checked by hand (scripts/demo-data.sh).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select, text, update

from app import cli
from app.core import audit
from app.core.config import get_settings
from app.core.db import transaction
from app.modules.organizations.models import Organization, OrganizationProfile
from app.modules.platform.models import PlatformUser
from tests.demo import content, seed
from tests.support import add_person, make_org


def _orgs(*slugs: str) -> set[str]:
    with transaction(None) as db:
        return set(db.scalars(select(Organization.slug).where(Organization.slug.in_(slugs))))


def _audit_rows(org_id: object) -> int:
    with transaction(org_id) as db:  # type: ignore[arg-type]
        return int(db.scalar(text("SELECT count(*) FROM audit_logs")) or 0)


def _platform_users(*emails: str) -> set[str]:
    with transaction(None, role="hms_platform") as db:
        return set(db.scalars(select(func.lower(PlatformUser.email)).where(func.lower(PlatformUser.email).in_(emails))))


def _marked(org_id: object) -> None:
    with transaction(org_id) as db:  # type: ignore[arg-type]
        db.execute(
            update(OrganizationProfile)
            .where(OrganizationProfile.org_id == org_id)
            .values(legal_name=f"Demo ({content.MARKER})")
        )


def test_reset_removes_only_marked_demo_organizations_and_the_demo_owner(app_db: str) -> None:
    assert get_settings().hms_db_name == "hms_test", "this test deletes rows: it must never run against hms"
    demo, bystander = make_org(content.DEMO_UNIVERSITY.slug), make_org()
    _marked(demo.id)
    for org in (demo, bystander):
        add_person(org, "admin", roles=("super_admin",))
        with transaction(org.id) as db:
            audit.record(db, audit.SYSTEM, "test.marker", "organization", org.id, org_id=org.id)
    uploads = Path(get_settings().hms_upload_dir)
    for org in (demo, bystander):
        (uploads / str(org.id)).mkdir(parents=True, exist_ok=True)
        (uploads / str(org.id) / "file.bin").write_bytes(b"x")
    cli.create_platform_owner(seed.OWNER_EMAIL, seed.OWNER_NAME, "quiet lantern 4 river")
    cli.create_platform_owner("someone.else@example.com", "Another Owner", "violet tram 4 eleven")

    assert seed.reset() == 1

    assert _orgs(demo.slug, bystander.slug) == {bystander.slug}
    assert _audit_rows(bystander.id) == 1  # the append-only audit log of anyone else is untouched
    assert not (uploads / str(demo.id)).exists() and (uploads / str(bystander.id) / "file.bin").exists()
    assert _platform_users(seed.OWNER_EMAIL, "someone.else@example.com") == {"someone.else@example.com"}
    assert seed.reset() == 0  # nothing left to remove: safe to run again


def test_reset_refuses_an_organization_the_seed_did_not_make(app_db: str) -> None:
    """Someone registered their own organization on a demo address: nothing at all is deleted."""
    assert get_settings().hms_db_name == "hms_test"
    real = make_org(content.SUNRISE.slug, name="A real hostel")
    add_person(real, "admin", roles=("super_admin",))
    with pytest.raises(seed.SeedError, match="did NOT create"):
        seed.reset()
    assert _orgs(content.SUNRISE.slug) == {content.SUNRISE.slug}
    _marked(real.id)  # clean up, so the test order never matters
    assert seed.reset() == 1


def test_demo_sizes_match_the_request() -> None:
    uni, sunrise = content.DEMO_UNIVERSITY, content.SUNRISE
    assert len(uni.hostels) == 2 and all(len(h.buildings) == 2 and h.floors == 3 for h in uni.hostels)
    assert sum(h.beds for h in uni.hostels) == 228 and sum(h.students for h in uni.hostels) == 200
    assert sum(h.students for h in sunrise.hostels) == 30 and sunrise.name == "Sunrise Girls Hostel"
    for spec in (uni, sunrise):
        assert spec.domain.endswith(".example")  # reserved: can never receive mail (RFC 2606)
        assert content.MARKER in spec.legal_name  # what reset looks for
        for h in spec.hostels:
            assert h.students + 2 <= h.beds  # room for the 2 reservations the seed makes per hostel
    assert uni.slug != "demo-university"  # that address belongs to a real organization in hms


def test_avatar_is_initials_not_a_photo() -> None:
    img = Image.open(io.BytesIO(seed.avatar("Asha Rao")))
    assert img.format == "JPEG" and img.size == (256, 256)


def _passwords_file(folder: Path, admin: str, owner: str, student: str) -> Path:
    (folder / seed.PASSWORDS_FILE).write_text(
        f"# demo passwords\nHMS_DEMO_ADMIN_PASSWORD={admin}\nHMS_DEMO_OWNER_PASSWORD={owner}\n"
        f"HMS_DEMO_STUDENT_PASSWORD={student}\n",
        encoding="utf-8",
    )
    return folder


def test_demo_passwords_come_from_the_secrets_folder(tmp_path: Path) -> None:
    """Decided 12 November: fixed passwords, read from demo-passwords.env in the secrets folder
    (never stored in the project), and checked with the application's rules before anything is removed."""
    source = Path(seed.__file__).read_text(encoding="utf-8")
    assert "secrets.choice" not in source and "easy_password" not in source  # no random passwords any more
    assert seed.STAFF_PASSWORD == seed.STUDENT_PASSWORD == seed.OWNER_PASSWORD == ""  # nothing until loaded

    with pytest.raises(seed.SeedError, match="missing from the secrets folder"):
        seed.load_passwords(tmp_path)
    folder = _passwords_file(tmp_path, "copper kite 41", "slate heron 52", "a long student passphrase 63")
    found = seed.load_passwords(folder)
    assert found == {
        "HMS_DEMO_ADMIN_PASSWORD": "copper kite 41",
        "HMS_DEMO_OWNER_PASSWORD": "slate heron 52",
        "HMS_DEMO_STUDENT_PASSWORD": "a long student passphrase 63",
    }

    # The application's rules apply: 8 characters for everyone (D87), no word from an organization's name.
    _passwords_file(tmp_path, "copper kite 41", "slate heron 52", "short7!")
    with pytest.raises(seed.SeedError, match="HMS_DEMO_STUDENT_PASSWORD.*refused"):
        seed.load_passwords(tmp_path)
    _passwords_file(tmp_path, "copper kite 41", "slate heron 52", "the sunrise passphrase 63")
    with pytest.raises(seed.SeedError, match="HMS_DEMO_STUDENT_PASSWORD.*refused"):
        seed.load_passwords(tmp_path)
    (tmp_path / seed.PASSWORDS_FILE).write_text("HMS_DEMO_ADMIN_PASSWORD=copper kite 41\n", encoding="utf-8")
    with pytest.raises(seed.SeedError, match="no value for: HMS_DEMO_OWNER_PASSWORD, HMS_DEMO_STUDENT_PASSWORD"):
        seed.load_passwords(tmp_path)

    # The backend (all the tests container can see) holds no passwords file.
    backend = Path(seed.__file__).resolve().parents[2]
    assert not list(backend.rglob(seed.PASSWORDS_FILE))


def test_names_are_hand_picked_and_fit_together() -> None:
    """Decided 11 November: names from content.NAME_GROUPS only, first name and surname from
    the same group, all different, and the same on every run."""
    from datetime import UTC, datetime

    now = datetime(2026, 11, 11, 4, 0, tzinfo=UTC)

    def names_of(builder: seed.Builder) -> list[tuple[str, content.NameGroup]]:
        return [builder.person_name("female" if i % 2 else "male") for i in range(240)]

    names = names_of(seed.Builder(content.DEMO_UNIVERSITY, None, now))  # type: ignore[arg-type]  # no API needed
    assert len({n for n, _ in names}) == 240  # all different (more than the 230 students the seed admits)
    for i, (name, group) in enumerate(names):
        first, surname = name.split(" ")
        assert first in (group.female if i % 2 else group.male) and surname in group.surnames, name
        assert name.replace(" ", "").isalpha(), name  # safe in the generated email addresses
    again = names_of(seed.Builder(content.DEMO_UNIVERSITY, None, now))  # type: ignore[arg-type]
    assert [n for n, _ in again] == [n for n, _ in names]  # the fixed seed: the same people every run


def test_logins_file_has_passwords_and_no_authenticator(tmp_path: Path) -> None:
    """D87: sign-in is the email and the password only, so demo-logins.md has no setup keys, no
    recovery codes and no QR pictures; it names the passwords file."""
    from datetime import UTC, datetime

    now = datetime(2026, 11, 11, 4, 0, tzinfo=UTC)
    builder = seed.Builder(content.SUNRISE, None, now)  # type: ignore[arg-type]  # no API needed
    builder.staff_logins = [{"role": "Administrator", "name": "Nandini Rathore", "id": "admin@sunrise-hostel.example"}]
    seed.write_logins(tmp_path, [builder], 60.0, now)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo-logins.md"]  # no pictures, no demo-totp.json
    md = (tmp_path / "demo-logins.md").read_text(encoding="utf-8")
    assert seed.PASSWORDS_FILE in md and "`admin@sunrise-hostel.example`" in md
    for word in ("Setup key", "setup key", "Recovery code", "recovery code", "authenticator", "otpauth://", ".png"):
        assert word not in md, word
    # D75: no removed roles or features in what the presenter reads.
    for word in ("Warden", "Accountant", "Security Guard", "Hostel Manager", "roll call", "gate pass"):
        assert word not in md, word


def test_each_organization_has_exactly_one_administrator() -> None:
    """D76: the organization's one Administrator is the person who registers it; the seed adds no other."""
    assert not hasattr(content.OrgSpec, "staff") and "staff" not in content.OrgSpec.__dataclass_fields__
    source = Path(seed.__file__).read_text(encoding="utf-8")
    assert '"/admin/staff",' not in source  # nothing posts a new staff account
