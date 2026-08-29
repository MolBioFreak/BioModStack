from __future__ import annotations

import copy
import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError


API_ROOT = Path(__file__).resolve().parents[1]


def _configuration():
    path = API_ROOT / "services" / "frustrampnn" / "configuration.py"
    assert path.is_file(), "FrustraMPNN configuration module is missing"
    return importlib.import_module("services.frustrampnn.configuration")


def _effective(*, model_position: int = 9, selected_model_number: int = 1):
    from services.frustrampnn.settings import (
        FrustraMPNNRequestedSettings,
        FrustraMPNNResolutionIdentity,
        FrustraMPNNResolvedChainSelection,
        FrustraMPNNResolvedResidue,
        _build_effective_settings,
    )

    requested = FrustraMPNNRequestedSettings.model_validate(
        {
            "source_structure": {
                "selected_model_number": selected_model_number,
                "preferred_altloc": "",
            }
        }
    )
    residue = FrustraMPNNResolvedResidue.model_validate(
        {
            "entity_instance_id": "entity-1",
            "source_entity_id": "1",
            "label_asym_id": "A",
            "label_seq_id": 10,
            "auth_asym_id": "A",
            "auth_seq_id": 10,
            "insertion_code": "",
            "sequence_index": 10,
            "wt": "L",
            "pdb_chain_id": "A",
            "pdb_residue_id": 10,
            "pdb_insertion_code": "",
            "model_position": model_position,
            "residue_name": "LEU",
        }
    )
    chain = FrustraMPNNResolvedChainSelection.model_validate(
        {
            "entity": {
                "entity_instance_id": "entity-1",
                "source_entity_id": "1",
                "label_asym_id": "A",
                "auth_asym_id": "A",
            },
            "pdb_chain_id": "A",
            "residues": [residue.model_dump(mode="json")],
        }
    )
    identity = FrustraMPNNResolutionIdentity.model_validate(
        {
            "source_artifact_sha256": "a" * 64,
            "structure_map_sha256": "b" * 64,
            "normalized_pdb_sha256": "c" * 64,
        }
    )
    return _build_effective_settings(
        requested,
        resolved_chains=(chain,),
        resolution_identity=identity,
    )


def test_historical_global_v1_configuration_and_request_projection_are_unchanged() -> None:
    module = _configuration()
    config = module.global_configuration()

    assert config["configuration_id"] == "frustrampnn_global_v1"
    assert config["schema_name"] == "frustrampnn_global_configuration"
    assert config["schema_version"] == 1
    assert config["runtime"]["checkpoint_id"] == "megascale.ckpt"
    assert config["threshold_policy"] == {
        "policy_id": "frustrampnn_class_v1",
        "high_max": -1.0,
        "minimal_min": 0.58,
    }
    assert config["configuration_sha256"] == module.configuration_sha256(config)
    assert config["configuration_sha256"] == module.GLOBAL_CONFIGURATION_SHA256
    assert module.request_parameters() == {
        "configuration_id": "frustrampnn_global_v1",
        "configuration_sha256": module.GLOBAL_CONFIGURATION_SHA256,
        "checkpoint_id": "megascale.ckpt",
        "threshold_policy_id": "frustrampnn_class_v1",
        "selected_model_number": 1,
        "altloc_policy": "blank_or_explicit:<blank>",
    }


def test_historical_v1_validation_still_rejects_runtime_or_hash_tampering() -> None:
    module = _configuration()
    config = module.global_configuration()

    tampered_hash = copy.deepcopy(config)
    tampered_hash["configuration_sha256"] = "0" * 64
    with pytest.raises(module.ConfigurationValidationError, match="configuration SHA-256"):
        module.validate_configuration(tampered_hash)

    tampered_runtime = copy.deepcopy(config)
    tampered_runtime["runtime"]["checkpoint_id"] = "other.ckpt"
    with pytest.raises(module.ConfigurationValidationError, match="checkpoint"):
        module.validate_configuration(tampered_runtime)


def test_execution_configuration_v3_is_one_exact_per_request_receipt() -> None:
    module = _configuration()
    from services.frustrampnn.settings import runtime_identity_sha256

    effective = _effective()
    receipt = module.execution_configuration(effective)
    payload = receipt.model_dump(mode="json")

    assert isinstance(receipt, module.FrustraMPNNExecutionConfigurationV3)
    assert receipt.configuration_id == "frustrampnn_execution_configuration_v3"
    assert receipt.schema_name == "frustrampnn_execution_configuration"
    assert receipt.schema_version == 3
    assert receipt.effective_settings == effective
    assert receipt.requested_settings_sha256 == effective.settings_sha256
    assert receipt.effective_settings_sha256 == effective.effective_settings_sha256
    assert (
        receipt.capability_inventory_byte_sha256
        == effective.capability_inventory_byte_sha256
    )
    assert receipt.classification_policy_sha256 == effective.threshold_policy_sha256
    assert receipt.runtime_identity_sha256 == runtime_identity_sha256()
    assert receipt.source_artifact_sha256 == "a" * 64
    assert receipt.structure_map_sha256 == "b" * 64
    assert receipt.normalized_pdb_sha256 == "c" * 64
    assert receipt.configuration_sha256 == module.configuration_sha256(payload)
    assert "default_requested_settings" not in payload
    assert "global" not in receipt.configuration_id
    module.validate_configuration(payload)


def test_configuration_id_is_generation_identity_not_request_identity() -> None:
    module = _configuration()
    first = module.execution_configuration(_effective(model_position=9))
    second = module.execution_configuration(
        _effective(model_position=10, selected_model_number=2)
    )

    assert first.configuration_id == second.configuration_id
    assert first.requested_settings_sha256 != second.requested_settings_sha256
    assert first.effective_settings_sha256 != second.effective_settings_sha256
    assert first.configuration_sha256 != second.configuration_sha256


def test_execution_configuration_requires_one_validated_effective_object() -> None:
    module = _configuration()
    effective = _effective()

    with pytest.raises((TypeError, module.ConfigurationValidationError), match="typed|effective"):
        module.execution_configuration(effective.model_dump(mode="json"))

    payload = effective.model_dump(mode="json")
    payload["effective_settings_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="effective settings SHA-256"):
        module.FrustraMPNNExecutionConfigurationV3.model_validate(
            {
                **module.execution_configuration(effective).model_dump(mode="json"),
                "effective_settings": payload,
            }
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda receipt: receipt["runtime"].update(
                {"checkpoint_id": "other.ckpt"}
            ),
            "runtime",
        ),
        (
            lambda receipt: receipt.update(
                {"capability_inventory_byte_sha256": "0" * 64}
            ),
            "capability inventory",
        ),
        (
            lambda receipt: receipt.update(
                {"requested_settings_sha256": "0" * 64}
            ),
            "requested settings",
        ),
        (
            lambda receipt: receipt.update(
                {"structure_map_sha256": "d" * 64}
            ),
            "structure map",
        ),
        (
            lambda receipt: receipt["effective_settings"]["resolution_identity"].update(
                {"normalized_pdb_sha256": "d" * 64}
            ),
            "effective settings",
        ),
    ],
)
def test_execution_configuration_rejects_tampering_after_outer_hash_recompute(
    mutation, message
) -> None:
    module = _configuration()
    receipt = module.execution_configuration(_effective()).model_dump(mode="json")
    mutation(receipt)
    receipt["configuration_sha256"] = module.configuration_sha256(receipt)

    with pytest.raises(
        (ValidationError, module.ConfigurationValidationError), match=message
    ):
        module.validate_configuration(receipt)
