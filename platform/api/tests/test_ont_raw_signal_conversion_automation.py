from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pod5
import pytest

pytestmark = [
    pytest.mark.filterwarnings("ignore:Call to deprecated function.*:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow.*:DeprecationWarning"),
]

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "platform" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "platform" / "api"))

from database import OntRawSignalDerivationJob, OntRawSignalRepresentation
from services import ont_raw_signal

QUALIFICATION_ROOT = Path("/mnt/BioModStack/ont-raw-signal-qualification/BFX6NB_1_JAN26-EL-Q2-01/subset-pod5")
QUALIFICATION_PARTITIONS = (
    QUALIFICATION_ROOT / "14e84168825dc5524fd61c441b7a5ad31b71ea16796502fccba679b6448bda45.pod5",
    QUALIFICATION_ROOT / "fdb1de8a28aad4ed732e9bf10f35faf75b4d1d8518a9385a5c4cceb1c2dc6cb5.pod5",
)


def _load_validator():
    path = ROOT / "scripts" / "ont_raw_signal_validate.py"
    spec = importlib.util.spec_from_file_location("ont_raw_signal_validate_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_mixed_pod5(path: Path) -> tuple[str, list[Any]]:
    reads = []
    for source in QUALIFICATION_PARTITIONS:
        assert source.is_file(), source
        with pod5.Reader(source) as reader:
            reads.append(next(reader.reads()).to_read())
    assert len({repr(read.run_info) for read in reads}) == 2
    with pod5.Writer(path, software_name="BioModStack raw-signal conversion test") as writer:
        for read in reads:
            writer.add_read(read)
    return str(reads[0].run_info.acquisition_id), reads


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source(path: Path, acquisition_id: str) -> OntRawSignalRepresentation:
    return OntRawSignalRepresentation(
        id="source-1",
        run_id="run-1",
        observed_generation=1,
        role="source",
        source_kind="minknow_native",
        format="pod5",
        source_fidelity="native",
        state="preparable",
        reason_code="awaiting_validation",
        artifact_manifest={
            "artifacts": [
                {
                    "kind": "pod5",
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            ]
        },
        manifest_sha256="a" * 64,
        parent_representation_ids=[],
        parent_manifest_sha256s=[],
        compression={},
        runtime_identity={},
        validation_receipts={},
        acquisition_id=acquisition_id,
        profile_id="native",
        created_at=datetime.utcnow(),
    )


def test_public_raw_signal_representation_is_path_opaque(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"source")
    representation = _source(source_path, "acquisition")
    representation.parent_representation_ids = ["parent-representation"]
    representation.parent_manifest_sha256s = ["b" * 64]
    representation.runtime_identity = {"container_digest": "c" * 64}
    representation.validation_receipts = {"adjacent_index": True, "semantic": {"status": "passed"}}

    public = ont_raw_signal._public_representation(representation)

    assert public["artifacts"][0]["sha256"] == _sha256(source_path)
    assert "path" not in public["artifacts"][0]
    assert public["parent_manifest_sha256s"] == ["b" * 64]
    assert public["validation_receipts"]["semantic"]["status"] == "passed"


def _job(stage_root: Path) -> tuple[OntRawSignalDerivationJob, dict[str, Any]]:
    job = OntRawSignalDerivationJob(
        id="job-1",
        run_id="run-1",
        observed_generation=1,
        source_representation_id="source-1",
        requested_preference="auto",
        consumer_id="test",
        profile_id=ont_raw_signal.BLOW5_PROFILE_ID,
        state="admitted",
        reason_code="test",
        resource_snapshot={},
        attempt=1,
        claim_token="claim-1",
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
        stage_receipts={},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    snapshot = {
        "container_runtime": "docker",
        "container_image": "biomodstack/ont-raw-signal:test",
        "container_digest": "b" * 64,
        "worker_uid": os.getuid(),
        "worker_gid": os.getgid(),
        "staging_root": str(stage_root),
    }
    job.resource_snapshot = snapshot
    return job, snapshot


# Contract tests: 12

def test_contract_01_partitioned_profile_is_versioned() -> None:
    assert ont_raw_signal.BLOW5_PROFILE_ID == "bms.blow5.partitioned-zstd-svb-zd.v2"


def test_contract_02_source_paths_require_immutable_authority(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"pod5")
    source = _source(source_path, "acquisition")
    source.artifact_manifest["artifacts"][0].pop("sha256")
    with pytest.raises(ValueError, match="immutable size and digest authority"):
        ont_raw_signal._source_paths(source)


def test_contract_03_commands_bind_sealed_digest_and_partition_map(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"pod5")
    source = _source(source_path, "acquisition")
    job, snapshot = _job(tmp_path / "staging")
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)
    command = commands["source_preflight"]
    assert command[command.index("--expected-sha256") + 1] == _sha256(source_path)
    assert command[command.index("--expected-size") + 1] == str(source_path.stat().st_size)
    assert commands["partition"][-8:] == [
        "--read-id-column", "read_id", "--columns", "group", "--output", "/stage/partitions", "--template", "{group}.pod5", "--threads", "4", "--missing-ok"
    ][-8:]


def test_contract_04_partition_authority_rejects_invalid_groups(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "passed", "groups": [{"fingerprint": "bad", "read_count": 1}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid run-info partition"):
        ont_raw_signal.conversion_partition_groups({"source_receipt": str(receipt)})


def test_contract_05_unit_commands_are_lossless_and_indexed(tmp_path: Path) -> None:
    fingerprint = "a" * 64
    commands = {"common": ["runtime", "image"]}
    unit = ont_raw_signal.conversion_unit_commands(commands, fingerprint)
    assert unit["convert"][-12:] == [
        "blue-crab", "p2s", "-c", "zstd", "-s", "svb-zd", "--iop", "1", "--threads", "4", "--batchsize", "1000",
        f"/stage/partitions/{fingerprint}.pod5", "-o", f"/stage/outputs/{fingerprint}.blow5",
    ][-12:]
    assert unit["index_create"][-2:] == ["slow5tools", "index"][-2:] or unit["index_create"][-3:-1] == ["slow5tools", "index"]


def test_contract_06_semantic_command_covers_every_partition() -> None:
    groups = ["a" * 64, "b" * 64]
    command = ont_raw_signal.conversion_semantic_command(
        {"common": ["runtime", "image"], "validator_input_args": ["--pod5", "/input/source.pod5"]},
        groups,
    )
    for group in groups:
        assert f"/stage/outputs/{group}.blow5" in command
        assert f"/stage/outputs/{group}.blow5.idx" in command
    assert command[-4:] == ["--routing", "/stage/routing.json", "--receipt", "/stage/semantic-receipt.json"]


def test_contract_07_runtime_packages_both_executables() -> None:
    dockerfile = (ROOT / "docker" / "ont-raw-signal.Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts/ont_raw_signal_validate.py /opt/bms/ont_raw_signal_validate.py" in dockerfile
    assert "COPY scripts/ont_raw_signal_lookup.py /opt/bms/ont_raw_signal_lookup.py" in dockerfile
    assert "ont_raw_signal_validate.py --help" in dockerfile
    assert "ont_raw_signal_lookup.py --help" in dockerfile


def test_contract_08_terminal_registration_requests_automatic_conversion() -> None:
    source = inspect.getsource(ont_raw_signal.register_native_pod5_generation)
    assert 'consumer_id="ont-terminal-reconciliation"' in source
    assert 'preference="auto"' in source
    assert "automatic=True" in source


def test_contract_09_partitioned_waveform_uses_digest_bound_route(tmp_path: Path) -> None:
    final = tmp_path / "job"
    outputs = final / "outputs"
    outputs.mkdir(parents=True)
    fingerprint = "a" * 64
    blow5 = outputs / f"{fingerprint}.blow5"
    index = outputs / f"{fingerprint}.blow5.idx"
    blow5.write_bytes(b"blow5")
    index.write_bytes(b"index")
    routing = final / "routing.json"
    routing.write_text(json.dumps({"groups": {fingerprint: {"blow5": blow5.name, "index": index.name}}, "read_to_group": {"read-1": fingerprint}}), encoding="utf-8")
    representation = OntRawSignalRepresentation(
        format="blow5", state="ready", validation_receipts={"adjacent_index": True},
        artifact_manifest={"artifacts": [
            {"kind": "blow5", "path": str(blow5)},
            {"kind": "blow5", "path": str(outputs / ("b" * 64 + ".blow5"))},
            {"kind": "blow5_index", "path": str(index)},
            {"kind": "blow5_index", "path": str(outputs / ("b" * 64 + ".blow5.idx"))},
            {"kind": "read_routing", "path": str(routing)},
        ]},
    )
    assert ont_raw_signal._validated_blow5_paths(representation, "read-1") == (blow5, index)


class _Session:
    def __init__(self, job: OntRawSignalDerivationJob):
        self.job = job
        self.added: list[Any] = []
        self.commits = 0

    async def get(self, _model, _identifier):
        return self.job

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_contract_10_cancellation_blocks_further_progress(tmp_path: Path) -> None:
    job, _snapshot = _job(tmp_path)
    job.cancel_requested_at = datetime.utcnow()
    session = _Session(job)
    with pytest.raises(ValueError, match="cancellation requested"):
        await ont_raw_signal.transition_derivation(session, job.id, str(job.claim_token), "converting", "started", {})


@pytest.mark.asyncio
async def test_contract_11_cancelled_terminal_transition_clears_lease(tmp_path: Path) -> None:
    job, _snapshot = _job(tmp_path)
    job.cancel_requested_at = datetime.utcnow()
    session = _Session(job)
    await ont_raw_signal.transition_derivation(session, job.id, str(job.claim_token), "cancelled", "cancelled_child_terminated", {})
    assert job.state == "cancelled"
    assert job.claim_token is None
    assert job.lease_expires_at is None
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_contract_12_publication_emits_one_pair_per_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staging_root = tmp_path / "staging"
    stage = staging_root / "job-1" / "attempt-1"
    outputs = stage / "outputs"
    outputs.mkdir(parents=True)
    groups = {"a" * 64: 1, "b" * 64: 2}
    routing_payload = {
        "schema": "bms.ont.raw-signal-routing.v1",
        "groups": {group: {"blow5": f"{group}.blow5", "index": f"{group}.blow5.idx", "read_count": count} for group, count in groups.items()},
        "read_to_group": {"read-a": "a" * 64, "read-b": "b" * 64},
    }
    routing = stage / "routing.json"
    routing.write_text(json.dumps(routing_payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    for group in groups:
        (outputs / f"{group}.blow5").write_bytes(group.encode())
        (outputs / f"{group}.blow5.idx").write_bytes(b"index")
    semantic = {
        "status": "passed", "duplicate_read_ids": 0, "partition_counts": groups,
        "read_count": 3, "routing_sha256": _sha256(routing),
    }
    (stage / "semantic-receipt.json").write_text(json.dumps(semantic), encoding="utf-8")
    source_file = tmp_path / "source.pod5"
    source_file.write_bytes(b"source")
    source = _source(source_file, "acquisition")
    job, snapshot = _job(staging_root)
    commands = {"stage": str(stage), "outputs": str(outputs), "routing": str(routing)}
    monkeypatch.setenv(ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(staging_root))
    session = _Session(job)
    representation = await ont_raw_signal.publish_derivation(session, job, source, commands)
    kinds = [artifact["kind"] for artifact in representation.artifact_manifest["artifacts"]]
    assert kinds.count("blow5") == 2
    assert kinds.count("blow5_index") == 2
    assert kinds.count("read_routing") == 1
    assert representation.read_count == 3
    assert not stage.exists()


# Validator fixture tests: 2

def test_validator_fixture_01_preflight_partitions_complete_run_info(tmp_path: Path) -> None:
    validator = _load_validator()
    source = tmp_path / "mixed.pod5"
    acquisition_id, _reads = _write_mixed_pod5(source)
    partition_map = tmp_path / "partition-map.csv"
    args = argparse.Namespace(
        pod5=[source], expected_sha256=[_sha256(source)], expected_size=[source.stat().st_size],
        expected_acquisition_id=acquisition_id, partition_map=partition_map,
    )
    receipt = validator.source_preflight(args)
    assert receipt["status"] == "passed"
    assert receipt["read_count"] == 2
    assert len(receipt["groups"]) == 2
    assert partition_map.read_text(encoding="utf-8").count("\n") == 3


def _slow5_record(read: Any) -> dict[str, Any]:
    digitisation = 8192.0
    return {
        "read_id": str(read.read_id), "signal": np.asarray(read.signal, dtype=np.int16),
        "len_raw_signal": len(read.signal), "digitisation": digitisation,
        "offset": float(read.calibration.offset), "range": float(read.calibration.scale) * digitisation,
        "sampling_rate": int(read.run_info.sample_rate), "start_time": int(read.start_sample),
        "read_number": int(read.read_number), "channel_number": int(read.pore.channel),
        "start_mux": int(read.pore.well), "median_before": float(read.median_before),
        "num_minknow_events": int(read.num_minknow_events),
        "tracked_scaling_shift": float(read.tracked_scaling.shift),
        "tracked_scaling_scale": float(read.tracked_scaling.scale),
        "predicted_scaling_shift": float(read.predicted_scaling.shift),
        "predicted_scaling_scale": float(read.predicted_scaling.scale),
        "num_reads_since_mux_change": int(read.num_reads_since_mux_change),
        "time_since_mux_change": float(read.time_since_mux_change),
        "open_pore_level": float(read.open_pore_level),
    }


class _FakeSlow5:
    def __init__(self, records: list[dict[str, Any]]):
        self.records = records

    def seq_reads(self, **_kwargs):
        yield from self.records

    def get_read(self, read_id: str, **_kwargs):
        return next((record for record in self.records if record["read_id"] == read_id), None)

    def close(self):
        return None


def test_validator_fixture_02_rejects_wrong_run_info_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _load_validator()
    source = tmp_path / "mixed.pod5"
    _acquisition_id, reads = _write_mixed_pod5(source)
    fingerprints = [validator._run_info_fingerprint(read.run_info) for read in reads]
    blow5 = tmp_path / f"{fingerprints[1]}.blow5"
    index = tmp_path / f"{fingerprints[1]}.blow5.idx"
    blow5.write_bytes(b"fixture")
    index.write_bytes(b"index")
    monkeypatch.setattr(validator, "_slow5_open", lambda _path: _FakeSlow5([_slow5_record(reads[0])]))
    args = argparse.Namespace(
        pod5=[source], expected_sha256=[_sha256(source)], expected_size=[source.stat().st_size],
        blow5=[blow5], index=[index], routing=tmp_path / "routing.json",
    )
    with pytest.raises(ValueError, match="wrong complete-run-info partition"):
        validator.semantic_validate(args)


# Mixed-run end-to-end conversion: 1

@pytest.mark.runtime_integration
def test_mixed_run_end_to_end_conversion(tmp_path: Path) -> None:
    source_path = tmp_path / "mixed.pod5"
    acquisition_id, _reads = _write_mixed_pod5(source_path)
    image_tag = "biomodstack/ont-raw-signal:focused-test"
    subprocess.run(
        ["docker", "build", "--network=host", "-f", str(ROOT / "docker" / "ont-raw-signal.Dockerfile"), "-t", image_tag, str(ROOT)],
        check=True, cwd=ROOT, timeout=600,
    )
    image_id = subprocess.run(
        ["docker", "image", "inspect", image_tag, "--format", "{{.Id}}"],
        check=True, text=True, capture_output=True, timeout=30,
    ).stdout.strip()
    assert image_id.startswith("sha256:")
    job, snapshot = _job(tmp_path / "staging")
    snapshot["container_image"] = image_id
    snapshot["container_digest"] = image_id.removeprefix("sha256:")
    source = _source(source_path, acquisition_id)
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)
    Path(commands["stage"]).mkdir(parents=True, mode=0o700)
    Path(commands["partitions"]).mkdir(mode=0o700)
    Path(commands["outputs"]).mkdir(mode=0o700)
    subprocess.run(commands["source_preflight"], check=True, timeout=120)
    groups = ont_raw_signal.conversion_partition_groups(commands)
    assert len(groups) == 2
    subprocess.run(commands["partition"], check=True, timeout=120)
    for group in groups:
        unit = ont_raw_signal.conversion_unit_commands(commands, group)
        subprocess.run(unit["convert"], check=True, timeout=120)
        subprocess.run(unit["quickcheck"], check=True, timeout=30)
        subprocess.run(unit["index_create"], check=True, timeout=30)
    subprocess.run(ont_raw_signal.conversion_semantic_command(commands, groups), check=True, timeout=180)
    semantic = json.loads((Path(commands["stage"]) / "semantic-receipt.json").read_text(encoding="utf-8"))
    assert semantic["status"] == "passed"
    assert semantic["read_count"] == 2
    assert semantic["partition_count"] == 2
    assert semantic["indexed_lookup_count"] == 2
    assert semantic["total_signal_samples_compared"] > 0


def test_existing_pod5_candidates_are_server_rooted_and_path_opaque(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "existing-pod5"
    source_root.mkdir()
    source = source_root / "BFX6NB_4.pod5"
    source.write_bytes(b"sealed-pod5")
    (source_root / "ignore.fastq").write_bytes(b"reads")
    monkeypatch.setenv(ont_raw_signal.EXTERNAL_POD5_ROOT_ENV, str(source_root))

    candidates = ont_raw_signal.list_external_pod5_candidates()

    source_info = source.stat()
    assert candidates == [{
        "candidate_id": ont_raw_signal._candidate_identity("BFX6NB_4.pod5", source_info),
        "display_name": "BFX6NB_4.pod5",
        "size_bytes": len(b"sealed-pod5"),
        "modified_at_ns": source_info.st_mtime_ns,
    }]
    assert str(source_root) not in json.dumps(candidates)


def test_existing_pod5_candidates_exclude_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "existing-pod5"
    source_root.mkdir()
    outside = tmp_path / "outside.pod5"
    outside.write_bytes(b"outside")
    (source_root / "escaped.pod5").symlink_to(outside)
    monkeypatch.setenv(ont_raw_signal.EXTERNAL_POD5_ROOT_ENV, str(source_root))

    assert ont_raw_signal.list_external_pod5_candidates() == []
    with pytest.raises(KeyError):
        ont_raw_signal.resolve_external_pod5_candidate(hashlib.sha256(b"escaped.pod5").hexdigest())


def test_existing_pod5_candidate_token_is_bound_to_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "existing-pod5"
    source_root.mkdir()
    source = source_root / "BFX6NB_4.pod5"
    source.write_bytes(b"first")
    monkeypatch.setenv(ont_raw_signal.EXTERNAL_POD5_ROOT_ENV, str(source_root))
    stale_token = ont_raw_signal.list_external_pod5_candidates()[0]["candidate_id"]

    source.write_bytes(b"replacement-with-different-identity")

    assert ont_raw_signal.list_external_pod5_candidates()[0]["candidate_id"] != stale_token
    with pytest.raises(KeyError):
        ont_raw_signal.resolve_external_pod5_candidate(stale_token)


def test_existing_pod5_root_rejects_symlinked_ancestors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real_root, target_is_directory=True)
    monkeypatch.setenv(ont_raw_signal.EXTERNAL_POD5_ROOT_ENV, str(alias))

    with pytest.raises(RuntimeError, match="symbolic links"):
        ont_raw_signal.list_external_pod5_candidates()
