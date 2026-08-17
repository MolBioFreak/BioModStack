from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.nextflow import build_nextflow_command, normalize_plr_structure_validators
from services import rfd3_local_redesign as rfd3_service
from services.result_ingester import _local_redesign_validate_native_request_artifact
from scripts.rfd3_local_redesign.contract import ContractError, build_request, request_sha256, write_request


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


def test_native_request_artifact_must_equal_the_immutable_request(tmp_path: Path) -> None:
    immutable = {
        "schema": "bms.rfd3.local-redesign.request.v1",
        "request_id": "bound",
        "input": {"path": "/tmp/β-structure.cif"},
    }
    artifact = tmp_path / "rfd3_local_redesign_request.json"
    write_request(artifact, immutable)
    _local_redesign_validate_native_request_artifact(
        artifact,
        request_payload=immutable,
        request_sha256=request_sha256(immutable),
    )
    artifact.write_text(
        json.dumps({**immutable, "request_id": "altered"}, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="native request artifact binding is invalid"):
        _local_redesign_validate_native_request_artifact(
            artifact,
            request_payload=immutable,
            request_sha256=request_sha256(immutable),
        )


def test_native_job_ingress_bypasses_antibody_defaults_before_contract_validation() -> None:
    jobs_source = (API_ROOT / "routers" / "jobs.py").read_text(encoding="utf-8")
    expected = '''if not (normalized_model_id == "protein_local_redesign" and normalized_mode == "local_redesign"):
            job_data.params = _normalize_antibody_job_params(job_data.params)

        if normalized_model_id == "protein_local_redesign" and normalized_mode == "local_redesign":'''
    assert expected in jobs_source


def test_partial_diffusion_fixes_every_atom_outside_the_editable_region() -> None:
    request = build_request(
        {
            "input_structure": "/tmp/input.pdb",
            "redesign_mode": "partial_diffusion",
            "design_chains": ["A"],
            "redesign_ranges": "A2",
            "source_residue_identities": SOURCE_IDENTITIES,
        }
    )

    assert request["rfd3"]["select_fixed_atoms"] == {
        "A1": ["ALL"],
        "A2": [],
        "A3": ["ALL"],
        "B1": ["ALL"],
    }


def test_partial_diffusion_rejects_custom_fixed_atom_overrides() -> None:
    with pytest.raises(ContractError, match="not exposed"):
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
                "design_chains": ["A"],
                "insertion_anchor": "A2",
                "insertion_min_length": 1,
                "insertion_max_length": 3,
                "select_fixed_atoms": {"A1": ["ALL"]},
                "source_residue_identities": SOURCE_IDENTITIES,
            }
        )


