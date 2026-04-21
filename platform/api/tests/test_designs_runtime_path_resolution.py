from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.responses import FileResponse


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import paths
from routers import designs


class _FakeResult:
    def __init__(self, design):
        self._design = design

    def scalar_one_or_none(self):
        return self._design


class _FakeSession:
    def __init__(self, design):
        self._design = design

    async def execute(self, _stmt):
        return _FakeResult(self._design)


def test_get_design_pdb_rewrites_legacy_host_paths_to_active_runtime(monkeypatch, tmp_path: Path) -> None:
    legacy_root = (tmp_path / "host-data").resolve()
    active_root = (tmp_path / "runtime-data").resolve()
    runtime_file = active_root / "bms_results" / "job-1" / "model_0.cif"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("data_test\n", encoding="utf-8")
    legacy_path = legacy_root / "bms_results" / "job-1" / "model_0.cif"

    monkeypatch.setattr(paths, "get_data_root", lambda: active_root)
    monkeypatch.setattr(paths, "_candidate_data_roots", lambda: [legacy_root])
    monkeypatch.setattr(paths, "_runtime_paths", lambda: {"container_state_path": str(active_root)})

    design = SimpleNamespace(id="design-1", name="model_0", pdb_path=str(legacy_path))

    response = asyncio.run(designs.get_design_pdb("design-1", session=_FakeSession(design)))

    assert isinstance(response, FileResponse)
    assert Path(response.path) == runtime_file
