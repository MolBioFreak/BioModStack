from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.anarcii_runtime import (
    ANARCIIRuntime,
    HostGPU,
    _compute_capability_to_sm_token,
    build_apptainer_exec_command,
    resolve_anarcii_runtime,
)


def test_compute_capability_normalizes_to_sm_token() -> None:
    assert _compute_capability_to_sm_token("8.6") == "sm_86"
    assert _compute_capability_to_sm_token("12.0") == "sm_120"


def test_resolve_anarcii_runtime_prefers_enabled_compatible_gpu(monkeypatch, tmp_path: Path) -> None:
    container_path = tmp_path / "antibody_tools.sif"
    container_path.write_text("stub")

    monkeypatch.setattr(
        "services.anarcii_runtime.get_host_gpus",
        lambda: (
            HostGPU(index=0, name="RTX 5090", compute_capability="12.0", sm_token="sm_120"),
            HostGPU(index=2, name="RTX 3090", compute_capability="8.6", sm_token="sm_86"),
            HostGPU(index=3, name="RTX 3090", compute_capability="8.6", sm_token="sm_86"),
        ),
    )
    monkeypatch.setattr(
        "services.anarcii_runtime.get_container_supported_sm_tokens",
        lambda _path: ("sm_80", "sm_86", "sm_90"),
    )
    monkeypatch.setattr("services.anarcii_runtime._scheduler_disabled_gpu_ids", lambda: {3})

    runtime = resolve_anarcii_runtime(container_path=container_path)

    assert runtime.mode == "gpu"
    assert runtime.gpu_id == 2


def test_resolve_anarcii_runtime_falls_back_to_cpu_without_compatible_gpu(monkeypatch, tmp_path: Path) -> None:
    container_path = tmp_path / "antibody_tools.sif"
    container_path.write_text("stub")

    monkeypatch.setattr(
        "services.anarcii_runtime.get_host_gpus",
        lambda: (
            HostGPU(index=0, name="RTX 5090", compute_capability="12.0", sm_token="sm_120"),
            HostGPU(index=1, name="RTX 5060 Ti", compute_capability="12.0", sm_token="sm_120"),
        ),
    )
    monkeypatch.setattr(
        "services.anarcii_runtime.get_container_supported_sm_tokens",
        lambda _path: ("sm_80", "sm_86", "sm_90"),
    )
    monkeypatch.setattr("services.anarcii_runtime._scheduler_disabled_gpu_ids", lambda: set())

    runtime = resolve_anarcii_runtime(container_path=container_path)

    assert runtime.mode == "cpu"
    assert runtime.gpu_id is None


def test_build_apptainer_exec_command_adds_gpu_env() -> None:
    runtime = ANARCIIRuntime(
        mode="gpu",
        gpu_id=2,
        reason="test",
        container_path=Path("/tmp/antibody_tools.sif"),
    )

    cmd = build_apptainer_exec_command(runtime, ["python3", "-c", "print('ok')"])

    assert cmd[:6] == [
        "apptainer",
        "exec",
        "--nv",
        "--env",
        "CUDA_DEVICE_ORDER=PCI_BUS_ID",
        "--env",
    ]
    assert "CUDA_VISIBLE_DEVICES=2" in cmd
