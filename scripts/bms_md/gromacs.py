from __future__ import annotations

from pathlib import Path


def assert_cuda_enabled(version_output: str) -> None:
    normalized = " ".join(str(version_output).lower().split())
    if "gpu support: cuda" not in normalized:
        raise RuntimeError("production MD requires a CUDA-enabled GROMACS runtime")


def build_mdrun_command(
    *,
    gmx: str,
    deffnm: str,
    gpu_id: str,
    ntmpi: int,
    ntomp: int,
    gpu_offload: str,
    pin: str,
    checkpoint: Path,
    checkpoint_interval_minutes: float | None = None,
) -> list[str]:
    command = [
        gmx,
        "mdrun",
        "-deffnm",
        deffnm,
        "-ntmpi",
        str(ntmpi),
        "-ntomp",
        str(ntomp),
        "-pin",
        pin,
        "-gpu_id",
        str(gpu_id),
        "-cpo",
        str(checkpoint),
    ]
    if gpu_offload == "full":
        command.extend(["-nb", "gpu", "-pme", "gpu", "-bonded", "gpu", "-update", "gpu"])
    elif gpu_offload == "full_forces":
        # Four-site waters such as OPC require virtual sites, which GROMACS
        # 2025.3 cannot update on GPU. Keep every supported force task on GPU
        # while making the CPU coordinate-update boundary explicit.
        command.extend(["-nb", "gpu", "-pme", "gpu", "-bonded", "gpu", "-update", "cpu"])
    elif gpu_offload == "partial":
        command.extend(["-nb", "gpu"])
    elif gpu_offload == "auto":
        pass
    elif gpu_offload == "none":
        command.extend(["-nb", "cpu", "-pme", "cpu", "-bonded", "cpu", "-update", "cpu"])
    else:
        raise ValueError(f"unsupported gpu_offload mode: {gpu_offload}")
    if checkpoint_interval_minutes is not None:
        if checkpoint_interval_minutes <= 0:
            raise ValueError("checkpoint_interval_minutes must be > 0")
        command.extend(["-cpt", str(checkpoint_interval_minutes)])
    if checkpoint.is_file():
        command.extend(["-cpi", str(checkpoint), "-append"])
    return command
