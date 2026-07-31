from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
SCRIPTS_ROOT = REPO_ROOT / "scripts"
FRONTEND_ROOT = REPO_ROOT / "platform" / "frontend"
for path in (API_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services.nextflow import build_nextflow_command  # noqa: E402
from services.result_state_integrity import job_expects_design_results  # noqa: E402
from protein_hunter_runtime import value_or_default  # noqa: E402


def test_de_novo_design_parent_does_not_claim_protein_hunter() -> None:
    model = yaml.safe_load(
        (API_ROOT / "config/models/protein_modification_experimental.yaml").read_text(encoding="utf-8")
    )
    assert model["name"] == "De Novo Design"
    assert [mode["id"] for mode in model["modes"]] == ["de_novo_design", "shape_blueprint", "region_redesign"]

    source = (FRONTEND_ROOT / "src/components/ProteinModificationTemplate.tsx").read_text(encoding="utf-8")
    assert "De Novo Design" in source
    assert "Iterative Binder Design" not in source
    assert "Protein Hunter" not in source


@pytest.mark.parametrize(
    ("model_id", "mode"),
    [
        ("protein_hunter_experimental", "design"),
        ("protein_modification_experimental", "iterative_binder_design"),
    ],
)
def test_fresh_protein_hunter_launches_fail_closed_until_ipsae(model_id: str, mode: str) -> None:
    with pytest.raises(ValueError, match="de novo binder workflow.*ipSAE"):
        build_nextflow_command(model_id, mode, {}, "/tmp/protein-hunter-blocked")


def test_standalone_protein_hunter_launcher_is_retired() -> None:
    assert not (API_ROOT / "config/templates/protein_hunter_experimental.yaml").exists()
    inventory = (FRONTEND_ROOT / "src/components/workflowModelInventory.ts").read_text(encoding="utf-8")
    assert "workflowId: 'protein_hunter_experimental'" not in inventory


def test_zero_and_false_are_not_treated_as_missing() -> None:
    payload = {"percent_x": 0, "cyclic": False, "alanine_bias": False, "temperature": 0.0}
    assert value_or_default(payload, "percent_x", 50) == 0
    assert value_or_default(payload, "cyclic", True) is False
    assert value_or_default(payload, "alanine_bias", True) is False
    assert value_or_default(payload, "temperature", 0.1) == 0.0

    module = (REPO_ROOT / "modules/protein_hunter_experimental.nf").read_text(encoding="utf-8")
    assert "params.ph_percent_x ?: 50" not in module
    assert "params.ph_cyclic ?: false" not in module
    assert "params.ph_alanine_bias ?: true" not in module


def test_protein_hunter_jobs_require_authoritative_design_rows() -> None:
    standalone: Any = SimpleNamespace(model_id="protein_hunter_experimental", mode="design", params={})
    assert job_expects_design_results(standalone) is True
