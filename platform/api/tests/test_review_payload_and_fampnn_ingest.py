from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers.designs import _merge_review_payload
from routers.jobs import (
    SavedReviewFilterSet,
    _build_antibody_iteration_job,
    _merge_preserved_gate_payload,
    _prune_iteration_params,
)
from services.result_ingester import _coalesce_cdr_lengths, _extract_fampnn_metrics


def test_review_payload_merge_preserves_saved_filter_sets() -> None:
    gate_payload = {
        "stage": "post_rfantibody",
        "candidate_count": 5000,
    }
    existing_payload = {
        "stage": "post_rfantibody",
        "review_filter_sets": [
            {"id": "saved-1", "name": "Top family", "design_ids": ["d1", "d2"]},
        ],
    }

    merged_router = _merge_preserved_gate_payload(gate_payload, existing_payload)
    merged_designs = _merge_review_payload(gate_payload, existing_payload)

    assert merged_router["review_filter_sets"][0]["id"] == "saved-1"
    assert merged_designs["review_filter_sets"][0]["name"] == "Top family"


def test_extract_fampnn_metrics_reads_sidecar_payload() -> None:
    payload = {
        "sequence": "A:QVQLVESGGGLVQAGGSLRLSCAASGFTFSSYAMSWVRQAPGKGLEWVSAIYSGGSTYYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCAAARGGYYYGMDVWGQGTTVTVS|B:TARGETSEQ",
        "chain_avg_psce": {"A": 0.31, "B": 0.0},
        "fampnn_avg_psce": 0.19,
        "fampnn_max_residue_psce": 1.74,
        "fampnn_min_residue_psce": 0.0,
    }

    metrics = _extract_fampnn_metrics(payload)

    assert metrics["avg_psce"] == 0.19
    assert metrics["chain_avg_psce"] == {"A": 0.31, "B": 0.0}
    assert metrics["binder_length"] == len(payload["sequence"].split("|", 1)[0].split(":", 1)[1])
    assert metrics["sequence"] == payload["sequence"]


def test_coalesce_cdr_lengths_prefers_structure_then_existing_then_lineage() -> None:
    merged = _coalesce_cdr_lengths(
        {"H1": 14},
        {"H1": 13, "H2": 17},
        {"H2": 16, "H3": 28},
    )

    assert merged == {"H1": 14, "H2": 17, "H3": 28}


def test_build_antibody_iteration_job_accepts_saved_review_dataset(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()

    root_job = SimpleNamespace(
        id="root-job",
        name="RBX1 beta large_resumed",
        params={
            "epitope_residues": "A45,A53",
            "antibody_chains": "H",
            "framework_type": "custom",
        },
        pinned_gpu=None,
    )
    source_job = SimpleNamespace(id="source-job")
    saved_filter_set = SavedReviewFilterSet(
        id="saved-1",
        name="elite targeters",
        created_at="2026-03-22T00:00:00Z",
        design_ids=["design-1", "design-2"],
    )

    launch_request = _build_antibody_iteration_job(
        root_job=root_job,
        source_job=source_job,
        action="ppiflow_backbone_refine",
        selection_dir=selection_dir,
        design_ids=["design-1", "design-2"],
        name_suffix=None,
        param_overrides={
            "ppiflow_backbone_region_mode": "selected_cdrs",
            "ppiflow_backbone_loop_scope": "H1",
        },
        saved_filter_set=saved_filter_set,
    )

    assert launch_request.params["selection_source_type"] == "saved_dataset"
    assert launch_request.params["selection_dataset_name"] == "elite targeters"
    assert launch_request.params["run_ppiflow_backbone_refine"] is True
    assert launch_request.params["ppiflow_stage_mode"] == "post_rfantibody"


def test_prune_iteration_params_strips_resume_and_batch_identity() -> None:
    pruned = _prune_iteration_params(
        {
            "job_id": "old-job",
            "batch_name": "legacy-batch",
            "resume_job_id": "resume-job",
            "resume_root_job_id": "resume-root",
            "resume_work_dir": "work",
            "resume_source_dir": "/tmp/original",
            "resume_stage_work_dir": "/tmp/original/work/aa/bb",
            "resume_requested_stage": "post_rfantibody",
            "resume_param_overrides": {"foo": "bar"},
            "resume_from_stage": "structure_validation",
            "resume_name_suffix": "continued",
            "resume_lock_retry_attempts": 3,
            "epitope_residues": "A45,A53",
        }
    )

    for forbidden in (
        "job_id",
        "batch_name",
        "resume_job_id",
        "resume_root_job_id",
        "resume_work_dir",
        "resume_source_dir",
        "resume_stage_work_dir",
        "resume_requested_stage",
        "resume_param_overrides",
        "resume_from_stage",
        "resume_name_suffix",
        "resume_lock_retry_attempts",
    ):
        assert forbidden not in pruned
    assert pruned["epitope_residues"] == "A45,A53"
