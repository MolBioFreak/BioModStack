from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .adapters import (
    EngineAdapter,
    EngineAdapterError,
    EngineUnavailableError,
    GromacsAdapter,
    OpenMMAdapter,
    ReplicaRequest,
)
from .contract import load_verified_job_config


_ADAPTERS: Mapping[str, EngineAdapter] = {
    "gromacs": GromacsAdapter(),
    "openmm": OpenMMAdapter(),
}


def get_engine_adapter(engine: str) -> EngineAdapter:
    name = str(engine).strip().lower()
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        raise EngineUnavailableError(f"MD_ENGINE_UNAVAILABLE: no MD adapter registered for {name}") from exc


def run_md_replica(config_path: Path, output_dir: Path, *, replica_index: int = 0) -> Path:
    config_path = Path(config_path).expanduser().resolve()
    config = load_verified_job_config(config_path)
    adapter = get_engine_adapter(config["engine"])
    return adapter.run(
        ReplicaRequest(
            config_path=config_path,
            output_dir=Path(output_dir).expanduser().resolve(),
            replica_index=replica_index,
        )
    )


__all__ = [
    "EngineAdapterError",
    "EngineUnavailableError",
    "get_engine_adapter",
    "run_md_replica",
]
