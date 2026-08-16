from __future__ import annotations

import json
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import routers.jobs as jobs
from services import nextflow as nextflow_service


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
    }.items():
        assert child_params[key] == expected
    assert "run_frustrampnn_batch" not in child_params


def test_msa_child_params_drop_retired_scoring_ownership_metadata() -> None:
    owner_key = "_frustrampnn_execution_" + "owner_v1"
    params = jobs._build_msa_batch_child_params(
        source_params={
            "run_frustrampnn": True,
            owner_key: {"schema_name": "frustrampnn_execution_owner"},
        },
        sequences_for_msa=[{"name": "reference_msa", "sequence": "ACDEFG"}],
        source_model_id="boltz2",
        source_mode="predict",
    )

    assert owner_key not in params
    assert "run_frustrampnn_batch" not in params


def test_msa_completion_has_no_retired_direct_scoring_owner() -> None:
    trigger_name = "maybe_trigger_batch_" + "frustrampnn"
    runner_name = "run_batch_" + "frustrampnn"
    canonical_name = "_is_canonical_protein_design_" + "batch"
    assert not hasattr(nextflow_service, trigger_name)
    assert not hasattr(nextflow_service, runner_name)
    assert not hasattr(nextflow_service, canonical_name)
