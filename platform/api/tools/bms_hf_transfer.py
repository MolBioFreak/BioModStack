"""Scoped HF delivery only: no credentials, redirects or cache publication.

The source capability is transient stdin data. Never include it, HTTP response
bodies, or transport exception text in diagnostics. All callers must separately
fence publication through the existing artifact cache owner.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import os
import re
import queue
import socket
import ssl
import threading
import time
from urllib.parse import parse_qsl, unquote, urlsplit

APPROVED_HOST = "us.aws.cdn.hf.co"
MAX_SOURCE_LIFETIME = 3600
RANGE_BYTES = 8 * 1024 * 1024
PARALLEL_RANGES = 8
REQUEST_TIMEOUT = 30
RANGE_TIMEOUT = 120
TRANSFER_TIMEOUT = 1800
MAX_ARTIFACT_BYTES = 10**12


class TransferError(ValueError):
    """Fixed safe diagnostic only; never constructed from external prose."""


class SourceExpired(TransferError):
    def __init__(self):
        super().__init__("hf_source_expired")


def validate_source(source: dict) -> tuple[str, int]:
    """Validate a capability without networking; returned URL must stay private."""
    try:
        if not isinstance(source, dict) or set(source) != {"url", "expires_at"}:
            raise TransferError("hf_invalid_source")
        url, expiry = source["url"], source["expires_at"]
        if (not isinstance(url, str) or not 1 <= len(url) <= 16384
                or type(expiry) is not int
                or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
                or any(ord(c) < 32 or ord(c) == 127 for c in unquote(url))):
            raise TransferError("hf_invalid_source")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != APPROVED_HOST
                or parsed.netloc not in (APPROVED_HOST, APPROVED_HOST + ":443")
                or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or parsed.fragment or "#" in url
                or not parsed.path.startswith("/") or "\\" in unquote(url)):
            raise TransferError("hf_invalid_source")
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=64)
        required = ("Signature", "Policy", "Key-Pair-Id", "Expires")
        values = {}
        for key in required:
            matches = [value for name, value in pairs if name == key]
            if len(matches) != 1 or not matches[0]:
                raise TransferError("hf_invalid_source")
            values[key] = matches[0]
        if not re.fullmatch(r"[0-9]{1,12}", values["Expires"]) or int(values["Expires"]) != expiry:
            raise TransferError("hf_invalid_source")
        remaining = expiry - time.time()
        if remaining <= 0:
            raise SourceExpired()
        if remaining > MAX_SOURCE_LIFETIME:
            raise TransferError("hf_source_lifetime_exceeded")
        return url, expiry
    except TransferError:
        raise
    except Exception:
        raise TransferError("hf_invalid_source") from None


def _check_deadline(expiry, deadline):
    if time.time() >= expiry:
        raise SourceExpired()
    if time.monotonic() >= deadline:
        raise TransferError("hf_transfer_timeout")


def _connect_socket(address, timeout, source_address=None):
    # getaddrinfo has no timeout argument. Isolate just that read-only resolver
    # in a daemon: stalled libc DNS cannot hold acquisition/thread-pool shutdown.
    deadline = time.monotonic() + timeout
    resolved = queue.Queue(maxsize=1)
    def resolve():
        try:
            resolved.put(socket.getaddrinfo(APPROVED_HOST, 443, type=socket.SOCK_STREAM))
        except Exception:
            resolved.put(None)
    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = resolved.get(timeout=timeout)
    except queue.Empty:
        raise TransferError("hf_transport_timeout") from None
    for family, kind, protocol, _, target in (addresses or [])[:16]:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(remaining)
            sock.connect(target)
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            return sock
        except Exception:
            sock.close()
    raise TransferError("hf_transport_failed")


def _fetch_range(url, expiry, start, end, size, deadline):
    """One direct TLS connection; http.client does not follow redirects/proxies."""
    connection = None
    response = None
    watchdog = None
    expired_deadline = threading.Event()
    try:
        deadline = min(deadline, time.monotonic() + RANGE_TIMEOUT,
                       time.monotonic() + max(0, expiry - time.time()))
        _check_deadline(expiry, deadline)
        parsed = urlsplit(url)
        connection = http.client.HTTPSConnection(APPROVED_HOST, port=443,
            timeout=min(REQUEST_TIMEOUT, deadline - time.monotonic()),
            context=ssl.create_default_context())
        connection._create_connection = _connect_socket
        connection.connect()
        _check_deadline(expiry, deadline)
        transport = connection.sock
        def interrupt():
            expired_deadline.set()
            # shutdown, not merely close: HTTPResponse retains a socket file.
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        watchdog = threading.Timer(max(0.001, deadline - time.monotonic()), interrupt)
        watchdog.daemon = True
        watchdog.start()
        connection.request("GET", parsed.path + "?" + parsed.query, headers={
            "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        _check_deadline(expiry, deadline)
        if response.status in (401, 403):
            raise TransferError("hf_authorization_failed")
        if response.status != 206:
            raise TransferError("hf_range_response_invalid")
        headers = response.getheaders()
        def one(name):
            values = [value for key, value in headers if key.lower() == name]
            if len(values) != 1:
                raise TransferError("hf_range_response_invalid")
            return values[0]
        length = end - start + 1
        if (one("content-range") != f"bytes {start}-{end}/{size}"
                or one("content-length") != str(length)
                or any(key.lower() == "transfer-encoding" for key, _ in headers)
                or any(key.lower() == "content-encoding" and value.lower() != "identity"
                       for key, value in headers)):
            raise TransferError("hf_range_response_invalid")
        result = bytearray()
        while len(result) < length:
            _check_deadline(expiry, deadline)
            # read1 returns after one underlying read, preventing slow trickles
            # from resetting a whole-range deadline indefinitely.
            if connection.sock is not None:
                connection.sock.settimeout(max(0.001, min(REQUEST_TIMEOUT,
                    deadline - time.monotonic(), expiry - time.time())))
            data = response.read1(min(1024 * 1024, length - len(result)))
            if not data:
                raise TransferError("hf_range_length_mismatch")
            result.extend(data)
        _check_deadline(expiry, deadline)
        return result
    except TransferError:
        _check_deadline(expiry, deadline)
        raise
    except Exception:
        _check_deadline(expiry, deadline)
        if expired_deadline.is_set():
            raise TransferError("hf_transfer_timeout") from None
        raise TransferError("hf_transport_failed") from None
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()
        for handle in (response, connection):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    # Cleanup must not replace a safe outcome with URL-bearing
                    # transport exception prose. No publication happens here.
                    pass


def download(fd, item, source):
    """Resume one exclusively locked incoming FD; hash all bytes, never publish."""
    url, expiry = validate_source(source)
    size = item["size_bytes"]
    if size > MAX_ARTIFACT_BYTES:
        raise TransferError("hf_artifact_too_large")
    started = time.monotonic()
    deadline = started + TRANSFER_TIMEOUT
    done = os.fstat(fd).st_size
    if done > size:
        raise TransferError("hf_partial_size_mismatch")
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    remaining = done
    while remaining:
        _check_deadline(expiry, deadline)
        data = os.read(fd, min(1024 * 1024, remaining))
        if not data:
            raise TransferError("hf_partial_size_mismatch")
        digest.update(data)
        remaining -= len(data)
    received = 0
    # A rolling window bounds queued and retained data to 8 x 8MiB. Refill
    # after each contiguous write, not after the slowest range in a batch.
    # Keep results in order so failed later ranges never create sparse holes.
    with ThreadPoolExecutor(max_workers=PARALLEL_RANGES) as executor:
        futures = deque()
        next_start = done
        try:
            while done < size:
                _check_deadline(expiry, deadline)
                while next_start < size and len(futures) < PARALLEL_RANGES:
                    _check_deadline(expiry, deadline)
                    end = min(size, next_start + RANGE_BYTES) - 1
                    futures.append(executor.submit(
                        _fetch_range, url, expiry, next_start, end, size, deadline))
                    next_start = end + 1
                data = futures[0].result(timeout=max(0.001, deadline - time.monotonic()))
                _check_deadline(expiry, deadline)
                view = memoryview(data)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise TransferError("hf_write_failed")
                    view = view[count:]
                digest.update(data)
                done += len(data)
                received += len(data)
                # Release the consumed result before admitting its replacement.
                futures.popleft()
                del data, view
        finally:
            for future in futures:
                future.cancel()
    if done != size or digest.hexdigest() != item["sha256"]:
        # Corrupt prefixes cannot poison every fresh-link retry indefinitely.
        os.ftruncate(fd, 0)
        os.fsync(fd)
        raise TransferError("hf_identity_mismatch")
    os.fsync(fd)
    elapsed = max(time.monotonic() - started, 0.000001)
    return {**item, "state": "downloaded", "received_bytes": received,
            "transfer_seconds": elapsed, "transfer_Mbps": received * 8 / elapsed / 1_000_000}
