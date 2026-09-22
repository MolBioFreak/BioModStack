"""Packed shared-weight delivery: member verification and the completion boundary.

The worker's archive pass runs the real helper over a real worker cache; HF HTTP
and the CDN download are declared doubles. This qualifies the software boundary
only, never cloud throughput, a live bucket or scientific acceptance.
"""
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
import subprocess
import sys
import uuid

import pytest

from services.remote_execution import cache
from services.remote_execution.bundle import CacheTransferArtifact, TransferPlan
from test_artifact_cache import module
from test_remote_cache_integration import local_transport


SCHEMA = 'bms.runtime-image-references.v1'


def member_rows(members):
    return [dict(name=name, sha256=hashlib.sha256(data).hexdigest(),
                 size_bytes=len(data), mode=0o444) for name, data in members.items()]


def row(name, data):
    return dict(name=name, sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data), mode=0o444)


def build_archive(path, members, names=None, truncate=None):
    """One ordinary tar of member bytes, plus the identity to declare for it.

    `names` replaces the stored names, which is how an archive can claim a layout
    member's name while carrying other bytes. `truncate` cuts the payload, to
    model an interrupted pack or download.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w') as tar:
        for name, data in members.items():
            info = tarfile.TarInfo((names or {}).get(name, name))
            info.size = len(data)
            info.mode = 0o444
            tar.addfile(info, io.BytesIO(data))
    payload = path.read_bytes()[:truncate] if truncate is not None else path.read_bytes()
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest(), len(payload)


def mid_second_member_offset(path, index=1):
    """Byte offset inside the second member's own payload, never in its header."""
    with tarfile.open(path, 'r') as tar:
        member = tar.getmembers()[index]
        return member.offset_data + member.size // 2


def objects(root):
    return sorted(path.name for path in (Path(root) / 'cache/objects/sha256').rglob('*')
                  if len(path.name) == 64)


def write_listing(runtime_root, rows):
    """The bundle's runtime listing, exactly as bundle.py writes it."""
    digest, normalized, _ = module.weight_layout(rows)
    document = json.dumps({'schema': SCHEMA, 'runtime_root': runtime_root,
                           'weights': normalized, 'images': []},
                          sort_keys=True, separators=(',', ':')).encode()
    return digest, normalized, document


@pytest.fixture
def worker(tmp_path):
    """A real worker cache plus the one delivery route an archive arrives by."""
    authority = module.Cache(tmp_path / 'cache')
    operation, batch = str(uuid.uuid4()), uuid.uuid4().hex

    def deliver(payload, digest):
        try:
            incoming = authority.incoming_batch(operation, batch, create=True)
        except FileExistsError:
            incoming = authority.incoming_batch(operation, batch)
        target = Path(str(incoming)) / digest
        target.write_bytes(payload)
        return target

    def layout(rows, runtime=None):
        runtime = runtime or (tmp_path / 'runtime')
        runtime.mkdir(parents=True, exist_ok=True)
        _, normalized, document = write_listing(str(runtime), rows)
        reference = {'path': str(runtime / '.bms-runtime-images.json'),
                     'sha256': hashlib.sha256(document).hexdigest()}
        (runtime / '.bms-runtime-images.json').write_bytes(document)
        return normalized, reference

    return SimpleNamespace(cache=authority, operation=operation, batch=batch,
                           deliver=deliver, layout=layout)


def unpack(worker, archive, layout):
    return worker.cache.unpack_weight_archive(archive, worker.operation, worker.batch, layout)


def probe(worker, archive, layout):
    return worker.cache.archive_state(archive, layout)


