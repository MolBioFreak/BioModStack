from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers.designs import (
    _compute_fampnn_response_metrics,
    _merge_review_payload,
    _should_force_review_stage_listing,
)
from routers.jobs import (
    SavedReviewFilterSet,
    _build_antibody_iteration_job,
    _build_selection_manifest_item,
    _child_job_has_reusable_outputs,
    _derive_source_stage_payload,
    _merge_preserved_gate_payload,
    _normalize_antibody_job_params,
    _prune_iteration_params,
)
from services.result_ingester import (
    _apply_ppiflow_filter_fields,
    _apply_ppiflow_score_fields,
    _candidate_source_design_ids,
    _coalesce_cdr_lengths,
    _discover_collected_ppiflow_structures,
    _extract_fampnn_metrics,
    _inherit_source_design_metrics,
    _parse_ppiflow_sample_index,
    parse_backbone_id,
)
from services.stage_review import refresh_gate_payload
from services.structure_utils import get_per_chain_fampnn_psce


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


def test_refresh_gate_payload_recovers_rfantibody_review_from_run_outputs(tmp_path: Path) -> None:
    raw_output_dir = tmp_path / "run" / "rfantibody" / "output"
    raw_output_dir.mkdir(parents=True)
    (raw_output_dir / "antibody_job_gpu0_0.pdb").write_text(
        "ATOM      1  CA  GLY H   1       0.000   0.000   0.000  1.00 50.00           C\nEND\n",
        encoding="utf-8",
    )
    screen_dir = tmp_path / "run" / "rfantibody_screen"
    screen_dir.mkdir(parents=True)
    (screen_dir / "screening_summary.json").write_text(
        """
        {
          "results": [
            {
              "design_name": "antibody_job_gpu0_0",
              "passed_screen": true,
              "screening_reason": "passed"
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    repaired = refresh_gate_payload(
        {
            "stage": "post_rfantibody",
            "candidate_dir": "bms_results/example/collected/rfantibody_filtered",
            "raw_dir": "bms_results/example/collected/rfantibody",
            "filtered_dir": "bms_results/example/collected/rfantibody_filtered",
        },
        str(tmp_path),
    )

    assert repaired["raw_candidate_count"] == 1
    assert repaired["filtered_candidate_count"] == 1
    assert repaired["candidate_count"] == 1
    assert str(raw_output_dir.resolve()) == repaired["raw_dir"]
    assert repaired["candidate_dir"] == repaired["raw_dir"]
    assert repaired["candidate_backbone_summary"]["assigned_total"] == 1
    assert repaired["candidate_backbone_summary"]["backbones"]["0"]["count"] == 1


def test_child_job_reuse_requires_real_ppiflow_outputs(tmp_path: Path) -> None:
    child_dir = tmp_path / "run"
    results_dir = child_dir / "run" / "ppiflow" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "demo_enriched_complex.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")

    child = SimpleNamespace(child_stage="maturation", output_dir=str(child_dir))
    assert not _child_job_has_reusable_outputs(child)

    (results_dir / "demo_ppiflow_seq_0_sample0.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")
    assert _child_job_has_reusable_outputs(child)


def test_discover_collected_ppiflow_structures_includes_redesign_outputs(tmp_path: Path) -> None:
    stage_dir = tmp_path / "collected" / "maturation"
    stage_dir.mkdir(parents=True)
    (stage_dir / "demo_ppiflow_seq_7_sample0.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")
    (stage_dir / "demo_enriched_complex.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")

    discovered = _discover_collected_ppiflow_structures(tmp_path)

    assert discovered == [("maturation", stage_dir / "demo_ppiflow_seq_7_sample0.pdb")]


def test_parse_ppiflow_sample_index_supports_redesign_names() -> None:
    assert _parse_ppiflow_sample_index("demo_ppiflow_sample4") == 4
    assert _parse_ppiflow_sample_index("demo_ppiflow_seq_7_sample0") == 0


def test_non_ppiflow_child_reuse_does_not_require_output_scan() -> None:
    child = SimpleNamespace(child_stage="structure_validation", output_dir=None)
    assert _child_job_has_reusable_outputs(child)


def test_parse_backbone_id_supports_rfantibody_gpu_job_names() -> None:
    assert parse_backbone_id("antibody_job_gpu0_0") == 0
    assert parse_backbone_id("antibody_job_gpu0_99") == 99
    assert parse_backbone_id("001_antibody_job_gpu0_7") == 7


def test_should_force_review_stage_listing_for_awaiting_review_parent() -> None:
    assert _should_force_review_stage_listing(
        SimpleNamespace(awaiting_input=True),
        "post_fampnn",
    )
    assert not _should_force_review_stage_listing(
        SimpleNamespace(awaiting_input=False),
        "post_fampnn",
    )
    assert not _should_force_review_stage_listing(
        SimpleNamespace(awaiting_input=True),
        None,
    )


def test_discover_collected_ppiflow_structures_ignores_enriched_intermediates(tmp_path: Path) -> None:
    stage_dir = tmp_path / "collected" / "backbone_refine"
    stage_dir.mkdir(parents=True)
    (stage_dir / "001_demo_enriched_complex.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")
    (stage_dir / "001_demo_ppiflow_sample0.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")
    (stage_dir / "001_demo_ppiflow_sample1.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")

    discovered = _discover_collected_ppiflow_structures(tmp_path)

    assert [(stage, path.name) for stage, path in discovered] == [
        ("backbone_refine", "001_demo_ppiflow_sample0.pdb"),
        ("backbone_refine", "001_demo_ppiflow_sample1.pdb"),
    ]


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


def test_candidate_source_design_ids_extracts_upstream_design_ids() -> None:
    assert _candidate_source_design_ids("job0_001_3c29fb38-2865-423d-a7a9-5beae79751cf_seq_0") == [
        "3c29fb38-2865-423d-a7a9-5beae79751cf",
    ]
    assert _candidate_source_design_ids("001_3c29fb38-2865-423d-a7a9-5beae79751cf_seq_0") == [
        "3c29fb38-2865-423d-a7a9-5beae79751cf",
    ]
    assert _candidate_source_design_ids("006_3a181a7c-8ae8-4ea0-bf91-e2c64d2ef859") == [
        "3a181a7c-8ae8-4ea0-bf91-e2c64d2ef859",
    ]


def test_inherit_source_design_metrics_copies_geometry_and_cdr_lengths(tmp_path: Path) -> None:
    pdb_path = tmp_path / "fampnn_inherit_source_metrics.pdb"
    pdb_path.write_text(
        "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00  1.00           C\n"
        "ATOM      2  CA  GLY A   2       2.000   0.000   0.000  1.00  1.00           C\n"
        "END\n"
    )

    design = SimpleNamespace(
        binder_length=159,
        antibody_type=None,
        humanness_score=None,
        cdr_h1_length=None,
        cdr_h2_length=None,
        cdr_h3_length=None,
        cdr_l1_length=None,
        cdr_l2_length=None,
        cdr_l3_length=None,
        fr2_contacts=None,
        de_loop=None,
        fr3_contacts=None,
        fr4_contacts=None,
        rfd_rog=None,
        passed_screen=None,
        rfa_hotspot_covered_count=None,
        epitope_contact_count=None,
        epitope_min_distance=None,
        epitope_min_atom_distance=None,
        epitope_nearest_antibody_residue=None,
        epitope_nearest_target_residue=None,
        epitope_nearest_antibody_atom=None,
        epitope_nearest_target_atom=None,
        epitope_mapping_mode=None,
        epitope_centroid_distance=None,
        target_contact_count=None,
        target_min_distance=None,
        target_min_atom_distance=None,
        target_nearest_antibody_residue=None,
        target_nearest_target_residue=None,
        target_nearest_antibody_atom=None,
        target_nearest_target_atom=None,
        target_centroid_distance=None,
        detected_antibody_chains=None,
        detected_target_chain=None,
        antibody_residue_count=None,
        target_residue_count=None,
        epitope_residue_count=None,
        rog=None,
    )
    source_design = SimpleNamespace(
        binder_length=159,
        antibody_type="vhh",
        humanness_score=0.81,
        cdr_h1_length=20,
        cdr_h2_length=20,
        cdr_h3_length=30,
        cdr_l1_length=10,
        cdr_l2_length=11,
        cdr_l3_length=12,
        fr2_contacts="37,42,44",
        de_loop="72-75",
        fr3_contacts="82-87",
        fr4_contacts="101-103",
        rfd_rog=17.5,
        passed_screen=True,
        rfa_hotspot_covered_count=4,
        epitope_contact_count=5,
        epitope_min_distance=4.2,
        epitope_min_atom_distance=2.6,
        epitope_nearest_antibody_residue="H:52",
        epitope_nearest_target_residue="T:83",
        epitope_nearest_antibody_atom="H:TYR52:OH",
        epitope_nearest_target_atom="T:ASP83:OD1",
        epitope_mapping_mode="selected_residues",
        epitope_centroid_distance=22.1,
        target_contact_count=80,
        target_min_distance=4.0,
        target_min_atom_distance=2.5,
        target_nearest_antibody_residue="H:52",
        target_nearest_target_residue="T:80",
        target_nearest_antibody_atom="H:TYR52:OH",
        target_nearest_target_atom="T:ASP80:OD1",
        target_centroid_distance=17.3,
        detected_antibody_chains="H",
        detected_target_chain="T",
        antibody_residue_count=159,
        target_residue_count=100,
        epitope_residue_count=5,
        rog=14.0,
    )

    changed = _inherit_source_design_metrics(design, source_design, structure_path=pdb_path)

    assert changed is True
    assert design.antibody_type == "vhh"
    assert design.humanness_score == pytest.approx(0.81)
    assert design.cdr_h1_length == 20
    assert design.cdr_h3_length == 30
    assert design.fr2_contacts == "37,42,44"
    assert design.de_loop == "72-75"
    assert design.fr3_contacts == "82-87"
    assert design.fr4_contacts == "101-103"
    assert design.epitope_contact_count == 5
    assert design.target_contact_count == 80
    assert design.detected_antibody_chains == "H"
    assert design.rfd_rog == 17.5
    assert design.rog is not None


def test_coalesce_cdr_lengths_prefers_structure_then_existing_then_lineage() -> None:
    merged = _coalesce_cdr_lengths(
        {"H1": 14},
        {"H1": 13, "H2": 17},
        {"H2": 16, "H3": 28},
    )

    assert merged == {"H1": 14, "H2": 17, "H3": 28}


def test_extract_fampnn_metrics_falls_back_to_structure_profile(tmp_path: Path) -> None:
    pdb_path = tmp_path / "fampnn_metrics_fallback.pdb"
    pdb_path.write_text("""ATOM      1  N   ALA A   1      11.104  13.207   9.599  1.00  0.10           N
ATOM      2  CA  ALA A   1      12.560  13.107   9.121  1.00  0.20           C
ATOM      3  C   ALA A   1      13.020  11.676   8.742  1.00  0.30           C
ATOM      4  O   ALA A   1      12.310  10.704   8.981  1.00  0.40           O
ATOM      5  CB  ALA A   1      13.288  13.943  10.169  1.00  1.00           C
ATOM      6  N   SER A   2      14.235  11.557   8.181  1.00  0.10           N
ATOM      7  CA  SER A   2      14.780  10.229   7.811  1.00  0.20           C
ATOM      8  C   SER A   2      14.057   9.162   8.646  1.00  0.30           C
ATOM      9  O   SER A   2      13.921   7.993   8.268  1.00  0.40           O
ATOM     10  CB  SER A   2      16.305  10.241   7.984  1.00  2.00           C
ATOM     11  OG  SER A   2      16.941   9.071   7.489  1.00  4.00           O
ATOM     12  N   ALA B   1      17.104  13.207   9.599  1.00  0.10           N
ATOM     13  CA  ALA B   1      18.560  13.107   9.121  1.00  0.20           C
ATOM     14  C   ALA B   1      19.020  11.676   8.742  1.00  0.30           C
ATOM     15  O   ALA B   1      18.310  10.704   8.981  1.00  0.40           O
ATOM     16  CB  ALA B   1      19.288  13.943  10.169  1.00  0.50           C
TER
END
""")

    metrics = _extract_fampnn_metrics(None, pdb_path)

    assert metrics["avg_psce"] == 1.5
    assert metrics["max_residue_psce"] == 3.0
    assert metrics["min_residue_psce"] == 0.5
    assert metrics["chain_avg_psce"] == {"A": 2.0, "B": 0.5}


def test_extract_fampnn_metrics_falls_back_to_structure_binder_length(tmp_path: Path) -> None:
    pdb_path = tmp_path / "ppiflow_binder_length_fallback.pdb"
    pdb_path.write_text("""ATOM      1  CA  GLN H   1      11.000  13.000   9.000  1.00  0.20           C
ATOM      2  CA  VAL H   2      12.000  13.000   9.000  1.00  0.20           C
ATOM      3  CA  GLN H   3      13.000  13.000   9.000  1.00  0.20           C
ATOM      4  CA  LEU H   4      14.000  13.000   9.000  1.00  0.20           C
ATOM      5  CA  GLN H   5      15.000  13.000   9.000  1.00  0.20           C
ATOM      6  CA  GLU H   6      16.000  13.000   9.000  1.00  0.20           C
ATOM      7  CA  SER H   7      17.000  13.000   9.000  1.00  0.20           C
ATOM      8  CA  GLY H   8      18.000  13.000   9.000  1.00  0.20           C
ATOM      9  CA  GLY H   9      19.000  13.000   9.000  1.00  0.20           C
ATOM     10  CA  GLY H  10      20.000  13.000   9.000  1.00  0.20           C
ATOM     11  CA  ALA T   1      21.000  13.000   9.000  1.00  0.20           C
ATOM     12  CA  GLY T   2      22.000  13.000   9.000  1.00  0.20           C
TER
END
""")

    metrics = _extract_fampnn_metrics(None, pdb_path)

    assert metrics["binder_length"] == 10
    assert metrics["binder_sequence"] == "QVQLQESGGG"


def test_compute_fampnn_response_metrics_uses_structure_fallback(tmp_path: Path) -> None:
    pdb_path = tmp_path / "fampnn_response_fallback.pdb"
    pdb_path.write_text("""ATOM      1  N   ALA A   1      11.104  13.207   9.599  1.00  0.10           N
ATOM      2  CA  ALA A   1      12.560  13.107   9.121  1.00  0.20           C
ATOM      3  C   ALA A   1      13.020  11.676   8.742  1.00  0.30           C
ATOM      4  O   ALA A   1      12.310  10.704   8.981  1.00  0.40           O
ATOM      5  CB  ALA A   1      13.288  13.943  10.169  1.00  1.00           C
ATOM      6  N   SER A   2      14.235  11.557   8.181  1.00  0.10           N
ATOM      7  CA  SER A   2      14.780  10.229   7.811  1.00  0.20           C
ATOM      8  C   SER A   2      14.057   9.162   8.646  1.00  0.30           C
ATOM      9  O   SER A   2      13.921   7.993   8.268  1.00  0.40           O
ATOM     10  CB  SER A   2      16.305  10.241   7.984  1.00  2.00           C
ATOM     11  OG  SER A   2      16.941   9.071   7.489  1.00  4.00           O
TER
END
""")

    design = SimpleNamespace(
        fampnn_psce=2.0,
        provenance={},
        confidence_metrics=None,
        pdb_path=str(pdb_path),
    )

    metrics = _compute_fampnn_response_metrics(design, include_structure_fallback=True)

    assert metrics["fampnn_psce"] == 2.0
    assert metrics["fampnn_max_residue_psce"] == 3.0
    assert metrics["fampnn_min_residue_psce"] == 1.0


def test_apply_ppiflow_score_fields_maps_loop_local_metrics() -> None:
    design = SimpleNamespace(
        maturation_delta_interface=None,
        maturation_interface_score=None,
        maturation_rmsd=None,
        maturation_selected_delta_interface=None,
        maturation_selected_interface_score=None,
        maturation_selected_rmsd=None,
        maturation_nonselected_rmsd=None,
        ppiflow_primary_loop=None,
        ppiflow_primary_loop_rmsd=None,
        ppiflow_primary_loop_target_contact_delta=None,
        ppiflow_primary_loop_target_distance_delta=None,
        ppiflow_primary_loop_epitope_contact_delta=None,
        ppiflow_primary_loop_epitope_distance_delta=None,
        ppiflow_objective_mode=None,
        ppiflow_objective_score=None,
        ppiflow_loop_metrics=None,
    )

    changed = _apply_ppiflow_score_fields(
        design,
        {
            "delta_interface_score": -1.25,
            "interface_score_matured": -8.5,
            "rmsd_backbone": 1.4,
            "selected_delta_interface_score": -2.0,
            "selected_interface_score_matured": -5.8,
            "selected_rmsd_backbone": 0.7,
            "nonselected_rmsd_backbone": 1.1,
            "primary_loop": "H3",
            "primary_loop_rmsd": 0.6,
            "primary_loop_target_contact_delta": 3,
            "primary_loop_target_distance_delta": 1.7,
            "primary_loop_epitope_contact_delta": 2,
            "primary_loop_epitope_distance_delta": 0.9,
            "objective_mode": "balanced",
            "objective_score": -1.9,
            "loop_metrics": {
                "H3": {
                    "objective_score": -2.3,
                    "target_contact_delta": 3,
                }
            },
        },
    )

    assert changed is True
    assert design.maturation_selected_delta_interface == -2.0
    assert design.ppiflow_primary_loop == "H3"
    assert design.ppiflow_primary_loop_target_contact_delta == 3
    assert design.ppiflow_primary_loop_epitope_distance_delta == 0.9
    assert design.ppiflow_objective_mode == "balanced"
    assert design.ppiflow_objective_score == -1.9
    assert design.ppiflow_loop_metrics == {"H3": {"objective_score": -2.3, "target_contact_delta": 3}}


def test_apply_ppiflow_filter_fields_maps_filter_status() -> None:
    design = SimpleNamespace(
        ppiflow_filter_passed=None,
        ppiflow_filter_reason=None,
    )

    changed = _apply_ppiflow_filter_fields(
        design,
        {
            "passed": "passed",
            "filter_reason": "objective_score_above_threshold",
        },
    )

    assert changed is True
    assert design.ppiflow_filter_passed is True
    assert design.ppiflow_filter_reason == "objective_score_above_threshold"


def test_normalize_antibody_job_params_defaults_loop_objective_for_ppiflow() -> None:
    normalized = _normalize_antibody_job_params(
        {
            "run_ppiflow_backbone_refine": True,
            "ppiflow_stage_mode": "post_rfantibody",
        }
    )

    assert normalized["ppiflow_objective_mode"] == "balanced"
    assert normalized["ppiflow_objective_threshold"] == 0.0


def test_normalize_antibody_job_params_applies_stage_optimized_ppiflow_defaults_pre_sequence() -> None:
    normalized = _normalize_antibody_job_params(
        {
            "run_ppiflow_backbone_refine": True,
            "ppiflow_stage_mode": "post_rfantibody",
            "ppiflow_tuning_profile": "stage_optimized",
        }
    )

    assert normalized["ppiflow_tuning_profile"] == "stage_optimized"
    assert normalized["ppiflow_start_t"] == 0.55
    assert normalized["ppiflow_samples_per_target"] == 7
    assert normalized["ppiflow_require_anchors"] is False
    assert normalized["ppiflow_objective_mode"] == "loop_epitope"
    assert normalized["ppiflow_objective_threshold"] == 0.0


def test_normalize_antibody_job_params_applies_stage_optimized_ppiflow_defaults_post_sequence() -> None:
    normalized = _normalize_antibody_job_params(
        {
            "run_ppiflow_maturation": True,
            "run_maturation": True,
            "ppiflow_stage_mode": "post_fampnn",
            "ppiflow_tuning_profile": "stage_optimized",
        }
    )

    assert normalized["ppiflow_tuning_profile"] == "stage_optimized"
    assert normalized["ppiflow_start_t"] == 0.8
    assert normalized["ppiflow_samples_per_target"] == 4
    assert normalized["ppiflow_require_anchors"] is True
    assert normalized["ppiflow_objective_mode"] == "balanced"
    assert normalized["ppiflow_objective_threshold"] == 0.0


def test_normalize_antibody_job_params_disables_stage_optimized_profile_for_both_mode() -> None:
    normalized = _normalize_antibody_job_params(
        {
            "run_ppiflow_backbone_refine": True,
            "run_ppiflow_maturation": True,
            "run_maturation": True,
            "ppiflow_stage_mode": "both",
            "ppiflow_tuning_profile": "stage_optimized",
        }
    )

    assert normalized["ppiflow_tuning_profile"] == "manual"


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
    assert launch_request.params["selected_input_manifest"].endswith("selection_manifest.json")
    assert launch_request.params["source_stage_job_id"] == "source-job"
    assert launch_request.params["source_selection_count"] == 2


def test_derive_source_stage_payload_prefers_selected_design_stage_metadata(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()
    source_job = SimpleNamespace(
        id="parent-job",
        model_id="template_antibody_denovo",
        mode="template_antibody_denovo",
        params={},
        child_stage=None,
        stage_family=None,
        stage_mode=None,
    )
    selected_designs = [
        SimpleNamespace(job_id="ppiflow-child-1", stage_family="ppiflow", stage_mode="backbone_refine"),
        SimpleNamespace(job_id="ppiflow-child-1", stage_family="ppiflow", stage_mode="backbone_refine"),
    ]

    payload = _derive_source_stage_payload(source_job, selected_designs, selection_dir)

    assert payload["source_stage_job_id"] == "ppiflow-child-1"
    assert payload["source_stage_family"] == "ppiflow"
    assert payload["source_stage_mode"] == "backbone_refine"
    assert payload["source_selection_count"] == 2
    assert payload["selected_input_manifest"].endswith("selection_manifest.json")


def test_derive_source_stage_payload_falls_back_to_review_stage_metadata(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()
    source_job = SimpleNamespace(
        id="parent-job",
        model_id="template_antibody_denovo",
        mode="template_antibody_denovo",
        params={},
        child_stage=None,
        stage_family=None,
        stage_mode=None,
    )
    selected_designs = [
        SimpleNamespace(
            job_id="rf-review-child-1",
            stage_family=None,
            stage_mode=None,
            source_stage="post_rfantibody",
            source_stage_family=None,
            source_stage_mode=None,
        ),
    ]

    payload = _derive_source_stage_payload(source_job, selected_designs, selection_dir)

    assert payload["source_stage_job_id"] == "rf-review-child-1"
    assert payload["source_stage_family"] == "rfantibody"
    assert payload["source_stage_mode"] == "post_rfantibody"


def test_build_selection_manifest_item_uses_canonical_source_keys(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdb"
    selection_path = tmp_path / "selection.pdb"
    design = SimpleNamespace(
        id="design-1",
        job_id="job-1",
        name="001_example",
        stage_family="ppiflow",
        stage_mode="backbone_refine",
        lineage_root_job_id="root-1",
        parent_design_id="parent-design-1",
        origin_design_id="origin-design-1",
        origin_backbone_design_id="origin-backbone-1",
        selected_loop_scope={"ppiflow_selected_loops": "H1"},
    )

    item = _build_selection_manifest_item(
        design,
        source_path=source_path,
        selection_path=selection_path,
        selection_entry_mode="symlink",
        extra={"source_stage_family": "ppiflow", "source_stage_mode": "backbone_refine"},
    )

    assert item["source_pdb_path"] == str(source_path)
    assert item["selection_pdb_path"] == str(selection_path)
    assert item["source_design_name"] == "001_example"
    assert item["selection_entry_mode"] == "symlink"
    assert item["source_stage_family"] == "ppiflow"


def test_build_selection_manifest_item_falls_back_to_review_stage_metadata(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdb"
    selection_path = tmp_path / "selection.pdb"
    design = SimpleNamespace(
        id="design-1",
        job_id="job-1",
        name="001_example",
        stage_family=None,
        stage_mode=None,
        source_stage="post_rfantibody",
        source_stage_family=None,
        source_stage_mode=None,
        lineage_root_job_id="root-1",
        parent_design_id="parent-design-1",
        origin_design_id="origin-design-1",
        origin_backbone_design_id="origin-backbone-1",
        selected_loop_scope={"ppiflow_selected_loops": "H1"},
    )

    item = _build_selection_manifest_item(
        design,
        source_path=source_path,
        selection_path=selection_path,
        selection_entry_mode="symlink",
    )

    assert item["design_stage_family"] == "rfantibody"
    assert item["design_stage_mode"] == "post_rfantibody"


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


def test_prune_iteration_params_strips_inherited_ppiflow_stage_flags() -> None:
    pruned = _prune_iteration_params(
        {
            "run_ppiflow_backbone_refine": True,
            "run_ppiflow_maturation": True,
            "run_maturation": True,
            "run_post_validation_maturation": True,
            "run_post_boltz_maturation": True,
            "ppiflow_stage_mode": "post_rfantibody",
            "epitope_residues": "A45,A53",
        }
    )

    for forbidden in (
        "run_ppiflow_backbone_refine",
        "run_ppiflow_maturation",
        "run_maturation",
        "run_post_validation_maturation",
        "run_post_boltz_maturation",
        "ppiflow_stage_mode",
    ):
        assert forbidden not in pruned
    assert pruned["epitope_residues"] == "A45,A53"


def test_build_antibody_iteration_job_rejects_recursive_ppiflow_backbone_refine(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()

    root_job = SimpleNamespace(
        id="root-job",
        name="RBX1 beta large_resumed",
        params={"run_ppiflow_backbone_refine": True},
        pinned_gpu=None,
    )
    source_job = SimpleNamespace(
        id="source-job",
        model_id="template_antibody_denovo",
        mode="template_antibody_denovo",
        params={},
        child_stage=None,
        stage_family="ppiflow",
        stage_mode="backbone_refine",
    )
    selected_designs = [
        SimpleNamespace(job_id="ppiflow-child-1", stage_family="ppiflow", stage_mode="backbone_refine"),
    ]

    with pytest.raises(Exception) as excinfo:
        _build_antibody_iteration_job(
            root_job=root_job,
            source_job=source_job,
            action="ppiflow_backbone_refine",
            selection_dir=selection_dir,
            design_ids=["design-1"],
            name_suffix=None,
            param_overrides={},
            selected_designs=selected_designs,
        )

    assert getattr(excinfo.value, "status_code", None) == 422
    assert "RFantibody backbone inputs" in getattr(excinfo.value, "detail", "")
    assert "ppiflow/backbone_refine" in getattr(excinfo.value, "detail", "")


def test_build_antibody_iteration_job_allows_ppiflow_backbone_outputs_to_feed_fampnn_then_maturation(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()

    root_job = SimpleNamespace(
        id="root-job",
        name="RBX1 beta large_resumed",
        params={
            "run_ppiflow_backbone_refine": True,
            "ppiflow_stage_mode": "post_rfantibody",
            "epitope_residues": "A45,A53",
        },
        pinned_gpu=None,
    )
    source_job = SimpleNamespace(
        id="source-job",
        model_id="template_antibody_denovo",
        mode="template_antibody_denovo",
        params={},
        child_stage=None,
        stage_family="ppiflow",
        stage_mode="backbone_refine",
    )
    selected_designs = [
        SimpleNamespace(job_id="ppiflow-child-1", stage_family="ppiflow", stage_mode="backbone_refine"),
    ]

    launch_request = _build_antibody_iteration_job(
        root_job=root_job,
        source_job=source_job,
        action="ui_refinement",
        selection_dir=selection_dir,
        design_ids=["design-1"],
        name_suffix=None,
        param_overrides={
            "seq_design_fampnn": True,
            "run_ppiflow_maturation": True,
            "run_maturation": True,
            "ppiflow_stage_mode": "post_fampnn",
            "run_structure_validation": False,
        },
        selected_designs=selected_designs,
    )

    assert launch_request.params["selected_input_stage_family"] == "ppiflow"
    assert launch_request.params["selected_input_stage_mode"] == "backbone_refine"
    assert launch_request.params["seq_design_fampnn"] is True
    assert launch_request.params.get("run_ppiflow_backbone_refine") is not True
    assert launch_request.params["run_ppiflow_maturation"] is True
    assert launch_request.params["run_maturation"] is True
    assert launch_request.params["rfantibody_input_pdbs"] == str(selection_dir)
    assert launch_request.params.get("fampnn_collected_pdbs") is None


def test_build_antibody_iteration_job_rejects_direct_ppiflow_maturation_from_ppiflow_backbone_without_sequence_design(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()

    root_job = SimpleNamespace(
        id="root-job",
        name="RBX1 beta large_resumed",
        params={"epitope_residues": "A45,A53"},
        pinned_gpu=None,
    )
    source_job = SimpleNamespace(
        id="source-job",
        model_id="template_antibody_denovo",
        mode="template_antibody_denovo",
        params={},
        child_stage=None,
        stage_family="ppiflow",
        stage_mode="backbone_refine",
    )
    selected_designs = [
        SimpleNamespace(job_id="ppiflow-child-1", stage_family="ppiflow", stage_mode="backbone_refine"),
    ]

    with pytest.raises(Exception) as excinfo:
        _build_antibody_iteration_job(
            root_job=root_job,
            source_job=source_job,
            action="ui_refinement",
            selection_dir=selection_dir,
            design_ids=["design-1"],
            name_suffix=None,
            param_overrides={
                "seq_design_fampnn": False,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_ppiflow_maturation": True,
                "run_maturation": True,
                "ppiflow_stage_mode": "post_fampnn",
            },
            selected_designs=selected_designs,
        )

    assert getattr(excinfo.value, "status_code", None) == 422
    assert "requires FA-MPNN-designed inputs" in getattr(excinfo.value, "detail", "")


def test_build_antibody_iteration_job_accepts_rfantibody_review_rows_with_source_stage_only(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()

    root_job = SimpleNamespace(
        id="root-job",
        name="RBX1 best of 100 RFA outputs",
        params={"epitope_residues": "A45,A53"},
        pinned_gpu=None,
    )
    source_job = SimpleNamespace(
        id="source-job",
        model_id="template_antibody_denovo",
        mode="antibody_denovo_pipeline",
        params={"interactive_gate_stage": "post_rfantibody"},
        child_stage=None,
        stage_family=None,
        stage_mode=None,
    )
    selected_designs = [
        SimpleNamespace(
            job_id="rf-review-child-1",
            stage_family=None,
            stage_mode=None,
            source_stage="post_rfantibody",
            source_stage_family=None,
            source_stage_mode=None,
        ),
    ]

    launch_request = _build_antibody_iteration_job(
        root_job=root_job,
        source_job=source_job,
        action="ui_refinement",
        selection_dir=selection_dir,
        design_ids=["design-1"],
        name_suffix=None,
        param_overrides={
            "seq_design_fampnn": False,
            "seq_design_antifold": False,
            "seq_design_proteinmpnn": False,
            "run_ppiflow_backbone_refine": True,
            "run_ppiflow_maturation": False,
            "run_maturation": False,
            "run_structure_validation": False,
            "run_frustrampnn": False,
            "ppiflow_stage_mode": "post_rfantibody",
            "interactive_swa": False,
            "interactive_gating": False,
            "interactive_gate_stage": "post_rfantibody",
        },
        selected_designs=selected_designs,
    )

    assert launch_request.params["selected_input_stage_family"] == "rfantibody"
    assert launch_request.params["selected_input_stage_mode"] == "post_rfantibody"
    assert launch_request.params["rfantibody_input_pdbs"] == str(selection_dir)


def test_build_antibody_iteration_job_allows_post_ppiflow_backbone_reattempt(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()

    root_job = SimpleNamespace(
        id="root-job",
        name="RBX1 beta large_resumed",
        params={"epitope_residues": "A45,A53"},
        pinned_gpu=None,
    )
    source_job = SimpleNamespace(
        id="source-job",
        model_id="template_antibody_denovo",
        mode="template_antibody_denovo",
        params={},
        child_stage=None,
        stage_family="ppiflow",
        stage_mode="backbone_refine",
    )
    selected_designs = [
        SimpleNamespace(job_id="ppiflow-child-1", stage_family="ppiflow", stage_mode="backbone_refine"),
    ]

    launch_request = _build_antibody_iteration_job(
        root_job=root_job,
        source_job=source_job,
        action="ui_refinement",
        selection_dir=selection_dir,
        design_ids=["design-1"],
        name_suffix=None,
        param_overrides={
            "seq_design_fampnn": False,
            "seq_design_antifold": False,
            "seq_design_proteinmpnn": False,
            "run_ppiflow_backbone_refine": True,
            "run_ppiflow_maturation": False,
            "run_maturation": False,
            "ppiflow_stage_mode": "post_ppiflow",
            "run_structure_validation": False,
        },
        selected_designs=selected_designs,
    )

    assert launch_request.params["selected_input_stage_family"] == "ppiflow"
    assert launch_request.params["selected_input_stage_mode"] == "backbone_refine"
    assert launch_request.params["ppiflow_stage_mode"] == "post_ppiflow"
    assert launch_request.params["run_ppiflow_backbone_refine"] is True
    assert launch_request.params["ppiflow_require_anchors"] is False
    assert launch_request.params["rfantibody_input_pdbs"] == str(selection_dir)
    assert launch_request.params.get("fampnn_collected_pdbs") is None


def test_build_antibody_iteration_job_rejects_post_ppiflow_backbone_reattempt_for_non_ppiflow_sources(tmp_path: Path) -> None:
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()

    root_job = SimpleNamespace(
        id="root-job",
        name="RBX1 beta large_resumed",
        params={"epitope_residues": "A45,A53"},
        pinned_gpu=None,
    )
    source_job = SimpleNamespace(
        id="source-job",
        model_id="template_antibody_denovo",
        mode="template_antibody_denovo",
        params={},
        child_stage=None,
        stage_family="fampnn",
        stage_mode="redesign",
    )
    selected_designs = [
        SimpleNamespace(job_id="fampnn-child-1", stage_family="fampnn", stage_mode="redesign"),
    ]

    with pytest.raises(Exception) as excinfo:
        _build_antibody_iteration_job(
            root_job=root_job,
            source_job=source_job,
            action="ui_refinement",
            selection_dir=selection_dir,
            design_ids=["design-1"],
            name_suffix=None,
            param_overrides={
                "seq_design_fampnn": False,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_ppiflow_backbone_refine": True,
                "run_ppiflow_maturation": False,
                "run_maturation": False,
                "ppiflow_stage_mode": "post_ppiflow",
                "run_structure_validation": False,
            },
            selected_designs=selected_designs,
        )

    assert getattr(excinfo.value, "status_code", None) == 422
    assert "post-PPIFlow reattempt mode only accepts PPIFlow-derived inputs" in getattr(excinfo.value, "detail", "")


def test_get_per_chain_fampnn_psce_extracts_sidechain_profiles(tmp_path: Path) -> None:
    pdb_path = tmp_path / "fampnn_profile.pdb"
    pdb_path.write_text(
        "ATOM      1  N   ALA A   1      11.104  13.207   9.599  1.00  0.10           N\n"
        "ATOM      2  CA  ALA A   1      12.560  13.107   9.121  1.00  0.20           C\n"
        "ATOM      3  C   ALA A   1      13.020  11.676   8.742  1.00  0.30           C\n"
        "ATOM      4  O   ALA A   1      12.310  10.704   8.981  1.00  0.40           O\n"
        "ATOM      5  CB  ALA A   1      13.288  13.943  10.169  1.00  1.00           C\n"
        "ATOM      6  N   SER A   2      14.235  11.557   8.181  1.00  0.10           N\n"
        "ATOM      7  CA  SER A   2      14.780  10.229   7.811  1.00  0.20           C\n"
        "ATOM      8  C   SER A   2      14.057   9.162   8.646  1.00  0.30           C\n"
        "ATOM      9  O   SER A   2      13.921   7.993   8.268  1.00  0.40           O\n"
        "ATOM     10  CB  SER A   2      16.305  10.241   7.984  1.00  2.00           C\n"
        "ATOM     11  OG  SER A   2      16.941   9.071   7.489  1.00  4.00           O\n"
        "ATOM     12  N   ALA B   1      17.104  13.207   9.599  1.00  0.10           N\n"
        "ATOM     13  CA  ALA B   1      18.560  13.107   9.121  1.00  0.20           C\n"
        "ATOM     14  C   ALA B   1      19.020  11.676   8.742  1.00  0.30           C\n"
        "ATOM     15  O   ALA B   1      18.310  10.704   8.981  1.00  0.40           O\n"
        "ATOM     16  CB  ALA B   1      19.288  13.943  10.169  1.00  0.50           C\n"
        "TER\n"
        "END\n"
    )

    profile = get_per_chain_fampnn_psce(pdb_path)

    assert profile["A"]["residue_numbers"] == [1, 2]
    assert profile["A"]["residue_names"] == ["ALA", "SER"]
    assert profile["A"]["psce"] == [1.0, 3.0]
    assert profile["A"]["avg_psce"] == 2.0
    assert profile["A"]["max_psce"] == 3.0
    assert profile["A"]["min_psce"] == 1.0
    assert profile["B"]["psce"] == [0.5]

    profile_ignore_cbeta = get_per_chain_fampnn_psce(pdb_path, ignore_cbeta=True)

    assert profile_ignore_cbeta["A"]["psce"] == [4.0]
    assert profile_ignore_cbeta["A"]["residue_numbers"] == [2]
    assert "B" not in profile_ignore_cbeta
