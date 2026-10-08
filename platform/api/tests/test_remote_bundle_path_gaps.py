"""Input admission uses managed inventories and path components."""
from pathlib import Path
import pytest
from services.remote_execution import bundle


@pytest.fixture
def roots(tmp_path, monkeypatch):
    roots = {key: tmp_path / key for key in ('data', 'inputs', 'results', 'weights', 'containers', 'repo')}
    for root in roots.values():
        root.mkdir()
    for getter, key in [('get_data_root', 'data'), ('get_inputs_dir', 'inputs'), ('get_results_dir', 'results'), ('get_weights_root', 'weights'), ('get_container_dir', 'containers')]:
        monkeypatch.setattr(bundle, getter, lambda key=key: roots[key], raising=False)
    return roots


def inputs(roots, raw, runtime=()):
    return bundle._input_assets({}, command=['nextflow', '--input', str(raw)],
                                repo_root=roots['repo'], runtime_paths=set(runtime),
                                output_dir=roots['results']/'job')


def test_independent_managed_input_root(roots):
    path = roots['inputs'] / 'seq.fa'
    path.write_text('>a\nAAAA\n')
    assert inputs(roots, path)[0][0] == path


def test_runtime_roots_excluded_before_admission(roots):
    path = roots['weights']/'protenix'
    path.mkdir()
    assert inputs(roots, roots['weights'], [path]) == []


def test_missing_declared_input_rejected(roots):
    with pytest.raises(bundle.RemoteBundleError, match='unavailable|missing'):
        inputs(roots, roots['data']/'missing.fa')


def test_ancestor_symlink_rejected(roots):
    real = roots['data']/'real'
    real.mkdir()
    (real/'seq.fa').write_text('data')
    alias = roots['data']/'alias'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(bundle.RemoteBundleError, match='symlink'):
        inputs(roots, alias/'seq.fa')


def test_rewrite_uses_components_not_substrings():
    mapping = {'/local/data': '/remote/data'}
    assert bundle._rewrite('/local/data/file', mapping) == '/remote/data/file'
    assert bundle._rewrite('/local/database/file', mapping) == '/local/database/file'
    assert bundle._rewrite('literal /local/data/file', mapping) == 'literal /local/data/file'
    assert bundle._rewrite('/local/data/../escape', mapping) == '/local/data/../escape'
