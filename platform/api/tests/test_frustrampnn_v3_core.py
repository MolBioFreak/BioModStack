from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from services.frustrampnn.manifests import validate_result_manifest
from services.frustrampnn.persistence import load_and_validate_result_bundle
from test_frustrampnn_component_phase3 import _mock_v2_runtime, _v2_inputs
from routers import frustrampnn as frustrampnn_router

component = importlib.import_module("scripts.run_frustrampnn_component")
V3_MANIFEST_PATH = "frustrampnn_result_manifest_v3.json"


def test_v3_cli_loads_exact_request_and_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, normalized, structure_map_path, _ = _v2_inputs(
        tmp_path,
        residues=[("A", 1)],
        selected=[("A", 0)],
        request_generation=3,
    )
    request["schema_version"] = 3
    request["component_contract_version"] = "3.0"
    request_path = tmp_path / "workflow_component_request_v3.json"
    request_payload = component.canonical_json_bytes(request)
    request_path.write_bytes(request_payload)
    observed: dict[str, object] = {}

    def _run_component(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {}

    monkeypatch.setattr(component, "run_component", _run_component)
    output = tmp_path / "candidate_bundle_v3_cli"
    result = component.main(
        [
            "--request",
            str(request_path),
            "--structure",
            str(normalized),
            "--structure-map",
            str(structure_map_path),
            "--output-dir",
            str(output),
            "--container",
            str(tmp_path / "mock.sif"),
            "--physical-gpu-id",
            "3",
        ]
    )

    assert result == 0
    assert observed["request"] == request
    assert observed["request_payload"] == request_payload


def test_v3_core_bundle_publishes_without_statistics_and_reopens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, normalized, structure_map_path, _ = _v2_inputs(
        tmp_path,
        residues=[("A", 1)],
        selected=[("A", 0)],
        request_generation=3,
    )
    request["schema_version"] = 3
    request["component_contract_version"] = "3.0"
    _mock_v2_runtime(component, monkeypatch, tmp_path)
    output = tmp_path / "candidate_bundle_v3"

    manifest = component.run_component(
        request=request,
        request_payload=component.canonical_json_bytes(request),
        source_structure=normalized,
        structure_map=structure_map_path,
        output_dir=output,
        container=tmp_path / "mock.sif",
        physical_gpu_id=3,
    )

    assert manifest["schema_version"] == 3
    summary = json.loads(
        (output / "frustrampnn_summary_v3.json").read_text(encoding="utf-8")
    )
    summary_model = getattr(frustrampnn_router, "FrustraMPNNSummaryV3Document", None)
    assert summary_model is not None
    assert summary_model.model_validate(summary).root == summary
    assert (output / V3_MANIFEST_PATH).is_file()
    assert not (output / "frustrampnn_statistics_v1.json").exists()
    payloads = validate_result_manifest(output, manifest)
    assert "workflow_component_request_v3.json" in payloads
    assert "frustrampnn_statistics_v1.json" not in payloads
    terminal = json.loads(
        (output / "workflow_component_result_v3.json").read_text(encoding="utf-8")
    )
    bundle = load_and_validate_result_bundle(
        output,
        expected_parent_job_id=str(request["parent_job_id"]),
        terminal_envelope=terminal,
    )
    assert bundle.contract_version == 3
    assert bundle.statistics is None
