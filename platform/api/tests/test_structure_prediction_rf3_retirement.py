from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_structure_template_has_no_rf3_launch_contract() -> None:
    template_path = REPO_ROOT / "platform/api/config/templates/structure_prediction.yaml"
    template = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    predictor = next(field for field in template["user_params"] if field["name"] == "pred_method")

    assert predictor["enum"] == ["boltz", "fold_cp"]
    assert all(not str(field["name"]).startswith("rf3_") for field in template["user_params"])
    assert "RF3" not in template["stages"][0]["tool"]

    validation_template_path = REPO_ROOT / "platform/api/config/templates/structure_validation.yaml"
    validation_template = yaml.safe_load(validation_template_path.read_text(encoding="utf-8"))
    validation_predictor = next(
        field for field in validation_template["user_params"] if field["name"] == "pred_method"
    )
    assert validation_predictor["enum"] == ["af2", "boltz"]
    assert "RF3" not in validation_template["stages"][0]["tool"]


def test_structure_nextflow_limits_rf3_dispatch_to_persisted_mutagenesis_children() -> None:
    workflow = (REPO_ROOT / "workflows/structure_prediction.nf").read_text(encoding="utf-8")
    module = (REPO_ROOT / "modules/structure_prediction.nf").read_text(encoding="utf-8")

    assert "process RF3FromSequence" in module
    assert "params.containsKey('mutagenesis_prediction') && params.mutagenesis_prediction == true" in module
    assert "is_mutagenesis_prediction && pred_method in ['rf3', 'both']" in module
    assert "params.containsKey('mutagenesis_prediction') && params.mutagenesis_prediction == true" in workflow
    assert "def allowedPredictors = isMutagenesisPrediction" in workflow
    assert "['boltz', 'rf3', 'both', 'esmfold2']" in workflow
    assert "pred_method == 'all'" not in module
    assert "pred_method == 'boltz_protenix'" in module
    assert "['boltz', 'protenix', 'esmfold2', 'boltz_protenix']" in workflow


def test_rf3_internal_model_and_non_structure_module_remain_for_history_and_other_workflows() -> None:
    assert (REPO_ROOT / "platform/api/config/models/rf3.yaml").is_file()
    assert (REPO_ROOT / "modules/rf3.nf").is_file()
    protein_design = (REPO_ROOT / "workflows/protein_design.nf").read_text(encoding="utf-8")
    assert "include { RunRF3 ; FilterRF3 } from '../modules/rf3.nf'" in protein_design


def test_resume_and_resubmit_routes_share_the_retired_structure_guard() -> None:
    source = (REPO_ROOT / "platform/api/routers/jobs.py").read_text(encoding="utf-8")
    resubmit_block = source.split('@router.post("/{job_id}/resubmit")', 1)[1].split("@router.", 1)[0]
    resume_block = source.split('@router.post("/{job_id}/resume")', 1)[1].split("@router.", 1)[0]

    assert "_job_has_retired_structure_predictor(original_job)" in resubmit_block
    assert "_job_has_retired_structure_predictor(job)" in resume_block