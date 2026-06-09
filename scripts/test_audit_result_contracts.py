from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from audit_result_contracts import summarize_design_contracts


def test_summarize_design_contracts_counts_result_sets_contracts_and_unsupported_rows() -> None:
    summary = summarize_design_contracts([
        {
            "id": "seq-1",
            "stage_family": "fampnn",
            "artifact_class": "sequence_designed_complex",
            "result_set": "sequence_designs",
            "analysis_contract_id": "sequence_design_v1",
            "supported_analyzers": ["sequence_design_v1"],
        },
        {
            "id": "ppiflow-1",
            "stage_family": "ppiflow",
            "artifact_class": "sequence_designed_complex",
            "result_set": "ppiflow_passed",
            "analysis_contract_id": "ppiflow_maturation_v1",
            "supported_analyzers": ["ppiflow_maturation_v1"],
        },
        {
            "id": "unknown-1",
            "stage_family": "new_model",
            "artifact_class": "novel_complex",
            "result_set": None,
            "analysis_contract_id": None,
            "supported_analyzers": [],
        },
    ])

    assert summary["total"] == 3
    assert summary["by_result_set"] == {"sequence_designs": 1, "ppiflow_passed": 1, "unsupported": 1}
    assert summary["by_contract"] == {"sequence_design_v1": 1, "ppiflow_maturation_v1": 1, "unsupported": 1}
    assert summary["by_stage_family"] == {"fampnn": 1, "ppiflow": 1, "new_model": 1}
    assert summary["unsupported_rows"] == [
        {
            "id": "unknown-1",
            "name": None,
            "stage_family": "new_model",
            "artifact_class": "novel_complex",
            "result_set": None,
        }
    ]
