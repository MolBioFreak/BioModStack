from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SingleSequenceInput:
    sequence: str
    name: str
    out_dir: str
    reference_sequence: Optional[str] = None


@dataclass(frozen=True)
class RuntimeOptions:
    db_path: str
    cache_dir: str
    max_age_days: int
    force_refresh: bool
    cache_only: bool
    threads: int
    use_gpu: bool | None
    gpu_id: int | None
    cpu_only: bool
    gpu_mode: str
    gpu_threshold: int
    preferred_gpus: tuple[int, ...]
    excluded_gpus: tuple[int, ...]
    gpu_server_mode: str
    gpu_server_wait_timeout: int
    gpu_server_db_load_mode: int
    gpu_server_startup_wait: float
    disallow_cpu_fallback: bool = False


@dataclass(frozen=True)
class MSAOverrides:
    preset: str
    num_iterations: int | None
    use_env: bool | None
    use_expand: bool | None
    use_filter: bool | None
    evalue: float | None
    sensitivity: float | None
    max_seqs: int | None
    min_seq_id: float | None
    min_coverage: float | None
    taxon_list: str | None
    min_depth_warning: int | None
    min_depth_fail: int | None
    fast_env_fallback_min_depth: int | None


@dataclass(frozen=True)
class SingleMSARequest:
    provider: str
    sequence: SingleSequenceInput
    runtime: RuntimeOptions
    overrides: MSAOverrides


@dataclass(frozen=True)
class BatchSequenceInput:
    name: str
    sequence: str


@dataclass(frozen=True)
class BatchMSARequest:
    sequences: tuple[BatchSequenceInput, ...]
    output_dir: Path
    db_path: Path
    cache_dir: Path | None
    gpu_id: int | None
    reference_sequence: str | None
    force_refresh: bool
    cache_only: bool
    cpu_only: bool
    max_seqs: int | None
    preset: str
    use_expand: int | None
    use_env: int | None
    num_iterations: int | None
    evalue: float | None
    min_seq_id: float | None
    min_coverage: float | None
    taxon_list: str | None
    min_depth_warning: int | None
    min_depth_fail: int | None
    gpu_mode: str | None
    gpu_threshold: int | None
    preferred_gpus: tuple[int, ...]
    excluded_gpus: tuple[int, ...]
    gpu_server_mode: str | None
    gpu_server_wait_timeout: int | None
    gpu_server_db_load_mode: int | None
    gpu_server_startup_wait: float | None
