from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import paths
from services.analysis_registry import (
    ANTIBODY_ANNOTATION_PACK_ANALYSIS,
    CHAIN_METRICS_ANALYSIS,
    CONTACT_MAP_ANALYSIS,
    FAMPNN_PSCE_PROFILE_ANALYSIS,
    JOB_AA_COMPOSITION_ANALYSIS,
    JOB_CDR_LOGO_PACK_ANALYSIS,
    JOB_CORRELATION_MATRIX_ANALYSIS,
    PAE_MATRIX_ANALYSIS,
    STRUCTURE_SUMMARY_ANALYSIS,
    IPSAE_INTERFACE_ANALYSIS,
    _structure_fingerprint,
    get_analysis_definition,
    normalize_contact_map_params,
    normalize_job_scope_params,
    normalize_pae_matrix_params,
)
from services.analysis_autorun import _viewer_minimum_analysis_types
from services.aligned_error_utils import (
    detect_aligned_error_artifact,
    fingerprint_aligned_error_artifact,
    load_structure_residue_records,
)
from services.analysis_subprocess import _extract_metric_pairs
from services.analysis_runs import build_artifact_manifest_for_run, serialize_analysis_run
from services.result_ingester import _compute_validation_geometry_fields, _validation_role_fields


def test_phase1_analysis_definitions_exist() -> None:
    assert get_analysis_definition(STRUCTURE_SUMMARY_ANALYSIS) is not None
    assert get_analysis_definition(CONTACT_MAP_ANALYSIS) is not None
    assert get_analysis_definition(CHAIN_METRICS_ANALYSIS) is not None
    assert get_analysis_definition(FAMPNN_PSCE_PROFILE_ANALYSIS) is not None
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


def test_generic_complex_jobs_infer_target_and_binder_chains() -> None:
    job = SimpleNamespace(
        model_id="protenix",
        mode="complex",
        params={
            "complex_components": [
                {"type": "protein", "id": "A", "sequence": "AAAA"},
                {"type": "ion", "id": "B", "ccd": "ZN"},
                {"type": "protein", "id": "E", "sequence": "BBBB"},
            ]
        },
    )

    assert _validation_role_fields(job, job.params) == {
        "detected_antibody_chains": "E",
        "detected_target_chain": "A",
    }


def test_viewer_minimum_bundle_includes_expected_complex_antibody_analyses() -> None:
    job = SimpleNamespace(model_id="boltz2", mode="complex", name="TdT nanobody")
    design = SimpleNamespace(
        aligned_error_path="/tmp/aligned_error.json",
        aligned_error_format="json",
        fampnn_psce=0.12,
    )

    assert set(_viewer_minimum_analysis_types(job, design)) == {
        STRUCTURE_SUMMARY_ANALYSIS,
        CHAIN_METRICS_ANALYSIS,
        IPSAE_INTERFACE_ANALYSIS,
        ANTIBODY_ANNOTATION_PACK_ANALYSIS,
        FAMPNN_PSCE_PROFILE_ANALYSIS,
    }


def test_viewer_minimum_bundle_for_plain_monomer_stays_small() -> None:
    job = SimpleNamespace(model_id="boltz2", mode="single", name="structure_prediction")
    design = SimpleNamespace(
        aligned_error_path=None,
        aligned_error_format=None,
        fampnn_psce=None,
    )

    assert _viewer_minimum_analysis_types(job, design) == [
        STRUCTURE_SUMMARY_ANALYSIS,
        CHAIN_METRICS_ANALYSIS,
    ]


def test_validation_geometry_without_epitope_only_persists_target_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.result_ingester.compute_contact_geometry_metrics",
        lambda **_kwargs: {
            "detected_antibody_chains": "B",
            "detected_target_chain": "A",
            "target_contact_count": 17,
            "target_min_distance": 3.4,
            "epitope_contact_count": 6,
            "epitope_min_distance": 7.2,
        },
    )

    metrics = _compute_validation_geometry_fields(
        structure_path=Path("/tmp/demo.pdb"),
        job_params={},
        detected_antibody_chains="B",
        detected_target_chain="A",
        epitope_residues=None,
    )

    assert metrics["target_contact_count"] == 17
    assert metrics["target_min_distance"] == 3.4
    assert "epitope_contact_count" not in metrics
    assert "epitope_min_distance" not in metrics


