from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.nextflow import build_nextflow_command
from services import rfd3_local_redesign as rfd3_service
from scripts.rfd3_local_redesign.contract import ContractError, build_request, write_request


SOURCE_IDENTITIES = [
    {
        "chain_id": "A",
        "residues": [
            {"res_num": 1, "insertion_code": "", "residue_name": "GLY"},
            {"res_num": 2, "insertion_code": "", "residue_name": "ALA"},
            {"res_num": 3, "insertion_code": "", "residue_name": "SER"},
        ],
    },
    {
        "chain_id": "B",
        "residues": [
            {"res_num": 1, "insertion_code": "", "residue_name": "THR"},
        ],
    },
]


def test_partial_diffusion_fixes_every_atom_outside_the_editable_region() -> None:
    request = build_request(
        {
            "input_structure": "/tmp/input.pdb",
            "redesign_mode": "partial_diffusion",
            "design_chains": ["A"],
            "redesign_ranges": "A2",
            "source_residue_identities": SOURCE_IDENTITIES,
            "select_fixed_atoms": {"A2": []},
        }
    )

    assert request["rfd3"]["select_fixed_atoms"] == {
        "A1": ["ALL"],
        "A2": [],
        "A3": ["ALL"],
        "B1": ["ALL"],
    }


def test_partial_diffusion_rejects_unfixing_outside_the_editable_region() -> None:
    with pytest.raises(ContractError, match="outside the editable region"):
        build_request(
            {
                "input_structure": "/tmp/input.pdb",
                "redesign_mode": "partial_diffusion",
                "design_chains": ["A"],
                "redesign_ranges": "A2",
                "source_residue_identities": SOURCE_IDENTITIES,
                "select_fixed_atoms": {"A1": []},
            }
        )


def test_minimal_insertion_rejects_partial_fixed_atom_maps() -> None:
    with pytest.raises(ContractError, match="does not accept select_fixed_atoms"):
        build_request(
            {
                "input_structure": "/tmp/source.pdb",
                "redesign_mode": "minimal_insertion",
                "contig": "A1-2,1-3,A3-4",
                "select_fixed_atoms": {"A1": ["ALL"]},
                "source_residue_identities": SOURCE_IDENTITIES,
            }
        )


