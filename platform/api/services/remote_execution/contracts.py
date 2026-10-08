"""Strict wire contracts for provider-neutral remote execution."""
from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from schemas import JobCreate

SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _clean_relative_posix_path(value: str, *, field: str) -> str:
    candidate = PurePosixPath(value)
    if (
        not value
        or value != candidate.as_posix()
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"{field} must be one clean relative POSIX path")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DiscoveredExecutionTarget(StrictModel):
    provider: Literal["vast"]
    provider_instance_id: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=255)
    provider_state: str = Field(min_length=1, max_length=64)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=64)
    gpu_name: str | None = Field(default=None, max_length=255)
    gpu_count: int = Field(default=0, ge=0, le=64)
    gpu_vram_mb: int | None = Field(default=None, ge=0)
    hourly_rate_usd: float | None = Field(default=None, ge=0)
    started_at: datetime | None = None
    verified: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ExecutionTargetSetup(StrictModel):
    phase: Literal["checking", "installing", "transferring", "verifying", "ready", "failed"]
    message: str
    started_at: datetime | None = None
    updated_at: datetime | None = None


class RemoteWorkflowActivity(StrictModel):
    stage: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_. -]+$")
    state: Literal["started", "completed", "failed"]
    updated_at: datetime


class RemoteArtifactProgress(StrictModel):
    operation_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    phase: Literal["checking", "transferring", "verifying", "running", "completed", "failed"]
    artifact: str | None = Field(default=None, max_length=256)
    message: str = Field(min_length=1, max_length=500)
    updated_at: datetime
    activity: RemoteWorkflowActivity | None = None


class PreloadRequest(StrictModel):
    job_id: str = Field(min_length=1, max_length=64)


class ProvisionSelection(StrictModel):
    kind: Literal["model", "image"]
    model_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")


from services.workflow_request_types import SubmitRequest
from services.md.starting_structures import MdLaunchPreviewRequest


class ConformationalMappingProvisionWorkflow(StrictModel):
    workflow_type: Literal["conformational_mapping"]
    request: SubmitRequest

    # Persist/replay an actual caller request, not server-authored provenance.
    # SubmitRequest's native validator deliberately rejects supplied origin.
    @field_serializer("request")
    def serialize_request(self, value):
        excluded = ({"frustrampnn_settings": {"settings_value_origin"}}
                    if "frustrampnn_settings" in value.model_fields_set else {"frustrampnn_settings": True})
        return value.model_dump(mode="json", exclude=excluded)


class MolecularDynamicsProvisionWorkflow(StrictModel):
    workflow_type: Literal["molecular_dynamics"]
    request: MdLaunchPreviewRequest


class WorkflowProvisionSelection(StrictModel):
    kind: Literal["workflow"]
    workflow_request: JobCreate | ConformationalMappingProvisionWorkflow | MolecularDynamicsProvisionWorkflow

    @field_validator("workflow_request", mode="before")
    @classmethod
    def closed_workflow_request(cls, value):
        if isinstance(value, dict) and "workflow_type" in value:
            native_type = {
                "conformational_mapping": ConformationalMappingProvisionWorkflow,
                "molecular_dynamics": MolecularDynamicsProvisionWorkflow,
            }.get(value["workflow_type"])
            if native_type is None:
                raise ValueError("Unsupported native workflow type")
            return native_type.model_validate(value)
        if isinstance(value, dict) and set(value) - set(JobCreate.model_fields):
            raise ValueError("Workflow provisioning accepts only existing JobCreate fields")
        return value


class WorkflowRuntimeSelection(StrictModel):
    """Non-biological workflow identity sent to the managed asset helper."""
    kind: Literal["workflow"]
    model_id: str = Field(pattern=SHA256_PATTERN)


class CriticalRuntimeSelection(StrictModel):
    kind: Literal["critical_runtime"] = "critical_runtime"
    model_id: Literal["worker"] = "worker"


class CriticalRuntimeCompatibility(StrictModel):
    requirements: dict[str, str]
    observed: dict[str, str]
    compatible: bool


class ProvisionRequest(ProvisionSelection):
    preview_sha256: str = Field(pattern=SHA256_PATTERN)


class WorkflowProvisionRequest(WorkflowProvisionSelection):
    preview_sha256: str = Field(pattern=SHA256_PATTERN)


class CachedArtifactReceipt(StrictModel):
    name: str = Field(min_length=1, max_length=2000)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        return _clean_relative_posix_path(value, field="name")


class ProvisionAssetState(CachedArtifactReceipt):
    state: Literal["verified", "missing", "corrupt", "incompatible", "unknown"]


class ProvisionArtifactProgress(CachedArtifactReceipt):
    state: Literal["pending", "transferring", "verifying", "verified", "interrupted"]


class ProvisionDestination(StrictModel):
    target_id: str
    remote_root: str


class ProvisionPreview(StrictModel):
    selection: ProvisionSelection | WorkflowProvisionSelection
    preview_sha256: str = Field(pattern=SHA256_PATTERN)
    artifacts: list[CachedArtifactReceipt]
    total_bytes: int = Field(ge=0)
    scientific_ready: Literal[False] = False
    scope: Literal["managed_asset_activation"] = "managed_asset_activation"
    destination: ProvisionDestination | None = None
    effective_params: dict[str, Any] | None = None
    plan_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    asset_states: list[ProvisionAssetState] = Field(default_factory=list)
    transfer_bytes: int = Field(default=0, ge=0)
    storage_bytes: int = Field(default=0, ge=0)
    inventory_state: Literal["current", "stale", "unobserved"] = "unobserved"
    blockers: list[str] = Field(default_factory=list)


