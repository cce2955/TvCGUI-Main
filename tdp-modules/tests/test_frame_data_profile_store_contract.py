from __future__ import annotations

import json
from pathlib import Path

from tvcgui.features.frame_data.profile_store import (
    iter_frame_data_profiles,
    load_frame_data_profile,
    save_frame_data_profile,
    split_legacy_profile_file,
)


def _profile(key: str, char_id: int, value: int) -> dict:
    return {
        "version": 9,
        "scanner_build": "test",
        "key": key,
        "char_id": char_id,
        "char_name": key,
        "table_signature": f"sig-{char_id}",
        "moves": [{"id": 0x100 + char_id, "damage": value}],
    }


def test_split_legacy_profiles_and_load_individually(tmp_path: Path) -> None:
    legacy = tmp_path / "frame_data_profiles.json"
    shards = tmp_path / "frame_data_profiles"
    profiles = {
        "id_01_alpha": _profile("id_01_alpha", 1, 100),
        "id_02_beta": _profile("id_02_beta", 2, 200),
    }
    legacy.write_text(
        json.dumps({"version": 9, "profiles": profiles}),
        encoding="utf-8",
    )

    report = split_legacy_profile_file(
        str(legacy),
        str(shards),
        expected_version=9,
    )

    assert len(report["written"]) == 2
    assert (shards / "id_01_alpha.json").is_file()
    assert (shards / "id_02_beta.json").is_file()
    loaded = load_frame_data_profile(
        "id_02_beta",
        expected_version=9,
        bundled_directory=str(shards),
        writable_directory=str(shards),
        bundled_legacy_file="",
        writable_legacy_file="",
    )
    assert loaded == profiles["id_02_beta"]


def test_writable_shard_overrides_bundled_seed(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    writable = tmp_path / "writable"
    assert save_frame_data_profile(
        "id_01_alpha",
        _profile("id_01_alpha", 1, 100),
        writable_directory=str(bundled),
        expected_version=9,
    )
    assert save_frame_data_profile(
        "id_01_alpha",
        _profile("id_01_alpha", 1, 999),
        writable_directory=str(writable),
        expected_version=9,
    )

    loaded = load_frame_data_profile(
        "id_01_alpha",
        expected_version=9,
        bundled_directory=str(bundled),
        writable_directory=str(writable),
        bundled_legacy_file="",
        writable_legacy_file="",
    )
    assert loaded is not None
    assert loaded["moves"][0]["damage"] == 999

    all_profiles = list(
        iter_frame_data_profiles(
            expected_version=9,
            bundled_directory=str(bundled),
            writable_directory=str(writable),
            bundled_legacy_file="",
            writable_legacy_file="",
        )
    )
    assert len(all_profiles) == 1
    assert all_profiles[0]["moves"][0]["damage"] == 999


def test_legacy_cache_auto_migrates_on_first_load(tmp_path: Path) -> None:
    legacy = tmp_path / "frame_data_profiles.json"
    shards = tmp_path / "frame_data_profiles"
    profile = _profile("id_03_gamma", 3, 300)
    legacy.write_text(
        json.dumps({"version": 9, "profiles": {"id_03_gamma": profile}}),
        encoding="utf-8",
    )

    loaded = load_frame_data_profile(
        "id_03_gamma",
        expected_version=9,
        bundled_directory="",
        writable_directory=str(shards),
        bundled_legacy_file="",
        writable_legacy_file=str(legacy),
    )

    assert loaded == profile
    assert (shards / "id_03_gamma.json").is_file()
    assert not legacy.exists()
    assert (tmp_path / "frame_data_profiles.json.migrated.bak").is_file()
