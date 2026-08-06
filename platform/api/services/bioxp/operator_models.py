from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

ActionKind = Literal["primitive", "meta"]
ActionSafety = Literal["read_only", "service", "motion", "stop", "emergency"]
ActionAssessment = Literal["pass", "fail", "unverified"]
ActionStatus = Literal["acknowledged", "admission_pending", "queued", "completed", "failed", "blocked", "rejected"]


class OperatorInputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    wire_name: str | None = Field(default=None, min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=120)
    value_type: Literal["string", "integer", "number", "boolean", "enum", "json"]
    location: Literal["path", "query", "body"] = "body"
    required: StrictBool
    description: str = Field(default="", max_length=1000)
    unit: str | None = Field(default=None, max_length=32)
    enum_values: list[str] = Field(default_factory=list, max_length=128)
    minimum: StrictFloat | StrictInt | None = None
    maximum: StrictFloat | StrictInt | None = None
    exclusive_minimum: StrictFloat | StrictInt | None = None
    exclusive_maximum: StrictFloat | StrictInt | None = None
    default: Any = None


class OperatorDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=160)
    met: StrictBool
    reason: str | None = Field(default=None, max_length=1000)


class OperatorDashboardAxis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    axis: str = Field(min_length=1, max_length=16)
    reference: str = Field(min_length=1, max_length=40)
    position_steps: StrictInt | None = None
    speed_steps_s: StrictInt | None = None
    run_current: StrictInt | None = None
    standby_current: StrictInt | None = None
    left_switch_state: StrictInt | None = None
    right_switch_state: StrictInt | None = None
    left_switch_raw_active: StrictBool | None = None
    right_switch_raw_active: StrictBool | None = None
    left_switch_active: StrictBool | None = None
    right_switch_active: StrictBool | None = None
    left_switch_disabled: StrictBool | None = None
    right_switch_disabled: StrictBool | None = None
    coordinate_contract: str | None = Field(default=None, max_length=80)
    min_steps: StrictInt | None = None
    max_steps: StrictInt | None = None
    motor_temperature_c: StrictFloat | StrictInt | None = None
    motor_temperature_available: StrictBool


class OperatorDashboardTemperature(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sensor: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    unit: Literal["°C"]
    temperature_c: StrictFloat | StrictInt | None = None
    available: StrictBool


class OperatorDashboard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["bioxp.operator_dashboard.v1"]
    ownership_generation: StrictInt = Field(ge=0)
    connection: dict[str, Any]
    motion: dict[str, Any]
    operation: dict[str, Any]
    enclosure: dict[str, Any]
    axes: list[OperatorDashboardAxis] = Field(max_length=16)
    z_axis: dict[str, Any]
    temperatures: list[OperatorDashboardTemperature] = Field(max_length=32)
    pipettes: dict[str, Any]
    snapshot: dict[str, Any]


class OperatorActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=160)
    subsystem: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=64)
    kind: ActionKind
    safety_class: ActionSafety
    description: str = Field(default="", max_length=2000)
    source_anchor: str | None = Field(default=None, max_length=1000)
    informational_method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    informational_path: str = Field(min_length=1, max_length=300, pattern=r"^/")
    provider_available: StrictBool
    provider_unavailable_reason: str | None = Field(default=None, max_length=1000)
    available: StrictBool
    unavailable_reason: str | None = Field(default=None, max_length=1000)
    enabled: StrictBool
    disabled_reason: str | None = Field(default=None, max_length=1000)
    dependencies: list[OperatorDependency] = Field(default_factory=list, max_length=32)
    requires_confirmation: StrictBool = True
    timeout_seconds: StrictInt | StrictFloat = Field(ge=0.1, le=3600)
    required_provider_capability: str | None = Field(default=None, min_length=1, max_length=120)
    inputs: list[OperatorInputSpec] = Field(default_factory=list, max_length=128)
    stages: list[str] = Field(default_factory=list, max_length=128)


class OperatorControlCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_name: Literal["bioxp.operator_control_catalog"]
    schema_version: Literal["bioxp.operator_control_catalog.v1"]
    machine_serial: str = Field(min_length=1, max_length=80)
    ownership_generation: StrictInt = Field(ge=0)
    registry_sha256: str = Field(pattern=r"^(?:[0-9a-f]{64}|unavailable)$")
    evidence_lock_sha256: str = Field(pattern=r"^(?:[0-9a-f]{64}|unavailable)$")
    source_authority_verified: StrictBool
    dashboard: OperatorDashboard
    actions: list[OperatorActionSpec] = Field(max_length=256)

    @field_validator("actions")
    @classmethod
    def unique_action_ids(cls, value: list[OperatorActionSpec]) -> list[OperatorActionSpec]:
        ids = [item.action_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("operator action ids must be unique")
        return value

    @model_validator(mode="after")
    def source_authority_consistent(self) -> "OperatorControlCatalog":
        hashes_available = self.registry_sha256 != "unavailable" and self.evidence_lock_sha256 != "unavailable"
        if self.source_authority_verified is not hashes_available:
            raise ValueError("source_authority_verified must match registry/evidence authority availability")
        return self


class OperatorActionInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    expected_ownership_generation: StrictInt = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=64)


class OperatorAdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    expected_ownership_generation: StrictInt = Field(ge=1)
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=64)


class OperatorAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    ownership_generation: StrictInt = Field(ge=0)
    enabled: StrictBool
    disabled_reason: str | None = Field(default=None, max_length=1000)
    dependencies: list[OperatorDependency] = Field(default_factory=list, max_length=32)


class OperatorAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    expected_ownership_generation: StrictInt = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    verdict: Literal["pass", "fail"]
    note: str = Field(min_length=1, max_length=4000)


class OperatorActionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["bioxp.operator_action_receipt.v1"]
    command_id: str = Field(min_length=1, max_length=128)
    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    kind: ActionKind
    safety_class: ActionSafety
    status: ActionStatus
    idempotency_key: str = Field(min_length=8, max_length=128)
    ownership_generation: StrictInt = Field(ge=0)
    started_at: str = Field(min_length=1, max_length=80)
    finished_at: str | None = Field(default=None, max_length=80)
    duration_ms: StrictInt | StrictFloat | None = Field(default=None, ge=0)
    remote_acknowledged: StrictBool
    controller_acknowledged: StrictBool = False
    controller_terminal_state_verified: StrictBool = False
    physical_effect_verified: StrictBool
    machine_assessment: ActionAssessment
    operator_assessment: Literal["pass", "fail"] | None = None
    operator_note: str | None = Field(default=None, max_length=4000)
    operator_assessment_idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    operator_assessed_at: StrictFloat | StrictInt | None = Field(default=None, ge=0)
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=64)
    requested_inputs: dict[str, Any] | None = Field(default=None, max_length=64)
    response: dict[str, Any] | None = None
    authority_receipt_id: str | None = Field(default=None, max_length=128)
    authority_receipt_status: str | dict[str, Any] | None = None
    observation_receipt_id: str | None = Field(default=None, max_length=128)
    observes_command_id: str | None = Field(default=None, max_length=128)
    error: str | None = Field(default=None, max_length=4000)
    stage_receipts: list[dict[str, Any]] = Field(default_factory=list, max_length=256)


class OperatorActionHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["bioxp.operator_action_history.v1"]
    receipts: list[OperatorActionReceipt] = Field(max_length=500)