def test_archive_members_publish_only_by_their_layout_digest(tmp_path, worker):
    members = {'boltz/boltz/model.ckpt': b'A' * 4096, 'protenix/params.npz': b'B' * 8192}
    absent = b'C' * 3000
    rows = member_rows({**members, 'boltz/boltz/large.ckpt': absent})
    normalized, layout = worker.layout(rows)
    path, digest, size = build_archive(tmp_path / 'weights.tar', members)
    worker.deliver(path.read_bytes(), digest)
    result = unpack(worker, {'sha256': digest, 'size_bytes': size}, layout)
    assert result['state'] == 'ready' and result['unmatched'] == 0
    assert result['matched'] == sorted(hashlib.sha256(data).hexdigest() for data in members.values())
    assert result['missing'] == [hashlib.sha256(absent).hexdigest()]
    # The objects are exactly the member bytes, published read-only under digest.
    for name, data in members.items():
        item = next(row for row in normalized if row['name'] == name)
        object_path = tmp_path / 'cache/objects/sha256' / item['sha256'][:2] / item['sha256']
        assert object_path.read_bytes() == data
        assert object_path.stat().st_mode & 0o777 == 0o444
    # One declared row is still absent, so no view may be published yet.
    assert worker.cache.weights(normalized)['state'] == 'missing'
    assert not [p for p in (tmp_path / 'cache/weights').iterdir() if not p.name.startswith('.')]
    # The row the archive does not carry arrives through the ordinary owner.
    incoming = Path(str(worker.cache.incoming_batch(worker.operation, worker.batch))) / 'absent'
    incoming.write_bytes(absent)
    worker.cache.ingest({'sha256': hashlib.sha256(absent).hexdigest(), 'size_bytes': len(absent)},
                        incoming)
    installed = worker.cache.weights(normalized, install=True)
    assert installed['state'] == 'ready'
    root = Path(installed['root'])
    for name, data in members.items():
        assert (root / name).read_bytes() == data
    assert (root / 'boltz/boltz/large.ckpt').read_bytes() == absent


def test_extra_and_mismatched_members_never_become_admitted_bytes(tmp_path, worker):
    good = b'D' * 2048
    impostor = b'E' * (module.MAX_UNMATCHED_ARCHIVE_BYTES + 4096)
    rows = member_rows({'weights/model.ckpt': good})
    normalized, layout = worker.layout(rows)
    path, digest, size = build_archive(tmp_path / 'weights.tar',
                                       {'weights/model.ckpt': impostor, 'weights/extra.bin': b'F' * 32},
                                       names={'weights/model.ckpt': 'weights/model.ckpt'})
    worker.deliver(path.read_bytes(), digest)
    with pytest.raises(ValueError, match='unexpected_archive_member'):
        unpack(worker, {'sha256': digest, 'size_bytes': size}, layout)
    # Nothing from a refused archive was published, and no view exists.
    assert objects(tmp_path) == []
    assert worker.cache.weights(normalized)['state'] == 'missing'
    assert probe(worker, {'sha256': digest, 'size_bytes': size}, layout)['state'] == 'partial'


def test_small_accompanying_member_is_counted_never_published(tmp_path, worker):
    good = b'G' * 4096
    rows = member_rows({'weights/model.ckpt': good})
    normalized, layout = worker.layout(rows)
    path, digest, size = build_archive(tmp_path / 'weights.tar',
                                       {'weights/model.ckpt': good, 'INDEX': b'packer index\n'})
    worker.deliver(path.read_bytes(), digest)
    result = unpack(worker, {'sha256': digest, 'size_bytes': size}, layout)
    assert result['state'] == 'ready' and result['unmatched'] == 1
    assert result['matched'] == [normalized[0]['sha256']]
    assert objects(tmp_path) == [normalized[0]['sha256']]
    # The record carries what the pass counted, so a warm answer stays truthful.
    again = unpack(worker, {'sha256': digest, 'size_bytes': size}, layout)
    assert again['unmatched'] == 1 and again['state'] == 'ready'


def test_archive_identity_is_authenticated_before_any_member(tmp_path, worker):
    members = {'weights/model.ckpt': b'H' * 4096}
    normalized, layout = worker.layout(member_rows(members))
    path, digest, size = build_archive(tmp_path / 'weights.tar', members)
    substituted = hashlib.sha256(b'other bytes').hexdigest()
    worker.deliver(path.read_bytes(), substituted)
    with pytest.raises(ValueError, match='archive_identity_mismatch'):
        unpack(worker, {'sha256': substituted, 'size_bytes': size}, layout)
    assert objects(tmp_path) == []
    # A pass that never started leaves nothing that could be mistaken for one.
    assert probe(worker, {'sha256': substituted, 'size_bytes': size}, layout)['state'] == 'missing'
    assert worker.cache.weights(normalized)['state'] == 'missing'


