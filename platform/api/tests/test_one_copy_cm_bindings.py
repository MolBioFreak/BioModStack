"""Tiny offline checkpoint custody/portable-read regressions, no model execution."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from scripts.lib import portable_inputs as portable
from services.conformational_mapping.request_builder import materialize_trusted_internal_request
from tests.test_conformational_mapping_routing import _request_params

ROOT = Path(__file__).resolve().parents[3]


def load_script(name, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_request(tmp_path, monkeypatch, *, retained=False):
    results = tmp_path / 'results'; results.mkdir()
    weights = tmp_path / 'weights'; weights.mkdir()
    checkpoint = weights / 'selected-old.pt'
    checkpoint.write_bytes(bytes(range(256)) * 256)
    checkpoint.chmod(0o444)
    latest = weights / 'latest.pt'; latest.write_bytes(b'different version')
    root = results / 'request'; root.mkdir()
    logical = 'registered/historical-source-id.pt'
    if retained:
        (root / 'registered').mkdir()
        shutil.copyfile(checkpoint, root / logical)
    params = _request_params('confornets')
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    params['confornets']['checkpoint'] = dict(path=logical, sha256=digest)
    materialized = materialize_trusted_internal_request(params, output_dir=root,
        request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture')
    (root / 'cm_runtime_registry_v1.json').write_text(json.dumps(params['confornets']['backend_identity']))
    document = json.loads(materialized.request_path.read_bytes())
    inventory = {str(path): dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                size_bytes=path.stat().st_size, format='pt')
                 for path in (checkpoint, latest)}
    binding = portable.bind_cm_checkpoint(document, owner=materialized.request_path,
                                           runtime_references=inventory)
    envelope = tmp_path / 'bindings.json'
    envelope.write_text(json.dumps(dict(schema=portable.SCHEMA, roots=[str(results), str(weights)],
        results_root=str(results), bindings=[binding])))
    monkeypatch.setenv(portable.ENV, str(envelope))
    monkeypatch.setenv('BMS_RESULTS_ROOT', str(results))
    return materialized, checkpoint, inventory, binding


@pytest.mark.parametrize('retained', [False, True])
def test_cold_warm_concurrent_prep_uses_one_selected_body(tmp_path, monkeypatch, retained):
    materialized, checkpoint, inventory, binding = setup_request(tmp_path, monkeypatch, retained=retained)
    prep = load_script('prep_canonical_confornets_request', monkeypatch)
    monkeypatch.setattr(prep, '_instrumented_confornets_runtime_available', lambda: True)
    original = materialized.request_path.read_bytes()
    before = checkpoint.stat()
    alias = tmp_path / 'request-alias'; alias.symlink_to(materialized.request_path.parent)
    os.link(checkpoint, checkpoint.parent / 'hard-alias.pt')
    leaf = checkpoint.parent / 'leaf-alias.pt'; leaf.symlink_to(checkpoint.name)
    assert portable.resolve_input_path(leaf) == checkpoint
    def prepare(index):
        assets = tmp_path / f'assets-{index}'; output = tmp_path / f'native-{index}.json'
        prep.prepare(alias / materialized.request_path.name,
                     alias / materialized.coordinate_plan_path.name, assets, output)
        native = json.loads(output.read_bytes())
        assert native['params']['checkpoint_path'] == str(checkpoint)
        assert native['params']['num_samples'] == 3
        assert native['params']['num_runs'] == 2
        return Path(native['params']['checkpoint_path']).stat().st_ino
    cold = prepare(0)
    warm = prepare(1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        readers = list(executor.map(prepare, range(2, 10)))
    assert set([cold, warm, *readers]) == {before.st_ino}
    assert materialized.request_path.read_bytes() == original
    assert checkpoint.stat().st_mtime_ns == before.st_mtime_ns
    assert checkpoint.stat().st_mode == before.st_mode
    assert not list(tmp_path.glob('assets-*/*.pt'))
    assert (materialized.request_path.parent / 'registered/historical-source-id.pt').exists() is retained
    print(json.dumps(dict(checkpoint_bytes=before.st_size, prep_invocations=10,
        additional_checkpoint_bodies=0, unique_selected_inodes=1,
        avoided_per_request_payload_bytes=before.st_size * 10)))


def test_discovery_binds_absent_logical_checkpoint_without_hash_or_upload(tmp_path, monkeypatch):
    materialized, checkpoint, inventory, binding = setup_request(tmp_path, monkeypatch)
    real_identity = portable._identity
    def identity(path):
        assert path != checkpoint, 'selected runtime inventory must not be rehashed during discovery'
        return real_identity(path)
    monkeypatch.setattr(portable, '_identity', identity)
    refs = portable.discover_native_input_references('conformational_mapping', 'map',
        {'cm_request_path': str(materialized.request_path)}, (), output_dir=materialized.request_path.parent,
        allowed_roots=[materialized.request_path.parent], runtime_references=inventory)
    runtime = [ref for ref in refs if ref['role'] == 'runtime']
    assert len(runtime) == 1
    assert runtime[0]['source_path'] == str(checkpoint)
    assert runtime[0]['reference_path'].endswith('/registered/historical-source-id.pt')
    assert not any(ref['role'] == 'runtime-snapshot' for ref in refs)
    monkeypatch.setattr(portable, '_identity', real_identity)
    assert portable.resolve_input_path('registered/historical-source-id.pt', owner=materialized.request_path) == checkpoint
    with pytest.raises(ValueError, match='not a selected dependency'):
        portable.bind_cm_checkpoint(json.loads(materialized.request_path.read_bytes()),
            owner=materialized.request_path, runtime_references={str(checkpoint.parent/'latest.pt'): inventory[str(checkpoint.parent/'latest.pt')]})


def test_worker_binding_preserves_document_bytes_and_old_logical_name(tmp_path, monkeypatch):
    materialized, checkpoint, inventory, binding = setup_request(tmp_path, monkeypatch)
    worker = tmp_path / 'worker'; worker.mkdir()
    worker_request = worker / 'cm_request_v1.json'
    worker_request.write_bytes(materialized.request_path.read_bytes())
    worker_checkpoint = worker / 'installed.pt'
    worker_checkpoint.write_bytes(checkpoint.read_bytes())  # distinct host cold installation
    binding['path'] = str(worker_checkpoint)
    source_ref = dict(source_path=str(materialized.request_path),
        sha256=hashlib.sha256(worker_request.read_bytes()).hexdigest(), size_bytes=worker_request.stat().st_size)
    envelope = worker/'bindings.json'
    envelope.write_text(json.dumps(dict(schema=portable.SCHEMA, roots=[str(worker)],
        bindings=[binding, dict(reference=source_ref, path=str(worker_request))])))
    monkeypatch.setenv(portable.ENV, str(envelope))
    assert portable.resolve_input_path('registered/historical-source-id.pt', owner=worker_request) == worker_checkpoint
    assert worker_request.read_bytes() == materialized.request_path.read_bytes()
    worker_checkpoint.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='digest/size'):
        portable.resolve_input_path('registered/historical-source-id.pt', owner=worker_request)


@pytest.mark.parametrize('alias_kind', ['directory', 'leaf', 'hardlink'])
def test_request_owned_aliases_keep_resolved_containment(tmp_path, monkeypatch, alias_kind):
    results = tmp_path/'results'; results.mkdir()
    monkeypatch.delenv(portable.ENV, raising=False)
    monkeypatch.setenv('BMS_RESULTS_ROOT', str(results))
    root = results/'request'; root.mkdir()
    source = root/'payload'; source.write_bytes(b'biological snapshot')
    prep = load_script('prep_canonical_confornets_request', monkeypatch)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if alias_kind == 'directory':
        alias = tmp_path/'stage'; alias.symlink_to(root)
        assert prep._resolve_authenticated(alias, 'payload', digest, 'reference') == source
    else:
        alias = root/'alias'
        if alias_kind == 'leaf': alias.symlink_to(source.name)
        else: os.link(source, alias)
        assert prep._resolve_authenticated(root, alias.name, digest, 'reference').read_bytes() == source.read_bytes()
    escaped = root/'escaped'; escaped.symlink_to(tmp_path/'outside')
    (tmp_path/'outside').write_bytes(b'outside')
    with pytest.raises(prep.CanonicalPrepError, match='escapes'):
        prep._resolve_authenticated(root, 'escaped', hashlib.sha256(b'outside').hexdigest(), 'reference')
    with pytest.raises(ValueError, match='outside approved'):
        portable._contained(escaped, [root])
    source.write_bytes(b'changed')
    with pytest.raises(prep.CanonicalPrepError, match='SHA-256'):
        prep._resolve_authenticated(root, 'payload', digest, 'reference')
