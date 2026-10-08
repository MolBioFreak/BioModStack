from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
FRONTEND_ROOT = REPO_ROOT / "platform" / "frontend"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import ModelRegistry  # noqa: E402
from services.nextflow import (  # noqa: E402
    MODEL_MODE_WORKFLOW_ENTRYPOINTS,
    build_nextflow_command,
)
from services.result_ingester import _trusted_producer_review_fields  # noqa: E402
from services.stage_review import _is_protein_local_redesign_job  # noqa: E402


CANONICAL_MODEL_ID = "protein_modification_experimental"


def test_registry_exposes_one_protein_modification_product_with_two_truthful_modes() -> None:
    model = ModelRegistry().get_model(CANONICAL_MODEL_ID)

    assert model is not None
    assert model.name == "De Novo Design"
    assert {mode.id for mode in model.modes} == {"de_novo_design", "region_redesign"}


def test_canonical_de_novo_mode_reuses_internal_protein_cad_entrypoint() -> None:
    assert MODEL_MODE_WORKFLOW_ENTRYPOINTS[(CANONICAL_MODEL_ID, "de_novo_design")] == (
        "workflows/protein_cad_experimental.nf"
    )

    cmd = build_nextflow_command(
        CANONICAL_MODEL_ID,
        "de_novo_design",
        {
            "backend": "disco",
            "design_task": "unconditional",
            "num_designs": 4,
            "target_lengths": "100,150",
        },
        "/tmp/protein-modification-de-novo",
        job_id="protein-modification-de-novo",
    )
    joined = " ".join(cmd)

    assert cmd[1] == "run"
    assert cmd[2] == "workflows/protein_cad_experimental.nf"
    assert "protein_cad_experimental,workstation_ryzen7960x" in cmd
    assert "--pcad_backend disco" in joined
    assert "--pcad_num_designs 4" in joined
    assert "--modification_mode de_novo_design" in joined


def test_canonical_region_mode_reuses_internal_local_redesign_entrypoint() -> None:
    assert MODEL_MODE_WORKFLOW_ENTRYPOINTS[(CANONICAL_MODEL_ID, "region_redesign")] == (
        "workflows/protein_local_redesign.nf"
    )

    cmd = build_nextflow_command(
        CANONICAL_MODEL_ID,
        "region_redesign",
        {
            "input_pdb": "/tmp/input.pdb",
            "design_chains": "A",
            "region_mode": "manual_ranges",
            "redesign_ranges": "10-20",
            "num_designs": 3,
        },
        "/tmp/protein-modification-region",
        job_id="protein-modification-region",
    )
    joined = " ".join(cmd)

    assert cmd[1] == "run"
    assert cmd[2] == "workflows/protein_local_redesign.nf"
    assert "protein_local_redesign,workstation_ryzen7960x" in cmd
    assert "--plr_input_pdb /tmp/input.pdb" in joined
    assert "--plr_redesign_ranges 10-20" in joined
    assert "--modification_mode region_redesign" in joined


def test_launcher_exposes_parent_and_not_legacy_products() -> None:
    job_submission = (FRONTEND_ROOT / "src/components/JobSubmission.tsx").read_text(encoding="utf-8")
    template_state = (FRONTEND_ROOT / "src/components/jobSubmissionTemplateState.ts").read_text(encoding="utf-8")
    parent_template = FRONTEND_ROOT / "src/components/ProteinModificationTemplate.tsx"

    assert parent_template.exists()
    assert "id: 'protein_modification_experimental'" in job_submission
    assert "name: 'De Novo Design'" in job_submission
    assert "id: 'protein_local_redesign'" not in job_submission
    assert "selectedTemplateId === 'protein_local_redesign'" not in job_submission
    assert "'protein_modification_experimental'" in template_state


def test_legacy_api_template_is_compatibility_only_and_hidden() -> None:
    assert (API_ROOT / "config/templates/protein_cad_experimental.yaml").exists()
    job_submission = (FRONTEND_ROOT / "src/components/JobSubmission.tsx").read_text(encoding="utf-8")
    assert "LEGACY_PROTEIN_MODIFICATION_TEMPLATE_IDS" in job_submission
    assert "!LEGACY_PROTEIN_MODIFICATION_TEMPLATE_IDS.has(t.id)" in job_submission


def test_canonical_region_jobs_retain_local_redesign_review_semantics() -> None:
    job: Any = SimpleNamespace(
        model_id=CANONICAL_MODEL_ID,
        mode="region_redesign",
        params={"modification_mode": "region_redesign"},
        awaiting_payload=None,
    )
    producer_payload = {
        "review_profile_id": "de_novo_generation_v1",
        "review_contract_version": 1,
        "review_contract_source": "producer",
        "review_role_map": {"result_role": "locally_redesigned_backbone"},
        "review_artifact_manifest": {
            "schema": "bms.review-artifacts.v1",
            "artifacts": {
                "structure": {
                    "kind": "structure",
                    "state": "ready",
                    "path": "/tmp/structure.pdb",
                }
            },
        },
    }

    assert _is_protein_local_redesign_job(job)
    assert _trusted_producer_review_fields(job, producer_payload)["review_profile_id"] == (
        "de_novo_generation_v1"
    )
