from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import _build_msa_batch_command


def test_build_msa_batch_command_preserves_explicit_local_runtime_overrides(tmp_path: Path) -> None:
    cmd = _build_msa_batch_command(
        params={
            "sequences_json": '[{"name":"reference_msa","sequence":"ACDEFGHIK"}]',
            "reference_sequence": "ACDEFGHIK",
            "msa_force_refresh": True,
            "msa_cache_only": True,
            "msa_use_gpu": False,
            "msa_max_seqs": 2048,
            "msa_preset": "balanced",
            "msa_use_expand": True,
            "msa_use_env": False,
            "msa_num_iterations": 2,
            "msa_evalue": 0.001,
            "msa_min_seq_id": 0.25,
            "msa_min_coverage": 0.75,
            "msa_taxon_list": "9606",
            "msa_min_depth_warning": 16,
            "msa_min_depth_fail": 4,
            "msa_gpu_mode": "required",
            "msa_gpu_threshold": 60,
            "msa_preferred_gpus": [2, 3],
            "msa_excluded_gpus": [0, 1],
            "msa_gpu_server_mode": "off",
            "msa_gpu_server_wait_timeout": 9,
            "msa_gpu_server_db_load_mode": 3,
            "msa_gpu_server_startup_wait": 7.5,
            "msa_local_db": "/custom/db",
            "msa_cache_dir": "/custom/cache",
            "msa_threads": 12,
            "msa_target_shard_mode": "required",
            "msa_target_shards": 2,
            "msa_target_shard_min_size_gb": 0,
        },
        output_dir=str(tmp_path),
    )

    joined = " ".join(cmd)
    assert "--db_path /custom/db" in joined
    assert "--cache_dir /custom/cache" in joined
    assert "--threads 12" in joined
    assert "--target-shard-mode required" in joined
    assert "--target-shards 2" in joined
    assert "--target-shard-min-size-gb 0" in joined
    assert "--cache-only" in joined
    assert "--force_refresh" in joined
    assert "--cpu-only" in joined
    assert "--max-seqs 2048" in joined
    assert "--preset balanced" in joined
    assert "--use-expand 1" in joined
    assert "--use-env 0" in joined
    assert "--num-iterations 2" in joined
    assert "--evalue 0.001" in joined
    assert "--min-seq-id 0.25" in joined
    assert "--min-coverage 0.75" in joined
    assert "--taxon-list 9606" in joined
    assert "--min-depth-warning 16" in joined
    assert "--min-depth-fail 4" in joined
    assert "--gpu-mode required" in joined
    assert "--gpu-threshold 60" in joined
    assert "--preferred-gpus 2,3" in joined
    assert "--excluded-gpus 0,1" in joined
    assert "--gpu-server-mode off" in joined
    assert "--gpu-server-wait-timeout 9" in joined
    assert "--gpu-server-db-load-mode 3" in joined
    assert "--gpu-server-startup-wait 7.5" in joined
