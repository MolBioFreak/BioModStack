"""Independent catalog v2 / preview v6 builders on existing presentation storage.

Worker-only; no scientific or durable lifecycle writes. Reuses precursor BAM
semantics, fingerprints, Parquet types and descriptor-pinned storage helpers.
"""
from contextlib import contextmanager, ExitStack
import errno
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import sqlite3
import re
import stat
import uuid

import pyarrow as pa
import pyarrow.parquet as pq
import pysam
from services import verified_native_reads as native

from services import ngs_alignment_sessions as storage
from services import ngs_alignment_presentation_v5 as records
from services.ngs_alignment_derived_products import canonical_bytes, identity_sha256, CATALOG_SEMANTICS

CATALOG_SCHEMA = pa.schema([f for f in records.CATALOG_SCHEMA if f.name not in {"in_preview", "preview_rank"}] + [
    pa.field("overlay_record_raw_bytes", pa.uint64()),
    pa.field("overlay_writer_compatible", pa.bool_()),
    pa.field("overlay_bgzf_bound_bytes", pa.uint64()),
])
LOCATOR_SCHEMA = records.LOCATOR_SCHEMA
MEMBERSHIP_SCHEMA = pa.schema([("read_id", pa.string()), ("preview_rank", pa.uint32())])
Failure = storage.AlignmentPresentationFailure
FILES = {"catalog": {"catalog": "read-catalog.parquet", "locators": "read-record-locators.parquet"},
         "preview": {"bam": "preview.bam", "index": "preview.bam.bai", "membership": "preview-membership.parquet"}}


def resolved_preview_policy():
    # Product targets, not execution-target RAM/disk allocation. No wall deadline.
    return {"schema": "bms.ngs.alignment-preview-policy.v6", "target_reads": 5000,
            "max_records": 20000, "max_bytes": 67108864,
            "projection": "alignment_core_projection_v1", "header_policy": "sq_coordinate_v1",
            "bgzf_admission_version": 2,
            "writer_contract": {"pysam": pysam.__version__, "htslib": pysam.__samtools_version__,
                                "mode": "wb", "compression_level": 6, "threads": 1,
                                "record_order": "reference_start_source_ordinal",
                                "selection": "stratified_largest_remainder_sha256_v1"}}


def _rows(path, schema):
    with storage._open_regular_file_no_symlinks(path) as handle:
        parquet = pq.ParquetFile(handle)
        if parquet.schema_arrow != schema:
            raise Failure("integrity_mismatch", message="derived Parquet schema diverged")
        for batch in parquet.iter_batches(batch_size=4096):
            yield from batch.to_pylist()


def _write_rows(path, rows, schema, checkpoint):
    iterator = iter(rows)
    expected_digest = hashlib.sha256()
    expected_count = 0
    with pq.ParquetWriter(path, schema, compression="zstd", version="2.6",
                          use_dictionary=False, write_statistics=True) as writer:
        while True:
            checkpoint()
            batch = list(itertools.islice(iterator, 4096))
            if not batch:
                break
            for row in batch:
                expected_digest.update(records._canonical_json_bytes(row) + b"\n")
                expected_count += 1
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    observed_digest = hashlib.sha256()
    observed_count = 0
    for row in _rows(path, schema):
        checkpoint()
        observed_digest.update(records._canonical_json_bytes(row) + b"\n")
        observed_count += 1
    if observed_count != expected_count or observed_digest.digest() != expected_digest.digest():
        raise Failure("integrity_mismatch", message="derived Parquet semantic round-trip mismatch")


def _hash_handle(handle, checkpoint):
    digest = hashlib.sha256()
    handle.seek(0)
    before = os.fstat(handle.fileno())
    while block := handle.read(1024 * 1024):
        checkpoint()
        digest.update(block)
    after = os.fstat(handle.fileno())
    def stamp(s):
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
    if stamp(before) != stamp(after):
        raise Failure("source_invalid", message="artifact changed during verification")
    handle.seek(0)
    return digest.hexdigest(), after.st_size


def _artifact(path, checkpoint):
    with storage._open_regular_file_no_symlinks(path) as handle:
        digest, size = _hash_handle(handle, checkpoint)
    return {"filename": path.name, "sha256": digest, "size_bytes": size}


@contextmanager
def _namespace(root, request, *, create):
    if request.id != "ngs-" + request.product + "-" + request.intent_sha256:
        raise Failure("integrity_mismatch", message="derived request namespace mismatch")
    with storage.open_presentation_authority_root(root, create=create) as base:
        with storage.open_presentation_authority_root(base / request.id, create=create) as owned:
            yield owned