def test_truncated_archive_is_never_presented_as_complete(tmp_path, worker):
    """The required interruption case: bytes may land, a complete tree may not."""
    members = {'weights/one.ckpt': b'I' * (1024 * 1024), 'weights/two.ckpt': b'J' * (1024 * 1024)}
    rows = member_rows(members)
    normalized, layout = worker.layout(rows)
    full, _, _ = build_archive(tmp_path / 'full.tar', members)
    path, digest, size = build_archive(tmp_path / 'partial.tar', members,
                                       truncate=mid_second_member_offset(full))
    worker.deliver(path.read_bytes(), digest)
    with pytest.raises(ValueError):
        unpack(worker, {'sha256': digest, 'size_bytes': size}, layout)
    first, second = (rows[0]['sha256'], rows[1]['sha256'])
    assert objects(tmp_path) == [first]
    observed = probe(worker, {'sha256': digest, 'size_bytes': size}, layout)
    assert observed['state'] == 'partial' and observed['matched'] == []
    assert worker.cache.weights(normalized)['state'] == 'missing'
    assert not [p for p in (tmp_path / 'cache/weights').iterdir() if not p.name.startswith('.')]
    assert not (tmp_path / 'cache/archives' / digest / 'complete.json').exists()


def test_pass_is_bound_to_one_archive_and_one_layout(tmp_path, worker):
    members = {'weights/model.ckpt': b'K' * 4096}
    normalized, layout = worker.layout(member_rows(members))
    _, other_layout = worker.layout(member_rows({**members, 'weights/other.ckpt': b'L' * 512}),
                                    runtime=tmp_path / 'other-runtime')
    path, digest, size = build_archive(tmp_path / 'weights.tar', members)
    worker.deliver(path.read_bytes(), digest)
    assert probe(worker, {'sha256': digest, 'size_bytes': size}, layout)['state'] == 'missing'
    first = unpack(worker, {'sha256': digest, 'size_bytes': size}, layout)
    assert first['state'] == 'ready'
    assert probe(worker, {'sha256': digest, 'size_bytes': size}, layout)['state'] == 'ready'
    # A pass is evidence for exactly the layout and object identity it names.
    assert probe(worker, {'sha256': digest, 'size_bytes': size}, other_layout)['state'] == 'partial'
    assert probe(worker, {'sha256': digest, 'size_bytes': size + 1}, layout)['state'] == 'partial'
    # Warm: the declared object is no longer even needed to answer, and a second
    # pass republishes nothing.
    (Path(str(worker.cache.incoming_batch(worker.operation, worker.batch))) / digest).unlink()
    again = unpack(worker, {'sha256': digest, 'size_bytes': size}, layout)
    assert again['state'] == 'ready' and again['matched'] == first['matched']
    assert objects(tmp_path) == [normalized[0]['sha256']]
    # A removed record is not silently trusted: the pass is redone.
    (tmp_path / 'cache/archives' / digest / 'complete.json').unlink()
    assert probe(worker, {'sha256': digest, 'size_bytes': size}, layout)['state'] == 'partial'
    assert worker.cache.weights(normalized)['state'] == 'missing'
    worker.deliver(path.read_bytes(), digest)
    assert unpack(worker, {'sha256': digest, 'size_bytes': size}, layout)['state'] == 'ready'


