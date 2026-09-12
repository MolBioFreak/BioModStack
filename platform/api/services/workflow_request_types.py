"""Native request DTOs shared by submission and dependency-only provisioning.

Dependency projections are metadata, not executable requests or staged inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from schemas import ExecutionPolicy
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    default_settings as default_frustrampnn_settings,
    validate_complete_requested_settings,
)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    execution_target_id: str | None = Field(default=None, min_length=1, max_length=160)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    notes: str = Field(default="", max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    backend: Literal["protenix_v2_ensemble", "confornets", "external_import"]
    ordered_seeds: list[int] = Field(min_length=1)
    samples_per_seed: int = Field(ge=1, le=100)
    feature_policy: dict[str, Any]
    runtime_policy: dict[str, Any]
    analysis_policy: dict[str, Any]
    registered_snapshot_id: str | None = None
    # The wire name stays plural for compatibility with the launcher contract.
    # External import is nevertheless singular in authority: exactly one item
    # is accepted by this bounded collection.
    registered_artifact_ids: list[str] = Field(default_factory=list, max_length=1)
    registered_sequence_id: str | None = None
    registered_reference_ids: list[str] = Field(default_factory=list, max_length=2)
    registered_checkpoint_id: str | None = None
    registered_config_id: str | None = None
    registered_transfer_id: str | None = None
    confornets: dict[str, Any] | None = None
    state_landscape_comparison: dict[str, Any] | None = None
    frustrampnn_settings: FrustraMPNNRequestedSettings = Field(
        default_factory=default_frustrampnn_settings
    )

    @field_validator("frustrampnn_settings", mode="before")
    @classmethod
    def _complete_frustrampnn_settings(
        cls, value: Any,
    ) -> FrustraMPNNRequestedSettings:
        return validate_complete_requested_settings(value)

@dataclass(frozen=True)
class NativeWorkflowDependencyRequest:
    """Server-normalized selection for the shared SelectedExecutionPlan owner.

    input_bindings are declared identities, not materialization receipts. Actual
    launch still validates source bytes and scientific admission at its owner.
    """

    model_id: str
    mode: str
    requested_params: dict[str, Any]
    effective_params: dict[str, Any]
    entrypoint: str
    input_bindings: tuple[dict[str, Any], ...] = ()
