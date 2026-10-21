"""Packaging regressions; scientific acceptance remains a separate GPU run."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_has_one_setuptools_and_matches_runtime():
    recipe = (ROOT / 'apptainer/laproteina.def').read_text()
    bootstrap = recipe.split("cat > /opt/bootstrap.lock <<'LOCK'\n", 1)[1].split('\nLOCK', 1)[0]
    pins = re.findall(r'^([\w.-]+)==([^\s]+)', bootstrap, re.M)
    assert len(dict(pins)) == len(pins)
    runtime = (ROOT / 'apptainer/locks/laproteina-cu128.lock').read_text()
    match = re.search(r'^setuptools==([^\s]+)', runtime, re.M)
    assert match is not None
    assert dict(pins)['setuptools'] == match[1]


def test_cuda_dependency_family_and_complete_hash_lock():
    lock = (ROOT / 'apptainer/locks/laproteina-cu128.lock').read_text()
    assert 'torch==2.7.0+cu128' in lock
    assert 'cu118' not in lock and '-cu11==' not in lock
    for name in ('torch-scatter', 'torch-sparse', 'torch-cluster'):
        assert re.search(r'^' + name + r'==[^\s]+\+pt27cu128', lock, re.M)
    for block in re.split(r'(?m)^(?=[\w.-]+==)', lock)[1:]:
        assert '--hash=sha256:' in block


def test_recipe_native_composition_and_source_identity():
    recipe = (ROOT / 'apptainer/laproteina.def').read_text()
    assert 'python@sha256:' in recipe
    assert 'cde5de3ead6e4d76f367da6dc5174be9913ef6ca' in recipe
    assert 'snapshot.debian.org/archive/debian/20261021T000000Z' in recipe
    assert 'for preset in PROFILES:' in recipe
    assert '_, cfg, name = parse_args_and_cfg()' in recipe
    assert '0.15' in recipe and '0.05' in recipe
