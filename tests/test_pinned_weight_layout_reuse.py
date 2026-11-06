"""Same-operation reuse, independent resume verification, and ephemeral transport."""
from dataclasses import asdict, replace
from pathlib import Path
import json
import pytest
from test_pinned_acquisition import source, cleanup_readonly_objects, PAYLOAD
from lib.pinned_acquisition import Artifact, acquire, AcquisitionError
from lib import pinned_weight_layout as layout
from lib import pinned_acquisition as acquisition


def test_cold_checkpoint_and_publication_are_not_reaudited(source, tmp_path, monkeypatch):
    item, _ = source
    observed = acquisition._observed
    calls = []
    def checkpoint(path):
        calls.append(path)
        return observed(path)
    monkeypatch.setattr(acquisition, '_observed', checkpoint)
    def no_second_audit(*args, **kwargs):
        raise AssertionError('publication already authenticated this generation')
    monkeypatch.setattr(acquisition, 'verify_image', no_second_audit)
    result = acquire(item, tmp_path, test_only=True)
    assert result['verification']['sha256'] == item.sha256
    assert len(calls) == 1


def test_acquired_member_not_reacquired_or_rehashed(source, tmp_path, monkeypatch):
    item, handler = source
    item = replace(item, kind='weights', license_id='fixture-license')
    receipt = acquire(item, tmp_path, test_only=True, accepted_licenses=['fixture-license'])
    entries = [{'member_path': 'model.safetensors', 'manifest': asdict(item)}]
    dep = {'kind': 'weights', 'relative_path': 'fixture'}
    def forbidden(*args, **kwargs):
        raise AssertionError('repeated same-operation work')
    monkeypatch.setattr(layout, 'acquire', forbidden)
    original = layout._hash
    monkeypatch.setattr(layout, '_hash', forbidden)
    result = layout.materialize_weights(dep, entries, tmp_path, test_only=True,
        accepted_licenses=['fixture-license'], acquired={item.manifest_digest: receipt})
    assert (Path(result['path']) / 'model.safetensors').resolve() == Path(receipt['path'])
    assert len(handler.requests) == 1
    calls = []
    def count(fd):
        calls.append(fd)
        return original(fd)
    monkeypatch.setattr(layout, '_hash', count)
    assert layout.materialize_weights(dep, entries, tmp_path, test_only=True,
        accepted_licenses=['fixture-license']) == result
    assert len(calls) == 1  # Independent invocation is still a full audit.
    payload = Path(receipt['path'])
    payload.chmod(0o600)
    payload.write_bytes(b'z' * len(PAYLOAD))
    payload.chmod(0o400)
    with pytest.raises(AcquisitionError, match='identity changed'):
        layout.materialize_weights(dep, entries, tmp_path, test_only=True,
            accepted_licenses=['fixture-license'], acquired={item.manifest_digest: receipt})


def test_transient_source_never_enters_manifest_or_journal(source, tmp_path):
    item, handler = source
    authority = item.url
    calls = []
    def resolver(artifact):
        calls.append(artifact)
        return authority + '?ephemeral-fixture-capability=CANARY'
    result = acquire(item, tmp_path, test_only=True, source_resolver=resolver)
    assert result['manifest_digest'] == item.manifest_digest
    assert 'CANARY' not in json.dumps(result)
    assert all('CANARY' not in p.read_text() for p in tmp_path.rglob('state.json'))
    assert acquire(item, tmp_path, test_only=True, source_resolver=resolver) == result
    assert len(calls) == len(handler.requests) == 1
