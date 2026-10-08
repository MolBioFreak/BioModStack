from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NEXTFLOW_CONFIG = ROOT / "nextflow.config"
STRUCTURE_MODULE = ROOT / "modules" / "structure_prediction.nf"


def test_nextflow_msa_defaults_keep_32_threads_and_enable_adaptive_target_sharding() -> None:
    text = NEXTFLOW_CONFIG.read_text(encoding="utf-8")

    assert "msa_threads = 32" in text
    assert "msa_target_shard_mode = 'auto'" in text
    assert "msa_target_shards = 4" in text
    assert "msa_target_shard_min_size_gb = 1.0" in text


def test_structure_prediction_module_passes_target_sharding_to_local_msa_runner() -> None:
    text = STRUCTURE_MODULE.read_text(encoding="utf-8")

    for assignment in [
        "msa_target_shard_mode =",
        "msa_target_shards =",
        "msa_target_shard_min_size_gb =",
    ]:
        assert assignment in text

    for flag in [
        "--target-shard-mode",
        "--target-shards",
        "--target-shard-min-size-gb",
    ]:
        assert flag in text
