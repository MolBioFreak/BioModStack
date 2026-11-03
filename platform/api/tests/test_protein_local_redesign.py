from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException, UploadFile


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.nextflow import (
    build_nextflow_command,
    normalize_plr_input_pdb_path,
    normalize_plr_structure_validators,
    resolve_nextflow_executable,
)
import routers.files as files_router
from scripts.rfd3_local_redesign.contract import ContractError, build_request


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


@pytest.mark.asyncio
async def test_content_addressed_structure_upload_is_atomic_immutable_and_hash_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir()
    monkeypatch.setattr(files_router, "get_allowed_roots", lambda: {"inputs": inputs_root})
    monkeypatch.setattr(files_router, "resolve_allowed_path", files_router._allowed_lexical_path)
    monkeypatch.setattr(
        files_router,
        "to_allowed_relative",
        lambda value: f"inputs/{value.relative_to(inputs_root)}",
    )

    payload = b"ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n"
    digest = hashlib.sha256(payload).hexdigest()
    filename = f"source-{digest}.pdb"
    first = await files_router.upload_immutable_structure(
        path="inputs/protein_local_redesign",
        sha256=digest,
        file=UploadFile(filename=filename, file=io.BytesIO(payload)),
        governed_roots=(),
    )
    stored = inputs_root / "protein_local_redesign" / filename
    assert stored.read_bytes() == payload
    assert first == {
        "filename": filename,
        "path": f"inputs/protein_local_redesign/{filename}",
        "size": len(payload),
        "sha256": digest,
        "existing": False,
    }

    second = await files_router.upload_immutable_structure(
        path="inputs/protein_local_redesign",
        sha256=digest,
        file=UploadFile(filename=filename, file=io.BytesIO(payload)),
        governed_roots=(),
    )
    assert second["existing"] is True

    with pytest.raises(HTTPException, match="filename must end with its SHA-256"):
        await files_router.upload_immutable_structure(
            path="inputs/protein_local_redesign",
            sha256=hashlib.sha256(b"different").hexdigest(),
            file=UploadFile(filename=filename, file=io.BytesIO(b"different")),
            governed_roots=(),
        )
    assert stored.read_bytes() == payload

    wrong_digest = "0" * 64
    wrong_name = f"wrong-{wrong_digest}.pdb"
    with pytest.raises(HTTPException, match="content SHA-256 mismatch"):
        await files_router.upload_immutable_structure(
            path="inputs/protein_local_redesign",
            sha256=wrong_digest,
            file=UploadFile(filename=wrong_name, file=io.BytesIO(payload)),
            governed_roots=(),
        )
    assert not (inputs_root / "protein_local_redesign" / wrong_name).exists()

    with pytest.raises(HTTPException, match="immutable structure upload route"):
        await files_router.upload_file(
            path="inputs/protein_local_redesign",
            file=UploadFile(filename=filename, file=io.BytesIO(b"tampered")),
            governed_roots=(),
        )
    assert stored.read_bytes() == payload


def test_normalize_plr_input_pdb_path_absolutizes_provisioned_relative_path(tmp_path: Path) -> None:
    source = tmp_path / "inputs" / "protein_local_redesign" / "source.pdb"
    params = normalize_plr_input_pdb_path(
        {"input_pdb": "inputs/protein_local_redesign/source.pdb"},
        resolve_relative=lambda rel: (tmp_path / rel).resolve(),
    )
    assert params["input_pdb"] == str(source.resolve())


def test_normalize_plr_input_pdb_path_passes_absolute_paths_through() -> None:
    def must_not_resolve(_rel: str) -> str:
        raise AssertionError("absolute input_pdb must pass through untouched")

    params = normalize_plr_input_pdb_path(
        {"input_pdb": "/abs/source.pdb"},
        resolve_relative=must_not_resolve,
    )
    assert params["input_pdb"] == "/abs/source.pdb"


def test_normalize_plr_input_pdb_path_propagates_resolution_errors() -> None:
    def reject(_rel: str) -> str:
        raise ValueError("Root not allowed: nope")

    with pytest.raises(ValueError, match="Root not allowed"):
        normalize_plr_input_pdb_path({"input_pdb": "nope/x.pdb"}, resolve_relative=reject)


def test_normalize_plr_input_pdb_path_ignores_missing_empty_and_non_string() -> None:
    assert normalize_plr_input_pdb_path({}, resolve_relative=lambda rel: None) == {}
    assert normalize_plr_input_pdb_path(
        {"input_pdb": ""}, resolve_relative=lambda rel: None
    ) == {"input_pdb": ""}
    assert normalize_plr_input_pdb_path(
        {"input_pdb": 5}, resolve_relative=lambda rel: None
    ) == {"input_pdb": 5}




