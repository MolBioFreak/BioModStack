from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

CommandName = Literal[
    "activate_usb_for_service",
    "collect_hardware_snapshot",
    "initialize_oem_environment",
    "initialize_motors",
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


_UNVERIFIED = "Disabled until the robot-online contract and OEM mapping are verified"

DEFAULT_COMMAND_REGISTRY: Mapping[CommandName, CommandDefinition] = MappingProxyType(
    {
        "activate_usb_for_service": CommandDefinition(
            name="activate_usb_for_service",
            enabled=True,
            route_key="activate_usb_for_service",
            required_capability=None,
            requires_runtime_ready=False,
            requires_hardware_ready=False,
            requires_runtime_inactive=True,
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
            enabled=True,
            route_key="initialize_oem_environment",
            required_capability="initialize_oem_environment",
            requires_hardware_ready=True,
            required_lifecycle_states=(
                ("constructor_pipette_stage", "not_run"),
                ("initialization_without_motion", "blocked"),
                ("initial_check", "blocked"),
            ),
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
