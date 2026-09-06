from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

ActionKind = Literal["primitive", "meta"]
ActionSafety = Literal["read_only", "service", "motion", "stop", "emergency"]
ActionAssessment = Literal["pass", "fail", "unverified"]
ActionStatus = Literal[
    "acknowledged",
    "admission_pending",
    "queued",
    "cleared",
    "completed",
    "failed",
    "blocked",
    "rejected",
    "reconciliation_required",
]


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
    telemetry_authority: str | None = Field(default=None, max_length=120)
    physical_position_verified: StrictBool | None = None


def _canonical_integer_keys(value: Any) -> Any:
    """Normalize JSON object keys without accepting non-canonical integers."""
    if not isinstance(value, dict):
        return value
    normalized: dict[int, Any] = {}
    for raw_key, item in value.items():
        if type(raw_key) is int:
            key = raw_key
        elif type(raw_key) is str and (
            raw_key == "0"
            or (raw_key.startswith("-") and raw_key[1:].isdigit() and raw_key[1:2] != "0")
            or (raw_key.isdigit() and raw_key[:1] != "0")
        ):
            key = int(raw_key)
        else:
            raise ValueError(f"non-canonical integer map key: {raw_key!r}")
        if key in normalized:
            raise ValueError(f"duplicate integer map key after JSON normalization: {key}")
        normalized[key] = item
    return normalized


def _bounded_integer_keys(*allowed: int):
    allowed_keys = frozenset(allowed)

    def validate(value: Any) -> Any:
        normalized = _canonical_integer_keys(value)
        if isinstance(normalized, dict) and not set(normalized).issubset(allowed_keys):
            raise ValueError(f"unexpected integer map key; allowed keys are {sorted(allowed_keys)}")
        return normalized

    return validate


def _bounded_string_keys(*allowed: str):
    allowed_keys = frozenset(allowed)

    def validate(value: Any) -> Any:
        if isinstance(value, dict) and not set(value).issubset(allowed_keys):
            raise ValueError(f"unexpected string map key; allowed keys are {sorted(allowed_keys)}")
        return value

    return validate


class OperatorDashboardXReceiptSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str | None = Field(max_length=160)
    intent: str | None = Field(max_length=160)
    status: str | None = Field(max_length=80)


class OperatorDashboardXFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    failure: str | None = Field(default=None, max_length=1000)
    reason: str | None = Field(default=None, max_length=1000)
    recorded_generation: StrictInt | None = None
    current_generation: StrictInt | None = None
    recorded_board_lifecycle_generation: StrictInt | None = None
    current_board_lifecycle_generation: StrictInt | None = None

    @model_validator(mode="after")
    def require_one_failure_branch(self):
        text_keys = {name for name in ("failure", "reason") if getattr(self, name) is not None}
        runtime_pair = (self.recorded_generation, self.current_generation)
        board_pair = (
            self.recorded_board_lifecycle_generation,
            self.current_board_lifecycle_generation,
        )
        runtime_present = any(value is not None for value in runtime_pair)
        board_present = any(value is not None for value in board_pair)
        if text_keys:
            if len(text_keys) != 1:
                raise ValueError("X dashboard failure text branch is contradictory")
            if self.failure in {"x_generation_changed", "x_board_lifecycle_generation_changed"}:
                if runtime_present and any(value is None for value in runtime_pair):
                    raise ValueError("X dashboard drift failure runtime generation context is incomplete")
                return self
            if runtime_present or board_present:
                raise ValueError("X dashboard failure text branch is contradictory")
            return self
        if runtime_present:
            if any(value is None for value in runtime_pair) or board_present:
                raise ValueError("X dashboard runtime generation branch is incomplete")
            return self
        if board_present:
            if any(value is None for value in board_pair):
                raise ValueError("X dashboard board generation branch is incomplete")
            return self
        raise ValueError("X dashboard failure requires one producer branch")


class OperatorDashboardXReferenceOperationFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    error: str = Field(min_length=1, max_length=1000)
    axis: Literal["x"]
    state: str = Field(min_length=1, max_length=80)
    origin_position_steps: StrictInt | None = None
    source: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=1000)
    updated_at: str | None = Field(default=None, max_length=80)
    last_motion_kind: str | None = Field(default=None, max_length=120)
    persisted: Literal[False]
    verified: Literal[False]
    durable_clean: Literal[False]


class OperatorDashboardXReferenceMutationSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    axis: Literal["x"]
    state: Literal["referenced", "desynced", "unknown"]
    origin_position_steps: StrictInt | None
    source: str | None = Field(max_length=160)
    note: str | None = Field(max_length=1000)
    updated_at: str | None = Field(max_length=80)
    last_motion_kind: str | None = Field(max_length=120)
    ok: Literal[True]
    persisted: Literal[True]
    verified: Literal[True]
    durable_clean: Literal[True]


