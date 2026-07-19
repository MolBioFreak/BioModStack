from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_partial_md_collection_publishes_manifest_before_parent_terminal_failure(tmp_path: Path) -> None:
    child_dir = tmp_path / "child-0" / "replicas" / "replica_0"
    child_dir.mkdir(parents=True)
    (child_dir / "production.xtc").write_bytes(b"trajectory")
    (child_dir / "production.cpt").write_bytes(b"checkpoint")
    (child_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "bms.md.run.v1",
                "status": "completed",
                "job_id": "md-parent-1",
                "replica_index": 0,
                "replica_seed": 20260717,
                "engine": "gromacs",
                "artifacts": {
                    "trajectory": {"path": "production.xtc"},
                    "checkpoint": {"path": "production.cpt"},
                },
            }
        ),
        encoding="utf-8",
    )
    child_status = tmp_path / "child_outputs.json"
    child_status.write_text(
        json.dumps(
            {
                "total": 2,
                "completed": 1,
                "failed": 1,
                "cancelled": 0,
                "child_ids": ["child-0", "child-1"],
                "child_output_dirs": [str(tmp_path / "child-0")],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "aggregate"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.bms_md.aggregate_children",
            "--child-status",
            str(child_status),
            "--output-dir",
            str(output_dir),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial_failure"
    assert manifest["lineage"]["failed_children"] == 1
    assert (output_dir / "replicas" / "replica_0" / "production.xtc").is_file()
