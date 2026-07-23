from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent


def _load_analysis_plane():
    path = REPO_ROOT / "scripts" / "run_conformational_mapping_analysis_plane.py"
    spec = importlib.util.spec_from_file_location("cm_analysis_plane_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cm_analysis_plane_omits_state_artifact_index_member_without_comparison_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_analysis_plane()
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "derived").mkdir()
    (canonical / "cm_ensemble_v1.json").write_text(json.dumps({"candidates": []}))
    request = tmp_path / "cm_request_v1.json"
    request.write_text(json.dumps({
        "request_id": "request-no-authority", "request_sha256": "a" * 64,
        "backend": "protenix_v2_ensemble",
        "analysis_policy": {"clash_detector_id": "test", "clash_detector_version": "1"},
    }))
    snapshots = tmp_path / "snapshots.json"
    snapshots.write_text("[]")
    checkpoint = tmp_path / "checkpoint.bin"
    checkpoint.write_bytes(b"checkpoint")
    tool = tmp_path / "frustrampnn"
    tool.write_bytes(b"tool")
    monkeypatch.setattr(module.shutil, "which", lambda _name: str(tool))
    monkeypatch.setattr(
        module,
        "analyze_landscapes",
        lambda *_args, **_kwargs: {
            "analysis_id": "analysis", "support_records": [], "pair_ledger": [],
            "ranking_policy": {}, "clash_records": [], "exclusions": [], "results": [],
        },
    )
    monkeypatch.setattr(module, "derive_state_landscape_analysis_for_request", lambda *_args: None)
    output = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_conformational_mapping_analysis_plane.py",
            "--request", str(request), "--snapshots", str(snapshots),
            "--canonical", str(canonical), "--checkpoint", str(checkpoint),
            "--checkpoint-id", "test", "--out", str(output),
        ],
    )

    module.main()

    index = json.loads((output / "cm_derived_index_v1.json").read_text())
    assert "state_landscape_analyses" not in index