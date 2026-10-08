from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_ROOT.parent

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

sys.modules.setdefault("pyrosetta", types.SimpleNamespace(rosetta=types.SimpleNamespace()))

from anchors_to_ppiflow_positions import build_positions
from prepare_ppiflow_maturation import _build_anchor_payload


class _FakePdbInfo:
    def __init__(self, records: dict[int, tuple[str, int, str]]):
        self._records = records

    def chain(self, resi: int) -> str:
        return self._records[resi][0]

    def number(self, resi: int) -> int:
        return self._records[resi][1]

    def icode(self, resi: int) -> str:
        return self._records[resi][2]


class _FakeResidue:
    def __init__(self, aa: str):
        self._aa = aa

    def name1(self) -> str:
        return self._aa


class _FakePose:
    def __init__(self, records: dict[int, tuple[str, int, str, str]]):
        self._pdb_info = _FakePdbInfo({idx: (chain, resnum, icode) for idx, (chain, resnum, icode, _aa) in records.items()})
        self._residues = {idx: _FakeResidue(aa) for idx, (_chain, _resnum, _icode, aa) in records.items()}

    def pdb_info(self) -> _FakePdbInfo:
        return self._pdb_info

    def residue(self, resi: int) -> _FakeResidue:
        return self._residues[resi]


def test_anchor_positions_exclude_movable_region_members_by_default() -> None:
    anchors = [
        {"chain": "H", "resnum": 27, "icode": "", "movable_region_member": True},
        {"chain": "H", "resnum": 28, "icode": "", "movable_region_member": True},
        {"chain": "H", "resnum": 50, "icode": "", "movable_region_member": False},
        {"chain": "L", "resnum": 30, "icode": "", "movable_region_member": False},
    ]

    assert build_positions(anchors) == "H50,L30"
    assert build_positions(anchors, include_movable_anchors=True) == "H27-28,H50,L30"


def test_build_anchor_payload_keeps_movable_candidates_out_of_effective_anchors() -> None:
    pose = _FakePose(
        {
            1: ("H", 27, "", "Y"),
            2: ("H", 50, "", "W"),
            3: ("A", 10, "", "S"),
        }
    )
    binder_scores = {
        ("H", 27, ""): -10.0,
        ("H", 50, ""): -7.0,
        ("A", 10, ""): -9.0,
    }

    payload = _build_anchor_payload(
        pose,
        interface_residues=[1, 2, 3],
        binder_residue_scores=binder_scores,
        energy_threshold=-5.0,
        movable_positions={("H", 27)},
        antibody_chains=["H"],
        antigen_chains=["A"],
        region_mode="selected_cdrs",
        selected_loops=["H3"],
    )

    assert payload["anchor_count"] == 1
    assert payload["anchor_candidate_count"] == 2
    assert payload["movable_anchor_candidate_count"] == 1
    assert payload["excluded_movable_anchor_count"] == 1
    assert payload["anchors_include_movable_positions"] is False
    assert [(entry["chain"], entry["resnum"]) for entry in payload["anchors"]] == [("H", 50)]
    assert [(entry["chain"], entry["resnum"]) for entry in payload["movable_anchor_candidates"]] == [("H", 27)]


def test_filter_maturation_fails_when_requested_objective_disagrees_with_score_json(tmp_path: Path) -> None:
    score_json = tmp_path / "score.json"
    score_json.write_text(
        json.dumps(
            {
                "objective_mode": "balanced",
                "objective_score": -2.0,
                "selected_delta_interface_score": -1.0,
                "delta_interface_score": -1.0,
            }
        ),
        encoding="utf-8",
    )
    pdb_path = tmp_path / "matured.pdb"
    pdb_path.write_text("MODEL\nENDMDL\n", encoding="utf-8")
    report_json = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_ROOT / "filter_maturation.py"),
            "--score_json",
            str(score_json),
            "--pdb_path",
            str(pdb_path),
            "--output_dir",
            str(tmp_path / "filtered"),
            "--objective_mode",
            "selected_interface",
            "--disable_filter",
            "--report_json",
            str(report_json),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "objective_mode mismatch" in result.stderr
    assert not report_json.exists()


def test_score_maturation_payload_documents_local_non_af3_objective() -> None:
    text = (SCRIPTS_ROOT / "score_maturation.py").read_text(encoding="utf-8")

    for expected in (
        '"scoring_backend": "biomodstack_local_pair_energy_geometry"',
        '"af3score_used": False',
        '"upstream_ppiflow_rank_score_used": False',
        '"selection_direction": "lower_is_better"',
    ):
        assert expected in text