def test_cli_dispatch_reports_the_archive_pass(tmp_path, worker):
    """The installed helper's own entrypoint answers the archive actions."""
    members = {'weights/model.ckpt': b'T' * 512}
    normalized, layout = worker.layout(member_rows(members))
    path, digest, size = build_archive(tmp_path / 'weights.tar', members)
    worker.deliver(path.read_bytes(), digest)
    archive = {'sha256': digest, 'size_bytes': size}
    unpack(worker, archive, layout)
    tool = Path(__file__).parents[1] / 'tools/bms_artifact_cache.py'
    result = subprocess.run([sys.executable, str(tool), '--root', str(worker.cache.root)],
                            input=json.dumps({'action': 'weights_archive_probe',
                                              'archive': archive, 'layout': layout}),
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['state'] == 'ready'
    # The same pass again is warm: no second member read, still complete.
    again = subprocess.run([sys.executable, str(tool), '--root', str(worker.cache.root)],
                           input=json.dumps({'action': 'unpack_weights_archive',
                                             'archive': archive, 'layout': layout,
                                             'operation_id': worker.operation,
                                             'batch_id': worker.batch}),
                           capture_output=True, text=True)
    assert again.returncode == 0
    assert json.loads(again.stdout)['unmatched'] == 0


def weight_bundle(tmp_path, members, absent):
    """A bundle whose authenticated listing names the whole shared weight tree."""
    attempt_id = str(uuid.uuid4())
    generation = f'{tmp_path}/worker/attempts/{attempt_id}/materialized'
    runtime = f'{generation}/runtime'
    rows = member_rows(members) + [absent['row']]
    digest, normalized, document = write_listing(runtime, rows)
    listing = tmp_path / 'listing.json'
    listing.write_bytes(document)
    sources = tmp_path / 'sources'
    sources.mkdir(exist_ok=True)
    weights = []
    for name, data in sorted(members.items()):
        source = sources / Path(name).name
        source.write_bytes(data)
        weights.append(CacheTransferArtifact(source, f'{tmp_path}/worker/cache/artifacts/v1/weights/{digest}/{name}',
                                             hashlib.sha256(data).hexdigest(), len(data), 0o444, 'weights'))
    missing = sources / Path(absent['row']['name']).name
    missing.write_bytes(absent['payload'])
    weights.append(CacheTransferArtifact(missing, f'{tmp_path}/worker/cache/artifacts/v1/weights/{digest}/'
                                                   f'{absent["row"]["name"]}',
                                         absent['row']['sha256'], absent['row']['size_bytes'], 0o444, 'weights'))
    record = SimpleNamespace(relative_path='runtime/.bms-runtime-images.json',
                             sha256=hashlib.sha256(document).hexdigest(),
                             size_bytes=len(document), mode=0o644, role='runtime',
                             link_target=None)
    bundle = SimpleNamespace(
        attempt_id=attempt_id,
        envelope=SimpleNamespace(files=[record], environment={
            'BMS_WEIGHTS': f'{tmp_path}/worker/cache/artifacts/v1/weights/{digest}'}),
        weight_layout=tuple(normalized), runtime_weights=tuple(weights),
        remote_source_dir=f'{generation}/source', remote_runtime_dir=runtime,
        runtime_transfers=(TransferPlan(listing, runtime + '/.bms-runtime-images.json'),))
    assert [item.role for item in bundle.runtime_weights] == ['weights'] * len(weights)
    return SimpleNamespace(remote_root=str(tmp_path / 'worker')), bundle


def cloud(monkeypatch, local_transport, payloads):
    """The existing HF doubles: capability issuance and one CDN download.

    `payloads` maps the digest of each expected download to the bytes the CDN
    would serve for it, so a double can never hand one asset another's bytes.
    """
    calls, uploads = local_transport
    original = cache.run_remote
    issued, downloads = [], []

    async def sources(entries, *, check_fence):
        await check_fence()
        issued.extend(entries)
        return {(entry.role == 'image', entry.sha256):
                {'url': 'https://us.aws.cdn.hf.co/fixture?Signature=opaque-read-capability',
                 'expires_at': 2000000000} for entry in entries}

    async def run(connection, argv, input_bytes=None, **kwargs):
        request = json.loads(input_bytes) if input_bytes and '-c' not in argv else {}
        if request.get('action') != 'acquire_hf':
            return await original(connection, argv, input_bytes=input_bytes, **kwargs)
        calls.append(request)
        downloads.append(request)
        item = request['artifact']
        destination = (Path(connection.remote_root) / 'cache/artifacts/v1/incoming'
                       / request['operation_id'] / request['batch_id'] / item['sha256'])
        destination.write_bytes(payloads[item['sha256']])
        return SimpleNamespace(stdout=json.dumps({'state': 'downloaded', 'sha256': item['sha256'],
                                                 'size_bytes': item['size_bytes']}))

    monkeypatch.setattr(cache, 'run_remote', run)
    monkeypatch.setattr(cache.hf_assets, 'configuration', lambda: object())
    monkeypatch.setattr(cache.hf_assets, 'prepare_sources', sources)
    # The delivery floor is exercised on its own; these fixtures are tiny.
    monkeypatch.setattr(cache.hf_assets, 'MIN_BYTES', 1)
    return issued, downloads, calls, uploads


@pytest.mark.asyncio
async def test_controller_takes_the_shared_tree_from_one_archive(tmp_path, monkeypatch, local_transport):
    members = {'boltz/model.ckpt': b'M' * (64 * 1024), 'protenix/params.npz': b'N' * (96 * 1024)}
    absent = {'row': row('protenix/large.ckpt', b'O' * (32 * 1024)), 'payload': b'O' * (32 * 1024)}
    connection, bundle = weight_bundle(tmp_path, members, absent)
    path, digest, size = build_archive(tmp_path / 'weights.tar', members)
    issued, downloads, calls, uploads = cloud(monkeypatch, local_transport,
        {digest: path.read_bytes(), absent['row']['sha256']: absent['payload']})
    # Both the archive and the row it does not carry are weight assets of at
    # least one delivered object; the floor only decides batching.
    monkeypatch.setattr(cache, 'HF_MIN_BYTES', 4096)
    monkeypatch.setenv('BMS_HF_WEIGHT_ARCHIVE', f'{digest}:{size}')
    receipts = await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert [request['artifact']['sha256'] for request in downloads] == [digest, absent['row']['sha256']]
    assert [item.role for item in issued] == ['weights', 'weights']
    assert any(request['action'] == 'unpack_weights_archive' for request in calls)
    # Only the bundle listing was relayed as files; no weight member was.
    assert len(uploads) == 1 and Path(uploads[0]).name.startswith('bms-cache-batch-')
    relayed = [request for request in calls if request['action'] in {'ingest', 'ingest_many'}]
    assert sum(len(entry.get('artifacts', [])) for entry in relayed) == 1
    # The worker published the shared view from verified objects alone.
    root = Path(bundle.envelope.environment['BMS_WEIGHTS'])
    for name, data in members.items():
        assert (root / name).read_bytes() == data
    assert (root / absent['row']['name']).read_bytes() == absent['payload']
    assert {item.sha256 for item in bundle.runtime_weights} <= {item['sha256'] for item in receipts}
    # One further receipt is the bundle's own runtime listing, staged first.
    assert len(receipts) == len(bundle.runtime_weights) + 1
    assert not list((Path(connection.remote_root) / 'cache/artifacts/v1/incoming').glob('*/*'))


@pytest.mark.asyncio
async def test_without_a_declared_archive_the_relay_is_unchanged(tmp_path, monkeypatch, local_transport):
    members = {'boltz/model.ckpt': b'P' * 4096}
    absent = {'row': row('protenix/other.ckpt', b'Q' * 2048), 'payload': b'Q' * 2048}
    connection, bundle = weight_bundle(tmp_path, members, absent)
    _, downloads, calls, uploads = cloud(monkeypatch, local_transport, {})
    monkeypatch.setattr(cache.hf_assets, 'configuration', lambda: None)
    monkeypatch.delenv('BMS_HF_WEIGHT_ARCHIVE', raising=False)
    await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert downloads == []
    assert not any(request['action'] == 'unpack_weights_archive' for request in calls)
    assert len(uploads) == 2
    root = Path(bundle.envelope.environment['BMS_WEIGHTS'])
    assert (root / 'boltz/model.ckpt').read_bytes() == members['boltz/model.ckpt']


@pytest.mark.asyncio
async def test_incomplete_archive_fails_the_stage_without_a_view(tmp_path, monkeypatch, local_transport):
    members = {'boltz/model.ckpt': b'R' * 4096}
    absent = {'row': row('protenix/other.ckpt', b'S' * 2048), 'payload': b'S' * 2048}
    connection, bundle = weight_bundle(tmp_path, members, absent)
    full, _, _ = build_archive(tmp_path / 'full.tar', members)
    path, digest, size = build_archive(tmp_path / 'cut.tar', members,
                                       truncate=mid_second_member_offset(full, 0))
    _, downloads, _, _ = cloud(monkeypatch, local_transport, {digest: path.read_bytes()})
    monkeypatch.setenv('BMS_HF_WEIGHT_ARCHIVE', f'{digest}:{size}')
    # A worker-side refusal reaches the controller as a failed helper, exactly as
    # every other cache-owner failure does, and the stage publishes no view.
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert len(downloads) == 1
    assert not Path(bundle.envelope.environment['BMS_WEIGHTS']).exists()
