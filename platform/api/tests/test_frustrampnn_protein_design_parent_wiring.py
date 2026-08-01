from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / "workflows" / "protein_design.nf"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _prepare_module():
    path = REPO_ROOT / "scripts" / "prepare_frustrampnn_candidate.py"
    spec = importlib.util.spec_from_file_location(
        "prepare_frustrampnn_candidate_protein_design_test", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdb(serial: int = 1) -> bytes:
    return (
        f"ATOM  {serial:5d}  N   GLY A   1      11.000  12.000  13.000  1.00 20.00           N  \n"
        f"ATOM  {serial + 1:5d}  CA  GLY A   1      12.000  12.000  13.000  1.00 20.00           C  \n"
        f"ATOM  {serial + 2:5d}  C   GLY A   1      13.000  12.000  13.000  1.00 20.00           C  \n"
        f"ATOM  {serial + 3:5d}  O   GLY A   1      14.000  12.000  13.000  1.00 20.00           O  \n"
        "END\n"
    ).encode()


def test_protein_design_has_one_canonical_component_and_no_legacy_owner() -> None:
    workflow = _workflow()

    assert "include { CanonicalFrustraMPNN } from '../modules/frustrampnn.nf'" in workflow
    assert workflow.count("CanonicalFrustraMPNN(") == 1
    assert "FrustrampnnQC" not in workflow
    assert "AggregateFrustrationReports" not in workflow
    assert "terminal_designs" in workflow
    assert "parent_workflow_id: 'protein_design'" in workflow
    assert "frustrampnn_results = CanonicalFrustraMPNN.out.result" in workflow
    assert re.search(r"emit:\s+final_structures\s+terminal_designs\s+frustrampnn_results", workflow)
    assert ".subscribe" not in workflow
    assert "workflow.onError" not in workflow
    assert "errorStrategy 'ignore'" not in workflow


def test_every_terminal_branch_contributes_typed_designs_without_placeholder() -> None:
    workflow = _workflow()
    required_branch_authorities = {
        "early_sequence_prediction",
        "rfd3_only",
        "af2_terminal",
        "boltz_terminal",
        "rf3_terminal",
        "protenix_terminal",
        "boltzgen_direct",
        "boltzgen_child",
        "skip_rfd",
        "skip_rfd_seq",
        "analysis_import",
    }
    for branch in required_branch_authorities:
        assert f"producer_branch: '{branch}'" in workflow

    canonical_block = workflow.split("CanonicalFrustraMPNN(", 1)[0]
    assert "terminal_designs" in canonical_block
    assert "protein_design:no_candidates" in workflow
    assert "placeholder.pdb" not in workflow
    assert "predicted.baseName" not in canonical_block
    assert ".withIndex()" not in canonical_block
    assert "producer_sample: artifactDigest" not in canonical_block
    assert "producer_rank: artifactDigest" not in canonical_block


def test_plain_pdb_projection_and_scheduler_owned_terminal_reporting() -> None:
    workflow = _workflow()

    assert "terminal_designs.map { candidate_meta, structure -> structure }" in workflow
    assert "PrepareProteinDesignFrustraMPNNCandidate" in workflow
    assert "PublishProteinDesignFrustraMPNNCandidate" in workflow
    assert "ReportProteinDesignFrustraMPNNNotRequested" in workflow
    assert "ReportProteinDesignFrustraMPNNComplete" in workflow
    assert "params.frustrampnn_requiredness ?: 'required'" in workflow
    assert "frustrampnn_requiredness must be required" in workflow
    assert "frustrampnn not_requested" in workflow
    assert "publish_frustrampnn_bundle.py" in workflow
    assert "frustrampnn complete" in workflow
    assert "test \\\"\\${#outputs[@]}\\\" -gt 0" in workflow


def test_duplicate_bytes_basenames_and_reordering_keep_producer_identity(tmp_path: Path) -> None:
    module = _prepare_module()
    first_dir = tmp_path / "producer-a"
    second_dir = tmp_path / "producer-b"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "candidate.pdb"
    second = second_dir / "candidate.pdb"
    first.write_bytes(_pdb())
    second.write_bytes(_pdb())
    digest = hashlib.sha256(first.read_bytes()).hexdigest()

    def identity(method: str, output_key: str, sample: str) -> dict[str, object]:
        fields = {
            "producer_method": method,
            "producer_sample": sample,
            "producer_rank": None,
            "producer_output_key": output_key,
        }
        return {
            **fields,
            "producer_identity_sha256": module.producer_identity_sha256(fields),
            "producer_artifact_sha256": digest,
            "source_format": "pdb",
        }

    identities = [
        identity("af2", "fold-a/candidate.pdb", "fold-a"),
        identity("af2", "fold-b/candidate.pdb", "fold-b"),
    ]
    forward = [item["producer_identity_sha256"] for item in identities]
    reverse = [item["producer_identity_sha256"] for item in reversed(identities)]
    assert len(set(forward)) == 2
    assert set(forward) == set(reverse)
    assert all(item["producer_rank"] is None for item in identities)
    assert all(item["producer_artifact_sha256"] == digest for item in identities)

    metadata = {
        "parent_job_id": "job-protein-design",
        "parent_workflow_id": "protein_design",
        "producer_stage": "protein_design:af2_terminal",
        "producer_candidate_key": f"frustrampnn/sources/af2/{forward[0]}/canonical.pdb",
        "requiredness": "required",
        "checkpoint_id": "megascale.ckpt",
        **identities[0],
    }
    encoded = base64.b64encode(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    decoded = module._decode_metadata(encoded, source=first)
    assert decoded["parent_workflow_id"] == "protein_design"
    assert decoded["candidate_id"] != identities[1]["producer_identity_sha256"]


def test_direct_batch_runner_and_owner_predicate_are_retired() -> None:
    from services import nextflow as module

    retired_names = (
        "maybe_trigger_batch_" + "frustrampnn",
        "run_batch_" + "frustrampnn",
        "_is_canonical_protein_design_" + "batch",
    )
    assert all(not hasattr(module, name) for name in retired_names)
