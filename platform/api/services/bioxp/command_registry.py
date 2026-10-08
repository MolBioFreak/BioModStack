from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

CommandName = Literal[
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
    }
)
