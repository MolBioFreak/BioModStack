from __future__ import annotations

import json
import os
from pathlib import Path

from ..contract import normalize_job_config
from ..cuda_contract import assert_single_cuda_device
from ..gromacs_pipeline import run_gromacs_job
from .base import EngineAdapterError, ReplicaRequest


class GromacsAdapter:
    name = "gromacs"

    def run(self, request: ReplicaRequest) -> Path:
        config = normalize_job_config(json.loads(request.config_path.read_text(encoding="utf-8")))
        if config["engine"] != self.name:
            raise EngineAdapterError("MD_ENGINE_ADAPTER_ERROR: GROMACS adapter received another engine")
        allocation = assert_single_cuda_device(config)
        manifest_path = run_gromacs_job(
            request.config_path,
            request.output_dir,
            replica_index=request.replica_index,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["engine"]["allocation"] = allocation
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, manifest_path)
        return manifest_path