def test_api_derives_fixed_scaffold_from_the_bound_source_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdb"
    source.write_text(
        "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\n"
        "ATOM      2  CA  ALA A   2       1.000   0.000   0.000  1.00 10.00           C\n"
        "ATOM      3  CA  THR B   1       2.000   0.000   0.000  1.00 10.00           C\n"
        "END\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rfd3_service, "resolve_runtime_data_path", lambda _value: source)

    normalized, request, _digest = rfd3_service.normalize_local_redesign_params(
        {
            "input_structure": str(source),
            "redesign_mode": "partial_diffusion",
            "design_chains": ["A"],
            "redesign_ranges": "A2",
            "source_residue_identities": [
                {
                    "chain_id": "A",
                    "residues": [{"res_num": 2, "insertion_code": "", "residue_name": "ALA"}],
                }
            ],
        },
        job_name="authoritative-source",
    )

    assert normalized["source_residue_identities"] == request["selection"]["source_residue_identities"]
    assert request["rfd3"]["select_fixed_atoms"] == {
        "A1": ["ALL"],
        "A2": [],
        "B1": ["ALL"],
    }


def test_api_derives_source_residue_identities_from_compressed_mmcif(tmp_path: Path) -> None:
    source = tmp_path / "source.cif.gz"
    mmcif = """data_source
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.pdbx_formal_charge
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA . GLY A 1 1 ? 0.0 0.0 0.0 1.0 10.0 ? 7 GLY A CA 1
"""
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write(mmcif)

    assert rfd3_service._source_residue_identities(source) == [
        {
            "chain_id": "A",
            "residues": [{"res_num": 7, "insertion_code": "", "residue_name": "GLY"}],
        }
    ]


def test_protein_local_redesign_is_first_class_native_model() -> None:
    frontend_text = (REPO_ROOT / "platform" / "frontend" / "src" / "components" / "JobSubmission.tsx").read_text(encoding="utf-8")
    modification_modes_text = (REPO_ROOT / "platform" / "frontend" / "src" / "components" / "proteinModificationModes.ts").read_text(encoding="utf-8")
    results_text = (REPO_ROOT / "platform" / "frontend" / "src" / "components" / "ResultsViewer.tsx").read_text(encoding="utf-8")
    workflow_text = (REPO_ROOT / "workflows" / "protein_local_redesign.nf").read_text(encoding="utf-8")
    model_text = (REPO_ROOT / "platform" / "api" / "config" / "models" / "protein_local_redesign.yaml").read_text(encoding="utf-8")

    assert "id: 'protein_modification_experimental'" in frontend_text
    assert "id: 'protein_local_redesign'" in frontend_text
    assert "label: 'RFD3 Local Redesign'" in modification_modes_text
    assert "RFD3LocalRedesignResultsPane" in results_text
    assert "id: protein_local_redesign" in model_text
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
            "seed": 23,
            "dump_trajectories": True,
            "write_full_json": False,
            "rfd3_batches_per_design": 99,
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
    assert "--plr_seed 23" in joined
    assert "--plr_dump_trajectories true" in joined
    assert "--plr_write_full_json false" in joined
    assert "--rfd3_batches_per_design 12" in joined
    assert "--rfd3_batches_per_design 99" not in joined
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


def test_native_rfd3_command_uses_exact_canonical_execution_controls() -> None:
    module_text = (REPO_ROOT / "modules" / "rfd3.nf").read_text(encoding="utf-8")

    assert "n_batches=${num_designs}" in module_text
    assert "diffusion_batch_size=1" in module_text
    assert "seed=${seed}" in module_text
    assert "dump_trajectories=${dumpTrajectories}" in module_text
    assert "output_full_json=${writeFullJson}" in module_text


def test_native_manifest_separates_candidates_trajectories_and_runtime_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.pdb"
    source.write_text(
        "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n",
        encoding="utf-8",
    )
    request = build_request(
        {
            "input_structure": str(source),
            "redesign_mode": "partial_diffusion",
            "design_chains": ["A"],
            "redesign_ranges": "A1",
            "source_residue_identities": SOURCE_IDENTITIES[:1],
            "num_designs": 1,
            "sequence_policy": "skip",
            "dump_trajectories": True,
            "write_full_json": True,
        },
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    request_path = tmp_path / "request.json"
    write_request(request_path, request)

    candidate = tmp_path / "protein_local_redesign_0_0_model_0.cif.gz"
    metadata = tmp_path / "protein_local_redesign_0_0_model_0.json"
    denoised = tmp_path / "protein_local_redesign_0_0_denoised_model_0.cif.gz"
    noisy = tmp_path / "protein_local_redesign_0_0_noisy_model_0.cif.gz"
    candidate.write_bytes(b"candidate")
    metadata.write_text(json.dumps({"producer_metric": 0.75}), encoding="utf-8")
    denoised.write_bytes(b"denoised")
    noisy.write_bytes(b"noisy")
    receipt = tmp_path / "rfd3_preparation_receipt.json"
    receipt.write_text(json.dumps({"sequence_design": {"state": "not_requested"}}), encoding="utf-8")
    log = tmp_path / "rfd3_protein_local_redesign_0.log"
    log.write_text("producer log\n", encoding="utf-8")
    metadata_jsonl = tmp_path / "rfd3_metadata_protein_local_redesign_0.jsonl"
    metadata_jsonl.write_text("{}\n", encoding="utf-8")

    storage_root = tmp_path / "job" / "run" / "rfd3"
    trajectory_storage = storage_root / "trajectories"
    trajectory_storage.mkdir(parents=True)
    for artifact in (candidate, metadata, log, metadata_jsonl):
        (storage_root / artifact.name).write_bytes(artifact.read_bytes())
    for artifact in (denoised, noisy):
        (trajectory_storage / artifact.name).write_bytes(artifact.read_bytes())
    stored_request = tmp_path / "job" / "requests" / request_path.name
    stored_request.parent.mkdir(parents=True)
    stored_request.write_bytes(request_path.read_bytes())
    stored_source = tmp_path / "job" / "external_inputs" / source.name
    stored_source.parent.mkdir(parents=True)
    stored_source.write_bytes(source.read_bytes())
    stored_receipt = tmp_path / "job" / "collected" / "protein_local_redesign" / receipt.name
    stored_receipt.parent.mkdir(parents=True)
    stored_receipt.write_bytes(receipt.read_bytes())
    output = tmp_path / "manifest.json"

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "rfd3_local_redesign" / "build_result_manifest.py"),
            "--request", str(request_path),
            "--cif-file", str(candidate),
            "--json-file", str(metadata),
            "--trajectory-file", str(denoised),
            "--trajectory-file", str(noisy),
            "--preparation-receipt", str(receipt),
            "--log-file", str(log),
            "--metadata-jsonl", str(metadata_jsonl),
            "--output", str(output),
            "--storage-root", str(storage_root),
            "--request-storage-path", str(stored_request),
            "--source-file", str(source),
            "--source-storage-path", str(stored_source),
            "--preparation-receipt-storage-path", str(stored_receipt),
        ],
        check=True,
    )

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert len(manifest["candidates"]) == 1
    candidate_roles = {artifact["role"] for artifact in manifest["candidates"][0]["artifacts"]}
    assert candidate_roles == {
        "structure",
        "native_prediction_metadata",
        "denoised_trajectory",
        "noisy_trajectory",
    }
    assert manifest["execution_evidence"] == {
        "requested_num_designs": 1,
        "observed_num_designs": 1,
        "candidate_count_integrity": "exact",
        "trajectories": "produced",
        "sequence_design": "not_requested",
    }
    assert {artifact["role"] for artifact in manifest["artifacts"]} >= {
        "preparation_receipt",
        "producer_log",
        "producer_metadata_index",
    }


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
