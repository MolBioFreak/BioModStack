from __future__ import annotations

from pathlib import Path

from ..contract import prepare_verified_worker_inputs
from ..cuda_contract import assert_single_cuda_device
from ..openmm_pipeline import run_openmm_job
from .base import EngineAdapterError, ReplicaRequest


class OpenMMAdapter:
    name = "openmm"

    def run(self, request: ReplicaRequest) -> Path:
        config = prepare_verified_worker_inputs(
            request.config_path,
            request.output_dir / ".worker_inputs",
        )
        if config["engine"] != self.name:
            raise EngineAdapterError("MD_ENGINE_ADAPTER_ERROR: OpenMM adapter received another engine")
        inputs = config["input"]
        if not inputs.get("coordinates") or not inputs.get("topology"):
            raise EngineAdapterError(
                "MD_ENGINE_ADAPTER_ERROR: experimental OpenMM requires prepared coordinates plus topology"
            )
        unsupported = [
            name
            for name in ("minimization", "nvt", "npt")
            if config["stages"][name]["enabled"]
        ]
        if unsupported:
            raise EngineAdapterError(
                "MD_ENGINE_ADAPTER_ERROR: experimental OpenMM is production-only; disable "
                + ", ".join(unsupported)
            )
        if not config["stages"]["production"]["enabled"]:
            raise EngineAdapterError(
                "MD_ENGINE_ADAPTER_ERROR: experimental OpenMM requires production enabled"
            )
        assert_single_cuda_device(config)
        return run_openmm_job(
            request.config_path,
            request.output_dir,
            replica_index=request.replica_index,
            _prepared_config=config,
        )
