from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.plr_workflow_results import build_protein_local_redesign_result_surface


def _design(root: Path, relative_path: str, *, design_id: str, name: str, confidence_metrics=None):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"ATOM      1  CA  ALA A   1  {name}\n", encoding="utf-8")
    return SimpleNamespace(
        id=design_id,
        name=name,
        pdb_path=str(path),
        json_path=None,
        stage_family=None,
        stage_mode="region_redesign",
        review_profile_id=None,
        review_contract_version=None,
        artifact_class="validated_local_redesign_structure",
        artifact_schema_version=1,
        provenance={},
        confidence_metrics=confidence_metrics or {},
        plddt_overall=None,
        plddt_binder=None,
        plddt_target=None,
        pae_overall=None,
        pae_interaction=None,
        rmsd_overall=None,
        rog=None,
        rfd_rog=None,
        fampnn_psce=None,
        iptm=None,
        ptm=None,
        conf_score=None,
        disorder=None,
        num_recycles=None,
    )


def test_plr_result_surface_has_model_native_tabs_and_bound_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "result"
    root.mkdir()
    (root / "inputs/protein_local_redesign").mkdir(parents=True)
    (root / "inputs/protein_local_redesign/region_manifest.json").write_text(
        json.dumps({"schema": "bms.protein-local-redesign.region-manifest.v1", "job_id": "job-1"}),
        encoding="utf-8",
    )
    (root / "validation").mkdir()
    (root / "validation/validator_suite_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflow": "protein_local_redesign",
                "state": "complete",
                "requested_validators": ["esmfold2", "protenix_v2"],
                "expected_candidate_count": 1,
                "validator_summaries": [
                    {"validator": "esmfold2", "state": "complete", "completed_candidates": 1, "expected_candidate_count": 1, "artifact_root": "validation/esmfold2"},
                    {"validator": "protenix_v2", "state": "complete", "completed_candidates": 1, "expected_candidate_count": 1, "artifact_root": "validation/protenix_v2"},
                ],
            }
        ),
        encoding="utf-8",
    )

    sequence = "ACDE"
    designs = [
        _design(root, "collected/protein_local_redesign_backbones/model_0.pdb", design_id="rfd3-0", name="candidate_model_0"),
        _design(
            root,
            "pdb_files/candidate_model_0_seq_0.pdb",
            design_id="fampnn-0",
            name="candidate_model_0_seq_0",
            confidence_metrics={"fampnn": {"sequence": sequence, "fampnn_avg_psce": 0.92}},
        ),
        _design(root, "validation/esmfold2/candidate_model_0_seq_0/esmfold2_results/model_0.cif", design_id="esm-0", name="candidate_model_0_seq_0"),
        _design(root, "validation/protenix_v2/candidate_model_0_seq_0/candidate_model_0_seq_0_sample_0.cif", design_id="prot-0", name="candidate_model_0_seq_0_sample_0"),
    ]
    esm_dir = root / "validation/esmfold2/candidate_model_0_seq_0/esmfold2_results"
    (esm_dir / "model_0.metrics.json").write_text(json.dumps({"mean_plddt": 91.2, "ptm": 0.8}), encoding="utf-8")
    prot_dir = root / "validation/protenix_v2/candidate_model_0_seq_0"
    (prot_dir / "candidate_model_0_seq_0_summary_confidence_sample_0.json").write_text(json.dumps({"confidence_score": 0.7}), encoding="utf-8")
    (prot_dir / "msa_report.json").write_text(json.dumps({"backend": "cache", "state": "complete"}), encoding="utf-8")

    surface = build_protein_local_redesign_result_surface(
        SimpleNamespace(id="job-1", name="PLR", status="completed", model_id="protein_modification_experimental", mode="region_redesign", output_dir=str(root), params={}),
        designs,
    )

    assert surface["schema"] == "bms.workflow.protein-local-redesign.results.v1"
    assert surface["composition"]["sha256"]
    assert [tab["id"] for tab in surface["tabs"]] == ["rfd3", "fampnn", "esmfold2", "protenix_v2"]
    assert [tab["label"] for tab in surface["tabs"]] == ["RFD3", "FA-MPNN", "ESMFold2", "Protenix V2"]
    assert all("[Validation]" not in tab["label"] for tab in surface["tabs"])
    assert [tab["count"] for tab in surface["tabs"]] == [1, 1, 1, 1]
    assert surface["tabs"][1]["items"][0]["sequence"] == sequence
    assert surface["tabs"][2]["items"][0]["metrics"]["mean_plddt"] == 91.2
    assert surface["tabs"][3]["items"][0]["sample_index"] == 0
    assert surface["tabs"][3]["items"][0]["msa"]["backend"] == "cache"
    assert all("/tmp/" not in json.dumps(surface_item) for surface_item in surface["tabs"])
    assert all(item["structure"]["content_url"].startswith("/api/jobs/job-1/workflow-results/artifacts/") for tab in surface["tabs"] for item in tab["items"])


def test_plr_result_surface_rejects_structure_outside_job_root(tmp_path: Path) -> None:
    root = tmp_path / "result"
    root.mkdir()
    outside = tmp_path / "outside.pdb"
    outside.write_text("ATOM\n", encoding="utf-8")
    design = _design(root, "inside.pdb", design_id="bad", name="candidate_model_0")
    design.pdb_path = str(outside)

    with pytest.raises(ValueError, match="contained"):
        build_protein_local_redesign_result_surface(
            SimpleNamespace(id="job-1", name="PLR", status="completed", model_id="protein_local_redesign", mode="local_redesign", output_dir=str(root), params={}),
            [design],
        )
