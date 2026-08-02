"""One immutable FrustraMPNN analysis configuration shared by every caller."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .contracts import canonical_sha256
from .runtime import FRUSTRAMPNN_RUNTIME_IDENTITY, runtime_identity_dict


class ConfigurationValidationError(ValueError):
    """The global FrustraMPNN configuration is incomplete or tampered."""


_BASE_CONFIGURATION: dict[str, Any] = {
    "configuration_id": "frustrampnn_global_v1",
    "schema_name": "frustrampnn_global_configuration",
    "schema_version": 1,
    "tool_id": "frustrampnn",
    "tool_version": "MegaScale",
    "runtime": {
        "sif_name": FRUSTRAMPNN_RUNTIME_IDENTITY.sif_name,
        "configured_sif_path": FRUSTRAMPNN_RUNTIME_IDENTITY.configured_sif_path,
        "sif_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256,
        "executable_path": FRUSTRAMPNN_RUNTIME_IDENTITY.executable_path,
        "executable_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.executable_sha256,
        "checkpoint_id": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_id,
        "checkpoint_path": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path,
        "checkpoint_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_sha256,
        "package_version": FRUSTRAMPNN_RUNTIME_IDENTITY.package_version,
        "source_commit": FRUSTRAMPNN_RUNTIME_IDENTITY.source_commit,
        "python_version": FRUSTRAMPNN_RUNTIME_IDENTITY.python_version,
        "pytorch_version": FRUSTRAMPNN_RUNTIME_IDENTITY.pytorch_version,
        "image_version": FRUSTRAMPNN_RUNTIME_IDENTITY.image_version,
    },
    "threshold_policy": {
        "policy_id": "frustrampnn_class_v1",
        "high_max": -1.0,
        "minimal_min": 0.58,
    },
    "normalization_policy": "frustrampnn_structure_normalizer_v1",
    "residue_mapping_policy": "frustrampnn_residue_mapping_v1",
    "selected_model_number": 1,
    "altloc_policy": "blank_or_explicit:<blank>",
    "landscape_schema": "frustrampnn_landscape_v1",
    "summary_schema": "frustrampnn_summary_v1",
    "component_adapter": "run_frustrampnn_component_v1",
}


def configuration_sha256(configuration: Mapping[str, Any]) -> str:
    """Hash the complete configuration except its self-referential digest."""

    payload = {key: value for key, value in configuration.items() if key != "configuration_sha256"}
    return canonical_sha256(payload)


GLOBAL_CONFIGURATION_SHA256 = configuration_sha256(_BASE_CONFIGURATION)
_GLOBAL_CONFIGURATION = {
    **copy.deepcopy(_BASE_CONFIGURATION),
    "configuration_sha256": GLOBAL_CONFIGURATION_SHA256,
}


def validate_configuration(configuration: Mapping[str, Any]) -> None:
    """Fail closed on identity, runtime, threshold, or self-hash drift."""

    if not isinstance(configuration, Mapping):
        raise ConfigurationValidationError("global configuration must be an object")
    if configuration.get("configuration_id") != "frustrampnn_global_v1":
        raise ConfigurationValidationError("configuration_id is not the canonical FrustraMPNN identity")
    if configuration.get("tool_id") != "frustrampnn":
        raise ConfigurationValidationError("tool_id is not frustrampnn")
    runtime = configuration.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ConfigurationValidationError("runtime configuration is missing")
    if runtime.get("checkpoint_id") != FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_id:
        raise ConfigurationValidationError("checkpoint identity does not match the canonical runtime registry")
    threshold = configuration.get("threshold_policy")
    if not isinstance(threshold, Mapping) or threshold.get("policy_id") != "frustrampnn_class_v1":
        raise ConfigurationValidationError("threshold policy identity is not canonical")
    if threshold.get("high_max") != -1.0 or threshold.get("minimal_min") != 0.58:
        raise ConfigurationValidationError("threshold policy values are not canonical")
    if configuration.get("configuration_sha256") != configuration_sha256(configuration):
        raise ConfigurationValidationError("configuration SHA-256 does not match its content")


def global_configuration() -> dict[str, Any]:
    """Return a detached copy of the canonical configuration."""

    return copy.deepcopy(_GLOBAL_CONFIGURATION)


def request_parameters() -> dict[str, Any]:
    """Return the only caller-facing analysis parameters permitted in a request."""

    validate_configuration(_GLOBAL_CONFIGURATION)
    return {
        "configuration_id": _GLOBAL_CONFIGURATION["configuration_id"],
        "configuration_sha256": _GLOBAL_CONFIGURATION["configuration_sha256"],
        "checkpoint_id": _GLOBAL_CONFIGURATION["runtime"]["checkpoint_id"],
        "threshold_policy_id": _GLOBAL_CONFIGURATION["threshold_policy"]["policy_id"],
        "selected_model_number": _GLOBAL_CONFIGURATION["selected_model_number"],
        "altloc_policy": _GLOBAL_CONFIGURATION["altloc_policy"],
    }


__all__ = [
    "ConfigurationValidationError",
    "GLOBAL_CONFIGURATION_SHA256",
    "configuration_sha256",
    "global_configuration",
    "request_parameters",
    "validate_configuration",
]
