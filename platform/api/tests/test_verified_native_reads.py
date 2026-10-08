"""Portable native transport regressions. Definitions only until first-pass gate.

The storage harness publishes real receipt digests over real files, but replaces
only the global allocation/database fixture. Native qualification uses real
patched pysam and HTTP sockets, not mocked HTSlib responses.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from services import ngs_alignment_sessions as storage
from services import verified_native_reads as native
from tests.ngs_resource_fixture import ngs_resources


@pytest.mark.parametrize("value,size,expected", [
    (None, 10, (0, 10, 200)), ("bytes=3-", 10, (3, 10, 206)),
    ("bytes=2-4", 10, (2, 5, 206)), ("bytes=-3", 10, (7, 10, 206)),
    ("bytes=-100", 10, (0, 10, 206)), ("bytes=0-999", 10, (0, 10, 206)),
    (None, 0, (0, 0, 200)),
])
def test_range(value, size, expected):
    assert native.byte_range(value, size) == expected


@pytest.mark.parametrize("value", ["bytes=10-", "bytes=-0", "bytes=4-3", "bytes=0-1,3-4", "bytes=-", "items=0-1"])
def test_unsatisfiable_range(value):
    with pytest.raises(ValueError):
        native.byte_range(value, 10)


@pytest.fixture
def generations(tmp_path, monkeypatch):
    """Real chunk verification and generation pinning, isolated cache ownership."""
    lock = threading.RLock()
    monkeypatch.setattr(storage, "SNAPSHOT_CHUNK_BYTES", 4096)
    monkeypatch.setattr(storage, "_snapshot_cache_condition", threading.Condition(lock))
    monkeypatch.setattr(storage, "_snapshot_cache_leases", {})
    monkeypatch.setattr(storage, "_snapshot_receipts", {})
    monkeypatch.setattr(storage, "_snapshot_invalid", set())
    monkeypatch.setattr(storage, "_mark_snapshot_invalid", lambda digest: None)
    monkeypatch.setattr(storage, "_discard_cached_snapshot_locked", lambda digest: None)
    files = {}
    def cached(digest, size):
        receipt, path = files[digest]
        handle = path.open("rb")
        if storage._snapshot_file_identity(handle) != receipt.identity:
            handle.close()
            raise storage.AlignmentSessionError("snapshot integrity mismatch")
        storage._snapshot_cache_leases[digest] = storage._snapshot_cache_leases.get(digest, 0) + 1
        return storage._SnapshotLease(handle, digest, receipt)
    monkeypatch.setattr(storage, "_cached_snapshot_locked", cached)
    def publish(payload):
        digest = hashlib.sha256(payload).hexdigest()
        path = tmp_path / digest
        path.write_bytes(payload)
        handle = path.open("rb")
        receipt = storage._SnapshotReceipt(storage._snapshot_file_identity(handle), tuple(
            hashlib.sha256(payload[i:i+4096]).digest() for i in range(0, len(payload), 4096)))
        files[digest] = (receipt, path)
        storage._snapshot_receipts[digest] = receipt
        storage._snapshot_cache_leases[digest] = 1
        return storage._SnapshotLease(handle, digest, receipt), path
    return publish


def test_independent_cursors_and_no_descriptor(generations):
    lease, _ = generations(b"0123456789")
    with lease, lease.fork() as child:
        lease.seek(5)
        assert child.read(2) == b"01"
        assert lease.read(2) == b"56"
        with pytest.raises(storage.AlignmentSessionError, match="managed native"):
            lease.fileno()
    assert not storage._snapshot_cache_leases


@pytest.mark.parametrize("attack", ["overwrite", "truncate", "replace"])
def test_active_generation_rejects_mutation(generations, attack):
    lease, path = generations(b"a" * 8192)
    with lease:
        already_yielded = lease.read(4096)
        if attack == "replace":
            alternate = path.with_suffix(".new")
            alternate.write_bytes(b"b" * 8192)
            alternate.replace(path)
            with pytest.raises(storage.AlignmentSessionError):
                lease.fork()
        else:
            path.write_bytes(b"b" * (8192 if attack == "overwrite" else 10))
            with pytest.raises(storage.AlignmentSessionError):
                lease.read(4096)
        assert already_yielded == b"a" * 4096


def test_chunk_hash_not_metadata_is_authority(generations, monkeypatch):
    lease, path = generations(b"a" * 8192)
    identity = lease._receipt.identity
    path.write_bytes(b"b" * 8192)
    monkeypatch.setattr(storage, "_snapshot_file_identity", lambda handle: identity)
    with lease, pytest.raises(storage.AlignmentSessionError, match="integrity"):
        lease.read(1)


async def request(delivery, grant, *, method="GET", headers=(), name="data.bam", send_hook=None):
    messages = []
    never = asyncio.Event()
    async def receive():
        await never.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        messages.append(message)
        if send_hook:
            await send_hook(message)
    scope = {"type": "http", "method": method, "headers": list(headers),
             "path": native.PREFIX + grant.token + "/" + name, "query_string": b""}
    await delivery.serve(scope, receive, send, scope["path"])
    assert grant.token not in repr(scope)
    return messages


@pytest.fixture
def transport():
    delivery = native.Delivery()
    delivery.slots = 4
    delivery.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-byte-producer")
    yield delivery
    delivery.pool.shutdown(wait=True)


@pytest.mark.asyncio
async def test_http_range_head_conditional_allowlist(generations, transport):
    lease, _ = generations(b"0123456789")
    with lease:
        transport.loop = asyncio.get_running_loop()
        grant = native.Grant({"data.bam": lease})
        transport.grants[grant.token] = grant
        messages = await request(transport, grant, headers=[(b"range", b"bytes=3-")])
        assert messages[0]["status"] == 206
        assert b"".join(m.get("body", b"") for m in messages) == b"3456789"
        assert (b"content-range", b"bytes 3-9/10") in messages[0]["headers"]
        assert (await request(transport, grant, name="foreign.bai"))[0]["status"] == 404
        messages = await request(transport, grant, method="HEAD")
        assert messages[0]["status"] == 200 and messages[-1]["body"] == b""
        etag = ('"' + lease._digest + '"').encode()
        assert (await request(transport, grant, headers=[(b"if-none-match", etag)]))[0]["status"] == 304
        assert (await request(transport, grant, headers=[(b"range", b"bytes=10-")]))[0]["status"] == 416
        grant.closing = True
        assert (await request(transport, grant))[0]["status"] == 404
    assert transport.active == 0


@pytest.mark.asyncio
async def test_later_corruption_poisoned_result_boundary(generations, transport):
    lease, path = generations(b"a" * 8192)
    with lease:
        transport.loop = asyncio.get_running_loop()
        grant = native.Grant({"data.bam": lease})
        transport.grants[grant.token] = grant
        async def mutate(message):
            if message.get("body"):
                path.write_bytes(b"b" * 8192)
        with pytest.raises(storage.AlignmentSessionError):
            await request(transport, grant, send_hook=mutate)
        assert grant.failure == "integrity" and not grant.tasks and transport.active == 0


@pytest.mark.asyncio
async def test_capacity_is_not_corruption(generations, transport):
    lease, _ = generations(b"a")
    with lease:
        transport.loop = asyncio.get_running_loop()
        grant = native.Grant({"data.bam": lease})
        transport.grants[grant.token] = grant
        transport.slots = 0
        assert (await request(transport, grant))[0]["status"] == 503
        assert grant.failure == "capacity"
        assert not storage._snapshot_invalid


@pytest.mark.asyncio
async def test_cancel_waits_for_inflight_verification(generations, transport, monkeypatch):
    lease, _ = generations(b"a" * 8192)
    entered, release = threading.Event(), threading.Event()
    original = storage._SnapshotLease.read
    def blocked(self, size):
        entered.set()
        release.wait()
        return original(self, size)
    monkeypatch.setattr(storage._SnapshotLease, "read", blocked)
    with lease:
        transport.loop = asyncio.get_running_loop()
        grant = native.Grant({"data.bam": lease})
        transport.grants[grant.token] = grant
        task = asyncio.create_task(request(transport, grant))
        await asyncio.to_thread(entered.wait)
        task.cancel()
        await asyncio.sleep(0)
        assert transport.active == 1 and storage._snapshot_cache_leases[lease._digest] == 2
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.active == 0 and storage._snapshot_cache_leases[lease._digest] == 1


def test_binding_patch_covers_open_and_iterator_reopen():
    patch = (Path(__file__).parents[1] / "vendor/pysam/no-save-index.patch").read_text()
    assert "samfile.index_load_flags" in patch
    assert "self.index_load_flags)" in patch
    assert "self.save_remote_index or cindexname" in patch
    assert "+    cdef readonly bint save_remote_index" in patch


@pytest.mark.native_http
def test_native_lane_blocks_external_python_and_child_network(native_http):
    import socket
    import subprocess
    import sys
    import pytest_socket
    with socket.socket() as client:
        for address in (("192.0.2.1", 9), ("localhost", 9), ("0.0.0.0", 9)):
            with pytest.raises(pytest_socket.SocketBlockedError):
                client.connect(address)
        with pytest.raises(pytest_socket.SocketBlockedError):
            client.bind(("0.0.0.0", 0))
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.getaddrinfo("example.invalid", 9)
    with pytest.raises(pytest.UsageError):
        pytest_socket.enable_socket()
    # Fresh child has unpatched sockets (like C HTSlib), but inherits the same
    # kernel network namespace. TEST-NET never reaches a real endpoint.
    result = subprocess.run([sys.executable, "-c", """
