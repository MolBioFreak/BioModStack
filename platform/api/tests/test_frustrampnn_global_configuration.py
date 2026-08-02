from __future__ import annotations

import copy
import importlib
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]


def _configuration():
    path = API_ROOT / "services" / "frustrampnn" / "configuration.py"
    assert path.is_file(), "global FrustraMPNN configuration module is missing"
    return importlib.import_module("services.frustrampnn.configuration")


def test_global_configuration_has_one_hashed_runtime_and_analysis_identity() -> None:
    module = _configuration()
    config = module.global_configuration()

    assert config["configuration_id"] == "frustrampnn_global_v1"
    assert config["tool_id"] == "frustrampnn"
    assert config["runtime"]["checkpoint_id"] == "megascale.ckpt"
    assert config["threshold_policy"]["policy_id"] == "frustrampnn_class_v1"
    assert config["normalization_policy"] == "frustrampnn_structure_normalizer_v1"
    assert config["residue_mapping_policy"] == "frustrampnn_residue_mapping_v1"
    assert config["configuration_sha256"] == module.configuration_sha256(config)
    assert config["configuration_sha256"] == module.GLOBAL_CONFIGURATION_SHA256


def test_configuration_validation_rejects_hash_or_runtime_policy_tampering() -> None:
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


def test_request_parameters_are_derived_from_the_global_configuration() -> None:
    module = _configuration()
    parameters = module.request_parameters()

    assert parameters == {
        "configuration_id": "frustrampnn_global_v1",
        "configuration_sha256": module.GLOBAL_CONFIGURATION_SHA256,
        "checkpoint_id": "megascale.ckpt",
        "threshold_policy_id": "frustrampnn_class_v1",
        "selected_model_number": 1,
        "altloc_policy": "blank_or_explicit:<blank>",
    }
