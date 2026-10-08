from __future__ import annotations

from dataclasses import dataclass

from .command_models import CommandRequest
from .command_registry import CommandDefinition
from .models import ControlDecision


@dataclass(frozen=True)
class CommandAdmissionContext:
    mutations_enabled: bool
    active: bool
    generation: int
    observation_fresh: bool | None
    runtime_ready: bool | None
    hardware_ready: bool | None
    capabilities: frozenset[str]


def evaluate_command(
    request: CommandRequest,
    definition: CommandDefinition,
    context: CommandAdmissionContext,
) -> ControlDecision:
    reasons: list[str] = []
    if not context.mutations_enabled:
        reasons.append("BioXP mutations are disabled by the server kill switch")
    if not context.active:
        reasons.append("An active target connection is required")
    if request.expected_generation != context.generation:
        reasons.append("Expected connection generation does not match the active generation")
    if not definition.enabled or definition.route_key is None:
        reasons.append(definition.disabled_reason or "Command mapping is disabled")
    if definition.requires_fresh_observation and context.observation_fresh is not True:
        reasons.append("A fresh robot observation is required")
    if definition.requires_runtime_ready and context.runtime_ready is not True:
        reasons.append("Runtime readiness must be known and ready")
    if definition.requires_hardware_ready and context.hardware_ready is not True:
        reasons.append("Hardware readiness must be known and ready")
    if definition.required_capability not in context.capabilities:
        reasons.append(f"Required capability is unavailable: {definition.required_capability}")
    return ControlDecision(allowed=not reasons, reasons=tuple(reasons))