def test_load_structure_residue_records_handles_cif_headers_with_trailing_spaces() -> None:
    cif_text = """data_test
#
loop_
_atom_site.group_PDB 
_atom_site.id 
_atom_site.type_symbol 
_atom_site.label_atom_id 
_atom_site.label_comp_id 
_atom_site.label_asym_id 
_atom_site.label_entity_id 
_atom_site.label_seq_id 
_atom_site.Cartn_x 
_atom_site.Cartn_y 
_atom_site.Cartn_z 
_atom_site.auth_asym_id 
ATOM 1 C CA ALA A 1 1 1.0 2.0 3.0 A
ATOM 2 C CB ALA A 1 1 1.5 2.5 3.5 A
#
"""
    with TemporaryDirectory() as tmpdir:
        cif_path = Path(tmpdir) / "test.cif"
        cif_path.write_text(cif_text, encoding="utf-8")
        residues, token_mask = load_structure_residue_records(cif_path)

    assert len(residues) == 1
    assert residues[0].chain_id == "A"
    assert residues[0].residue_name == "ALA"
    assert token_mask.tolist() == [True]


def test_detect_aligned_error_artifact_prefers_summary_relative_sidecar_path() -> None:
    with TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        structure_path = base / "design_protenixpred.pdb"
        summary_path = base / "design_protenixpred.json"
        aligned_error_path = base / "aligned_error" / "design_full_data_sample_0.json"

        structure_path.write_text("MODEL\nENDMDL\n", encoding="utf-8")
        aligned_error_path.parent.mkdir(parents=True, exist_ok=True)
        aligned_error_path.write_text('{"token_pair_pae": [[0.0]]}', encoding="utf-8")
        summary_path.write_text(
            '{"aligned_error_artifact":"aligned_error/design_full_data_sample_0.json","aligned_error_format":"protenix_full_json"}',
            encoding="utf-8",
        )

        detected = detect_aligned_error_artifact(
            structure_path=structure_path,
            summary_json_path=summary_path,
        )

    assert detected is not None
    artifact_path, artifact_format, artifact_key = detected
    assert artifact_path == aligned_error_path.resolve()
    assert artifact_format == "protenix_full_json"
    assert artifact_key == "token_pair_pae"


def test_structure_fingerprint_rewrites_legacy_host_paths_to_active_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy_root = (tmp_path / "host-data").resolve()
    active_root = (tmp_path / "runtime-data").resolve()
    runtime_file = active_root / "bms_results" / "job-legacy" / "model_0.pdb"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("MODEL\nENDMDL\n", encoding="utf-8")
    legacy_path = legacy_root / "bms_results" / "job-legacy" / "model_0.pdb"

    monkeypatch.setattr(paths, "get_data_root", lambda: active_root)
    monkeypatch.setattr(paths, "_candidate_data_roots", lambda: [legacy_root])
    monkeypatch.setattr(paths, "_runtime_paths", lambda: {"container_state_path": str(active_root)})

    fingerprint = _structure_fingerprint(str(legacy_path))

    assert fingerprint["path"] == str(runtime_file)
    assert fingerprint["size"] == runtime_file.stat().st_size


def test_aligned_error_fingerprint_rewrites_legacy_host_paths_to_active_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy_root = (tmp_path / "host-data").resolve()
    active_root = (tmp_path / "runtime-data").resolve()
    runtime_artifact = active_root / "bms_results" / "job-legacy" / "confidence_model_0.json"
    runtime_artifact.parent.mkdir(parents=True)
    runtime_artifact.write_text('{"pae": [[1.0]]}', encoding="utf-8")
    legacy_artifact = legacy_root / "bms_results" / "job-legacy" / "confidence_model_0.json"

    monkeypatch.setattr(paths, "get_data_root", lambda: active_root)
    monkeypatch.setattr(paths, "_candidate_data_roots", lambda: [legacy_root])
    monkeypatch.setattr(paths, "_runtime_paths", lambda: {"container_state_path": str(active_root)})

    fingerprint = fingerprint_aligned_error_artifact(
        aligned_error_path=str(legacy_artifact),
        aligned_error_format="confidence_json",
        matrix_key="pae",
    )

    assert fingerprint["path"] == str(runtime_artifact)
    assert fingerprint["size"] == runtime_artifact.stat().st_size
