"""Provider-neutral remote execution package."""

from .contracts import (
    DiscoveredExecutionTarget,
    ExecutionTargetActivateRequest,
    ExecutionTargetInventoryResponse,
    ExecutionTargetResponse,
    RemoteAttemptStatus,
    RemoteExecutionEnvelope,
    RemoteFileRecord,
    RemoteResultManifest,
)

__all__ = [
    "DiscoveredExecutionTarget",
    "ExecutionTargetActivateRequest",
    "ExecutionTargetInventoryResponse",
    "ExecutionTargetResponse",
    "RemoteAttemptStatus",
    "RemoteExecutionEnvelope",
    "RemoteFileRecord",
    "RemoteResultManifest",
]
