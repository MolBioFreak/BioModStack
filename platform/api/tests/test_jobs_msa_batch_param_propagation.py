from __future__ import annotations

import json
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import routers.jobs as jobs


def test_build_msa_batch_child_params_preserves_local_runtime_contract() -> None:
    sequences_for_msa = [{"name": "reference_msa", "sequence": "ACDEFGHIK"}]
    child_params = jobs._build_msa_batch_child_params(
        source_params={
            "msa_reference_sequence": "ACDEFGHIK",
            "msa_force_refresh": True,
            "msa_cache_only": True,
            "msa_use_gpu": False,
            "msa_max_seqs": 4096,
            "msa_preset": "balanced",
            "msa_use_expand": 1,
            "msa_use_env": 0,
            "msa_num_iterations": 2,
            "msa_evalue": 0.001,
            "msa_min_seq_id": 0.25,
            "msa_min_coverage": 0.75,
            "msa_taxon_list": "9606,10090",
            "msa_min_depth_warning": 16,
            "msa_min_depth_fail": 4,
            "msa_gpu_mode": "required",
            "msa_gpu_threshold": 55,
            "msa_preferred_gpus": "2,3",
            "msa_excluded_gpus": "0,1",
            "msa_gpu_server_mode": "off",
            "msa_gpu_server_wait_timeout": 11,
            "msa_gpu_server_db_load_mode": 3,
            "msa_gpu_server_startup_wait": 7.5,
            "msa_local_db": "/custom/db",
            "msa_cache_dir": "/custom/cache",
            "msa_threads": 18,
            "msa_target_shard_mode": "required",
            "msa_target_shards": 2,
            "msa_target_shard_min_size_gb": 0,
            "run_frustrampnn": True,
        },
        sequences_for_msa=sequences_for_msa,
    )

    assert child_params["sequences"] == sequences_for_msa
    assert json.loads(child_params["sequences_json"]) == sequences_for_msa
    for key, expected in {
        "reference_sequence": "ACDEFGHIK",
        "msa_force_refresh": True,
        "msa_cache_only": True,
        "msa_use_gpu": False,
        "msa_max_seqs": 4096,
        "msa_preset": "balanced",
        "msa_use_expand": 1,
        "msa_use_env": 0,
        "msa_num_iterations": 2,
        "msa_evalue": 0.001,
        "msa_min_seq_id": 0.25,
        "msa_min_coverage": 0.75,
        "msa_taxon_list": "9606,10090",
        "msa_min_depth_warning": 16,
        "msa_min_depth_fail": 4,
        "msa_gpu_mode": "required",
        "msa_gpu_threshold": 55,
        "msa_preferred_gpus": "2,3",
        "msa_excluded_gpus": "0,1",
        "msa_gpu_server_mode": "off",
        "msa_gpu_server_wait_timeout": 11,
        "msa_gpu_server_db_load_mode": 3,
        "msa_gpu_server_startup_wait": 7.5,
        "msa_local_db": "/custom/db",
        "msa_cache_dir": "/custom/cache",
        "msa_threads": 18,
        "msa_target_shard_mode": "required",
        "msa_target_shards": 2,
        "msa_target_shard_min_size_gb": 0,
        "run_frustrampnn_batch": True,
    }.items():
        assert child_params[key] == expected