@contextmanager
def _sources(inputs, source, checkpoint):
    with ExitStack() as stack:
        handles = {}
        for key, sha, size in (("alignment_path", "alignment_sha256", "alignment_size_bytes"),
                               ("index_path", "alignment_index_sha256", "alignment_index_size_bytes")):
            checkpoint()
            if isinstance(inputs[key], storage._SnapshotLease):
                handle = stack.enter_context(inputs[key].fork())
                if handle._digest != source[sha] or handle._receipt.identity[2] != source[size]:
                    raise Failure("source_invalid", message="source receipt changed")
                handles[key] = handle
            else:
                handles[key] = stack.enter_context(storage.open_verified_artifact_snapshot(
                    inputs[key], expected_sha256=source[sha], expected_size=source[size]))
        yield {**inputs, **handles}
        checkpoint()
        for handle in handles.values():
            handle._check()
        for key, sha, size in (("alignment_path", "alignment_sha256", "alignment_size_bytes"),
                               ("index_path", "alignment_index_sha256", "alignment_index_size_bytes")):
            if not isinstance(inputs[key], storage._SnapshotLease):
                storage._check_snapshot_source(inputs[key], source[sha], source[size])


def _catalog_tables(directory, bam, checkpoint, allocation):
    from services import ngs_read_overlay_policy as overlay
    database = sqlite3.connect(directory / "records.sqlite3")
    try:
        database.execute("PRAGMA journal_mode=OFF")
        database.execute("PRAGMA temp_store=FILE")
        # SQLite's own page/cache limits derive from the admitted target, not
        # compressed BAM size or a workstation-specific NGS default.
        database.execute("PRAGMA max_page_count=" + str(max(1, allocation.disk_bytes // 4096)))
        database.execute("PRAGMA cache_size=-" + str(max(1, allocation.dram_bytes // (4 * 1024))))
        database.execute("CREATE TABLE records (read_id TEXT COLLATE BINARY, ordinal INTEGER, "
                         "offset INTEGER, kind TEXT, contig TEXT, start0 INTEGER, end0 INTEGER, "
                         "flags INTEGER, fingerprint TEXT, projection TEXT, reference_id INTEGER, raw_bytes INTEGER, "
                         "PRIMARY KEY(read_id, ordinal)) WITHOUT ROWID")
        with native.alignment(bam) as source:
            source_header = overlay.header_identity(source.header)
            ordinal = 0
            while True:
                checkpoint()
                offset = source.tell()
                try:
                    record = next(source)
                except StopIteration:
                    break
                name = records._validate_read_id(record.query_name)
                flags = int(record.flag)
                kind = records._record_class(flags)
                mapped = not bool(flags & 4)
                fingerprint, projection = records._source_record_projection(
                    record, ordinary_unmapped=kind == "unmapped",
                    include_catalog=kind in {"primary", "unmapped"})
                raw_bytes = None
                if mapped and kind in {"primary", "supplementary"}:
                    try:
                        raw_bytes = overlay.record_raw_bytes(record)
                    except (ValueError, UnicodeError, OverflowError):
                        pass  # Inspectable source record, unsupported exact overlay writer.
                database.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                    name, ordinal, offset, kind, record.reference_name if mapped else None,
                    record.reference_start if mapped else None, record.reference_end if mapped else None,
                    flags, fingerprint, json.dumps(projection, allow_nan=False), record.reference_id, raw_bytes))
                ordinal += 1
        database.commit()
        states = dict.fromkeys(("mapped_primary", "ambiguous_primary", "unmapped", "no_primary"), 0)
        def catalog_rows():
            for aggregate in database.execute(
                "SELECT read_id, COUNT(*), SUM(kind='primary'), SUM(kind='supplementary'), "
                "SUM(kind='supplementary' AND (flags & 4)=0), SUM(kind='secondary'), "
                "SUM(kind='unmapped') FROM records GROUP BY read_id ORDER BY read_id COLLATE BINARY"
            ):
                checkpoint()
                name, total, primary, supplementary, mapped_supplementary, secondary, unmapped = aggregate
                if total > 0xFFFFFFFF or any(n > 0xFFFF for n in aggregate[2:]):
                    raise Failure("resource_limit", message="catalog count exceeds its semantic type")
                state = ("mapped_primary" if primary == 1 else "ambiguous_primary" if primary > 1
                         else "unmapped" if unmapped else "no_primary")
                states[state] += 1
                canonical = None
                if state in {"mapped_primary", "unmapped"}:
                    canonical = database.execute(
                        "SELECT ordinal, projection FROM records WHERE read_id=? AND kind=? "
                        "ORDER BY ordinal LIMIT 1", (name, "primary" if primary == 1 else "unmapped")
                    ).fetchone()
                row = dict.fromkeys(CATALOG_SCHEMA.names)
                row.update(read_id=name, source_record_count=total, mapped_primary_count=primary,
                           supplementary_count=supplementary, mapped_supplementary_count=mapped_supplementary,
                           secondary_count=secondary, unmapped_count=unmapped, alignment_state=state,
                           unmapped=False, dorado_tag_parse_valid=False)
                if canonical is not None:
                    row.update(json.loads(canonical[1]), canonical_record_ordinal=canonical[0])
                retained = "read_id=? AND (kind='primary' OR (kind='supplementary' AND (flags & 4)=0))"
                count, valid, raw_total = database.execute(
                    "SELECT COUNT(*), COUNT(raw_bytes), SUM(raw_bytes) FROM records WHERE " + retained, (name,)
                ).fetchone()
                compatible = state == "mapped_primary" and count == valid == 1 + mapped_supplementary
                row.update(overlay_writer_compatible=compatible, overlay_record_raw_bytes=raw_total if compatible else None,
                           overlay_bgzf_bound_bytes=None)
                if compatible:
                    lengths = (item[0] for item in database.execute(
                        "SELECT raw_bytes FROM records WHERE " + retained + " ORDER BY reference_id, start0, ordinal", (name,)))
                    row["overlay_bgzf_bound_bytes"] = bgzf_bound(source_header["raw_bytes"], lengths)
                yield row
        def locator_rows():
            for row in database.execute("SELECT read_id, ordinal, offset, kind, contig, start0, end0, "
                                        "flags, fingerprint FROM records ORDER BY read_id COLLATE BINARY, ordinal"):
                checkpoint()
                yield dict(zip(LOCATOR_SCHEMA.names, row, strict=True))
        _write_rows(directory / FILES["catalog"]["catalog"], catalog_rows(), CATALOG_SCHEMA, checkpoint)
        _write_rows(directory / FILES["catalog"]["locators"], locator_rows(), LOCATOR_SCHEMA, checkpoint)
        return {"logical_read_count": sum(states.values()), "record_count": ordinal, "states": states,
                "complete_source_header": source_header}
    finally:
        database.close()
        (directory / "records.sqlite3").unlink(missing_ok=True)


def _equal_rows(left, right, schema, checkpoint):
    sentinel = object()
    for a, b in itertools.zip_longest(_rows(left, schema), _rows(right, schema), fillvalue=sentinel):
        checkpoint()
        if a != b:
            raise Failure("integrity_mismatch", message="adopted semantics differ from source")


def _manifest(directory, request, checkpoint, *, expected_manifest=None, expected_authority=None, verify_artifacts=True):
    with storage._open_regular_file_no_symlinks(directory / "manifest.json") as handle:
        raw = handle.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise Failure("integrity_mismatch", message="derived manifest is oversized")
    manifest = json.loads(raw)
    schema = "bms.ngs.read-catalog-manifest.v2" if request.product == "catalog" else "bms.ngs.alignment-preview-manifest.v6"
    allowed = {"schema", "source", "request_sha256", "authority", "authority_sha256", "statistics", "implementation"}
    if (set(manifest) not in (allowed, allowed | {"resource_allocation"})
            or manifest["schema"] != schema or manifest["source"] != request.source_identity
            or manifest["request_sha256"] != request.request_sha256
            or canonical_bytes(manifest) != raw
            or identity_sha256(manifest["authority"]) != manifest["authority_sha256"]
            or expected_manifest is not None and hashlib.sha256(raw).hexdigest() != expected_manifest
            or expected_authority is not None and manifest["authority_sha256"] != expected_authority):
        raise Failure("integrity_mismatch", message="derived manifest authority mismatch")
    if "resource_allocation" in manifest:
        from services.global_resource_admission import validate_receipt
        try:
            validate_receipt(manifest["resource_allocation"])
        except ValueError as exc:
            raise Failure("integrity_mismatch", message="invalid global allocation provenance") from exc
    implementation = manifest["implementation"]
    if (not isinstance(implementation, dict)
            or set(implementation) != {"creation_revision", "creation_source_tree", "pysam", "pyarrow"}
            or not all(isinstance(implementation[key], str) and implementation[key]
                       for key in ("creation_revision", "pysam", "pyarrow"))
            or implementation["creation_source_tree"] is not None and not isinstance(implementation["creation_source_tree"], str)):
        raise Failure("integrity_mismatch", message="derived implementation provenance is malformed")
    authority = manifest["authority"]
    keys = ({"schema", "request_sha256", "artifacts", "semantics"} if request.product == "catalog" else
            {"schema", "request_sha256", "artifacts", "catalog_authority_sha256", "policy"})
    if set(authority) != keys or authority["request_sha256"] != request.request_sha256 or set(authority["artifacts"]) != set(FILES[request.product]):
        raise Failure("integrity_mismatch", message="derived artifact closure mismatch")
    expected = ({"schema": "bms.ngs.read-catalog-authority.v2", "semantics": CATALOG_SEMANTICS}
                if request.product == "catalog" else
                {"schema": "bms.ngs.alignment-preview-authority.v6", "policy": request.request_contract["policy"],
                 "catalog_authority_sha256": request.catalog_authority_sha256})
    if any(authority.get(key) != value for key, value in expected.items()):
        raise Failure("integrity_mismatch", message="derived semantic contract mismatch")
    for role, name in FILES[request.product].items():
        metadata = authority["artifacts"][role]
        if (not isinstance(metadata, dict) or set(metadata) != {"filename", "sha256", "size_bytes"}
                or metadata["filename"] != name
                or not isinstance(metadata["sha256"], str)
                or len(metadata["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in metadata["sha256"])
                or type(metadata["size_bytes"]) is not int or metadata["size_bytes"] < 0):
            raise Failure("integrity_mismatch", message="derived artifact metadata mismatch")
        # Publication/recovered adoption retain full validation. Readers resolve
        # these exact receipt-bound objects through the verified cache instead.
        if verify_artifacts and metadata != _artifact(directory / name, checkpoint):
            raise Failure("integrity_mismatch", message="derived artifact digest mismatch")
    return manifest


def _core(record, header):
    if len(record.cigartuples or ()) > 65535:
        return None
    core = pysam.AlignedSegment(header)
    for field in ("query_name", "flag", "reference_id", "reference_start", "mapping_quality",
                  "cigartuples", "next_reference_id", "next_reference_start", "template_length",
                  "query_sequence", "query_qualities"):
        setattr(core, field, getattr(record, field))
    return core


def _record_bytes(record):
    length = len(record.query_sequence or "")
    return 4 + 32 + len(record.query_name.encode("utf-8")) + 1 + 4 * len(record.cigartuples or ()) + (length + 1) // 2 + length


def bgzf_bound(header_raw_bytes, lengths):
    blocks = (header_raw_bytes + 65279) // 65280
    pending = 0
    for length in lengths:
        if pending and pending + length > 65280:
            blocks += 1
            pending = 0
        blocks += (pending + length) // 65280
        pending = (pending + length) % 65280
    return (blocks + bool(pending)) * 65536 + 28


def _preview_plan(directory, catalog, bam, policy, source_sha, checkpoint, allocation):
    if policy != resolved_preview_policy():
        raise Failure("integrity_mismatch", message="preview writer differs from admitted policy")
    database = sqlite3.connect(directory / "preview.sqlite3")
    try:
        database.execute("PRAGMA temp_store=FILE")
        database.execute("PRAGMA max_page_count=" + str(max(1, allocation.disk_bytes // 4096)))
        database.execute("PRAGMA cache_size=-" + str(max(1, allocation.dram_bytes // (4 * 1024))))
        database.execute("CREATE TABLE candidates (read_id TEXT PRIMARY KEY COLLATE BINARY, "
                         "contig TEXT, tile INTEGER, strand TEXT, rank TEXT)")
        database.execute("CREATE TABLE sizes (ref INTEGER, start0 INTEGER, ordinal INTEGER PRIMARY KEY, "
                         "read_id TEXT, offset INTEGER, bytes INTEGER, fingerprint TEXT)")
        with native.alignment(bam) as source:
            references = list(zip(source.references, source.lengths, strict=True))
            header = pysam.AlignmentHeader.from_dict({"HD": {"VN": "1.6", "SO": "coordinate"},
                "SQ": [{"SN": name, "LN": length} for name, length in references]})
            widths = {name: max(1, (length + 63) // 64) for name, length in references}
            for row in _rows(catalog / FILES["catalog"]["catalog"], CATALOG_SCHEMA):
                checkpoint()
                if row["alignment_state"] == "mapped_primary":
                    database.execute("INSERT INTO candidates VALUES (?,?,?,?,?)", (
                        row["read_id"], row["contig"], (row["start_1based"] - 1) // widths[row["contig"]],
                        "reverse" if row["strand"] == "-" else "forward", storage._rank_read(source_sha, row["read_id"])))
            database.commit()
            strata = database.execute("SELECT contig, tile, strand, COUNT(*) FROM candidates "
                                      "GROUP BY contig, tile, strand ORDER BY contig, tile, strand").fetchall()
            eligible_read_count = sum(row[3] for row in strata)
            target = min(policy["target_reads"], eligible_read_count)
            base = int(target >= len(strata))
            quotas = {tuple(row[:3]): base for row in strata}
            capacity = sum(row[3] - base for row in strata)
            remaining = target - base * len(strata)
            remainders = []
            if capacity:
                for contig, tile, strand, count in strata:
                    key = (contig, tile, strand)
                    whole, remainder = divmod(remaining * (count - base), capacity)
                    quotas[key] += whole
                    remainders.append((-remainder, key))
                for _, key in sorted(remainders)[:target - sum(quotas.values())]:
                    quotas[key] += 1
            selected = {}
            for key, quota in quotas.items():
                for name, rank in database.execute(
                    "SELECT read_id, rank FROM candidates WHERE contig=? AND tile=? AND strand=? "
                    "ORDER BY rank, read_id COLLATE BINARY LIMIT ?", (*key, quota)):
                    selected[name] = rank
            excluded = set()
            # Size metadata spills to disk, including pathological supplementary counts.
            # Decode candidate locators once here; never retain decoded BAM records.
            for row in _rows(catalog / FILES["catalog"]["locators"], LOCATOR_SCHEMA):
                checkpoint()
                if row["read_id"] not in selected or row["record_class"] not in {"primary", "supplementary"} or row["flags"] & 4:
                    continue
                source.seek(row["bgzf_virtual_offset"])
                record = next(source)
                if record.query_name != row["read_id"] or records.alignment_record_fingerprint(record) != row["record_fingerprint_sha256"]:
                    raise Failure("source_invalid", message="preview source locator mismatch")
                core = _core(record, header)
                if core is None:
                    excluded.add(row["read_id"])
                    continue
                database.execute("INSERT INTO sizes VALUES (?,?,?,?,?,?,?)", (
                    record.reference_id, record.reference_start, row["source_record_ordinal"], row["read_id"],
                    row["bgzf_virtual_offset"], _record_bytes(core), row["record_fingerprint_sha256"]))
        database.commit()
        ranked = sorted((name for name in selected if name not in excluded),
                        key=lambda name: (selected[name], name.encode("utf-8")))
        header_bytes = 12 + len(str(header).encode("utf-8")) + sum(8 + len(name.encode("utf-8")) + 1 for name, _ in references)
        bound = bgzf_bound(header_bytes, [])
        if bound > policy["max_bytes"]:
            raise Failure("resource_limit", message="preview header exceeds the BAM ceiling")
        retained = []
        limiting_reason = None
        database.execute("CREATE TABLE admitted (read_id TEXT PRIMARY KEY)")
        for name in ranked:
            checkpoint()
            database.execute("INSERT INTO admitted VALUES (?)", (name,))
            count = database.execute("SELECT COUNT(*) FROM sizes JOIN admitted USING(read_id)").fetchone()[0]
            if count > policy["max_records"]:
                limiting_reason = "record_limit"
                database.execute("DELETE FROM admitted WHERE read_id=?", (name,))
                break
            candidate_bound = bgzf_bound(header_bytes, (row[0] for row in database.execute(
                "SELECT bytes FROM sizes JOIN admitted USING(read_id) ORDER BY ref, start0, ordinal")))
            if candidate_bound > policy["max_bytes"]:
                limiting_reason = "byte_limit"
                database.execute("DELETE FROM admitted WHERE read_id=?", (name,))
                break
            retained.append(name)
            bound = candidate_bound
        metadata = database.execute("SELECT ref, start0, ordinal, read_id, offset, bytes, fingerprint "
                                    "FROM sizes JOIN admitted USING(read_id) ORDER BY ref, start0, ordinal").fetchall()
        return header, metadata, retained, {
            "eligible_read_count": eligible_read_count,
            "target_read_count": target,
            "population_state": "empty" if not retained else "reduced" if len(retained) < target else "capped" if eligible_read_count > target else "complete",
            "population_reasons": (["no_mapped_primary_reads"] if not eligible_read_count else [])
                + (["read_limit"] if eligible_read_count > target else [])
                + (["long_cigar_exclusion"] if excluded else [])
                + ([limiting_reason] if limiting_reason else []),
            "selected_read_count": len(retained), "selected_record_count": len(metadata),
            "preview_complete_to_target": len(retained) == policy["target_reads"],
            "excluded_long_cigar_reads": len(excluded), "bgzf_bound_bytes": bound, "header_raw_bytes": header_bytes}
    finally:
        database.close()
        (directory / "preview.sqlite3").unlink(missing_ok=True)


def _preview_records(source, header, metadata, checkpoint):
    for _, _, _, name, offset, _, fingerprint in metadata:
        checkpoint()
        source.seek(offset)
        record = next(source)
        if record.query_name != name or records.alignment_record_fingerprint(record) != fingerprint:
            raise Failure("source_invalid", message="preview locator changed before output")
        yield _core(record, header)


def _check_preview(directory, bam, header, metadata, retained, statistics, policy, checkpoint):
    sentinel = object()
    # Pin both derived files while the native parser checks the pair.
    with storage._open_regular_file_no_symlinks(directory / "preview.bam") as bam_handle, storage._open_regular_file_no_symlinks(
            directory / "preview.bam.bai") as index_handle:
        with native.alignment(bam) as source, native.alignment(bam_handle, index_handle) as preview:
            if preview.header.to_dict() != header.to_dict() or not preview.check_index():
                raise Failure("integrity_mismatch", message="preview header/index mismatch")
            from collections import Counter
            sequential = {name: Counter() for name in preview.references}
            for expected, observed in itertools.zip_longest(
                    _preview_records(source, header, metadata, checkpoint), preview, fillvalue=sentinel):
                checkpoint()
                if expected is sentinel or observed is sentinel or records.alignment_record_fingerprint(expected) != records.alignment_record_fingerprint(observed):
                    raise Failure("integrity_mismatch", message="preview record projection mismatch")
                sequential[observed.reference_name][records.alignment_record_fingerprint(observed)] += 1
            for name in preview.references:
                indexed = Counter()
                for record in preview.fetch(name):
                    checkpoint()
                    indexed[records.alignment_record_fingerprint(record)] += 1
                if indexed != sequential[name]:
                    raise Failure("integrity_mismatch", message="preview BAI traversal differs from records")
            if preview.nocoordinate != 0 or any(item.unmapped != 0 or item.mapped != sum(sequential[item.contig].values())
                                               for item in preview.get_index_statistics()):
                raise Failure("integrity_mismatch", message="preview BAI count metadata differs from records")
    expected_membership = ({"read_id": name, "preview_rank": rank} for rank, name in enumerate(retained))
    for expected, observed in itertools.zip_longest(expected_membership,
            _rows(directory / "preview-membership.parquet", MEMBERSHIP_SCHEMA), fillvalue=sentinel):
        checkpoint()
        if expected != observed:
            raise Failure("integrity_mismatch", message="preview membership mismatch")
    size = (directory / "preview.bam").stat().st_size
    if size > statistics["bgzf_bound_bytes"] or size > policy["max_bytes"]:
        raise Failure("integrity_mismatch", message="standard writer exceeded admitted BGZF bound")


def _quarantine_orphan(namespace):
    fd = int(namespace.name)
    os.rename("sealed", ".quarantine-" + uuid.uuid4().hex, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


def _validate_orphan(namespace, operation, *args, **kwargs):
    """Quarantine only a failure while validating this unpublished object.

    Failures in source resolution, catalog dependencies, or scratch construction
    are not evidence that a retained orphan is corrupt.
    """
    try:
        return operation(*args, **kwargs)
    except (storage.AlignmentSessionError, ValueError, KeyError, TypeError, FileNotFoundError) as exc:
        if isinstance(exc, Failure) and exc.code != "integrity_mismatch":
            raise
        _quarantine_orphan(namespace)
        raise Failure("integrity_mismatch", message="rejected orphan retained; explicit retry required") from exc


def _remove_owned_attempts(namespace):
    # Caller holds the generation lock. Never match publications, quarantine,
    # predecessor names, or another namespace's attempts.
    for name in os.listdir(namespace):
        if re.fullmatch(r"\.attempt-[0-9a-f]{32}", name):
            storage._remove_locus_transient(int(namespace.name), name)


def cleanup_restarted_attempts(job, request):
    """Startup-only removal of positively quiescent temporary products.

    Lease expiry is not proof of process death. The existing filesystem lock is
    the fence, including across API processes; a live holder is always skipped.
    Missing/unsafe roots remain untouched. No reconstruction or requeue occurs.
    """
    from services.job_result_roots import resolve_persisted_job_result_root
    from services.ngs_read_overlays import cleanup_restarted_overlays
    root = resolve_persisted_job_result_root(job) / ".alignment-products"
    try:
        with _namespace(root, request, create=False) as namespace:
            fd = os.open(".generation.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=int(namespace.name))
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError("invalid product generation lock")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return
                _remove_owned_attempts(namespace)
                from services.global_resource_admission import reconcile_quiescent_storage
                reconcile_quiescent_storage(root / request.id)
            finally:
                os.close(fd)
    except FileNotFoundError:
        pass
    if request.product == "catalog":
        cleanup_restarted_overlays(root)


def _build_product(request, inputs, checkpoint, *, catalog_request=None, allocation=None):
    """Build or semantically validate/adopt ONE product, returning CAS evidence.

    The generation lock fences old builders after DB lease expiry. Cleanup owns
    only attempt directories under that lock, after builder/parser quiescence.
    No elapsed-time deadline, automatic requeue, overflow rewrite or coverage.
    """
    if (request.state != "running" or not request.claim_token
            or request.authority_sha256 is not None or request.manifest_sha256 is not None):
        raise Failure("source_invalid", message="product build requires unpublished claimed authority")
    root = Path(inputs["result_root"]) / ".alignment-products"
    with _namespace(root, request, create=True) as namespace:
        fd = int(namespace.name)
        lock_fd = os.open(".generation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o640, dir_fd=fd)
        with os.fdopen(lock_fd, "a+b") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise Failure("integrity_mismatch", message="invalid product generation lock")
            while True:
                checkpoint()
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    import time
                    time.sleep(0.1)
            _remove_owned_attempts(namespace)
            temporary_name = ".attempt-" + uuid.uuid4().hex
            os.mkdir(temporary_name, mode=0o750, dir_fd=fd)
            temporary = namespace / temporary_name
            destination = namespace / "sealed"
            adopted = False
            try:
                with _sources(inputs, request.source_identity, checkpoint) as pinned:
                    with ExitStack() as stack:
                        adopted = destination.exists() or destination.is_symlink()
                        existing = stack.enter_context(storage.open_presentation_authority_root(destination, create=False)) if adopted else None
                        manifest = _validate_orphan(namespace, _manifest, existing, request, checkpoint) if existing is not None else None
                        if request.product == "catalog":
                            statistics = _catalog_tables(temporary, pinned["alignment_path"], checkpoint, allocation)
                            authority = {"schema": "bms.ngs.read-catalog-authority.v2",
                                         "request_sha256": request.request_sha256, "semantics": CATALOG_SEMANTICS}
                            if existing is not None:
                                for role, schema in (("catalog", CATALOG_SCHEMA), ("locators", LOCATOR_SCHEMA)):
                                    name = FILES["catalog"][role]
                                    _validate_orphan(namespace, _equal_rows, temporary / name, existing / name, schema, checkpoint)
                        else:
                            if (catalog_request is None or catalog_request.state != "ready"
                                    or catalog_request.id != request.catalog_request_id
                                    or catalog_request.authority_sha256 != request.catalog_authority_sha256):
                                raise Failure("source_invalid", message="preview exact catalog is not ready")
                            catalog_ns = stack.enter_context(_namespace(root, catalog_request, create=False))
                            catalog = stack.enter_context(storage.open_presentation_authority_root(catalog_ns / "sealed", create=False))
                            _manifest(catalog, catalog_request, checkpoint, expected_manifest=catalog_request.manifest_sha256,
                                      expected_authority=request.catalog_authority_sha256)
                            policy = request.request_contract["policy"]
                            header, metadata, retained, statistics = _preview_plan(temporary, catalog, pinned["alignment_path"],
                                policy, request.source_identity["alignment_sha256"], checkpoint, allocation)
                            authority = {"schema": "bms.ngs.alignment-preview-authority.v6",
                                         "request_sha256": request.request_sha256,
                                         "catalog_authority_sha256": request.catalog_authority_sha256, "policy": policy}
                            if existing is None:
                                with native.alignment(pinned["alignment_path"]) as source, pysam.AlignmentFile(
                                        temporary / "preview.bam", "wb", header=header, threads=1,
                                        format_options=[b"level=6"]) as output:
                                    for record in _preview_records(source, header, metadata, checkpoint):
                                        output.write(record)
                                checkpoint()
                                native.index_path(temporary / "preview.bam")
                                checkpoint()
                                _write_rows(temporary / "preview-membership.parquet",
                                    ({"read_id": name, "preview_rank": rank} for rank, name in enumerate(retained)),
                                    MEMBERSHIP_SCHEMA, checkpoint)
                            if existing is not None:
                                _validate_orphan(namespace, _check_preview, existing, pinned["alignment_path"],
                                    header, metadata, retained, statistics, policy, checkpoint)
                            else:
                                _check_preview(temporary, pinned["alignment_path"], header, metadata,
                                    retained, statistics, policy, checkpoint)
                        if manifest is not None:
                            if (manifest["statistics"] != statistics
                                    or any(manifest["authority"].get(key) != value for key, value in authority.items())):
                                _quarantine_orphan(namespace)
                                raise Failure("integrity_mismatch", message="adopted product contract mismatch; explicit retry required")
                        else:
                            authority["artifacts"] = {role: _artifact(temporary / name, checkpoint)
                                                      for role, name in FILES[request.product].items()}
                            revision, tree = storage._creation_authority()
                            manifest = {"implementation": {"creation_revision": revision, "creation_source_tree": tree,
                                                           "pysam": pysam.__version__, "pyarrow": pa.__version__},
                                        "schema": "bms.ngs.read-catalog-manifest.v2" if request.product == "catalog" else "bms.ngs.alignment-preview-manifest.v6",
                                        "source": request.source_identity, "request_sha256": request.request_sha256,
                                        "resource_allocation": allocation.receipt,
                                        "authority": authority, "authority_sha256": identity_sha256(authority), "statistics": statistics}
                            with (temporary / "manifest.json").open("xb") as handle:
                                handle.write(canonical_bytes(manifest))
                                handle.flush()
                                os.fsync(handle.fileno())
                            for path in temporary.iterdir():
                                with path.open("rb") as handle:
                                    os.fsync(handle.fileno())
                            _manifest(temporary, request, checkpoint)
                checkpoint()
                if not adopted:
                    directory_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    os.rename(temporary_name, "sealed", src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                return {"source_authority_sha256": request.source_authority_sha256,
                        "authority_sha256": manifest["authority_sha256"],
                        "manifest_sha256": hashlib.sha256(canonical_bytes(manifest)).hexdigest()}
            except OSError as exc:
                if exc.errno in {errno.ENOSPC, errno.EDQUOT, errno.ENOMEM}:
                    raise Failure("resource_limit", message="derived storage capacity exhausted") from exc
                raise
            finally:
                storage._remove_locus_transient(fd, temporary_name)


def build_product(request, inputs, checkpoint, *, catalog_request=None):
    """Fresh global allocation remains held until synchronous native I/O exits."""
    from services import global_resource_admission as resources
    root = Path(inputs["result_root"]).resolve(strict=True)
    allocation = None
    owned = root / ".alignment-products" / request.id
    with _sources(inputs, request.source_identity, checkpoint) as verified_inputs:
        leave_disk = leave_ram = 0
        if request.product == "preview":
            policy = request.request_contract["policy"]
            with native.alignment(verified_inputs["alignment_path"]) as source:
                index_bound = 32 + 32 * len(source.references) + sum(
                    8 * ((length + 16383) // 16384) for length in source.lengths) + 128 * policy["max_records"]
            leave_disk = policy["max_bytes"] + index_bound
            leave_ram = 8 * storage.SNAPSHOT_CHUNK_BYTES + 128 * (
                (policy["max_bytes"] + storage.SNAPSHOT_CHUNK_BYTES - 1) // storage.SNAPSHOT_CHUNK_BYTES
                + (index_bound + storage.SNAPSHOT_CHUNK_BYTES - 1) // storage.SNAPSHOT_CHUNK_BYTES)
        try:
            # Reduce dead-attempt disk reservations to the surviving owned files.
            # A live resource owner prevents reconciliation, regardless of DB lease.
            resources.reconcile_quiescent_storage(owned)
            allocation = resources.reserve(owner="ngs-product:" + request.id + ":" + request.claim_token,
                                           storage_root=root, owned_path=owned, cpu_threads=1,
                                           leave_disk_bytes=leave_disk, leave_dram_bytes=leave_ram)
            with resources.use_compute(allocation):
                result = _build_product(request, verified_inputs, checkpoint, catalog_request=catalog_request,
                                        allocation=allocation)
            # The published product remains charged after compute is released.
            allocation.retain(disk_bytes=resources.owned_storage_bytes(owned))
            return result
        except resources.ResourceCapacityUnavailable as exc:
            raise Failure("resource_limit", message=str(exc)) from exc
        finally:
            if allocation is not None:
                # On error, retain the disk charge until explicit quiescent cleanup
                # proves which attempt bytes survived. Never free unknown live data.
                allocation.release()
                resources.reconcile_quiescent_storage(owned)