def test_ppiflow_workflow_propagates_objective_and_uses_zero_preserving_defaults() -> None:
    workflow_text = (REPO_ROOT / "workflows" / "antibody_denovo.nf").read_text(encoding="utf-8")
    module_text = (REPO_ROOT / "modules" / "ppiflow.nf").read_text(encoding="utf-8")

    for process_name in ("process SpawnMaturationJobs {", "process SpawnValidatedMaturationJobs {"):
        start = workflow_text.index(process_name)
        end = workflow_text.index("process ", start + len(process_name))
        block = workflow_text[start:end]
        for expected in (
            "ppiflow_objective_mode: paramValueOrDefault(params, 'ppiflow_objective_mode', null)",
            "ppiflow_objective_threshold: paramValueOrDefault(params, 'ppiflow_objective_threshold', null)",
            "ppiflow_rotamer_shell_distance: paramValueOrDefault(params, 'ppiflow_rotamer_shell_distance', paramValueOrDefault(params, 'ppiflow_rotamer_shell_cutoff', 20.0))",
            "ppiflow_rotamer_shell_cutoff: paramValueOrDefault(params, 'ppiflow_rotamer_shell_distance', paramValueOrDefault(params, 'ppiflow_rotamer_shell_cutoff', 20.0))",
            "ppiflow_relax_antibody_backbone_shell: paramValueOrDefault(params, 'ppiflow_relax_antibody_backbone_shell', false)",
            "def payloadMaturationRegionMode = stage_name == 'backbone_refine'",
            "ppiflow_backbone_region_mode: payloadBackboneRegionMode",
            "ppiflow_maturation_region_mode: payloadMaturationRegionMode",
        ):
            assert expected in block
        for forbidden in (
            "params.ppiflow_start_t ?: 0.8",
            "params.maturation_anchor_threshold ?: -5.0",
            "params.maturation_anchor_distance_cutoff ?: 12.0",
            "params.maturation_min_improvement ?: -1.0",
            "params.ppiflow_rotamer_shell_cutoff ?: 20.0",
            "def maturationRegionMode = params.ppiflow_maturation_region_mode",
            "def postValidationRegionMode = params.ppiflow_maturation_region_mode",
        ):
            assert forbidden not in block

    assert "def maturationRegionMode = params.ppiflow_maturation_region_mode" not in workflow_text
    assert "def postValidationRegionMode = params.ppiflow_maturation_region_mode" not in workflow_text
    assert 'maturationSelectedLoops ?: ""' in workflow_text
    assert 'postValidationSelectedLoops ?: ""' in workflow_text
    assert 'backboneRefineSelectedLoops ?: ""' in workflow_text
    assert "def ppiflowGlobalRegionMode = params.containsKey('ppiflow_region_mode') ? params.get('ppiflow_region_mode') : null" in workflow_text
    assert "paramValueOrDefault(params, 'ppiflow_backbone_region_mode', ppiflowGlobalRegionMode)" in workflow_text
    assert "paramValueOrDefault(params, 'ppiflow_maturation_region_mode', ppiflowGlobalRegionMode)" in workflow_text
    assert "paramValueOrDefault(params, 'ppiflow_maturation_loop_scope', null)" in workflow_text

    for forbidden in (
        "params.maturation_anchor_threshold ?: -5.0",
        "params.maturation_anchor_distance_cutoff ?: 12.0",
        "params.ppiflow_rotamer_shell_distance ?: 20.0",
        "params.ppiflow_objective_mode ?: 'selected_interface'",
    ):
        assert forbidden not in module_text


def test_ppiflow_module_wires_pure_sidechain_rotamer_enrichment_and_mask_validation() -> None:
    module_text = (REPO_ROOT / "modules" / "ppiflow.nf").read_text(encoding="utf-8")
    prepare_text = (SCRIPTS_ROOT / "prepare_ppiflow_maturation.py").read_text(encoding="utf-8")

    assert "--relax_antibody_backbone_shell" in prepare_text
    assert "relax_antibody_backbone_shell" in module_text
    assert "--movable_positions" in module_text
    assert "validate_ppiflow_masks.py" in module_text
    assert "backbone_movement_allowed" in prepare_text
