from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InitializeMotorsStep(_StepBase):
    action: Literal["initialize_motors"]


class StartJobStep(_StepBase):
    action: Literal["start_job"]
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class PauseJobStep(_StepBase):
    action: Literal["pause_job"]
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class ResumeJobStep(_StepBase):
    action: Literal["resume_job"]
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class StopJobStep(_StepBase):
    action: Literal["stop_job"]
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class RecoverRuntimeStep(_StepBase):
    action: Literal["recover_runtime"]


ProtocolStep = Annotated[
    InitializeMotorsStep
    | StartJobStep
    | PauseJobStep
    | ResumeJobStep
    | StopJobStep
    | RecoverRuntimeStep,
    Field(discriminator="action"),
]


class BioXpProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=160)
    steps: tuple[ProtocolStep, ...] = Field(min_length=1, max_length=500)


class CompiledBioXpProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: BioXpProtocol
    compiled_hash: str
    validation_status: Literal["validated_offline"] = "validated_offline"
    robot_compatible: None = None
    executable: Literal[False] = False
    required_capabilities: tuple[str, ...]
    blockers: tuple[str, ...]


def compile_protocol(protocol: BioXpProtocol) -> CompiledBioXpProtocol:
    canonical = json.dumps(
        protocol.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    capabilities = tuple(sorted({step.action for step in protocol.steps}))
    return CompiledBioXpProtocol(
        protocol=protocol,
        compiled_hash=digest,
        required_capabilities=capabilities,
        blockers=(
            "Robot compatibility is unknown until the online contract is verified",
            "Normal command mappings are disabled; this protocol is not executable",
        ),
    )
