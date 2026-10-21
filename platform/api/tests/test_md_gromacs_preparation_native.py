"""Opt-in real CPU preparation/grompp; never starts dynamics or a GPU task."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from scripts.bms_md.gromacs_pipeline import _prepare_system
from scripts.bms_md.native_config import parse_mdp, render_mdp
from scripts.bms_md.runner import StageLedger


def test_native_legacy_recipe_and_authored_mdp_consumption(tmp_path):
    executable = os.environ.get("BMS_TEST_GROMACS")
    if executable is None:
        pytest.skip("Set BMS_TEST_GROMACS to an existing GROMACS executable or managed-runtime wrapper")
    source = Path(__file__).parent / "fixtures/md/1AKI.pdb"
    original = source.read_bytes()
    config = {
        "schema": "bms.md.job.v3", "input": {"structure": str(source)},
        "chemistry": {"profile_id": "gmx_amber99sb_ildn_tip3p_smoke_v1",
                      "resolved_preparation": {"force_field": "amber99sb-ildn", "water_model": "tip3p"}},
        "preparation": {"box_type": "dodecahedron", "padding_nm": 1.0, "salt_molar": 0.15, "neutralize": True},
    }
    coordinates, topology, _ = _prepare_system(
        config, config_path=tmp_path / "unused.json", output_dir=tmp_path,
        gmx_binary=executable, ledger=StageLedger(tmp_path / "stage-state.json"),
    )
    requested = {"integrator": "steep", "nsteps": 0, "emtol": 1000, "cutoff-scheme": "Verlet",
                 "coulombtype": "PME", "rcoulomb": 1.0, "rvdw": 1.0, "pbc": "xyz",
                 "constraints": "h-bonds", "gen-vel": "no", "nstxout": 0}
    mdp = tmp_path / "authored.mdp"
    mdp.write_text(render_mdp(requested))
    result = subprocess.run([executable, "grompp", "-f", str(mdp), "-c", str(coordinates),
                             "-p", str(topology), "-o", str(tmp_path / "authored.tpr"),
                             "-po", str(tmp_path / "effective.mdp")],
                            cwd=topology.parent, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    effective = parse_mdp((tmp_path / "effective.mdp").read_text())
    assert effective["integrator"] == "steep" and effective["nsteps"] == "0"
    assert effective["gen-vel"] == "no" and effective["nstxout"] == "0"
    assert "amber99sb-ildn.ff/forcefield.itp" in topology.read_text()
    assert "amber99sb-ildn.ff/tip3p.itp" in topology.read_text()
    vectors = [float(value) for value in coordinates.read_text().splitlines()[-1].split()]
    assert len(vectors) == 9 and all(value > 0 for value in vectors[:3])
    assert vectors[3:7] == [0.0] * 4
    assert vectors[7] == pytest.approx(vectors[0] / 2, abs=1e-5)
    assert vectors[8] == pytest.approx(vectors[1] / 2, abs=1e-5)
    assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(original).digest()
