"""One exact-read overlay, independent of optional preview publication.

Storage/range verification is owned by ngs_alignment_sessions. This module
never changes source bytes or durable scientific/catalog state. Global admission
and retained storage use the shared global resource owner.
"""
from collections import Counter
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import signal
import subprocess
import sys
import time
import uuid

import pysam

from services import ngs_alignment_catalog_reader as reader
from services import ngs_alignment_product_builder as builder
from services import ngs_alignment_sessions as storage
from services import ngs_read_overlay_policy as bounds
from services import global_resource_admission as resources
from services.ngs_alignment_derived_products import canonical_bytes, identity_sha256, resolve_product_source

FILES = {"bam": "selected-read.bam", "bai": "selected-read.bam.bai"}


class _ColdOverlay(Exception):
    """A bounded child must perform first semantic adoption."""


class OverlayError(reader.CatalogReadError):
    def __init__(self, code, message, status=409, reason=None):
        super().__init__(code, message, status)
        self.reason = reason


def checkpoint(deadline):
    if time.monotonic() >= deadline:
        raise OverlayError("NGS_READ_OVERLAY_TIMEOUT", "Selected-read preparation timed out. Retry explicitly.", 503)


@contextmanager
def lease(root, key, *, exclusive, deadline):
    if re.fullmatch(r"[0-9a-f]{64}", key) is None:
        raise ValueError("invalid overlay key")
    fd = os.open(root / ("." + key + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("invalid overlay lock")
        while True:
            checkpoint(deadline)
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        yield fd
    finally:
        os.close(fd)


def cleanup_restarted_overlays(root, *, attempt_id=None):
    """Remove only dead-attempt scratch; publications and locks are retained.

    Called at worker startup and after a bounded child exits. Nonblocking flock
    proves writer/indexer quiescence, not a PID, timestamp or expired DB lease.
    """
    try:
        with storage.open_presentation_authority_root(root / "ngs_alignment_read_overlays", create=False) as cache:
            for name in os.listdir(cache):
                if re.fullmatch(r"[0-9a-f]{64}", name):
                    lock_fd = os.open(cache / ("." + name + ".lock"), os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
                    try:
                        try:
                            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            continue
                        with storage.open_presentation_authority_root(cache / name, create=False) as envelope:
                            for child in os.listdir(envelope):
                                match_attempt = re.fullmatch(r"\.attempt-([0-9a-f]{32})", child)
                                if match_attempt and (attempt_id is None or match_attempt[1] == attempt_id):
                                    storage._remove_locus_transient(int(envelope.name), child)
                        resources.reconcile_quiescent_storage(cache / name)
                    finally:
                        os.close(lock_fd)
                    continue
                match = re.fullmatch(r"\.([0-9a-f]{64})-([0-9a-f]{32})", name)
                if match is None or attempt_id is not None and match[2] != attempt_id:
                    continue
                try:
                    fd = os.open("." + match[1] + ".lock", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=int(cache.name))
                except FileNotFoundError:
                    continue  # No lock authority: do not infer ownership.
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ValueError("invalid overlay lock")
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    storage._remove_locus_transient(int(cache.name), name)
                finally:
                    os.close(fd)
    except FileNotFoundError:
        return


def _selection(job, catalog, root, read_id, population_id):
    reader.records._validate_read_id(read_id)
    resolve_product_source(job, catalog)
    reader.assert_population(population_id, reader.population(catalog))
    with reader.snapshot(catalog, root) as (db, manifest):
        rows = reader._rows(db, "SELECT * FROM catalog WHERE read_id=?", [read_id])
        if len(rows) != 1:
            raise OverlayError("NGS_RESOURCE_NOT_FOUND", "The exact read was not found.", 404)
        row = rows[0]
        reason = bounds.eligibility(row)
        if reason is not None:
            raise OverlayError("NGS_READ_NOT_OVERLAY_ELIGIBLE", "This read is available in Detail and the original BAM.", reason=reason)
        locators = reader._rows(db, "SELECT * FROM locators WHERE read_id=? AND "
            "(record_class='primary' OR (record_class='supplementary' AND (flags & 4)=0)) "
            "ORDER BY source_record_ordinal LIMIT ?", [read_id, bounds.MAX_RECORDS + 1])
        if (len(locators) != 1 + row["mapped_supplementary_count"] or len(locators) > bounds.MAX_RECORDS
                or sum(item["record_class"] == "primary" for item in locators) != 1):
            raise storage.AlignmentSessionError("overlay locator cardinality mismatch")
        key_input = {"schema": "bms.ngs.read-overlay-identity.v2", "source": catalog.source_identity,
            "catalog_authority_sha256": catalog.authority_sha256,
            "catalog_artifacts": manifest["authority"]["artifacts"],
            "source_header": manifest["statistics"]["complete_source_header"],
            "read_id": read_id, "policy": bounds.policy()}
        return row, locators, key_input


def _verify(directory, key_input, row, locators, check, *, warm_only=False, retry_cache=False):
    with storage._open_regular_file_no_symlinks(directory / "overlay-manifest.json") as handle:
        raw = handle.read(1048577)
    manifest = json.loads(raw)
    if (len(raw) > 1048576 or canonical_bytes(manifest) != raw
            or set(manifest) != {"schema", "identity", "overlay_id", "artifacts", "primary_record_count", "mapped_supplementary_record_count", "bgzf_bound_bytes"}
            or manifest["schema"] != "bms.ngs.read-overlay-manifest.v2"
            or manifest["identity"] != key_input or manifest["overlay_id"] != identity_sha256(key_input)
            or manifest["primary_record_count"] != 1
            or manifest["mapped_supplementary_record_count"] != row["mapped_supplementary_count"]
            or manifest["bgzf_bound_bytes"] != row["overlay_bgzf_bound_bytes"]
            or set(manifest["artifacts"]) != set(FILES)):
        raise storage.AlignmentSessionError("overlay manifest integrity mismatch")
    for role, filename in FILES.items():
        check()
        meta = manifest["artifacts"][role]
        if (not isinstance(meta, dict) or set(meta) != {"filename", "sha256", "size_bytes"}
                or meta["filename"] != filename or not isinstance(meta["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", meta["sha256"]) is None
                or type(meta["size_bytes"]) is not int):
            raise storage.AlignmentSessionError("overlay artifact metadata mismatch")
        if not 0 < meta["size_bytes"] <= (min(bounds.MAX_BAM_BYTES, row["overlay_bgzf_bound_bytes"]) if role == "bam" else bounds.MAX_INDEX_BYTES):
            raise storage.AlignmentSessionError("overlay artifact exceeds bound")
    if retry_cache:
        for meta in manifest["artifacts"].values():
            storage.retry_verified_artifact_cache(directory / meta["filename"],
                expected_sha256=meta["sha256"], expected_size=meta["size_bytes"])
    expected = Counter(item["record_fingerprint_sha256"] for item in locators)
    semantic_key = hashlib.sha256(canonical_bytes({"manifest": manifest,
        "locators": locators, "record_raw_bytes": row["overlay_record_raw_bytes"]})).hexdigest()
    if warm_only:
        # Never turn a warm POST into an unbounded cold native adoption in the
        # API process. The bounded child retains cold construction/adoption.
        with storage._snapshot_cache_condition:
            for meta in manifest["artifacts"].values():
                source_key = (meta["sha256"], str((directory / meta["filename"]).resolve(strict=True)))
                if (meta["sha256"] not in storage._snapshot_receipts
                        or source_key not in storage._snapshot_sources):
                    raise _ColdOverlay()
    with storage.open_verified_artifact_snapshot(directory / FILES["bam"],
            expected_sha256=manifest["artifacts"]["bam"]["sha256"], expected_size=manifest["artifacts"]["bam"]["size_bytes"]) as bam_handle, \
         storage.open_verified_artifact_snapshot(directory / FILES["bai"],
            expected_sha256=manifest["artifacts"]["bai"]["sha256"], expected_size=manifest["artifacts"]["bai"]["size_bytes"]) as index_handle:
        if bam_handle.has_verified_semantics(semantic_key, index_handle):
            check()
            return manifest
        if warm_only:
            raise _ColdOverlay()
        with pysam.AlignmentFile(storage._descriptor_path(bam_handle.fileno()), "rb",
                index_filename=storage._descriptor_path(index_handle.fileno())) as bam:
            if bounds.header_identity(bam.header) != key_input["source_header"] or not bam.check_index():
                raise storage.AlignmentSessionError("overlay header or index mismatch")
            observed = Counter()
            previous = None
            sizes = []
            for record in bam:
                check()
                coordinate = (record.reference_id, record.reference_start)
                if (record.query_name != key_input["read_id"] or record.is_unmapped
                        or reader.records._record_class(record.flag) not in {"primary", "supplementary"}
                        or previous is not None and coordinate < previous
                        or sum(observed.values()) >= bounds.MAX_RECORDS):
                    raise storage.AlignmentSessionError("overlay record set mismatch")
                previous = coordinate
                observed[reader.records.alignment_record_fingerprint(record)] += 1
                sizes.append(bounds.record_raw_bytes(record))
            if (observed != expected or sum(sizes) != row["overlay_record_raw_bytes"]
                    or builder.bgzf_bound(key_input["source_header"]["raw_bytes"], sizes) != row["overlay_bgzf_bound_bytes"]):
                raise storage.AlignmentSessionError("overlay exact record fingerprint mismatch")
            # Exercise index lookup independently of sequential BAM decoding.
            indexed = Counter(reader.records.alignment_record_fingerprint(record)
                for name in bam.references for record in bam.fetch(name))
            if indexed != expected:
                raise storage.AlignmentSessionError("overlay index record set mismatch")
        check()
        bam_handle.remember_verified_semantics(semantic_key, index_handle)
    check()
    return manifest


def _response(manifest):
    identity = manifest["identity"]
    source = identity["source"]
    base = "/api/jobs/{}/alignment-sessions/{}/read-overlays/{}".format(source["job_id"], source["session_id"], manifest["overlay_id"])
    artifacts = dict(manifest["artifacts"])
    raw = canonical_bytes(manifest)
    artifacts["manifest"] = {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
    result = {"schema": "bms.ngs.read-overlay.v2", "job_id": source["job_id"], "session_id": source["session_id"],
        "overlay_id": manifest["overlay_id"], "state": "ready", "read_id": identity["read_id"],
        "catalog_authority_sha256": identity["catalog_authority_sha256"], "identity": identity,
        "primary_record_count": 1, "mapped_supplementary_record_count": manifest["mapped_supplementary_record_count"]}
    for role, item in artifacts.items():
        result["index" if role == "bai" else role] = {"url": base + "/" + item["sha256"] + "/" + role,
            "sha256": item["sha256"], "size_bytes": item["size_bytes"],
            "mime_type": "application/octet-stream", "range_capable": True}
    return result


@contextmanager
def overlay_allocation(cache, key, row, *, header_bytes=0, construct=True):
    owned = (cache / key).resolve(strict=False)
    existing = resources.owned_storage_bytes(owned)
    disk = existing + (row["overlay_bgzf_bound_bytes"] + bounds.MAX_INDEX_BYTES + 1048576 if construct else 0)
    # Serialized selected records/header plus decoded objects and I/O buffers.
    # This is cooperative accounting, not a process-wide RSS enforcement claim.
    ram = 4 * (row["overlay_record_raw_bytes"] + header_bytes + storage.SNAPSHOT_CHUNK_BYTES) + 1048576
    allocation = None
    try:
        allocation = resources.reserve(owner="ngs-read-overlay:" + key, storage_root=cache,
            owned_path=owned, cpu_threads=1, dram_bytes=ram, disk_bytes=disk, adopt_quiescent=True)
        yield allocation
    except resources.ResourceCapacityUnavailable as exc:
        raise OverlayError("NGS_READ_CAPACITY_UNAVAILABLE", "Selected-read allocation capacity is unavailable.", 503) from exc
    finally:
        if allocation is not None:
            try:
                allocation.retain(disk_bytes=resources.owned_storage_bytes(owned))
            finally:
                allocation.release(storage_removed=not owned.exists())


def create(job, catalog, preview, root, *, read_id, population_id, deadline, attempt_id):
    check = lambda: checkpoint(deadline)
    check()
    reader.assert_population(population_id, reader.population(catalog))
    resolve_product_source(job, catalog)
    # Membership precedence is authoritative only for a ready matching preview.
    decoration = reader.decorate_preview(catalog, preview, root, {"read": {"read_id": read_id}})
    if decoration["read"].get("in_preview") is True:
        raise OverlayError("NGS_READ_ALREADY_IN_PREVIEW", "The read is already in the matching preview.", reason="already_in_preview")
    row, locators, identity = _selection(job, catalog, root, read_id, population_id)
    key = identity_sha256(identity)
    with storage.open_presentation_authority_root(root / "ngs_alignment_read_overlays", create=True) as cache:
        with lease(cache, key, exclusive=True, deadline=deadline) as ownership_fd:
            # A stable per-key envelope owns both imports and sealed output.
            # Renames stay inside this namespace, so disk accounting never loses
            # a publication between its transient and durable path.
            with overlay_allocation(cache, key, row, header_bytes=identity["source_header"]["raw_bytes"]) as allocation, \
                    storage.open_presentation_authority_root(cache / key, create=True) as envelope:
                return _create_owned(job, catalog, cache, envelope, key, row, locators,
                    identity, check, deadline, attempt_id, ownership_fd, allocation)


def _create_owned(job, catalog, cache, envelope, key, row, locators, identity,
                  check, deadline, attempt_id, ownership_fd, allocation):
    if (envelope / "sealed").exists() or (envelope / "sealed").is_symlink():
        try:
            with storage.open_presentation_authority_root(envelope / "sealed", create=False) as directory:
                return _response(_verify(directory, identity, row, locators, check))
        except (storage.AlignmentSessionError, ValueError, KeyError, TypeError) as exc:
            # Preserve rejected bytes and return a terminal optional
            # failure. A subsequent explicit POST may build anew.
            os.rename("sealed", ".quarantine-" + uuid.uuid4().hex,
                      src_dir_fd=int(envelope.name), dst_dir_fd=int(envelope.name))
            os.fsync(int(envelope.name))
            raise OverlayError("NGS_ARTIFACT_INTEGRITY_CONFLICT",
                "Selected-read cached authority is invalid. Retry explicitly.") from exc
    check()
    temporary = ".attempt-" + attempt_id
    try:
        with storage.open_presentation_authority_root(envelope / temporary, create=True) as directory:
            with reader._source(job, catalog, allocation=allocation) as source:
                check()
                if bounds.header_identity(source.header) != identity["source_header"]:
                    raise storage.AlignmentSessionError("overlay source header mismatch")
                selected = []
                for locator in locators:
                    check()
                    record = reader.verified_record(source, locator)
                    selected.append((record, locator["source_record_ordinal"]))
                selected.sort(key=lambda pair: (pair[0].reference_id, pair[0].reference_start, pair[1]))
                lengths = [bounds.record_raw_bytes(record) for record, _ in selected]
                if (sum(lengths) != row["overlay_record_raw_bytes"]
                        or builder.bgzf_bound(identity["source_header"]["raw_bytes"], lengths) != row["overlay_bgzf_bound_bytes"]):
                    raise storage.AlignmentSessionError("overlay source size mismatch")
                with pysam.AlignmentFile(directory / FILES["bam"], "wb6", header=source.header, threads=1) as output:
                    for record, _ in selected:
                        check()
                        output.write(record)
            check()
            if (directory / FILES["bam"]).stat().st_size > min(bounds.MAX_BAM_BYTES, row["overlay_bgzf_bound_bytes"]):
                raise storage.AlignmentSessionError("overlay writer integrity bound exceeded")
            storage._index_bam_with_deadline(directory / FILES["bam"], deadline=deadline,
                label="selected read", byte_limit=bounds.MAX_INDEX_BYTES,
                ownership_fds=(ownership_fd, allocation._descriptor))
            manifest = {"schema": "bms.ngs.read-overlay-manifest.v2", "identity": identity,
                "overlay_id": key, "primary_record_count": 1,
                "mapped_supplementary_record_count": row["mapped_supplementary_count"],
                "bgzf_bound_bytes": row["overlay_bgzf_bound_bytes"],
                "artifacts": {role: builder._artifact(directory / name, check) for role, name in FILES.items()}}
            (directory / "overlay-manifest.json").write_bytes(canonical_bytes(manifest))
            for filename in (*FILES.values(), "overlay-manifest.json"):
                with storage._open_regular_file_no_symlinks(directory / filename) as handle:
                    os.fsync(handle.fileno())
            _verify(directory, identity, row, locators, check)
            os.fsync(int(directory.name))
        check()
        os.rename(envelope / temporary, envelope / "sealed")
        os.fsync(int(envelope.name))
        with storage.open_presentation_authority_root(envelope / "sealed", create=False) as directory:
            return _response(_verify(directory, identity, row, locators, check))
    finally:
        storage._remove_locus_transient(int(envelope.name), temporary)


@contextmanager
def artifact(job, catalog, root, key, digest, kind, *, retry_cache=False):
    if kind not in {*FILES, "manifest"} or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("invalid overlay artifact")
    deadline = time.monotonic() + bounds.MAX_SECONDS
    with storage.open_presentation_authority_root(root / "ngs_alignment_read_overlays", create=False) as cache:
        with lease(cache, key, exclusive=retry_cache, deadline=deadline):
            with storage.open_presentation_authority_root(cache / key / "sealed", create=False) as directory:
                with storage._open_regular_file_no_symlinks(directory / "overlay-manifest.json") as handle:
                    raw = handle.read(1048577)
                if len(raw) > 1048576:
                    raise ValueError("oversized overlay manifest")
                candidate = json.loads(raw)
                row, locators, identity = _selection(job, catalog, root, candidate["identity"]["read_id"], None)
                if identity_sha256(identity) != key:
                    raise storage.AlignmentSessionError("overlay current authority changed")
                if retry_cache:
                    selected = _response(candidate)["index" if kind == "bai" else kind]
                    if selected["sha256"] != digest:
                        raise storage.AlignmentSessionError("overlay artifact digest mismatch")
                with overlay_allocation(cache, key, row,
                        header_bytes=identity["source_header"]["raw_bytes"], construct=False):
                    manifest = _verify(directory, identity, row, locators, lambda: checkpoint(deadline), retry_cache=retry_cache)
                metadata = _response(manifest)["index" if kind == "bai" else kind]
                if metadata["sha256"] != digest:
                    raise storage.AlignmentSessionError("overlay artifact digest mismatch")
                if retry_cache and kind == "manifest":
                    storage.retry_verified_artifact_cache(directory / "overlay-manifest.json",
                        expected_sha256=digest, expected_size=metadata["size_bytes"])
                yield directory / (FILES[kind] if kind in FILES else "overlay-manifest.json"), metadata



def retry_selected_cache(job, catalog, root, *, read_id, population_id):
    """Recover an existing exact selected-read copy; never construct an overlay."""
    deadline = time.monotonic() + bounds.MAX_SECONDS
    row, locators, identity = _selection(job, catalog, root, read_id, population_id)
    key = identity_sha256(identity)
    with storage.open_presentation_authority_root(root / "ngs_alignment_read_overlays", create=False) as cache:
        with lease(cache, key, exclusive=True, deadline=deadline):
            with storage.open_presentation_authority_root(cache / key / "sealed", create=False) as directory:
                with overlay_allocation(cache, key, row,
                        header_bytes=identity["source_header"]["raw_bytes"], construct=False):
                    manifest = _verify(directory, identity, row, locators,
                        lambda: checkpoint(deadline), retry_cache=True)
                metadata = _response(manifest)["manifest"]
                storage.retry_verified_artifact_cache(directory / "overlay-manifest.json",
                    expected_sha256=metadata["sha256"], expected_size=metadata["size_bytes"])


def create_bounded(job, catalog, preview, root, *, read_id, population_id, deadline):
    """Hard elapsed boundary around decoding/writing, including child indexing.

    This is the existing synchronous one-read operation, not a scientific queue.
    Only already authorized, detached source metadata crosses into the child.
    """
    checkpoint(deadline)
    # Reuse a ready overlay in the long-lived serving process. All selection,
    # source and preview precedence checks still run; only exact-generation
    # semantic work is reused. No GET repair or new artifact is introduced.
    row, locators, identity = _selection(job, catalog, root, read_id, population_id)
    decoration = reader.decorate_preview(catalog, preview, root, {"read": {"read_id": read_id}})
    if decoration["read"].get("in_preview") is True:
        raise OverlayError("NGS_READ_ALREADY_IN_PREVIEW", "The read is already in the matching preview.", reason="already_in_preview")
    key = identity_sha256(identity)
    try:
        with storage.open_presentation_authority_root(root / "ngs_alignment_read_overlays", create=False) as cache:
            with lease(cache, key, exclusive=False, deadline=deadline):
                with storage.open_presentation_authority_root(cache / key / "sealed", create=False) as directory:
                    return _response(_verify(directory, identity, row, locators,
                        lambda: checkpoint(deadline), warm_only=True))
    except (FileNotFoundError, _ColdOverlay):
        pass
    except storage.AlignmentSessionError:
        # The explicit POST child owns quarantine/failure handling as before;
        # this warm shortcut must not bypass its corruption retry lifecycle.
        pass
    attempt_id = uuid.uuid4().hex
    job_fields = ("id", "status", "queue_status", "awaiting_input", "params", "provenance", "child_output_dir", "output_dir")
    product_fields = ("id", "product", "state", "job_id", "session_id", "mode", "intent_sha256", "request_sha256",
        "source_identity", "source_authority_sha256", "catalog_request_id", "catalog_authority_sha256",
        "authority_sha256", "manifest_sha256", "request_contract")
    def detached(row, fields):
        return None if row is None else {key: getattr(row, key, None) for key in fields}
    payload = {"job": detached(job, job_fields), "catalog": detached(catalog, product_fields),
        "preview": detached(preview, product_fields), "root": str(root), "read_id": read_id,
        "population_id": population_id, "deadline": deadline, "attempt_id": attempt_id}
    process = None
    try:
        process = subprocess.Popen([sys.executable, "-m", "services.ngs_read_overlays"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, cwd=Path(__file__).resolve().parent.parent)
        try:
            stdout, _stderr = process.communicate(json.dumps(payload, ensure_ascii=False, allow_nan=False),
                timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise OverlayError("NGS_READ_OVERLAY_TIMEOUT", "Selected-read preparation timed out. Retry explicitly.", 503) from exc
        checkpoint(deadline)
        if process.returncode != 0 or len(stdout.encode("utf-8")) > 1048576:
            raise OverlayError("NGS_READ_CAPACITY_UNAVAILABLE", "Selected-read preparation could not complete.", 503)
        result = json.loads(stdout)
        if "error" in result:
            error = result["error"]
            raise OverlayError(error["code"], error["message"], error["status"], error.get("reason"))
        return result["ready"]
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        # After the child and indexer are quiescent, remove only this attempt's
        # temporary directory. Complete publications and other attempts survive.
        cleanup_restarted_overlays(root, attempt_id=attempt_id)



def _main():
    from contextlib import redirect_stdout
    from types import SimpleNamespace
    payload = json.load(sys.stdin)
    try:
        with redirect_stdout(sys.stderr):
            result = create(SimpleNamespace(**payload["job"]), SimpleNamespace(**payload["catalog"]),
                SimpleNamespace(**payload["preview"]) if payload["preview"] is not None else None,
                Path(payload["root"]), read_id=payload["read_id"], population_id=payload["population_id"],
                deadline=payload["deadline"], attempt_id=payload["attempt_id"])
        response = {"ready": result}
    except reader.CatalogReadError as exc:
        response = {"error": {"code": exc.code, "message": str(exc), "status": exc.status, "reason": getattr(exc, "reason", None)}}
    except storage._AlignmentDerivativeTimeout:
        response = {"error": {"code": "NGS_READ_OVERLAY_TIMEOUT", "message": "Selected-read preparation timed out. Retry explicitly.", "status": 503}}
    except Exception as exc:
        capacity = (isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}
            or any(word in str(exc).lower() for word in ("capacity", "snapshot limit")))
        response = {"error": {"code": "NGS_READ_CAPACITY_UNAVAILABLE" if capacity else "NGS_ARTIFACT_INTEGRITY_CONFLICT",
            "message": "Selected-read capacity is unavailable." if capacity else "The exact selected-read authority could not be verified.",
            "status": 503 if capacity else 409}}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    _main()
