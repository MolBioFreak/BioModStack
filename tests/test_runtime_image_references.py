import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from publish_runtime_images import publish_references


@pytest.mark.parametrize('selector', [
    'BMS_PROTENIX_CONTAINER_PATH', 'BMS_FRUSTRAMPNN_SIF',
    'BMS_NGS_RUNTIME_SIF', 'BMS_CM_CONFORNETS_CONTAINER_PATH',
])
def test_two_lanes_share_one_object_and_reference_republication_does_not_copy(tmp_path, selector):
    source = tmp_path / 'source.sif'
    source.write_bytes(b'explicit synthetic container bytes')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    spec = {selector: {'source': str(source), 'sha256': digest}}
    store = tmp_path / 'store'
    dev = publish_references(store, 'development', spec)
    obj = store / 'objects' / 'sha256' / digest / 'runtime.sif'
    before = obj.stat()
    prod = publish_references(store, 'production', spec)
    assert dev.read_text() == prod.read_text()
    assert obj.stat().st_ino == before.st_ino
    assert obj.stat().st_mtime_ns == before.st_mtime_ns
    assert list(store.rglob('runtime.sif')) == [obj]
    assert source.read_bytes() == b'explicit synthetic container bytes'


def test_model_names_do_not_create_separate_stores_or_objects(tmp_path):
    source = tmp_path / 'source.sif'
    source.write_bytes(b'explicit synthetic shared runtime')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    keys = ['BMS_PROTENIX_CONTAINER_PATH', 'BMS_FRUSTRAMPNN_SIF',
            'BMS_NGS_RUNTIME_SIF', 'BMS_CM_CONFORNETS_CONTAINER_PATH']
    store = tmp_path / 'store'
    spec = {key: {'source': str(source), 'sha256': digest} for key in keys}
    reference = publish_references(store, 'development', spec)
    objects = list(store.rglob('runtime.sif'))
    assert len(objects) == 1
    assert objects[0].stat().st_nlink == 1
    for key in keys:
        assert f'{key}={objects[0]}\n' in reference.read_text()


def test_bad_digest_keeps_existing_lane_references(tmp_path):
    source = tmp_path / 'source.sif'
    source.write_bytes(b'fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = tmp_path / 'store'
    ref = publish_references(store, 'development', {'BMS_NGS_RUNTIME_SIF': {'source': str(source), 'sha256': digest}})
    previous = ref.read_bytes()
    with pytest.raises(Exception):
        publish_references(store, 'development', {'BMS_NGS_RUNTIME_SIF': {'source': str(source), 'sha256': 'a'*64}})
    assert ref.read_bytes() == previous


def test_reference_configuration_is_closed(tmp_path):
    with pytest.raises(ValueError):
        publish_references(tmp_path/'store', 'development', {'LD_PRELOAD': {'source':'a', 'sha256':'b'}})
    with pytest.raises(ValueError):
        publish_references(tmp_path/'store', '../other', {})
