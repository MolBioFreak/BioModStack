from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
FRONTEND_ROOT = REPO_ROOT / "platform" / "frontend"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import pytest
from model_registry import ModelRegistry  # noqa: E402
from services.nextflow import (  # noqa: E402
    MODEL_MODE_WORKFLOW_ENTRYPOINTS,
    build_nextflow_command,
)
from services.result_ingester import _trusted_producer_review_fields  # noqa: E402
from services.stage_review import _is_protein_local_redesign_job  # noqa: E402


CANONICAL_MODEL_ID = "protein_modification_experimental"


def test_registry_exposes_one_protein_modification_product_with_three_truthful_modes() -> None:
    model = ModelRegistry().get_model(CANONICAL_MODEL_ID)

    assert model is not None
    assert model.name == "De Novo Design"
    assert {mode.id for mode in model.modes} == {"de_novo_design", "region_redesign"}


def test_canonical_de_novo_mode_defaults_to_rfd4_proteina_and_refuses_rfd3(tmp_path: Path) -> None:
    assert MODEL_MODE_WORKFLOW_ENTRYPOINTS[(CANONICAL_MODEL_ID, "de_novo_design")] == (
        "workflows/rfd4_proteina.nf"
    )
    from fastapi import HTTPException
    from schemas import JobCreate
    from services.job_requests import normalize_job_request

    # C1: De Novo without generator defaults to rfd4_proteina
    model = ModelRegistry().get_model(CANONICAL_MODEL_ID)
    assert model is not None
    gen_param = next(p for p in model.params if p.name == "generator")
    assert gen_param.default == "rfd4_proteina"
    assert "rfd3" not in (gen_param.enum or [])

    # C2: explicit generator='rfd3' is refused
    with pytest.raises(HTTPException) as exc_info:
        normalize_job_request(
            JobCreate(
                name="refused-rfd3",
                model_id=CANONICAL_MODEL_ID,
                mode="de_novo_design",
                params={"generator": "rfd3", "num_designs": 4},
            )
        )
    assert exc_info.value.status_code == 422


def test_canonical_region_mode_reuses_internal_local_redesign_entrypoint() -> None:
    assert MODEL_MODE_WORKFLOW_ENTRYPOINTS[(CANONICAL_MODEL_ID, "region_redesign")] == (
        "workflows/region_redesign.nf"
    )

    cmd = build_nextflow_command(
        CANONICAL_MODEL_ID,
        "region_redesign",
        {
            "backbone_input_pdbs": "/tmp/designs",
            "design_chains": "A",
            "seq_method": "mpnn",
            "fixed_positions": "A1-20",
            "designable_positions": "A21-40",
        },
        "/tmp/protein-modification-region",
        job_id="protein-modification-region",
    )
    joined = " ".join(cmd)

    assert cmd[1] == "run"
    assert cmd[2] == "workflows/region_redesign.nf"
    assert cmd[4] == "workstation_ryzen7960x"
    assert "--plr_backbone_input_pdbs /tmp/designs" in joined
    assert "--plr_fixed_positions A1-20" in joined
    assert "--plr_designable_positions A21-40" in joined
    assert "--plr_seq_method mpnn" in joined
    assert "--modification_mode region_redesign" in joined






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
