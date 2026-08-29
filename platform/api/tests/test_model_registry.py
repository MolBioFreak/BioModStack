from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import ModelRegistry  # noqa: E402
from routers import models as models_router_module  # noqa: E402


def _model_record(model_id: str, *, enabled: bool = True, public_launch: bool = True) -> dict[str, Any]:
    return {
        "id": model_id,
        "name": model_id.replace("_", " ").title(),
        "version": "1.0",
        "category": "scientific_analysis",
        "description": f"{model_id} test model",
        "container": f"/private/runtime/{model_id}.sif",
        "enabled": enabled,
        "public_launch": public_launch,
    }


def _integration_record() -> dict[str, Any]:
    record = _model_record("frustrampnn", public_launch=False)
    record["integration"] = {
        "stage_parameter": "run_frustrampnn",
        "operator_label": "Frustration analysis",
        "checkpoint_label": "MegaScale-trained checkpoint",
        "model_summary": "Maps residue-level energetic frustration.",
        "semantic_roles": ["structure_interpretation", "mutagenesis_guidance"],
        "workflows": {
            "structure_prediction": {
                "default_enabled": True,
                "enabled_summary": "Analyze each predicted structure.",
            }
        },
    }
    return record


def _write_registry(config_dir: Path, records: list[dict[str, Any]]) -> ModelRegistry:
    config_dir.mkdir()
    for index, record in enumerate(records):
        (config_dir / f"{index:02d}-{record['id']}.yaml").write_text(
            yaml.safe_dump(record, sort_keys=False),
            encoding="utf-8",
        )
    return ModelRegistry(config_dir=config_dir)


def _client_for(registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(models_router_module, "get_registry", lambda: registry)
    app = FastAPI()
    app.include_router(models_router_module.router, prefix="/api/models")
    return TestClient(app)


def test_public_model_routes_hide_internal_and_disabled_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _write_registry(
        tmp_path / "models",
        [
            _model_record("public_enabled"),
            _model_record("internal_enabled", public_launch=False),
            _model_record("public_disabled", enabled=False),
        ],
    )
    client = _client_for(registry, monkeypatch)

    response = client.get("/api/models", params={"include_experimental": True})

    assert response.status_code == 200
    assert [model["id"] for model in response.json()] == ["public_enabled"]
    assert client.get("/api/models/public_enabled").status_code == 200
    assert client.get("/api/models/internal_enabled").status_code == 404
    assert client.get("/api/models/public_disabled").status_code == 404
    assert registry.validate_job_params("internal_enabled", "any", {}) == ["Unknown model: internal_enabled"]


def test_string_list_enum_validates_each_selected_value(tmp_path: Path) -> None:
    record = _model_record("validator_suite")
    record["modes"] = [
        {
            "id": "validate",
            "name": "Validate",
            "description": "Validate with selected peers",
            "params": ["structure_validators"],
        }
    ]
    record["params"] = [
        {
            "name": "structure_validators",
            "type": "string_list",
            "description": "Selected peer validators",
            "enum": ["boltz2", "esmfold2", "protenix_v2"],
        }
    ]
    registry = _write_registry(tmp_path / "models", [record])

    assert registry.validate_job_params(
        "validator_suite",
        "validate",
        {"structure_validators": ["esmfold2", "protenix_v2"]},
    ) == []
    assert registry.validate_job_params(
        "validator_suite",
        "validate",
        {"structure_validators": ["esmfold2", "unknown"]},
    ) == [
        "Invalid value for structure_validators: members must be one of "
        "['boltz2', 'esmfold2', 'protenix_v2']"
    ]


def test_internal_model_integration_route_returns_only_bounded_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _write_registry(tmp_path / "models", [_integration_record()])
    client = _client_for(registry, monkeypatch)

    response = client.get("/api/models/frustrampnn/integration")

    assert response.status_code == 200
    assert response.json() == {
        "model_id": "frustrampnn",
        "model_name": "Frustrampnn",
        "model_version": "1.0",
        "stage_parameter": "run_frustrampnn",
        "operator_label": "Frustration analysis",
        "checkpoint_label": "MegaScale-trained checkpoint",
        "model_summary": "Maps residue-level energetic frustration.",
        "semantic_roles": ["structure_interpretation", "mutagenesis_guidance"],
        "workflows": {
            "structure_prediction": {
                "default_enabled": True,
                "enabled_summary": "Analyze each predicted structure.",
            }
        },
    }
    assert "container" not in response.json()
    assert client.get("/api/models/frustrampnn").status_code == 404

    disabled_record = _integration_record()
    disabled_record["enabled"] = False
    disabled_registry = _write_registry(tmp_path / "disabled-models", [disabled_record])
    disabled_client = _client_for(disabled_registry, monkeypatch)
    assert disabled_client.get("/api/models/frustrampnn/integration").status_code == 404


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        (lambda record: record["integration"]["workflows"].update({"unknown_workflow": record["integration"]["workflows"].pop("structure_prediction")}), "unknown workflow"),
        (lambda record: record["integration"].update({"stage_parameter": "arbitrary_browser_key"}), "stage parameter"),
        (lambda record: record["integration"].update({"semantic_roles": ["structure_interpretation", " "]}), "blank semantic role"),
        (lambda record: record["integration"].update({"semantic_roles": ["structure_interpretation", "structure_interpretation"]}), "duplicate semantic role"),
        (lambda record: record["integration"].update({"operator_label": "  "}), "operator label"),
        (lambda record: record["integration"].update({"model_summary": ""}), "model summary"),
        (lambda record: record["integration"]["workflows"]["structure_prediction"].update({"enabled_summary": "\t"}), "enabled summary"),
    ],
    ids=[
        "unknown-workflow",
        "nonallowlisted-stage-key",
        "blank-role",
        "duplicate-role",
        "blank-operator-label",
        "blank-model-summary",
        "blank-enabled-summary",
    ],
)
def test_registry_rejects_malformed_integration_records(
    tmp_path: Path,
    mutation: Any,
    expected_message: str,
) -> None:
    record = deepcopy(_integration_record())
    mutation(record)

    with pytest.raises(ValueError, match=expected_message):
        _write_registry(tmp_path / "models", [record])


def test_failed_reload_preserves_the_last_complete_registry(tmp_path: Path) -> None:
    record = _integration_record()
    config_dir = tmp_path / "models"
    config_dir.mkdir()
    yaml_path = config_dir / "frustrampnn.yaml"
    yaml_path.write_text(yaml.safe_dump(record), encoding="utf-8")
    registry = ModelRegistry(config_dir=config_dir)

    broken = deepcopy(record)
    broken["integration"]["semantic_roles"] = ["scientific_analysis", "scientific_analysis"]
    yaml_path.write_text(yaml.safe_dump(broken), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate semantic role"):
        registry.reload()

    retained = registry.get_internal_model_definition("frustrampnn")
    assert retained is not None
    assert retained.integration is not None
    assert retained.integration.semantic_roles == record["integration"]["semantic_roles"]