class PreloadProgress(StrictModel):
    operation_id: str
    job_id: str | None = None
    selection: ProvisionSelection | WorkflowProvisionSelection | None = None
    artifact_progress: list[ProvisionArtifactProgress] = Field(default_factory=list)
    sequence: int = Field(default=0, ge=0)
    endpoint_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    cancel_requested: bool = False
    recovery_required: bool = False
    artifacts: list[CachedArtifactReceipt] = Field(default_factory=list)
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    phase: Literal["checking", "transferring", "verifying", "source_download_ready", "failed", "cancelling", "recovery_blocked", "cancelled"]
    artifact: str | None = Field(default=None, max_length=256)
    message: str = Field(min_length=1, max_length=500)
    started_at: datetime
    updated_at: datetime


class ObservedArtifactInventory(StrictModel):
    operation_id: str
    selection: ProvisionSelection | WorkflowProvisionSelection
    observed_at: datetime
    artifacts: list[CachedArtifactReceipt]
    scope: Literal["last_independent_provision"] = "last_independent_provision"
    state: Literal["download_verified", "stale"] = "stale"
    scientific_ready: Literal[False] = False


class ExecutionTargetResponse(StrictModel):
    artifact_inventory: ObservedArtifactInventory | None = None
    setup: ExecutionTargetSetup | None = None
    preload: PreloadProgress | None = None
    progress: RemoteArtifactProgress | None = None
    id: str
    provider: Literal["vast"]
    provider_instance_id: str
    name: str | None
    state: Literal["discovered", "probing", "ready", "unavailable", "inactive"]
    active: bool
    host: str | None
    port: int | None
    username: str | None
    remote_root: str
    host_key_sha256: str | None
    capabilities: dict[str, Any]
    pricing: dict[str, Any]
    last_error: str | None
    last_seen_at: datetime | None
    activated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExecutionTargetInventoryResponse(StrictModel):
    provider: Literal["vast"]
    available: bool
    credential_configured: bool
    message: str
    instances: list[DiscoveredExecutionTarget]


class ExecutionTargetActivateRequest(StrictModel):
    provider: Literal["vast"] = "vast"
    provider_instance_id: str = Field(min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=64)
    remote_root: str = Field(default="/opt/biomodstack", min_length=1, max_length=500)

    @field_validator("remote_root")
    @classmethod
    def validate_remote_root(cls, value: str) -> str:
        candidate = PurePosixPath(value)
        if (
            not candidate.is_absolute()
            or candidate == PurePosixPath("/")
            or value != candidate.as_posix()
            or any(part in {".", ".."} for part in candidate.parts)
            or any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in candidate.parts[1:])
        ):
            raise ValueError("remote_root must be an absolute normalized path")
        return value


class RemoteFileRecord(StrictModel):
    relative_path: str = Field(min_length=1, max_length=2000)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)
    role: Literal["source", "input", "runtime", "result", "log", "receipt"]
    link_target: str | None = Field(default=None, max_length=2000)
    mode: int = Field(default=0o644, ge=0, le=0o777, strict=True)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _clean_relative_posix_path(value, field="relative_path")


class RemoteExecutionEnvelope(StrictModel):
    schema_name: Literal["bms.remote-execution.v1"] = Field(
        default="bms.remote-execution.v1", alias="schema", serialization_alias="schema"
    )
    job_id: str
    root_job_id: str
    parent_job_id: str | None
    attempt_id: str
    execution_target_id: str
    source_revision: str
    source_tree: str
    source_archive_sha256: str = Field(pattern=SHA256_PATTERN)
    command: list[str] = Field(min_length=1)
    working_directory: str
    environment: dict[str, str] = Field(default_factory=dict)
    output_directory: str
    resource_monitor: dict[str, Any] | None = None
    expected_result_contract: dict[str, Any]
    path_map: dict[str, str]
    files: list[RemoteFileRecord]
    created_at: datetime


class RemoteAttemptStatus(StrictModel):
    generation: int = Field(default=0, ge=0, strict=True)
    native_output_directory: str | None = None
    control_group: str | None = None
    plan_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    continuation_lease_id: str | None = None
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    activity: RemoteWorkflowActivity | None = None
    boot_id: str | None = None
    quiescent: bool = False
    schema_name: Literal["bms.remote-attempt-status.v1"] = Field(
        default="bms.remote-attempt-status.v1", alias="schema", serialization_alias="schema"
    )
    attempt_id: str
    job_id: str
    state: Literal[
        "prepared", "running", "awaiting_input", "cancelling", "cancelled", "succeeded", "failed", "lost"
    ]
    supervisor_pid: int | None = None
    supervisor_start_ticks: int | None = None
    workflow_pid: int | None = None
    workflow_start_ticks: int | None = None
    exit_code: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_manifest_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    error: str | None = Field(default=None, max_length=4000)


class RemoteResultManifest(StrictModel):
    generation: int = Field(default=0, ge=0, strict=True)
    schema_name: Literal["bms.remote-result-manifest.v1"] = Field(
        default="bms.remote-result-manifest.v1", alias="schema", serialization_alias="schema"
    )
    attempt_id: str
    job_id: str
    exit_code: int
    completed_at: datetime
    artifacts: list[RemoteFileRecord]
    source_revision: str
    source_tree: str
    execution_envelope_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_unique_artifacts(self) -> "RemoteResultManifest":
        paths = [artifact.relative_path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifacts contains duplicate relative_path values")
        return self