class OperatorDashboardXReferenceStoreNotBound(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    error: Literal["reference store not bound"]


class OperatorDashboardXReferenceRecoverySuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    persisted: Literal[True]
    verified: Literal[True]
    durable_clean: Literal[True]
    axes: list[Literal["x"]] = Field(max_length=1)
    rows: list[OperatorDashboardXReferenceRow] = Field(max_length=1)


class OperatorDashboardXReferenceRecoveryFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    persisted: Literal[False]
    verified: Literal[False]
    durable_clean: Literal[False]
    error: str = Field(min_length=1, max_length=1000)
    axes: list[Literal["x"]] = Field(max_length=1)
    rows: list[OperatorDashboardXReferenceRow] = Field(max_length=1)


class OperatorDashboardXOmissionMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    omitted: Literal[
        "item_limit",
        "non_finite_float",
        "string_limit",
        "depth_limit",
        "cycle",
        "mapping_items_error",
        "mapping_width_limit",
        "sequence_width_limit",
        "unsupported_type",
        "projection_error",
        "serialization_error",
        "byte_limit",
    ]
    type: str | None = Field(default=None, max_length=96)
    prefix: str | None = Field(default=None, max_length=512)
    original_length: StrictInt | None = Field(default=None, ge=0)
    encoded_bytes: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def bind_fields_to_omission_reason(self):
        present = self.model_fields_set - {"omitted"}
        required = {
            "string_limit": {"prefix", "original_length"},
            "depth_limit": {"type"},
            "cycle": {"type"},
            "mapping_items_error": {"type"},
            "unsupported_type": {"type"},
            "projection_error": {"type"},
            "serialization_error": {"type"},
            "byte_limit": {"encoded_bytes"},
        }.get(self.omitted, set())
        if present != required:
            raise ValueError(f"omission fields do not match reason {self.omitted!r}")
        return self


class OperatorDashboardXReferenceAuthorityEffect(
    RootModel[
        OperatorDashboardXReferenceMutationSuccess
        | OperatorDashboardXReferenceOperationFailure
        | OperatorDashboardXReferenceStoreNotBound
        | OperatorDashboardXReferenceRecoverySuccess
        | OperatorDashboardXReferenceRecoveryFailure
        | OperatorDashboardXOmissionMarker
    ]
):
    pass


class OperatorDashboardXYReferenceRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    axis: Literal["x", "y"]
    state: Literal["referenced", "desynced", "unknown"]
    origin_position_steps: StrictInt | None
    source: str | None = Field(max_length=160)
    note: str | None = Field(max_length=1000)
    updated_at: str | None = Field(max_length=80)
    last_motion_kind: str | None = Field(max_length=120)


class OperatorDashboardXYSingleReferenceMutationSuccess(OperatorDashboardXYReferenceRow):
    ok: Literal[True]
    persisted: Literal[True]
    verified: Literal[True]
    durable_clean: Literal[True]


class OperatorDashboardXYSingleReferenceMutationFailure(OperatorDashboardXYReferenceRow):
    ok: Literal[False]
    persisted: Literal[False]
    verified: Literal[False]
    durable_clean: Literal[False]
    error: str = Field(min_length=1, max_length=1000)


class OperatorDashboardXYReferenceMutationSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    persisted: Literal[True]
    verified: Literal[True]
    durable_clean: Literal[True]
    axes: list[Literal["x", "y"]] = Field(min_length=2, max_length=2)
    rows: list[OperatorDashboardXYReferenceRow] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def bind_xy_rows(self):
        if set(self.axes) != {"x", "y"} or {row.axis for row in self.rows} != {"x", "y"}:
            raise ValueError("X/Y reference mutation axes are incomplete")
        return self


class OperatorDashboardXYReferenceMutationFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    persisted: Literal[False]
    verified: Literal[False]
    durable_clean: Literal[False]
    error: str = Field(min_length=1, max_length=1000)
    axes: list[Literal["x", "y"]] = Field(min_length=2, max_length=2)
    rows: list[OperatorDashboardXYReferenceRow] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def bind_xy_rows(self):
        if set(self.axes) != {"x", "y"} or {row.axis for row in self.rows} != {"x", "y"}:
            raise ValueError("failed X/Y reference mutation axes are incomplete")
        return self


class OperatorDashboardXYReferenceAuthorityEffect(
    RootModel[
        OperatorDashboardXYSingleReferenceMutationSuccess
        | OperatorDashboardXYSingleReferenceMutationFailure
        | OperatorDashboardXYReferenceMutationSuccess
        | OperatorDashboardXYReferenceMutationFailure
        | OperatorDashboardXOmissionMarker
    ]
):
    pass


class OperatorDashboardXControllerAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    machine_serial: Literal[206]
    acquisition_id: str = Field(min_length=1, max_length=200)
    evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mutation_authorized: StrictBool
    component_source: str = Field(min_length=1, max_length=1000)


class OperatorDashboardXJsonSafeEvidence(RootModel[JsonValue]):
    """Bounded diagnostic evidence. It never grants X authority."""

    @model_validator(mode="after")
    def enforce_robot_projection_bounds(self):
        remaining = 128

        def walk(value: JsonValue, depth: int) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining < 0:
                raise ValueError("bounded evidence exceeds the robot item budget")
            if depth > 8:
                raise ValueError("bounded evidence exceeds the robot depth limit")
            if isinstance(value, str):
                if len(value) > 512:
                    raise ValueError("bounded evidence string exceeds the robot limit")
                return
            if isinstance(value, float):
                if not math.isfinite(value):
                    raise ValueError("bounded evidence contains a non-finite float")
                return
            if isinstance(value, dict):
                if "omitted" in value:
                    OperatorDashboardXOmissionMarker.model_validate(value)
                    return
                for key, item in value.items():
                    if len(key) > 96:
                        raise ValueError("bounded evidence key exceeds the robot limit")
                    walk(item, depth + 1)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item, depth + 1)

        walk(self.root, 0)
        encoded = json.dumps(
            self.root, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(encoded) > 8192:
            raise ValueError("bounded evidence exceeds the robot byte limit")
        return self


class OperatorDashboardXFailedLifecycleEvidence(RootModel[JsonValue]):
    """Bounded robot-owned X diagnostic evidence retained without reclassifying lifecycle state."""

    @model_validator(mode="after")
    def enforce_failed_projection_bounds(self):
        remaining = 256

        def walk(value: JsonValue, depth: int) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining < 0:
                raise ValueError("failed X lifecycle evidence exceeds the item budget")
            if depth > 8:
                raise ValueError("failed X lifecycle evidence exceeds the depth limit")
            if isinstance(value, str):
                if len(value) > 512:
                    raise ValueError("failed X lifecycle evidence string exceeds the limit")
                return
            if isinstance(value, float):
                if not math.isfinite(value):
                    raise ValueError("failed X lifecycle evidence contains a non-finite float")
                return
            if isinstance(value, dict):
                if "omitted" in value:
                    OperatorDashboardXOmissionMarker.model_validate(value)
                    return
                for key, item in value.items():
                    if len(key) > 96:
                        raise ValueError("failed X lifecycle evidence key exceeds the limit")
                    walk(item, depth + 1)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item, depth + 1)

        walk(self.root, 0)
        encoded = json.dumps(
            self.root, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(encoded) > 8192:
            raise ValueError("failed X lifecycle evidence exceeds the byte limit")
        return self


XIntent = Literal[
    "prepare",
    "reconcile_switch_masks",
    "move_steps",
    "move_absolute",
    "move_to",
    "home_axis",
    "startup_home",
    "manual_panel_home",
    "move_to_origin_home",
    "caught_plate_recovery_home",
    "set_home",
    "set_max_speed",
    "set_max_acc",
    "restore_original_speed",
    "set_stall_guard",
    "enable_xy_current",
    "enable_xyz_current",
    "terminal_status",
    "wait_for_motor",
    "stop",
    "abort",
]


class OperatorDashboardXActionInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    expected_generation: StrictInt
    confirm: Literal["RECONCILE_X_SWITCH_MASKS"] | None = None
    steps: StrictInt | None = None
    position_steps: StrictInt | None = None
    acceleration: StrictInt | None = None
    timeout_s: StrictFloat | StrictInt | None = None
    wait_timeout_s: StrictFloat | StrictInt | None = None
    wait_for_stop: StrictBool | None = None
    source_mode: str | None = Field(default=None, max_length=200)
    operator_ack: Literal["SET_HOME_CURRENT_POSITION"] | None = None
    value: StrictInt | None = None
    enabled: StrictBool | None = None
    z_current_up: StrictInt | None = None
    physical_scope: Literal["all_present_motion_boards"] | None = None
    x: StrictInt | None = None
    y: StrictInt | None = None
    z: StrictInt | None = None
    pseudo_z_home: StrictInt | None = None
    run_in_parallel: StrictBool | None = None
    gripper_confirmed: StrictBool | None = None
    tip_loaded: StrictBool | None = None
    plate_on_gantry: StrictInt | None = None
    location19_y: StrictInt | None = None

    def validate_for_intent(self, intent: XIntent) -> None:
        common = {"command_id", "idempotency_key", "expected_generation"}
        required_by_intent = {
            "reconcile_switch_masks": {"confirm"},
            "move_steps": {"steps", "wait_timeout_s"},
            "move_absolute": {"position_steps", "wait_timeout_s"},
            "move_to": {"x", "y", "z", "pseudo_z_home", "run_in_parallel", "gripper_confirmed", "tip_loaded", "location19_y", "wait_timeout_s"},
            "home_axis": {"timeout_s"},
            "startup_home": {"timeout_s"},
            "manual_panel_home": {"timeout_s"},
            "move_to_origin_home": {"timeout_s"},
            "caught_plate_recovery_home": {"timeout_s"},
            "set_home": {"operator_ack"},
            "set_max_speed": {"value"},
            "set_max_acc": {"value"},
            "set_stall_guard": {"value"},
            "enable_xy_current": {"enabled"},
            "enable_xyz_current": {"enabled", "z_current_up"},
            "wait_for_motor": {"wait_timeout_s"},
            "stop": {"timeout_s"},
            "abort": {"timeout_s"},
        }
        optional_by_intent = {
            "move_absolute": {"acceleration", "wait_for_stop", "source_mode"},
            "move_to": {"plate_on_gantry"},
            "abort": {"physical_scope"},
        }
        required = common | required_by_intent.get(intent, set())
        allowed = required | optional_by_intent.get(intent, set())
        present = {
            key for key in self.model_fields_set
            if getattr(self, key) is not None
        }
        if not required.issubset(present):
            raise ValueError(f"active X receipt inputs are missing keys for {intent!r}")
        if not present.issubset(allowed):
            raise ValueError(f"active X receipt inputs contain keys for another intent: {intent!r}")


class OperatorDashboardXTmclFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    classification: str = Field(min_length=1, max_length=80)
    arbitration_id: StrictInt
    dlc: StrictInt
    data: list[StrictInt | OperatorDashboardXOmissionMarker] = Field(default_factory=list, max_length=64)
    raw: list[StrictInt | OperatorDashboardXOmissionMarker] = Field(default_factory=list, max_length=64)
    received_at: StrictFloat | StrictInt
    receive_sequence: StrictInt | None = None
    receive_owner: str | None = None
    owner_generation: StrictInt | None = None


class OperatorDashboardXTmclSkippedFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    classification: str = Field(min_length=1, max_length=80)
    arbitration_id: StrictInt | None = None
    dlc: StrictInt | None = None
    data: list[StrictInt | OperatorDashboardXOmissionMarker] | None = Field(default=None, max_length=64)
    raw: list[StrictInt | OperatorDashboardXOmissionMarker] = Field(default_factory=list, max_length=64)
    received_at: StrictFloat | StrictInt
    receive_sequence: StrictInt | None = None
    receive_owner: str | None = None
    owner_generation: StrictInt | None = None
    error: str | None = Field(default=None, max_length=1000)


class OperatorDashboardXTmclMultipart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    present: StrictBool
    part_count: StrictInt = Field(ge=0)
    parts: list[OperatorDashboardXTmclFrame] = Field(default_factory=list, max_length=256)
    reassembled_data: list[StrictInt | OperatorDashboardXOmissionMarker] = Field(default_factory=list, max_length=4096)
    reassembly_policy: str = Field(min_length=1, max_length=160)
    oem_chunk_index_equivalence: str = Field(min_length=1, max_length=80)


class OperatorDashboardXTmclWaitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: str = Field(min_length=1, max_length=80)
    classification: str = Field(min_length=1, max_length=80)
    apartment_equivalence: str | None = Field(default=None, min_length=1, max_length=80)
    source_anchor: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def bind_exact_wait_policy_authority(self):
        if (self.apartment_equivalence is None) == (self.source_anchor is None):
            raise ValueError("TMCL wait policy requires exactly one authority field")
        return self


class OperatorDashboardXTmclAttempt(BaseModel):
    """Attempt observations retain same-call response ambiguity."""

    model_config = ConfigDict(extra="forbid", strict=True)
    attempt_ordinal: StrictInt = Field(ge=1)
    tx_timestamp: StrictFloat | StrictInt
    tx_write_completed_at: StrictFloat | StrictInt
    wait_signaled: StrictBool
    outcome: str = Field(min_length=1, max_length=80)
    response_present: StrictBool
    receive_sequence: StrictInt | None
    response_attempt_attribution: Literal["single_write", "same_call_ambiguous"]


class OperatorDashboardXTmclProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transaction_id: str | None = Field(max_length=160)
    owner_generation: StrictInt | None
    generation_changed: StrictBool | None = None
    ok: StrictBool
    outcome: str = Field(min_length=1, max_length=80)
    matcher: str = Field(min_length=1, max_length=160)
    registration_timestamp: StrictFloat | StrictInt
    tx_timestamp: StrictFloat | StrictInt
    tx_write_completed_at: StrictFloat | StrictInt
    timeout_ms: StrictInt = Field(ge=0)
    tx_raw: list[StrictInt | OperatorDashboardXOmissionMarker] = Field(default_factory=list, max_length=4096)
    tx_frame_count: StrictInt | None = Field(default=None, ge=0)
    tx_frames: list[list[StrictInt | OperatorDashboardXOmissionMarker]] | None = Field(default=None, max_length=256)
    tx_write_timestamps: list[StrictFloat | StrictInt] | None = Field(default=None, max_length=256)
    tx_write_policy: str | None = Field(default=None, max_length=160)
    command_family: str | None = Field(default=None, max_length=80)
    tx_id: StrictInt | None = None
    tx_dlc: StrictInt | None = None
    expected_board: StrictInt | None = None
    expected_command: StrictInt | None = None
    expected_type: StrictInt | None = None
    expected_motor: StrictInt | None = None
    expected_value: StrictInt | None = None
    receive_timestamp: StrictFloat | StrictInt | None = None
    observed_rx_id: StrictInt | None = None
    observed_rx_dlc: StrictInt | None = None
    observed_rx_raw: list[StrictInt | OperatorDashboardXOmissionMarker] | None = Field(default=None, max_length=4096)
    observed_status: StrictInt | None = None
    frames: list[OperatorDashboardXTmclFrame] = Field(default_factory=list, max_length=256)
    ack_received: StrictBool | None = None
    completion_received: StrictBool | None = None
    multipart_received: StrictBool | None = None
    multipart: OperatorDashboardXTmclMultipart | None = None
    wait_policy: OperatorDashboardXTmclWaitPolicy | None = None
    oem_receive_queue_cleared: StrictInt | None = Field(default=None, ge=0)
    skipped_count: StrictInt | None = Field(default=None, ge=0)
    skipped_frames: list[OperatorDashboardXTmclSkippedFrame] = Field(default_factory=list, max_length=256)
    skipped_frames_truncated: StrictBool | None = None
    attempts: list[OperatorDashboardXTmclAttempt] | None = None


class OperatorDashboardXTmclAck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: StrictInt
    status_str: str | None = Field(max_length=160)
    board: StrictInt
    cmd: StrictInt
    value: StrictInt
    raw: list[StrictInt | OperatorDashboardXOmissionMarker] = Field(max_length=64)
    provenance: OperatorDashboardXTmclProvenance


class OperatorDashboardXRegisterReadback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    param: StrictInt
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    value: StrictInt | None


class OperatorDashboardAddressedRegisterReadback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    param: StrictInt
    motor: Literal[0, 1]
    ack: OperatorDashboardXTmclAck | None
    value: StrictInt | None


class OperatorDashboardAddressedParameterWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    param: Literal[6]
    motor: Literal[0, 1]
    set_value: StrictInt
    ack: OperatorDashboardXTmclAck | None
    readback: OperatorDashboardAddressedRegisterReadback
    ok: StrictBool

    @model_validator(mode="after")
    def bind_current_write_readback(self):
        if (
            self.readback.board != self.board
            or self.readback.motor != self.motor
            or self.readback.param != self.param
        ):
            raise ValueError("current-write readback does not match write address")
        return self


class OperatorDashboardXYParameterWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    param: Literal[4, 5]
    motor: Literal[0]
    set_value: StrictInt
    ack: OperatorDashboardXTmclAck | None
    readback: OperatorDashboardAddressedRegisterReadback
    ok: StrictBool

    @model_validator(mode="after")
    def bind_write_readback(self):
        if (
            self.readback.board != self.board
            or self.readback.motor != self.motor
            or self.readback.param != self.param
        ):
            raise ValueError("XY parameter write readback does not match write address")
        return self


class OperatorDashboardXYPositionReadback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    position: StrictInt | None
    ok: StrictBool


class OperatorDashboardXYSetHomeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    readback: OperatorDashboardAddressedRegisterReadback
    direct_ack: StrictBool
    readback_verified: StrictBool
    ok: StrictBool

    @model_validator(mode="after")
    def bind_set_home_readback(self):
        if (
            self.readback.board != self.board
            or self.readback.motor != self.motor
            or self.readback.param != 1
            or self.readback.value != 0
        ):
            raise ValueError("XY set-home readback must bind GAP1=0")
        if self.ok != (self.direct_ack or self.readback_verified):
            raise ValueError("XY set-home status does not match evidence")
        return self


PreparationStageEvidence = (
    OperatorDashboardXControllerAuthority
    | OperatorDashboardXJsonSafeEvidence
)


class OperatorDashboardXPreparationStage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stage_id: Literal[
        "authority",
        "rail_24v_readback",
        "door_readback",
        "latch_readback",
        "latch_solenoid",
        "door_readback_after_latch",
        "latch_readback_after_latch",
        "deactivateBoard",
        "activateBoard",
        "boardLifecycleGeneration",
        "waitForBoard",
        "initializeMotorsWithoutMotion",
        "x_switch_mask_precondition",
        "z_switch_mask_precondition",
        "parameter_readback",
    ] | OperatorDashboardXOmissionMarker
    status: Literal["passed", "failed", "not_applicable"] | OperatorDashboardXOmissionMarker
    source_anchor: str | OperatorDashboardXOmissionMarker
    controller_evidence: PreparationStageEvidence
    physical_motion: Literal[False] | OperatorDashboardXOmissionMarker

    @model_validator(mode="before")
    @classmethod
    def parse_stage_evidence(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        stage_id = value.get("stage_id")
        evidence = value.get("controller_evidence")
        if isinstance(evidence, BaseModel):
            return value
        model = (
            OperatorDashboardXControllerAuthority
            if stage_id == "authority"
            else OperatorDashboardXJsonSafeEvidence
        )
        return {**value, "controller_evidence": model.model_validate(evidence)}

    @model_validator(mode="after")
    def keep_authority_separate_from_diagnostic_evidence(self):
        if self.stage_id == "authority":
            if not isinstance(self.controller_evidence, OperatorDashboardXControllerAuthority):
                raise ValueError("authority stage lacks closed Serial-206 controller authority")
        elif isinstance(self.stage_id, str) and not isinstance(
            self.controller_evidence, OperatorDashboardXJsonSafeEvidence
        ):
            raise ValueError("non-authority stage evidence must remain diagnostic")
        return self


class OperatorDashboardXPreparationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["bioxp.oem_prepare_without_motion.v2"]
    failure_stage: str | None = Field(default=None, max_length=200)
    error: str | None = Field(default=None, max_length=200)
    ok: StrictBool
    state: Literal["completed", "failed_closed"] | OperatorDashboardXOmissionMarker
    machine_serial: Literal[206]
    controller_evidence: OperatorDashboardXControllerAuthority
    stage_ledger: list[OperatorDashboardXPreparationStage | OperatorDashboardXOmissionMarker] = Field(default_factory=list, max_length=256)
    stage_receipts: list[OperatorDashboardXPreparationStage | OperatorDashboardXOmissionMarker] | OperatorDashboardXOmissionMarker = Field(default_factory=list)
    board_lifecycle_generation: StrictInt | None = None
    physical_motion: Literal[False]
    physical_motion_commanded: Literal[False]
    homing_performed: Literal[False]
    motor_output_state: Literal["unknown"]
    motor_torque_verified: Literal[False]
    global_24v_switch_claimed: Literal[False]

    @model_validator(mode="after")
    def bind_serial206_controller_authority(self):
        if self.machine_serial != self.controller_evidence.machine_serial:
            raise ValueError("preparation serial does not match controller authority")
        return self


class OperatorDashboardXPreparationRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    observed_generation: StrictInt
    physical_motion: Literal[False]
    blocker: Literal["ownership_generation_changed_before_preparation"]
    axis: Literal["x"]
    source_anchor: Literal["ClassControlInterface.initializeMotorsWithoutMotion:3187-3195"]
    source_exact: Literal[True]
    literal_switch_mask_writes: list[StrictInt] = Field(max_length=0)


class OperatorDashboardXPreparationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    observed_generation: StrictInt
    board_lifecycle_generation: StrictInt | None
    board_preparation_verified: StrictBool
    initialize_without_motion_verified: StrictBool
    board_lifecycle_reused: StrictBool = False
    physical_motion: Literal[False]
    motor_output_state: Literal["unknown"]
    motor_torque_verified: Literal[False]
    receipt: OperatorDashboardXPreparationEvidence | OperatorDashboardXOmissionMarker
    axis: Literal["x"]
    source_anchor: Literal["ClassControlInterface.initializeMotorsWithoutMotion:3187-3195"]
    source_exact: Literal[True]
    literal_switch_mask_writes: list[StrictInt] = Field(max_length=0)


class OperatorDashboardXPreparationReceipt(
    RootModel[
        OperatorDashboardXPreparationRejection
        | OperatorDashboardXPreparationAttempt
    ]
):
    pass


class OperatorDashboardXEventWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    after_sequence: StrictInt = Field(ge=0)
    cleared: StrictInt = Field(ge=0)
    router_cleared: dict[str, StrictInt] = Field(max_length=8)
    dispatch_cursors: dict[str, StrictFloat | StrictInt] = Field(max_length=16)
    dispatch_cursor: StrictFloat | StrictInt | None

    _bound_router_queues = field_validator("router_cleared", mode="before")(
        _bounded_string_keys("valid_async", "unknown_async")
    )
    _bound_dispatch_cursors = field_validator("dispatch_cursors", mode="before")(
        _bounded_string_keys("5:0")
    )


class OperatorDashboardXProfileFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    speed: StrictInt | None
    acceleration: StrictInt | None
    current: StrictInt | None
    stall_threshold: StrictInt | None


class OperatorDashboardXProfileReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    source: Literal["initializeMotorsWithoutMotion"]
    axis: Literal["x"]
    board: Literal[5]
    motor: Literal[0]
    board_lifecycle_generation: StrictInt
    profile_fingerprint: OperatorDashboardXProfileFingerprint
    readbacks: dict[StrictInt, OperatorDashboardXRegisterReadback] = Field(min_length=4, max_length=4)

    _normalize_readback_keys = field_validator("readbacks", mode="before")(
        _bounded_integer_keys(4, 5, 6, 205)
    )

    @model_validator(mode="after")
    def bind_readback_parameters(self):
        if set(self.readbacks) != {4, 5, 6, 205}:
            raise ValueError("X profile receipt readbacks are incomplete")
        if any(parameter != row.param for parameter, row in self.readbacks.items()):
            raise ValueError("profile readback key does not match nested parameter")
        return self


class OperatorDashboardXPreflightProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: Literal["X"]
    board: Literal[5]
    motor: Literal[0]
    speed: StrictInt
    acc: StrictInt
    run_current: StrictInt
    standby_current: StrictInt
    stall_guard: StrictInt
    warm_enable: Literal[True]
    axis_min_steps: Literal[0]
    axis_max_steps: Literal[90263]


class OperatorDashboardXPreflightLegacy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile: OperatorDashboardXPreflightProfile
    profile_receipt: OperatorDashboardXProfileReceipt | OperatorDashboardXOmissionMarker | None
    switch_masks: dict[StrictInt, OperatorDashboardXRegisterReadback] | OperatorDashboardXOmissionMarker
    expected_switch_masks: dict[StrictInt, StrictInt] = Field(default_factory=dict, max_length=2)

    @field_validator("switch_masks", mode="before")
    @classmethod
    def normalize_switch_mask_keys(cls, value: Any) -> Any:
        if isinstance(value, dict) and "omitted" in value:
            return value
        return _bounded_integer_keys(12, 13)(value)
    _normalize_expected_mask_keys = field_validator("expected_switch_masks", mode="before")(
        _bounded_integer_keys(12, 13)
    )

    @model_validator(mode="after")
    def bind_switch_mask_authority(self):
        if self.expected_switch_masks not in ({}, {12: 1, 13: 0}, {12: 0, 13: 0}):
            raise ValueError("serial-206 X switch-mask observation metadata is invalid")
        if isinstance(self.switch_masks, dict) and set(self.switch_masks) != {12, 13}:
            raise ValueError("serial-206 X switch-mask readbacks are incomplete")
        if isinstance(self.switch_masks, dict) and any(parameter != row.param for parameter, row in self.switch_masks.items()):
            raise ValueError("switch-mask key does not match nested parameter")
        return self


class OperatorDashboardXPreflightSkipped(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    skipped: Literal[True]
    reason: Literal["recovered_oem_move_has_no_switch_mask_preflight"]
    profile_overrides: dict[StrictInt, StrictInt] = Field(default_factory=dict, max_length=8)
    source_exact: Literal[True]
    physical_motion: Literal[False]
    failure: None

    _normalize_override_keys = field_validator("profile_overrides", mode="before")(
        _bounded_integer_keys(4, 5, 6, 12, 13, 205)
    )


OperatorDashboardXPreflight = OperatorDashboardXPreflightSkipped | OperatorDashboardXPreflightLegacy


class OperatorDashboardXMoveReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    ack: OperatorDashboardXTmclAck | None
    board: Literal[5]
    motor: Literal[0]
    position: StrictInt
    source_noop: Literal[False]
    event_window: OperatorDashboardXEventWindow


class OperatorDashboardXParameterWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    param: Literal[4, 5, 6, 12, 13, 205]
    motor: Literal[0, 1]
    set_value: StrictInt
    ack: OperatorDashboardXTmclAck | None
    readback: OperatorDashboardAddressedRegisterReadback
    ok: StrictBool

    @model_validator(mode="after")
    def bind_parameter_write_receipt(self):
        if self.readback.board != self.board or self.readback.motor != self.motor or self.readback.param != self.param:
            raise ValueError("parameter write readback does not match the addressed write")
        return self


class OperatorDashboardXReferenceRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    axis: Literal["x"]
    state: Literal["unknown", "referenced", "desynced"]
    origin_position_steps: StrictInt | None
    source: str | None = Field(max_length=160)
    note: str | None = Field(max_length=1000)
    updated_at: str | None = Field(max_length=80)
    last_motion_kind: str | None = Field(max_length=120)


class OperatorDashboardXReferenceSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    axes: list[Literal["x"]] = Field(min_length=1, max_length=1)
    rows: dict[Literal["x"], OperatorDashboardXReferenceRow] = Field(min_length=1, max_length=1)
    persisted: Literal[True]
    verified: Literal[True]
    durable_clean: Literal[True]
    authority_untrusted: Literal[False] = False

    @model_validator(mode="after")
    def bind_reference_axes_and_rows(self):
        if self.axes != ["x"] or set(self.rows) != {"x"}:
            raise ValueError("successful X reference snapshot must contain exactly the X row")
        return self


class OperatorDashboardXReferencePendingFallback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    durable_clean: Literal[True]
    authority_untrusted: Literal[False]
    rows: dict[Literal["x"], OperatorDashboardXReferenceRow] = Field(max_length=0)


class OperatorDashboardXReferenceProjectionFallback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    authority_untrusted: Literal[True]


class OperatorDashboardXReferenceSnapshotFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    persisted: Literal[False]
    verified: Literal[False]
    durable_clean: Literal[False]
    error: str = Field(min_length=1, max_length=1000)
    axes: list[Literal["x"]] = Field(min_length=1, max_length=1)
    rows: dict[Literal["x"], OperatorDashboardXReferenceRow] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def bind_reference_axes_and_rows(self):
        if self.axes != ["x"] or set(self.rows) != {"x"}:
            raise ValueError("failed X reference snapshot must contain exactly the X row")
        return self


class OperatorDashboardXReference(
    RootModel[
        OperatorDashboardXReferenceSuccess
        | OperatorDashboardXReferencePendingFallback
        | OperatorDashboardXReferenceProjectionFallback
        | OperatorDashboardXReferenceSnapshotFailure
    ]
):
    @property
    def axes(self) -> list[Literal["x"]]:
        return self.root.axes if isinstance(self.root, OperatorDashboardXReferenceSuccess) else []

    @property
    def rows(self) -> dict[Literal["x"], OperatorDashboardXReferenceRow]:
        if isinstance(self.root, (OperatorDashboardXReferenceSuccess, OperatorDashboardXReferencePendingFallback)):
            return self.root.rows
        return {}


class OperatorDashboardXPositionReadback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    position: StrictInt | None
    ok: StrictBool


class OperatorDashboardXPendingTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    axis: Literal["x"]
    source_mode: str = Field(min_length=1, max_length=200)
    requested_position_steps: StrictInt
    target_position_steps: StrictInt
    before: OperatorDashboardXPositionReadback
    before_position_steps: StrictInt
    preflight: OperatorDashboardXPreflight
    command_issued: Literal[True]
    source_noop: Literal[False]
    physical_motion_commanded: Literal[True]
    controller_command_acknowledged: StrictBool
    event_window: OperatorDashboardXEventWindow
    move: OperatorDashboardXMoveReceipt
    pending_motion: Literal[True]
    physical_motion: Literal[True]
    reference_before: OperatorDashboardXReferenceSuccess
    acceleration_set: OperatorDashboardXParameterWrite | None = None
    acceleration_restore: OperatorDashboardXParameterWrite | None = None
    acceleration_restore_verified: StrictBool | None = None
    failure: str | None = Field(max_length=1000)

    @model_validator(mode="after")
    def bind_optional_acceleration_receipts(self):
        present = (
            self.acceleration_set is not None,
            self.acceleration_restore is not None,
            self.acceleration_restore_verified is not None,
        )
        if any(present) and not all(present):
            raise ValueError("pending X acceleration evidence must be complete")
        return self


class OperatorDashboardXNormalActiveReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(min_length=1, max_length=160)
    intent: XIntent
    idempotency_key: str | None = Field(max_length=160)
    generation: StrictInt
    inputs: OperatorDashboardXActionInputs
    status: Literal["executing"]
    result: None

    @model_validator(mode="after")
    def bind_inputs_to_intent(self):
        self.inputs.validate_for_intent(self.intent)
        return self


class OperatorDashboardXPendingActiveReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(min_length=1, max_length=160)
    intent: Literal["move_absolute"]
    status: Literal["executing"]
    result: OperatorDashboardXPendingTicket | OperatorDashboardXOmissionMarker


class OperatorDashboardXActiveReceipt(
    RootModel[
        OperatorDashboardXNormalActiveReceipt
        | OperatorDashboardXPendingActiveReceipt
    ]
):
    pass


class OperatorDashboardXGenuineStringFailure(RootModel[str]):
    @model_validator(mode="after")
    def validate_string_failure(self):
        literals = {
            "legacy_x_state_missing_board_lifecycle_generation",
            "interrupted_x_transaction_outcome_ambiguous",
            "restart_or_reentry_during_executing",
            "operator_rejected_x_home",
            "x_switch_mask_reconciliation_admitted",
            "x_reference_persistence_failed",
            "x_observation_not_reference_eligible",
            "xy_restart_or_reentry_during_executing",
            "homexy_restart_or_reentry_during_executing",
        }
        if self.root in literals:
            return self
        if re.fullmatch(r"(?:xy|homexy)_intent_exception:[A-Za-z_][A-Za-z0-9_]*:.+", self.root):
            return self
        raise ValueError("unrecognized serial-206 X lifecycle string failure")


class OperatorDashboardXGenerationDriftFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    failure: Literal["x_generation_changed", "x_board_lifecycle_generation_changed"]
    recorded_generation: StrictInt | None
    current_generation: StrictInt
    recorded_board_lifecycle_generation: StrictInt | None
    current_board_lifecycle_generation: StrictInt | None


class OperatorDashboardXInterruptExceptionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    error: str = Field(min_length=1, max_length=1000)
    interrupt_epoch: StrictInt = Field(ge=0)
    interrupted_command_ids: list[str] = Field(max_length=32)


class OperatorDashboardXInterruptNonMappingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["x_stop_result_not_mapping", "x_abort_result_not_mapping"]
    interrupt_epoch: StrictInt = Field(ge=0)
    interrupted_command_ids: list[str] = Field(max_length=32)


class OperatorDashboardXExactStopResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    first_delivery: OperatorDashboardXTmclAck | None
    second_delivery: OperatorDashboardXTmclAck | None
    oem_double_stop: Literal[True]
    ok: StrictBool


class OperatorDashboardXYExactStopResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    first_delivery: OperatorDashboardXTmclAck | None
    second_delivery: OperatorDashboardXTmclAck | None
    oem_double_stop: Literal[True]
    ok: StrictBool


class OperatorDashboardXYStopMap(RootModel[dict[Literal["x", "y"], OperatorDashboardXYExactStopResult]]):
    @model_validator(mode="after")
    def bind_axis_addresses(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("XY stop map must contain X and Y")
        expected = {"x": 5, "y": 4}
        if any(row.board != expected[axis] for axis, row in self.root.items()):
            raise ValueError("XY stop receipt does not match axis address")
        return self


class OperatorDashboardXWaitStoppedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stopped: StrictBool
    elapsed_ms: StrictInt = Field(ge=0)
    polls: StrictInt = Field(ge=0)
    last_speed: StrictInt | None
    seen_nonzero: StrictBool
    target_position: StrictInt | None
    target_reached: StrictBool
    last_position: StrictInt | None
    last_ack: OperatorDashboardXTmclAck | None
    ambiguous_no_motion: StrictBool | None = None

    @model_validator(mode="after")
    def bind_timeout_field(self):
        if self.stopped and self.ambiguous_no_motion is not None:
            raise ValueError("stopped wait result cannot carry timeout ambiguity")
        if not self.stopped and self.ambiguous_no_motion is None:
            raise ValueError("failed wait result requires timeout ambiguity")
        return self


class OperatorDashboardXLogicalAbortResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    no24v: Literal[True]
    latched: Literal[True]
    reason: str = Field(min_length=1, max_length=1000)
    abort_generation: StrictInt = Field(ge=1)
    affected_waiters: list[Literal["x", "y", "z", "g", "door"]] = Field(max_length=5)
    source_anchor: Literal["ClassControlInterface.forceAbortMotion; board forceAbort overrides"]


class OperatorDashboardXSpeedReadback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    speed: StrictInt | None
    ok: StrictBool


class OperatorDashboardXYSpeedReadback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    speed: StrictInt | None
    ok: StrictBool


class OperatorDashboardXYSpeedMap(RootModel[dict[Literal["x", "y"], OperatorDashboardXYSpeedReadback]]):
    @model_validator(mode="after")
    def bind_axis_addresses(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("XY speed map must contain X and Y")
        expected = {"x": 5, "y": 4}
        if any(row.board != expected[axis] for axis, row in self.root.items()):
            raise ValueError("XY speed readback does not match axis address")
        return self


class OperatorDashboardXRouterProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str | None = Field(default=None, max_length=160)
    queue: str | None = Field(default=None, max_length=160)
    classification: str | None = Field(default=None, max_length=160)
    arbitration_id: StrictInt | None = None
    dlc: StrictInt | None = None
    data: list[StrictInt] = Field(default_factory=list, max_length=64)
    raw: list[StrictInt] = Field(default_factory=list, max_length=64)
    received_at: StrictFloat | StrictInt | None = None
    router_sequence: StrictInt | None = None


class OperatorDashboardXDecodedBusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: StrictInt | None = None
    status: StrictInt | None = None
    status_str: str | None = Field(default=None, max_length=160)
    cmd: StrictInt | None = None
    value: StrictInt | None = None
    motor: StrictInt | None = None
    raw: list[StrictInt] = Field(default_factory=list, max_length=64)
    source: str | None = Field(default=None, max_length=160)
    observed_ms: StrictFloat | StrictInt | None = None
    event_sequence: StrictInt | None = None
    received_at: StrictFloat | StrictInt | None = None
    router_provenance: OperatorDashboardXRouterProvenance | None = None


class OperatorDashboardXAsyncBusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(min_length=1, max_length=160)
    classification: str = Field(min_length=1, max_length=160)
    raw: list[StrictInt] = Field(default_factory=list, max_length=64)
    observed_ms: StrictFloat | StrictInt | None = None
    received_at: StrictFloat | StrictInt | None = None
    router_provenance: OperatorDashboardXRouterProvenance | None = None
    error: str | None = Field(default=None, max_length=1000)


class OperatorDashboardXBusEvent(
    RootModel[OperatorDashboardXDecodedBusEvent | OperatorDashboardXAsyncBusEvent]
):
    pass


class OperatorDashboardXTargetWaitSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    target_reached: Literal[True]
    board: Literal[5]
    motor: Literal[0]
    event: OperatorDashboardXBusEvent
    elapsed_ms: StrictInt = Field(ge=0)
    events: list[OperatorDashboardXBusEvent] = Field(max_length=256)
    event_window: OperatorDashboardXEventWindow | OperatorDashboardXOmissionMarker


class OperatorDashboardXTargetWaitTimeout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    target_reached: Literal[False]
    board: Literal[5]
    motor: Literal[0]
    elapsed_ms: StrictInt = Field(ge=0)
    events: list[OperatorDashboardXBusEvent] = Field(max_length=256)
    failure: Literal["oem_moveToAbs_target_event_timeout", "x_target_wait_not_verified"]
    event_window: OperatorDashboardXEventWindow | OperatorDashboardXOmissionMarker


class OperatorDashboardXTargetWaitControllerFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    target_reached: Literal[False]
    board: Literal[5]
    motor: Literal[0]
    event: OperatorDashboardXBusEvent
    elapsed_ms: StrictInt = Field(ge=0)
    events: list[OperatorDashboardXBusEvent] = Field(max_length=256)
    failure: Literal[
        "controller_async_error_13", "controller_async_error_14",
        "oem_moveToAbs_stall_event", "x_controller_error_event",
    ]
    event_window: OperatorDashboardXEventWindow | OperatorDashboardXOmissionMarker


class OperatorDashboardXTargetWaitNo24V(OperatorDashboardXTargetWaitTimeout):
    failure: Literal["No24V"]
    no24v: Literal[True]


class OperatorDashboardXTargetWaitResult(
    RootModel[
        OperatorDashboardXTargetWaitSuccess
        | OperatorDashboardXTargetWaitTimeout
        | OperatorDashboardXTargetWaitControllerFailure
        | OperatorDashboardXTargetWaitNo24V
        | OperatorDashboardXOmissionMarker
    ]
):
    pass


class OperatorDashboardXMultiTargetWaitPerAxis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    target_reached: StrictBool
    event: OperatorDashboardXBusEvent | None
    failure: str | None = Field(default=None, max_length=160)


class OperatorDashboardXMultiTargetWaitSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    failure: None
    pending: Literal[False]
    reached: Literal[True]
    events: list[OperatorDashboardXBusEvent] = Field(max_length=512)
    event_window: OperatorDashboardXEventWindow | OperatorDashboardXOmissionMarker
    elapsed_ms: StrictInt = Field(ge=0)


class OperatorDashboardXMultiTargetWaitFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["No24V", "oem_moveToAbs_stall_event", "x_target_wait_not_verified"]
    pending: StrictBool
    reached: Literal[False]
    events: list[OperatorDashboardXBusEvent] = Field(max_length=512)
    event_window: OperatorDashboardXEventWindow | OperatorDashboardXOmissionMarker
    elapsed_ms: StrictInt = Field(ge=0)
    no24v: Literal[True] | None = None
    stalled: Literal[True] | None = None


class OperatorDashboardXMultiTargetWaitResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    result: OperatorDashboardXMultiTargetWaitSuccess | OperatorDashboardXMultiTargetWaitFailure
    per_axis: dict[Literal["x", "y"], OperatorDashboardXMultiTargetWaitPerAxis]
    sta_sequential: StrictBool

    @model_validator(mode="after")
    def bind_per_axis(self):
        if set(self.per_axis) != {"x", "y"}:
            raise ValueError("multi-target wait must contain X and Y")
        return self


class OperatorDashboardXMoveXYIgnoredCompatibilityInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    speed: StrictInt | None
    acc: StrictInt | None
    wait_timeout_s: StrictFloat | StrictInt


class OperatorDashboardXMoveXYIntegerMap(
    RootModel[dict[Literal["x", "y"], StrictInt]]
):
    @model_validator(mode="after")
    def require_both_axes(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("X/Y integer map must contain both axes")
        return self


class OperatorDashboardXMoveXYNullableIntegerMap(
    RootModel[dict[Literal["x", "y"], StrictInt | None]]
):
    @model_validator(mode="after")
    def require_both_axes(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("X/Y nullable integer map must contain both axes")
        return self


class OperatorDashboardXMoveXYAccelerationMap(
    RootModel[dict[Literal["x", "y"], OperatorDashboardXYParameterWrite]]
):
    @model_validator(mode="after")
    def bind_axis_addresses(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("X/Y acceleration map must contain both axes")
        expected = {"x": 5, "y": 4}
        if any(row.board != expected[axis] or row.motor != 0 or row.param != 5 for axis, row in self.root.items()):
            raise ValueError("X/Y acceleration receipt does not match axis address")
        return self


class OperatorDashboardXMoveXYAxisMoveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    board: Literal[4, 5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    position: StrictInt
    source_noop: Literal[False]
    event_window: OperatorDashboardXEventWindow | OperatorDashboardXOmissionMarker


class OperatorDashboardXMoveXYCommandMap(
    RootModel[dict[Literal["x", "y"], OperatorDashboardXMoveXYAxisMoveResult]]
):
    @model_validator(mode="after")
    def bind_axis_addresses(self):
        if not self.root or not set(self.root).issubset({"x", "y"}):
            raise ValueError("X/Y command map has invalid keys")
        expected = {"x": 5, "y": 4}
        if any(row.board != expected[axis] for axis, row in self.root.items()):
            raise ValueError("X/Y command receipt does not match axis address")
        return self


class OperatorDashboardXMoveXYWaitMap(
    RootModel[dict[Literal["x", "y"], OperatorDashboardXTargetWaitResult]]
):
    @model_validator(mode="after")
    def bind_axis_keys(self):
        if not self.root or not set(self.root).issubset({"x", "y"}):
            raise ValueError("X/Y wait map has invalid keys")
        return self


class OperatorDashboardXMoveXYAxisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_acknowledged: StrictBool
    wait_accepted: StrictBool
    target_event_128_observed: StrictBool
    controller_error_events: list[OperatorDashboardXBusEvent]
    position: OperatorDashboardXYPositionReadback
    position_verified: StrictBool
    position_delta_steps: StrictInt | None = None
    terminal_speed: OperatorDashboardXYSpeedReadback
    terminal_speed_verified: StrictBool
    ok: StrictBool


class OperatorDashboardXMoveXYAxisEvidenceMap(
    RootModel[dict[Literal["x", "y"], OperatorDashboardXMoveXYAxisEvidence]]
):
    @model_validator(mode="after")
    def bind_axis_addresses(self):
        if not self.root or not set(self.root).issubset({"x", "y"}):
            raise ValueError("X/Y axis evidence map has invalid keys")
        expected = {"x": 5, "y": 4}
        if any(row.position.board != expected[axis] or row.terminal_speed.board != expected[axis] for axis, row in self.root.items()):
            raise ValueError("X/Y axis evidence does not match axis address")
        return self


class OperatorDashboardXMoveXYSafetyStopMap(
    RootModel[dict[Literal["x", "y"], OperatorDashboardXYExactStopResult]]
):
    @model_validator(mode="after")
    def bind_axis_addresses(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("X/Y safety-stop map must contain both axes")
        expected = {"x": 5, "y": 4}
        if any(row.board != expected[axis] for axis, row in self.root.items()):
            raise ValueError("X/Y safety-stop receipt does not match axis address")
        return self


class OperatorDashboardXSetHomeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    readback: OperatorDashboardXRegisterReadback
    direct_ack: StrictBool
    readback_verified: StrictBool
    ok: StrictBool

    @model_validator(mode="after")
    def bind_home_readback(self):
        if self.readback.param != 1 or self.readback.value != 0:
            raise ValueError("set-home readback must bind GAP1=0")
        if self.ok != (self.direct_ack or self.readback_verified):
            raise ValueError("set-home status does not match evidence")
        return self


class OperatorDashboardXHomeSwitchReadback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    value: StrictInt | None
    ok: StrictBool


class OperatorDashboardXRotateReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    motor: Literal[0]
    cmd: Literal[2]
    velocity: StrictInt
    ack: OperatorDashboardXTmclAck | None
    ok: StrictBool


class OperatorDashboardXHomeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    failure_predicate: str | None = Field(max_length=1000)
    controller_home_proof_verified: StrictBool
    home_search_command_acknowledged: StrictBool
    terminal_stop_verified: StrictBool
    controller_motion_evidence_seen: StrictBool | None = None
    seen_inactive: StrictBool | None = None
    home_hit_in_loop: StrictBool | None = None
    home_hit_after_stop: StrictBool | None = None
    home_after_stop_active: StrictBool
    switch_transition_verified: StrictBool | None = None
    set_home_acknowledged: StrictBool
    set_home_readback_zero: StrictBool
    position_after_sethome_zero: StrictBool
    home_after_sethome_active: StrictBool
    position_before: StrictInt | None = None
    search_start_position: StrictInt | None = None
    position_after_stop: StrictInt | None = None
    position_after_sethome: StrictInt | None = None
    gap9_before: StrictInt | None = None
    gap9_after_stop: StrictInt | None = None
    gap9_after_sethome: StrictInt | None = None
    gap10_before: StrictInt | None = None
    board_lifecycle_generation: StrictInt | None = None
    board_lifecycle_generation_after: StrictInt | None = None


class OperatorDashboardXSwitchSnapshotDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    motor: Literal[0]
    left: OperatorDashboardAddressedRegisterReadback
    right: OperatorDashboardAddressedRegisterReadback
    left_disable: OperatorDashboardAddressedRegisterReadback
    right_disable: OperatorDashboardAddressedRegisterReadback
    left_disabled: StrictBool
    right_disabled: StrictBool
    left_effective_active: StrictBool
    right_effective_active: StrictBool
    left_raw_active: StrictBool
    right_raw_active: StrictBool
    left_state: StrictInt
    right_state: StrictInt

    @model_validator(mode="after")
    def bind_switch_registers(self):
        expected = {
            "left": 9,
            "right": 10,
            "left_disable": 13,
            "right_disable": 12,
        }
        for name, param in expected.items():
            row = getattr(self, name)
            if row.board != self.board or row.motor != self.motor or row.param != param:
                raise ValueError("home switch register receipt does not match its fixed address")
        return self


class OperatorDashboardXHomeSwitchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[4, 5]
    motor: Literal[0]
    active_raw_value: StrictInt
    left_active: StrictBool
    left_disabled: StrictBool
    left_raw_active: StrictBool
    left_state: StrictInt
    right_active: StrictBool
    right_disabled: StrictBool
    right_raw_active: StrictBool
    right_state: StrictInt
    switches: OperatorDashboardXSwitchSnapshotDetails

    @model_validator(mode="after")
    def bind_switch_address(self):
        if self.switches.board != self.board or self.switches.motor != self.motor:
            raise ValueError("home switch receipt does not match its board and motor")
        return self


class OperatorDashboardXHomeStatusReadback(BaseModel):
    """Closed status projection used by the X home producer before a search."""

    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    status: StrictInt | None = None
    status_str: str | None = Field(default=None, max_length=160)
    position: StrictInt | None = None
    speed: StrictInt | None = None
    home: StrictInt | None = None
    left: StrictInt | None = None
    right: StrictInt | None = None
    raw: list[StrictInt] = Field(default_factory=list, max_length=64)


class OperatorDashboardXSetHomeSkipped(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    oem_source_step: Literal["axisSearchHome.setHome()"]
    reason: Literal["live_false_home_guard_no_initial_setHome_before_switch_transition"]
    skipped: Literal[True]


class OperatorDashboardXHomeMoveReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    speed: StrictInt
    ack: OperatorDashboardXTmclAck | None
    ok: StrictBool


class OperatorDashboardXPreclearStopReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    ack: OperatorDashboardXTmclAck | None
    ok: StrictBool


class OperatorDashboardXPreclearMoveReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    board: Literal[5]
    motor: Literal[0]
    position: StrictInt
    ack: OperatorDashboardXTmclAck | None
    pre_stop: OperatorDashboardXPreclearStopReceipt | OperatorDashboardXOmissionMarker
    ok: StrictBool


class OperatorDashboardXHomeTraceSample(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    t_ms: StrictInt
    position: StrictInt
    speed: StrictInt
    home: StrictInt
    left: StrictInt
    right: StrictInt


class OperatorDashboardXGoHomeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    axis: Literal["x", "y"]
    board: Literal[4, 5]
    motor: Literal[0]
    board_lifecycle_generation: StrictInt | None
    source_return_code: StrictInt
    ok: StrictBool
    short_circuit: Literal["MotorHome_and_CurrentPosition_zero"] | None = None
    false_home_guard: str | None = Field(max_length=1000)
    speed: StrictInt | None = None
    rehome: StrictBool | None = None
    home_active_value: StrictInt | None = None
    position_before: OperatorDashboardXYPositionReadback | OperatorDashboardXOmissionMarker | None = None
    position_after: OperatorDashboardXYPositionReadback | OperatorDashboardXOmissionMarker | None = None
    position_after_sethome: OperatorDashboardXYPositionReadback | OperatorDashboardXOmissionMarker | None = None
    status_before: OperatorDashboardXHomeStatusReadback | OperatorDashboardXOmissionMarker | None = None
    closed_before: StrictBool | None = None
    opened_before: StrictBool | None = None
    home_before: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker | None = None
    switches_before: OperatorDashboardXHomeSwitchSnapshot | OperatorDashboardXOmissionMarker | None = None
    home_after_rehome: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker | None = None
    switches_after_rehome: OperatorDashboardXHomeSwitchSnapshot | OperatorDashboardXOmissionMarker | None = None
    search_start_position: OperatorDashboardXYPositionReadback | OperatorDashboardXOmissionMarker | None = None
    max_search_abs_delta: StrictInt | None = None
    search_stop_margin_steps: StrictInt | None = None
    search_stop_threshold_steps: StrictInt | None = None
    axis_min_steps: StrictInt | None = None
    axis_max_steps: StrictInt | None = None
    pre_home_outside_axis_limit: StrictBool | None = None
    search_start_home: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker | None = None
    home_hit: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker | None = None
    home_after_stop: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker | None = None
    home_after: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker | None = None
    rehome_move: OperatorDashboardXHomeMoveReceipt | OperatorDashboardXOmissionMarker | None = None
    rehome_wait: OperatorDashboardXWaitStoppedResult | OperatorDashboardXOmissionMarker | None = None
    rehome_position: OperatorDashboardXYPositionReadback | OperatorDashboardXOmissionMarker | None = None
    move_home: OperatorDashboardXHomeMoveReceipt | OperatorDashboardXOmissionMarker | None = None
    move_left: OperatorDashboardXHomeMoveReceipt | OperatorDashboardXOmissionMarker | None = None
    move_direction: Literal["move_left"] | None = None
    pre_command_event_window: OperatorDashboardXEventWindow | None = None
    event_window_reset: OperatorDashboardXEventWindow | None = None
    source_events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    controller_error_events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    wait: OperatorDashboardXWaitStoppedResult | OperatorDashboardXOmissionMarker | None = None
    stop: OperatorDashboardXYExactStopResult | OperatorDashboardXOmissionMarker | None = None
    set_home: OperatorDashboardXYSetHomeResult | OperatorDashboardXOmissionMarker | None = None
    controller_command_acknowledged: StrictBool
    controller_terminal_state_verified: StrictBool
    controller_home_proof_verified: StrictBool
    home_decision: OperatorDashboardXHomeDecision
    seen_motion: StrictBool | None = None
    controller_motion_evidence_seen: StrictBool | None = None
    controller_speed_nonzero_seen: StrictBool | None = None
    controller_position_counter_changed: StrictBool | None = None
    motor_output_state: Literal["stopped_readback", "unverified"]
    motor_torque_verified: Literal[False]
    independent_physical_motion_verified: Literal[False]
    physical_effect_verified: Literal[False]
    switch_transition: StrictBool | None = None
    home_predicate_confirmed: StrictBool
    trace_tail: list[OperatorDashboardXHomeTraceSample] | OperatorDashboardXOmissionMarker | None = None

    @model_validator(mode="after")
    def bind_home_result_variant(self):
        expected_board = 5 if self.axis == "x" else 4
        if self.board != expected_board:
            raise ValueError("home result axis does not match board address")
        addressed = (
            self.position_before,
            self.position_after,
            self.position_after_sethome,
            self.search_start_position,
            self.rehome_position,
        )
        if any(isinstance(row, OperatorDashboardXYPositionReadback) and row.board != expected_board for row in addressed):
            raise ValueError("home position readback does not match axis address")
        home_switches = (
            self.home_before,
            self.home_after_rehome,
            self.search_start_home,
            self.home_hit,
            self.home_after_stop,
            self.home_after,
        )
        if any(isinstance(row, (OperatorDashboardXHomeSwitchReadback, OperatorDashboardXHomeSwitchSnapshot)) and row.board != expected_board for row in home_switches):
            raise ValueError("home-switch readback does not match axis address")
        control_receipts = (
            self.move_home,
            self.move_left,
            self.stop,
            self.set_home,
        )
        control_types = (
            OperatorDashboardXHomeMoveReceipt,
            OperatorDashboardXYExactStopResult,
            OperatorDashboardXYSetHomeResult,
        )
        for receipt in control_receipts:
            if isinstance(receipt, control_types) and receipt.board != expected_board:
                raise ValueError("home control receipt does not match axis address")
        short = self.short_circuit is not None
        if short:
            if self.rehome is not None or self.speed is not None:
                raise ValueError("short-circuit home cannot carry search configuration")
        else:
            required = {
                "speed", "rehome", "home_active_value", "position_after",
                "search_start_position", "wait", "stop", "home_decision",
            }
            present = {name for name in required if getattr(self, name) is not None}
            if present != required:
                raise ValueError("full X home result is incomplete")
        if self.ok != self.controller_home_proof_verified:
            raise ValueError("home status does not match controller proof")
        return self


class OperatorDashboardXAxisSearchHomeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    axis: Literal["x", "y"]
    board: Literal[4, 5]
    motor: Literal[0]
    speed: StrictInt
    oem_method: Literal["axisSearchHome"]
    home_active_value: StrictInt
    sethome_init: OperatorDashboardXYSetHomeResult | OperatorDashboardXSetHomeSkipped | OperatorDashboardXOmissionMarker
    home_before_axis_search: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker
    switches_before_axis_search: OperatorDashboardXHomeSwitchSnapshot
    preclear_move: OperatorDashboardXPreclearMoveReceipt | OperatorDashboardXOmissionMarker | None
    preclear_wait: OperatorDashboardXWaitStoppedResult | OperatorDashboardXOmissionMarker | None
    home_after_preclear: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker | None
    switches_after_preclear: OperatorDashboardXHomeSwitchSnapshot | OperatorDashboardXOmissionMarker | None
    go_home: OperatorDashboardXGoHomeResult | OperatorDashboardXOmissionMarker | None
    max_search_abs_delta: StrictInt | None
    ok: StrictBool
    failure: str | None = Field(default=None, max_length=1000)
    home_after: OperatorDashboardXHomeSwitchReadback | OperatorDashboardXOmissionMarker | None = None
    set_home: OperatorDashboardXYSetHomeResult | OperatorDashboardXOmissionMarker | None = None
    source_return_code: StrictInt | None = None
    switch_transition: StrictBool | None = None
    false_home_guard: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def bind_axis_search_result(self):
        expected_board = 5 if self.axis == "x" else 4
        if self.board != expected_board:
            raise ValueError("axis-search result axis does not match board address")
        if self.switches_before_axis_search.board != expected_board:
            raise ValueError("axis-search switch snapshot does not match board address")
        if isinstance(self.switches_after_preclear, OperatorDashboardXHomeSwitchSnapshot) and self.switches_after_preclear.board != expected_board:
            raise ValueError("axis-search preclear switch snapshot does not match board address")
        if self.ok and not isinstance(self.go_home, OperatorDashboardXGoHomeResult):
            raise ValueError("successful axis search requires closed go-home evidence")
        if self.ok != (self.failure is None):
            raise ValueError("axis-search status does not match failure")
        return self


class OperatorDashboardXHomePrimitiveResult(
    RootModel[
        OperatorDashboardXGoHomeResult
        | OperatorDashboardXAxisSearchHomeResult
        | OperatorDashboardXOmissionMarker
    ]
):
    pass


class OperatorDashboardXStopInterruptInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    expected_generation: StrictInt
    timeout_s: StrictFloat | StrictInt


class OperatorDashboardXAbortInterruptInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    expected_generation: StrictInt
    timeout_s: StrictFloat | StrictInt
    physical_scope: Literal["all_present_motion_boards"]


class OperatorDashboardXInterruptInputs(
    RootModel[
        OperatorDashboardXStopInterruptInputs
        | OperatorDashboardXAbortInterruptInputs
        | OperatorDashboardXOmissionMarker
    ]
):
    pass


class OperatorDashboardXStopFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    axis: Literal["x"]
    intent: Literal["stop"]
    stop: OperatorDashboardXExactStopResult | OperatorDashboardXOmissionMarker
    wait: OperatorDashboardXWaitStoppedResult | OperatorDashboardXOmissionMarker
    controller_command_acknowledged: StrictBool
    controller_terminal_state_verified: StrictBool
    physical_motion: Literal[False]
    physical_effect_verified: Literal[False]
    failure: Literal["x_stop_not_verified"] | None
    interrupt_epoch: StrictInt | None = Field(default=None, ge=0)
    interrupted_command_ids: list[str] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def bind_interrupt_overlay(self):
        if (self.interrupt_epoch is None) != (self.interrupted_command_ids is None):
            raise ValueError("X stop interrupt overlay must be complete")
        if self.ok != (
            self.controller_command_acknowledged
            and self.controller_terminal_state_verified
        ):
            raise ValueError("X stop status does not match controller evidence")
        if (self.failure is None) != self.ok:
            raise ValueError("X stop failure must be null exactly on success")
        if isinstance(self.stop, OperatorDashboardXExactStopResult) and self.stop.board != 5:
            raise ValueError("X stop receipt must address board 5")
        return self


class OperatorDashboardXAggregateAbortFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    axis_context: Literal["x"]
    intent: Literal["aggregate_oem_abort"]
    physical_scope: Literal["aggregate_oem_all_present_boards"]
    x_only: Literal[False]
    logical_abort: OperatorDashboardXLogicalAbortResult | OperatorDashboardXOmissionMarker
    x_terminal_stop: OperatorDashboardXStopFailure | OperatorDashboardXOmissionMarker
    reference_desync: OperatorDashboardXReferenceAuthorityEffect | None
    controller_command_acknowledged: StrictBool
    controller_terminal_state_verified: StrictBool
    physical_effect_verified: Literal[False]
    failure: Literal["aggregate_oem_abort_not_verified"] | None
    interrupt_epoch: StrictInt | None = Field(default=None, ge=0)
    interrupted_command_ids: list[str] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def bind_interrupt_overlay(self):
        if (self.interrupt_epoch is None) != (self.interrupted_command_ids is None):
            raise ValueError("X abort interrupt overlay must be complete")
        if (self.failure is None) != self.ok:
            raise ValueError("X abort failure must be null exactly on success")
        return self


class OperatorDashboardXInterruptResult(
    RootModel[
        OperatorDashboardXStopFailure
        | OperatorDashboardXAggregateAbortFailure
        | OperatorDashboardXInterruptExceptionResult
        | OperatorDashboardXInterruptNonMappingResult
        | OperatorDashboardXOmissionMarker
    ]
):
    pass


class OperatorDashboardXSafetyInterruptReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(min_length=1, max_length=160)
    receipt_id: str = Field(min_length=1, max_length=200)
    intent: Literal["stop", "abort"]
    idempotency_key: str | None = Field(max_length=160)
    idempotency_replay_enabled: Literal[False]
    generation: StrictInt
    inputs: OperatorDashboardXInterruptInputs
    status: Literal["completed", "failed"]
    started_at: StrictFloat | StrictInt
    finished_at: StrictFloat | StrictInt
    interrupt_epoch: StrictInt = Field(ge=0)
    interrupted_command_ids: list[str] = Field(max_length=32)
    result: OperatorDashboardXInterruptResult

    @model_validator(mode="before")
    @classmethod
    def parse_intent_bound_inputs_and_result(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        intent = value.get("intent")
        raw_inputs = value.get("inputs")
        raw_result = value.get("result")
        if isinstance(raw_inputs, dict) and "omitted" not in raw_inputs:
            model = OperatorDashboardXStopInterruptInputs if intent == "stop" else OperatorDashboardXAbortInterruptInputs
            value = dict(value)
            value["inputs"] = OperatorDashboardXInterruptInputs(root=model.model_validate(raw_inputs))
        if isinstance(raw_result, dict) and "omitted" not in raw_result:
            if "error" in raw_result:
                expected = OperatorDashboardXInterruptExceptionResult
            elif raw_result.get("failure") in {"x_stop_result_not_mapping", "x_abort_result_not_mapping"}:
                expected = OperatorDashboardXInterruptNonMappingResult
            else:
                expected = OperatorDashboardXStopFailure if intent == "stop" else OperatorDashboardXAggregateAbortFailure
            value = dict(value)
            value["result"] = OperatorDashboardXInterruptResult(root=expected.model_validate(raw_result))
        return value

    @model_validator(mode="after")
    def bind_status_and_intent(self):
        result = self.result.root
        if isinstance(result, OperatorDashboardXStopFailure) and self.intent != "stop":
            raise ValueError("stop result requires stop interrupt intent")
        if isinstance(result, OperatorDashboardXAggregateAbortFailure) and self.intent != "abort":
            raise ValueError("abort result requires abort interrupt intent")
        if isinstance(result, (OperatorDashboardXStopFailure, OperatorDashboardXAggregateAbortFailure)):
            succeeded = result.ok is True
            if self.status != ("completed" if succeeded else "failed"):
                raise ValueError("interrupt receipt status does not match result")
        else:
            if self.status != "failed":
                raise ValueError("secondary interrupt result requires failed status")
            if isinstance(result, OperatorDashboardXInterruptNonMappingResult):
                expected = "x_stop_result_not_mapping" if self.intent == "stop" else "x_abort_result_not_mapping"
                if result.failure != expected:
                    raise ValueError("non-mapping interrupt result does not match intent")
        return self


class OperatorDashboardXSafetyInterruptFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: Literal["x_safety_stop_dispatched", "x_safety_abort_dispatched"]
    receipt: OperatorDashboardXSafetyInterruptReceipt | OperatorDashboardXOmissionMarker

    @model_validator(mode="after")
    def bind_reason_to_receipt(self):
        if isinstance(self.receipt, OperatorDashboardXSafetyInterruptReceipt):
            expected = "x_safety_stop_dispatched" if self.receipt.intent == "stop" else "x_safety_abort_dispatched"
            if self.reason != expected:
                raise ValueError("X safety interrupt reason does not match receipt intent")
        return self


class OperatorDashboardXIntentExceptionFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: str = Field(pattern=r"^(?:x_intent_exception:[A-Za-z_][A-Za-z0-9_]*:.+|x_home_evidence_not_verified)$")
    command_issued: Literal[False]


class OperatorDashboardXNonMappingFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["x_result_not_mapping"]


class OperatorDashboardXMaskNonMappingFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["x_mask_reconciliation_result_not_mapping"]
    prior_lifecycle_state: str = Field(min_length=1, max_length=80)
    fresh_prepare_required: Literal[True]


class OperatorDashboardXSafetyInterruptionWrapper(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["x_intent_interrupted_by_safety_command"]
    command_issued: StrictBool
    interrupt_epoch: StrictInt = Field(ge=0)


class OperatorDashboardXProfileParameterFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    intent: Literal["set_max_speed", "set_max_acc", "restore_original_speed", "set_stall_guard"]
    source_method: str = Field(min_length=1, max_length=300)
    board: Literal[5]
    motor: Literal[0]
    parameter: Literal[4, 5, 205]
    value: StrictInt
    write: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker
    readback: OperatorDashboardXRegisterReadback | OperatorDashboardXOmissionMarker
    controller_command_acknowledged: StrictBool
    controller_terminal_state_verified: Literal[False]
    physical_motion_commanded: Literal[False]
    physical_effect_verified: Literal[False]
    failure: Literal["x_profile_parameter_readback_mismatch"]

    @model_validator(mode="after")
    def bind_intent_to_parameter(self):
        expected = {"set_max_speed": 4, "set_max_acc": 5, "restore_original_speed": 4, "set_stall_guard": 205}
        if self.parameter != expected[self.intent]:
            raise ValueError("X profile intent does not match register")
        return self


class OperatorDashboardXTerminalStatusFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    board: Literal[5]
    motor: Literal[0]
    position_steps: StrictInt | None
    speed_steps_s: StrictInt | None
    max_speed: StrictInt | None
    max_acceleration: StrictInt | None
    max_current: StrictInt | None
    left_switch_state: StrictInt | None
    right_switch_state: StrictInt | None
    right_switch_disabled: StrictBool | None
    left_switch_disabled: StrictBool | None
    stall_guard: StrictInt | None
    profile_verified: StrictBool
    expected_profile: dict[StrictInt, StrictInt] = Field(min_length=4, max_length=4)
    switch_mask_verified: StrictBool | None = None
    switch_mask_tuple: dict[StrictInt, StrictInt | None] = Field(min_length=2, max_length=2)
    expected_switch_masks: dict[StrictInt, StrictInt] = Field(default_factory=dict, max_length=2)
    switch_mask_policy: Literal["observed_only_oem_source_omits_x_writes"] | None = None
    readbacks: dict[StrictInt, OperatorDashboardXRegisterReadback] = Field(min_length=10, max_length=10)
    authority: Literal["serial206_x_terminal_register_readback"]
    failure: Literal["x_terminal_speed_not_typed_integer_zero", "x_terminal_readback_not_verified", "x_oem_profile_mismatch", "x_terminal_status_incomplete"]

    _normalize_expected_profile_keys = field_validator("expected_profile", mode="before")(
        _bounded_integer_keys(4, 5, 6, 205)
    )
    _normalize_switch_tuple_keys = field_validator("switch_mask_tuple", mode="before")(
        _bounded_integer_keys(12, 13)
    )
    _normalize_expected_mask_keys = field_validator("expected_switch_masks", mode="before")(
        _bounded_integer_keys(12, 13)
    )
    _normalize_readback_keys = field_validator("readbacks", mode="before")(
        _bounded_integer_keys(1, 3, 4, 5, 6, 9, 10, 12, 13, 205)
    )

    @model_validator(mode="after")
    def bind_terminal_authority(self):
        if set(self.expected_profile) != {4, 5, 6, 205}:
            raise ValueError("failed X terminal profile keys are incomplete")
        if self.expected_switch_masks not in ({}, {12: 1, 13: 0}, {12: 0, 13: 0}):
            raise ValueError("failed X terminal switch-mask metadata is invalid")
        if not self.expected_switch_masks and self.switch_mask_policy != "observed_only_oem_source_omits_x_writes":
            raise ValueError("failed X terminal observed-only switch policy is missing")
        if set(self.switch_mask_tuple) != {12, 13}:
            raise ValueError("failed X terminal switch-mask tuple is incomplete")
        if set(self.readbacks) != {1, 3, 4, 5, 6, 9, 10, 12, 13, 205}:
            raise ValueError("failed X terminal readbacks are incomplete")
        if any(parameter != row.param for parameter, row in self.readbacks.items()):
            raise ValueError("failed X terminal readback key does not match parameter")
        return self


class OperatorDashboardXSwitchReconciliationFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    intent: Literal["reconcile_switch_masks"]
    source_exact: Literal[False]
    adaptation: Literal["serial206_machine_safety_adaptation"]
    classification: Literal["serial206_machine_safety_adaptation"]
    writes: dict[Literal["12"], OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker]
    before: dict[StrictInt, OperatorDashboardXRegisterReadback] | OperatorDashboardXOmissionMarker
    after: dict[StrictInt, OperatorDashboardXRegisterReadback] | OperatorDashboardXOmissionMarker
    machine_bound_expected: dict[StrictInt, StrictInt]
    switch_mask_tuple: dict[StrictInt, StrictInt | None]
    preparation_invalidated: Literal[True]
    reference_invalidated: StrictBool
    reference_invalidation: OperatorDashboardXReferenceAuthorityEffect | None
    controller_command_acknowledged: StrictBool
    controller_terminal_state_verified: Literal[False]
    physical_motion_commanded: Literal[False]
    physical_effect_verified: Literal[False]
    failure: Literal["x_switch_mask_reconciliation_failed"]
    prior_lifecycle_state: str = Field(min_length=1, max_length=80)
    fresh_prepare_required: Literal[True]

    @model_validator(mode="after")
    def bind_switch_maps(self):
        if set(self.writes) != {"12"}:
            raise ValueError("X reconciliation write map is incomplete")
        for value in (self.before, self.after):
            if isinstance(value, dict) and set(value) != {12, 13}:
                raise ValueError("X reconciliation readbacks are incomplete")
        if self.machine_bound_expected != {12: 1, 13: 0}:
            raise ValueError("X reconciliation expected masks are invalid")
        if set(self.switch_mask_tuple) != {12, 13}:
            raise ValueError("X reconciliation switch tuple is incomplete")
        return self


class OperatorDashboardXCurrentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    axis: Literal["x", "y", "z"]
    board: StrictInt
    motor: StrictInt
    stage: Literal["warm_10", "run_31", "z_current_up", "disabled_1"]
    value: StrictInt
    result: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker
    verified: StrictBool

    @model_validator(mode="after")
    def bind_axis_address_and_receipt(self):
        expected = {"x": (5, 0), "y": (4, 0), "z": (4, 1)}[self.axis]
        if (self.board, self.motor) != expected:
            raise ValueError("current-write axis does not match controller address")
        if isinstance(self.result, OperatorDashboardXParameterWrite):
            if (self.result.board, self.result.motor) != expected:
                raise ValueError("current-write receipt does not match axis address")
            if self.result.set_value != self.value:
                raise ValueError("current-write receipt value does not match requested value")
        return self


class OperatorDashboardXEnableXYCurrentFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    intent: Literal["enableXY"]
    enabled: StrictBool
    writes: list[OperatorDashboardXCurrentWrite] = Field(max_length=16)
    waits_s: list[StrictFloat | StrictInt] = Field(max_length=2)
    gripper_current_written: Literal[False]
    physical_motion_commanded: Literal[False]
    controller_terminal_state_verified: Literal[False]
    source_anchor: Literal["ClassControlInterface.enableXY:5161-5194"]
    failure: Literal["enableXY_current_readback_failed"]


class OperatorDashboardXEnableXYZCurrentFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    intent: Literal["enableXYZ"]
    enabled: StrictBool
    writes: list[OperatorDashboardXCurrentWrite] = Field(max_length=16)
    waits_s: list[StrictFloat | StrictInt] = Field(max_length=3)
    z_current_up: StrictInt
    gripper_current_written: Literal[False]
    physical_motion_commanded: Literal[False]
    controller_terminal_state_verified: Literal[False]
    source_anchor: Literal["ClassControlInterface.enableXYZ:5113-5159"]
    failure: Literal["enableXYZ_current_readback_failed"]


class OperatorDashboardXCurrentNonMappingFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["enableXY_result_not_mapping", "enableXYZ_result_not_mapping"]
    physical_motion_commanded: Literal[False]


class OperatorDashboardXSetHomePositionUnavailable(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    intent: Literal["set_home"]
    failure: Literal["x_set_home_position_before_unavailable"]
    physical_motion_commanded: Literal[False]


class OperatorDashboardXSetHomeFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    intent: Literal["set_home"]
    source_method: Literal["ClassMotor.setHome (SAP1=0); recovery-only; no motion"]
    source_anchor: Literal["ClassMotor.cs:492-516; ClassHeadBoard.cs:121-124"]
    before: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    set_home: OperatorDashboardXSetHomeResult | OperatorDashboardXOmissionMarker
    after: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    controller_command_acknowledged: StrictBool
    controller_terminal_state_verified: Literal[False]
    reference_publication_required: Literal[False]
    physical_motion_commanded: Literal[False]
    physical_effect_verified: Literal[False]
    failure: Literal["x_set_home_zero_readback_not_verified", "x_home_evidence_not_verified"]


class OperatorDashboardXGenericHomeFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    intent: Literal[
        "startup_home", "diagnostic_home_axis", "manual_panel_home",
        "move_to_origin_home", "caught_plate_recovery_home"
    ]
    source_method: str = Field(min_length=1, max_length=300)
    home: OperatorDashboardXHomePrimitiveResult
    source_return: StrictInt | None
    position: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    terminal_speed: OperatorDashboardXSpeedReadback | OperatorDashboardXOmissionMarker
    controller_command_acknowledged: StrictBool
    controller_terminal_state_verified: Literal[False]
    reference_publication_required: Literal[False]
    physical_motion_commanded: StrictBool
    physical_effect_verified: Literal[False]
    failure: Literal["x_home_evidence_not_verified"]


class OperatorDashboardXStartupHomeFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    intent: Literal["startup_home"]
    source_method: Literal["axisSearchHome(250);20ms;setHome;SAP4=1700;40ms;moveX(6000)"]
    home: OperatorDashboardXAxisSearchHomeResult | OperatorDashboardXOmissionMarker
    home_evidence: OperatorDashboardXHomePrimitiveResult | OperatorDashboardXOmissionMarker
    set_home: OperatorDashboardXSetHomeResult | OperatorDashboardXOmissionMarker
    speed_restore: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker
    park: OperatorDashboardXMoveReceipt | OperatorDashboardXOmissionMarker
    controller_command_acknowledged: Literal[False]
    controller_terminal_state_verified: Literal[False]
    controller_position_steps: StrictInt | None
    oem_display_position_steps: Literal[0]
    reference_publication_required: Literal[False]
    physical_motion_commanded: Literal[True]
    physical_effect_verified: Literal[False]
    failure: Literal["x_startup_home_sequence_failed", "x_home_evidence_not_verified"]


class OperatorDashboardXAbsolutePositionUnavailable(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["x_position_before_unavailable"]
    command_issued: Literal[False]
    before: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    reference_before: OperatorDashboardXReference | OperatorDashboardXOmissionMarker
    physical_motion: Literal[False]
    acceleration_restore: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore_verified: StrictBool | None = None

    @model_validator(mode="after")
    def bind_acceleration_overlay(self):
        if (self.acceleration_restore is None) != (self.acceleration_restore_verified is None):
            raise ValueError("X acceleration restore overlay must be complete")
        return self


class OperatorDashboardXAccelerationSetupFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    source_mode: str = Field(min_length=1, max_length=200)
    requested_position_steps: StrictInt
    target_position_steps: StrictInt
    before: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    before_position_steps: StrictInt
    preflight: OperatorDashboardXPreflight | OperatorDashboardXOmissionMarker
    command_issued: Literal[False]
    source_noop: Literal[False]
    physical_motion_commanded: Literal[False]
    controller_command_acknowledged: Literal[False]
    acceleration_set: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker
    failure: Literal["x_acceleration_write_or_readback_failed", "x_acceleration_restore_failed"]
    acceleration_restore: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker
    acceleration_restore_verified: StrictBool
    reference_before: OperatorDashboardXReference | OperatorDashboardXOmissionMarker
    physical_motion: Literal[False]


class OperatorDashboardXAbsoluteNoopFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    source_mode: str = Field(min_length=1, max_length=200)
    requested_position_steps: StrictInt
    target_position_steps: StrictInt
    before: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    before_position_steps: StrictInt
    preflight: OperatorDashboardXPreflight | OperatorDashboardXOmissionMarker
    command_issued: StrictBool
    source_noop: Literal[True]
    physical_motion_commanded: Literal[False]
    controller_command_acknowledged: StrictBool
    noop_reason: str = Field(min_length=1, max_length=160)
    move: OperatorDashboardXMoveReceipt | OperatorDashboardXOmissionMarker | None = None
    after: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    after_position_steps: StrictInt | None
    terminal_speed: OperatorDashboardXSpeedReadback | OperatorDashboardXOmissionMarker
    controller_terminal_state_verified: Literal[False]
    target_event_128_observed: Literal[False]
    target_position_verified: Literal[False]
    physical_effect_verified: Literal[False]
    failure: Literal["x_noop_terminal_state_unverified", "x_transport_noop_terminal_state_unverified", "x_acceleration_restore_failed"]
    reference_before: OperatorDashboardXReference | OperatorDashboardXOmissionMarker
    physical_motion: Literal[False]
    acceleration_set: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore_verified: StrictBool | None = None


class OperatorDashboardXIssuedMoveFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    source_mode: str = Field(min_length=1, max_length=200)
    requested_position_steps: StrictInt
    target_position_steps: StrictInt
    before: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    before_position_steps: StrictInt
    preflight: OperatorDashboardXPreflight | OperatorDashboardXOmissionMarker
    command_issued: Literal[True]
    source_noop: Literal[False]
    physical_motion_commanded: Literal[True]
    controller_command_acknowledged: StrictBool
    event_window: OperatorDashboardXEventWindow | OperatorDashboardXOmissionMarker
    move: OperatorDashboardXMoveReceipt | OperatorDashboardXOmissionMarker
    failure: str = Field(min_length=1, max_length=200)
    reference_before: OperatorDashboardXReference | OperatorDashboardXOmissionMarker
    physical_motion: Literal[True]
    physical_effect_ambiguous: Literal[True] | None = None
    wait: OperatorDashboardXTargetWaitResult | OperatorDashboardXOmissionMarker | None = None
    wait_verified: StrictBool | None = None
    events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    after: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker | None = None
    after_position_steps: StrictInt | None = None
    terminal_speed: OperatorDashboardXSpeedReadback | OperatorDashboardXOmissionMarker | None = None
    controller_error_events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    target_events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    target_event_128_verified: StrictBool | None = None
    target_event_128_observed: StrictBool | None = None
    target_position_verified: StrictBool | None = None
    target_position_delta_steps: StrictInt | None = None
    controller_terminal_state_verified: StrictBool | None = None
    physical_effect_verified: StrictBool | None = None
    safety_stop: OperatorDashboardXExactStopResult | OperatorDashboardXOmissionMarker | None = None
    reference_desync: OperatorDashboardXReferenceAuthorityEffect | None = None
    reference_state: OperatorDashboardXReferenceAuthorityEffect | None = None
    acceleration_set: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore_verified: StrictBool | None = None

    @model_validator(mode="after")
    def validate_issued_failure_literal(self):
        allowed = {
            "x_move_command_not_acknowledged", "No24V", "controller_async_error_13",
            "controller_async_error_14", "oem_moveToAbs_stall_event",
            "oem_moveToAbs_target_event_timeout", "x_target_wait_not_verified",
            "x_terminal_zero_speed_not_verified", "x_controller_error_event",
            "x_target_event_128_missing_or_stale", "x_target_position_not_verified",
            "x_absolute_terminal_evidence_not_accepted",
            "x_motion_reference_metadata_not_verified", "x_acceleration_restore_failed",
        }
        if self.failure not in allowed:
            raise ValueError("unrecognized issued X move failure")
        terminal_fields = {
            "wait", "wait_verified", "events", "after", "after_position_steps",
            "terminal_speed", "controller_error_events", "target_events",
            "target_event_128_verified", "target_event_128_observed",
            "target_position_verified", "controller_terminal_state_verified",
            "physical_effect_verified",
        }
        present = self.model_fields_set
        if self.failure == "x_move_command_not_acknowledged":
            if self.controller_command_acknowledged is not False or present.intersection(terminal_fields):
                raise ValueError("unacknowledged X move contains terminal branch evidence")
        elif self.controller_command_acknowledged is not True or not terminal_fields.issubset(present):
            raise ValueError("post-ack X move failure lacks terminal branch evidence")
        acceleration_fields = {"acceleration_set", "acceleration_restore", "acceleration_restore_verified"}
        if self.failure == "x_acceleration_restore_failed":
            if not acceleration_fields.issubset(present) or self.acceleration_restore_verified is not False:
                raise ValueError("X acceleration restore failure evidence is incomplete")
        elif present.intersection(acceleration_fields) and not acceleration_fields.issubset(present):
            raise ValueError("X acceleration overlay must be complete")
        return self


class OperatorDashboardXRelativePositionUnavailable(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["x_position_before_unavailable"]
    physical_motion_commanded: Literal[False]
    before: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker


class OperatorDashboardXRelativeLimitFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["x_source_coordinate_inside_limit_margin"]
    command_issued: Literal[False]
    before_position_steps: StrictInt
    requested_steps: StrictInt
    target_position_steps: StrictInt
    source_min_steps: Literal[0]
    source_max_steps: Literal[90263]
    source_limit_margin_steps: Literal[20]
    physical_motion_commanded: Literal[False]


class OperatorDashboardXRelativeMoveFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    intent: Literal["move_steps"]
    source_mode: Literal["ClassControlInterface.moveSteps"] | None = None
    source_noop: StrictBool | None = None
    noop_reason: Literal["zero_steps"] | None = None
    requested_steps: StrictInt
    target_position_steps: StrictInt
    before: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker
    before_position_steps: StrictInt
    preflight: OperatorDashboardXPreflight | OperatorDashboardXOmissionMarker
    event_window: OperatorDashboardXEventWindow | None = None
    move: OperatorDashboardXMoveReceipt | OperatorDashboardXOmissionMarker | None = None
    command_issued: StrictBool
    physical_motion_commanded: StrictBool
    controller_command_acknowledged: StrictBool
    failure: str | None = Field(max_length=200)
    after: OperatorDashboardXPositionReadback | OperatorDashboardXOmissionMarker | None = None
    after_position_steps: StrictInt | None = None
    terminal_speed: OperatorDashboardXSpeedReadback | OperatorDashboardXOmissionMarker | None = None
    target_event_128_observed: StrictBool | None = None
    controller_terminal_state_verified: StrictBool | None = None
    physical_effect_verified: StrictBool | None = None
    reference_before: OperatorDashboardXReference | OperatorDashboardXOmissionMarker
    physical_motion: StrictBool
    wait: OperatorDashboardXTargetWaitResult | OperatorDashboardXOmissionMarker | None = None
    wait_verified: StrictBool | None = None
    events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    controller_error_events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    target_events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    target_event_128_verified: StrictBool | None = None
    target_position_verified: StrictBool | None = None
    target_position_delta_steps: StrictInt | None = None
    safety_stop: OperatorDashboardXExactStopResult | OperatorDashboardXOmissionMarker | None = None
    reference_desync: OperatorDashboardXReferenceAuthorityEffect | None = None
    acceleration_set: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore: OperatorDashboardXParameterWrite | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore_verified: StrictBool | None = None

    @model_validator(mode="after")
    def validate_relative_failure(self):
        allowed = {
            "x_noop_terminal_state_unverified", "x_relative_command_ack_failed", "No24V",
            "controller_async_error_13", "controller_async_error_14",
            "oem_moveToAbs_stall_event", "oem_moveToAbs_target_event_timeout",
            "x_target_wait_not_verified", "x_terminal_zero_speed_not_verified",
            "x_controller_error_event", "x_target_event_128_missing_or_stale",
            "x_target_position_not_verified", "x_absolute_terminal_evidence_not_accepted",
            "x_motion_reference_metadata_not_verified", "x_acceleration_restore_failed",
        }
        if self.failure not in allowed:
            raise ValueError("unrecognized relative X move failure")
        present = self.model_fields_set
        terminal_fields = {
            "wait", "wait_verified", "events", "after", "after_position_steps",
            "terminal_speed", "controller_error_events", "target_events",
            "target_event_128_verified", "target_position_verified",
            "controller_terminal_state_verified", "physical_effect_verified",
        }
        acceleration_fields = {"acceleration_set", "acceleration_restore", "acceleration_restore_verified"}
        if self.source_noop is True:
            if (
                self.source_mode != "ClassControlInterface.moveSteps"
                or self.noop_reason != "zero_steps"
                or self.command_issued is not False
                or self.physical_motion_commanded is not False
                or present.intersection({"event_window", "move", "wait", "events", "target_events"})
            ):
                raise ValueError("relative X no-op branch is contradictory")
            required = {
                "source_mode", "source_noop", "noop_reason", "controller_terminal_state_verified",
                "target_event_128_observed", "target_position_verified", "physical_effect_verified",
            }
            if not required.issubset(present):
                raise ValueError("relative X no-op evidence is incomplete")
        else:
            if self.command_issued is not True or self.physical_motion_commanded is not True:
                raise ValueError("relative X issued branch must record motion command")
            if "event_window" not in present or "move" not in present:
                raise ValueError("relative X issued branch lacks command evidence")
            if self.failure == "x_relative_command_ack_failed":
                if self.controller_command_acknowledged is not False or present.intersection(terminal_fields):
                    raise ValueError("relative X acknowledgment failure contains terminal evidence")
            elif self.controller_command_acknowledged is not True or not terminal_fields.issubset(present):
                raise ValueError("relative X post-ack failure lacks terminal evidence")
        if self.physical_motion is not self.physical_motion_commanded:
            raise ValueError("relative X physical-motion fields disagree")
        if self.failure == "x_acceleration_restore_failed":
            if not acceleration_fields.issubset(present) or self.acceleration_restore_verified is not False:
                raise ValueError("relative X acceleration restore evidence is incomplete")
        elif present.intersection(acceleration_fields) and not acceleration_fields.issubset(present):
            raise ValueError("relative X acceleration overlay must be complete")
        return self


class OperatorDashboardXMoveXYFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    source_operation: Literal["ClassControlInterface.moveXY"]
    source_anchor: Literal["ClassControlInterface.cs:4285-4367"]
    requested: dict[Literal["x", "y"], StrictInt]
    board_present: dict[Literal["x", "y"], StrictBool]
    ignored_compatibility_inputs: OperatorDashboardXMoveXYIgnoredCompatibilityInputs
    oem_wait_timeout_ms: Literal[5000]
    branch: Literal[
        "missing_y_calls_moveX_x", "missing_x_calls_moveX_y", "source_noop",
        "parallel_setup_failed_before_motion", "near_axis_sequential", "parallel"
    ] | None = None
    failure: str | None = Field(default=None, max_length=200)
    fallback: OperatorDashboardXMoveReceipt | OperatorDashboardXOmissionMarker | None = None
    command_issued: StrictBool | None = None
    physical_motion_commanded: StrictBool | None = None
    reference_before: OperatorDashboardXReference | OperatorDashboardXOmissionMarker | None = None
    before: OperatorDashboardXMoveXYNullableIntegerMap | None = None
    distances: OperatorDashboardXMoveXYIntegerMap | None = None
    source_noop: StrictBool | None = None
    noop_reason: str | None = Field(default=None, max_length=160)
    controller_command_acknowledged: StrictBool | None = None
    target_event_128_observed: StrictBool | None = None
    controller_error_events: list[OperatorDashboardXBusEvent] | None = None
    motion_metadata_recorded: StrictBool | None = None
    after: OperatorDashboardXMoveXYNullableIntegerMap | OperatorDashboardXOmissionMarker | None = None
    terminal_speed: OperatorDashboardXYSpeedMap | OperatorDashboardXOmissionMarker | None = None
    terminal_speed_steps_s: OperatorDashboardXMoveXYNullableIntegerMap | None = None
    target_position_verified: StrictBool | None = None
    controller_terminal_state_verified: StrictBool | None = None
    physical_effect_verified: StrictBool | None = None
    acceleration_set: OperatorDashboardXMoveXYAccelerationMap | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore: OperatorDashboardXMoveXYAccelerationMap | OperatorDashboardXOmissionMarker | None = None
    reference_effect: Literal["unchanged_no_motion_delivered"] | None = None
    launch_order: list[Literal["x", "y"]] | None = Field(default=None, max_length=2)
    acceleration_selected: OperatorDashboardXMoveXYIntegerMap | None = None
    event_window: OperatorDashboardXEventWindow | OperatorDashboardXOmissionMarker | None = None
    stagger_ms: StrictInt | None = None
    pre_wait_sleep_ms: Literal[5] | None = None
    pair_wait: OperatorDashboardXMultiTargetWaitResult | None | OperatorDashboardXOmissionMarker = None
    commands: OperatorDashboardXMoveXYCommandMap | OperatorDashboardXOmissionMarker | None = None
    waits: OperatorDashboardXMoveXYWaitMap | OperatorDashboardXOmissionMarker | None = None
    events: list[OperatorDashboardXBusEvent] | OperatorDashboardXOmissionMarker | None = None
    axis_evidence: OperatorDashboardXMoveXYAxisEvidenceMap | OperatorDashboardXOmissionMarker | None = None
    acceleration_restore_verified: StrictBool | None = None
    moved_axes: list[Literal["x", "y"]] | None = Field(default=None, max_length=2)
    safety_stop: OperatorDashboardXYStopMap | OperatorDashboardXOmissionMarker | None = None
    reference_state: OperatorDashboardXYReferenceAuthorityEffect | None = None
    reference_desync: OperatorDashboardXYReferenceAuthorityEffect | None = None

    @model_validator(mode="after")
    def bind_move_xy_branch(self):
        present = self.model_fields_set
        common_terminal = {
            "commands", "waits", "events", "axis_evidence", "moved_axes", "after",
            "acceleration_restore", "acceleration_restore_verified",
            "controller_command_acknowledged", "controller_terminal_state_verified",
            "target_position_verified",
        }
        parallel_only = {"event_window", "stagger_ms", "pre_wait_sleep_ms", "pair_wait"}
        if self.branch is None:
            required = {"failure", "command_issued", "physical_motion_commanded"}
            if (
                self.failure != "moveXY_position_before_not_acknowledged"
                or self.command_issued is not False
                or self.physical_motion_commanded is not False
                or not required.issubset(present)
            ):
                raise ValueError("unbranched moveXY result is not the producer pre-readback failure")
            if present.intersection(common_terminal | parallel_only | {"fallback", "before", "distances"}):
                raise ValueError("moveXY pre-readback failure contains later-branch evidence")
            return self
        if self.branch in {"missing_y_calls_moveX_x", "missing_x_calls_moveX_y"}:
            required = {"fallback"}
            forbidden = common_terminal | parallel_only | {"failure", "before", "distances", "launch_order", "acceleration_set"}
        elif self.branch == "source_noop":
            required = {
                "reference_before", "before", "distances", "source_noop", "noop_reason",
                "command_issued", "physical_motion_commanded", "controller_command_acknowledged",
                "target_event_128_observed", "controller_error_events", "motion_metadata_recorded",
                "after", "terminal_speed", "terminal_speed_steps_s", "target_position_verified",
                "controller_terminal_state_verified", "physical_effect_verified", "failure",
            }
            forbidden = parallel_only | common_terminal | {"fallback", "launch_order", "acceleration_set", "acceleration_restore"}
            if self.source_noop is not True or self.command_issued is not False or self.physical_motion_commanded is not False:
                raise ValueError("moveXY source-noop values are contradictory")
        elif self.branch == "parallel_setup_failed_before_motion":
            required = {
                "reference_before", "before", "distances", "acceleration_set",
                "acceleration_restore", "failure", "command_issued", "reference_effect",
            }
            forbidden = common_terminal | parallel_only | {"fallback", "launch_order", "commands", "waits"}
            if self.command_issued is not False:
                raise ValueError("moveXY setup-failure branch cannot command motion")
        elif self.branch == "near_axis_sequential":
            required = {
                "reference_before", "before", "distances", "launch_order", "commands", "waits",
                "events", "axis_evidence", "moved_axes", "after", "acceleration_restore",
                "acceleration_restore_verified", "controller_command_acknowledged",
                "controller_terminal_state_verified", "target_position_verified",
            }
            forbidden = parallel_only | {"fallback", "acceleration_selected", "acceleration_set"}
        else:
            required = {
                "reference_before", "before", "distances", "launch_order", "commands", "waits",
                "events", "axis_evidence", "moved_axes", "after", "acceleration_selected",
                "acceleration_set", "acceleration_restore", "acceleration_restore_verified",
                "event_window", "stagger_ms", "pre_wait_sleep_ms", "pair_wait",
                "controller_command_acknowledged", "controller_terminal_state_verified",
                "target_position_verified",
            }
            forbidden = {"fallback"}
        if not required.issubset(present):
            raise ValueError(f"moveXY {self.branch} branch evidence is incomplete")
        if present.intersection(forbidden):
            raise ValueError(f"moveXY {self.branch} branch contains incompatible evidence")
        return self


class OperatorDashboardXMoveXYWrapperFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["xy_interrupted_or_generation_changed"]
    primitive_result: OperatorDashboardXMoveXYFailure | OperatorDashboardXOmissionMarker


class OperatorDashboardHomeXYWriteMap(
    RootModel[
        dict[
            Literal["x_speed", "x_acc", "y_speed", "y_acc"],
            OperatorDashboardXYParameterWrite,
        ]
    ]
):
    @model_validator(mode="after")
    def bind_home_xy_writes(self):
        expected_keys = {"x_speed", "x_acc", "y_speed", "y_acc"}
        if set(self.root) != expected_keys:
            raise ValueError("HomeXY write map is incomplete")
        expected_addresses = {
            "x_speed": (5, 4, 0), "x_acc": (5, 5, 0),
            "y_speed": (4, 4, 0), "y_acc": (4, 5, 0),
        }
        for key, row in self.root.items():
            if (row.board, row.param, row.motor) != expected_addresses[key]:
                raise ValueError("HomeXY write receipt does not match fixed address")
        return self


class OperatorDashboardHomeXYSetupMap(OperatorDashboardHomeXYWriteMap):
    @model_validator(mode="after")
    def bind_setup_values(self):
        if any(row.set_value != 200 for row in self.root.values()):
            raise ValueError("HomeXY setup values must all equal 200")
        return self


class OperatorDashboardHomeXYRestoreMap(OperatorDashboardHomeXYWriteMap):
    @model_validator(mode="after")
    def bind_restore_values(self):
        expected: dict[Literal["x_speed", "x_acc", "y_speed", "y_acc"], int] = {
            "x_speed": 1700, "x_acc": 350, "y_speed": 1800, "y_acc": 400,
        }
        if any(self.root[key].set_value != value for key, value in expected.items()):
            raise ValueError("HomeXY restore values do not match the OEM tuple")
        return self


class OperatorDashboardHomeXYSourceReturn(
    RootModel[dict[Literal["x", "y"], StrictInt | None]]
):
    @model_validator(mode="after")
    def require_both_axes(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("HomeXY source-return map must contain X and Y")
        return self


class OperatorDashboardHomeXYPositionMap(
    RootModel[dict[Literal["x", "y"], OperatorDashboardXYPositionReadback]]
):
    @model_validator(mode="after")
    def bind_axis_addresses(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("HomeXY position map must contain X and Y")
        expected = {"x": 5, "y": 4}
        if any(row.board != expected[axis] or row.motor != 0 for axis, row in self.root.items()):
            raise ValueError("HomeXY position readback does not match axis address")
        return self


class OperatorDashboardHomeXYHomeMap(
    RootModel[dict[Literal["x", "y"], OperatorDashboardXHomePrimitiveResult]]
):
    @model_validator(mode="after")
    def require_both_axes(self):
        if set(self.root) != {"x", "y"}:
            raise ValueError("HomeXY home map must contain X and Y")
        return self


class OperatorDashboardXHomeXYSkippedPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    skipped: Literal[True]
    reason: Literal["homexy_missing_board_source_null"]


class OperatorDashboardXHomeXYSourceNoop(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[True]
    intent: Literal["home_xy"]
    source_noop: Literal[True]
    source_return: None
    source_return_semantics: Literal["null_when_either_board_absent"]
    board_presence: dict[Literal["x", "y"], StrictBool]
    command_issued: Literal[False]
    physical_motion_commanded: Literal[False]
    controller_command_acknowledged: Literal[False]
    target_event_128_observed: Literal[False]
    physical_effect_verified: Literal[False]
    source_anchor: Literal["ClassControlInterface.HomeXY:5054-5070"]
    live_preflight: OperatorDashboardXHomeXYSkippedPreflight | OperatorDashboardXOmissionMarker

    @model_validator(mode="after")
    def bind_board_presence(self):
        if set(self.board_presence) != {"x", "y"} or all(self.board_presence.values()):
            raise ValueError("HomeXY source no-op requires at least one missing board")
        return self


class OperatorDashboardXHomeXYFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    intent: Literal["home_xy"]
    command_issued: StrictBool
    setup: OperatorDashboardHomeXYSetupMap | OperatorDashboardXOmissionMarker
    home: OperatorDashboardHomeXYHomeMap | OperatorDashboardXOmissionMarker
    source_return: OperatorDashboardHomeXYSourceReturn
    home_errors: list[str] = Field(max_length=16)
    positions: OperatorDashboardHomeXYPositionMap | OperatorDashboardXOmissionMarker
    restore: OperatorDashboardHomeXYRestoreMap | OperatorDashboardXOmissionMarker
    controller_command_acknowledged: StrictBool
    controller_terminal_state_verified: StrictBool
    reference_publication_required: StrictBool
    physical_effect_verified: Literal[False]
    failure: Literal["homexy_evidence_not_verified", "x_home_evidence_not_verified"] | None
    source_anchor: Literal["ClassControlInterface.HomeXY:5054-5069"]
    safety_stop: OperatorDashboardXYStopMap | OperatorDashboardXOmissionMarker | None = None
    reference_desync: OperatorDashboardXYReferenceAuthorityEffect | None = None
    live_preflight: OperatorDashboardXPreflight | OperatorDashboardXOmissionMarker

    @model_validator(mode="after")
    def bind_failed_home_xy(self):
        expected_ok = bool(
            self.controller_command_acknowledged
            and self.controller_terminal_state_verified
            and self.reference_publication_required
            and self.failure is None
        )
        if self.ok is not expected_ok:
            raise ValueError("HomeXY result status does not match controller authority")
        if self.ok and self.safety_stop is not None:
            raise ValueError("successful HomeXY result cannot carry safety-stop evidence")
        if not self.ok and self.safety_stop is None:
            raise ValueError("failed HomeXY result requires safety-stop evidence")
        if self.command_issued and isinstance(self.home, OperatorDashboardXOmissionMarker):
            raise ValueError("issued HomeXY failure cannot omit home authority")
        if not self.command_issued and self.home_errors:
            raise ValueError("HomeXY setup failure cannot claim home worker errors")
        return self


class OperatorDashboardXHomeXYPrimitiveResult(
    RootModel[
        OperatorDashboardXHomeXYSourceNoop
        | OperatorDashboardXHomeXYFailure
        | OperatorDashboardXOmissionMarker
    ]
):
    pass


class OperatorDashboardXHomeXYNonMappingFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["homexy_result_not_mapping"]
    live_preflight: OperatorDashboardXPreflight | OperatorDashboardXOmissionMarker


class OperatorDashboardXHomeXYWrapperFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["homexy_interrupted_or_generation_changed"]
    primitive_result: OperatorDashboardXHomeXYPrimitiveResult


class OperatorDashboardXBoardLifecycleInvalidation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: Literal["board5_lifecycle_change"] | OperatorDashboardXOmissionMarker
    transition: Literal["activated", "deactivated"] | OperatorDashboardXOmissionMarker
    command64_value: Literal[0, 1] | None
    previous_state: str = Field(min_length=1, max_length=80)
    ack: OperatorDashboardXTmclAck | OperatorDashboardXOmissionMarker | None
    invalidated_at: StrictFloat | StrictInt
    reference_invalidation: OperatorDashboardXReferenceAuthorityEffect | None

    @model_validator(mode="after")
    def bind_transition_to_command64(self):
        if isinstance(self.transition, OperatorDashboardXOmissionMarker):
            if self.command64_value not in {0, 1}:
                raise ValueError("omitted board-5 transition requires a typed command-64 value")
            return self
        allowed = {
            ("activated", 1),
            ("activated", None),
            ("deactivated", 0),
        }
        if (self.transition, self.command64_value) not in allowed:
            raise ValueError("board-5 transition does not match command-64 value")
        return self


class OperatorDashboardXPrimitiveOperationResult(
    RootModel[
        OperatorDashboardXSafetyInterruptionWrapper
        | OperatorDashboardXIntentExceptionFailure
        | OperatorDashboardXNonMappingFailure
        | OperatorDashboardXProfileParameterFailure
        | OperatorDashboardXTerminalStatusFailure
        | OperatorDashboardXEnableXYCurrentFailure
        | OperatorDashboardXEnableXYZCurrentFailure
        | OperatorDashboardXCurrentNonMappingFailure
        | OperatorDashboardXSetHomePositionUnavailable
        | OperatorDashboardXSetHomeFailure
        | OperatorDashboardXGenericHomeFailure
        | OperatorDashboardXStartupHomeFailure
        | OperatorDashboardXStopFailure
        | OperatorDashboardXAggregateAbortFailure
        | OperatorDashboardXAbsolutePositionUnavailable
        | OperatorDashboardXAccelerationSetupFailure
        | OperatorDashboardXAbsoluteNoopFailure
        | OperatorDashboardXIssuedMoveFailure
        | OperatorDashboardXRelativePositionUnavailable
        | OperatorDashboardXRelativeLimitFailure
        | OperatorDashboardXRelativeMoveFailure
        | OperatorDashboardXPendingTicket
        | OperatorDashboardXOmissionMarker
    ]
):
    pass


class OperatorDashboardXGenerationChangedDuringCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: Literal["x_generation_changed_during_command"]
    command_issued: StrictBool
    recorded_generation: StrictInt
    current_generation: StrictInt
    primitive_result: OperatorDashboardXPrimitiveOperationResult


class OperatorDashboardXLifecycleLastFailure(
    RootModel[
        OperatorDashboardXGenuineStringFailure
        | OperatorDashboardXOmissionMarker
        | OperatorDashboardXGenerationDriftFailure
        | OperatorDashboardXSafetyInterruptFailure
        | OperatorDashboardXReferenceOperationFailure
        | OperatorDashboardXPreparationReceipt
        | OperatorDashboardXIntentExceptionFailure
        | OperatorDashboardXNonMappingFailure
        | OperatorDashboardXMaskNonMappingFailure
        | OperatorDashboardXSafetyInterruptionWrapper
        | OperatorDashboardXGenerationChangedDuringCommand
        | OperatorDashboardXProfileParameterFailure
        | OperatorDashboardXTerminalStatusFailure
        | OperatorDashboardXSwitchReconciliationFailure
        | OperatorDashboardXEnableXYCurrentFailure
        | OperatorDashboardXEnableXYZCurrentFailure
        | OperatorDashboardXCurrentNonMappingFailure
        | OperatorDashboardXSetHomePositionUnavailable
        | OperatorDashboardXSetHomeFailure
        | OperatorDashboardXGenericHomeFailure
        | OperatorDashboardXStartupHomeFailure
        | OperatorDashboardXStopFailure
        | OperatorDashboardXAggregateAbortFailure
        | OperatorDashboardXAbsolutePositionUnavailable
        | OperatorDashboardXAccelerationSetupFailure
        | OperatorDashboardXAbsoluteNoopFailure
        | OperatorDashboardXIssuedMoveFailure
        | OperatorDashboardXRelativePositionUnavailable
        | OperatorDashboardXRelativeLimitFailure
        | OperatorDashboardXRelativeMoveFailure
        | OperatorDashboardXMoveXYFailure
        | OperatorDashboardXMoveXYWrapperFailure
        | OperatorDashboardXHomeXYFailure
        | OperatorDashboardXHomeXYNonMappingFailure
        | OperatorDashboardXHomeXYWrapperFailure
        | OperatorDashboardXBoardLifecycleInvalidation
    ]
):
    pass


class OperatorDashboardXLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    schema_version: Literal["bioxp.serial206_x_lifecycle.v2"]
    state: Literal[
        "unprepared",
        "prepared_unreferenced",
        "executing",
        "awaiting_operator_observation",
        "referenced_ready",
        "failed_latched",
    ]
    generation: StrictInt | None
    board_lifecycle_generation: StrictInt | None
    reference_state: Literal["unknown", "referenced", "desynced"]
    prepared_receipt: OperatorDashboardXPreparationReceipt | OperatorDashboardXOmissionMarker | None
    active_receipt: OperatorDashboardXActiveReceipt | OperatorDashboardXOmissionMarker | None
    pending_ticket: OperatorDashboardXPendingTicket | OperatorDashboardXOmissionMarker | None
    awaiting_observation_receipt_id: str | None = Field(max_length=160)
    terminal_state: None
    last_failure: OperatorDashboardXLifecycleLastFailure | OperatorDashboardXFailedLifecycleEvidence | None
    receipt_storage: Literal["robot_sqlite"]
    receipt_detail_on_request: Literal[True]
    recent_receipt_count: StrictInt = Field(ge=0)
    latest_receipt: OperatorDashboardXReceiptSummary | None

class OperatorDashboardXLiveStatusSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    axis: Literal["x"]
    board: Literal[5]
    motor: Literal[0]
    position_steps: StrictInt | None
    speed_steps_s: StrictInt | None
    max_speed: StrictInt | None
    max_acceleration: StrictInt | None
    max_current: StrictInt | None
    left_switch_state: StrictInt | None
    right_switch_state: StrictInt | None
    right_switch_disabled: StrictBool | None
    left_switch_disabled: StrictBool | None
    stall_guard: StrictInt | None
    profile_verified: StrictBool
    expected_profile: dict[StrictInt, StrictInt] = Field(min_length=4, max_length=4)
    switch_mask_verified: StrictBool | None = None
    switch_mask_tuple: dict[StrictInt, StrictInt | None] = Field(min_length=2, max_length=2)
    expected_switch_masks: dict[StrictInt, StrictInt] = Field(default_factory=dict, max_length=2)
    switch_mask_policy: Literal["observed_only_oem_source_omits_x_writes"] | None = None
    readbacks: dict[StrictInt, OperatorDashboardXRegisterReadback] = Field(min_length=10, max_length=10)
    authority: Literal["serial206_x_terminal_register_readback"]
    failure: Literal[
        "x_terminal_speed_not_typed_integer_zero",
        "x_terminal_readback_not_verified",
        "x_oem_profile_or_switch_mask_mismatch",
        "x_oem_profile_mismatch",
    ] | None

    _normalize_expected_profile_keys = field_validator("expected_profile", mode="before")(
        _bounded_integer_keys(4, 5, 6, 205)
    )
    _normalize_switch_tuple_keys = field_validator("switch_mask_tuple", mode="before")(
        _bounded_integer_keys(12, 13)
    )
    _normalize_expected_mask_keys = field_validator("expected_switch_masks", mode="before")(
        _bounded_integer_keys(12, 13)
    )
    _normalize_readback_keys = field_validator("readbacks", mode="before")(
        _bounded_integer_keys(1, 3, 4, 5, 6, 9, 10, 12, 13, 205)
    )

    @model_validator(mode="after")
    def bind_live_status_authority(self):
        if set(self.expected_profile) != {4, 5, 6, 205}:
            raise ValueError("serial-206 X live expected profile keys are incomplete")
        if self.expected_switch_masks not in ({}, {12: 1, 13: 0}, {12: 0, 13: 0}):
            raise ValueError("serial-206 X live switch-mask metadata is invalid")
        if not self.expected_switch_masks and self.switch_mask_policy != "observed_only_oem_source_omits_x_writes":
            raise ValueError("serial-206 X live observed-only switch policy is missing")
        if set(self.switch_mask_tuple) != {12, 13}:
            raise ValueError("serial-206 X switch-mask tuple is incomplete")
        if set(self.readbacks) != {1, 3, 4, 5, 6, 9, 10, 12, 13, 205}:
            raise ValueError("serial-206 X terminal readbacks are incomplete")
        if any(parameter != row.param for parameter, row in self.readbacks.items()):
            raise ValueError("live readback key does not match nested parameter")
        return self


class OperatorDashboardXLiveStatusFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    failure: str = Field(min_length=1, max_length=1000)


class OperatorDashboardXLiveStatus(
    RootModel[
        OperatorDashboardXLiveStatusSuccess
        | OperatorDashboardXLiveStatusFailure
        | OperatorDashboardXOmissionMarker
    ]
):
    @property
    def readbacks(self) -> dict[int, OperatorDashboardXRegisterReadback]:
        return self.root.readbacks if isinstance(self.root, OperatorDashboardXLiveStatusSuccess) else {}


class OperatorDashboardXSwitchMasks(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected: dict[StrictInt, StrictInt] = Field(default_factory=dict, max_length=2)
    verified: StrictBool | None = None
    observed: dict[StrictInt, StrictInt | None] | None = Field(default=None, max_length=2)
    policy: Literal["observed_only_oem_source_omits_x_writes"] | None = None

    _normalize_expected_keys = field_validator("expected", mode="before")(
        _bounded_integer_keys(12, 13)
    )
    _normalize_observed_keys = field_validator("observed", mode="before")(
        _bounded_integer_keys(12, 13)
    )

    @model_validator(mode="after")
    def bind_expected_switch_masks(self):
        if self.policy == "observed_only_oem_source_omits_x_writes":
            if self.expected or self.verified is not None:
                raise ValueError("observed-only X switch registers cannot carry expected or verified authority")
            if self.observed is not None and set(self.observed) != {12, 13}:
                raise ValueError("observed-only X switch register tuple is incomplete")
            return self
        if self.expected not in ({12: 1, 13: 0}, {12: 0, 13: 0}) or self.verified is None:
            raise ValueError("legacy serial-206 X switch-mask metadata is invalid")
        return self


class OperatorDashboardXProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected: dict[str, StrictInt] = Field(default_factory=dict, max_length=16)
    verified: StrictBool

    _bound_expected_keys = field_validator("expected", mode="before")(
        _bounded_string_keys("4", "5", "6", "205")
    )

    @model_validator(mode="after")
    def require_expected_profile_keys(self):
        if set(self.expected) != {"4", "5", "6", "205"}:
            raise ValueError("serial-206 X profile authority keys are invalid")
        return self


class OperatorDashboardXProviderSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    authority: Literal["Serial206OemInitializationProvider"]
    axis: Literal["x"]
    board: Literal[5]
    motor: Literal[0]
    source_min_steps: Literal[0]
    source_max_steps: Literal[90263]
    # Current robot projection publishes null: its production X profile has no
    # axis_max_source key. Preserve unknown provenance, not invented authority.
    source_limit_authority: None = None
    effective_absolute_min_steps: Literal[60]
    relative_limit_margin_steps: Literal[20]
    current_generation: StrictInt
    current_board_lifecycle_generation: StrictInt | None
    board_generation_fresh: StrictBool
    lifecycle: OperatorDashboardXLifecycle
    live_status: OperatorDashboardXLiveStatus
    switch_masks: OperatorDashboardXSwitchMasks
    profile: OperatorDashboardXProfile
    reference: OperatorDashboardXReference
    bound: StrictBool
    physical_position_verified: Literal[False]


class OperatorDashboardXProviderFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: Literal[False]
    axis: Literal["x"]
    state: Literal["failed_latched"]
    failure: str = Field(pattern=r"^projection_failed:[A-Za-z_][A-Za-z0-9_]*$")
    bound: StrictBool
    physical_position_verified: Literal[False]


class OperatorDashboardXProviderUnavailable(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    bound: Literal[False]
    physical_position_verified: Literal[False]


class OperatorDashboardXProvider(
    RootModel[
        OperatorDashboardXProviderSuccess
        | OperatorDashboardXProviderFailure
        | OperatorDashboardXProviderUnavailable
    ]
):
    @property
    def lifecycle(self) -> OperatorDashboardXLifecycle | None:
        return self.root.lifecycle if isinstance(self.root, OperatorDashboardXProviderSuccess) else None

    @property
    def live_status(self) -> OperatorDashboardXLiveStatus | None:
        return self.root.live_status if isinstance(self.root, OperatorDashboardXProviderSuccess) else None

    @property
    def reference(self) -> OperatorDashboardXReference | None:
        return self.root.reference if isinstance(self.root, OperatorDashboardXProviderSuccess) else None


class OperatorDashboardSnapshotFreshness(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: str = Field(min_length=1, max_length=80)
    age_s: StrictFloat | StrictInt | None = None
    fresh_for_s: StrictFloat | StrictInt | None = None


class OperatorDashboardXAxis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: OperatorDashboardAxis | None = None
    provider: OperatorDashboardXProvider
    snapshot_freshness: OperatorDashboardSnapshotFreshness
    last_failure: OperatorDashboardXLifecycleLastFailure | OperatorDashboardXFailedLifecycleEvidence | None = None
    latest_receipt: OperatorDashboardXReceiptSummary | None = None
    authority: str = Field(min_length=1, max_length=120)
    physical_position_verified: Literal[False]

class OperatorDashboardTemperature(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sensor: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    unit: Literal["°C"]
    temperature_c: StrictFloat | StrictInt | None = None
    available: StrictBool


class OperatorDashboardPipetteTransportDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["OEM Novo.Devices.CanInterfaceBoard over one shared NovoRouter"]
    vid: Literal["0x03eb"]
    pid: Literal["0x2423"]
    alt: StrictInt
    shared_bioxp_usb_runtime: StrictBool


class OperatorDashboardPipetteHardwareEvidence(BaseModel):
    """Closed, typed readback evidence exactly matching the BioXP producer envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    hardware_truth_level: Literal["hardware_query", "unparsed_hardware_reply", "no_readback"] | None = None
    reply_received: StrictBool | None = None
    semantic_ok: StrictBool | None = None
    tip_loaded: StrictBool | None = None
    pressure: StrictFloat | StrictInt | None = None
    error: str | None = Field(default=None, max_length=2000)
    delivery_verified: StrictBool | None = None
    controller_acknowledged: StrictBool | None = None
    completion_verified: StrictBool | None = None
    board_id: StrictInt | None = None
    payload: list[StrictInt] | None = None
    dlc: StrictInt | None = None
    command_name: str | None = Field(default=None, max_length=240)
    ack_required: StrictBool | None = None
    tx_ok: StrictBool | None = None
    immediate_ack_received: StrictBool | None = None
    semantic_query_response_verified: StrictBool | None = None
    completion_deferred: StrictBool | None = None
    completion_owner_token: str | None = Field(default=None, max_length=240)
    ack: dict[str, JsonValue] | None = None
    provenance: dict[str, JsonValue] | None = None
    pipette_message_state: dict[str, JsonValue] | None = None
    ascii_command: str | None = Field(default=None, max_length=240)
    length: StrictInt | None = None
    observed_at: StrictFloat | StrictInt | None = None
    reader_generation: StrictInt | None = None
    oem_source_anchor: str | None = Field(default=None, max_length=240)


class OperatorDashboardPipetteLastError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel: Literal[0, 1, 2, 3]
    error_code: StrictInt
    source: Literal["ClassPipetteCollection.handlePipetteMessage"]


class PipetteReceiptTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    semantic_query_response_verified: StrictBool
    delivery_verified: StrictBool
    controller_acknowledged: StrictBool
    completion_verified: StrictBool
    hardware_precondition_verified: StrictBool
    hardware_postcondition_verified: StrictBool
    physical_effect_verified: Literal[False]
    physical_effect_claim_suppressed: Literal[True]


class PipetteReceiptEvidenceAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_lock_path: str = Field(min_length=1, max_length=4096)
    evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_lock_schema: Literal["bioxp.oem_evidence_lock.v4"]
    acquisition_id: str = Field(min_length=1, max_length=240)
    evidence_lock_identity_verified: Literal[True]


_PIPETTE_SOURCE_ROLES = frozenset(
    {
        "pipette_models",
        "pipette_transport",
        "pipette_receipts",
        "can_driver",
        "novo_router",
        "novo_usb_can",
        "pipette_service",
        "pipette_spec",
    }
)


class PipetteReceiptSourceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    repository_root: str = Field(min_length=1, max_length=4096)
    source_sha256: dict[str, str]
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_authority: PipetteReceiptEvidenceAuthority
    authority_verified: Literal[True]

    @field_validator("source_sha256")
    @classmethod
    def validate_source_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != _PIPETTE_SOURCE_ROLES:
            raise ValueError(
                "source_sha256 must contain exactly the producer source-role key set: "
                + ", ".join(sorted(_PIPETTE_SOURCE_ROLES))
            )
        if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in value.values()):
            raise ValueError("source_sha256 values must be lowercase SHA-256 digests")
        return value


class PipetteReceiptDeploymentIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    deployed_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    deployed_sha_verified: StrictBool
    runtime_sha_verified: StrictBool
    status: str = Field(min_length=1, max_length=240)


class PipetteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    schema_name: Literal["bioxp.pipette.receipt.v1"] = Field(alias="schema")
    receipt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: str = Field(min_length=1, max_length=120)
    operation: str = Field(min_length=1, max_length=120)
    requested_inputs: dict[str, JsonValue]
    effective_inputs: dict[str, JsonValue]
    result: dict[str, JsonValue]
    truth: PipetteReceiptTruth
    runtime_binding: dict[str, JsonValue]
    ownership_epoch: StrictInt
    source_identity: PipetteReceiptSourceIdentity
    deployment_identity: PipetteReceiptDeploymentIdentity
    response: dict[str, JsonValue] | None = None
    stage_receipts: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=256)


class OperatorDashboardPipetteChannel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    transport: Literal["novo_usb_can"]
    channel: Literal[0, 1, 2, 3]
    bitrate: StrictInt
    pipette_id: Literal[0, 1, 2, 3]
    transport_details: OperatorDashboardPipetteTransportDetails
    available: StrictBool
    initialized: StrictBool
    software_initialized: StrictBool
    tip_loaded: StrictBool
    software_tip_loaded: StrictBool
    pressure_profile: str | None = Field(default=None, max_length=120)
    top_speed: StrictFloat | StrictInt
    last_command: str | None = Field(default=None, max_length=240)
    last_transaction: dict[str, JsonValue] | None
    pipette_message_state: dict[str, JsonValue]
    oem_initialization_counter: StrictInt
    oem_diagnosis: str | None = Field(default=None, max_length=2000)
    oem_error_queue: list[StrictInt]
    oem_process_error_code: StrictInt | None
    hardware_tip_status: OperatorDashboardPipetteHardwareEvidence | None
    hardware_pressure: OperatorDashboardPipetteHardwareEvidence | None
    hardware_truth_level: str = Field(min_length=1, max_length=120)
    ack_required: Literal[True]
    delivery_verified: StrictBool
    controller_acknowledged: StrictBool | None
    completion_verified: StrictBool
    semantic_query_response_verified: StrictBool | None = None
    hardware_precondition_verified: StrictBool
    hardware_postcondition_verified: StrictBool
    state_reconciled: StrictBool
    state_reconciliation_source: str | None = Field(default=None, max_length=240)
    physical_effect_verified: Literal[False]
    response_timeout_s: StrictFloat | StrictInt
    liquid_level_ul: StrictFloat | StrictInt
    front_air_level_ul: StrictFloat | StrictInt
    rear_air_level_ul: StrictFloat | StrictInt

    @model_validator(mode="after")
    def validate_channel_identity(self):
        if self.pipette_id != self.channel:
            raise ValueError("pipette_id must equal channel")
        return self


class OperatorDashboardPipettes(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    transport: Literal["novo_usb_can"]
    channels: list[OperatorDashboardPipetteChannel] = Field(min_length=4, max_length=4)
    channel_count: Literal[4]
    group_status_spacing_ms: Literal[30]
    live_query_performed: Literal[False]
    last_group_transaction: dict[str, JsonValue] | None
    liquid_mutation_enabled: StrictBool
    tip_type: StrictInt
    tip_location: StrictInt
    allow_to_stop: StrictBool
    fluid_detection_timestamps: dict[str, StrictFloat | None]
    last_error: OperatorDashboardPipetteLastError | None
    physical_effect_verified: Literal[False]
    latest_receipt: PipetteReceipt | None = None
    application: "PipetteApplicationStatus | None" = None

    @model_validator(mode="after")
    def validate_four_channel_group(self):
        if [channel.channel for channel in self.channels] != [0, 1, 2, 3]:
            raise ValueError("pipette channels must be the ordered unique IDs [0, 1, 2, 3]")
        if set(self.fluid_detection_timestamps) != {"0", "1", "2", "3"}:
            raise ValueError("fluid_detection_timestamps must contain exactly channels 0..3")
        return self


class PipetteReadbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    include_data: StrictBool = False


class PipetteReadbackChannel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel: Literal[0, 1, 2, 3]
    semantic_ok: StrictBool
    firmware: dict[str, JsonValue]
    status: dict[str, JsonValue]
    tip: dict[str, JsonValue]
    pressure: dict[str, JsonValue] | None
    data: dict[str, JsonValue] | None


class PipetteReadbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hardware_truth_level: Literal["hardware_query"]
    ok: StrictBool
    semantic_ok: StrictBool
    available: StrictBool
    channel_count: Literal[4]
    channels_constructed_unconditionally: list[Literal[0, 1, 2, 3]] = Field(min_length=4, max_length=4)
    channels: list[PipetteReadbackChannel] = Field(min_length=4, max_length=4)
    include_data: StrictBool
    live_query_performed: Literal[True]
    truth_source: Literal["live_hardware_queries"]
    delivery_verified: Literal[False]
    controller_acknowledged: Literal[False]
    completion_verified: Literal[False]
    hardware_postcondition_verified: Literal[False]
    physical_effect_verified: Literal[False]
    oem_source_anchor: Literal["ClassPipetteCollection constructor/readback; ClassPipette QueryFirmware/Q1/?31/?57/getData"]
    receipt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    receipt_truth: PipetteReceiptTruth

    @model_validator(mode="after")
    def validate_four_channel_readback(self):
        expected = [0, 1, 2, 3]
        if self.channels_constructed_unconditionally != expected:
            raise ValueError("channels_constructed_unconditionally must be [0, 1, 2, 3]")
        if [channel.channel for channel in self.channels] != expected:
            raise ValueError("active readback channels must be the ordered unique IDs 0..3")
        if self.include_data is False and any(channel.data is not None for channel in self.channels):
            raise ValueError("channel data must be null when include_data is false")
        if self.include_data is True and any(channel.data is None for channel in self.channels):
            raise ValueError("channel data must be present when include_data is true")
        return self


class PipetteLoadTipPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["load_tip"]
    tip_tray: str = Field(min_length=1, max_length=120)
    tip_well: str = Field(min_length=1, max_length=32)
    tip_type: StrictInt
    tip_location: Literal[0, 1, 2, 3]
    home_z_after: StrictBool = True


class PipetteMoveToWastePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["move_to_waste"]


class PipetteDetectFluidPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["detect_fluid"]
    fluid_class: Literal["TC", "MS", "OC", "RC", "STRIP"]


class PipettePlungerUpPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["plunger_up"]


class PipettePlungerDownPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["plunger_down"]


PipetteApplicationPlanRequest = Annotated[
    PipetteLoadTipPlanRequest
    | PipetteMoveToWastePlanRequest
    | PipetteDetectFluidPlanRequest
    | PipettePlungerUpPlanRequest
    | PipettePlungerDownPlanRequest,
    Field(discriminator="operation"),
]


class PipetteApplicationDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    bound: StrictBool
    authority: str | None = Field(default=None, max_length=240)
    generation: StrictInt
    state: dict[str, JsonValue]
    blockers: list[str] = Field(max_length=32)


class PipetteApplicationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    mode: Literal["plan_only"]
    execution_admitted: Literal[False]
    physical_effect_verified: Literal[False]
    operations: list[Literal["load_tip", "move_to_waste", "detect_fluid", "plunger_up", "plunger_down"]] = Field(min_length=5, max_length=5)
    dependencies: dict[str, PipetteApplicationDependency] = Field(min_length=6, max_length=6)
    required_dependencies: list[str] = Field(min_length=6, max_length=6)
    missing_dependencies: list[str] = Field(max_length=6)
    dependency_blockers: list[str] = Field(max_length=64)
    dependencies_satisfied: StrictBool
    blocker: Literal["physical_pipette_execution_not_authorized"]

    @field_validator("operations")
    @classmethod
    def validate_operations(cls, value: list[str]) -> list[str]:
        if set(value) != {"load_tip", "move_to_waste", "detect_fluid", "plunger_up", "plunger_down"}:
            raise ValueError("operations must contain each supported no-motion application operation exactly once")
        return value

    @model_validator(mode="after")
    def validate_dependency_status(self):
        expected = {"deck", "gantry", "z", "pressure", "pipette", "machine_state"}
        if set(self.dependencies) != expected or set(self.required_dependencies) != expected:
            raise ValueError("application status must contain all six exact dependencies")
        if not set(self.missing_dependencies).issubset(expected):
            raise ValueError("application status has an unknown missing dependency")
        satisfied = not self.missing_dependencies and not self.dependency_blockers
        if self.dependencies_satisfied != satisfied or self.ok != satisfied:
            raise ValueError("application dependency status is internally inconsistent")
        return self


class PipetteApplicationStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: str = Field(min_length=1, max_length=240)
    mutates: StrictBool
    location_id: StrictInt | None = None
    wire_command: str | None = Field(default=None, max_length=120)
    current: StrictInt | None = None
    owner: Literal["deck", "gantry", "z", "pressure", "pipette", "machine_state"]


class PipetteApplicationPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: StrictBool
    operation: Literal["load_tip", "move_to_waste", "detect_fluid", "plunger_up", "plunger_down"]
    mode: Literal["plan_only"]
    execution_admitted: Literal[False]
    motion_commanded: Literal[False]
    liquid_mutation_commanded: Literal[False]
    controller_acknowledged: Literal[False]
    completion_verified: Literal[False]
    physical_effect_verified: Literal[False]
    state_reconciled: Literal[False]
    requested_inputs: dict[str, JsonValue]
    effective_inputs: None = None
    steps: list[PipetteApplicationStep] = Field(min_length=1, max_length=32)
    dependencies: dict[str, PipetteApplicationDependency] = Field(min_length=1, max_length=6)
    required_dependencies: list[str] = Field(min_length=1, max_length=6)
    missing_dependencies: list[str] = Field(max_length=6)
    dependency_blockers: list[str] = Field(max_length=64)
    dependencies_satisfied: StrictBool
    required_completion_evidence: list[str] = Field(max_length=32)
    constants: dict[str, JsonValue]
    oem_source_anchor: str = Field(min_length=1, max_length=1000)
    blocker: Literal["physical_pipette_execution_not_authorized", "application_dependencies_unbound"]
    receipt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    receipt_truth: PipetteReceiptTruth

    @model_validator(mode="after")
    def validate_plan_dependencies(self):
        required = set(self.required_dependencies)
        if set(self.dependencies) != required:
            raise ValueError("application plan dependency map must match required_dependencies")
        if not set(self.missing_dependencies).issubset(required):
            raise ValueError("application plan has an unknown missing dependency")
        satisfied = not self.missing_dependencies and not self.dependency_blockers
        if self.dependencies_satisfied != satisfied or self.ok != satisfied:
            raise ValueError("application plan dependency status is internally inconsistent")
        expected_blocker = (
            "physical_pipette_execution_not_authorized"
            if satisfied
            else "application_dependencies_unbound"
        )
        if self.blocker != expected_blocker:
            raise ValueError("application plan blocker does not match dependency status")
        return self


class PipetteDirectReleaseSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    commit: str | None
    tree: str | None
    mode: str | None
    root: str | None
    host_path: str | None = None
    manifest_sha256: str | None
    aggregate_sha256: str | None


class PipetteDirectReleaseBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service_unit: str | None
    unit_path: str | None
    unit_sha256: str | None
    launcher_path: str | None
    launcher_sha256: str | None
    configuration_sha256: str | None
    oem_lock_path: str | None
    oem_lock_sha256: str | None
    declared_listener: "OperatorReportListenerV1 | None"
    observed_listener: "OperatorReportListenerV1 | None"
    database_root: str | None
    systemd_invocation_id: str | None
    udocker_path: str | None = None
    udocker_sha256: str | None = None
    udocker_tree_sha256: str | None = None
    launcher_pid: StrictInt | None = None
    launcher_cgroup_sha256: str | None = None


class PipetteDirectReleaseObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pid: StrictInt
    cgroup: str | None
    cgroup_sha256: str | None
    started_at: StrictFloat | StrictInt | None
    listener: "OperatorReportListenerV1 | None"
    database_root: str | None


class PipetteDirectRuntimeReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    receipt_id: str | None
    receipt_sha256: str | None
    recorded_at: StrictFloat | StrictInt | None


class PipetteDirectReleaseIdentity(BaseModel):
    """Private producer envelope, validated then omitted, never public authority."""
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_name: Literal["bioxp.runtime.release_identity.v1"] = Field(alias="schema")
    status: Literal["verified", "unverified"]
    verified: StrictBool
    reason: str | None
    release_id: str | None
    source: PipetteDirectReleaseSource
    image: "OperatorReportReleaseImageV1"
    deployment: "OperatorReportReleaseDeploymentV1"
    binding: PipetteDirectReleaseBinding
    runtime_release_receipt: PipetteDirectRuntimeReceipt | None
    observation: PipetteDirectReleaseObservation


class PipetteDirectSourceIdentity(PipetteReceiptSourceIdentity):
    release_identity: PipetteDirectReleaseIdentity


class PipetteDirectPostMetadata(BaseModel):
    """Explicit service/replay envelope; never a science-field catch-all."""
    model_config = ConfigDict(extra="forbid", strict=True)
    source_identity: PipetteDirectSourceIdentity | None = None
    callback_session_id: str = Field(default="", max_length=160)
    command_id: str = Field(default="", max_length=160)
    pipette_operation_id: str = Field(default="", max_length=160)
    replayed: StrictBool = False
    status: str = Field(default="", max_length=120)


class PipetteReadbackPostEnvelope(PipetteReadbackResponse, PipetteDirectPostMetadata):
    semantic_query_response_verified: StrictBool

    @model_validator(mode="after")
    def validate_semantic_receipt_truth(self):
        if self.semantic_query_response_verified != self.receipt_truth.semantic_query_response_verified:
            raise ValueError("readback semantic envelope must match receipt truth")
        return self


class PipetteApplicationPlanPostEnvelope(PipetteApplicationPlanResponse, PipetteDirectPostMetadata):
    pass


class PipetteDirectPlanInputs(BaseModel):
    """Robot producer normalization, distinct from the narrower BMS POST union."""
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["load_tip", "move_to_waste", "detect_fluid", "plunger_up", "plunger_down"]
    home_z_after: StrictBool
    tip_tray: str | None = Field(default=None, max_length=120)
    tip_well: str | None = Field(default=None, max_length=32)
    tip_type: StrictInt | None = None
    tip_location: Literal[0, 1, 2, 3] | None = None
    fluid_class: Literal["TC", "MS", "OC", "RC", "STRIP"] | None = None

    @model_validator(mode="after")
    def validate_required_inputs(self):
        if self.operation == "load_tip" and any(getattr(self, name) is None for name in ("tip_tray", "tip_well", "tip_type", "tip_location")):
            raise ValueError("incomplete load-tip input binding")
        if self.operation == "detect_fluid" and self.fluid_class is None:
            raise ValueError("missing fluid class")
        return self


class PipetteDirectReadbackInputs(PipetteReadbackRequest):
    include_data: StrictBool = Field(...)


class PipetteDirectRequestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(min_length=1, max_length=160)
    pipette_operation_id: str | None = Field(min_length=1, max_length=160)
    canonical_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: str = Field(min_length=1, max_length=160)
    entrypoint_id: str = Field(min_length=1, max_length=160)
    caller_class: str = Field(min_length=1, max_length=160)
    control_class: str = Field(min_length=1, max_length=160)
    action_id: str = Field(min_length=1, max_length=240)
    command_status: str = Field(min_length=1, max_length=120)
    pipette_status: str | None = Field(min_length=1, max_length=120)
    outcome: str | None = Field(min_length=1, max_length=120)
    failure_code: str | None = Field(min_length=1, max_length=240)
    ownership_generation: StrictInt = Field(ge=0)
    connection_generation: StrictInt | None = Field(ge=0)
    requested_inputs: PipetteDirectReadbackInputs | PipetteDirectPlanInputs
    result: PipetteReadbackResponse | PipetteApplicationPlanResponse | None


class PipetteDirectRequestLookupResponse(BaseModel):
    """Historical evidence only; a lookup never permits resubmission."""
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    schema_version: Literal["bioxp.direct-liquid.lookup.v1"] = Field(alias="schema")
    request_kind: Literal["readback", "application_plan"]
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{7,199}$")
    lookup_state: Literal["unknown", "pending", "incomplete", "resolved", "conflict", "unavailable"]
    reason: Literal["identity_not_found", "nonterminal", "outcome_unresolved", "receipt_incomplete", "identity_scope_conflict", "store_unavailable", "stored_binding_invalid"] | None
    retry_forbidden: Literal[True]
    live_query_performed: Literal[False]
    record: PipetteDirectRequestRecord | None

    @model_validator(mode="after")
    def validate_lookup_binding(self):
        reasons = {
            "unknown": {"identity_not_found"}, "pending": {"nonterminal"},
            "incomplete": {"outcome_unresolved", "receipt_incomplete"}, "resolved": {None},
            "conflict": {"identity_scope_conflict"},
            "unavailable": {"store_unavailable", "stored_binding_invalid"},
        }
        if self.reason not in reasons[self.lookup_state]:
            raise ValueError("lookup state/reason mismatch")
        if self.lookup_state in {"unknown", "conflict", "unavailable"}:
            if self.record is not None:
                raise ValueError("lookup cannot disclose a record")
            return self
        r = self.record
        if r is None:
            raise ValueError("lookup requires recorded identity")
        readback = self.request_kind == "readback"
        if readback:
            if not isinstance(r.requested_inputs, PipetteReadbackRequest):
                raise ValueError("wrong readback request binding")
            family = ("live_readback", "direct.liquid.readback", "direct_api", "hardware_query")
            if r.result is not None and (not isinstance(r.result, PipetteReadbackResponse) or r.result.include_data != r.requested_inputs.include_data):
                raise ValueError("wrong original readback result")
        else:
            if not isinstance(r.requested_inputs, PipetteDirectPlanInputs):
                raise ValueError("wrong plan request binding")
            family = ("application_plan:" + r.requested_inputs.operation, "legacy.record", "legacy", "pipette_state_command")
            if r.result is not None and (not isinstance(r.result, PipetteApplicationPlanResponse) or r.result.operation != r.requested_inputs.operation):
                raise ValueError("wrong original plan result")
        if (r.operation, r.entrypoint_id, r.caller_class, r.control_class) != family or r.action_id != "pipette." + r.operation:
            raise ValueError("wrong direct-liquid producer")
        if self.lookup_state != "resolved" and r.result is not None:
            raise ValueError("nonterminal lookup cannot associate result")
        # receipt_id is independently allocated; it is NOT the child row ID.
        pending = {"reserved", "queued", "admitted", "dispatched", "acknowledged", "executing", "running", "blocked"}
        terminal = {"completed", "observed", "failed", "rejected", "cleared", "cancelled"}
        if (r.pipette_operation_id is None) != (r.pipette_status is None):
            raise ValueError("missing child identity/status mismatch")
        if self.lookup_state == "pending":
            if r.command_status not in pending or r.pipette_status not in pending:
                raise ValueError("pending lookup requires nonterminal rows")
        if self.lookup_state == "resolved":
            if r.outcome is None:
                raise ValueError("resolved lookup requires a recorded outcome")
            if r.command_status not in terminal or r.command_status != r.pipette_status:
                raise ValueError("resolved lookup requires consistent terminal rows")
            if r.command_status in {"completed", "observed"} and r.result is None:
                raise ValueError("successful terminal lookup requires complete receipt")
        if self.lookup_state == "incomplete" and self.reason == "receipt_incomplete":
            if r.pipette_operation_id is not None and not (r.command_status in terminal and r.command_status == r.pipette_status):
                raise ValueError("receipt incomplete requires terminal rows or a missing child")
        return self


class OperatorDashboardZTerminalState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    authority: str | None = Field(default=None, max_length=160)
    position_steps: StrictInt | None = None
    speed_steps_s: StrictInt | None = None
    left_switch_state: StrictInt | None = None
    right_switch_state: StrictInt | None = None
    left_switch_disabled: StrictBool | None = None
    right_switch_disabled: StrictBool | None = None
    switch_mask_tuple: dict[str, StrictInt | None] | None = None
    switch_mask_policy: Literal["observed_only_oem_source_omits_z_writes"] | None = None
    source_command_id: str | None = Field(default=None, max_length=160)
    observed_at: StrictFloat | StrictInt | None = None


class OperatorDashboardZProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: str | None = Field(default=None, max_length=160)
    state: str | None = Field(default=None, max_length=80)
    generation: NonnegativeStrictInt | None = None
    board_lifecycle_generation: NonnegativeStrictInt | None = None
    prepared_receipt: JsonValue | None = None
    active_receipt: JsonValue | None = None
    awaiting_observation_receipt_id: str | None = Field(default=None, max_length=160)
    reference_state: str | None = Field(default=None, max_length=80)
    terminal_state: OperatorDashboardZTerminalState | None = None
    last_failure: JsonValue | None = None
    receipt_storage: Literal["robot_sqlite"] | None = None
    receipt_detail_on_request: StrictBool | None = None
    recent_receipt_count: NonnegativeStrictInt | None = None
    latest_receipt: JsonValue | None = None
    last_observation: JsonValue | None = None
    available: StrictBool | None = None
    ownership_generation: NonnegativeStrictInt | None = None
    current_board_lifecycle_generation: NonnegativeStrictInt | None = None
    board_lifecycle_generation_fresh: StrictBool | None = None
    authority: str | None = Field(default=None, max_length=160)
    board: Literal[4] | None = None
    motor: Literal[1] | None = None
    coordinate_contract: Literal["oem_source_nonnegative_z"] | None = None
    source_min_steps: Literal[0] | None = None
    source_max_steps: Literal[160000] | None = None
    blockers: list[str] = Field(default_factory=list, max_length=64)
    failure: str | None = Field(default=None, max_length=1000)
    bound: StrictBool
    expected_startup_stage: str | None = Field(default=None, max_length=160)
    startup_terminal_state: str | None = Field(default=None, max_length=80)
    switch_mask_policy: Literal["observed_only_oem_source_omits_z_writes"] | None = None
    switch_mask_tuple: dict[str, StrictInt | None] | None = None

    @model_validator(mode="after")
    def bind_observed_switch_registers(self):
        if not self.bound:
            return self
        if self.switch_mask_policy != "observed_only_oem_source_omits_z_writes":
            raise ValueError("bound Z provider must declare observed-only switch-register policy")
        if self.switch_mask_tuple is not None and set(self.switch_mask_tuple) != {"12", "13"}:
            raise ValueError("bound Z provider switch-register observation is incomplete")
        return self


class OperatorDashboardZAxis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: OperatorDashboardAxis | None
    provider: OperatorDashboardZProvider
    snapshot_freshness: dict[str, JsonValue]
    last_failure: JsonValue | None
    authority: str = Field(min_length=1, max_length=160)


class OperatorDashboardQueueAxis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    active_command_id: str | None = Field(default=None, max_length=160)
    depth: StrictInt = Field(ge=0)
    head_action_id: str | None = Field(default=None, max_length=128)
    state: Literal["running", "queued", "idle"]


class OperatorDashboard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["bioxp.operator_dashboard.v1"]
    ownership_generation: StrictInt = Field(ge=0)
    connection: dict[str, Any]
    motion: dict[str, Any]
    operation: dict[str, Any]
    enclosure: dict[str, Any]
    axes: list[OperatorDashboardAxis] = Field(max_length=16)
    x_axis: OperatorDashboardXAxis
    z_axis: OperatorDashboardZAxis
    temperatures: list[OperatorDashboardTemperature] = Field(max_length=32)
    pipettes: OperatorDashboardPipettes
    snapshot: dict[str, Any]
    successive_move_queue: dict[str, OperatorDashboardQueueAxis] = Field(
        default_factory=dict, max_length=8
    )
    command_queue: dict[str, Any] = Field(default_factory=dict)


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
    aggregate_abort: Literal[True] | None = None
    physical_scope: Literal["aggregate_oem_all_present_boards"] | None = None
    x_only: Literal[False] | None = None

    @model_validator(mode="after")
    def bind_aggregate_abort_scope(self):
        aggregate_fields = {"aggregate_abort", "physical_scope", "x_only"}
        present = aggregate_fields.intersection(self.model_fields_set)
        if self.action_id == "oem.abort_all":
            if present != aggregate_fields:
                raise ValueError("aggregate OEM abort requires complete machine-scope metadata")
        elif present:
            raise ValueError("aggregate OEM abort metadata is forbidden on non-aggregate actions")
        return self


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
    actions: list[OperatorActionSpec] = Field(max_length=512)

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
    transport_exchanges: list[OperatorTransportExchangeV2] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    transport_retention_errors: list[OperatorTransportRetentionErrorV2] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @field_validator("transport_exchanges", "transport_retention_errors", mode="before")
    @classmethod
    def reject_null_transport_evidence(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("present transport evidence must be an array")
        return value
    schema_version: Literal["bioxp.operator_action_receipt.v1"]
    command_id: str = Field(min_length=1, max_length=128)
    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    kind: ActionKind
    safety_class: ActionSafety
    status: ActionStatus
    idempotency_key: str = Field(min_length=8, max_length=128)
    idempotency_replay_enabled: StrictBool = True
    ownership_generation: StrictInt = Field(ge=0)
    started_at: str = Field(min_length=1, max_length=80)
    finished_at: str | None = Field(default=None, max_length=80)
    duration_ms: StrictInt | StrictFloat | None = Field(default=None, ge=0)
    request_received_at: StrictFloat | None = Field(default=None, ge=0)
    lock_acquired_at: StrictFloat | None = Field(default=None, ge=0)
    admission_completed_at: StrictFloat | None = Field(default=None, ge=0)
    queued_at: StrictFloat | None = Field(default=None, ge=0)
    dispatched_at: StrictFloat | None = Field(default=None, ge=0)
    cleared_at: StrictFloat | None = Field(default=None, ge=0)
    provider_entry_at: StrictFloat | None = Field(default=None, ge=0)
    provider_returned_at: StrictFloat | None = Field(default=None, ge=0)
    receipt_persist_started_at: StrictFloat | None = Field(default=None, ge=0)
    remote_acknowledged: StrictBool
    controller_acknowledged: StrictBool = False
    controller_terminal_state_verified: StrictBool = False
    physical_effect_verified: StrictBool
    automatic_retry: StrictBool | None = None
    physical_outcome: str | None = Field(default=None, max_length=80)
    persistence_fallback: dict[str, Any] | None = None
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
    response: dict[str, JsonValue] | None = None
    authority_receipt_id: str | None = Field(default=None, max_length=128)
    authority_receipt_status: ActionStatus | OperatorDashboardXOmissionMarker | None = None
    authority_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    observation_receipt_id: str | None = Field(default=None, max_length=128)
    observes_command_id: str | None = Field(default=None, max_length=128)
    error: str | None = Field(default=None, max_length=4000)
    stage_receipts: list[dict[str, Any]] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def bind_serial206_x_authority(self):
        legacy_response_body = self.response.get("body") if self.response is not None else None
        bounded_completed_x_home_summary = (
            self.action_id == "oem.x.manual_panel_home"
            and self.status == "completed"
            and self.controller_acknowledged
            and self.authority_receipt_id == self.command_id
            and isinstance(self.authority_receipt_status, OperatorDashboardXOmissionMarker)
            and self.authority_fingerprint is not None
            and isinstance(legacy_response_body, dict)
            and legacy_response_body.get("ok") is True
            and legacy_response_body.get("state") == "awaiting_operator_observation"
        )
        if bounded_completed_x_home_summary:
            self.authority_receipt_status = "completed"
        legacy_serial206_x_action = (
            self.action_id.startswith("oem.x.")
            or self.action_id.startswith("oem.xy.")
            or self.action_id.startswith("oem.xyz.")
            or self.action_id == "oem.abort_all"
        )
        if (
            legacy_serial206_x_action
            and self.status == "completed"
            and self.controller_acknowledged
            and self.authority_receipt_id is None
            and self.authority_receipt_status is None
            and self.authority_fingerprint is not None
            and isinstance(legacy_response_body, dict)
            and (
                isinstance(legacy_response_body.get("intent"), str)
                or (
                    self.action_id == "oem.x.manual_panel_home"
                    and legacy_response_body.get("ok") is True
                    and legacy_response_body.get("state") == "awaiting_operator_observation"
                )
            )
        ):
            self.authority_receipt_id = self.command_id
            self.authority_receipt_status = "completed"
        if (self.authority_receipt_id is None) != (self.authority_receipt_status is None):
            raise ValueError("authority receipt identity and status must be present together")
        if self.controller_terminal_state_verified and not self.controller_acknowledged:
            raise ValueError("terminal controller evidence requires controller acknowledgment")
        serial206_x_action = (
            self.action_id.startswith("oem.x.")
            or self.action_id.startswith("oem.xy.")
            or self.action_id.startswith("oem.xyz.")
            or self.action_id == "oem.abort_all"
        )
        failed_response_detail = (
            legacy_response_body.get("detail")
            if isinstance(legacy_response_body, dict)
            else None
        )
        failed_automatic_prerequisite = bool(
            self.status in {"failed", "rejected"}
            and self.action_id in {"oem.x.move_steps", "oem.x.move_absolute"}
            and self.authority_receipt_id is None
            and self.authority_receipt_status is None
            and isinstance(failed_response_detail, dict)
            and failed_response_detail.get("ok") is False
            and failed_response_detail.get("requested_motion_dispatched") is False
        )
        if (
            serial206_x_action
            and (self.controller_acknowledged or self.controller_terminal_state_verified)
            and self.authority_receipt_id is None
            and self.safety_class != "read_only"
            and not failed_automatic_prerequisite
        ):
            raise ValueError("serial-206 X controller evidence requires an authority receipt identity")

        expected_x_intent = {
            "oem.x.prepare": "prepare",
            "oem.x.reconcile_switch_masks": "reconcile_switch_masks",
            "oem.x.move_steps": "move_steps",
            "oem.x.move_absolute": "move_absolute",
            "oem.x.manual_panel_home": "manual_panel_home",
            "oem.x.diagnostic_home_axis": "home_axis",
            "oem.x.startup_home": "startup_home",
            "oem.x.move_to_origin_home": "move_to_origin_home",
            "oem.x.caught_plate_recovery_home": "caught_plate_recovery_home",
            "oem.xyz.move_to": "move_to",
            "oem.x.set_home": "set_home",
            "oem.x.set_max_speed": "set_max_speed",
            "oem.x.set_max_acc": "set_max_acc",
            "oem.x.restore_original_speed": "restore_original_speed",
            "oem.x.set_stall_guard": "set_stall_guard",
            "oem.x.stop": "stop",
            "oem.abort_all": "abort",
            "oem.xy.home_xy": "home_xy",
            "oem.xy.move_xy": "move_xy",
            "oem.xy.enable": "enable_xy_current",
            "oem.xyz.enable": "enable_xyz_current",
        }.get(self.action_id)
        response_payload: dict[str, JsonValue] | None = self.response
        response_body = self.response.get("body") if self.response is not None else None
        if isinstance(response_body, dict):
            response_payload = response_body
        if expected_x_intent is not None:
            response_intent = response_payload.get("intent") if response_payload is not None else None
            legacy_completed_x_summary = (
                self.status == "completed"
                and response_intent is None
                and isinstance(response_payload, dict)
                and response_payload.get("ok") is True
                and self.authority_fingerprint is not None
                and self.authority_receipt_id == self.command_id
                and self.authority_receipt_status == "completed"
            )
            if (
                self.status == "completed"
                and response_intent != expected_x_intent
                and not legacy_completed_x_summary
            ):
                raise ValueError("completed serial-206 X response intent does not match the action identity")
            if self.status == "failed" and response_intent is not None and response_intent != expected_x_intent:
                raise ValueError("failed serial-206 X response carries a contradictory action intent")
        if self.action_id == "oem.x.observe":
            observation = response_payload.get("observation") if response_payload is not None else None
            observation_reference = (
                observation.get("reference_persistence")
                if isinstance(observation, dict)
                else None
            )
            bounded_completed_observation = (
                self.status == "completed"
                and isinstance(response_payload, dict)
                and response_payload.get("ok") is True
                and response_payload.get("state") == "referenced_ready"
                and isinstance(observation, dict)
                and observation.get("command_id") == self.command_id
                and observation.get("status") == "completed"
                and isinstance(observation_reference, dict)
                and observation_reference.get("ok") is True
                and observation_reference.get("state") == "referenced"
                and self.observation_receipt_id == self.command_id
                and isinstance(self.observes_command_id, str)
                and self.inputs.get("command_id") == self.observes_command_id
                and self.inputs.get("verdict") == "pass"
                and self.inputs.get("physical_motion_observed") is True
                and self.inputs.get("expected_direction_observed") is True
                and self.inputs.get("home_endpoint_observed") is True
                and self.inputs.get("stopped_observed") is True
                and self.authority_fingerprint is not None
            )
            if self.status == "completed" and (
                not isinstance(observation, dict)
                or (
                    observation.get("intent") != "observation"
                    and not bounded_completed_observation
                )
            ):
                raise ValueError("completed serial-206 X observation response lacks its bound observation intent")
            if self.status == "failed" and isinstance(observation, dict) and observation.get("intent") != "observation":
                raise ValueError("failed serial-206 X observation carries a contradictory intent")
        return self


class OperatorLegacyReconciliationReceipt(BaseModel):
    """Known schema-2 command row retained by the v1 history projection."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: str = Field(min_length=1, max_length=160)
    automatic_retry: Literal[False]
    callback_session_id: str | None = Field(default=None, max_length=160)
    caller_class: str = Field(min_length=1, max_length=120)
    command_id: str = Field(min_length=1, max_length=160)
    connection_generation: StrictInt | None = Field(default=None, ge=0)
    control_class: str = Field(min_length=1, max_length=120)
    duration_ms: StrictFloat | StrictInt = Field(ge=0)
    entrypoint_id: str = Field(min_length=1, max_length=160)
    finished_at: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=1, max_length=256)
    idempotency_replay_enabled: StrictBool
    lifecycle_stage_id: str | None = Field(default=None, max_length=160)
    operation: str = Field(min_length=1, max_length=160)
    ownership_generation: StrictInt = Field(ge=0)
    physical_outcome: Literal["ambiguous"]
    protocol_action_id: str | None = Field(default=None, max_length=160)
    protocol_job_id: str | None = Field(default=None, max_length=160)
    requested_inputs: dict[str, JsonValue]
    response: dict[str, JsonValue]
    stage_receipts: list[dict[str, JsonValue]]
    status: Literal["reconciliation_required"]


class OperatorRestartReconciliationEvidence(BaseModel):
    """Exact restart evidence attached to schema-5 outcome-unknown rows."""

    model_config = ConfigDict(extra="forbid", strict=True)

    authority: Literal["restart_reconciliation"]
    automatic_retry: Literal[False]
    prior_command_status: Literal["reconciliation_required"]
    prior_pipette_status: None
    reason: Literal["process_restart_with_nonterminal_projection"]


class OperatorMigratedOutcomeUnknownReceipt(BaseModel):
    """Schema-2 command history preserved by the schema-5 v1 projection."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: str = Field(min_length=1, max_length=160)
    automatic_retry: Literal[False]
    callback_session_id: None
    caller_class: str = Field(min_length=1, max_length=120)
    command_id: str = Field(min_length=1, max_length=160)
    completion_verified: Literal[False]
    connection_generation: None
    control_class: str = Field(min_length=1, max_length=120)
    controller_acknowledged: Literal[False]
    delivery_verified: Literal[False]
    duration_ms: StrictFloat | StrictInt = Field(ge=0)
    entrypoint_id: str = Field(min_length=1, max_length=160)
    failure_code: Literal["startup_reconciliation"]
    finished_at: str = Field(min_length=1, max_length=80)
    hardware_postcondition_verified: Literal[False]
    hardware_precondition_verified: Literal[False]
    idempotency_key: str = Field(min_length=1, max_length=256)
    idempotency_replay_enabled: Literal[True]
    lifecycle_stage_id: None
    operation: str = Field(min_length=1, max_length=160)
    outcome: Literal["outcome_unknown"]
    ownership_generation: StrictInt = Field(ge=0)
    physical_effect_verified: Literal[False]
    physical_outcome: Literal["ambiguous"]
    protocol_action_id: None
    protocol_job_id: None
    reconciliation: OperatorRestartReconciliationEvidence
    requested_inputs: dict[str, JsonValue]
    response: dict[str, JsonValue]
    semantic_query_response_verified: Literal[False]
    stage_receipts: list[dict[str, JsonValue]]
    status: Literal["outcome_unknown"]


class OperatorLegacyPipetteReceiptTruth(BaseModel):
    """Recorded truth before semantic-query evidence was added to v1.

    The absent field stays absent, not false/null or inferred from outcome.
    This closed shape is accepted only by the read-only history projection;
    current mutation receipts still require PipetteReceiptTruth in full.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    delivery_verified: StrictBool
    controller_acknowledged: StrictBool
    completion_verified: StrictBool
    hardware_precondition_verified: StrictBool
    hardware_postcondition_verified: StrictBool
    physical_effect_verified: Literal[False]
    physical_effect_claim_suppressed: Literal[True]


class OperatorLegacyHistoryPipetteReceipt(BaseModel):
    """Closed pre-semantic-query indexed receipt, for history reads only."""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    schema_name: Literal["bioxp.pipette.receipt.v1"] = Field(alias="schema")
    receipt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: str = Field(min_length=1, max_length=120)
    operation: str = Field(min_length=1, max_length=120)
    requested_inputs: dict[str, JsonValue]
    effective_inputs: dict[str, JsonValue]
    result: dict[str, JsonValue]
    truth: OperatorLegacyPipetteReceiptTruth
    runtime_binding: dict[str, JsonValue]
    ownership_epoch: StrictInt
    source_identity: PipetteReceiptSourceIdentity
    deployment_identity: PipetteReceiptDeploymentIdentity
    response: dict[str, JsonValue]
    stage_receipts: list[dict[str, JsonValue]] = Field(max_length=256)
    status: str = Field(min_length=1, max_length=80)
    outcome: str = Field(min_length=1, max_length=2000)
    controller_acknowledged: StrictBool
    physical_effect_verified: Literal[False]


class OperatorHistoryPipetteReceipt(PipetteReceipt):
    """Indexed pipette receipt with schema-5 history projection fields."""

    status: str = Field(min_length=1, max_length=80)
    outcome: str = Field(min_length=1, max_length=2000)
    controller_acknowledged: StrictBool
    physical_effect_verified: Literal[False]


class OperatorLegacyPipetteRuntimeBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    callback_session_id: str = Field(min_length=1, max_length=160)
    caller_class: str = Field(min_length=1, max_length=120)
    control_class: str = Field(min_length=1, max_length=120)
    entrypoint_id: str = Field(min_length=1, max_length=160)
    lifecycle_stage_id: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=160)
    transport_owner_bound: Literal[True]


class OperatorLegacyUnindexedPipetteReceipt(BaseModel):
    """Known pre-index pipette failure retained without inventing an ID."""

    model_config = ConfigDict(extra="forbid", strict=True)

    error: str = Field(min_length=1, max_length=4000)
    failure_code: str = Field(min_length=1, max_length=2000)
    ok: Literal[False]
    outcome: str = Field(min_length=1, max_length=2000)
    response: dict[str, JsonValue]
    runtime_binding: OperatorLegacyPipetteRuntimeBinding
    stage_receipts: list[dict[str, JsonValue]]
    status: Literal["failed"]
    controller_acknowledged: Literal[False]
    physical_effect_verified: Literal[False]


class OperatorHistorySourceIdentity(BaseModel):
    """Producer-recorded identity, not current runtime authority."""

    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_lock_identity_verified: StrictBool
    evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_id: str = Field(min_length=1, max_length=128)
    release_verified: StrictBool
    robot_identity: str = Field(min_length=1, max_length=128)
    source_aggregate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OperatorRecordedActionReceipt(OperatorActionReceipt):
    """Legacy ledger evidence extension; never used for mutation receipts.

    Require the complete observed extension rather than defaulting missing
    evidence, dropping producer fields, or deriving authority from status.
    """

    completion_ambiguous: StrictBool
    completion_verified: StrictBool
    delivery_verified: StrictBool
    hardware_postcondition_verified: StrictBool
    hardware_precondition_verified: StrictBool
    reconciliation_required: StrictBool
    retry_forbidden: StrictBool
    source_identity: OperatorHistorySourceIdentity


class OperatorActionHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["bioxp.operator_action_history.v1"]
    receipts: list[
        OperatorActionReceipt
        | OperatorRecordedActionReceipt
        | PipetteReceipt
        | OperatorHistoryPipetteReceipt
        | OperatorLegacyHistoryPipetteReceipt
        | OperatorLegacyReconciliationReceipt
        | OperatorMigratedOutcomeUnknownReceipt
        | OperatorLegacyUnindexedPipetteReceipt
    ] = Field(max_length=500)


# Serial-206 operator wire contracts. Keep these separate from the strict v1
# models above. The robot and BMS cut over together; v1 callers never receive
# a widened model.
CommandStatusV2 = Literal[
    "queued",
    "dispatched",
    "issued_pending",
    "interrupting",
    "completed",
    "failed",
    "cleared",
    "interrupted",
    "ambiguous",
    "rejected",
    "stopped",
    "aborted",
    "cancelled",
]
_NONTERMINAL_V2 = frozenset({"queued", "dispatched", "issued_pending", "interrupting"})
Board4StateV2 = Literal["unknown", "inactive", "transitioning", "active", "faulted"]
YAxisLifecycleStateV2 = Literal[
    "unbound",
    "unprepared",
    "prepared_unreferenced",
    "referenced_ready",
    "generation_stale",
    "reconciliation_required",
    "faulted",
]
YAxisReferenceStateV2 = Literal[
    "unreferenced",
    "referenced",
    "generation_stale",
    "reconciliation_required",
]
MethodStatusV1 = Literal[
    "queued",
    "active",
    "pause_requested",
    "paused",
    "cancel_requested",
    "stopping",
    "aborting",
    "completed",
    "completed_partial",
    "failed",
    "cleared",
    "interrupted",
    "ambiguous",
]
NonnegativeStrictInt = Annotated[StrictInt, Field(ge=0)]
SignedInt32 = Annotated[StrictInt, Field(ge=-(2**31), le=2**31 - 1)]
YRelativeStepsV2 = SignedInt32
ReceiptScalarV2 = StrictInt | StrictFloat | StrictBool | str | None


class OperatorYMoveStepsInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    steps: StrictInt = Field(ge=-2_147_483_648, le=2_147_483_647)


class OperatorYMoveAbsoluteInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_steps: StrictInt = Field(ge=-2_147_483_648, le=2_147_483_647)


class OperatorEmptyInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OperatorMoveStepsInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    steps: SignedInt32


class OperatorMoveAbsoluteInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    position_steps: SignedInt32


class OperatorMoveXYInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    x: SignedInt32
    y: SignedInt32


class OperatorDeckMoveInputsV1(BaseModel):
    """Finite semantic deck intent; the robot remains coordinate authority."""

    model_config = ConfigDict(extra="forbid", strict=True)
    target: str = Field(min_length=1, max_length=160, pattern=r"^[A-Z0-9][A-Z0-9_]*$")
    camera_offset: StrictBool


OperatorNormalInputsV2 = (
    OperatorYMoveStepsInputsV2
    | OperatorYMoveAbsoluteInputsV2
    | OperatorEmptyInputsV2
    | OperatorMoveStepsInputsV2
    | OperatorMoveAbsoluteInputsV2
    | OperatorMoveXYInputsV2
)


def _canonical_board_epoch_map(value: dict[str, int]) -> dict[str, int]:
    if any(not key.isdecimal() or str(int(key)) != key for key in value):
        raise ValueError("board epoch keys must be canonical nonnegative decimal board IDs")
    return value


class OperatorActionRequestV2(BaseModel):
    """BMS-local connection fence plus the exact normal robot v2 body."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    schema_version: Literal["bioxp.operator_action_request.v2"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_ownership_generation: NonnegativeStrictInt
    expected_board_epoch_by_board: dict[str, NonnegativeStrictInt]
    inputs: dict[str, JsonValue] = Field(default_factory=dict, max_length=16)

    @field_validator("idempotency_key")
    @classmethod
    def bounded_idempotency_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("idempotency_key must be at most 128 bytes")
        return value

    @field_validator("inputs")
    @classmethod
    def bounded_wire_scalars(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if "steps" in value:
            steps = value["steps"]
            if type(steps) is not int or not (-(2**31) <= steps <= 2**31 - 1):
                raise ValueError("steps must be a signed int32 integer")
        return value

    @field_validator("expected_board_epoch_by_board")
    @classmethod
    def canonical_board_epoch_keys(cls, value: dict[str, int]) -> dict[str, int]:
        return _canonical_board_epoch_map(value)


class OperatorInterruptRequestV1(BaseModel):
    """BMS connection fence plus the durable robot STOP idempotency key."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    schema_version: Literal["bioxp.operator_interrupt_request.v1"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)
    observed_ownership_generation: NonnegativeStrictInt | None
    observed_board_epoch_by_board: dict[str, NonnegativeStrictInt]

    @field_validator("idempotency_key")
    @classmethod
    def bounded_interrupt_idempotency_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("idempotency_key must be at most 128 bytes")
        return value

    @field_validator("reason")
    @classmethod
    def bounded_reason_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 500:
            raise ValueError("reason must be at most 500 bytes")
        return value

    @field_validator("observed_board_epoch_by_board")
    @classmethod
    def canonical_observed_board_epoch_keys(cls, value: dict[str, int]) -> dict[str, int]:
        return _canonical_board_epoch_map(value)


class OperatorInterruptEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_id: str = Field(min_length=1, max_length=500)
    evidence_kind: Literal["controller_response"]
    content_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    payload_bytes: StrictInt = Field(ge=0)


class OperatorInterruptReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["bioxp.operator_interrupt_receipt.v1"]
    robot_identity: str = Field(min_length=1, max_length=160)
    ownership_generation: NonnegativeStrictInt
    observed_ownership_generation: NonnegativeStrictInt | None
    observed_board_epoch_by_board: dict[str, NonnegativeStrictInt]
    interrupt_attempt_id: str = Field(min_length=1, max_length=160)
    interrupt_id: str = Field(min_length=1, max_length=160)
    action_id: Literal["oem.x.stop", "oem.y.stop", "oem.z.stop", "oem.z.abort", "oem.abort_all"]
    scope: Literal["x", "y", "z", "aggregate"]
    cutoff: NonnegativeStrictInt | None = None
    active_command_id: str | None = Field(default=None, max_length=160)
    active_command_ids: list[str] = Field(default_factory=list, max_length=10_000)
    global_safety_epoch: NonnegativeStrictInt | None = None
    x_safety_epoch: NonnegativeStrictInt | None = None
    y_safety_epoch: NonnegativeStrictInt | None = None
    z_safety_epoch: NonnegativeStrictInt | None = None
    oem_abort_latched: StrictBool
    controller_stop_attempted: Literal[True]
    source_call_completed: StrictBool
    source_return_ok: StrictBool
    controller_stop_acknowledged: StrictBool
    controller_stop_delivered: StrictBool | None = None
    controller_response: JsonValue
    controller_response_evidence: OperatorInterruptEvidenceV1 | None = None
    first_stop_ack: JsonValue | None = None
    second_stop_ack: JsonValue | None = None
    terminal_speed_evidence: JsonValue | None = None
    authority_snapshot: JsonValue | None = None
    error: str | None = Field(default=None, max_length=1000)
    physical_effect_verified: Literal[False]
    persistence_state: Literal["committed", "recovery_required"]
    recovery_hold: StrictBool
    transition_sequence: NonnegativeStrictInt | None = None
    terminal_transition_sequences: list[NonnegativeStrictInt] = Field(default_factory=list, max_length=10_000)
    idempotent_replay: StrictBool = False

    @field_validator("observed_board_epoch_by_board")
    @classmethod
    def canonical_observed_board_epoch_keys(cls, value: dict[str, int]) -> dict[str, int]:
        return _canonical_board_epoch_map(value)

    @model_validator(mode="after")
    def bind_interrupt_semantics(self):
        expected_scope = {
            "oem.x.stop": "x",
            "oem.y.stop": "y",
            "oem.z.stop": "z",
            "oem.z.abort": "aggregate",
            "oem.abort_all": "aggregate",
        }[self.action_id]
        if self.scope != expected_scope:
            raise ValueError("interrupt scope does not match action")
        if self.active_command_id is not None and self.active_command_id not in self.active_command_ids:
            raise ValueError("active interrupt command must be present in active_command_ids")
        if self.source_call_completed and self.source_return_ok and self.persistence_state == "committed" and self.error is not None:
            raise ValueError("successful OEM STOP source return cannot carry an error")
        if self.source_call_completed and not self.source_return_ok and not self.error:
            raise ValueError("failed OEM STOP source return requires an error")
        if not self.source_call_completed and self.source_return_ok:
            raise ValueError("incomplete OEM STOP source call cannot report a successful source return")
        if not self.source_call_completed and not self.error:
            raise ValueError("incomplete OEM STOP source call requires an error")
        if self.persistence_state == "committed":
            if self.recovery_hold:
                raise ValueError("committed STOP cannot require recovery")
            if None in (self.cutoff, self.global_safety_epoch, self.x_safety_epoch, self.y_safety_epoch, self.z_safety_epoch):
                raise ValueError("committed STOP requires persisted cutoff and safety epochs")
        elif not self.recovery_hold:
            raise ValueError("non-committed STOP requires recovery hold")
        return self


class OperatorXYMoveAbsoluteInputsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    x_steps: SignedInt32
    y_steps: SignedInt32


class OperatorMethodRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    schema_version: Literal["bioxp.operator_method_request.v1"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    method_action_id: Literal["oem.xy.move_absolute", "oem.xy.home"]
    expected_ownership_generation: NonnegativeStrictInt
    expected_board_epoch_by_board: dict[str, NonnegativeStrictInt]
    inputs: OperatorXYMoveAbsoluteInputsV1 | OperatorEmptyInputsV2

    @field_validator("idempotency_key")
    @classmethod
    def bounded_method_idempotency_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("idempotency_key must be at most 128 bytes")
        return value

    @field_validator("expected_board_epoch_by_board")
    @classmethod
    def canonical_method_board_epoch_keys(cls, value: dict[str, int]) -> dict[str, int]:
        return _canonical_board_epoch_map(value)

    @model_validator(mode="after")
    def bind_inputs_to_method(self):
        expected_type = (
            OperatorXYMoveAbsoluteInputsV1
            if self.method_action_id == "oem.xy.move_absolute"
            else OperatorEmptyInputsV2
        )
        if not isinstance(self.inputs, expected_type):
            raise ValueError("method inputs do not match method_action_id")
        return self


class OperatorReceiptFailureDetailV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider_failure: str = Field(min_length=1, max_length=160)
    failure: str = Field(min_length=1, max_length=160)
    axis: str = Field(min_length=1, max_length=16)
    board: StrictInt = Field(ge=0, le=255)
    motor: StrictInt = Field(ge=0, le=255)
    source_return_code: StrictInt = Field(ge=-(2 ** 31), le=2 ** 31 - 1)
    controller_acknowledged: StrictBool
    controller_terminal_state_verified: StrictBool
    physical_effect_verified: StrictBool
    lifecycle_state: str = Field(min_length=1, max_length=160)
    reference_state: str = Field(min_length=1, max_length=160)


class OperatorReceiptErrorV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=1000)
    retryable: StrictBool
    detail: OperatorReceiptFailureDetailV2 | None = None


class OperatorTransportExceptionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, serialize_by_alias=True)

    exception_class: str = Field(alias="class", min_length=1)
    message: str = Field(max_length=512)


class OperatorTransportRetentionErrorV2(OperatorTransportExceptionV2):
    stage: str = Field(min_length=1)


class OperatorTransportExchangeV2(BaseModel):
    """Caller-local F06 evidence; grants no delivery or physical authority."""

    model_config = ConfigDict(extra="forbid", strict=True)

    exchange_id: str = Field(min_length=1)
    transaction_id: str = Field(min_length=1)
    attempt_ordinal: StrictInt = Field(ge=1)
    response_attempt_attribution: Literal["single_write", "same_call_ambiguous"]
    owner_generation: StrictInt | None
    matcher: str | None
    registration_timestamp: StrictFloat | None
    tx_timestamp: StrictFloat | None
    tx_write_completed_at: StrictFloat | None
    timeout_ms: StrictInt | None
    tx_raw: list[Annotated[StrictInt, Field(ge=0, le=255)]] | None
    command_family: Literal["tmcl"]
    tx_id: StrictInt | None
    tx_dlc: StrictInt | None
    expected_board: StrictInt | None
    expected_command: StrictInt | None
    write_attempted: StrictBool
    write_returned: StrictBool
    wait_signaled: StrictBool
    response_present: StrictBool
    observed_status: StrictInt | None
    observed_rx_raw: list[Annotated[StrictInt, Field(ge=0, le=255)]] | None
    observed_rx_id: StrictInt | None
    observed_rx_dlc: StrictInt | None
    receive_timestamp: StrictFloat | None
    receive_sequence: StrictInt | None
    outcome: str
    router_outcome: str
    exception: OperatorTransportExceptionV2 | None
    finalized_at: StrictFloat
    physical_effect_verified: StrictBool
    command_id: str | None
    trace_id: str = Field(min_length=1)
    transport_call_ordinal: StrictInt = Field(ge=1)
    durable_ownership_claimed: StrictBool

    @field_validator(
        "registration_timestamp", "tx_timestamp", "tx_write_completed_at",
        "receive_timestamp", "finalized_at",
    )
    @classmethod
    def finite_transport_times(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("transport evidence times must be finite")
        return value

    @field_validator("physical_effect_verified", "durable_ownership_claimed")
    @classmethod
    def evidence_has_no_authority(cls, value: bool) -> bool:
        if value:
            raise ValueError("caller-local transport evidence cannot confer authority")
        return value


class OperatorActionReceiptV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["bioxp.operator_action_receipt.v2"]
    command_id: str = Field(min_length=1, max_length=160)
    action_id: str = Field(min_length=1, max_length=160)
    status: CommandStatusV2
    terminal: StrictBool
    sequence: StrictInt = Field(ge=1)
    method_id: str | None
    ownership_generation: StrictInt = Field(ge=0)
    expected_board_epoch_by_board: dict[str, NonnegativeStrictInt]
    state_version: StrictInt = Field(ge=1)
    status_path: str = Field(min_length=1, max_length=400)
    accepted_at: StrictFloat
    queued_at: StrictFloat
    dispatched_at: StrictFloat | None
    finished_at: StrictFloat | None
    terminal_receipt_id: str | None
    completion_class: str | None
    physical_effect_verified: StrictBool
    error: OperatorReceiptErrorV2 | None
    transport_exchanges: list[OperatorTransportExchangeV2] = Field(default_factory=list)
    transport_retention_errors: list[OperatorTransportRetentionErrorV2] = Field(default_factory=list)

    @field_validator("accepted_at", "queued_at", "dispatched_at", "finished_at")
    @classmethod
    def finite_times(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("receipt times must be finite")
        return value

    @model_validator(mode="after")
    def bind_terminality(self):
        expected = self.status not in _NONTERMINAL_V2
        if self.terminal is not expected:
            raise ValueError("receipt terminal flag does not match its closed status")
        return self


class OperatorTransportArtifactV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str = Field(min_length=1, max_length=1000)
    bytes: StrictInt = Field(ge=0)


class OperatorTransitionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    transition_id: str = Field(min_length=1, max_length=160)
    from_status: CommandStatusV2 | None
    to_status: CommandStatusV2
    at: StrictFloat
    reason: str | None

    @field_validator("at")
    @classmethod
    def finite_at(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("transition time must be finite")
        return value


class OperatorDeckMovementReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str = Field(min_length=1, max_length=160, pattern=r"^[A-Z0-9][A-Z0-9_]*$")
    target_label: str | None = Field(default=None, min_length=1, max_length=240)
    source_branch: str | None = Field(default=None, min_length=1, max_length=240)
    controller_completion_verified: StrictBool | None = None
    semantic_state_committed: StrictBool | None = None
    physical_observation_verified: StrictBool | None = None


class OperatorActionReceiptDetailV2(OperatorActionReceiptV2):
    canonical_inputs: dict[str, JsonValue]
    requested_values: dict[str, ReceiptScalarV2]
    effective_values: dict[str, ReceiptScalarV2]
    observed_values: dict[str, ReceiptScalarV2]
    raw_return_layers: dict[str, JsonValue]
    controller_evidence: dict[str, JsonValue]
    transport_artifacts: list[OperatorTransportArtifactV2]
    child_receipts: list[OperatorActionReceiptV2]
    transitions: list[OperatorTransitionV2]
    deck_movement: OperatorDeckMovementReceiptV1 | None = None

    @model_validator(mode="after")
    def bind_deck_movement_detail(self):
        if self.action_id == "oem.deck.move_to_location":
            if self.deck_movement is None:
                raise ValueError("deck movement detail requires typed deck evidence")
            if self.deck_movement.target != self.canonical_inputs.get("target"):
                raise ValueError("deck receipt target must match canonical inputs")
            deck = self.deck_movement
            if deck.physical_observation_verified is True and not self.physical_effect_verified:
                raise ValueError("deck physical observation cannot exceed receipt physical-effect truth")
            if self.physical_effect_verified and deck.physical_observation_verified is not True:
                raise ValueError("verified physical effect requires matching deck observation")
            if self.status == "completed":
                if deck.controller_completion_verified is not True or deck.semantic_state_committed is not True:
                    raise ValueError("completed deck movement requires controller completion and semantic commit")
                if deck.target_label is None or deck.source_branch is None:
                    raise ValueError("completed deck movement requires established target and source branch")
            else:
                if deck.semantic_state_committed is True:
                    raise ValueError("non-completed deck movement cannot claim semantic commit")
                if deck.controller_completion_verified is True and deck.semantic_state_committed is not True:
                    if self.status != "ambiguous" or self.completion_class != "recovery_required":
                        raise ValueError(
                            "controller-completed deck movement without semantic commit requires ambiguous recovery"
                        )
                if self.status in {"queued", "dispatched", "rejected"} and deck.controller_completion_verified is True:
                    raise ValueError("unissued or rejected deck movement cannot claim controller completion")
        elif self.deck_movement is not None:
            raise ValueError("deck movement evidence is forbidden on other actions")
        return self


class OperatorQueueItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command_id: str = Field(min_length=1, max_length=160)
    sequence: StrictInt = Field(ge=1)
    status: Literal["queued", "dispatched", "issued_pending", "interrupting"]
    method_id: str | None
    resource_keys: list[str]
    accepted_at: StrictFloat

    @field_validator("accepted_at")
    @classmethod
    def finite_accepted_at(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("queue time must be finite")
        return value


class OperatorQueueV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["bioxp.oem_command_queue.v1"]
    generated_at: StrictFloat
    items: list[OperatorQueueItemV1]

    @field_validator("generated_at")
    @classmethod
    def finite_generated_at(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("queue time must be finite")
        return value


class OperatorBoard4AuthorityV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    state: Board4StateV2
    prior_board_epoch: NonnegativeStrictInt | None
    active_board_epoch: NonnegativeStrictInt | None
    transition_phase: str = Field(min_length=1, max_length=80)
    transition_evidence: dict[str, JsonValue]
    member_motors: dict[str, StrictInt]
    state_version: StrictInt = Field(ge=1)
    updated_at: StrictFloat

    @field_validator("updated_at")
    @classmethod
    def finite_updated_at(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("authority time must be finite")
        return value

    @field_validator("member_motors")
    @classmethod
    def exact_member_motors(cls, value: dict[str, int]) -> dict[str, int]:
        if value != {"y": 0, "z": 1, "gripper": 2}:
            raise ValueError("board 4 member motors must be the frozen Serial-206 map")
        return value


class OperatorYAxisAuthorityV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    axis: Literal["y"]
    board_id: Literal[4]
    motor_id: Literal[0]
    ownership_generation: StrictInt = Field(ge=0)
    prior_board_epoch: NonnegativeStrictInt | None
    active_board_epoch: NonnegativeStrictInt | None
    prepared_board_epoch: NonnegativeStrictInt | None
    lifecycle_state: YAxisLifecycleStateV2
    reference_state: YAxisReferenceStateV2
    position_steps: StrictInt | None
    position_reply_valid: StrictBool
    position_status_code: StrictInt | None
    speed_steps_s: StrictInt | None
    speed_reply_valid: StrictBool
    speed_status_code: StrictInt | None
    left_switch_raw: StrictInt | None
    left_switch_reply_valid: StrictBool
    left_switch_status_code: StrictInt | None
    home_effective: StrictBool | None
    profile_fingerprint: str | None
    profile_readback_valid: StrictBool
    profile_mismatches: list[str]
    active_command: OperatorActionReceiptV2 | None
    interrupt_epoch: StrictInt = Field(ge=0)
    latest_compact_receipt: OperatorActionReceiptV2 | None
    last_discrepancy_steps: StrictInt | None
    state_version: StrictInt = Field(ge=1)
    updated_at: StrictFloat
    physical_position_verified: StrictBool

    @field_validator("updated_at")
    @classmethod
    def finite_y_updated_at(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Y authority time must be finite")
        return value


class OperatorDeckDestinationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Z0-9][A-Z0-9_]*$")
    label: str = Field(min_length=1, max_length=240)
    aliases: list[str] = Field(default_factory=list, max_length=64)
    branch_kind: Literal["ordinary", "barcode", "park"]
    camera_offset_supported: StrictBool


class OperatorDeckDashboardV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    current_location: str | None = Field(default=None, max_length=160)
    current_well: str | None = Field(default=None, max_length=160)
    position_table_revision: str = Field(min_length=1, max_length=160)
    destination_catalog_revision: str = Field(min_length=1, max_length=160)
    semantic_state_revision: StrictInt = Field(ge=0)
    ownership_generation: StrictInt = Field(ge=0)
    expected_board_epoch_by_board: dict[str, NonnegativeStrictInt]
    destinations: list[OperatorDeckDestinationV1]

    @field_validator("expected_board_epoch_by_board")
    @classmethod
    def exact_deck_board_epochs(cls, value: dict[str, int]) -> dict[str, int]:
        value = _canonical_board_epoch_map(value)
        if set(value) != {"4", "5"}:
            raise ValueError("deck dashboard requires exact board epochs 4 and 5")
        return value

    @field_validator("destinations")
    @classmethod
    def unique_deck_destinations(cls, value: list[OperatorDeckDestinationV1]) -> list[OperatorDeckDestinationV1]:
        keys = [destination.key for destination in value]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("deck dashboard destination keys must be present and unique")
        return value


class OperatorDashboardV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["bioxp.operator_dashboard.v2"]
    generated_at: StrictFloat
    ownership_generation: StrictInt = Field(ge=0)
    board4: OperatorBoard4AuthorityV2
    y_axis: OperatorYAxisAuthorityV2
    active_commands: list[OperatorActionReceiptV2]
    command_queue: OperatorQueueV1
    latest_receipts: list[OperatorActionReceiptV2]
    telemetry: OperatorDashboard | None = None
    deck: OperatorDeckDashboardV1 | None = None

    @field_validator("generated_at")
    @classmethod
    def finite_dashboard_time(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("dashboard time must be finite")
        return value

    @model_validator(mode="after")
    def bind_embedded_authority(self):
        if self.y_axis.ownership_generation != self.ownership_generation:
            raise ValueError("Y ownership generation must match dashboard ownership generation")
        return self


class OperatorActionSpecV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: str = Field(min_length=1, max_length=160)
    request_schema_version: Literal[
        "bioxp.operator_action_request.v2",
        "bioxp.operator_interrupt_request.v1",
    ]
    response_schema_version: Literal[
        "bioxp.operator_action_receipt.v2",
        "bioxp.operator_interrupt_receipt.v1",
    ]
    interrupt: StrictBool
    enabled: StrictBool
    disabled_reason: str | None
    destination_catalog_revision: str | None = Field(default=None, min_length=1, max_length=160)
    position_table_revision: str | None = Field(default=None, min_length=1, max_length=160)
    required_board_ids: list[Literal[4, 5]] | None = None
    expected_board_epoch_by_board: dict[str, NonnegativeStrictInt] | None = None
    destinations: list[OperatorDeckDestinationV1] | None = None

    @model_validator(mode="after")
    def bind_request_schema_to_interrupt(self):
        expected_request = (
            "bioxp.operator_interrupt_request.v1"
            if self.interrupt
            else "bioxp.operator_action_request.v2"
        )
        expected_response = (
            "bioxp.operator_interrupt_receipt.v1"
            if self.interrupt
            else "bioxp.operator_action_receipt.v2"
        )
        if self.request_schema_version != expected_request or self.response_schema_version != expected_response:
            raise ValueError("catalog request/response schemas do not match interrupt classification")
        deck_fields = (
            self.destination_catalog_revision,
            self.position_table_revision,
            self.required_board_ids,
            self.expected_board_epoch_by_board,
            self.destinations,
        )
        if self.action_id == "oem.deck.move_to_location":
            if any(value is None for value in deck_fields):
                raise ValueError("deck movement catalog row requires complete finite destination authority")
            if self.required_board_ids != [4, 5]:
                raise ValueError("deck movement requires exact board IDs 4 and 5")
            if set(self.expected_board_epoch_by_board or {}) != {"4", "5"}:
                raise ValueError("deck movement catalog requires exact board epochs 4 and 5")
            keys = [destination.key for destination in self.destinations or []]
            if not keys or len(keys) != len(set(keys)):
                raise ValueError("deck destination keys must be present and unique")
        elif any(value is not None for value in deck_fields):
            raise ValueError("deck destination authority is forbidden on other actions")
        return self


class OperatorControlCatalogV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["bioxp.operator_control_catalog.v2"]
    dashboard: OperatorDashboardV2
    actions: list[OperatorActionSpecV2]

    @model_validator(mode="after")
    def bind_deck_action_to_embedded_dashboard(self):
        deck_actions = [action for action in self.actions if action.action_id == "oem.deck.move_to_location"]
        if len(deck_actions) > 1:
            raise ValueError("catalog cannot contain duplicate deck movement actions")
        if not deck_actions:
            if self.dashboard.deck is not None:
                raise ValueError("embedded deck dashboard requires its canonical action")
            return self
        deck = self.dashboard.deck
        if deck is None:
            raise ValueError("deck movement action requires embedded deck dashboard authority")
        action = deck_actions[0]
        if deck.ownership_generation != self.dashboard.ownership_generation:
            raise ValueError("deck ownership generation must match embedded dashboard")
        if action.destination_catalog_revision != deck.destination_catalog_revision:
            raise ValueError("deck destination catalog revision is incoherent")
        if action.position_table_revision != deck.position_table_revision:
            raise ValueError("deck position table revision is incoherent")
        if action.expected_board_epoch_by_board != deck.expected_board_epoch_by_board:
            raise ValueError("deck board epochs are incoherent")
        if action.destinations != deck.destinations:
            raise ValueError("deck destination list is incoherent")
        return self


class OperatorActionHistoryV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["bioxp.operator_action_history.v2"]
    items: list[OperatorActionReceiptV2]
    next_cursor: str | None
    limit: StrictInt = Field(ge=1, le=200)


class OperatorMethodV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["bioxp.operator_method.v1"]
    method_id: str = Field(min_length=1, max_length=160)
    action_id: Literal["oem.xy.move_absolute", "oem.xy.home"]
    status: MethodStatusV1
    state_version: StrictInt = Field(ge=1)
    child_receipts: list[OperatorActionReceiptV2]
    accepted_at: StrictFloat
    finished_at: StrictFloat | None

    @field_validator("accepted_at", "finished_at")
    @classmethod
    def finite_method_times(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("method times must be finite")
        return value


OperatorDashboardWire = Annotated[
    OperatorDashboard | OperatorDashboardV2,
    Field(discriminator="schema_version"),
]
OperatorControlCatalogWire = Annotated[
    OperatorControlCatalog | OperatorControlCatalogV2,
    Field(discriminator="schema_version"),
]


class OperatorReportReleaseSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    commit: str | None
    tree: str | None
    mode: str | None
    manifest_sha256: str | None
    aggregate_sha256: str | None


class OperatorReportReleaseImageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str | None
    inspection_receipt_sha256: str | None


class OperatorReportReleaseDeploymentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    receipt_id: str | None
    installed_at: StrictFloat | StrictInt | str | None
    receipt_sha256: str | None


class OperatorReportListenerV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    host: str | None
    port: StrictInt | None


class OperatorReportReleaseBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    service_unit: str | None
    unit_sha256: str | None
    launcher_sha256: str | None
    configuration_sha256: str | None
    oem_lock_sha256: str | None
    udocker_sha256: str | None
    udocker_tree_sha256: str | None
    declared_listener: OperatorReportListenerV1 | None
    observed_listener: OperatorReportListenerV1 | None


class OperatorReportReleaseIdentityV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_value: str | None = Field(alias="schema", serialization_alias="schema")
    status: str | None
    verified: StrictBool
    reason_code: str | None
    release_id: str | None
    source: OperatorReportReleaseSourceV1
    image: OperatorReportReleaseImageV1
    deployment: OperatorReportReleaseDeploymentV1
    binding: OperatorReportReleaseBindingV1


class OperatorReportUnavailableReleaseIdentityV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: str
    verified: Literal[False]
    reason_code: str


class OperatorReportJsonObjectV1(RootModel[dict[str, JsonValue]]):
    model_config = ConfigDict(strict=True)


class OperatorReportSourceHighWatersV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operator_commands: StrictInt
    operator_transitions: StrictInt
    pipette_operations: StrictInt
    pipette_channel_observations: StrictInt
    pipette_transport_exchanges: StrictInt
    runtime_events: StrictInt
    pipette_pressure_streams: StrictInt
    pipette_pressure_chunks: StrictInt
    runtime_evidence_objects: StrictInt
    runtime_evidence_links: StrictInt | None = None
    runtime_evidence_events: StrictInt
    operator_plane_command_versions: StrictInt
    operator_plane_pipette_versions: StrictInt
    operator_plane_pressure_stream_versions: StrictInt
    operator_plane_evidence_versions: StrictInt


class OperatorReportSchemaIdentityV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    database_identity: Literal["robot_authoritative_sqlite"]
    schema_version: Literal[5]
    identity_version: StrictInt | None
    release_identity: OperatorReportReleaseIdentityV1


class OperatorReportLegacySnapshotV1(BaseModel):
    """Exact read-only snapshot identity emitted by supported schema 2."""

    model_config = ConfigDict(extra="forbid", strict=True)

    database_identity: Literal["robot_authoritative_sqlite"]
    schema_version: Literal[2]
    database_path_exposed: Literal[False]
    identity_version: Literal[2]
    high_water_sequence: StrictInt | None = Field(default=None, ge=0)
    high_water_rowid: StrictInt | None = Field(default=None, ge=0)
    high_water_event_id: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_one_high_water(self):
        high_waters = (
            self.high_water_sequence,
            self.high_water_rowid,
            self.high_water_event_id,
        )
        if sum(value is not None for value in high_waters) != 1:
            raise ValueError("legacy report snapshot requires exactly one high-water identity")
        return self


class OperatorReportSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    database_incarnation_id: str = Field(min_length=1)
    schema_identity: OperatorReportSchemaIdentityV1
    release_identity: OperatorReportReleaseIdentityV1
    source_high_waters: OperatorReportSourceHighWatersV1
    high_water_sequence: StrictInt | None = Field(default=None, ge=0)
    high_water_rowid: StrictInt | None = Field(default=None, ge=0)
    high_water_event_id: StrictInt | None = Field(default=None, ge=0)


OperatorReportSnapshotContractV1 = OperatorReportSnapshotV1 | OperatorReportLegacySnapshotV1


class OperatorReportFiltersV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    start: StrictFloat | StrictInt | None = None
    end: StrictFloat | StrictInt | None = None
    status: str | None = None
    operation: str | None = None
    action: str | None = None
    event_kind: str | None = None
    channel: StrictInt | None = Field(default=None, ge=0, le=3)
    entrypoint: str | None = None
    caller_class: str | None = None
    control_class: str | None = None
    protocol_job_id: str | None = None
    protocol_action_id: str | None = None
    lifecycle_stage_id: str | None = None
    lifecycle_attempt_id: str | None = None
    outcome: str | None = None
    event_source: str | None = None
    pressure_stream_id: str | None = None
    delivery_verified: StrictBool | None = None
    controller_acknowledged: StrictBool | None = None
    completion_verified: StrictBool | None = None
    hardware_postcondition_verified: StrictBool | None = None
    physical_effect_verified: StrictBool | None = None
    evidence_state: str | None = None
    command_id: str | None = None
    pipette_operation_id: str | None = None
    connection_generation: StrictInt | None = Field(default=None, ge=0)
    ownership_generation: StrictInt | None = Field(default=None, ge=0)
    limit: StrictInt = Field(ge=1, le=100000)


class OperatorReportCommandCountsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    total: StrictInt = Field(ge=0)
    by_status: dict[str, StrictInt]


class OperatorReportPipetteCountsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    total: StrictInt = Field(ge=0)
    by_status: dict[str, StrictInt] | None = None


class OperatorReportEventCountsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    total: StrictInt = Field(ge=0)
    by_kind: dict[str, StrictInt] | None = None


class OperatorReportPressureCountsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    streams: StrictInt = Field(ge=0)
    chunks: StrictInt = Field(ge=0)


class OperatorReportRatesV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    delivery_rate: StrictFloat = Field(ge=0, le=1)
    ack_rate: StrictFloat = Field(ge=0, le=1)
    completion_rate: StrictFloat = Field(ge=0, le=1)
    postcondition_rate: StrictFloat = Field(ge=0, le=1)
    physical_effect_rate: StrictFloat = Field(ge=0, le=1)
    failure_rate: StrictFloat = Field(ge=0, le=1)


class OperatorReportLatencyV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    average_ms: StrictFloat = Field(ge=0)
    maximum_ms: StrictFloat = Field(ge=0)


class OperatorReportErrorCountsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    by_code: dict[str, StrictInt]


class OperatorReportSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["window", "filtered"]
    filters: OperatorReportFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    commands: OperatorReportCommandCountsV1
    pipette_operations: OperatorReportPipetteCountsV1
    runtime_events: OperatorReportEventCountsV1
    pressure: OperatorReportPressureCountsV1
    rates: OperatorReportRatesV1
    latency: OperatorReportLatencyV1
    errors: OperatorReportErrorCountsV1


class OperatorReportCommandRowV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sequence: StrictInt = Field(ge=1)
    command_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=256)
    operation: str | None = Field(default=None, max_length=120)
    command_kind: str | None = Field(default=None, max_length=80)
    entrypoint_id: str | None = Field(default=None, max_length=160)
    caller_class: str | None = Field(default=None, max_length=120)
    control_class: str | None = Field(default=None, max_length=120)
    action_id: str | None = Field(default=None, max_length=160)
    status: str = Field(min_length=1, max_length=80)
    outcome: str | None = Field(default=None, max_length=2000)
    failure_code: str | None = Field(default=None, max_length=2000)
    ownership_generation: StrictInt | None = Field(default=None, ge=0)
    connection_generation: StrictInt | None = Field(default=None, ge=0)
    started_at: StrictFloat | StrictInt | str | None = None
    admitted_at: StrictFloat | StrictInt | str | None = None
    dispatched_at: StrictFloat | StrictInt | str | None = None
    finished_at: StrictFloat | StrictInt | str | None = None
    duration_ms: StrictFloat | StrictInt | None = Field(default=None, ge=0)
    delivery_verified: StrictBool
    controller_acknowledged: StrictBool
    completion_verified: StrictBool
    semantic_query_response_verified: StrictBool
    hardware_precondition_verified: StrictBool
    hardware_postcondition_verified: StrictBool
    physical_effect_verified: StrictBool
    evidence_state: str | None = Field(default=None, max_length=80)


class OperatorReportTransitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    transition_id: StrictInt = Field(ge=1)
    state: str = Field(min_length=1, max_length=80)
    observed_at: StrictFloat | StrictInt | str
    detail: OperatorReportJsonObjectV1


class OperatorReportLegalHoldAssessmentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str
    observed_at: StrictFloat | StrictInt | str
    legal_hold_requested: StrictBool
    assessment: JsonValue
    actor: str | None
    retained_deadline: StrictFloat | StrictInt | str | None
    legal_hold_projection_updated: StrictBool


class OperatorReportEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    evidence_artifact_id: str = Field(min_length=1, max_length=160)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: StrictInt = Field(ge=0)
    created_at: StrictFloat | StrictInt | str
    retention_deadline: StrictFloat | StrictInt | str | None
    legal_hold: StrictBool
    latest_legal_hold_assessment: OperatorReportLegalHoldAssessmentV1 | None
    expiry_state: str = Field(min_length=1, max_length=80)
    expiry_receipt_id: str | None = Field(default=None, max_length=160)


class OperatorReportPipetteRowV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pipette_operation_id: str = Field(min_length=1, max_length=160)
    command_id: str = Field(min_length=1, max_length=160)
    operation: str | None = Field(default=None, max_length=120)
    entrypoint_id: str | None = Field(default=None, max_length=160)
    caller_class: str | None = Field(default=None, max_length=120)
    control_class: str | None = Field(default=None, max_length=120)
    action_id: str | None = Field(default=None, max_length=160)
    protocol_job_id: str | None = Field(default=None, max_length=160)
    protocol_action_id: str | None = Field(default=None, max_length=160)
    lifecycle_stage_id: str | None = Field(default=None, max_length=160)
    lifecycle_attempt_id: str | None = Field(default=None, max_length=160)
    callback_session_id: str | None = Field(default=None, max_length=160)
    status: str = Field(min_length=1, max_length=80)
    outcome: str | None = Field(default=None, max_length=2000)
    failure_code: str | None = Field(default=None, max_length=2000)
    delivery_verified: StrictBool
    controller_acknowledged: StrictBool
    completion_verified: StrictBool
    semantic_query_response_verified: StrictBool
    hardware_postcondition_verified: StrictBool
    physical_effect_verified: StrictBool
    evidence_state: str | None = Field(default=None, max_length=80)


class OperatorReportChannelV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    observation_id: str = Field(min_length=1, max_length=160)
    command_id: str = Field(min_length=1, max_length=160)
    pipette_operation_id: str = Field(min_length=1, max_length=160)
    channel: StrictInt = Field(ge=0, le=3)
    phase: str | None = Field(default=None, max_length=80)
    observed_at: StrictFloat | StrictInt | str
    semantic_validity: str | None = Field(default=None, max_length=80)
    truth_source: str | None = Field(default=None, max_length=120)
    tip_loaded: StrictBool | None
    pressure: StrictFloat | StrictInt | None
    pressure_units: str | None = Field(default=None, max_length=40)
    status: str | StrictInt | None = Field(default=None)
    error_code: str | StrictInt | None
    firmware_class: str | None = Field(default=None, max_length=120)
    detail: OperatorReportJsonObjectV1


class OperatorReportExchangeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    exchange_id: str = Field(min_length=1, max_length=160)
    transaction_id: str | StrictInt | None = Field(default=None)
    channel: StrictInt | None = Field(default=None, ge=0, le=3)
    transaction_phase: str | None = Field(default=None, max_length=80)
    command_family: str | None
    matcher_name: str | None = Field(default=None, max_length=120)
    tx_id: StrictInt | None
    expected_rx_id: StrictInt | None
    observed_rx_id: StrictInt | None
    tx_bytes: list[StrictInt]
    rx_bytes: list[StrictInt]
    delivery_verified: StrictBool
    semantic_match: StrictBool
    controller_acknowledged: StrictBool
    completion_verified: StrictBool
    completion_before_ack: StrictBool
    sent_at: StrictFloat | StrictInt | str | None
    received_at: StrictFloat | StrictInt | str | None
    ack_at: StrictFloat | StrictInt | str | None
    completion_at: StrictFloat | StrictInt | str | None


class OperatorReportPipetteEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: StrictInt = Field(ge=1)
    event_source: str = Field(min_length=1, max_length=120)
    event_kind: str = Field(min_length=1, max_length=120)
    observed_at: StrictFloat | StrictInt | str
    event: OperatorReportJsonObjectV1


class OperatorReportPressureStreamChildV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stream_session_id: str = Field(min_length=1, max_length=160)
    channels: list[StrictInt]
    sample_period_ms: StrictFloat | StrictInt | None = Field(default=None, ge=0)
    started_at: StrictFloat | StrictInt | str
    stopped_at: StrictFloat | StrictInt | str | None
    source_generation: StrictInt | None = Field(default=None, ge=0)
    reader_generation: StrictInt | None = Field(default=None, ge=0)
    offset_identity: str | None = Field(default=None, max_length=160)
    terminal_state: str | None = Field(default=None, max_length=80)
    loss_count: StrictInt | None = Field(default=None, ge=0)


class OperatorReportPipetteDetailV1(OperatorReportPipetteRowV1):
    channels: list[OperatorReportChannelV1]
    exchanges: list[OperatorReportExchangeV1]
    events: list[OperatorReportPipetteEventV1]
    pressure_streams: list[OperatorReportPressureStreamChildV1]
    snapshot: OperatorReportSnapshotContractV1
    child_page_limit: StrictInt = Field(ge=1)


class OperatorReportCommandPageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    filters: OperatorReportFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    commands: list[OperatorReportCommandRowV1]


class OperatorReportPageContinuationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None


class OperatorReportCommandChildFiltersV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command_id: str = Field(min_length=1, max_length=160)
    limit: StrictInt = Field(ge=1)


class OperatorReportPipetteChildFiltersV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pipette_operation_id: str = Field(min_length=1, max_length=160)
    limit: StrictInt = Field(ge=1)


class OperatorReportCommandDetailV1(OperatorReportCommandRowV1):
    requested_inputs: OperatorReportJsonObjectV1
    effective_inputs: OperatorReportJsonObjectV1
    source_identity: OperatorReportJsonObjectV1
    transitions: list[OperatorReportTransitionV1]
    evidence: list[OperatorReportEvidenceV1]
    evidence_preview: list[OperatorReportEvidenceV1]
    evidence_continuation: OperatorReportPageContinuationV1
    pipette: OperatorReportPipetteDetailV1 | None
    snapshot: OperatorReportSnapshotContractV1
    child_page_limit: StrictInt = Field(ge=1)


class OperatorReportTransitionsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command_id: str = Field(min_length=1, max_length=160)
    filters: OperatorReportCommandChildFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    transitions: list[OperatorReportTransitionV1]


class OperatorReportCommandEvidencePageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command_id: str = Field(min_length=1, max_length=160)
    filters: OperatorReportCommandChildFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    evidence: list[OperatorReportEvidenceV1]


class OperatorReportPipettePageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    filters: OperatorReportFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    pipette: list[OperatorReportPipetteRowV1]


class OperatorReportPipetteChannelsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pipette_operation_id: str = Field(min_length=1, max_length=160)
    filters: OperatorReportPipetteChildFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    channels: list[OperatorReportChannelV1]


class OperatorReportPipetteExchangesV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pipette_operation_id: str = Field(min_length=1, max_length=160)
    filters: OperatorReportPipetteChildFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    exchanges: list[OperatorReportExchangeV1]


class OperatorReportEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: StrictInt = Field(ge=1)
    command_id: str | None = Field(default=None, max_length=160)
    pipette_operation_id: str | None = Field(default=None, max_length=160)
    event_source: str = Field(min_length=1, max_length=120)
    event_kind: str = Field(min_length=1, max_length=120)
    channel: StrictInt | None = Field(default=None, ge=0, le=3)
    observed_at: StrictFloat | StrictInt | str
    event: OperatorReportJsonObjectV1
    snapshot: OperatorReportSnapshotContractV1 | None = None


class OperatorReportEventsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_kind: str | None
    filters: OperatorReportFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    events: list[OperatorReportEventV1]


class OperatorReportPressureStreamRowV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stream_session_id: str = Field(min_length=1, max_length=160)
    pipette_operation_id: str | None = Field(default=None, max_length=160)
    channels: list[StrictInt]
    sample_period_ms: StrictFloat | StrictInt | None = Field(default=None, ge=0)
    started_at: StrictFloat | StrictInt | str
    stopped_at: StrictFloat | StrictInt | str | None
    source_generation: StrictInt | None = Field(default=None, ge=0)
    reader_generation: StrictInt | None = Field(default=None, ge=0)
    offset_identity: str | None = Field(default=None, max_length=160)
    terminal_state: str | None = Field(default=None, max_length=80)
    loss_count: StrictInt | None = Field(default=None, ge=0)


class OperatorReportPressureStreamsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    filters: OperatorReportFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    pressure_streams: list[OperatorReportPressureStreamRowV1]


class OperatorReportPressureChunkV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chunk_id: str = Field(min_length=1, max_length=160)
    channel: StrictInt = Field(ge=0, le=3)
    chunk_sequence: StrictInt = Field(ge=0)
    sample_count: StrictInt = Field(ge=0)
    lost_sample_count: StrictInt = Field(ge=0)
    units: str = Field(min_length=1, max_length=40)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: StrictInt = Field(ge=0)
    evidence_artifact_id: str | None = Field(default=None, max_length=160)
    summary: OperatorReportJsonObjectV1 | None = None


class OperatorReportPressureDetailV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stream_session_id: str = Field(min_length=1, max_length=160)
    pipette_operation_id: str | None = Field(default=None, max_length=160)
    channels: list[StrictInt]
    sample_period_ms: StrictFloat | StrictInt | None = Field(default=None, ge=0)
    started_at: StrictFloat | StrictInt | str
    stopped_at: StrictFloat | StrictInt | str | None
    terminal_state: str | None = Field(default=None, max_length=80)
    loss_count: StrictInt | None = Field(default=None, ge=0)
    chunks: list[OperatorReportPressureChunkV1]
    child_page_limit: StrictInt = Field(ge=1)
    snapshot: OperatorReportSnapshotContractV1


class OperatorReportPressureSamplesV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stream_session_id: str = Field(min_length=1, max_length=160)
    filters: OperatorReportFiltersV1
    snapshot: OperatorReportSnapshotContractV1
    returned_count: StrictInt = Field(ge=0)
    filtered_total: StrictInt = Field(ge=0)
    has_more: StrictBool
    next_cursor: str | None
    samples: list[OperatorReportPressureChunkV1]


class OperatorReportExportArtifactV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: StrictInt = Field(ge=0)
    relpath: str | None = None


class OperatorReportExportReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    receipt_schema: str
    publisher_identity: str
    export_id: str
    evidence_artifact_id: str
    created_at: StrictFloat | StrictInt
    retention_deadline: StrictFloat | StrictInt
    normalized_filters: OperatorReportFiltersV1
    filter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_high_waters: OperatorReportSourceHighWatersV1
    schema_identity: OperatorReportSchemaIdentityV1
    release_identity: OperatorReportReleaseIdentityV1
    database_incarnation_id: str
    row_count: StrictInt = Field(ge=0)
    artifact: OperatorReportExportArtifactV1
    public_download_available: StrictBool = True
    evidence_state: str
    legal_hold: StrictBool
    evidence_available: StrictBool


class OperatorReportLegacyExportReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    receipt_schema: str
    publisher_identity: str
    export_id: str
    evidence_artifact_id: str
    created_at: StrictFloat | StrictInt
    retention_deadline: StrictFloat | StrictInt
    filter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: StrictInt = Field(ge=0)
    artifact: OperatorReportExportArtifactV1
    legacy_snapshot: OperatorReportJsonObjectV1
    release_identity: OperatorReportUnavailableReleaseIdentityV1
    public_download_available: Literal[False]
    evidence_state: str
    legal_hold: StrictBool
    evidence_available: StrictBool


class OperatorReportExportV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    export_id: str = Field(min_length=1, max_length=160)
    evidence_artifact_id: str = Field(min_length=1, max_length=160)
    status: str
    format: str
    row_count: StrictInt = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: StrictInt = Field(ge=0)
    release_identity: OperatorReportReleaseIdentityV1
    download: str = Field(min_length=1, max_length=512)


class OperatorReportExportListItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    export_id: str
    format: str
    row_count: StrictInt
    sha256: str
    byte_count: StrictInt
    status: str
    created_at: StrictFloat | StrictInt | str
    release_identity: OperatorReportReleaseIdentityV1 | OperatorReportUnavailableReleaseIdentityV1
    publication_state: str
    evidence_state: str
    legal_hold: StrictBool
    evidence_available: StrictBool
    download: str | None


class OperatorReportExportListV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[OperatorReportExportListItemV1]
    returned_count: StrictInt = Field(ge=0)
    limit: StrictInt = Field(ge=1, le=1000)
    available: StrictBool = True
    unavailable_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def bind_availability(self):
        if self.available and self.unavailable_reason is not None:
            raise ValueError("available export index cannot carry an unavailable reason")
        if not self.available and self.unavailable_reason is None:
            raise ValueError("unavailable export index requires a reason")
        if not self.available and (self.items or self.returned_count != 0):
            raise ValueError("unavailable export index cannot claim indexed exports")
        return self


class OperatorReportHealthSchemaCheckV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str | None


class OperatorReportHealthStoreCheckV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str | None
    recorded_schema_version: StrictInt | None
    current_schema_version: Literal[5] | None


class OperatorReportHealthDurabilityV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str | None
    journal_mode: str | None
    synchronous: StrictInt | None


class OperatorReportHealthCheckpointV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str | None
    age_seconds: StrictFloat | StrictInt | None


class OperatorReportHealthBackupV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str | None
    age_seconds: StrictFloat | StrictInt | None
    backup_id: str | None


class OperatorReportHealthWriterV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str | None
    writer_status: str | None
    queue_depth: StrictInt | None
    telemetry_available: StrictBool | None
    mode: str | None


class OperatorReportHealthStorageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str | None
    database_bytes: StrictInt | None
    wal_bytes: StrictInt | None
    wal_threshold_bytes: StrictInt | None
    free_bytes: StrictInt | None
    minimum_free_bytes: StrictInt | None
    evidence_bytes: StrictInt | None
    backup_bytes: StrictInt | None


class OperatorReportHealthChecksV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_check: OperatorReportHealthSchemaCheckV1 = Field(alias="schema", serialization_alias="schema")
    store_identity: OperatorReportHealthStoreCheckV1
    integrity: OperatorReportHealthSchemaCheckV1
    foreign_keys: OperatorReportHealthSchemaCheckV1
    durability: OperatorReportHealthDurabilityV1
    checkpoint: OperatorReportHealthCheckpointV1
    backup: OperatorReportHealthBackupV1
    writer: OperatorReportHealthWriterV1
    storage: OperatorReportHealthStorageV1


class OperatorReportHealthCountsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    commands: StrictInt | None
    pipette_operations: StrictInt | None
    retained_evidence: StrictInt | None
    pending_expiry_evidence: StrictInt | None
    integrity_failures: StrictInt | None


class OperatorReportAuditHealthV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_value: str | None = Field(alias="schema", serialization_alias="schema")
    status: str | None
    generated_at: StrictFloat | StrictInt | None
    degraded_reasons: list[str]
    checks: OperatorReportHealthChecksV1
    counts: OperatorReportHealthCountsV1
    release_identity: OperatorReportReleaseIdentityV1
    physical_admission_gate_added: StrictBool


class OperatorReportExportRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["json", "csv"] = "json"
    start: StrictFloat | StrictInt | None = None
    end: StrictFloat | StrictInt | None = None
    status: str | None = None
    operation: str | None = None
    action: str | None = None
    channel: StrictInt | None = Field(default=None, ge=0, le=3)
    limit: StrictInt = Field(default=1000, ge=1, le=100000)


class OperatorReportExportMetadataV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    export_id: str = Field(min_length=1, max_length=160)
    format: str
    filter: OperatorReportFiltersV1 | None
    filter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: OperatorReportExportReceiptV1 | OperatorReportLegacyExportReceiptV1
    receipt: OperatorReportExportReceiptV1 | OperatorReportLegacyExportReceiptV1
    release_identity: OperatorReportReleaseIdentityV1 | OperatorReportUnavailableReleaseIdentityV1
    row_count: StrictInt = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: StrictInt = Field(ge=0)
    status: str
    created_at: StrictFloat | StrictInt | str
    completed_at: StrictFloat | StrictInt | str | None
    publication_state: str
    evidence_state: str
    legal_hold: StrictBool
    evidence_available: StrictBool
    download: str | None = Field(default=None, max_length=512)
