from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


ENGINE_ADAPTER_ERROR = "MD_ENGINE_ADAPTER_ERROR"
ENGINE_UNAVAILABLE = "MD_ENGINE_UNAVAILABLE"


class EngineAdapterError(RuntimeError):
    code = ENGINE_ADAPTER_ERROR


class EngineUnavailableError(EngineAdapterError):
    code = ENGINE_UNAVAILABLE


@dataclass(frozen=True)
class ReplicaRequest:
    config_path: Path
    output_dir: Path
    replica_index: int


class EngineAdapter(Protocol):
    name: str

    def run(self, request: ReplicaRequest) -> Path: ...
