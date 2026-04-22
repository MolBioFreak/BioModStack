from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable, Mapping

from ..runtime import parse_gpu_csv
from ..types import MSAOverrides, RuntimeOptions, SingleMSARequest, SingleSequenceInput


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def build_single_request_from_namespace(args: Namespace) -> SingleMSARequest:
    return SingleMSARequest(
        provider=str(args.msa_provider or "local"),
        sequence=SingleSequenceInput(
            sequence=str(args.sequence),
            name=str(args.name),
            out_dir=str(args.out_dir),
            reference_sequence=str(args.reference_sequence) if args.reference_sequence is not None else None,
        ),
        runtime=RuntimeOptions(
            db_path=str(args.db_path),
            cache_dir=str(args.cache_dir),
            max_age_days=int(args.max_age_days),
            force_refresh=bool(args.force_refresh),
            cache_only=bool(args.cache_only),
            threads=int(args.threads),
            use_gpu=True if bool(args.use_gpu) else None,
            gpu_id=int(args.gpu_id) if args.gpu_id is not None else None,
            cpu_only=bool(args.cpu_only),
            gpu_mode=str(args.gpu_mode or "auto"),
            gpu_threshold=int(args.gpu_threshold),
            preferred_gpus=tuple(parse_gpu_csv(args.preferred_gpus) or ()),
            excluded_gpus=tuple(parse_gpu_csv(args.excluded_gpus) or ()),
            gpu_server_mode=str(args.gpu_server_mode or "persistent"),
            gpu_server_wait_timeout=int(args.gpu_server_wait_timeout),
            gpu_server_db_load_mode=int(args.gpu_server_db_load_mode),
            gpu_server_startup_wait=float(args.gpu_server_startup_wait),
            disallow_cpu_fallback=bool(args.disallow_cpu_fallback),
        ),
        overrides=MSAOverrides(
            preset=str(args.preset),
            num_iterations=int(args.num_iterations) if args.num_iterations is not None else None,
            use_env=_optional_bool(args.use_env),
            use_expand=_optional_bool(args.use_expand),
            use_filter=_optional_bool(args.use_filter),
            evalue=float(args.evalue) if args.evalue is not None else None,
            sensitivity=float(args.sensitivity) if args.sensitivity is not None else None,
            max_seqs=int(args.max_seqs) if args.max_seqs is not None else None,
            min_seq_id=float(args.min_seq_id) if args.min_seq_id is not None else None,
            min_coverage=float(args.min_coverage) if args.min_coverage is not None else None,
            taxon_list=str(args.taxon_list) if args.taxon_list is not None else None,
            min_depth_warning=int(args.min_depth_warning) if args.min_depth_warning is not None else None,
            min_depth_fail=int(args.min_depth_fail) if args.min_depth_fail is not None else None,
            fast_env_fallback_min_depth=int(args.fast_env_fallback_min_depth)
            if args.fast_env_fallback_min_depth is not None
            else None,
        ),
    )


def dispatch_single_request(
    request: SingleMSARequest,
    *,
    local_executor: Callable[..., Any],
    colabfold_api_executor: Callable[..., Any],
    colabfold_api_options: Mapping[str, Any] | None = None,
) -> Any:
    common_kwargs = dict(
        sequence=request.sequence.sequence,
        job_name=request.sequence.name,
        out_dir=request.sequence.out_dir,
        db_path=request.runtime.db_path,
        cache_dir=request.runtime.cache_dir,
        max_age_days=request.runtime.max_age_days,
        force_refresh=request.runtime.force_refresh,
        cache_only=request.runtime.cache_only,
        num_threads=request.runtime.threads,
        use_gpu=request.runtime.use_gpu,
        gpu_id=request.runtime.gpu_id,
        cpu_only=request.runtime.cpu_only,
        gpu_mode=request.runtime.gpu_mode,
        gpu_threshold=request.runtime.gpu_threshold,
        preferred_gpus=list(request.runtime.preferred_gpus) if request.runtime.preferred_gpus else None,
        excluded_gpus=list(request.runtime.excluded_gpus) if request.runtime.excluded_gpus else None,
        gpu_server_mode=request.runtime.gpu_server_mode,
        gpu_server_wait_timeout=request.runtime.gpu_server_wait_timeout,
        gpu_server_db_load_mode=request.runtime.gpu_server_db_load_mode,
        gpu_server_startup_wait=request.runtime.gpu_server_startup_wait,
        reference_sequence=request.sequence.reference_sequence,
        preset=request.overrides.preset,
        num_iterations=request.overrides.num_iterations,
        use_env=request.overrides.use_env,
        use_expand=request.overrides.use_expand,
        use_filter=request.overrides.use_filter,
        evalue=request.overrides.evalue,
        sensitivity=request.overrides.sensitivity,
        max_seqs=request.overrides.max_seqs,
        min_seq_id=request.overrides.min_seq_id,
        min_coverage=request.overrides.min_coverage,
        taxon_list=request.overrides.taxon_list,
        min_depth_warning=request.overrides.min_depth_warning,
        min_depth_fail=request.overrides.min_depth_fail,
        fast_env_fallback_min_depth=request.overrides.fast_env_fallback_min_depth,
    )
    if request.provider == "colabfold_api":
        extra = dict(colabfold_api_options or {})
        return colabfold_api_executor(**common_kwargs, **extra)
    return local_executor(
        **common_kwargs,
        disallow_cpu_fallback=request.runtime.disallow_cpu_fallback,
    )
