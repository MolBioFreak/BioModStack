from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, TypeAdapter, model_validator


class _CommandBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_generation: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class CollectHardwareSnapshotCommand(_CommandBase):
    command: Literal["collect_hardware_snapshot"]


class ActivateUsbForServiceCommand(_CommandBase):
    command: Literal["activate_usb_for_service"]


class InitializeOemEnvironmentCommand(_CommandBase):
    command: Literal["initialize_oem_environment"]
    mode: Literal["live"]
    operator_ack: Literal["INITIALIZE"]


class InitializeMotorsCommand(_CommandBase):
    command: Literal["initialize_motors"]


class RunOemMotorStageCommand(_CommandBase):
    """One admitted legacy source stage; M02 action current is never standalone."""

    command: Literal["run_oem_motor_stage"]
    stage: Literal["z-home", "gripper-clear-10000", "gripper-home"]
    mode: Literal["live"]
    operator_ack: Literal["HOME"]


class RecordOemMotorStageObservationCommand(_CommandBase):
    command: Literal["record_oem_motor_stage_observation"]
    stage: Literal[
        "z-home",
        "gripper-clear-10000",
        "gripper-home",
    ]
    observed_pass: StrictBool
    operator_ack: Literal["OBSERVE"]
    operator_note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


AxisDiagnosticAxis: TypeAlias = Literal["x", "y", "z", "g", "door"]
AxisDiagnosticOperation: TypeAlias = Literal[
    "move-negative",
    "move-positive",
    "home",
    "park-6000",
    "commission-home",
    "close",
    "open",
    "open-wide",
]
_AXIS_DIAGNOSTIC_OPERATIONS = {
    "x": frozenset({"move-negative", "move-positive", "home", "park-6000"}),
    "y": frozenset({"move-negative", "move-positive", "home"}),
    "z": frozenset({"move-negative", "move-positive", "home"}),
    "g": frozenset({"commission-home", "close", "open", "open-wide"}),
    "door": frozenset({"home", "open", "close"}),
}


class CollectAxisDiagnosticsCommand(_CommandBase):
    command: Literal["collect_axis_diagnostics"]


class RunAxisDiagnosticCommand(_CommandBase):
    command: Literal["run_axis_diagnostic"]
    axis: AxisDiagnosticAxis
    operation: AxisDiagnosticOperation

    @model_validator(mode="after")
    def validate_axis_operation(self) -> "RunAxisDiagnosticCommand":
        if self.operation not in _AXIS_DIAGNOSTIC_OPERATIONS[self.axis]:
            raise ValueError(f"operation {self.operation!r} is not valid for axis {self.axis!r}")
        return self


class StopAxisDiagnosticCommand(_CommandBase):
    command: Literal["stop_axis_diagnostic"]
    axis: AxisDiagnosticAxis


class RecoverMotionNonHomingCommand(_CommandBase):
    command: Literal["recover_motion_non_homing"]


class StartJobCommand(_CommandBase):
    command: Literal["start_job"]
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class PauseJobCommand(_CommandBase):
    command: Literal["pause_job"]
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class ResumeJobCommand(_CommandBase):
    command: Literal["resume_job"]
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class StopJobCommand(_CommandBase):
    command: Literal["stop_job"]
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class RecoverRuntimeCommand(_CommandBase):
    command: Literal["recover_runtime"]


CommandRequest: TypeAlias = Annotated[
    ActivateUsbForServiceCommand
    | CollectHardwareSnapshotCommand
    | InitializeOemEnvironmentCommand
    | InitializeMotorsCommand
    | RunOemMotorStageCommand
    | RecordOemMotorStageObservationCommand
    | CollectAxisDiagnosticsCommand
    | RunAxisDiagnosticCommand
    | StopAxisDiagnosticCommand
    | RecoverMotionNonHomingCommand
    | StartJobCommand
    | PauseJobCommand
    | ResumeJobCommand
    | StopJobCommand
    | RecoverRuntimeCommand,
    Field(discriminator="command"),
]
COMMAND_REQUEST_ADAPTER = TypeAdapter(CommandRequest)


def parse_command_request(payload: object) -> CommandRequest:
    return COMMAND_REQUEST_ADAPTER.validate_python(payload)
