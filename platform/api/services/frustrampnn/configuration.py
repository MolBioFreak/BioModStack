"""Versioned FrustraMPNN configuration projections and execution receipts."""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .contracts import canonical_sha256
from .runtime import FRUSTRAMPNN_RUNTIME_IDENTITY, runtime_identity_dict
from .settings import (
    FrustraMPNNEffectiveSettings,
    compatible_effective_settings_payload,
    default_settings,
    effective_settings_sha256,
    load_capability_inventory,
    runtime_identity_sha256,
)


class ConfigurationValidationError(ValueError):
    """A FrustraMPNN configuration projection is incomplete or tampered."""


_NORMALIZATION_POLICY = "frustrampnn_structure_normalizer_v1"
_RESIDUE_MAPPING_POLICY = "frustrampnn_residue_mapping_v1"
_RUNTIME = runtime_identity_dict()
_DEFAULT_SETTINGS = default_settings()
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
Sha256String = Annotated[str, Field(pattern=_SHA256_PATTERN)]
NonEmptyString = Annotated[str, Field(min_length=1)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FrustraMPNNRuntimeIdentityV1(_StrictFrozenModel):
    sif_name: NonEmptyString
    configured_sif_path: NonEmptyString
    sif_sha256: Sha256String
    executable_path: NonEmptyString
    executable_sha256: Sha256String
    checkpoint_id: NonEmptyString
    checkpoint_path: NonEmptyString
    checkpoint_sha256: Sha256String
    package_version: NonEmptyString
    source_commit: NonEmptyString
    python_version: NonEmptyString
    pytorch_version: NonEmptyString
    image_version: NonEmptyString


class FrustraMPNNExecutionConfigurationV2(_StrictFrozenModel):
    """One immutable per-request execution configuration receipt."""

    configuration_id: Literal["frustrampnn_execution_configuration_v2"] = (
        "frustrampnn_execution_configuration_v2"
    )
    schema_name: Literal["frustrampnn_execution_configuration"] = (
        "frustrampnn_execution_configuration"
    )
    schema_version: Literal[2] = 2
    tool_id: Literal["frustrampnn"] = "frustrampnn"
    tool_version: Literal["MegaScale"] = "MegaScale"
    effective_settings: FrustraMPNNEffectiveSettings
    settings_value_origin: Literal["bms_default", "operator_request"]
    requested_settings_sha256: Sha256String
    effective_settings_sha256: Sha256String
    capability_inventory_byte_sha256: Sha256String
    classification_policy_sha256: Sha256String
    runtime: FrustraMPNNRuntimeIdentityV1
    runtime_identity_sha256: Sha256String
    normalization_policy_id: Literal["frustrampnn_structure_normalizer"] = (
        "frustrampnn_structure_normalizer"
    )
    normalization_policy_version: Literal[1] = 1
    threshold_policy_id: Literal["frustrampnn_class_v1"] = "frustrampnn_class_v1"
    source_artifact_sha256: Sha256String
    structure_map_sha256: Sha256String
    normalized_pdb_sha256: Sha256String
    configuration_sha256: Sha256String

    @model_validator(mode="after")
    def _validate_cross_bindings(self) -> FrustraMPNNExecutionConfigurationV2:
        if self.runtime.model_dump(mode="json") != _RUNTIME:
            raise ValueError("runtime identity does not match the immutable runtime registry")
        if self.runtime_identity_sha256 != runtime_identity_sha256():
            raise ValueError("runtime identity SHA-256 does not match immutable runtime")
        if self.settings_value_origin != self.effective_settings.settings_value_origin:
            raise ValueError("settings value origin is not cross-bound")
        if self.requested_settings_sha256 != self.effective_settings.settings_sha256:
            raise ValueError("requested settings SHA-256 is not cross-bound")
        if self.effective_settings_sha256 != effective_settings_sha256(
            self.effective_settings
        ):
            raise ValueError("effective settings SHA-256 is not cross-bound")
        if (
            self.capability_inventory_byte_sha256
            != self.effective_settings.capability_inventory_byte_sha256
        ):
            raise ValueError("capability inventory byte SHA-256 is not cross-bound")
        _, installed_inventory_sha256 = load_capability_inventory()
        if self.capability_inventory_byte_sha256 != installed_inventory_sha256:
            raise ValueError(
                "capability inventory byte SHA-256 does not match installed bytes"
            )
        if (
            self.classification_policy_sha256
            != self.effective_settings.threshold_policy_sha256
        ):
            raise ValueError("classification policy SHA-256 is not cross-bound")
        if (
            self.normalization_policy_id
            != self.effective_settings.normalization_policy_id
            or self.normalization_policy_version
            != self.effective_settings.normalization_policy_version
        ):
            raise ValueError("normalization policy identity is not cross-bound")
        if self.threshold_policy_id != self.effective_settings.threshold_policy_id:
            raise ValueError("threshold policy identity is not cross-bound")

        resolution = self.effective_settings.resolution_identity
        if self.source_artifact_sha256 != resolution.source_artifact_sha256:
            raise ValueError("source artifact SHA-256 is not cross-bound")
        if self.structure_map_sha256 != resolution.structure_map_sha256:
            raise ValueError("structure map SHA-256 is not cross-bound")
        if self.normalized_pdb_sha256 != resolution.normalized_pdb_sha256:
            raise ValueError("normalized PDB SHA-256 is not cross-bound")
        if self.configuration_sha256 != configuration_sha256(
            self.model_dump(mode="json")
        ):
            raise ValueError("configuration SHA-256 does not match its content")
        return self


def _legacy_altloc_policy() -> str:
    altloc = _DEFAULT_SETTINGS.source_structure.preferred_altloc or "<blank>"
    return f"blank_or_explicit:{altloc}"


_LEGACY_BASE_CONFIGURATION: dict[str, Any] = {
    "configuration_id": "frustrampnn_global_v1",
    "schema_name": "frustrampnn_global_configuration",
    "schema_version": 1,
    "tool_id": "frustrampnn",
    "tool_version": "MegaScale",
    "runtime": copy.deepcopy(_RUNTIME),
    "threshold_policy": {
        "policy_id": "frustrampnn_class_v1",
        "high_max": _DEFAULT_SETTINGS.classification_policy.high_max,
        "minimal_min": _DEFAULT_SETTINGS.classification_policy.minimal_min,
    },
    "normalization_policy": _NORMALIZATION_POLICY,
    "residue_mapping_policy": _RESIDUE_MAPPING_POLICY,
    "selected_model_number": _DEFAULT_SETTINGS.source_structure.selected_model_number,
    "altloc_policy": _legacy_altloc_policy(),
    "landscape_schema": "frustrampnn_landscape_v1",
    "summary_schema": "frustrampnn_summary_v1",
    "component_adapter": "run_frustrampnn_component_v1",
}


def configuration_sha256(configuration: Mapping[str, Any] | BaseModel) -> str:
    """Hash a complete configuration except its self-referential digest."""

    source = copy.deepcopy(
        configuration.model_dump(mode="json", exclude_none=False)
        if isinstance(configuration, BaseModel)
        else dict(configuration)
    )
    effective = source.get("effective_settings")
    if isinstance(effective, Mapping):
        source["effective_settings"] = compatible_effective_settings_payload(
            effective
        )
    return canonical_sha256(
        {
            key: value
            for key, value in source.items()
            if key != "configuration_sha256"
        }
    )


GLOBAL_CONFIGURATION_SHA256 = configuration_sha256(_LEGACY_BASE_CONFIGURATION)
_LEGACY_GLOBAL_CONFIGURATION = {
    **copy.deepcopy(_LEGACY_BASE_CONFIGURATION),
    "configuration_sha256": GLOBAL_CONFIGURATION_SHA256,
}


def _validate_legacy_configuration(configuration: Mapping[str, Any]) -> None:
    if configuration.get("configuration_id") != "frustrampnn_global_v1":
        raise ConfigurationValidationError(
            "configuration_id is not the canonical FrustraMPNN v1 identity"
        )
    if configuration.get("tool_id") != "frustrampnn":
        raise ConfigurationValidationError("tool_id is not frustrampnn")
    runtime = configuration.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ConfigurationValidationError("runtime configuration is missing")
    if runtime.get("checkpoint_id") != FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_id:
        raise ConfigurationValidationError(
            "checkpoint identity does not match the canonical runtime registry"
        )
    threshold = configuration.get("threshold_policy")
    if (
        not isinstance(threshold, Mapping)
        or threshold.get("policy_id") != "frustrampnn_class_v1"
    ):
        raise ConfigurationValidationError("threshold policy identity is not canonical")
    if (
        threshold.get("high_max")
        != _DEFAULT_SETTINGS.classification_policy.high_max
        or threshold.get("minimal_min")
        != _DEFAULT_SETTINGS.classification_policy.minimal_min
    ):
        raise ConfigurationValidationError("threshold policy values are not canonical")
    if configuration.get("configuration_sha256") != configuration_sha256(
        configuration
    ):
        raise ConfigurationValidationError(
            "configuration SHA-256 does not match its content"
        )


def validate_configuration(
    configuration: Mapping[str, Any] | FrustraMPNNExecutionConfigurationV2,
) -> None:
    """Validate historical global v1 or per-request execution configuration v2."""

    if isinstance(configuration, FrustraMPNNExecutionConfigurationV2):
        payload = configuration.model_dump(mode="json", exclude_none=False)
    elif isinstance(configuration, Mapping):
        payload = dict(configuration)
    else:
        raise ConfigurationValidationError("configuration must be an object")

    if payload.get("schema_version") == 2:
        try:
            FrustraMPNNExecutionConfigurationV2.model_validate(payload)
        except ValidationError as exc:
            raise ConfigurationValidationError(
                f"execution configuration is invalid: {exc}"
            ) from exc
    else:
        _validate_legacy_configuration(payload)


def global_configuration() -> dict[str, Any]:
    """Return the detached historical v1 default projection for existing readers."""

    return copy.deepcopy(_LEGACY_GLOBAL_CONFIGURATION)


def execution_configuration(
    effective: FrustraMPNNEffectiveSettings,
) -> FrustraMPNNExecutionConfigurationV2:
    """Build one v2 receipt from one already validated effective settings object."""

    if not isinstance(effective, FrustraMPNNEffectiveSettings):
        raise ConfigurationValidationError(
            "effective must be a typed FrustraMPNN effective settings object"
        )
    try:
        validated_effective = FrustraMPNNEffectiveSettings.model_validate(
            effective.model_dump(mode="json", exclude_none=False)
        )
    except ValidationError as exc:
        raise ConfigurationValidationError(
            f"effective settings are invalid: {exc}"
        ) from exc

    resolution = validated_effective.resolution_identity
    payload: dict[str, Any] = {
        "configuration_id": "frustrampnn_execution_configuration_v2",
        "schema_name": "frustrampnn_execution_configuration",
        "schema_version": 2,
        "tool_id": "frustrampnn",
        "tool_version": "MegaScale",
        "effective_settings": validated_effective.model_dump(
            mode="json", exclude_none=False
        ),
        "settings_value_origin": validated_effective.settings_value_origin,
        "requested_settings_sha256": validated_effective.settings_sha256,
        "effective_settings_sha256": effective_settings_sha256(validated_effective),
        "capability_inventory_byte_sha256": (
            validated_effective.capability_inventory_byte_sha256
        ),
        "classification_policy_sha256": validated_effective.threshold_policy_sha256,
        "runtime": copy.deepcopy(_RUNTIME),
        "runtime_identity_sha256": runtime_identity_sha256(),
        "normalization_policy_id": validated_effective.normalization_policy_id,
        "normalization_policy_version": validated_effective.normalization_policy_version,
        "threshold_policy_id": validated_effective.threshold_policy_id,
        "source_artifact_sha256": resolution.source_artifact_sha256,
        "structure_map_sha256": resolution.structure_map_sha256,
        "normalized_pdb_sha256": resolution.normalized_pdb_sha256,
    }
    payload["configuration_sha256"] = configuration_sha256(payload)
    try:
        return FrustraMPNNExecutionConfigurationV2.model_validate(payload)
    except ValidationError as exc:
        raise ConfigurationValidationError(
            f"execution configuration is invalid: {exc}"
        ) from exc


def request_parameters() -> dict[str, Any]:
    """Preserve the historical v1 request projection until its owner-path cutover."""

    validate_configuration(_LEGACY_GLOBAL_CONFIGURATION)
    return {
        "configuration_id": _LEGACY_GLOBAL_CONFIGURATION["configuration_id"],
        "configuration_sha256": _LEGACY_GLOBAL_CONFIGURATION[
            "configuration_sha256"
        ],
        "checkpoint_id": _LEGACY_GLOBAL_CONFIGURATION["runtime"]["checkpoint_id"],
        "threshold_policy_id": _LEGACY_GLOBAL_CONFIGURATION["threshold_policy"][
            "policy_id"
        ],
        "selected_model_number": _LEGACY_GLOBAL_CONFIGURATION[
            "selected_model_number"
        ],
        "altloc_policy": _LEGACY_GLOBAL_CONFIGURATION["altloc_policy"],
    }


__all__ = [
    "ConfigurationValidationError",
    "FrustraMPNNExecutionConfigurationV2",
    "FrustraMPNNRuntimeIdentityV1",
    "GLOBAL_CONFIGURATION_SHA256",
    "configuration_sha256",
    "execution_configuration",
    "global_configuration",
    "request_parameters",
    "validate_configuration",
]
