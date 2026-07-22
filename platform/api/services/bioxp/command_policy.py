from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    startup_lifecycle: dict[str, Any] | None = None


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
    if definition.requires_runtime_inactive and context.runtime_ready is True:
        reasons.append("USB runtime is already active for the managed service")
    if definition.required_capability is not None and definition.required_capability not in context.capabilities:
        reasons.append(f"Required capability is unavailable: {definition.required_capability}")
    reasons.extend(required_lifecycle_state_reasons(definition, context.startup_lifecycle))
    reasons.extend(lifecycle_stage_reasons(definition, context.startup_lifecycle))
    return ControlDecision(allowed=not reasons, reasons=tuple(reasons))


def required_lifecycle_state_reasons(
    definition: CommandDefinition,
    startup_lifecycle: dict[str, Any] | None,
) -> tuple[str, ...]:
    if not definition.required_lifecycle_states:
        return ()
    stages = ((startup_lifecycle or {}).get("stages") or {})
    reasons: list[str] = []
    for name, expected in definition.required_lifecycle_states:
        stage = stages.get(name)
        if not isinstance(stage, dict):
            reasons.append(f"Startup lifecycle stage evidence is malformed or missing: {name}")
            continue
        actual = stage.get("state")
        if actual != expected:
            reasons.append(
                f"OEM startup requires a fresh ownership epoch: {name} must be {expected}, got {actual!r}"
            )
    return tuple(reasons)


def lifecycle_stage_reasons(
    definition: CommandDefinition,
    startup_lifecycle: dict[str, Any] | None,
) -> tuple[str, ...]:
    if definition.lifecycle_stage is None:
        return ()
    stages = ((startup_lifecycle or {}).get("stages") or {})
    stage = stages.get(definition.lifecycle_stage)
    if not isinstance(stage, dict):
        return (f"Startup lifecycle stage evidence is malformed or missing: {definition.lifecycle_stage}",)
    state = stage.get("state")
    if state not in {"blocked", "not_run", "running", "passed", "failed"}:
        return (
            f"Startup lifecycle stage has unknown state for {definition.lifecycle_stage}: {state!r}",
        )
    if state == "blocked":
        return (f"Robot startup stage {definition.lifecycle_stage} is blocked by its predecessor",)
    if state == "running":
        return (f"Robot startup stage {definition.lifecycle_stage} is already running",)
    if state == "passed" and not definition.repeatable:
        return (f"Robot startup stage {definition.lifecycle_stage} already passed in this ownership epoch",)
    return ()
