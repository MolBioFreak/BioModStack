"""A locally built clone image is carried by its own measured hash, not the reference build's."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'platform/api')]


def _lock(tmp_path):
    lock = json.loads((ROOT / 'config/ngs/wf_clone_validation_v1.8.4.lock.json').read_text())
    cache = tmp_path / 'images'
    cache.mkdir()
    lock['containers']['cache_dir'] = str(cache)
    lock['patched_source']['path'] = str(tmp_path / 'source')
    lock['compatibility_patch']['path'] = str(ROOT / 'patches/wf-clone-validation-v1.8.4-nextflow25.patch')
    path = tmp_path / 'runtime.lock.json'
    path.write_text(json.dumps(lock))
    return lock, path, cache


def _image_releases(lock_path, tmp_path):
    import native_components as native
    dependencies, paths = native.ngs_clone_runtime_dependencies(
        {'wf_clone_runtime_lock': str(lock_path), 'wf_clone_nxf_home': str(tmp_path / 'nxf')})
    return {d.relative_path.rsplit('/', 1)[1]: d.semantic_release for d in dependencies if d.kind == 'image'}, dependencies


def test_local_build_uses_its_measured_hash_and_passes_the_selected_hash_check(tmp_path):
    from services.remote_execution.bundle import RemoteBundleError, verify_selected_runtime_hashes
    from types import SimpleNamespace
    import pytest
    lock, lock_path, cache = _lock(tmp_path)
    flye = [image for image in lock['containers']['images'] if image.get('assembly_tool') != 'canu']
    observed = {}
    for index, image in enumerate(flye):
        body = f'locally built {index}'.encode()
        (cache / image['cache_file']).write_bytes(body)
        observed['containers/ngs/wf-clone-validation/images/' + image['cache_file']] = hashlib.sha256(body).hexdigest()
    releases, dependencies = _image_releases(lock_path, tmp_path)
    for image in flye:
        measured = observed['containers/ngs/wf-clone-validation/images/' + image['cache_file']]
        assert releases[image['cache_file']] == 'sha256:' + measured
        assert measured != image['sha256']  # never relabelled with the reference digest
    images_only = SimpleNamespace(dependencies=[d for d in dependencies if d.kind == 'image'])
    verify_selected_runtime_hashes(images_only, observed)
    # Bytes that change after selection are still refused by the existing check.
    changed = dict(observed)
    changed[next(iter(changed))] = '0' * 64
    with pytest.raises(RemoteBundleError, match='changed or is missing'):
        verify_selected_runtime_hashes(images_only, changed)


def test_replaced_image_is_rehashed_and_a_missing_image_keeps_the_reference_hash(tmp_path):
    lock, lock_path, cache = _lock(tmp_path)
    first = next(image for image in lock['containers']['images'] if image.get('assembly_tool') != 'canu')
    target = cache / first['cache_file']
    releases, _ = _image_releases(lock_path, tmp_path)
    assert releases[first['cache_file']] == 'sha256:' + first['sha256']
    target.write_bytes(b'one')
    assert _image_releases(lock_path, tmp_path)[0][first['cache_file']] == 'sha256:' + hashlib.sha256(b'one').hexdigest()
    target.unlink()
    target.write_bytes(b'another build')
    assert _image_releases(lock_path, tmp_path)[0][first['cache_file']] == 'sha256:' + hashlib.sha256(b'another build').hexdigest()
