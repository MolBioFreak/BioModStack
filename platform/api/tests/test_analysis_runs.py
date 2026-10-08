from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.analysis_registry import (
    ANTIBODY_ANNOTATION_PACK_ANALYSIS,
    CHAIN_METRICS_ANALYSIS,
    CONTACT_MAP_ANALYSIS,
    JOB_AA_COMPOSITION_ANALYSIS,
    JOB_CDR_LOGO_PACK_ANALYSIS,
    JOB_CORRELATION_MATRIX_ANALYSIS,
    PAE_MATRIX_ANALYSIS,
    STRUCTURE_SUMMARY_ANALYSIS,
    get_analysis_definition,
    normalize_contact_map_params,
    normalize_job_scope_params,
    normalize_pae_matrix_params,
)
from services.analysis_subprocess import _extract_metric_pairs
from services.analysis_runs import build_artifact_manifest_for_run, serialize_analysis_run


def test_phase1_analysis_definitions_exist() -> None:
    assert get_analysis_definition(STRUCTURE_SUMMARY_ANALYSIS) is not None
    assert get_analysis_definition(CONTACT_MAP_ANALYSIS) is not None
    assert get_analysis_definition(CHAIN_METRICS_ANALYSIS) is not None
    assert get_analysis_definition(PAE_MATRIX_ANALYSIS) is not None
    assert get_analysis_definition(ANTIBODY_ANNOTATION_PACK_ANALYSIS) is not None
    assert get_analysis_definition(JOB_CORRELATION_MATRIX_ANALYSIS) is not None
    assert get_analysis_definition(JOB_AA_COMPOSITION_ANALYSIS) is not None
    assert get_analysis_definition(JOB_CDR_LOGO_PACK_ANALYSIS) is not None


def test_contact_map_params_are_clamped() -> None:
    assert normalize_contact_map_params({"max_size": 12})["max_size"] == 50
    assert normalize_contact_map_params({"max_size": 900})["max_size"] == 500
    assert normalize_contact_map_params({"max_size": "300"})["max_size"] == 300
    assert normalize_contact_map_params({})["max_size"] == 300


def test_pae_params_and_job_scope_are_normalized() -> None:
    assert normalize_pae_matrix_params({"max_size": 12})["max_size"] == 50
    assert normalize_pae_matrix_params({"max_size": 900})["max_size"] == 500
    assert normalize_job_scope_params({
        "include_children": "false",
        "design_ids": [" b ", "a", "b", ""],
    }) == {
        "include_children": False,
        "design_ids": ["a", "b"],
    }


def test_analysis_artifact_manifest_uses_analysis_cache_root() -> None:
    previous_bms_data = os.environ.get("BMS_DATA")
    with TemporaryDirectory() as tmpdir:
        os.environ["BMS_DATA"] = tmpdir
        run = SimpleNamespace(
            id="run-123",
            subject_kind="design",
            subject_id="design-abc",
            analysis_type="structure_summary",
            cache_key="cache-xyz",
        )
        manifest = build_artifact_manifest_for_run(run)
        assert str(manifest["cache_dir"]).startswith("analysis_cache/")
        assert str(manifest["result_json"]).endswith("/result.json")
        assert str(manifest["summary_json"]).endswith("/summary.json")
    if previous_bms_data is None:
        os.environ.pop("BMS_DATA", None)
    else:
        os.environ["BMS_DATA"] = previous_bms_data


def test_serialize_analysis_run_missing_returns_missing_status() -> None:
    payload = serialize_analysis_run(
        None,
        analysis_type="structure_summary",
        subject_kind="design",
        subject_id="design-abc",
        params={},
    )

    assert payload["status"] == "missing"
    assert payload["run_id"] is None
    assert payload["result"] is None


def test_job_correlation_pairs_are_aligned_by_design() -> None:
    designs = [
        SimpleNamespace(id="d1", plddt_overall=92.0, pae_overall=1.5),
        SimpleNamespace(id="d2", plddt_overall=88.0, pae_overall=None),
        SimpleNamespace(id="d3", plddt_overall=None, pae_overall=3.2),
        SimpleNamespace(id="d4", plddt_overall=74.0, pae_overall=6.1),
    ]

    assert _extract_metric_pairs(designs, "plddt_overall", "pae_overall") == [
        (92.0, 1.5),
        (74.0, 6.1),
    ]