def test_minimal_insertion_contig_is_source_derived_and_claim_bound() -> None:
    request = build_request(
        {
            "input_structure": "/tmp/source.pdb",
            "redesign_mode": "minimal_insertion",
            "design_chains": ["A"],
            "context_chains": ["B"],
            "insertion_anchor": "A2",
            "insertion_min_length": 1,
            "insertion_max_length": 3,
            "source_residue_identities": SOURCE_IDENTITIES,
        }
    )
    assert request["rfd3"]["contig"] == "A1-2,1-3,A3,/0,B1"
    assert request["selection"]["insertion_anchor"] == "A2"

    with pytest.raises(ContractError, match="does not match"):
        build_request(
            {
                "input_structure": "/tmp/source.pdb",
                "redesign_mode": "minimal_insertion",
                "design_chains": ["A"],
                "context_chains": ["B"],
                "insertion_anchor": "A2",
                "insertion_min_length": 1,
                "insertion_max_length": 3,
                "contig": "A1,9-9,A2-3,/0,B1",
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
    monkeypatch.setattr(rfd3_service, "get_data_root", lambda: tmp_path)

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
    output_dir = tmp_path / "job-output"
    output_dir.mkdir()
    materialized, materialized_request, materialized_digest, request_path = (
        rfd3_service.materialize_local_redesign_request(
            normalized,
            output_dir=output_dir,
            job_id="owned-source-job",
        )
    )
    owned_source = output_dir / "external_inputs" / source.name
    assert owned_source.read_bytes() == source.read_bytes()
    assert materialized_request["input"]["path"] == str(owned_source.resolve())
    assert materialized["rfd3_request"] == materialized_request
    for alias in ("input_structure", "input_pdb", "input_cif", "input", "plr_input_pdb"):
        assert materialized[alias] == str(owned_source.resolve())
    assert request_path.is_relative_to(output_dir.resolve())
    assert request_sha256(materialized_request) == materialized_digest

    unsafe_output = tmp_path / "unsafe-output"
    unsafe_output.mkdir()
    outside = tmp_path / "outside-owned"
    outside.mkdir()
    (unsafe_output / "external_inputs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ContractError, match="source directory is unsafe"):
        rfd3_service.materialize_local_redesign_request(
            normalized,
            output_dir=unsafe_output,
            job_id="unsafe-owned-source-job",
        )
    assert not list(outside.iterdir())

    failed_output = tmp_path / "failed-output"
    failed_output.mkdir()
    original_write_request = rfd3_service.write_request

    def fail_request_write(_path: Path, _request: dict[str, object]) -> str:
        raise OSError("forced request write failure")

    monkeypatch.setattr(rfd3_service, "write_request", fail_request_write)
    with pytest.raises(ContractError, match="failed to materialize"):
        rfd3_service.materialize_local_redesign_request(
            normalized,
            output_dir=failed_output,
            job_id="retryable-owned-source-job",
        )
    assert not list((failed_output / "external_inputs").iterdir())
    assert not list((failed_output / "requests").iterdir())
    monkeypatch.setattr(rfd3_service, "write_request", original_write_request)
    retried, _, _, _ = rfd3_service.materialize_local_redesign_request(
        normalized,
        output_dir=failed_output,
        job_id="retryable-owned-source-job",
    )
    assert Path(retried["input_structure"]).is_file()


def test_api_rejects_source_outside_the_active_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "outside.pdb"
    source.write_text(
        "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n",
        encoding="utf-8",
    )
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    monkeypatch.setattr(rfd3_service, "resolve_runtime_data_path", lambda _value: source)
    monkeypatch.setattr(rfd3_service, "get_data_root", lambda: allowed_root)

    with pytest.raises(ContractError, match="active BioModStack data root"):
        rfd3_service.normalize_local_redesign_params(
            {
                "input_structure": str(source),
                "redesign_mode": "partial_diffusion",
                "design_chains": ["A"],
                "redesign_ranges": "A1",
            },
            job_name="outside-root",
        )


def test_api_rejects_tampered_current_revision_request_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdb"
    source.write_text(
        "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\n"
        "ATOM      2  CA  ALA A   2       1.000   0.000   0.000  1.00 10.00           C\n"
        "END\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rfd3_service, "resolve_runtime_data_path", lambda _value: source)
    monkeypatch.setattr(rfd3_service, "get_data_root", lambda: tmp_path)
    params = {
        "input_structure": str(source),
        "redesign_mode": "partial_diffusion",
        "design_chains": ["A"],
        "redesign_ranges": "A2",
        "partial_t": 1.0,
    }
    _, request, _ = rfd3_service.normalize_local_redesign_params(params, job_name="replay")
    tampered = json.loads(json.dumps(request))
    tampered["rfd3"]["partial_t"] = 9.0

    with pytest.raises(ContractError, match="does not match the canonical source-derived request"):
        rfd3_service.normalize_local_redesign_params(
            {**params, "rfd3_request": tampered},
            job_name="replay",
        )


def test_native_preparation_uses_and_hash_checks_the_staged_source(tmp_path: Path) -> None:
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
            "source_residue_identities": [
                {
                    "chain_id": "A",
                    "residues": [{"res_num": 1, "insertion_code": "", "residue_name": "GLY"}],
                }
            ],
        },
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    request_path = tmp_path / "request.json"
    write_request(request_path, request)
    staged = tmp_path / "staged" / source.name
    staged.parent.mkdir()
    staged.write_bytes(source.read_bytes())
    native_input = tmp_path / "native.json"
    receipt = tmp_path / "receipt.json"
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "rfd3_local_redesign" / "prepare_native_input.py"),
        "--request", str(request_path),
        "--input-structure", str(staged),
        "--output-native", str(native_input),
        "--output-receipt", str(receipt),
    ]
    subprocess.run(command, check=True)

    native_payload = json.loads(native_input.read_text(encoding="utf-8"))
    assert native_payload["protein_local_redesign_0"]["input"] == staged.name

    staged.write_text("END\n", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(command, check=True)


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
    assert "--plr_write_full_json true" in joined
    assert "--rfd3_batches_per_design 12" in joined
    assert "--rfd3_batches_per_design 99" not in joined
    assert "--plr_seq_method skip" in joined
    assert "--plr_fix_fixed_sidechains" not in joined
    assert "--plr_run_boltz_validation false" in joined
    assert "--interactive_gating false" in joined
    assert "--interactive_gate_stage" not in joined
    assert "--plr_backbone_input_pdbs" not in joined
    assert "--plr_region_manifest" not in joined
    assert "--plr_final_candidate_dir" not in joined
    assert "--rfd_num_designs 12" in joined
    assert "--rfd_mode protein_local_redesign" in joined
    assert "--seqs_per_design" not in joined
    assert "--boltz_sampling_steps" not in joined
    assert "--boltz_recycling_steps" not in joined
    assert "--input_pdb /tmp/input.pdb" not in joined
    assert "--design_chains A" not in joined


def test_experimental_protein_local_redesign_maps_validator_suite() -> None:
    cmd = build_nextflow_command(
        "protein_modification_experimental",
        "region_redesign",
        {
            "input_pdb": "/tmp/input.pdb",
            "design_chains": "A",
            "structure_validators": ["esmfold2", "protenix_v2"],
        },
        "/tmp/out",
        job_id="job-123",
    )

    joined = " ".join(cmd)
    assert "--plr_structure_validators esmfold2,protenix_v2" in joined
    assert "--plr_validator_suite_active true" in joined


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        (["protenix_v2"], ["protenix_v2"]),
        (["esmfold2", "protenix_v2"], ["esmfold2", "protenix_v2"]),
        (["boltz2", "esmfold2", "protenix_v2"], ["boltz2", "esmfold2", "protenix_v2"]),
    ],
)
def test_protein_local_validator_selection_accepts_any_supported_subset(
    selected: list[str], expected: list[str]
) -> None:
    normalized = normalize_plr_structure_validators({"structure_validators": selected})

    assert normalized["structure_validators"] == expected


