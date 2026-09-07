import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from publish_runtime_images import publish_references


def test_two_lanes_share_one_object_and_reference_republication_does_not_copy(tmp_path):
    source = tmp_path / 'source.sif'
    source.write_bytes(b'explicit synthetic container bytes')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    spec = {'BMS_PROTENIX_CONTAINER_PATH': {'source': str(source), 'sha256': digest}}
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
