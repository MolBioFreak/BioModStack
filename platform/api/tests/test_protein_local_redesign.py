from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import build_nextflow_command


def test_protein_local_redesign_is_internal_mode_of_protein_modification_product() -> None:
    frontend_text = (REPO_ROOT / "platform" / "frontend" / "src" / "components" / "JobSubmission.tsx").read_text(encoding="utf-8")
    workflow_text = (REPO_ROOT / "workflows" / "protein_local_redesign.nf").read_text(encoding="utf-8")

    assert "id: 'protein_modification_experimental'" in frontend_text
    assert "name: 'De Novo Design'" in frontend_text
    assert "id: 'protein_local_redesign'" not in frontend_text
    assert "workflow PROTEIN_LOCAL_REDESIGN" in workflow_text
    assert "workflow {" in workflow_text
    assert "PROTEIN_LOCAL_REDESIGN()" in workflow_text


def test_build_nextflow_command_maps_protein_local_redesign_params() -> None:
    cmd = build_nextflow_command(
        "protein_local_redesign",
        "local_redesign",
        {
            "input_pdb": "/tmp/input.pdb",
            "design_chains": "A",
            "context_chains": "B",
            "region_mode": "manual_ranges",
            "redesign_ranges": "45-58,83-91",
            "interface_cutoff": 6.5,
            "region_padding": 3,
            "num_designs": 12,
            "seq_method": "fampnn",
            "seqs_per_design": 6,
            "fix_fixed_sidechains": True,
            "run_boltz_validation": True,
            "boltz_sampling_steps": 150,
            "boltz_recycling_steps": 4,
            "interactive_gating": True,
            "interactive_gate_stage": "post_fampnn",
            "backbone_input_pdbs": "/tmp/plr_backbones",
            "region_manifest": "/tmp/region_manifest.json",
            "final_candidate_dir": "/tmp/final_candidates",
        },
        "/tmp/out",
        job_id="job-123",
    )

    joined = " ".join(cmd)

    assert cmd[1:4] == ["run", "workflows/protein_local_redesign.nf", "-profile"]
    assert "protein_local_redesign,workstation_ryzen7960x" in cmd
    assert "--plr_input_pdb /tmp/input.pdb" in joined
    assert "--plr_design_chains A" in joined
    assert "--plr_context_chains B" in joined
    assert "--plr_region_mode manual_ranges" in joined
    assert "--plr_redesign_ranges 45-58,83-91" in joined
    assert "--plr_interface_cutoff 6.5" in joined
    assert "--plr_region_padding 3" in joined
    assert "--plr_num_designs 12" in joined
    assert "--plr_seq_method fampnn" in joined
    assert "--plr_fix_fixed_sidechains true" in joined
    assert "--plr_run_boltz_validation true" in joined
    assert "--interactive_gating true" in joined
    assert "--interactive_gate_stage post_fampnn" in joined
    assert "--plr_backbone_input_pdbs /tmp/plr_backbones" in joined
    assert "--plr_region_manifest /tmp/region_manifest.json" in joined
    assert "--plr_final_candidate_dir /tmp/final_candidates" in joined
    assert "--rfd_num_designs 12" in joined
    assert "--rfd_mode protein_local_redesign" in joined
    assert "--seqs_per_design 6" in joined
    assert "--boltz_sampling_steps 150" in joined
    assert "--boltz_recycling_steps 4" in joined
    assert "--input_pdb /tmp/input.pdb" not in joined
    assert "--design_chains A" not in joined