def test_protein_local_validator_selection_defaults_to_protenix_v2() -> None:
    normalized = normalize_plr_structure_validators({})

    assert normalized["structure_validators"] == ["protenix_v2"]


@pytest.mark.parametrize(
    "selected",
    [
        [],
        ["esmfold2", "esmfold2"],
        ["unsupported"],
        "esmfold2",
        ["boltz2", "esmfold2", "protenix_v2", "unsupported"],
    ],
)
def test_protein_local_validator_selection_rejects_invalid_suites(selected: object) -> None:
    with pytest.raises(ValueError, match="structure_validators"):
        normalize_plr_structure_validators({"structure_validators": selected})


def test_experimental_region_redesign_uses_protein_local_review_contract() -> None:
    from types import SimpleNamespace
    from typing import Any, cast

    from routers.jobs import _is_protein_local_redesign_job

    assert _is_protein_local_redesign_job(
        cast(Any, SimpleNamespace(model_id="protein_modification_experimental", mode="region_redesign"))
    )
    assert not _is_protein_local_redesign_job(
        cast(Any, SimpleNamespace(model_id="protein_modification_experimental", mode="de_novo"))
    )


def test_protein_local_validator_selection_rejects_unavailable_models() -> None:
    from fastapi import HTTPException

    from routers.jobs import _validate_plr_validator_availability

    class Registry:
        def get_model(self, model_id: str) -> object | None:
            return object() if model_id in {"esmfold2", "protenix"} else None

    _validate_plr_validator_availability(
        Registry(), {"structure_validators": ["esmfold2", "protenix_v2"]}
    )
    with pytest.raises(HTTPException, match="disabled or unavailable"):
        _validate_plr_validator_availability(Registry(), {"structure_validators": ["boltz2"]})


def test_native_rfd3_command_uses_exact_canonical_execution_controls() -> None:
    module_text = (REPO_ROOT / "modules" / "rfd3.nf").read_text(encoding="utf-8")

    assert "n_batches=${num_designs}" in module_text
    assert "diffusion_batch_size=1" in module_text
    assert "seed=${seed}" in module_text
    assert "dump_trajectories=${dumpTrajectories}" in module_text
    assert "output_full_json=${writeFullJson}" in module_text
    assert (
        "publishDir \"${params.out_dir}/run/rfd3\", mode: 'copy', "
        "pattern: \"rfd3_trajectories\", saveAs: { ignored -> 'trajectories' }"
    ) in module_text
    assert 'pattern: "rfd3_trajectories/*.cif.gz"' not in module_text
    assert "trajectory_inventory.json" not in module_text


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
    design_id = "protein_local_redesign_0"
    runtime_native = dict(request["rfd3"])
    runtime_native["input"] = source.name
    native_json = json.dumps({design_id: runtime_native}, sort_keys=True, separators=(",", ":"))
    native_input = tmp_path / "rfd3_input_protein_local_redesign_0.json"
    native_input.write_text(native_json + "\n", encoding="utf-8")
    receipt.write_text(
        json.dumps(
            {
                "schema": "bms.rfd3.local-redesign.preparation-receipt.v1",
                "request_sha256": request_sha256(request),
                "native_input_sha256": hashlib.sha256(native_json.encode("utf-8")).hexdigest(),
                "runtime_input": {
                    "path": source.name,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
                "design_id": design_id,
                "redesign_mode": request["redesign_mode"],
                "sequence_policy": request["sequence_policy"],
                "sequence_design": {"state": "not_requested"},
                "native_rfd3": runtime_native,
            }
        ),
        encoding="utf-8",
    )
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
    stored_native_input = stored_receipt.parent / native_input.name
    stored_native_input.write_bytes(native_input.read_bytes())
    output = tmp_path / "manifest.json"

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "rfd3_local_redesign" / "build_result_manifest.py"),
            "--request", str(request_path),
            "--cif-file", str(candidate),
            "--json-file", str(metadata),
            "--native-input", str(native_input),
            "--native-input-storage-path", str(stored_native_input),
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
        "native_producer_input",
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


