"""Managed API transport for receipt-bound native readers (not an artifact store).

Install NativeReadMiddleware on the existing API. The configured origin MUST route
back to this process; multi-process deployments need per-worker private origins.
Capabilities never persist and a wrong worker fails closed. Only callers already
holding authorized verified generation leases can grant access. Native operations
must run off the serving event loop. No descriptor is exposed to HTSlib.
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar, copy_context
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import asyncio
import os
import re
import secrets
import threading
from urllib.parse import urlsplit

PREFIX = "/_bms_native/"
_cancellation = ContextVar("native_read_cancellation", default=None)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def storage():
    from services import ngs_alignment_sessions
    return ngs_alignment_sessions


def unavailable():
    return storage().AlignmentCapacityUnavailable("verified native runtime unavailable")


def byte_range(value, size):
    """One RFC byte range. No multipart, compression, or guessed satisfiability."""
    if value is None:
        return 0, size, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError("unsatisfiable range")
    first, last = match.groups()
    if not first:
        count = int(last)
        if not count:
            raise ValueError("unsatisfiable range")
        return max(0, size - count), size, 206
    start = int(first)
    end = min(size, int(last) + 1) if last else size
    if start >= size or end <= start:
        raise ValueError("unsatisfiable range")
    return start, end, 206


@dataclass(eq=False)
class Grant:
    handles: dict
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    failure: str | None = None
    closing: bool = False
    cancellation: object = None
    tasks: set = field(default_factory=set)


async def _quiesce(future):
    """Cleanup must outlive repeated task cancellation, not its resource owner."""
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    return future.result()


class Delivery:
    def __init__(self):
        self.condition = threading.Condition()
        self.grants = {}
        self.loop = None
        self.pool = None
        self.allocation = None
        self.origin = None
        self.active = 0
        self.slots = 0
        self.stopping = False

    async def start(self):
        origin = os.environ.get("BMS_NATIVE_READ_ORIGIN")
        if not origin:
            return  # Explicitly unavailable, never an FD fallback.
        url = urlsplit(origin)
        # Only a literal, same-process loopback listener is qualified by this
        # implementation. A remote C connect cannot yet be forcibly canceled.
        if (url.scheme not in {"http", "https"} or url.hostname not in {"127.0.0.1", "::1"} or url.username
                or url.password or url.query or url.fragment or url.path not in {"", "/"}
                or (url.scheme == "http" and (url.hostname not in {"127.0.0.1", "::1"}
                    or os.environ.get("BMS_NATIVE_READ_TRUST_LOOPBACK") != "1"))):
            raise unavailable()
        # libcurl obeys these variables. Reject instead of mutating process-wide
        # environment or accidentally exporting a capability to a proxy/logger.
        forbidden = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                     "HTS_PATH", "HTS_TRACE", "REF_PATH", "REF_CACHE",
                     "HTS_AUTH_LOCATION", "HTS_ALLOW_UNENCRYPTED_AUTHORIZATION_HEADER")
        if any(os.environ.get(key) for key in forbidden):
            raise unavailable()
        from services import global_resource_admission as resources
        try:
            threads = int(os.environ["BMS_NATIVE_READ_THREADS"])
            memory = int(os.environ["BMS_NATIVE_READ_DRAM_BYTES"])
        except (KeyError, ValueError):
            raise unavailable() from None
        require_runtime().set_verbosity(0)
        chunk = storage().SNAPSHOT_CHUNK_BYTES
        if threads < 1 or memory < 4 * chunk:
            raise unavailable()
        # Reserve BEFORE derived work can consume remaining target capacity.
        if self.allocation is not None or self.pool is not None:
            raise unavailable()
        loop = asyncio.get_running_loop()
        reservation = loop.run_in_executor(None, partial(resources.reserve,
            owner="verified-native-delivery", storage_root=storage()._snapshot_cache_directory(),
            cpu_threads=threads, dram_bytes=memory, disk_bytes=0))
        try:
            self.allocation = await asyncio.shield(reservation)
            self.slots = memory // (4 * chunk)
            self.pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="verified-byte-producer")
        except BaseException:
            # A canceled await does not cancel the ledger transaction thread.
            try:
                allocation = await _quiesce(reservation)
            except Exception:
                pass
            else:
                await _quiesce(loop.run_in_executor(None,
                    partial(allocation.release, storage_removed=True)))
            self.allocation = None
            raise
        self.loop = asyncio.get_running_loop()
        self.stopping = False
        self.origin = origin.rstrip("/")

    async def stop(self):
        with self.condition:
            self.stopping = True
            grants = list(self.grants.values())
            for grant in grants:
                grant.closing = True
                for task in list(grant.tasks):
                    task.cancel()
        await asyncio.to_thread(self._drain)
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
        if self.allocation:
            await asyncio.to_thread(self.allocation.release, storage_removed=True)
        self.origin = self.loop = self.pool = self.allocation = None

    def _drain(self):
        with self.condition:
            while self.active or self.grants:
                self.condition.wait()

    @contextmanager
    def grant(self, handles):
        if self.origin is None or not handles or any(not NAME.fullmatch(n) for n in handles):
            raise unavailable()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise unavailable()  # Synchronous HTSlib would deadlock its producer.
        with ExitStack() as stack:
            pinned = {name: stack.enter_context(handle.fork()) for name, handle in handles.items()}
            grant = Grant(pinned, cancellation=_cancellation.get())
            with self.condition:
                if (self.stopping or len(self.grants) >= self.slots
                        or grant.cancellation is not None and grant.cancellation.is_set()):
                    raise unavailable()
                self.grants[grant.token] = grant
            try:
                yield {name: self.origin + PREFIX + grant.token + "/" + name for name in handles}
            finally:
                with self.condition:
                    grant.closing = True
                    self.grants.pop(grant.token, None)
                    self.condition.notify_all()
                    for task in list(grant.tasks):
                        self.loop.call_soon_threadsafe(task.cancel)
                    while grant.tasks:
                        self.condition.wait()
                for handle in pinned.values():
                    handle._check()
                if grant.failure == "capacity":
                    raise unavailable()
                if grant.failure:
                    raise storage().AlignmentSessionError("verified native delivery failed")

    def cancel(self, cancellation):
        with self.condition:
            for grant in self.grants.values():
                if grant.cancellation is cancellation:
                    grant.closing = True
                    for task in list(grant.tasks):
                        self.loop.call_soon_threadsafe(task.cancel)

    async def serve(self, scope, receive, send, path):
        # Retain capability only in private local variables, never log scope.
        scope["path"] = PREFIX + "[redacted]"
        scope["raw_path"] = scope["path"].encode()
        scope["query_string"] = b""
        async def respond(status, headers=()):
            await send({"type": "http.response.start", "status": status,
                        "headers": list(headers) + [(b"cache-control", b"no-store"), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
        parts = path[len(PREFIX):].split("/")
        if len(parts) != 2 or scope["method"] not in {"GET", "HEAD"}:
            return await respond(404)
        task = asyncio.current_task()
        with self.condition:
            grant = self.grants.get(parts[0])
            if (not grant or grant.closing or grant.failure or parts[1] not in grant.handles
                    or grant.cancellation is not None and grant.cancellation.is_set()):
                grant = None
            elif self.active >= self.slots:
                grant.failure = "capacity"
                return_status = 503
            else:
                return_status = None
                self.active += 1
                grant.tasks.add(task)
        if grant is None:
            return await respond(404)
        if return_status:
            return await respond(return_status)
        lease = None
        pending = None
        async def disconnected():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    task.cancel()
                    return
        watcher = asyncio.create_task(disconnected())
        def verified_read(amount):
            try:
                return lease.read(amount)
            except Exception:
                grant.failure = "integrity"
                raise storage().AlignmentSessionError("verified native read failed") from None
        try:
            lease = grant.handles[parts[1]].fork()
            size = lease._receipt.identity[2]
            etag = ('"' + lease._digest + '"').encode()
            headers = {k.lower(): v for k, v in scope["headers"]}
            # Reject ambiguous duplicate representation/range headers.
            if sum(k.lower() == b"range" for k, v in scope["headers"]) > 1:
                return await respond(416, [(b"content-range", f"bytes */{size}".encode())])
            if headers.get(b"if-none-match") in {etag, b"*"}:
                return await respond(304, [(b"etag", etag)])
            value = headers.get(b"range")
            if b"if-range" in headers and headers[b"if-range"] != etag:
                value = None
            try:
                start, end, status = byte_range(value.decode("ascii") if value else None, size)
            except (ValueError, UnicodeError):
                return await respond(416, [(b"content-range", f"bytes */{size}".encode())])
            lease.seek(start)
            response_headers = [(b"content-length", str(end-start).encode()), (b"etag", etag),
                (b"accept-ranges", b"bytes"), (b"cache-control", b"no-store, no-transform"),
                (b"content-type", b"application/octet-stream"), (b"x-content-type-options", b"nosniff")]
            if status == 206:
                response_headers.append((b"content-range", f"bytes {start}-{end-1}/{size}".encode()))
            await send({"type": "http.response.start", "status": status, "headers": response_headers})
            if scope["method"] != "HEAD":
                while start < end:
                    if grant.closing:
                        raise asyncio.CancelledError()
                    pending = self.loop.run_in_executor(self.pool, verified_read,
                        min(storage().SNAPSHOT_CHUNK_BYTES, end-start))
                    block = await asyncio.shield(pending)
                    pending = None
                    if not block:
                        raise storage().AlignmentSessionError("verified native short read")
                    start += len(block)
                    await send({"type": "http.response.body", "body": block, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except asyncio.CancelledError:
            # The C reader may still be verifying a chunk. Never close/release
            # its lease until that read has quiesced.
            while pending is not None and not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    grant.failure = "integrity"
            if pending is not None and pending.done() and not pending.cancelled():
                if pending.exception() is not None:
                    grant.failure = "integrity"
            raise
        except (ConnectionError, OSError):
            # Native seeks legitimately disconnect full/open-ended responses.
            # Integrity exceptions are separate from socket backpressure.
            raise
        except Exception:
            grant.failure = "integrity"
            raise storage().AlignmentSessionError("verified native delivery failed") from None
        finally:
            watcher.cancel()
            if lease:
                lease.close()
            with self.condition:
                grant.tasks.discard(task)
                self.active -= 1
                self.condition.notify_all()


delivery = Delivery()


class NativeReadMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "http" and path.startswith(PREFIX):
            return await delivery.serve(scope, receive, send, path)
        return await self.app(scope, receive, send)


@contextmanager
def snapshot_handle(handle):
    if isinstance(handle, storage()._SnapshotLease):
        with handle.fork() as snapshot:
            yield snapshot
        return
    import hashlib
    source = storage()._DescriptorImport(handle)
    known = getattr(handle, "_bms_verified_identity", None)
    if known is not None:
        expected, identity = known
        if identity != source.identity:
            raise storage().AlignmentSessionError("source changed after receipt verification")
    else:
        digest = hashlib.sha256()
        offset = 0
        while offset < source.identity[2]:
            block = os.pread(handle.fileno(), min(storage().SNAPSHOT_CHUNK_BYTES, source.identity[2] - offset), offset)
            if not block:
                raise storage().AlignmentSessionError("source truncated before native import")
            digest.update(block)
            offset += len(block)
        expected = digest.hexdigest()
    if storage()._snapshot_file_identity(handle) != source.identity:
        raise storage().AlignmentSessionError("source changed before native import")
    with storage().open_verified_artifact_snapshot(source, expected_size=source.identity[2],
            expected_sha256=expected) as snapshot:
        yield snapshot
    if storage()._snapshot_file_identity(handle) != source.identity:
        raise storage().AlignmentSessionError("source changed during native read")


@contextmanager
def compute():
    from services import global_resource_admission as resources
    current = resources.current_compute()
    try:
        if current is not None:
            with resources.use_compute(current):
                yield current
        else:
            with resources.derived_work(owner="verified-native-parser",
                    storage_root=storage()._snapshot_cache_directory(), cpu_threads=1, disk_bytes=0) as allocation:
                yield allocation
    except resources.ResourceCapacityUnavailable:
        raise unavailable() from None


def require_delivery():
    if delivery.origin is None or delivery.loop is None or delivery.stopping:
        raise unavailable()


def require_runtime():
    import pysam
    from pysam import config
    try:
        from pysam.bms_native_build import IDENTITY
    except ImportError:
        raise unavailable() from None
    import hashlib
    import json
    from pathlib import Path
    build = Path(__file__).parents[1] / "vendor/pysam"
    expected = json.loads((build / "upstream.json").read_text())
    if (IDENTITY.get("upstream_sha256") != expected["sha256"]
            or IDENTITY.get("backend_sha256") != hashlib.sha256((build / "bms_build.py").read_bytes()).hexdigest()
            or IDENTITY.get("patch_sha256") != hashlib.sha256((build / "no-save-index.patch").read_bytes()).hexdigest()
            or IDENTITY.get("build_requirements_sha256") != hashlib.sha256((build / "build-requirements.lock").read_bytes()).hexdigest()
            or IDENTITY.get("binding_api") != 1 or config.HAVE_LIBCURL != 1 or config.HTSLIB != "builtin"
            or pysam.__version__ != "0.23.3+bms1"
            or not hasattr(pysam.AlignmentFile, "save_remote_index")):
        raise unavailable()
    return pysam


def require_bam_bytes(handle):
    # HTSlib's hts_open can dispatch htsget JSON to external URLs. Establish
    # BAM format from guarded bytes BEFORE native format autodetection.
    import gzip
    try:
        with handle.fork() as sniff, gzip.GzipFile(fileobj=sniff) as compressed:
            if compressed.read(4) != b"BAM\x01":
                raise ValueError("not BAM")
    except (OSError, ValueError, EOFError):
        raise storage().AlignmentSessionError("native input is not a receipt-bound BAM") from None


@contextmanager
def alignment(handle, index=None, **kwargs):
    require_delivery()
    pysam = require_runtime()
    handles = {"data.bam": handle}
    if index is not None:
        handles["data.bam.bai"] = index
    with ExitStack() as stack:
        handles = {name: stack.enter_context(snapshot_handle(value)) for name, value in handles.items()}
        stack.enter_context(compute())
        require_bam_bytes(handles["data.bam"])
        urls = stack.enter_context(delivery.grant(handles))
        try:
            with pysam.AlignmentFile(urls["data.bam"], "rb", save_remote_index=False,
                    index_filename=urls.get("data.bam.bai"), require_index=index is not None,
                    **kwargs) as bam:
                yield bam
        except storage().AlignmentSessionError:
            raise
        except (OSError, ValueError):
            raise storage().AlignmentSessionError("verified native BAM read failed") from None


@contextmanager
def fasta(handle, index):
    require_delivery()
    pysam = require_runtime()
    with snapshot_handle(handle) as handle, snapshot_handle(index) as index, \
            compute(), delivery.grant({"reference.fa": handle, "reference.fa.fai": index}) as urls:
        try:
            with pysam.FastaFile(urls["reference.fa"]) as reference:
                yield reference
        except storage().AlignmentSessionError:
            raise
        except (OSError, ValueError):
            raise storage().AlignmentSessionError("verified native FASTA read failed") from None


@contextmanager
def alignment_path(path, *, sha256=None, size=None, index=None, index_sha256=None, index_size=None, **kwargs):
    """Import a producer/accepted path once, then use only generation bytes."""
    require_delivery()
    with ExitStack() as stack:
        if sha256 is None or size is None:
            sha256, size = storage()._sha256_file_and_size(path)
        handle = stack.enter_context(storage().open_verified_artifact_snapshot(
            path, expected_sha256=sha256, expected_size=size))
        peer = None
        if index is not None:
            if index_sha256 is None or index_size is None:
                index_sha256, index_size = storage()._sha256_file_and_size(index)
            peer = stack.enter_context(storage().open_verified_artifact_snapshot(
                index, expected_sha256=index_sha256, expected_size=index_size))
        with alignment(handle, peer, **kwargs) as bam:
            yield bam


@contextmanager
def variant(handle, index=None):
    """BCF explicit CSI uses upstream bcf_index_load2 -> hts_idx_load3(flags=0).

    Uncompressed VCF has no native index. BGZF VCF/TBI remains unsupported here:
    pinned tbx_index_load2 saves remote indices and is NOT covered by BAM patch.
    """
    require_delivery()
    import gzip
    pysam = require_runtime()
    with ExitStack() as stack:
        source = stack.enter_context(snapshot_handle(handle))
        with source.fork() as sniff:
            magic = sniff.read(2)
            sniff.seek(0)
            if magic == b"\x1f\x8b":
                with gzip.GzipFile(fileobj=sniff) as compressed:
                    if compressed.read(5) != b"BCF\x02\x02" or index is None:
                        raise unavailable()
                basename = "data.bcf"
            else:
                if index is not None:
                    raise unavailable()
                if not sniff.read(16).startswith(b"##fileformat=VCF"):
                    raise storage().AlignmentSessionError("native input is not VCF")
                basename = "data.vcf"
        handles = {basename: source}
        if index is not None:
            handles[basename + ".csi"] = stack.enter_context(snapshot_handle(index))
        stack.enter_context(compute())
        urls = stack.enter_context(delivery.grant(handles))
        try:
            with pysam.VariantFile(urls[basename], index_filename=urls.get(basename + ".csi")) as variants:
                yield variants
        except storage().AlignmentSessionError:
            raise
        except (OSError, ValueError):
            raise storage().AlignmentSessionError("verified native variant read failed") from None


@contextmanager
def path_urls(path, index=None):
    require_delivery()
    with ExitStack() as stack:
        handles = {}
        for name, value in (("data.bam", path), ("data.bam.bai", index)):
            if value is None:
                continue
            digest, size = storage()._sha256_file_and_size(value)
            handles[name] = stack.enter_context(storage().open_verified_artifact_snapshot(
                value, expected_sha256=digest, expected_size=size))
        stack.enter_context(compute())
        require_bam_bytes(handles["data.bam"])
        with delivery.grant(handles) as urls:
            yield urls


def index_path(path):
    require_runtime()
    import ctypes
    import pysam.libchtslib
    # pysam's samtools dispatcher rejects URLs with os.path.exists before
    # invoking HTSlib. Use the public API in the same authenticated library;
    # CDLL releases the GIL so managed delivery can make progress.
    index = ctypes.CDLL(pysam.libchtslib.__file__).sam_index_build3
    index.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    index.restype = ctypes.c_int
    # This file is a freshly generated derivative, not an immutable path input.
    # Reused workspace names must bind to the exact newly opened generation.
    with storage()._open_regular_file_no_symlinks(path) as generated, \
            snapshot_handle(generated) as snapshot, compute(), \
            delivery.grant({"data.bam": snapshot}) as urls:
        require_bam_bytes(snapshot)
        try:
            status = index(urls["data.bam"].encode(), os.fsencode(str(path) + ".bai"), 0, 1)
            if status != 0:
                raise ValueError("native index failed")
        except Exception:
            raise storage().AlignmentSessionError("verified native index generation failed") from None


async def run_in_threadpool(func, *args, **kwargs):
    """Cancelable native consumer; keep ownership until the blocking call exits.

    Producer work uses Delivery.pool, never this executor. Cancellation revokes
    every associated HTTP stream/reopen before awaiting native quiescence.
    """
    cancel = threading.Event()
    token = _cancellation.set(cancel)
    try:
        context = copy_context()
        future = asyncio.get_running_loop().run_in_executor(None, context.run, partial(func, *args, **kwargs))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            cancel.set()
            delivery.cancel(cancel)
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if future.done() and not future.cancelled():
                future.exception()  # consume failure without exposing capabilities
            raise
    finally:
        _cancellation.reset(token)


@contextmanager
def fastx(handle, **kwargs):
    require_delivery()
    pysam = require_runtime()
    with snapshot_handle(handle) as snapshot, compute(), delivery.grant({"data.fastx": snapshot}) as urls:
        try:
            with pysam.FastxFile(urls["data.fastx"], **kwargs) as records:
                yield records
        except storage().AlignmentSessionError:
            raise
        except (OSError, ValueError):
            raise storage().AlignmentSessionError("verified native FASTX read failed") from None
