from __future__ import annotations

import json
from pathlib import Path

import pytest


def _load():
    from services.bioxp.models import BioXpProfile
    from services.bioxp.profile_store import BioXpProfileStore, ProfileStoreError

    return BioXpProfile, BioXpProfileStore, ProfileStoreError


def test_profile_store_round_trip_is_canonical_and_private(tmp_path: Path) -> None:
    BioXpProfile, BioXpProfileStore, _ = _load()
    path = tmp_path / "bioxp" / "profile.json"
    store = BioXpProfileStore(path)

    saved = store.save(BioXpProfile(display_name="Lab robot", api_url="http://robot:8123"))

    assert store.load() == saved
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "display_name": "Lab robot",
        "api_url": "http://robot:8123",
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.glob("*.tmp")) == []


def test_profile_store_malformed_file_fails_closed(tmp_path: Path) -> None:
    _, BioXpProfileStore, ProfileStoreError = _load()
    path = tmp_path / "bioxp" / "profile.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"api_url":', encoding="utf-8")

    with pytest.raises(ProfileStoreError, match="malformed"):
        BioXpProfileStore(path).load()


def test_legacy_linkage_migration_is_explicit_and_one_shot(tmp_path: Path) -> None:
    _, BioXpProfileStore, _ = _load()
    profile_path = tmp_path / "bioxp" / "profile.json"
    legacy_path = tmp_path / "bioxp_linkage_url"
    legacy_path.write_text("http://robot:8123\n", encoding="utf-8")
    store = BioXpProfileStore(profile_path, legacy_path=legacy_path)

    assert store.load() is None
    migrated = store.migrate_legacy(display_name="BioXP3200")

    assert migrated is not None
    assert migrated.api_url == "http://robot:8123"
    assert store.load() == migrated
    assert not legacy_path.exists()
    assert store.migrate_legacy() is None