def test_native_rfd3_registry_matches_the_canonical_launch_contract() -> None:
    registry_path = API_ROOT / "config" / "models" / "protein_local_redesign.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    mode_params = set(registry["modes"][0]["params"])
    params = {entry["name"]: entry for entry in registry["params"]}

    assert params["redesign_mode"]["enum"] == ["partial_diffusion", "minimal_insertion"]
    assert "select_fixed_atoms" not in mode_params
    assert "select_unfixed_sequence" not in mode_params
    assert "select_unfixed_sequence" not in params
    assert {
        "design_chains",
        "context_chains",
        "redesign_ranges",
        "sequence_policy",
        "insertion_anchor",
        "insertion_min_length",
        "insertion_max_length",
    }.issubset(mode_params)
    assert "contig" not in mode_params
    assert "write_full_json" not in mode_params


def test_native_rfd3_read_model_does_not_expose_host_storage_paths() -> None:
    from routers.jobs import _rfd3_public_json

    projected = _rfd3_public_json(
        {
            "request_path": "/home/operator/work/rfd3_request.json",
            "input_structure": "/home/operator/inputs/source.pdb",
            "plr_input_pdb": "/home/operator/inputs/source.pdb",
            "native_rfd3": {"input": "/home/operator/inputs/source.pdb"},
            "records": [{"output_path": "/mnt/results/candidate.cif.gz"}],
            "trajectory_paths": ["/mnt/results/noisy.cif.gz", "/mnt/results/denoised.cif.gz"],
            "working_dir": "/tmp/rfd3-work",
            "paths": ["/mnt/results/a.json", "/mnt/results/b.json"],
            "file": "/tmp/native.json",
            "directory": "/tmp/native-output",
            "directories": ["/tmp/native-a", "/tmp/native-b"],
            "filepath": "/tmp/native-file.json",
            "semantic": "/A/50",
        }
    )
    assert projected == {
        "request_path": "rfd3_request.json",
        "input_structure": "source.pdb",
        "plr_input_pdb": "source.pdb",
        "native_rfd3": {"input": "source.pdb"},
        "records": [{"output_path": "candidate.cif.gz"}],
        "trajectory_paths": ["noisy.cif.gz", "denoised.cif.gz"],
        "working_dir": "rfd3-work",
        "paths": ["a.json", "b.json"],
        "file": "native.json",
        "directory": "native-output",
        "directories": ["native-a", "native-b"],
        "filepath": "native-file.json",
        "semantic": "/A/50",
    }

    router_source = (API_ROOT / "routers" / "jobs.py").read_text(encoding="utf-8")
    route_source = router_source.split('@router.get("/{job_id}/rfd3-local-redesign")', 1)[1].split(
        '@router.get("/{job_id}", response_model=JobResponse)', 1
    )[0]

    assert '"request_path_scope": "basename"' in route_source
    assert '"provenance_path_scope": "basename"' in route_source
    assert '"storage_path": row.storage_path' not in route_source
    assert "params=job.params" not in router_source
    assert "params=existing_child.params" not in router_source
    assert "params=first_job.params" not in router_source
    assert "RFD3 local-redesign source artifact path binding is invalid" in router_source


def test_native_rfd3_ingester_requires_exact_candidate_assignment_and_receipt() -> None:
    ingester_source = (API_ROOT / "services" / "result_ingester.py").read_text(encoding="utf-8")

    assert "RFD3 local-redesign candidate artifact is reused" in ingester_source
    assert "RFD3 local-redesign candidate artifact assignment is incomplete" in ingester_source
    assert 'receipt_relative_path != "collected/protein_local_redesign/rfd3_preparation_receipt.json"' in ingester_source
    assert "RFD3 local-redesign preparation receipt is unavailable" in ingester_source
    assert '"native_producer_input"' in ingester_source
    assert "RFD3 local-redesign native producer input path is invalid" in ingester_source
    assert "not source.is_relative_to(data_root)" in ingester_source
