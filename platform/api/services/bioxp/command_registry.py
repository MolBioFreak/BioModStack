from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

CommandName = Literal[
    "activate_usb_for_service",
    "collect_hardware_snapshot",
    "initialize_oem_environment",
    "initialize_motors",
    "run_oem_motor_stage",
    "record_oem_motor_stage_observation",
    "collect_axis_diagnostics",
    "run_axis_diagnostic",
    "stop_axis_diagnostic",
    "recover_motion_non_homing",
    "start_job",
    "pause_job",
    "resume_job",
    "stop_job",
    "recover_runtime",
]


@dataclass(frozen=True)
class CommandDefinition:
    name: CommandName
    enabled: bool
    route_key: str | None
    required_capability: str | None
    requires_fresh_observation: bool = True
    requires_runtime_ready: bool = True
    requires_hardware_ready: bool = True
    requires_runtime_inactive: bool = False
    disabled_reason: str = ""
    lifecycle_stage: str | None = None
    repeatable: bool = False
    required_lifecycle_states: tuple[tuple[str, str], ...] = ()
    maintenance_policy: Literal["independent", "motion_unblocked", "recovery_required"] = "independent"


_UNVERIFIED = "Disabled until the robot-online contract and OEM mapping are verified"
_ROBOT_CONTRACT_UNAVAILABLE = (
    "robot-contract-unavailable: unsupported by the exact robot runtime contract"
)

DEFAULT_COMMAND_REGISTRY: Mapping[CommandName, CommandDefinition] = MappingProxyType(
    {
        "activate_usb_for_service": CommandDefinition(
            name="activate_usb_for_service",
            enabled=False,
            route_key=None,
            required_capability=None,
            requires_runtime_ready=False,
            requires_hardware_ready=False,
            requires_runtime_inactive=True,
            disabled_reason=_ROBOT_CONTRACT_UNAVAILABLE,
        ),
        "collect_hardware_snapshot": CommandDefinition(
            name="collect_hardware_snapshot",
            enabled=True,
            route_key="collect_hardware_snapshot",
            required_capability="collect_hardware_snapshot",
            requires_fresh_observation=False,
            requires_runtime_ready=True,
            requires_hardware_ready=False,
        ),
        "initialize_oem_environment": CommandDefinition(
            name="initialize_oem_environment",
            enabled=False,
            route_key=None,
            required_capability=None,
            requires_hardware_ready=True,
            disabled_reason=_ROBOT_CONTRACT_UNAVAILABLE,
            required_lifecycle_states=(
                ("constructor_pipette_stage", "not_run"),
                ("initialization_without_motion", "blocked"),
                ("initial_check", "blocked"),
            ),
        ),
        "run_oem_motor_stage": CommandDefinition(
            name="run_oem_motor_stage",
            enabled=False,
            route_key=None,
            required_capability=None,
            requires_hardware_ready=True,
            disabled_reason=_ROBOT_CONTRACT_UNAVAILABLE,
            required_lifecycle_states=(("initial_check", "passed"),),
            maintenance_policy="motion_unblocked",
        ),
        "record_oem_motor_stage_observation": CommandDefinition(
            name="record_oem_motor_stage_observation",
            enabled=False,
            route_key=None,
            required_capability=None,
            requires_runtime_ready=True,
            requires_hardware_ready=False,
            disabled_reason=_ROBOT_CONTRACT_UNAVAILABLE,
            required_lifecycle_states=(("initial_check", "passed"),),
        ),
        "collect_axis_diagnostics": CommandDefinition(
            name="collect_axis_diagnostics",
            enabled=True,
            route_key="collect_axis_diagnostics",
            required_capability="collect_axis_diagnostics",
            requires_fresh_observation=True,
            requires_runtime_ready=True,
            requires_hardware_ready=False,
        ),
        "run_axis_diagnostic": CommandDefinition(
            name="run_axis_diagnostic",
            enabled=True,
            route_key="run_axis_diagnostic",
            required_capability="run_axis_diagnostic",
            requires_fresh_observation=False,
            requires_runtime_ready=False,
            requires_hardware_ready=False,
            maintenance_policy="independent",
        ),
        "stop_axis_diagnostic": CommandDefinition(
            name="stop_axis_diagnostic",
            enabled=True,
            route_key="stop_axis_diagnostic",
            required_capability="stop_axis_diagnostic",
            requires_fresh_observation=False,
            requires_runtime_ready=False,
            requires_hardware_ready=False,
        ),
        "recover_motion_non_homing": CommandDefinition(
            name="recover_motion_non_homing",
            enabled=True,
            route_key="recover_motion_non_homing",
            required_capability="recover_motion_non_homing",
            requires_fresh_observation=False,
            requires_runtime_ready=False,
            requires_hardware_ready=False,
            maintenance_policy="independent",
        ),
        **{
        name: CommandDefinition(
            name=name,
            enabled=False,
            route_key=None,
            required_capability=name,
            disabled_reason=_UNVERIFIED,
        )
        for name in (
            "initialize_motors",
            "start_job",
            "pause_job",
            "resume_job",
            "stop_job",
            "recover_runtime",
        )
        },
    }
)