@pytest.mark.parametrize(("model", "mode", "params", "expected"), [
    ("protein_local_redesign", "local_redesign", {}, False),
    ("protein_modification_experimental", "region_redesign", {}, False),
    ("protein_modification_experimental", "de_novo_design", {"generator": "rfd3"}, False),
    ("protein_modification_experimental", "de_novo_design", {}, False),
    ("rfantibody", "antibody_denovo", {}, True),
    ("protein_modification_experimental", "de_novo_design", {"generator": "disco"}, True),
])
def test_ingress_normalization_policy_preserves_native_requests(model, mode, params, expected) -> None:
    from routers.jobs import _should_normalize_antibody_job_params

    original = params.copy()
    # The shared helper owns create/resume normalization policy, not the number
    # or AST spelling of its callers. Include non-RFD3 controls for specificity.
    assert _should_normalize_antibody_job_params(model, mode, params) is expected
    assert params == original




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
    assert request["sequence_policy"] == "insert_only"

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














def test_retired_local_redesign_catalog_preserves_active_region_loop() -> None:
    from services.nextflow import resolve_nextflow_entrypoint

    retired = yaml.safe_load((API_ROOT / "config/models/protein_local_redesign.yaml").read_text())
    assert retired["id"] == "protein_local_redesign"
    assert retired["public_launch"] is False
    assert retired["enabled"] is False
    active = yaml.safe_load((API_ROOT / "config/models/protein_modification_experimental.yaml").read_text())
    assert "region_redesign" in {mode["id"] for mode in active["modes"]}
    assert resolve_nextflow_entrypoint(
        effective_profile="protein_local_redesign",
        model_id="protein_modification_experimental", mode="region_redesign", params={},
    ) == "workflows/protein_local_redesign.nf"
    workflow = (REPO_ROOT / "workflows/protein_local_redesign.nf").read_text()
    assert "workflow PROTEIN_LOCAL_REDESIGN" in workflow
    assert "PROTEIN_LOCAL_REDESIGN()" in workflow


@pytest.mark.parametrize("padding", [0, 3])
def test_build_nextflow_command_maps_active_region_loop_params(padding) -> None:
    params = {
        "input_pdb": "/source/input.pdb", "design_chains": "A", "context_chains": "B",
        "region_padding": padding, "seq_method": "fampnn", "seqs_per_design": 6,
        "fix_fixed_sidechains": False, "interactive_gating": True,
        "interactive_gate_stage": "post_fampnn", "backbone_input_pdbs": "/source/backbones",
        "region_manifest": "/source/manifest.json", "fixed_positions": "A1,A3",
        "designable_positions": "A2", "resequence_sources": [{"private": "custody"}],
        "resequence_preparation": {"private": "custody"},
    }
    cmd = build_nextflow_command("protein_modification_experimental", "region_redesign",
                                 params, "/output", job_id="region-loop")
    assert cmd[1:4] == ["run", "workflows/protein_local_redesign.nf", "-profile"]
    assert "protein_local_redesign,workstation_ryzen7960x" in cmd
    expected = {
        "plr_input_pdb": "/source/input.pdb", "plr_design_chains": "A", "plr_context_chains": "B",
        "plr_region_padding": str(padding), "plr_seq_method": "fampnn", "seqs_per_design": "6",
        "plr_fix_fixed_sidechains": "false", "interactive_gating": "true",
        "interactive_gate_stage": "post_fampnn", "plr_backbone_input_pdbs": "/source/backbones",
        "plr_region_manifest": "/source/manifest.json", "plr_fixed_positions": "A1,A3",
        "plr_designable_positions": "A2", "modification_mode": "region_redesign",
    }
    for key, value in expected.items():
        assert cmd.count("--" + key) == 1
        assert cmd[cmd.index("--" + key) + 1] == value
    for key in ("input_pdb", "design_chains", "context_chains", "region_padding",
                "fixed_positions", "designable_positions", "resequence_sources", "resequence_preparation"):
        assert "--" + key not in cmd


def test_experimental_protein_local_redesign_maps_validator_suite() -> None:
    cmd = build_nextflow_command(
        "protein_modification_experimental",
        "region_redesign",
        {
            "input_pdb": "/tmp/input.pdb",
            "design_chains": "A",
            "structure_validators": ["esmfold2", "protenix_v2"],
            "msa_provider": "colabfold_api",
        },
        "/tmp/out",
        job_id="job-123",
    )

    joined = " ".join(cmd)
    assert "--plr_structure_validators esmfold2,protenix_v2" in joined
    assert "--plr_validator_suite_active true" in joined
    assert "--protenix_msa_backend colabfold_api" in joined


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


