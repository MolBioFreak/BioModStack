"""Publication metadata drives all download selections and warm runtime rows."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import paths
from services.remote_execution import bundle, cache, hf_assets, images
from services.remote_execution.contracts import ProvisionSelection, WorkflowPackSelection


@pytest.fixture
def publication(tmp_path, monkeypatch):
    containers, weights = tmp_path / 'containers', tmp_path / 'weights'
    containers.mkdir()
    weights.mkdir()
    for owner in (paths, bundle):
        monkeypatch.setattr(owner, 'get_container_dir', lambda: containers)
        monkeypatch.setattr(owner, 'get_weights_root', lambda: weights)
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path)
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'images'))
    monkeypatch.setenv('BMS_HF_WEIGHT_ARCHIVE', 'a' * 64 + ':123')
    index = dict(schema=hf_assets.NAMED_INDEX_SCHEMA,
        archive=dict(sha256='a' * 64, size_bytes=123), digest_sizes={},
        dependencies=['containers/fixture.sif', 'weights/fixture'], artifacts=[
            dict(name='containers/fixture.sif', sha256='b' * 64, size_bytes=999, mode=0o555,
                 source=str(containers / 'fixture.sif')),
            dict(name='weights/fixture/model', sha256='c' * 64, size_bytes=1000, mode=0o444)])
    path = hf_assets.publication_index_path()
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(json.dumps(index))
    path.chmod(0o444)
    refs = (SimpleNamespace(kind='image', relative_path='fixture.sif'),
            SimpleNamespace(kind='weights', relative_path='fixture'))
    monkeypatch.setattr(cache, '_independent_dependencies', lambda _: refs)
    return SimpleNamespace(index=index, path=path, containers=containers, weights=weights)


def fail(*args, **kwargs):
    raise AssertionError('Shared asset bytes must not be inventoried')


@pytest.mark.parametrize('kind', ['workflow_pack', 'model', 'image'])
def test_named_planning_uses_no_asset_bodies_or_recursive_inventory(publication, monkeypatch, kind):
    monkeypatch.setattr(cache, '_records_for_source', fail)
    monkeypatch.setattr(bundle, '_records_for_source', fail)
    monkeypatch.setattr(images, 'verify_image', fail)
    monkeypatch.setattr(Path, 'rglob', fail)
    monkeypatch.setattr(hf_assets, 'prepare_sources', fail)
    entries = cache.independent_plan(SimpleNamespace(kind=kind))
    assert len(entries) == (1 if kind == 'image' else 2)
    assert entries[0].sha256 == 'b' * 64
    assert entries[0].size_bytes == 999
    assert entries[0].mode == 0o555


def test_warm_bundle_rows_reuse_declarations_but_custom_inputs_do_not(publication, monkeypatch):
    p = publication
    monkeypatch.setattr(bundle, '_records_for_source', fail)
    rows = bundle._runtime_records(p.weights / 'fixture', 'runtime/weights/fixture')
    assert rows[0].relative_path == 'runtime/weights/fixture/model'
    assert rows[0].sha256 == 'c' * 64
    assert bundle._published_runtime_records(p.weights / 'custom', 'weights/fixture') is None
    with pytest.raises(AssertionError):
        bundle._runtime_records(p.weights / 'custom', 'weights/fixture')
    with pytest.raises(AssertionError):
        bundle._records_for_source(p.weights, 'inputs/fixture', 'input')


@pytest.mark.parametrize('fault', ['missing', 'old', 'writable', 'identity', 'malformed'])
def test_legacy_metadata_falls_back_without_new_gate(publication, monkeypatch, fault):
    p = publication
    p.path.chmod(0o600)
    if fault == 'missing':
        p.path.unlink()
    elif fault == 'old':
        p.index.pop('schema')
    elif fault == 'identity':
        p.index['archive']['size_bytes'] = 124
    elif fault == 'malformed':
        p.index['artifacts'][0]['name'] = 'containers/../fixture.sif'
    if fault != 'missing':
        p.path.write_text(json.dumps(p.index))
        p.path.chmod(0o666 if fault == 'writable' else 0o444)
    calls = []
    def legacy(path, prefix, role):
        calls.append(prefix)
        return []
    monkeypatch.setattr(cache, '_records_for_source', legacy)
    cache.independent_plan(SimpleNamespace(kind='model'))
    assert 'containers/fixture.sif' in calls


def test_image_authority_change_does_not_reuse_old_named_identity(publication, monkeypatch):
    p = publication
    image = p.containers / 'fixture.sif'
    image.write_bytes(b'new')
    monkeypatch.setattr(images, 'image_reference', lambda *args: (image, 'd' * 64))
    rows = bundle._runtime_records(image, 'containers/fixture.sif')
    assert [(r.sha256, r.size_bytes) for r in rows] == [('d' * 64, 3)]


def test_preview_digest_binds_named_modes_and_endpoint(publication, monkeypatch):
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a' * 40, 'b' * 40))
    selection = ProvisionSelection(kind='model', model_id='fixture')
    target = SimpleNamespace(id='worker', host='host', port=22, username='user',
                            remote_root='/worker', host_key_sha256='e' * 64)
    first, _ = cache.independent_preview(selection, target)
    again, _ = cache.independent_preview(selection, target)
    assert first.preview_sha256 == again.preview_sha256
    target.port = 23
    changed, _ = cache.independent_preview(selection, target)
    assert first.preview_sha256 != changed.preview_sha256
    publication.index['artifacts'][0]['mode'] = 0o444
    publication.path.chmod(0o600)
    publication.path.write_text(json.dumps(publication.index))
    publication.path.chmod(0o444)
    mode_changed, _ = cache.independent_preview(selection, target)
    assert changed.preview_sha256 != mode_changed.preview_sha256


def test_adoption_uses_retained_manifest_modes_and_not_asset_files(publication, monkeypatch):
    p = publication
    script = Path(__file__).resolve().parents[3] / 'scripts/adopt_hf_archive_index.py'
    spec = importlib.util.spec_from_file_location('adopt_index', script)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    manifest = dict(selection=dict(kind='model', model_id='fixture'), source_revision='a' * 40,
        source_tree='b' * 40, artifacts=[{k: v for k, v in r.items() if k != 'source'}
                                       for r in p.index['artifacts']])
    manifest['artifacts'][0]['kind'] = 'runtime_image'
    preview = dict(selection=dict(kind='model', model_id='fixture'),
        dependencies=[dict(name=name) for name in p.index['dependencies']],
        artifacts=[{k: r[k] for k in ('name', 'sha256', 'size_bytes')} for r in manifest['artifacts']])
    p.path.chmod(0o600)
    p.path.write_text(json.dumps(dict(archive=p.index['archive'], digest_sizes={})))
    monkeypatch.setattr(bundle, '_sha256_file', fail)
    adopted = owner.adopt(preview, [manifest])
    index = json.loads(adopted.read_bytes())
    assert index['schema'] == hf_assets.NAMED_INDEX_SCHEMA
    assert index['artifacts'][0]['mode'] == 0o555
    assert len(cache.independent_plan(SimpleNamespace(kind='model'))) == 2
    assert adopted.stat().st_mode & 0o777 == 0o444
    wrong = json.loads(json.dumps(preview))
    wrong['artifacts'][0]['sha256'] = 'd' * 64
    with pytest.raises(ValueError, match='exactly match'):
        owner.adopt(wrong, [manifest])
