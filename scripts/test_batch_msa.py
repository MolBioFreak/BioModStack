import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import batch_msa


def test_batch_script_exports_package_run_batch_msa() -> None:
    from local_msa.batching import run_batch_msa

    assert batch_msa.run_batch_msa is run_batch_msa


def test_batch_script_exports_package_defaults() -> None:
    from local_msa.config import DEFAULT_CACHE_DIR, DEFAULT_DB_PATH

    assert batch_msa.DEFAULT_DB_PATH == DEFAULT_DB_PATH
    assert batch_msa.DEFAULT_CACHE_DIR == DEFAULT_CACHE_DIR


def test_run_batch_msa_balanced_mode_delegates_cache_only_and_threads(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def _fake_run_colabfold_per_sequence(**kwargs):
        captured.update(kwargs)
        return [
            {
                "name": "seq1",
                "sequence_hash": "deadbeef",
                "msa_path": str((tmp_path / "out" / "seq1.a3m").resolve()),
                "cache_hit": True,
                "success": True,
            }
        ]

    monkeypatch.setattr(batch_msa, "_run_colabfold_per_sequence", _fake_run_colabfold_per_sequence)

    manifest = batch_msa.run_batch_msa(
        sequences=[{"name": "seq1", "sequence": "ACDEFGHIK"}],
        output_dir=tmp_path / "out",
        db_path=tmp_path / "db",
        cache_dir=tmp_path / "cache",
        preset="balanced",
        cache_only=True,
        threads=7,
        target_shard_mode="required",
        target_shards=2,
        target_shard_min_size_gb=0,
    )

    assert captured["cache_only"] is True
    assert captured["threads"] == 7
    assert captured["target_shard_mode"] == "required"
    assert captured["target_shards"] == 2
    assert captured["target_shard_min_size_gb"] == 0
    assert manifest["successful"] == 1
    assert manifest["sequences"][0]["success"] is True
