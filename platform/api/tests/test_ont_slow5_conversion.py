from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from database import OntRawSignalDerivationJob, OntRawSignalRepresentation
from services import ont_raw_signal


def _slow5_source(path: Path) -> OntRawSignalRepresentation:
    artifact = ont_raw_signal._file_artifact(path, "upload", kind="slow5")
    return OntRawSignalRepresentation(
        id="slow5-source", run_id="ont-external-run-x", observed_generation=1,
        role="source", source_kind="external_native", format="slow5",
        source_fidelity="unknown", state="unavailable", reason_code="source_validation_required",
        artifact_manifest={"artifacts": [artifact]}, manifest_sha256="a" * 64,
        parent_representation_ids=[], parent_manifest_sha256s=[], compression={},
        runtime_identity={}, validation_receipts={},
    )


def _job(staging: Path) -> tuple[OntRawSignalDerivationJob, dict]:
    snapshot = {
        "container_runtime": "docker", "container_image": "biomodstack/ont-raw-signal:test",
        "container_digest": "b" * 64, "worker_uid": os.getuid(), "worker_gid": os.getgid(),
        "staging_root": str(staging),
    }
    job = OntRawSignalDerivationJob(
        id="ont-raw-job-1", run_id="ont-external-run-x", observed_generation=1,
        source_representation_id="slow5-source", requested_preference="blow5", consumer_id="test",
        profile_id=ont_raw_signal.SLOW5_TO_BLOW5_PROFILE_ID, state="admitted", reason_code="t",
        resource_snapshot=snapshot, attempt=1, claim_token="c",
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5), stage_receipts={},
    )
    return job, snapshot


def test_slow5_commands_convert_losslessly_with_pinned_slow5tools(tmp_path: Path) -> None:
    source = tmp_path / "reads.slow5"
    source.write_bytes(b"#slow5_version\t0.2.0\n")
    job, snapshot = _job(tmp_path / "staging")
    commands = ont_raw_signal._slow5_conversion_commands(job, _slow5_source(source), snapshot)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert commands["fingerprint"] == digest
    assert commands["stage"] == str(tmp_path / "staging" / "ont-raw-job-1" / "attempt-1")
    convert = commands["convert"]
    assert convert[convert.index("slow5tools"):] == [
        "slow5tools", "view", "--to", "blow5", "-c", "zstd", "-s", "svb-zd", "-t", "4",
        "/stage/source/source.slow5", "-o", f"/stage/outputs/{digest}.blow5",
    ]
    for name in ("convert", "quickcheck", "index_create", "canonical_source", "canonical_output"):
        assert "--network=none" in commands[name] and "--read-only" in commands[name]


def test_staged_slow5_must_match_its_registered_digest(tmp_path: Path) -> None:
    source = tmp_path / "reads.slow5"
    source.write_bytes(b"registered bytes")
    representation = _slow5_source(source)
    job, snapshot = _job(tmp_path / "staging")
    commands = ont_raw_signal._slow5_conversion_commands(job, representation, snapshot)
    (Path(commands["stage"]) / "source").mkdir(parents=True)
    assert ont_raw_signal.stage_slow5_source(commands)["bytes"] == len(b"registered bytes")
    (Path(commands["stage"]) / "source" / "source.slow5").unlink()
    commands["source_artifact"]["sha256"] = "c" * 64
    with pytest.raises(ValueError, match="diverged"):
        ont_raw_signal.stage_slow5_source(commands)


def test_roundtrip_mismatch_is_reported(tmp_path: Path) -> None:
    check = tmp_path / "check"
    check.mkdir()
    (check / "source.slow5").write_text("a")
    (check / "output.slow5").write_text("b")
    with pytest.raises(ValueError, match="round-trip"):
        ont_raw_signal.slow5_canonical_roundtrip({"stage": str(tmp_path)})


def test_finalize_writes_publication_receipts_for_one_partition(tmp_path: Path) -> None:
    fingerprint = "d" * 64
    (tmp_path / "source").mkdir()
    (tmp_path / "check").mkdir()
    identity = {"sha256": "e" * 64, "bytes": 1, "device": 1, "inode": 2, "mtime_ns": 3, "ctime_ns": 4}
    (tmp_path / "validation-receipt.json").write_text(json.dumps({
        "status": "passed", "read_count": 2, "duplicate_read_ids": 0, "indexed_lookup_count": 2,
        "blow5_identity": identity, "blow5_index_identity": identity, "read_metrics": {"row_count": 2},
    }))
    (tmp_path / "read-metrics.jsonl").write_text(
        json.dumps({"read_id": "r1", "sample_count": 5}) + "\n" + json.dumps({"read_id": "r2", "sample_count": 7}) + "\n"
    )
    semantic = ont_raw_signal.finalize_slow5_conversion(
        {"stage": str(tmp_path), "fingerprint": fingerprint}, {"source_canonical_sha256": "x"},
    )
    routing_bytes = (tmp_path / "routing.json").read_bytes()
    assert semantic["partition_counts"] == {fingerprint: 2}
    assert semantic["total_signal_samples_compared"] == 12
    assert semantic["routing_sha256"] == hashlib.sha256(routing_bytes).hexdigest()
    assert json.loads(routing_bytes)["read_to_group"] == {"r1": fingerprint, "r2": fingerprint}
    assert not (tmp_path / "source").exists() and not (tmp_path / "check").exists()