@pytest.mark.parametrize(("total", "batch_size", "batches"), [
    (3, 1, [(3, 1)]), (6, 3, [(2, 3)]), (7, 3, [(2, 3), (1, 1)]),
])
def test_native_rfd3_command_uses_exact_canonical_execution_controls(total, batch_size, batches) -> None:
    from scripts.rfd3_generation.run_native import commands

    execution = {"diffusion_batch_size": batch_size, "seed": 0, "dump_trajectories": False,
                 "write_full_json": True}
    actual = commands({"generation": {"num_designs": total}, "execution": execution},
                      "input with spaces.json", "output dir")
    expected = []
    for index, (count, size) in enumerate(batches):
        command = ["rfd3", "inputs=input with spaces.json", "out_dir=output dir",
                   f"n_batches={count}", f"diffusion_batch_size={size}",
                   "dump_trajectories=false", "output_full_json=true", "seed=0"]
        if len(batches) > 1:
            command.append(f"global_prefix=bms_part{index}_")
        expected.append(command)
    assert actual == expected
    module = (REPO_ROOT / "modules/rfd3.nf").read_text()
    assert "def nativeRequest = localRedesignRequest || generalGenerationRequest" in module
    assert "python3 /scripts/rfd3_generation/run_native.py --request" in module
    assert """pattern: "rfd3_trajectories", saveAs: { ignored -> 'trajectories' }""" in module
    assert 'pattern: "rfd3_trajectories/*.cif.gz"' not in module
    assert "trajectory_inventory.json" not in module


# The retired local-redesign manifest producer was deleted intentionally.
# Current generation custody is exercised by test_rfd3_generation_contract.py
# (hash-bound manifest, tamper rejection, ingestion and typed reopening).


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
            "--sequence_redesign_ranges",
            "A2",
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
    assert manifest["sequence_redesign_positions_spec"] == "A2"
    assert "A1" not in manifest["fixed_positions_spec"]
    assert "A1" in manifest["sequence_fixed_positions_spec"]
    assert "A3" in manifest["fixed_positions_spec"]
    assert "B1" in manifest["fixed_positions_spec"]
    assert "2-2" in manifest["contig_spec"]
    assert "ATOM" in seed_text
    assert " B " not in seed_text

    mpnn_input = tmp_path / "mpnn_input"
    mpnn_output = tmp_path / "mpnn_output"
    mpnn_input.mkdir()
    (mpnn_input / "candidate.pdb").write_text(seed_text, encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "add_fixed_labels_from_spec.py"),
            "--input_dir",
            str(mpnn_input),
            "--output_dir",
            str(mpnn_output),
            "--manifest",
            str(manifest_path),
        ],
        check=True,
    )
    labels_by_residue: dict[int, set[float]] = {}
    for line in (mpnn_output / "candidate.pdb").read_text(encoding="utf-8").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        labels_by_residue.setdefault(int(line[22:26]), set()).add(float(line[60:66]))
    assert labels_by_residue == {1: {10.0}, 2: {10.0}, 3: {10.0}}
    from scripts.proteinmpnn_native_binding import ROLE_REMARK

    output_text = (mpnn_output / "candidate.pdb").read_text()
    role_lines = [line[len(ROLE_REMARK):] for line in output_text.splitlines()
                  if line.startswith(ROLE_REMARK)]
    assert len(role_lines) == 1
    roles = json.loads(role_lines[0])
    assert roles["designed_chains"] == ["A"]
    assert roles["fixed_chains"] == []
    assert roles["source_residues"] == [["A", 1, ""], ["A", 2, ""], ["A", 3, ""]]
    assert roles["fixed_positions"] == [["A", 1, ""], ["A", 3, ""]]
    assert roles["source_pdb_sha256"] == hashlib.sha256(seed_text.encode()).hexdigest()
    assert [line for line in output_text.splitlines() if line.startswith(("ATOM", "HETATM"))] == [
        line for line in seed_text.splitlines() if line.startswith(("ATOM", "HETATM"))
    ]








def test_plr_profile_does_not_override_scheduler_gpu_concurrency() -> None:
    rendered = subprocess.run(
        [
            resolve_nextflow_executable(),
            "config",
            "-profile",
            "protein_local_redesign,workstation_ryzen7960x",
            "-flat",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "process.'withName:ESMFold2FromPdb'.maxForks" not in rendered
    assert "process.'withName:ProtenixFromComplex'.maxForks" not in rendered
    assert "params.gpu_id" in rendered
    assert "params.num_parallel_jobs" in rendered
