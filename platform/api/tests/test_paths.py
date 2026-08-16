from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import paths


def test_get_db_path_prefers_discovered_data_root_without_env(monkeypatch, tmp_path: Path) -> None:
    discovered_root = tmp_path / "BioModStack"
    discovered_root.mkdir(parents=True)
    db_path = (discovered_root / "biomodstack.db").resolve()
    db_path.write_text("", encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    monkeypatch.delenv("BMS_DATA", raising=False)
    monkeypatch.delenv("BMS_DB_PATH", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(paths, "get_code_root", lambda: repo_root)
    monkeypatch.setattr(
        paths,
        "resolve_runtime_paths",
        lambda project_root=None: {"data_root": str(discovered_root.resolve()), "db_path": str(db_path)},
    )

    assert paths.get_data_root() == discovered_root.resolve()
    assert paths.get_db_path() == db_path


def test_resolve_runtime_data_path_maps_legacy_data_root_to_active_runtime(monkeypatch, tmp_path: Path) -> None:
    legacy_root = (tmp_path / "host-data").resolve()
    active_root = (tmp_path / "runtime-data").resolve()
    runtime_file = active_root / "bms_results" / "job-1" / "model_0.cif"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("data_test\n", encoding="utf-8")

    legacy_path = legacy_root / "bms_results" / "job-1" / "model_0.cif"

    monkeypatch.setattr(paths, "get_data_root", lambda: active_root)
    monkeypatch.setattr(paths, "_candidate_data_roots", lambda: [legacy_root])
    monkeypatch.setattr(paths, "_runtime_paths", lambda: {"container_state_path": str(active_root)})

    assert paths.resolve_runtime_data_path(legacy_path) == runtime_file


def test_get_mobile_ui_updates_dir_defaults_under_runtime_data_root(monkeypatch, tmp_path: Path) -> None:
    runtime_root = (tmp_path / "runtime-data").resolve()
    monkeypatch.delenv("BMS_MOBILE_UI_UPDATES_DIR", raising=False)
    monkeypatch.setattr(paths, "get_data_root", lambda: runtime_root)

    assert paths.get_mobile_ui_updates_dir() == runtime_root / "mobile-ui-updates"


def test_resolve_allowed_path_rejects_symlink_prefix_escape(monkeypatch, tmp_path: Path) -> None:
    allowed_root = (tmp_path / "data").resolve()
    outside_prefix_sibling = (tmp_path / "data_evil").resolve()
    outside_manifest = outside_prefix_sibling / "qc_manifest.json"
    outside_manifest.parent.mkdir(parents=True)
    outside_manifest.write_text("{}", encoding="utf-8")
    allowed_root.mkdir(parents=True)
    (allowed_root / "linked").symlink_to(outside_prefix_sibling, target_is_directory=True)

    monkeypatch.setattr(paths, "get_allowed_roots", lambda: {"bms_results": allowed_root})

    try:
        paths.resolve_allowed_path("bms_results/linked/qc_manifest.json")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("symlink escape with shared string prefix was allowed")
