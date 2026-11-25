#!/usr/bin/env python3
"""Thin CLI entrypoint over scripts/lib/local_msa for ColabFold API MSA retrieval."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from biomodstack_msa_policy import reject_local_search  # noqa: E402
from local_msa.cache import (  # noqa: E402
    _cleanup_profile_caches,
    _count_cached_depth,
    _is_cache_fresh,
    _normalize_taxon_filter,
    acquire_msa_lock,
    build_cache_profile,
    check_cache,
    compute_sequence_hash,
    get_cache_path,
    get_legacy_cache_path,
    get_msa_lock_path,
    get_single_cache_path,
    load_from_cache,
    release_msa_lock,
    save_to_cache,
)
from local_msa.cli.args import build_arg_parser  # noqa: E402
from local_msa.cli.run_single import (  # noqa: E402
    build_single_request_from_namespace,
    dispatch_single_request,
)
from local_msa.config import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_COLABFOLD_API_HOST,
    DEFAULT_COLABFOLD_API_USER_AGENT,
    DEFAULT_DB_PATH,
    MSA_PRESETS,
)
from local_msa.providers.colabfold_api import (  # noqa: E402
    _entries_to_a3m,
    _extract_tar_archive_safely,
    _http_download_file,
    _http_get_json,
    _http_post_form_json,
    _merge_colabfold_a3m_contents,
    _normalize_colabfold_host,
    _postfilter_a3m_by_taxonomy,
    _read_a3m_entries,
    _resolve_colabfold_api_mode,
    _run_colabfold_api_search,
    _wait_for_colabfold_submit_slot,
    run_colabfold_api_msa_workflow,
    sanitize_a3m_for_boltz,
)
from local_msa.runtime import inspect_mmseqs_runtime, parse_gpu_csv  # noqa: E402

if TYPE_CHECKING:
    from lib.local_msa.cli.run_single import dispatch_single_request as _dispatch_single_request_ast_check


def run_colabfold_msa_workflow(*_args: Any, **_kwargs: Any) -> Any:
    """Local MMseqs2 MSA execution has been removed; fail closed before any IO."""
    reject_local_search()


# Backward compatibility alias (also fails closed)
run_local_mmseqs2 = run_colabfold_msa_workflow


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    request = build_single_request_from_namespace(args)
    if request.provider != "colabfold_api":
        reject_local_search()

    os.makedirs(args.out_dir, exist_ok=True)
    dispatch_single_request(
        request,
        local_executor=run_colabfold_msa_workflow,
        colabfold_api_executor=run_colabfold_api_msa_workflow,
        colabfold_api_options={
            "colabfold_api_host": args.colabfold_api_host,
            "colabfold_api_min_interval": args.colabfold_api_min_interval,
            "colabfold_api_poll_interval": args.colabfold_api_poll_interval,
        },
    )
    return 0


__all__ = [
    "DEFAULT_CACHE_DIR",
    "DEFAULT_COLABFOLD_API_HOST",
    "DEFAULT_COLABFOLD_API_USER_AGENT",
    "DEFAULT_DB_PATH",
    "MSA_PRESETS",
    "_cleanup_profile_caches",
    "_count_cached_depth",
    "_entries_to_a3m",
    "_extract_tar_archive_safely",
    "_http_download_file",
    "_http_get_json",
    "_http_post_form_json",
    "_is_cache_fresh",
    "_merge_colabfold_a3m_contents",
    "_normalize_colabfold_host",
    "_normalize_taxon_filter",
    "_postfilter_a3m_by_taxonomy",
    "_read_a3m_entries",
    "_resolve_colabfold_api_mode",
    "_run_colabfold_api_search",
    "_wait_for_colabfold_submit_slot",
    "acquire_msa_lock",
    "build_arg_parser",
    "build_cache_profile",
    "check_cache",
    "compute_sequence_hash",
    "get_cache_path",
    "get_legacy_cache_path",
    "get_msa_lock_path",
    "get_single_cache_path",
    "inspect_mmseqs_runtime",
    "load_from_cache",
    "main",
    "parse_gpu_csv",
    "release_msa_lock",
    "run_colabfold_api_msa_workflow",
    "run_colabfold_msa_workflow",
    "run_local_mmseqs2",
    "sanitize_a3m_for_boltz",
    "save_to_cache",
]


if __name__ == "__main__":
    raise SystemExit(main())
