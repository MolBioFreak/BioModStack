import asyncio
import json
import tracemalloc

import pytest
from fastapi import HTTPException

from routers import family_msa_results as route


class Session:
    async def rollback(self):
        self.rolled_back = True


class Request:
    def __init__(self, chunks):
        self.chunks = chunks

    async def stream(self):
        for chunk in self.chunks:
            yield chunk


def test_large_header_spooled_truncated_and_valid(tmp_path, monkeypatch):
    async def owner(*args, **kwargs):
        pass

    async def imported(project, replay, manifest, root, session, domain):
        assert (root / 'file').read_bytes() == b'abc'
        assert manifest['files'][0]['size'] == 3
        assert replay == 'key'
        return {'receipt_id': 'receipt', 'replayed': False, 'shared_state': 'none'}

    monkeypatch.setattr(route, '_require_mutation_owner', owner)
    monkeypatch.setattr(route, '_import_directory', imported)
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    size = 32 * 1024 * 1024
    block = b' ' * (64 * 1024)

    def truncated_chunks():
        yield (size * 2).to_bytes(8, 'big')
        for _ in range(size // len(block)):
            yield block

    session = Session()
    tracemalloc.start()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.import_bundle_stream(Request(truncated_chunks()), 'project', session))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f'truncated {size} bytes: Python allocation peak {peak} bytes')
    assert exc.value.status_code == 400
    assert session.rolled_back
    assert peak < 8 * 1024 * 1024
    assert not list(tmp_path.iterdir())

    metadata = json.dumps({'replay_key': 'key', 'manifest': {'files': [{'path': 'file', 'size': 3}]}}).encode()
    def valid_chunks():
        yield (len(metadata) + size).to_bytes(8, 'big')[:3]
        yield (len(metadata) + size).to_bytes(8, 'big')[3:] + metadata
        for _ in range(size // len(block)):
            yield block
        yield b'abc'
    result = asyncio.run(route.import_bundle_stream(Request(valid_chunks()), 'project', Session()))
    assert result['receipt_id'] == 'receipt'
    assert not list(tmp_path.iterdir())

    # A genuinely large metadata value is preserved, not merely whitespace.
    value = 'é' * (2 * 1024 * 1024)
    metadata = json.dumps({'replay_key': 'key', 'manifest': {
        'files': [{'path': 'file', 'size': 3}], 'annotation': value}}, ensure_ascii=False).encode()
    async def check_large(project, replay, manifest, root, session, domain):
        assert manifest['annotation'] == value
        assert (root / 'file').read_bytes() == b'abc'
        return {'receipt_id': 'large', 'replayed': False, 'shared_state': 'none'}
    monkeypatch.setattr(route, '_import_directory', check_large)
    body = len(metadata).to_bytes(8, 'big') + metadata + b'abc'
    chunks = (body[i:i + 65536] for i in range(0, len(body), 65536))
    assert asyncio.run(route.import_bundle_stream(Request(chunks), 'project', Session()))['receipt_id'] == 'large'
    assert not list(tmp_path.iterdir())
