"""Closed workflow wire contracts; native scientific payloads remain lossless JSON."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictInt, field_validator, model_validator


class ProtocolWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Identity = Annotated[str, Field(min_length=1)]
Generation = Annotated[StrictInt, Field(ge=0)]
WorkflowPhase = Literal["queued", "preparing", "starting", "executing", "waiting", "waking", "epilogue", "cleanup", "reconciling", "terminal"]
WorkflowGate = Literal["ordinary_pause", "deferred_pause", "delaypoint", "review", "error_hold"]
CommandStatus = Literal["queued", "dispatched", "interrupting", "completed", "failed", "interrupted", "cleared", "ambiguous", "rejected"]


class ProtocolSubmission(ProtocolWireModel):
    expected_connection_generation: Generation
    source_type: Literal["native", "oem_xml"] = "native"
    # Native authoring/prepared inputs are owned and validated by the robot
    # compiler, not translated into the historical BMS lifecycle-step language.
    document: dict[str, JsonValue] | None = None
    xml_path: str | None = None
    dry_run: StrictBool = True
    idempotency_key: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    live_execution: dict[str, JsonValue] | None = None
    live_execution_ack: StrictBool = False
    operator_id: Annotated[str, Field(max_length=120)] | None = None
    physical_console_verified: StrictBool = False
    deck_manifest: dict[str, JsonValue] | None = None
    preflight: dict[str, JsonValue] | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    snapshot_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def selected_source(self) -> ProtocolSubmission:
        if self.source_type == "native" and (self.document is None or self.xml_path is not None):
            raise ValueError("native input requires document only")
        if self.source_type == "oem_xml" and (not self.xml_path or self.document is not None):
            raise ValueError("OEM XML input requires xml_path only")
        if not self.dry_run and not (self.idempotency_key or "").strip():
            raise ValueError("live submission requires its original idempotency key")
        return self


class ControlBinding(ProtocolWireModel):
    expected_connection_generation: Generation
    idempotency_key: Annotated[str, Field(min_length=1, max_length=256)]
    command_id: Identity
    expected_ownership_generation: Generation

    @field_validator("idempotency_key", "command_id")
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity must not be blank")
        return value


class PauseControl(ControlBinding):
    action: Literal["pause"]
    mode: Literal["ordinary", "deferred"]


class WakeControl(ControlBinding):
    action: Literal["wake"]
    gate_id: Identity


class ContinueControl(ControlBinding):
    action: Literal["continue"]
    gate: Literal["ordinary_pause", "deferred_pause", "delaypoint"]
    gate_id: Identity


class SafeStopControl(ControlBinding):
    action: Literal["safe_stop"]


class AbortControl(ControlBinding):
    action: Literal["abort"]


ProtocolControlRequest = Annotated[PauseControl | WakeControl | ContinueControl | SafeStopControl | AbortControl, Field(discriminator="action")]


class ProtocolReviewRequest(ControlBinding):
    reviewer: Annotated[str, Field(min_length=1, max_length=120)] = "operator"
    note: Annotated[str, Field(max_length=4000)] | None = None
    stage_id: Identity
    action_id: Identity | None = None


class ProtocolCommand(ProtocolWireModel):
    command_id: Identity
    idempotency_key: Identity
    ownership_generation: Generation
    state_version: Generation
    status: CommandStatus
    terminal: StrictBool
    status_path: Identity


class ProtocolControlResponse(ProtocolWireModel):
    control_command_id: Identity
    idempotency_key: Identity
    command_id: Identity
    job_id: Identity
    ownership_generation: Generation
    state_version: Generation
    accepted: StrictBool
    reached: StrictBool
    phase: WorkflowPhase
    gate: WorkflowGate | None
    gate_id: Identity | None
    status_path: Identity


class RequestedPause(ProtocolWireModel):
    action: Literal["pause"]
    mode: Literal["ordinary", "deferred"]


class RequestedWake(ProtocolWireModel):
    action: Literal["wake"]
    gate_id: Identity


class RequestedContinue(ProtocolWireModel):
    action: Literal["continue"]
    gate: Literal["ordinary_pause", "deferred_pause", "delaypoint"]
    gate_id: Identity


class RequestedTermination(ProtocolWireModel):
    action: Literal["safe_stop", "abort"]


RequestedControl = Annotated[RequestedPause | RequestedWake | RequestedContinue | RequestedTermination, Field(discriminator="action")]


class ProtocolWorkflowState(ProtocolWireModel):
    command_id: Identity
    phase: WorkflowPhase
    gate: WorkflowGate | None
    gate_id: Identity | None
    source_occurrence_id: Identity | None
    requested_control: RequestedControl | None
    last_control_id: Identity | None
    reached_control_id: Identity | None
    held_reason: Literal[
        "recording_failed", "native_authority_unavailable", "source_error_hold",
        "review_blocks_termination", "source_model_unknown", "termination_boundary_unreached",
        "workflow_owner_lost", "workflow_settlement_unknown",
    ] | None
    child_command_ids: list[Identity]


class ProtocolStageState(ProtocolWireModel):
    stage_id: Identity
    title: str | None
    status: Literal["pending", "running", "paused", "failed", "completed"]
    review_required: StrictBool
    current_action_id: str | None
    completed_actions: list[str]
    pause_marker_action_id: str | None


class ProtocolEvent(ProtocolWireModel):
    sequence: StrictInt
    event: str
    stage_id: str | None
    action_id: str | None
    detail: dict[str, JsonValue]


class ProtocolSourceWell(ProtocolWireModel):
    content: str | None
    volume: float
    capacity: float
    empty: StrictBool
    zone_index: StrictInt | None = None


class ProtocolSourceTray(ProtocolWireModel):
    tray_id: str
    location: StrictInt
    wells: list[ProtocolSourceWell]
    tip_type: StrictInt | None = None
    tray_empty: StrictBool | None = None
    strip_color: str | None = None


class ProtocolSourceModel(ProtocolWireModel):
    logical_tip_present: StrictBool | None = None
    carried_plate_present: StrictBool | None = None
    allow_to_stop: StrictBool | None = None
    trays: dict[str, ProtocolSourceTray] = Field(default_factory=dict)
    strips: list[ProtocolSourceTray] = Field(default_factory=list)
    tip_trays: list[ProtocolSourceTray] = Field(default_factory=list)
    pressure_baseline: list[float] = Field(default_factory=list)
    pressure_history: list[list[float]] = Field(default_factory=list)
    fluid_name: str | None = None
    tip_zone_index: StrictInt | None = None
    old_tip_well: str | None = None


class ProtocolRuntimeState(ProtocolWireModel):
    protocol_id: Identity
    dry_run: StrictBool
    job_id: str | None
    current_stage_id: str | None
    paused: StrictBool
    awaiting_review: StrictBool
    completed: StrictBool
    pause_reason: str | None
    stage_states: dict[str, ProtocolStageState]
    events: list[ProtocolEvent]
    action_results: list[dict[str, JsonValue]]
    workflow: ProtocolWorkflowState | None = None
    source_model: ProtocolSourceModel = Field(default_factory=ProtocolSourceModel)


class ProtocolExecution(ProtocolWireModel):
    dry_run: StrictBool
    runtime_state: ProtocolRuntimeState
    live_contract: dict[str, JsonValue] | None = None
    idempotency_binding: dict[str, JsonValue] | None = None


class ProtocolSource(ProtocolWireModel):
    source_type: Literal["native", "oem_xml"]
    source_path: str | None
    coverage: dict[str, JsonValue]
    experiment: dict[str, JsonValue]
    inventory: dict[str, JsonValue]
    document: dict[str, JsonValue]


class ProtocolPendingReview(ProtocolWireModel):
    stage_id: str | None
    action_id: str | None
    reason: str | None


class ProtocolOperator(ProtocolWireModel):
    manual_review_required: StrictBool
    pending_review: ProtocolPendingReview | None
    reviews: list[dict[str, JsonValue]]


class ProtocolJob(ProtocolWireModel):
    schema_version: Literal["bioxp.protocol_operator_bundle.v1"]
    job_id: Identity
    created_at: str
    updated_at: str
    status: CommandStatus | Literal["awaiting_review", "running"]
    protocol: ProtocolSource
    execution: ProtocolExecution
    operator: ProtocolOperator
    artifacts: dict[str, JsonValue]
    command: ProtocolCommand | None = None

    @model_validator(mode="after")
    def canonical_identity(self) -> ProtocolJob:
        if self.command is not None:
            state = self.execution.runtime_state
            if (self.command.command_id != self.job_id or state.job_id != self.job_id
                    or state.workflow is None or state.workflow.command_id != self.job_id
                    or self.status != self.command.status):
                raise ValueError("workflow identity does not match canonical job")
        return self


class ProtocolJobSummary(ProtocolWireModel):
    job_id: Identity
    status: CommandStatus | Literal["awaiting_review", "running"]
    dry_run: StrictBool
    protocol_id: str
    source_type: Literal["native", "oem_xml"]
    created_at: str
    updated_at: str
    pending_review: ProtocolPendingReview | None
    command: ProtocolCommand | None = None


class ProtocolJobs(ProtocolWireModel):
    rows: list[ProtocolJob | ProtocolJobSummary]
