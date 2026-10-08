"""TEST-generated receipts: measure real read boundaries, not inferred cache hits."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from types import SimpleNamespace

import pyarrow as pa
import pytest

from services.scientific_artifacts import writer, query, envelope_rows
from services.scientific_artifacts.resolve import query_json_envelope_page, resolve_json_envelope_fields
from services.sequence_qc_manifest import load_sequence_qc_manifest, SequenceQcManifestError
from services import ngs_alignment_sessions as ngs


@pytest.fixture
def io_counts(monkeypatch):
    writer._digest_memo.clear()
    counts = {"bytes": 0, "reads": 0, "connections": 0}
    pread = os.pread
    connect = query._query_connection

    def read(fd, size, offset):
        block = pread(fd, size, offset)
        counts["bytes"] += len(block)
        counts["reads"] += 1
        return block

    def connection(path):
        counts["connections"] += 1
        return connect(path)

    monkeypatch.setattr(os, "pread", read)
    monkeypatch.setattr(query, "_query_connection", connection)
    return counts


def install(root, payload):
    return writer.install_parquet_rows(
        root=root, owner_kind="test", owner_id="read-reuse", role="payload",
        schema_id="bms.json-envelope.v1", schema_version=1,
        source_sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
        rows=envelope_rows(payload),
        schema=pa.schema([("key", pa.string()), ("item_index", pa.int64()), ("payload_json", pa.string())]),
    )


def test_cm_first_projection_one_descriptor_and_connection(tmp_path, monkeypatch, io_counts):
    from routers.conformational_mapping import _bounded_cm_record_payload

    monkeypatch.setenv("BMS_SCIENTIFIC_ARTIFACT_ROOT", str(tmp_path))
    payload = {"schema_name": "TEST", "schema_version": 1, "analysis_id": "test-analysis",
               "results": [{"n": n} for n in range(251)], "support_records": [],
               "pair_ledger": [], "exclusions": [], "clash_records": []}
    artifact = install(tmp_path, payload)
    row = SimpleNamespace(record_type="analysis", payload_json=artifact.reference())
    result, pages, _ = _bounded_cm_record_payload(row)
    cold = dict(io_counts)
    assert result["results"] == payload["results"][:10]
    assert pages["results"]["total_count"] == 251
    assert pages["results"]["next_offset"] == 10
    assert pages["support_records"]["total_count"] == 0
    assert cold["bytes"] == artifact.size_bytes
    assert cold["connections"] == 1
    assert _bounded_cm_record_payload(row) == (result, pages, _)
    assert io_counts["bytes"] == cold["bytes"]
    assert io_counts["connections"] == 2
    page = query_json_envelope_page(artifact.reference(), key="results", offset=250, limit=10, root=tmp_path)
    assert page["rows"] == [{"n": 250}]
    assert page["next_offset"] is None
    print("CM_IO", json.dumps({"size": artifact.size_bytes, "cold": cold, "warm_and_page": io_counts}))


def test_summary_projection_rejects_lookahead_overflow(tmp_path):
    artifact = install(tmp_path, {"small": [1, 2, 3]})
    with pytest.raises(ValueError, match="exceeds"):
        resolve_json_envelope_fields(artifact.reference(), keys=["small"], max_items_per_key=2, root=tmp_path)


def test_descriptor_digest_concurrent_cold_single_flight(tmp_path, io_counts):
    path = tmp_path / "test.bin"
    data = b"TEST-read-generation" * 200000
    path.write_bytes(data)

    def verify(_):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.lseek(fd, 3, os.SEEK_SET)
            result = writer.descriptor_content_digest(fd, scope=str(tmp_path))
            assert os.lseek(fd, 0, os.SEEK_CUR) == 3
            return result
        finally:
            os.close(fd)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(verify, range(16)))
    assert results == [(len(data), hashlib.sha256(data).hexdigest())] * 16
    assert io_counts["bytes"] == len(data)


@pytest.mark.parametrize("change", ["write", "replace", "symlink", "parent_symlink"])
def test_warm_receipt_rejects_mutation_and_no_follow(tmp_path, change, io_counts):
    artifact = install(tmp_path, {"value": 1})
    writer.verify_artifact(artifact, root=tmp_path)
    path = artifact.storage_path
    old_stat = path.stat()
    if change == "write":
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 1
        path.write_bytes(data)
        os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    elif change == "replace":
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"x" * artifact.size_bytes)
        os.replace(replacement, path)
    elif change == "symlink":
        target = tmp_path / "retained.parquet"
        path.rename(target)
        path.symlink_to(target)
    else:
        folder = path.parent
        moved = tmp_path / "retained"
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
    with pytest.raises(writer.ScientificArtifactError):
        writer.verify_artifact(artifact, root=tmp_path)


def test_mid_hash_mutation_not_memoized(tmp_path, monkeypatch):
    path = tmp_path / "changing"
    path.write_bytes(b"a" * (2 * 1024 * 1024))
    original = os.pread
    changed = False

    def mutate(fd, size, offset):
        nonlocal changed
        block = original(fd, size, offset)
        if not changed:
            changed = True
            with path.open("r+b") as handle:
                handle.write(b"b")
        return block

    monkeypatch.setattr(os, "pread", mutate)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(writer.ScientificArtifactError, match="changed"):
            writer.descriptor_content_digest(fd, scope=str(tmp_path))
    finally:
        os.close(fd)
    assert not any(key[0] == str(tmp_path) for key in writer._digest_memo)


def test_digest_memo_is_bounded_and_scope_bound(tmp_path, monkeypatch, io_counts):
    monkeypatch.setattr(writer, "_DIGEST_MEMO_LIMIT", 3)
    path = tmp_path / "data"
    path.write_bytes(b"TEST")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        for scope in range(5):
            writer.descriptor_content_digest(fd, scope=str(scope))
        assert len(writer._digest_memo) == 3
        assert io_counts["bytes"] == 20
    finally:
        os.close(fd)


def test_ngs_manifest_and_descriptor_reads_share_unchanged_bytes(tmp_path, io_counts):
    path = tmp_path / "test.fastq"
    data = b"@TEST\nACGT\n+\nIIII\n" * 167481
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    manifest_path = tmp_path / "qc_manifest.json"
    manifest_path.write_text(json.dumps({
        "artifact_schema_version": 1, "job_id": "TEST-job", "workflow_id": "ont_fastq_qc",
        "input_mode": "fastq", "analysis_status": "completed",
        "artifacts": [{"kind": "reads", "path": path.name, "required": True,
                       "sha256": digest, "size_bytes": len(data)}],
    }))
    for _ in range(3):
        manifest = load_sequence_qc_manifest(manifest_path, expected_job_id="TEST-job")
        assert manifest["artifacts"][0]["integrity_valid"] is True
        assert ngs._sha256_file_and_size(path) == (digest, len(data))
        ngs.verify_current_artifact_bytes(path, expected_size=len(data), expected_sha256=digest)
    assert io_counts["bytes"] == len(data)
    print("NGS_IO", json.dumps({"size": len(data), "three_manifest_descriptor_verifications": io_counts}))
    metadata = path.stat()
    with path.open("r+b") as handle:
        handle.write(b"!")
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    assert load_sequence_qc_manifest(manifest_path)["artifacts"][0]["integrity_valid"] is False
    with pytest.raises(ngs.AlignmentSessionError, match="digest"):
        ngs.verify_current_artifact_bytes(path, expected_size=len(data), expected_sha256=digest)
    path.unlink()
    path.symlink_to(manifest_path)
    with pytest.raises(SequenceQcManifestError, match="symlink"):
        load_sequence_qc_manifest(manifest_path)


def test_descriptor_only_import_does_not_require_parquet(tmp_path):
    import subprocess
    import sys

    code = """
import importlib.abc
import sys
class DenyParquet(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pyarrow', 'duckdb', 'sqlalchemy'}:
            raise ImportError('descriptor-only import must not require ' + fullname)
sys.meta_path.insert(0, DenyParquet())
from services.scientific_artifacts.writer import descriptor_content_digest
assert callable(descriptor_content_digest)
"""
    subprocess.run([sys.executable, '-c', code], check=True)


def test_query_context_reuses_key_table_without_stale_values(tmp_path):
    artifact = install(tmp_path, {"first": 1, "second": 2})
    with query.artifact_query(artifact.reference(), root=tmp_path) as reader:
        first = reader.query_rows_by_values(key_column="key", values=["first"], columns=["key"])
        second = reader.query_rows_by_values(key_column="key", values=["second"], columns=["key"])
    assert first == [{"key": "first"}]
    assert second == [{"key": "second"}]
