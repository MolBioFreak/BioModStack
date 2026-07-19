from __future__ import annotations

import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
FRONTEND_ROOT = REPO_ROOT / "platform" / "frontend"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import (  # noqa: E402
    MODEL_MODE_WORKFLOW_ENTRYPOINTS,
    STRUCTURE_PREDICTION_ENTRYPOINT,
    WORKFLOW_ENTRYPOINTS,
    build_nextflow_command,
    resolve_nextflow_entrypoint,
)


@pytest.mark.parametrize(
    "relative_path",
    [
        "workflows/esmfold2_experimental.nf",
        "workflows/boltzgen_design.nf",
        "platform/api/config/templates/esmfold2.yaml",
        "platform/api/config/templates/esmfold2_experimental.yaml",
        "platform/api/config/templates/boltzgen_design.yaml",
    ],
)
def test_engine_capabilities_have_no_standalone_entrypoint_or_template(relative_path: str) -> None:
    assert not (REPO_ROOT / relative_path).exists(), relative_path


def test_engine_capabilities_are_absent_from_public_nextflow_entrypoint_registries() -> None:
    assert "esmfold2_experimental" not in WORKFLOW_ENTRYPOINTS
    assert "boltzgen" not in WORKFLOW_ENTRYPOINTS
    assert not any(model_id == "boltzgen" for model_id, _mode in MODEL_MODE_WORKFLOW_ENTRYPOINTS)


def test_esmfold2_ids_route_only_through_structure_prediction() -> None:
    for model_id in ("esmfold2", "esmfold2_experimental"):
        assert resolve_nextflow_entrypoint(
            effective_profile="esmfold2",
            model_id=model_id,
            mode="predict",
        ) == STRUCTURE_PREDICTION_ENTRYPOINT


def test_direct_boltzgen_product_launch_is_rejected() -> None:
    with pytest.raises(ValueError, match="BoltzGen is an internal de-novo engine"):
        build_nextflow_command(
            "boltzgen",
            "design",
            {"target_pdb": "/tmp/target.pdb"},
            "/tmp/out",
            job_id="forbidden-standalone-boltzgen",
        )

    with pytest.raises(ValueError, match="BoltzGen is an internal de-novo engine"):
        resolve_nextflow_entrypoint(effective_profile="boltzgen")


def test_required_calling_workflows_expose_engines_as_nested_selectors() -> None:
    structure_ui = (FRONTEND_ROOT / "src/components/structurePredictionUiState.ts").read_text()
    mutagenesis_ui = (FRONTEND_ROOT / "src/components/MutagenesisTemplate.tsx").read_text()
    denovo_ui = (FRONTEND_ROOT / "src/components/AntibodyDenovoTemplate.tsx").read_text()
    job_submission_ui = (FRONTEND_ROOT / "src/components/JobSubmission.tsx").read_text()
    boltzgen_module = (REPO_ROOT / "modules/boltzgen.nf").read_text()

    assert "'esmfold2'" in mutagenesis_ui
    assert "pred_method: predictorConfig.predictor" in job_submission_ui
    assert "predictorConfig.predictor === 'esmfold2'" in job_submission_ui
    assert "'esmfold2'" in structure_ui
    assert "structure_validator" in denovo_ui
    assert "esmfold2" in denovo_ui.lower()
    assert "'boltzgen'" in denovo_ui
    assert "RunBoltzGen" in boltzgen_module

    protein_design = (REPO_ROOT / "workflows/protein_design.nf").read_text()
    nextflow_config = (REPO_ROOT / "nextflow.config").read_text()
    assert "run_boltzgen_only" not in protein_design
    assert "BoltzGen standalone" not in protein_design
    assert "run_boltzgen_only" not in nextflow_config


def test_frontend_has_no_dedicated_esmfold2_or_boltzgen_launcher() -> None:
    source = (FRONTEND_ROOT / "src/components/JobSubmission.tsx").read_text()
    inventory = (FRONTEND_ROOT / "src/components/workflowModelInventory.ts").read_text()

    assert "import { BoltzGenTemplate }" not in source
    assert "boltzgen: 'boltzgen_design'" not in source
    assert "esmfold2: 'esmfold2'" not in source
    assert "esmfold2_experimental: 'esmfold2_experimental'" not in source
    assert "Standalone ESMFold2" not in source
    assert "id: 'boltzgen_design'" not in inventory
    assert "id: 'esmfold2'" not in inventory
    assert "id: 'esmfold2_experimental'" not in inventory
