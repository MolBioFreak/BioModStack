from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_antibody_batch_boltz_uses_shared_cache_not_task_fake_home() -> None:
    module_text = (REPO_ROOT / "modules" / "antibody_batch.nf").read_text(encoding="utf-8")

    assert 'export BOLTZ_CACHE_DIR="\\$BOLTZ_SHARED_CACHE"' in module_text
    assert 'export BOLTZ_CACHE="\\$BOLTZ_SHARED_CACHE"' in module_text
    assert '--cache "\\$BOLTZ_CACHE_DIR"' in module_text
    assert 'export HOME="\\$BOLTZ_SHARED_CACHE/home"' in module_text
    assert '.fake_home' not in module_text
    assert '.fake_home/.boltz' not in module_text
    assert 'mols.tar' not in module_text
    assert '$(pwd)/.boltz_cache' not in module_text


def test_nested_wf_clone_has_shared_singularity_and_nxf_caches() -> None:
    config_text = (REPO_ROOT / "nextflow.config").read_text(encoding="utf-8")
    module_text = (REPO_ROOT / "modules" / "ngs" / "clone_validation.nf").read_text(encoding="utf-8")

    assert "BMS_WF_CLONE_SINGULARITY_CACHE" in config_text
    assert 'wf_clone_singularity_cache = System.getenv(\'BMS_WF_CLONE_SINGULARITY_CACHE\') ?: "${bmsContainerRootFallback}/singularity_cache"' in config_text
    assert 'wf_clone_nxf_home = System.getenv(\'BMS_WF_CLONE_NXF_HOME\') ?: "${cacheRoot}/nextflow/wf-clone"' in config_text
    assert 'apptainer.cacheDir = System.getenv(\'NXF_APPTAINER_CACHEDIR\') ?: ensureDir("${cacheRoot}/apptainer")' in config_text
    assert 'export NXF_SINGULARITY_CACHEDIR="${wfCloneSingularityCache}"' in module_text
    assert 'export NXF_HOME="${wfCloneNxfHome}"' in module_text
