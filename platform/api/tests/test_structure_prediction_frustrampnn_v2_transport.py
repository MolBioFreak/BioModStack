from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from services.frustrampnn.contracts import canonical_json_bytes, validate_schema
from services.frustrampnn.settings import FrustraMPNNRequestedSettings, requested_settings_sha256
from services.nextflow import FRUSTRAMPNN_SETTINGS_MAX_BYTES, build_nextflow_command


REPO_ROOT = Path(__file__).resolve().parents[3]
PREPARER = REPO_ROOT / "scripts" / "prepare_frustrampnn_candidate.py"
WORKFLOW = REPO_ROOT / "workflows" / "structure_prediction.nf"


def _prepare_module():
    spec = importlib.util.spec_from_file_location("structure_prediction_v2_preparer", PREPARER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdb_atom(
    serial: int,
    atom: str,
    residue: str,
    chain: str,
    number: int,
    *,
    insertion: str = "",
    x: float = 1.0,
) -> str:
    element = next(character for character in atom if character.isalpha())
    atom_field = atom if len(atom) == 4 else f" {atom:<3}"
    return (
        f"ATOM  {serial:5d} {atom_field} {residue:>3} {chain}{number:4d}{insertion or ' '}"
        f"   {x:8.3f}{(x + 1):8.3f}{(x + 2):8.3f}{1.0:6.2f}{20.0:6.2f}"
        f"          {element:>2}  \n"
    )


def _two_model_pdb() -> bytes:
    lines: list[str] = []
    serial = 1
    for model, x in ((1, 1.0), (2, 20.0)):
        lines.append(f"MODEL        {model}\n")
        for atom in ("N", "CA", "C", "O"):
            lines.append(
                _pdb_atom(serial, atom, "GLY", "A", 10, insertion="A", x=x + serial)
            )
            serial += 1
        lines.append("ENDMDL\n")
    lines.append("END\n")
    return "".join(lines).encode("ascii")


def _metadata() -> dict[str, object]:
    return {
        "parent_job_id": "structure-parent-1",
        "parent_workflow_id": "structure_prediction",
        "producer_stage": "structure_prediction:boltz",
        "producer_candidate_key": "frustrampnn/sources/boltz/sample/rank_0.normalized.pdb",
        "requiredness": "required",
    }


def _selected_settings(*, auth_seq_id: int = 10) -> FrustraMPNNRequestedSettings:
    return FrustraMPNNRequestedSettings.model_validate(
        {
            "schema_name": "frustrampnn_settings",
            "schema_version": 1,
            "settings_value_origin": "operator_request",
            "protein_selection": {
                "mode": "selected_residues",
                "entities": [],
                "residues": [
                    {
                        "entity_instance_id": "pdb:A",
                        "source_entity_id": None,
                        "label_asym_id": None,
                        "auth_asym_id": "A",
                        "auth_seq_id": auth_seq_id,
                        "insertion_code": "A",
                        "sequence_index": 1,
                    }
                ],
            },
            "source_structure": {
                "selected_model_number": 2,
                "preferred_altloc": "A",
            },
            "classification_policy": {
                "mode": "custom",
                "high_max": -0.7,
                "minimal_min": 0.25,
            },
        }
    )


def _settings_transport_bytes(settings: FrustraMPNNRequestedSettings) -> bytes:
    return canonical_json_bytes(
        settings.model_dump(
            mode="json",
            exclude_none=False,
            exclude={"settings_value_origin"},
        )
    )


def _managed_command(settings: dict[str, object]) -> list[str]:
    return build_nextflow_command(
        "esmfold2",
        "predict",
        {
            "pred_method": "esmfold2",
            "gpu_id": 3,
            "run_frustrampnn": True,
            "sequence_input": "ACDE",
            "sequence_name": "transport-probe",
            "frustrampnn_settings": settings,
            "unrelated_nested_parameter": {"must": "remain unsupported"},
        },
        "/tmp/frustrampnn-v2-managed-transport",
        job_id="frustrampnn-v2-managed-transport",
    )


def test_managed_launcher_transports_only_known_settings_as_exact_compact_canonical_json() -> None:
    settings = _selected_settings().model_dump(mode="json", exclude_none=False)
    settings["settings_value_origin"] = "operator_request"
    settings["classification_policy"] = {
        "mode": "custom",
        "high_max": -0.612345,
        "minimal_min": 0.312345,
    }

    command = _managed_command(settings)

    assert command.count("--frustrampnn_settings") == 1
    assert command.count("--frustrampnn_settings_value_origin") == 1
    assert command[command.index("--frustrampnn_settings_value_origin") + 1] == "operator_request"
    encoded = command[command.index("--frustrampnn_settings") + 1]
    transported_settings = dict(settings)
    transported_settings.pop("settings_value_origin")
    assert encoded.encode("utf-8") == canonical_json_bytes(transported_settings)
    assert json.loads(encoded) == transported_settings
    assert "--unrelated_nested_parameter" not in command
    assert "--frustrampnn_physical_gpu_id" in command


@pytest.mark.parametrize("origin", [None, "request", "bms_default ", "unknown"])
def test_managed_launcher_rejects_missing_or_noncanonical_settings_origin(origin) -> None:
    settings = _selected_settings().model_dump(mode="json", exclude_none=False)
    if origin is None:
        settings.pop("settings_value_origin")
    else:
        settings["settings_value_origin"] = origin

    with pytest.raises(ValueError, match="settings_value_origin"):
        _managed_command(settings)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_managed_launcher_rejects_non_finite_settings_values(non_finite: float) -> None:
    settings = _selected_settings().model_dump(mode="json", exclude_none=False)
    settings["classification_policy"]["high_max"] = non_finite

    with pytest.raises(ValueError, match="non-finite"):
        _managed_command(settings)


def test_managed_launcher_rejects_settings_larger_than_bounded_argv_contract() -> None:
    settings = _selected_settings().model_dump(mode="json", exclude_none=False)
    settings["oversized_unknown_field"] = "x" * FRUSTRAMPNN_SETTINGS_MAX_BYTES

    with pytest.raises(ValueError, match="exceeds .* byte limit"):
        _managed_command(settings)


def test_structure_prediction_preparer_v2_consumes_exact_settings_and_emits_bound_tuple(
    tmp_path: Path,
) -> None:
    prepare = _prepare_module()
    source = tmp_path / "predicted.pdb"
    source.write_bytes(_two_model_pdb())
    normalized = tmp_path / "canonical_source.pdb"
    structure_map_path = tmp_path / "frustrampnn_structure_map_v1.json"
    request_path = tmp_path / "workflow_component_request_v2.json"
    settings = _selected_settings()
    settings_payload = _settings_transport_bytes(settings)
    settings_sha256 = requested_settings_sha256(settings)

    request = prepare.prepare_candidate(
        source=source,
        output_pdb=normalized,
        request_path=request_path,
        metadata=prepare._decode_metadata(
            base64.b64encode(canonical_json_bytes(_metadata())).decode("ascii"),
            source=source,
            request_version=2,
        ),
        request_version=2,
        structure_map_path=structure_map_path,
        settings_payload=settings_payload,
        settings_sha256=settings_sha256,
        settings_value_origin="operator_request",
    )

    assert request_path.read_bytes() == canonical_json_bytes(request)
    assert request["schema_version"] == 2
    assert request["component_contract_version"] == "2.0"
    assert request["settings_value_origin"] == "operator_request"
    assert request["requested_settings"] == settings.model_dump(
        mode="json", exclude_none=False
    )
    assert request["requested_settings_sha256"] == settings_sha256
    assert request["requested_settings_sha256"] == requested_settings_sha256(
        FrustraMPNNRequestedSettings.model_validate(request["requested_settings"])
    )
    assert request["effective_settings"]["settings_value_origin"] == "operator_request"
    assert request["execution_configuration"]["settings_value_origin"] == "operator_request"
    assert request["effective_settings"]["requested_settings"] == request["requested_settings"]
    assert request["effective_settings"]["resolved_chains"][0]["pdb_chain_id"] == "A"
    assert request["effective_settings"]["resolved_chains"][0]["residues"][0][
        "model_position"
    ] == 0
    structure_map = json.loads(structure_map_path.read_bytes())
    assert structure_map["selected_source_model"] == 2
    assert structure_map["altloc_policy"] == "blank_or_explicit:A"
    assert request["structure_map_sha256"] == hashlib.sha256(
        structure_map_path.read_bytes()
    ).hexdigest()
    assert request["normalized_pdb_sha256"] == hashlib.sha256(normalized.read_bytes()).hexdigest()
    assert request["execution_configuration"]["effective_settings"] == request["effective_settings"]
    assert "checkpoint_id" not in _metadata()
    validate_schema("workflow_component_request_v2", request)


def test_structure_prediction_preparer_v2_rejects_missing_or_invalid_origin_before_outputs(
    tmp_path: Path,
) -> None:
    prepare = _prepare_module()
    source = tmp_path / "predicted.pdb"
    source.write_bytes(_two_model_pdb())
    settings = _selected_settings()
    settings_payload = _settings_transport_bytes(settings)

    for origin in (None, "request", "bms_default "):
        normalized = tmp_path / "canonical_source.pdb"
        structure_map_path = tmp_path / "frustrampnn_structure_map_v1.json"
        request_path = tmp_path / "workflow_component_request_v2.json"
        with pytest.raises(ValueError, match="settings value origin"):
            prepare.prepare_candidate(
                source=source,
                output_pdb=normalized,
                request_path=request_path,
                metadata=prepare._decode_metadata(
                    base64.b64encode(canonical_json_bytes(_metadata())).decode("ascii"),
                    source=source,
                    request_version=2,
                ),
                request_version=2,
                structure_map_path=structure_map_path,
                settings_payload=settings_payload,
                settings_sha256="0" * 64,
                settings_value_origin=origin,
            )
        assert not normalized.exists()
        assert not structure_map_path.exists()
        assert not request_path.exists()


@pytest.mark.parametrize("selection_mode", ["all_protein_entities", "selected_entities"])
def test_structure_prediction_preparer_v2_resolves_mappable_entity_selections(
    tmp_path: Path,
    selection_mode: str,
) -> None:
    prepare = _prepare_module()
    source = tmp_path / f"{selection_mode}.pdb"
    source.write_bytes(_two_model_pdb())
    payload = _selected_settings().model_dump(mode="json", exclude_none=False)
    payload["protein_selection"] = {
        "mode": selection_mode,
        "entities": (
            [
                {
                    "entity_instance_id": "pdb:A",
                    "source_entity_id": None,
                    "label_asym_id": None,
                    "auth_asym_id": "A",
                }
            ]
            if selection_mode == "selected_entities"
            else []
        ),
        "residues": [],
    }
    settings = FrustraMPNNRequestedSettings.model_validate(payload)
    settings_payload = _settings_transport_bytes(settings)

    request = prepare.prepare_candidate(
        source=source,
        output_pdb=tmp_path / "canonical_source.pdb",
        request_path=tmp_path / "workflow_component_request_v2.json",
        metadata=prepare._decode_metadata(
            base64.b64encode(canonical_json_bytes(_metadata())).decode("ascii"),
            source=source,
            request_version=2,
        ),
        request_version=2,
        structure_map_path=tmp_path / "frustrampnn_structure_map_v1.json",
        settings_payload=settings_payload,
        settings_sha256=requested_settings_sha256(settings),
        settings_value_origin=settings.settings_value_origin,
    )

    assert request["effective_settings"]["resolved_chains"][0]["entity"] == {
        "entity_instance_id": "pdb:A",
        "source_entity_id": None,
        "label_asym_id": None,
        "auth_asym_id": "A",
    }
    assert request["effective_settings"]["resolved_chains"][0]["pdb_chain_id"] == "A"


def test_structure_prediction_preparer_v2_fails_closed_when_selector_cannot_map(
    tmp_path: Path,
) -> None:
    prepare = _prepare_module()
    source = tmp_path / "predicted.pdb"
    source.write_bytes(_two_model_pdb())
    normalized = tmp_path / "canonical_source.pdb"
    structure_map_path = tmp_path / "frustrampnn_structure_map_v1.json"
    request_path = tmp_path / "workflow_component_request_v2.json"
    settings = _selected_settings(auth_seq_id=99)
    settings_payload = _settings_transport_bytes(settings)

    with pytest.raises(ValueError, match="selected residue"):
        prepare.prepare_candidate(
            source=source,
            output_pdb=normalized,
            request_path=request_path,
            metadata=prepare._decode_metadata(
                base64.b64encode(canonical_json_bytes(_metadata())).decode("ascii"),
                source=source,
                request_version=2,
            ),
            request_version=2,
            structure_map_path=structure_map_path,
            settings_payload=settings_payload,
            settings_sha256=requested_settings_sha256(settings),
            settings_value_origin=settings.settings_value_origin,
        )

    assert not normalized.exists()
    assert not structure_map_path.exists()
    assert not request_path.exists()


def test_structure_prediction_workflow_is_v2_only_when_enabled_and_preserves_disabled_lane() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    enabled = workflow.split("if (params.run_frustrampnn != false)", 1)[1].split("} else {", 1)[0]
    preparer = workflow.split("process PrepareStructurePredictionFrustraMPNNCandidate", 1)[1].split(
        "process ReportStructurePredictionFrustraMPNNNotRequested", 1
    )[0]

    assert "include { CanonicalFrustraMPNNV2 } from '../modules/frustrampnn.nf'" in workflow
    assert "CanonicalFrustraMPNNV2(PrepareStructurePredictionFrustraMPNNCandidate.out.prepared)" in enabled
    assert "CanonicalFrustraMPNN(" not in enabled
    assert "checkpoint_id" not in enabled
    assert "frustrampnn_settings" in enabled
    assert "requireCompleteFrustraMPNNSettings" in workflow
    assert "FRUSTRAMPNN_SETTINGS_MAX_BYTES" in workflow
    assert "params.frustrampnn_settings instanceof CharSequence" in enabled
    assert "canonicalJsonBytes(rawSettings)" in enabled
    assert "Arrays.equals(settingsBytes, canonicalSettingsBytes)" in enabled
    assert "settingsSha256" in enabled
    assert "workflow_component_request_v2.json" in preparer
    assert "frustrampnn_structure_map_v1.json" in preparer
    assert "--request-version 2" in preparer
    assert "--settings-base64" in preparer
    assert "--settings-sha256" in preparer
    assert "--settings-value-origin" in preparer
    assert "frustrampnn_settings_value_origin" in enabled
    assert "workflow_component_request_v1.json" not in preparer
    assert "workflow_component_result_v1.json" not in enabled
    assert "frustrampnn_result_manifest_v1.json" not in enabled
    assert "ReportStructurePredictionFrustraMPNNNotRequested" in workflow
    assert "status: 'not_requested'" in workflow
