from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers.designs import _merge_review_payload
from routers.jobs import _merge_preserved_gate_payload
from services.result_ingester import _extract_fampnn_metrics


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
