from pathlib import Path
import pytest
from services.nextflow import _build_msa_batch_command


def test_saved_local_batch_rejected_without_rewriting_or_side_effects(tmp_path: Path):
    params = {"msa_local_db": "/custom/db", "msa_cache_dir": "/custom/cache",
              "msa_target_shards": 2, "msa_provider": "local"}
    with pytest.raises(ValueError, match="Local MSA search is disabled"):
        _build_msa_batch_command(params, str(tmp_path / "out"))
    assert params["msa_provider"] == "local"
    assert not (tmp_path / "out").exists()