import errno, os, socket, sys
assert os.readlink('/proc/self/ns/net') == sys.argv[1]
with socket.socket() as client:
    client.settimeout(1)
    assert client.connect_ex(('192.0.2.1', 9)) == errno.ENETUNREACH
print('external unreachable')
""", os.readlink("/proc/self/ns/net")], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "external unreachable"


@pytest.mark.native_http
def test_native_lane_restores_after_fixture_failure(request):
    import socket
    import pytest_socket
    from conftest import isolated_native_loopback, default_network_namespace_active
    qualified = SimpleNamespace(node=request.node, fixturenames=["native_http"])
    lane = isolated_native_loopback.__wrapped__(qualified, None)
    next(lane)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        lane.throw(RuntimeError("synthetic startup failure"))
    assert default_network_namespace_active()
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.socket()


@pytest.mark.native_http
def test_native_lane_refuses_unqualified_namespace(request, monkeypatch):
    import conftest
    qualified = SimpleNamespace(node=request.node, fixturenames=["native_http"])
    monkeypatch.setattr(conftest, "default_network_namespace_active", lambda: False)
    lane = conftest.isolated_native_loopback.__wrapped__(qualified, None)
    with pytest.raises(pytest.UsageError, match="route-free private netns"):
        next(lane)


def test_native_lane_restores_default_guards():
    import socket
    import pytest_socket
    from conftest import default_network_namespace_active
    assert default_network_namespace_active()
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.socket()


@pytest.mark.native_http
def test_native_marker_alone_does_not_enable_sockets():
    import socket
    import pytest_socket
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.socket()


def test_native_fixture_requires_marker(request):
    with pytest.raises(pytest.UsageError, match="native HTTP requires"):
        request.getfixturevalue("isolated_native_loopback")


def test_stock_runtime_is_rejected(monkeypatch):
    import pysam
    monkeypatch.setattr(pysam, "__version__", "0.23.3")
    with pytest.raises(storage.AlignmentCapacityUnavailable):
        native.require_runtime()


@pytest.mark.native_http
def test_real_native_no_save_index_and_iterator_reopen(tmp_path, generations, native_http, monkeypatch):
    import pysam
    native.require_runtime()
    bam_path = tmp_path / "fixture.bam"
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "ref", "LN": 200000}]}
    with pysam.AlignmentFile(bam_path, "wb", header=header) as output:
        for i in range(400):
            record = pysam.AlignedSegment(output.header)
            record.query_name = "read-" + str(i)
            record.query_sequence = "ACGT" * 125
            record.query_qualities = pysam.qualitystring_to_array("I" * 500)
            record.reference_id = 0
            record.reference_start = i * 100
            record.mapping_quality = 60
            record.cigarstring = "500M"
            output.write(record)
    pysam.index(str(bam_path))
    bam, _ = generations(bam_path.read_bytes())
    index, _ = generations(Path(str(bam_path) + ".bai").read_bytes())
    working = tmp_path / "hostile-cwd"
    working.mkdir()
    monkeypatch.chdir(working)
    hostile = working / "data.bam.bai"
    hostile.write_bytes(b"not a BAI: must never be read or replaced")
    before = {p.name: p.read_bytes() for p in working.iterdir()}
    with bam, index, native.alignment(bam, index) as source:
        assert source.save_remote_index is False and source.check_index()
        locators = []
        while True:
            offset = source.tell()
            try:
                record = next(source)
            except StopIteration:
                break
            locators.append((offset, record.query_name))
        assert len(locators) == 400 and locators[-1][0] >> 16 > 0
        for offset, name in locators[::47]:
            source.seek(offset)
            assert next(source).query_name == name
        first = source.fetch("ref", 1000, 2000, multiple_iterators=True)
        second = source.fetch("ref", 1000, 2000, multiple_iterators=True)
        assert [r.query_name for r in first] == [r.query_name for r in second]
    assert {p.name: p.read_bytes() for p in working.iterdir()} == before


@pytest.mark.native_http
def test_real_native_literal_fasta_sidecar(tmp_path, generations, native_http):
    import pysam
    native.require_runtime()
    path = tmp_path / "reference.fa"
    path.write_text(">ref\nACGTACGT\n")
    pysam.faidx(str(path))
    fasta, _ = generations(path.read_bytes())
    index, _ = generations(Path(str(path) + ".fai").read_bytes())
    with fasta, index, native.fasta(fasta, index) as source:
        assert source.fetch("ref", 2, 7) == "GTACG"


@pytest.mark.asyncio
async def test_wrong_worker_cannot_resolve_capability(generations, transport):
    lease, _ = generations(b"private")
    with lease:
        transport.loop = asyncio.get_running_loop()
        foreign_grant = native.Grant({"data.bam": lease})
        assert (await request(transport, foreign_grant))[0]["status"] == 404
        assert not transport.grants and transport.active == 0


@pytest.mark.asyncio
async def test_concurrent_ranges_have_independent_cursors(generations, transport):
    payload = bytes(range(256)) * 64
    lease, _ = generations(payload)
    with lease:
        transport.loop = asyncio.get_running_loop()
        grant = native.Grant({"data.bam": lease})
        transport.grants[grant.token] = grant
        results = await asyncio.gather(*[
            request(transport, grant, headers=[(b"range", f"bytes={start}-{start+1000}".encode())])
            for start in (5, 3070, 8191)])
        for start, messages in zip((5, 3070, 8191), results):
            assert b"".join(m.get("body", b"") for m in messages) == payload[start:start+1001]
        assert lease.tell() == 0 and not grant.tasks


@pytest.mark.asyncio
async def test_native_worker_cancellation_revokes_then_quiesces(generations, transport, monkeypatch):
    lease, _ = generations(b"data")
    transport.loop = asyncio.get_running_loop()
    transport.origin = "http://test.invalid"
    monkeypatch.setattr(native, "delivery", transport)
    entered, exited = threading.Event(), threading.Event()
    def consumer():
        try:
            with transport.grant({"data.bam": lease}):
                event = native._cancellation.get()
                entered.set()
                event.wait()
        finally:
            exited.set()
    with lease:
        task = asyncio.create_task(native.run_in_threadpool(consumer))
        await asyncio.to_thread(entered.wait)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set() and not transport.grants
        assert storage._snapshot_cache_leases[lease._digest] == 1


@pytest.mark.native_http
def test_real_fastx_preserves_sequence_quality(generations, native_http):
    source, _ = generations(b"@read\nACGT\n+\nIIII\n")
    with source, native.fastx(source, persist=False) as fastq:
        record = next(fastq)
        assert (record.name, record.sequence, record.quality) == ("read", "ACGT", "IIII")
        with pytest.raises(StopIteration):
            next(fastq)


def test_compute_context_uses_existing_allocation_without_double_charge(monkeypatch):
    from services import global_resource_admission as resources
    allocation = SimpleNamespace(_closed=False, cpu_threads=1, dram_bytes=4096)
    monkeypatch.setattr(resources, "reserve", lambda **kw: pytest.fail("unexpected double reservation"))
    with resources.use_compute(allocation):
        with native.compute() as actual:
            assert actual is allocation
    assert resources.current_compute() is None


@pytest.mark.native_http
def test_fastx_missing_http_source_is_io_error_not_null_dereference(native_http):
    pysam = native.require_runtime()
    with pytest.raises(OSError):
        with pysam.FastxFile(native.delivery.origin + native.PREFIX + "expired/data.fastx") as reads:
            list(reads)


@pytest.mark.parametrize("content", [b'{"htsget":{"urls":[]}}', b"CRAM\x03\x00", b"@SQ\tSN:ref\n"])
def test_native_format_guard_rejects_external_dispatch(generations, content):
    source, _ = generations(content)
    with source, pytest.raises(storage.AlignmentSessionError):
        native.require_bam_bytes(source)


@pytest.mark.native_http
def test_explicit_bcf_csi_fetch_reopen_ignores_cwd(tmp_path, generations, native_http, monkeypatch):
    pysam = native.require_runtime()
    from pysam import bcftools
    path = tmp_path / "variant.bcf"
    header = pysam.VariantHeader()
    header.contigs.add("ref", length=100)
    with pysam.VariantFile(str(path), "wb", header=header) as out:
        out.write(out.new_record(contig="ref", start=9, stop=10, alleles=("A", "C")))
    bcftools.index("-f", str(path))
    source, _ = generations(path.read_bytes())
    index, _ = generations(Path(str(path) + ".csi").read_bytes())
    working = tmp_path / "bcf-cwd"
    working.mkdir()
    hostile = working / "data.bcf.csi"
    hostile.write_bytes(b"untrusted")
    monkeypatch.chdir(working)
    with source, index, native.variant(source, index) as variants:
        assert [(v.contig, v.pos, v.alleles) for v in variants.fetch("ref", 0, 100, reopen=True)] == [("ref", 10, ("A", "C"))]
    assert hostile.read_bytes() == b"untrusted"
    assert {p.name for p in working.iterdir()} == {"data.bcf.csi"}


@pytest.mark.asyncio
async def test_delivery_budget_is_globally_admitted_and_released(tmp_path, monkeypatch):
    from services import global_resource_admission as resources
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "HTS_PATH", "HTS_TRACE", "REF_PATH", "REF_CACHE", "HTS_AUTH_LOCATION",
                "HTS_ALLOW_UNENCRYPTED_AUTHORIZATION_HEADER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BMS_NATIVE_READ_ORIGIN", "http://127.0.0.1:8000")
    monkeypatch.setenv("BMS_NATIVE_READ_TRUST_LOOPBACK", "1")
    monkeypatch.setenv("BMS_NATIVE_READ_THREADS", "2")
    memory = 16 * storage.SNAPSHOT_CHUNK_BYTES
    monkeypatch.setenv("BMS_NATIVE_READ_DRAM_BYTES", str(memory))
    monkeypatch.setattr(native, "require_runtime", lambda: SimpleNamespace(set_verbosity=lambda x: None))
    monkeypatch.setattr(storage, "_snapshot_cache_directory", lambda: tmp_path)
    calls, released = [], []
    def reserve(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(release=lambda **kw: released.append(kw))
    monkeypatch.setattr(resources, "reserve", reserve)
    service = native.Delivery()
    await service.start()
    assert calls == [dict(owner="verified-native-delivery", storage_root=tmp_path,
                          cpu_threads=2, dram_bytes=memory, disk_bytes=0)]
    assert service.slots == memory // (4 * storage.SNAPSHOT_CHUNK_BYTES)
    await service.stop()
    assert released == [{"storage_removed": True}] and service.pool is None


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["http://example.org", "https://example.org", "http://user@127.0.0.1", "http://127.0.0.1/path"])
async def test_nonlocal_or_credential_origin_is_unavailable(monkeypatch, origin):
    monkeypatch.setenv("BMS_NATIVE_READ_ORIGIN", origin)
    with pytest.raises(storage.AlignmentCapacityUnavailable):
        await native.Delivery().start()


@pytest.mark.native_http
def test_generated_index_reads_verified_http_and_reused_workspace_generation(tmp_path, monkeypatch, ngs_resources, native_http):
    import pysam
    monkeypatch.setattr(pysam, "index", lambda *a, **k: pytest.fail("URL-rejecting dispatcher used"))
    path = tmp_path / "generated.bam"
    index_path = Path(str(path) + ".bai")
    for name in ("first", "replacement"):
        replacement = tmp_path / "next.bam"
        with pysam.AlignmentFile(str(replacement), "wb", header={
            "HD": {"VN": "1.6", "SO": "coordinate"},
            "SQ": [{"SN": "ref", "LN": 100}],
        }) as output:
            read = pysam.AlignedSegment(output.header)
            read.query_name = name
            read.query_sequence = "ACGT"
            read.flag = 0
            read.reference_id = 0
            read.reference_start = 3
            read.mapping_quality = 60
            read.cigarstring = "4M"
            output.write(read)
        replacement.replace(path)
        index_path.unlink(missing_ok=True)
        native.index_path(path)
        assert index_path.is_file() and index_path.stat().st_size > 0
        with path.open("rb") as generated, index_path.open("rb") as generated_index, \
                native.alignment(generated, generated_index) as bam:
            assert [r.query_name for r in bam.fetch("ref", 0, 20)] == [name]