def test_resolve_redesign_regions_accepts_plain_manual_ranges(tmp_path: Path) -> None:
    pdb_path = tmp_path / "input.pdb"
    pdb_path.write_text(
        "ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00 10.00           N\n"
        "ATOM      2  CA  GLY A   1       1.000   0.000   0.000  1.00 10.00           C\n"
        "ATOM      3  C   GLY A   1       1.500   1.000   0.000  1.00 10.00           C\n"
        "ATOM      4  O   GLY A   1       1.500   2.000   0.000  1.00 10.00           O\n"
        "ATOM      5  N   ALA A   2       2.500   0.500   0.000  1.00 10.00           N\n"
        "ATOM      6  CA  ALA A   2       3.500   1.000   0.000  1.00 10.00           C\n"
        "ATOM      7  C   ALA A   2       4.500   0.000   0.000  1.00 10.00           C\n"
        "ATOM      8  O   ALA A   2       5.500   0.500   0.000  1.00 10.00           O\n"
        "ATOM      9  N   SER A   3       5.500  -0.500   0.000  1.00 10.00           N\n"
        "ATOM     10  CA  SER A   3       6.500   0.000   0.000  1.00 10.00           C\n"
        "ATOM     11  C   SER A   3       7.500  -1.000   0.000  1.00 10.00           C\n"
        "ATOM     12  O   SER A   3       8.500  -0.500   0.000  1.00 10.00           O\n"
        "ATOM     13  N   THR B   1       3.500   3.000   0.000  1.00 10.00           N\n"
        "ATOM     14  CA  THR B   1       4.500   3.500   0.000  1.00 10.00           C\n"
        "ATOM     15  C   THR B   1       5.500   2.500   0.000  1.00 10.00           C\n"
        "ATOM     16  O   THR B   1       6.500   3.000   0.000  1.00 10.00           O\n"
        "END\n",
        encoding="utf-8",
    )
    seed_pdb = tmp_path / "seed.pdb"
    manifest_path = tmp_path / "manifest.json"

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "resolve_redesign_regions.py"),
            "--input_pdb",
            str(pdb_path),
            "--design_chains",
            "A",
            "--context_chains",
            "B",
            "--region_mode",
            "manual_ranges",
            "--redesign_ranges",
            "1-2",
            "--output_seed_pdb",
            str(seed_pdb),
            "--output_manifest",
            str(manifest_path),
        ],
        check=True,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seed_text = seed_pdb.read_text(encoding="utf-8")

    assert manifest["design_chain"] == "A"
    assert manifest["movable_positions_spec"] == "A1-2"
    assert "A3" in manifest["fixed_positions_spec"]
    assert "B1" in manifest["fixed_positions_spec"]
    assert "2-2" in manifest["contig_spec"]
    assert "ATOM" in seed_text
    assert " B " not in seed_text


def test_plr_rfd3_input_normalizes_legacy_contigs(tmp_path: Path) -> None:
    seed_pdb = tmp_path / "seed.pdb"
    manifest_path = tmp_path / "manifest.json"
    output_json = tmp_path / "rfd3_input.json"

    seed_pdb.write_text(
        "ATOM      1  N   GLY A 146       0.000   0.000   0.000  1.00 10.00           N\n"
        "ATOM      2  CA  GLY A 146       1.000   0.000   0.000  1.00 10.00           C\n"
        "END\n",
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "contig_spec": "[A146-165/34-34/A200-219]",
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "prep_protein_local_redesign_rfd3_input.py"),
            "--seed-pdb",
            str(seed_pdb),
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_json),
        ],
        check=True,
    )

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    spec = next(iter(payload.values()))

    assert spec["contig"] == "A146-165,34-34,A200-219"


def test_merge_local_redesign_emits_canonical_typed_review_contract(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "merged"
    input_dir.mkdir()
    original = tmp_path / "complex.pdb"
    manifest = tmp_path / "region_manifest.json"
    redesign = input_dir / "design_0.pdb"

    original.write_text(
        "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\n"
        "TER\n"
        "ATOM      2  CA  ALA B   1       1.000   0.000   0.000  1.00 10.00           C\n"
        "TER\nEND\n",
        encoding="utf-8",
    )
    redesign.write_text(
        "ATOM      1  CA  SER A   1       0.500   0.000   0.000  1.00 10.00           C\nEND\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps({"design_chain": "A", "context_chains": ["B"], "region_mode": "manual_ranges"}),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "merge_redesigned_complexes.py"),
            "--input-dir",
            str(input_dir),
            "--complex-pdb",
            str(original),
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output_dir),
        ],
        check=True,
    )

    payload = json.loads((output_dir / "design_0.json").read_text(encoding="utf-8"))
    assert payload["review_profile_id"] == "de_novo_generation_v1"
    assert payload["review_contract_version"] == 1
    assert payload["review_contract_source"] == "producer"
    assert payload["review_role_map"] == {
        "result_role": "locally_redesigned_backbone",
        "design_chains": ["A"],
        "context_chains": ["B"],
    }
    assert payload["review_artifact_manifest"]["schema"] == "bms.review-artifacts.v1"
    assert payload["review_artifact_manifest"]["artifacts"]["structure"] == {
        "kind": "structure",
        "state": "ready",
        "path": "design_0.pdb",
        "reason": None,
    }
    assert payload["artifact_class"] == "generated_complex"
    assert payload["result_set"] == "de_novo_backbones"
