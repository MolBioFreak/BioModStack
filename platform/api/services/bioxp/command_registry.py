from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

CommandName = Literal[
    "collect_hardware_snapshot",
    "construct_pipettes",
    "initialize_without_motion",
    "run_initial_check",
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
    required_capability: str
    requires_fresh_observation: bool = True
    requires_runtime_ready: bool = True
    requires_hardware_ready: bool = True
    disabled_reason: str = ""


_UNVERIFIED = "Disabled until the robot-online contract and OEM mapping are verified"

DEFAULT_COMMAND_REGISTRY: Mapping[CommandName, CommandDefinition] = MappingProxyType(
    {
        "collect_hardware_snapshot": CommandDefinition(
            name="collect_hardware_snapshot",
            enabled=True,
            route_key="collect_hardware_snapshot",
            required_capability="collect_hardware_snapshot",
            requires_runtime_ready=False,
            requires_hardware_ready=False,
        ),
        "construct_pipettes": CommandDefinition(
            name="construct_pipettes",
            enabled=True,
            route_key="construct_pipettes",
            required_capability="construct_pipettes",
        ),
        "initialize_without_motion": CommandDefinition(
            name="initialize_without_motion",
            enabled=True,
            route_key="initialize_without_motion",
            required_capability="initialize_without_motion",
            requires_hardware_ready=False,
        ),
        "run_initial_check": CommandDefinition(
            name="run_initial_check",
            enabled=True,
            route_key="run_initial_check",
            required_capability="run_initial_check",
            requires_hardware_ready=False,
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
