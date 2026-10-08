from __future__ import annotations

from pathlib import Path

import pytest

import routers.ont_runs as ont_runs
from routers.ont_runs import OntNgsSubmitRequest, _job_create_for_ont_submit
from services.ont_ngs_contract import CANONICAL_ONT_WORKFLOWS


@pytest.fixture(autouse=True)
def _unit_contract_paths_are_prevalidated(monkeypatch):
    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, _label, **_kwargs: str(value))


@pytest.mark.parametrize(
    "field",
    [
        "wf_clone_source",
        "wf_clone_revision",
        "wf_clone_workflow_dir",
        "wf_clone_profile",
        "wf_clone_lock_manifest",
        "wf_clone_runtime_provenance",
    ],
)
def test_wf_clone_runtime_assets_are_server_controlled(field: str) -> None:
    with pytest.raises(ValueError, match="server-controlled.*runtime"):
        _job_create_for_ont_submit(
            "wf_clone_validation",
            OntNgsSubmitRequest(
                params={
                    "fastq_path": "/tmp/reads.fastq",
                    "reference_fasta": "/tmp/reference.fasta",
                    field: "/caller/value",
                }
            ),
        )


def test_wf_clone_declares_p3_adapter_and_provenance_artifacts() -> None:
    kinds = set(CANONICAL_ONT_WORKFLOWS["wf_clone_validation"].artifact_kinds)
    assert {"clone_validation_adapter", "clone_validation_runtime_provenance", "construct_verification"} <= kinds


def test_wf_clone_exact_user_selections_are_preserved_and_unsupported_values_rejected() -> None:
    job = _job_create_for_ont_submit(
        "wf_clone_validation",
        OntNgsSubmitRequest(
            params={
                "fastq_path": "/tmp/reads.fastq",
                "reference_fasta": "/tmp/reference.fasta",
                "wf_clone_assembly_tool": "canu",
                "wf_clone_basecaller_model": "dna_r10.4.1_e8.2_400bps_hac@v5.0.0",
            }
        ),
    )
    assert job.params["wf_clone_assembly_tool"] == "canu"
    assert job.params["wf_clone_basecaller_model"] == "dna_r10.4.1_e8.2_400bps_hac@v5.0.0"

    for field, value in (
        ("wf_clone_assembly_tool", "spades"),
        ("wf_clone_basecaller_model", "dna_r10.4.1_e8.2_400bps_fast@v5.0.0"),
    ):
        with pytest.raises(ValueError, match="exact"):
            _job_create_for_ont_submit(
                "wf_clone_validation",
                OntNgsSubmitRequest(
                    params={
                        "fastq_path": "/tmp/reads.fastq",
                        "reference_fasta": "/tmp/reference.fasta",
                        field: value,
                    }
                ),
            )


def test_frontend_does_not_emit_or_display_mutable_wf_clone_runtime_controls() -> None:
    root = Path(__file__).resolve().parents[3]
    for relative in (
        "platform/frontend/src/components/NanoporeTemplate.tsx",
        "platform/frontend/src/components/NGSToolkit.tsx",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        for token in ("wf_clone_source", "wf_clone_revision", "wf_clone_workflow_dir", "wf_clone_profile"):
            assert token not in source
