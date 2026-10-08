from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BioXpProfile(BaseModel):
    """The only persisted BMS-side BioXP configuration schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    display_name: str = Field(default="BioXP3200", min_length=1, max_length=120)
    api_url: str = Field(min_length=1, max_length=2048)


class ControlDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reasons: tuple[str, ...] = ()


class CommandRecord(BaseModel):
    """Bounded, non-authoritative local history for one normal command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str
    command: str
    idempotency_key: str
    generation: int
    status: Literal["acknowledged", "delivered_unacknowledged", "delivery_failed"]
    started_at: datetime
    finished_at: datetime
    remote_acknowledged: bool
    physical_effect_verified: Literal[False] = False
    detail: str
    handler_response: dict[str, Any] | None = None


class EmergencyStopResult(BaseModel):
    """Delivery evidence only; never represents verified physical stop state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str
    generation: int
    attempted_at: datetime
    delivery_attempted: bool
    remote_acknowledged: bool
    physical_effect_verified: Literal[False] = False
    detail: str


class BioXpSnapshot(BaseModel):
    """Orthogonal state; unknown facts are represented by ``None``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    configured: bool
    display_name: str | None = None
    masked_target: str | None = None
    active: bool
    generation: int = Field(ge=0)
    reachable: bool | None = None
    runtime_ready: bool | None = None
    hardware_ready: bool | None = None
    hardware_observed_at: datetime | None = None
    hardware_observation_fresh: bool | None = None
    hardware_observation_stale: bool = False
    hardware_evidence_error: str | None = None
    capabilities: tuple[str, ...] = ()
    observed_at: datetime | None = None
    freshness_budget_seconds: float = Field(gt=0)
    observation_fresh: bool | None = None
    observation_stale: bool = False
    last_observed_reachable: bool | None = None
    last_observed_runtime_ready: bool | None = None
    last_observed_hardware_ready: bool | None = None
    last_error: str | None = None
    controls: dict[str, ControlDecision] = Field(default_factory=dict)
    command_active: bool = False
    startup_lifecycle: dict[str, Any] | None = None


class RobotObservation(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    runtime_ready: bool | None = None
    hardware_ready: bool | None = None
    capabilities: tuple[str, ...] = ()
    raw: dict[str, Any] = Field(default_factory=dict)
