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
    (discovered_root / "biomodstack.db").write_text("", encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    monkeypatch.delenv("BMS_DATA", raising=False)
    monkeypatch.delenv("BMS_DB_PATH", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(paths, "get_code_root", lambda: repo_root)
    monkeypatch.setattr(paths, "_candidate_data_roots", lambda: [discovered_root])

    assert paths.get_data_root() == discovered_root.resolve()
    assert paths.get_db_path() == (discovered_root / "biomodstack.db").resolve()
