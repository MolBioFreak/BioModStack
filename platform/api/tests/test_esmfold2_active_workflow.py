from pathlib import Path

import pytest

from model_registry import ModelRegistry
from services.nextflow import build_nextflow_command, resolve_nextflow_entrypoint


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_esmfold2_has_canonical_active_model_and_legacy_alias():
    registry = ModelRegistry()
    canonical = registry.get_model("esmfold2")
    legacy = registry.get_model("esmfold2_experimental")

    assert canonical is not None
    assert canonical.experimental is False
    assert canonical.category == "structure_prediction"
    assert legacy is not None
    assert legacy.experimental is False
    assert legacy.category == "structure_prediction"


@pytest.mark.parametrize("model_id", ["esmfold2", "esmfold2_experimental"])
def test_esmfold2_ids_route_to_active_structure_prediction_workflow(model_id):
    assert resolve_nextflow_entrypoint(
        effective_profile="esmfold2",
        model_id=model_id,
        mode="predict",
    ) == "workflows/structure_prediction.nf"


@pytest.mark.parametrize("model_id", ["esmfold2", "esmfold2_experimental"])
def test_esmfold2_build_command_uses_active_profile_and_predictor(monkeypatch, tmp_path, model_id):
    official_nextflow = tmp_path / "nextflow"
    official_nextflow.write_text("#!/bin/sh\nexit 0\n")
    official_nextflow.chmod(0o755)
    monkeypatch.setenv("BMS_NEXTFLOW_BIN", str(official_nextflow))

    cmd = build_nextflow_command(
        model_id,
        "predict",
        {
            "model_id": model_id,
            "mode": "predict",
            "sequence": "MKTIIALSYIFCLVFADYKDDDDK",
            "sequence_name": "esmfold2_active_contract",
            "esmf_model_variant": "fast",
            "esmf_num_loops": 1,
            "esmf_num_sampling_steps": 5,
            "esmf_num_diffusion_samples": 1,
        },
        output_dir=str(tmp_path / "out"),
        job_id="esmfold2-active-contract",
    )

    assert cmd[0] == str(official_nextflow)
    assert cmd[1:4] == ["run", "workflows/structure_prediction.nf", "-profile"]
    assert cmd[4] == "esmfold2,workstation_ryzen7960x"
    assert cmd[cmd.index("--pred_method") + 1] == "esmfold2"
    assert cmd[cmd.index("--sequence_input") + 1] == "MKTIIALSYIFCLVFADYKDDDDK"


def test_active_structure_prediction_dispatches_esmfold2_channel_process():
    workflow = (REPO_ROOT / "workflows" / "structure_prediction.nf").read_text()
    module = (REPO_ROOT / "modules" / "structure_prediction.nf").read_text()
    esmfold2_module = (REPO_ROOT / "modules" / "esmfold2_experimental.nf").read_text()
    nextflow_config = (REPO_ROOT / "nextflow.config").read_text()

    assert "'esmfold2'" in workflow
    assert "--pred_method must be one of: boltz, protenix, esmfold2, boltz_protenix" in workflow
    assert "params.pred_method in ['boltz', 'protenix', 'esmfold2', 'boltz_protenix']" in workflow
    assert "include { ESMFold2Predict } from './esmfold2_experimental.nf'" in module
    assert "ESMFold2Predict(typed_inputs)" in module
    assert "ESMFold2Predict.out.typed_cifs" in module
    assert "process ESMFold2Predict" in esmfold2_module
    assert "label 'ESMFold2'" in esmfold2_module
    assert "tuple val(producer_meta), val(sequence), val(sequence_name)" in esmfold2_module
    assert "tuple val(producer_meta), path('esmfold2_results/*.cif')" in esmfold2_module
    assert '${params.container_dir}/esmfold2.sif' in nextflow_config
    assert "${containerDir}" not in nextflow_config
    assert "${dataDir}" not in nextflow_config

    active_profile = nextflow_config.split("    esmfold2 {", 1)[1].split("    esmfold2_experimental {", 1)[0]
    assert "withName: ESMFold2Predict" in active_profile
    assert "memory = '16 GB'" in active_profile


def test_de_novo_workflows_accept_esmfold2_validator():
    batch = (REPO_ROOT / "modules" / "antibody_batch.nf").read_text()
    normalizer = (REPO_ROOT / "scripts" / "normalize_esmfold2_validation.py").read_text()
    parent = (REPO_ROOT / "workflows" / "antibody_child.nf").read_text()
    child = (REPO_ROOT / "workflows" / "antibody_child.nf").read_text()

    assert "process BatchESMFold2Validation" in batch
    assert "BatchESMFold2Validation" in parent
    assert "BatchESMFold2Validation" in child
    assert "['boltz2', 'protenix', 'esmfold2']" in parent
    assert "['boltz2', 'protenix', 'esmfold2']" in child
    assert "run_esmfold2_inference.py" in batch
    assert "run_esmfold2.py" not in batch
    assert "--local-files-only true" in batch
    assert "--weights-root" not in batch
    assert "--no-existing-msas" not in batch
    assert '"workflow": "esmfold2"' in normalizer
    assert '"workflow": "esmfold2_experimental"' not in normalizer
