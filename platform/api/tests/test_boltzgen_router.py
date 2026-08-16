from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import boltzgen


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.asyncio
async def test_preview_design_spec_returns_yaml_and_check_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def _fake_prepare(params: dict[str, object]) -> tuple[dict[str, object], list[str]]:
        resolved = dict(params)
        resolved["boltzgen_nanobody_scaffold_specs"] = json.dumps(
            [{"name": "7EOW", "path": "/tmp/7eow_hlt.pdb", "chain_id": "H"}]
        )
        return resolved, ["BoltzGen scaffold resolved from SAbDab framework 7EOW"]

    def _fake_run(cmd: list[str], capture_output: bool, text: bool, cwd: Path):
        if "prep_boltzgen.py" in " ".join(cmd):
            output_index = cmd.index("--output_yaml") + 1
            yaml_path = Path(cmd[output_index])
            yaml_path.write_text("entities:\n- file:\n    path: target.pdb\n", encoding="utf-8")
            return _Result(returncode=0, stdout="prep ok")
        return _Result(returncode=0, stdout="check ok", stderr="")

    monkeypatch.setattr(boltzgen, "prepare_boltzgen_params_for_launch", _fake_prepare)
    monkeypatch.setattr(boltzgen.subprocess, "run", _fake_run)
    monkeypatch.setattr(boltzgen, "get_code_root", lambda: tmp_path)
    monkeypatch.setattr(boltzgen, "get_container_path", lambda name: tmp_path / name)
    (tmp_path / "boltzgen.sif").write_text("stub", encoding="utf-8")

    response = await boltzgen.preview_design_spec(
        boltzgen.BoltzGenPreviewRequest(
            params={
                "boltzgen_mode": "nanobody_binder",
                "boltzgen_target_pdb_path": "/tmp/target.pdb",
            },
            validate=True,
        )
    )

    assert response.check_ok is True
    assert response.yaml_text.startswith("entities:")
    assert response.scaffold_specs[0]["name"] == "7EOW"
    assert "framework 7EOW" in response.notes[0]
