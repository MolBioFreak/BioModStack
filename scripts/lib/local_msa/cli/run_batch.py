from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..runtime import parse_gpu_csv
from ..types import BatchMSARequest, BatchSequenceInput


def _serialize_gpu_csv(values: tuple[int, ...]) -> str | None:
    if not values:
        return None
    return ",".join(str(value) for value in values)


def parse_sequences_json(raw: str) -> tuple[BatchSequenceInput, ...]:
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("--sequences must decode to a JSON list")
    items: list[BatchSequenceInput] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each sequence entry must be an object")
        name = str(item.get("name") or "").strip()
        sequence = str(item.get("sequence") or "").strip()
        if not name or not sequence:
            raise ValueError("Each sequence entry must include name and sequence")
        items.append(BatchSequenceInput(name=name, sequence=sequence))
    return tuple(items)


def build_batch_request(
    *,
    sequences: tuple[BatchSequenceInput, ...],
    output_dir: Path,
    db_path: Path,
    cache_dir: Path | None,
    gpu_id: int | None,
    reference_sequence: str | None,
    force_refresh: bool,
    cache_only: bool,
    cpu_only: bool,
    max_seqs: int | None,
    preset: str,
    use_expand: int | None,
    use_env: int | None,
    num_iterations: int | None,
    evalue: float | None,
    min_seq_id: float | None,
    min_coverage: float | None,
    taxon_list: str | None,
    min_depth_warning: int | None,
    min_depth_fail: int | None,
    gpu_mode: str | None,
    gpu_threshold: int | None,
    preferred_gpus: str | None,
    excluded_gpus: str | None,
    gpu_server_mode: str | None,
    gpu_server_wait_timeout: int | None,
    gpu_server_db_load_mode: int | None,
    gpu_server_startup_wait: float | None,
) -> BatchMSARequest:
    return BatchMSARequest(
        sequences=tuple(sequences),
        output_dir=Path(output_dir),
        db_path=Path(db_path),
        cache_dir=Path(cache_dir) if cache_dir else None,
        gpu_id=int(gpu_id) if gpu_id is not None else None,
        reference_sequence=reference_sequence,
        force_refresh=bool(force_refresh),
        cache_only=bool(cache_only),
        cpu_only=bool(cpu_only),
        max_seqs=int(max_seqs) if max_seqs is not None else None,
        preset=str(preset),
        use_expand=int(use_expand) if use_expand is not None else None,
        use_env=int(use_env) if use_env is not None else None,
        num_iterations=int(num_iterations) if num_iterations is not None else None,
        evalue=float(evalue) if evalue is not None else None,
        min_seq_id=float(min_seq_id) if min_seq_id is not None else None,
        min_coverage=float(min_coverage) if min_coverage is not None else None,
        taxon_list=str(taxon_list) if taxon_list is not None else None,
        min_depth_warning=int(min_depth_warning) if min_depth_warning is not None else None,
        min_depth_fail=int(min_depth_fail) if min_depth_fail is not None else None,
        gpu_mode=str(gpu_mode) if gpu_mode is not None else None,
        gpu_threshold=int(gpu_threshold) if gpu_threshold is not None else None,
        preferred_gpus=tuple(parse_gpu_csv(preferred_gpus) or ()),
        excluded_gpus=tuple(parse_gpu_csv(excluded_gpus) or ()),
        gpu_server_mode=str(gpu_server_mode) if gpu_server_mode is not None else None,
        gpu_server_wait_timeout=int(gpu_server_wait_timeout) if gpu_server_wait_timeout is not None else None,
        gpu_server_db_load_mode=int(gpu_server_db_load_mode) if gpu_server_db_load_mode is not None else None,
        gpu_server_startup_wait=float(gpu_server_startup_wait) if gpu_server_startup_wait is not None else None,
    )


def dispatch_batch_request(request: BatchMSARequest, *, executor: Callable[..., Any]) -> Any:
    return executor(
        sequences=[{"name": item.name, "sequence": item.sequence} for item in request.sequences],
        output_dir=request.output_dir,
        db_path=request.db_path,
        cache_dir=request.cache_dir,
        gpu_id=request.gpu_id,
        reference_sequence=request.reference_sequence,
        force_refresh=request.force_refresh,
        cache_only=request.cache_only,
        cpu_only=request.cpu_only,
        max_seqs=request.max_seqs,
        preset=request.preset,
        use_expand=request.use_expand,
        use_env=request.use_env,
        num_iterations=request.num_iterations,
        evalue=request.evalue,
        min_seq_id=request.min_seq_id,
        min_coverage=request.min_coverage,
        taxon_list=request.taxon_list,
        min_depth_warning=request.min_depth_warning,
        min_depth_fail=request.min_depth_fail,
        gpu_mode=request.gpu_mode,
        gpu_threshold=request.gpu_threshold,
        preferred_gpus=_serialize_gpu_csv(request.preferred_gpus),
        excluded_gpus=_serialize_gpu_csv(request.excluded_gpus),
        gpu_server_mode=request.gpu_server_mode,
        gpu_server_wait_timeout=request.gpu_server_wait_timeout,
        gpu_server_db_load_mode=request.gpu_server_db_load_mode,
        gpu_server_startup_wait=request.gpu_server_startup_wait,
    )
