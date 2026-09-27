"""Compile the antibody entrypoint with an explicitly supplied Nextflow 25 jar."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = ROOT / "workflows" / "antibody_denovo.nf"


def test_backbone_extraction_preserves_gate_and_downstream_wiring() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    helper, parent = source.split("workflow ANTIBODY_DENOVO {", 1)
    assert "workflow ANTIBODY_BACKBONE_PREPARATION {" in helper
    assert "RFANTIBODY(rfantibody_input, framework_for_rfantibody)" in helper
    assert "StageRFantibodyBackbones(staged_rfantibody_pdbs)" in helper
    assert "ScreenRFantibodyBackbones(" in helper
    assert "backbones = backbone_designs" in helper
    assert "reviewed = reviewed_backbone_designs" in helper
    assert "candidate_count = rfantibody_candidate_count" in helper
    assert "ready_dir = rfantibody_ready_dir" in helper
    assert "ANTIBODY_BACKBONE_PREPARATION(" in parent
    assert "backbone_designs = ANTIBODY_BACKBONE_PREPARATION.out.backbones" in parent
    assert "reviewed_backbone_designs = ANTIBODY_BACKBONE_PREPARATION.out.reviewed" in parent
    assert "rfantibody_candidate_count = ANTIBODY_BACKBONE_PREPARATION.out.candidate_count" in parent
    assert "if (shouldPauseAfterRFantibody) {" in parent
    assert "CheckRFantibodyYield(rfantibody_candidate_count)" in parent
    assert "designs = final_designs" in parent
    assert "backbones = backbone_designs" in parent


def test_nextflow_25_inspects_antibody_entrypoint(tmp_path: Path) -> None:
    jar_path = os.environ.get("BMS_TEST_NEXTFLOW_JAR")
    if jar_path is None:
        pytest.skip("Set BMS_TEST_NEXTFLOW_JAR to the approved Nextflow 25.10.1 jar")
    jar = Path(jar_path)
    assert jar.is_file(), f"Nextflow jar missing: {jar}"
    env = dict(os.environ, NXF_HOME=str(tmp_path / "nxf-home"))
    result = subprocess.run(
        ["java", "-jar", str(jar), "inspect", "workflows/antibody_denovo.nf"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    processes = json.loads(result.stdout)["processes"]
    names = {process["name"] for process in processes}
    assert {"RFANTIBODY", "BatchBoltzValidation", "BatchProtenixValidation"} <= names
