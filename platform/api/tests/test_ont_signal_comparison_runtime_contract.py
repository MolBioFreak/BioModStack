from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from datetime import UTC, datetime

import pytest

from database import (
    InputFile,
    OntMoveTableSource,
    OntRawSignalRepresentation,
    OntSignalComparisonJob,
    OntSignalMappingArtifact,
    OntSignalMappingJob,
    OntSignalMappingProfile,
)
from molbio_ngs_models import MolBioNGSReferenceArtifact, MolBioNGSReferenceRevision
from services import ont_signal_workbench as service
from services import ont_signal_worker as worker_module
from services.ont_signal_worker import OntSignalWorker


REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_ROOT = REPO_ROOT / "platform/api/config/ont_signal_workbench"
SQUIGULATOR_COMMIT = "c5f0c619a28b9532388877096acb7568c34b9c4b"
SQUIGULATOR_RELEASE_SHA256 = (
    "f8b428655d586427c6e0c939d4a0383fa8569523234e3c21951edcd23372a66a"
)
SQUIGUALISER_COMMIT = "5a2404f1f43bc3227a85475c59b2b77970078b2e"


def _load_runtime(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer_runtime = _load_runtime("comparison_producer_runtime", "scripts/ont_squigulator_runtime.py")
renderer_runtime = _load_runtime("comparison_renderer_runtime", "scripts/ont_signal_comparison_runtime.py")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError("deadline"), "container_timeout"),
        (worker_module.ContainerLogLimitExceeded("log"), "log_limit"),
        (worker_module.OutputLimitExceeded("output"), "output_limit"),
        (worker_module.ComparisonRuntimeFailure("malformed_signal", "signal"), "malformed_signal"),
        (worker_module.ComparisonRuntimeFailure("malformed_sam", "sam"), "malformed_sam"),
        (worker_module.ParentAuthorityDrift("parent"), "parent_drift"),
        (worker_module.TerminalFenceLost("lease"), "lease_loss"),
        (worker_module.ContainerCleanupError("cleanup"), "cleanup_failure"),
    ],
)
def test_comparison_failure_reason_vocabulary_is_closed_and_distinct(
    exc: Exception,
    expected: str,
) -> None:
    assert worker_module.COMPARISON_FAILURE_REASON_CODES == frozenset({
        "container_timeout",
        "log_limit",
        "output_limit",
        "malformed_signal",
        "malformed_sam",
        "parent_drift",
        "lease_loss",
        "cleanup_failure",
    })
    assert worker_module._comparison_failure_reason(exc) == expected


@pytest.mark.parametrize(
    ("kind", "stderr_tail", "expected"),
    [
        (
            "squigulator_producer",
            "Traceback (most recent call last):\nValueError: Squigulator SAM header identity is invalid\n",
            "malformed_sam",
        ),
        (
            "squigulator_producer",
            "Traceback (most recent call last):\nValueError: simulated BLOW5 signal is empty or length-divergent\n",
            "malformed_signal",
        ),
        (
            "squigualiser_comparison_renderer",
            "Traceback (most recent call last):\nValueError: comparison BLOW5 requires its exact adjacent index\n",
            "malformed_signal",
        ),
    ],
)
def test_container_failure_messages_cross_boundary_as_typed_reasons(
    kind: str,
    stderr_tail: str,
    expected: str,
) -> None:
    exc = worker_module._comparison_container_failure(kind, stderr_tail)
    assert isinstance(exc, worker_module.ComparisonRuntimeFailure)
    assert exc.reason_code == expected


def test_unknown_container_failure_remains_generic() -> None:
    exc = worker_module._comparison_container_failure(
        "squigulator_producer",
        "Traceback (most recent call last):\nRuntimeError: unexpected upstream failure\n",
    )
    assert worker_module._comparison_failure_reason(exc) == "runtime_validation_failed"


def test_producer_child_logs_fail_at_combined_four_mib_ceiling() -> None:
    command = [
        sys.executable,
        "-c",
        "import os; os.write(1, b'x' * (4 * 1024 * 1024)); os.write(2, b'y')",
    ]

    with pytest.raises(RuntimeError, match="Squigulator combined log limit exceeded"):
        producer_runtime.run_bounded_command(
            command,
            timeout=30,
            log_limit_bytes=4 * 1024 * 1024,
        )

    exc = worker_module._comparison_container_failure(
        "squigulator_producer",
        "RuntimeError: Squigulator combined log limit exceeded\n",
    )
    assert worker_module._comparison_failure_reason(exc) == "log_limit"


@pytest.mark.asyncio
async def test_cleanup_failure_is_published_before_worker_shutdown(monkeypatch) -> None:
    worker = OntSignalWorker(lambda: None, lambda: None)
    published: list[tuple[object, str, str, str, Exception]] = []

    async def claim(table, _field):
        if table is OntSignalComparisonJob:
            return "comparison-1", "claim-1"
        return None

    async def cleanup_failure(_item_id: str, _token: str) -> None:
        raise worker_module.ContainerCleanupError("container removal failed")

    async def publish(table, field, item_id, token, exc) -> None:
        published.append((table, field, item_id, token, exc))

    monkeypatch.setattr(worker, "_claim", claim)
    monkeypatch.setattr(worker, "_process_comparison", cleanup_failure)
    monkeypatch.setattr(worker, "_fail", publish)

    await worker._run()

    assert worker._stop.is_set()
    assert len(published) == 1
    table, field, item_id, token, exc = published[0]
    assert table is OntSignalComparisonJob
    assert (field, item_id, token) == ("state", "comparison-1", "claim-1")
    assert worker_module._comparison_failure_reason(exc) == "cleanup_failure"


@pytest.mark.asyncio
async def test_running_comparison_cancellation_publishes_equal_terminal_event() -> None:
    added: list[object] = []
    row = SimpleNamespace(claim_token="claim-1", cancel_requested_at=datetime.now(UTC))

    class Result:
        rowcount = 1

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def get(self, _table, _item_id): return row
        async def execute(self, _statement): return Result()
        async def rollback(self): return None
        async def commit(self): return None
        def add(self, value): added.append(value)

    worker = OntSignalWorker(lambda: Session(), lambda: None)
    await worker._cancel_claim(
        OntSignalComparisonJob, "state", "comparison-1", "claim-1"
    )

    assert len(added) == 1
    event = added[0]
    assert isinstance(event, worker_module.OntSignalComparisonEvent)
    assert (event.state, event.reason_code) == ("cancelled", "cancelled")
    assert event.comparison_job_id == "comparison-1"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _canonical_sha256(value: dict[str, object], field: str = "content_sha256") -> str:
    preimage = dict(value)
    preimage.pop(field, None)
    return hashlib.sha256(
        json.dumps(preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def test_parameter_contract_classifies_every_pinned_upstream_option() -> None:
    contract = _load(CONFIG_ROOT / "squigulator_ideal_comparison_schema_v1.json")

    assert contract["schema"] == "bms.ont-squigulator-ideal-comparison.v1"
    assert contract["upstream"] == {
        "name": "Squigulator",
        "version": "0.5.0",
        "commit": SQUIGULATOR_COMMIT,
        "release_source_asset": "squigulator-v0.5.0-release.tar.gz",
        "release_source_asset_sha256": SQUIGULATOR_RELEASE_SHA256,
    }
    options = contract["upstream_options"]
    assert isinstance(options, list)
    assert {item["option"] for item in options} == {
        "--verbose", "--help", "--version", "--output", "--ideal",
        "--full-contigs", "--nreads", "--fasta", "--rlen", "--seed",
        "--ideal-time", "--ideal-amp", "--dwell-mean", "--profile",
        "--kmer-model", "--prefix", "--dwell-std", "--threads",
        "--batchsize", "--paf", "--amp-noise", "--paf-ref", "--sam",
        "--coverage", "--digitisation", "--sample-rate", "--range",
        "--offset-mean", "--offset-std", "--bps", "--median-before-mean",
        "--median-before-std", "--trans-count", "--trans-trunc", "--cdna",
        "--ont-friendly", "--meth-freq", "--meth-model", "--meth-all-ctx",
    }
    assert all(
        set(item) >= {"option", "authority", "supported", "reason", "digest_participation"}
        for item in options
    )
    assert {item["authority"] for item in options} <= {
        "operator_owned", "profile_fixed", "workflow_fixed", "runtime_owned", "unsupported"
    }
    assert all(item["reason"] for item in options if not item["supported"])

    parameters = contract["operator_parameters"]
    assert set(parameters) == {
        "profile_id", "seed", "scale", "point_size", "fixed_width", "base_width",
        "base_limit", "signal_sample_limit", "show_samples", "show_base_colours",
        "remove_signal_outliers",
    }
    assert parameters["seed"]["minimum"] == 1
    assert parameters["seed"]["maximum"] == 2_147_483_647
    assert parameters["seed"]["default"] == 1
    assert parameters["base_limit"]["maximum"] == 1000
    assert contract["additionalProperties"] is False


def test_selected_read_span_authority_fails_closed_when_absent() -> None:
    with pytest.raises(service.OntSignalError, match="selected read mapping authority is unavailable"):
        service._selected_read_span({}, "read-1", "plasmid")


def test_selected_read_span_resolves_from_verified_indexed_mapping_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pysam

    root = tmp_path / "results"
    mapping_dir = root / "ont_signal_workbench" / "mappings" / "mapping-1"
    mapping_dir.mkdir(parents=True)
    paf = mapping_dir / "realign.paf"
    paf.write_text(
        "read-1\t5000\t100\t4900\t+\tplasmid\t6000\t99\t2200\t2000\t2100\t255\tss:Z:1D\n",
        encoding="utf-8",
    )
    compressed = Path(str(paf) + ".gz")
    pysam.tabix_compress(str(paf), str(compressed), force=True)
    pysam.tabix_index(
        str(compressed), seq_col=5, start_col=7, end_col=8, zerobased=True, force=True
    )
    index = Path(str(compressed) + ".tbi")
    monkeypatch.setattr(service, "get_results_dir", lambda: root)
    artifact = SimpleNamespace(
        kind="realign_paf",
        managed_relative_path=str(compressed),
        sha256=hashlib.sha256(compressed.read_bytes()).hexdigest(),
        size_bytes=compressed.stat().st_size,
        validation_receipt={
            "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
            "index_size_bytes": index.stat().st_size,
            "record_count": 1,
        },
    )

    assert service._selected_read_span_from_indexed_artifact(
        artifact, "read-1", "plasmid", 100, 120
    ) == {"contig": "plasmid", "start": 100, "end": 2200, "strand": "+"}


@pytest.mark.parametrize("start,end", [
    (None, 20), (1, None), (True, 20), (1, False), ("1", 20),
    (1, "20"), (0, 20), (-1, 20), (20, 19),
])
def test_selected_read_span_authority_rejects_malformed_coordinates(start, end) -> None:
    receipt = {"read_spans": {"read-1": {
        "contig": "plasmid", "start": start, "end": end, "strand": "forward",
    }}}
    with pytest.raises(service.OntSignalError, match="selected read mapping authority is unavailable"):
        service._selected_read_span(receipt, "read-1", "plasmid")


def test_comparison_compatibility_requires_complete_real_authority() -> None:
    simulated = {
        "molecule_type": "dna", "flow_cell_generation": "R10.4.1", "device_class": "MinION",
        "sample_rate": 5000, "digitisation": 8192, "range": 1536.598389,
        "compatibility_floor": "approximate_profile",
    }
    complete = service._derive_ideal_comparison_compatibility(
        simulated_profile=simulated,
        mapping_profile={"molecule_type": "dna", "basecall_model_id": "dna_r10.4.1_e8.2_400bps_sup@v4.3.0", "kmer_length": 2},
        move_source={"molecule_type": "dna", "basecall_model_id": "dna_r10.4.1_e8.2_400bps_sup@v4.3.0",
                     "source_runtime_identity": {"authority_state": "verified"}},
        raw_header={"sample_frequency": "5000", "digitisation": "8192", "range": "1536.598389"},
        run_receipt={"flow_cell_generation": "R10.4.1", "device_class": "MinION"},
    )
    assert complete["disposition"] == "approximate_profile"
    assert complete["evidence"]["mapping_profile_kmer_length"] == 2

    current = service._derive_ideal_comparison_compatibility(
        simulated_profile=simulated,
        mapping_profile={"molecule_type": "dna", "basecall_model_id": "dna_r10.4.1_e8.2_400bps_sup@v4.3.0", "kmer_length": 2},
        move_source={"molecule_type": "dna", "basecall_model_id": "dna_r10.4.1_e8.2_400bps_sup@v4.3.0",
                     "source_runtime_identity": {"authority_state": "verified"}},
        raw_header={"sample_frequency": "5000", "digitisation": "8192", "range": "1536.598389"},
        run_receipt={"flow_cell_generation": "R10.4.1", "device_class": "MinION"},
    )
    assert current["disposition"] == "approximate_profile"

    unknown = service._derive_ideal_comparison_compatibility(
        simulated_profile=simulated,
        mapping_profile={"molecule_type": "dna", "basecall_model_id": "dna_r10.4.1_e8.2_400bps_sup@v4.3.0", "kmer_length": 2},
        move_source={"molecule_type": "dna", "basecall_model_id": "dna_r10.4.1_e8.2_400bps_sup@v4.3.0",
                     "source_runtime_identity": {"authority_state": "legacy_unknown"}},
        raw_header={}, run_receipt={},
    )
    assert unknown["disposition"] == "legacy_unknown"
    assert unknown["missing_authorities"]

    mismatch = service._derive_ideal_comparison_compatibility(
        simulated_profile=simulated,
        mapping_profile={"molecule_type": "dna", "basecall_model_id": "dna_r9.4.1_sup@v3", "kmer_length": 2},
        move_source={"molecule_type": "rna", "basecall_model_id": "rna004_sup@v1",
                     "source_runtime_identity": {"authority_state": "verified"}},
        raw_header={"sample_frequency": "4000", "digitisation": "8192", "range": "1536.598389"},
        run_receipt={"flow_cell_generation": "R9.4.1", "device_class": "MinION"},
    )
    assert mismatch["disposition"] == "incompatible"
    assert mismatch["mismatches"]


def test_eight_profile_constants_match_v050_executable_source() -> None:
    contract = _load(CONFIG_ROOT / "squigulator_ideal_comparison_schema_v1.json")
    profiles = contract["profiles"]
    assert set(profiles) == {
        "dna-r9-min", "dna-r9-prom", "rna-r9-min", "rna-r9-prom",
        "dna-r10-min", "dna-r10-prom", "rna004-min", "rna004-prom",
    }
    assert profiles["dna-r10-min"] | {
        "sample_rate": 5000,
        "translocation_speed": 400,
        "dwell_mean": 13.0,
        "dwell_standard_deviation": 4.0,
    } == profiles["dna-r10-min"]
    assert profiles["dna-r10-prom"] | {
        "sample_rate": 5000,
        "translocation_speed": 400,
        "dwell_mean": 13.0,
        "dwell_standard_deviation": 4.0,
    } == profiles["dna-r10-prom"]
    for profile_id in ("dna-r10-min", "dna-r10-prom", "rna004-min", "rna004-prom"):
        assert profiles[profile_id]["compatibility_floor"] == "approximate_profile"
        assert "crude" in profiles[profile_id]["model_quality_warning"].lower()


def test_producer_and_comparison_renderer_are_distinct_network_denied_pins() -> None:
    producer = _load(CONFIG_ROOT / "squigulator_runtime_policy_v1.json")
    renderer = _load(CONFIG_ROOT / "comparison_render_runtime_policy_v1.json")

    assert producer["schema"] == "bms.ont-squigulator-runtime-policy.v1"
    assert producer["upstream"]["commit"] == SQUIGULATOR_COMMIT
    assert producer["source_asset"]["sha256"] == SQUIGULATOR_RELEASE_SHA256
    assert producer["network"] == "none"
    assert producer["wrapper_sha256"] == hashlib.sha256(
        (REPO_ROOT / str(producer["wrapper"])).read_bytes()
    ).hexdigest()
    assert renderer["schema"] == "bms.ont-squigualiser-comparison-runtime-policy.v1"
    assert renderer["upstream"]["commit"] == SQUIGUALISER_COMMIT
    assert renderer["network"] == "none"
    assert renderer["wrapper_sha256"] == hashlib.sha256(
        (REPO_ROOT / str(renderer["wrapper"])).read_bytes()
    ).hexdigest()
    assert producer["runtime_id"] != renderer["runtime_id"]
    assert producer["oci_digest"] != renderer["oci_digest"]


def test_runtime_build_sources_pin_named_release_and_separate_wrappers() -> None:
    producer = (REPO_ROOT / "docker/ont-squigulator.Dockerfile").read_text(encoding="utf-8")
    renderer = (REPO_ROOT / "docker/ont-squigualiser-comparison.Dockerfile").read_text(encoding="utf-8")
    producer_wrapper = REPO_ROOT / "scripts/ont_squigulator_runtime.py"
    renderer_wrapper = REPO_ROOT / "scripts/ont_signal_comparison_runtime.py"

    assert "squigulator-v0.5.0-release.tar.gz" in producer
    assert "debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171" in producer
    assert "python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579" in producer
    assert "1704d57628b46e9a8e06d90e92a6c38f87e6a04ade7afc3e7a718b35de889a13" not in producer
    assert SQUIGULATOR_RELEASE_SHA256 in producer
    assert SQUIGULATOR_COMMIT in producer
    assert 'test "$observed_version" = "$SQUIGULATOR_VERSION"' in producer
    assert '|| true' not in producer.split('make -C /src', 1)[0].rsplit('tar -xzf', 1)[1]
    assert "COPY scripts/ont_squigulator_runtime.py" in producer
    assert 'Path("/usr/local/lib/python3.12/site-packages")' in producer
    assert "ont-squigulator-index.c" in producer
    assert "bms-slow5-index" in producer
    assert SQUIGUALISER_COMMIT in renderer
    assert "exec(open(" not in renderer
    assert "COPY scripts/ont_signal_comparison_runtime.py" in renderer
    assert "ont_signal_runtime.py" not in renderer
    assert producer_wrapper.is_file()
    assert renderer_wrapper.is_file()
    assert "--full-contigs" in producer_wrapper.read_text(encoding="utf-8")
    assert "--shared_x" in renderer_wrapper.read_text(encoding="utf-8")


def test_comparison_runtime_policy_is_opened_once_and_wrapper_is_repo_constrained() -> None:
    worker = (REPO_ROOT / "platform/api/services/ont_signal_worker.py").read_text(encoding="utf-8")
    body = worker.split("def _comparison_runtime_identity", 1)[1].split("def _comparison_container_command", 1)[0]
    assert "os.O_NOFOLLOW" in body
    assert ".read_text(" not in body
    assert "policy_path.read_bytes" not in body
    assert "wrapper_relative.parts[0] != \"scripts\"" in body
    assert "expected_policy" in body


def test_development_api_unit_receives_both_comparison_runtime_identities() -> None:
    services = (REPO_ROOT / "biomodstack_services.py").read_text(encoding="utf-8")
    assert "BMS_ONT_SQUIGULATOR_IMAGE" in services
    assert "BMS_ONT_SQUIGULATOR_IMAGE_DIGEST" in services
    assert "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE" in services
    assert "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE_DIGEST" in services


def test_worker_claims_comparison_and_preserves_two_stage_runtime_order() -> None:
    worker = (REPO_ROOT / "platform/api/services/ont_signal_worker.py").read_text(encoding="utf-8")
    assert "OntSignalComparisonJob" in worker
    assert "_process_comparison" in worker
    assert "squigulator_producer" in worker
    assert "squigualiser_comparison_renderer" in worker
    assert worker.index("squigulator_producer") < worker.index("squigualiser_comparison_renderer")
    assert '"--pids-limit", "64"' in worker
    assert '"--memory", "1g"' in worker
    assert '"--pids-limit", "128"' in worker
    assert '"--memory", "4g"' in worker


def test_producer_emits_complete_truth_and_coordinate_receipts(
    tmp_path: Path, monkeypatch
) -> None:
    reference = tmp_path / "reference.fasta"
    reference.write_text(">plasmid\nAACCGGTTAACCGGTT\n", encoding="ascii")
    output = tmp_path / "output"
    output.mkdir()
    (output / ".owner").write_text("comparison-job-1", encoding="utf-8")
    generated_id = "generated-read-0001"
    input_id = producer_runtime.virtual_sequence_id(
        hashlib.sha256(reference.read_bytes()).hexdigest(), "plasmid", 1, 16, "reverse"
    )

    def fake_run(command: list[str], **_kwargs):
        assert command == producer_runtime.build_squigulator_argv(
            profile_id="dna-r10-min", seed=7,
            input_fasta=str(output / "simulation_input.fasta"), output_root=str(output),
        )
        (output / "simulated.blow5").write_bytes(b"BLOW5-one-record")
        (output / "simulated.blow5.idx").write_bytes(b"IDX-one-record")
        (output / "simulated_reads.fasta").write_text(
            f">{generated_id}\nAACCGGTTAACCGGTT\n", encoding="ascii"
        )
        (output / "simulated_source.paf").write_text(
            f"{generated_id}\t104\t0\t104\t+\t{input_id}\t8\t0\t8\t8\t8\t255\t"
            "sc:f:1.000000\tsh:f:0.000000\tss:Z:13,13,13,13,13,13,13,13,\n",
            encoding="ascii",
        )
        (output / "simulated_source.sam").write_text(
            f"@HD\tVN:1.6\n@SQ\tSN:{input_id}\tLN:16\n"
            f"{generated_id}\t0\t{input_id}\t1\t255\t16M\t*\t0\t0\tAACCGGTTAACCGGTT\t*\t"
            "si:Z:0,104,0,8\tss:Z:13,13,13,13,13,13,13,13,\n",
            encoding="ascii",
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(producer_runtime, "run_bounded_command", fake_run)
    monkeypatch.setattr(
        producer_runtime,
        "validate_blow5",
        lambda *_args, **_kwargs: {
            "record_count": 1,
            "read_id": generated_id,
            "signal_length": 104,
            "calibration_fields": {"digitisation": 8192, "offset": 13.380569389019,
                "range": 1536.598389, "sampling_rate": 5000},
            "header_fields": {"sample_frequency": "5000"},
        },
        raising=False,
    )

    manifest = producer_runtime.produce_comparison(
        reference_fasta=reference,
        output=output,
        reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        contig="plasmid",
        window_start=1,
        window_end=16,
        orientation="reverse",
        profile_id="dna-r10-min",
        seed=7,
    )

    expected = {
        "simulation_input_fasta",
        "simulation_coordinate_map",
        "simulated_blow5",
        "simulated_blow5_index",
        "simulated_read_fasta",
        "simulated_read_id_map",
        "simulated_source_paf",
        "simulated_normalized_paf",
        "simulated_source_sam",
        "simulated_normalized_sam",
    }
    assert {item["kind"] for item in manifest["artifacts"]} == expected
    assert (output / ".owner").read_text(encoding="utf-8") == "comparison-job-1"
    assert ".owner" not in {item["filename"] for item in manifest["artifacts"]}
    assert manifest["generated_read_id_relation"] == {
        "input_sequence_id": manifest["virtual_sequence_id"],
        "generated_read_id": generated_id,
    }
    coordinate_map = json.loads((output / "simulation_coordinate_map.json").read_text())
    assert coordinate_map["source"] == {
        "contig": "plasmid",
        "start": 1,
        "end": 16,
        "orientation": "reverse",
        "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
    }
    normalized = (output / "simulated_normalized.paf").read_text(encoding="ascii")
    assert f"\tplasmid\t16\t0\t8\t" in normalized
    assert "or:Z:reverse" in normalized


def test_producer_rejects_cross_artifact_signal_and_profile_calibration_mismatch() -> None:
    paf = ["read-1", "104", "0", "104"]
    coherent = {"signal_length": 104, "calibration_fields": {
        "digitisation": 8192, "offset": 13.380569389019,
        "range": 1536.598389, "sampling_rate": 5000,
    }}
    producer_runtime.validate_profile_signal_receipt(coherent, paf, "dna-r10-min")
    with pytest.raises(ValueError, match="signal length diverges"):
        producer_runtime.validate_profile_signal_receipt(
            {**coherent, "signal_length": 128}, paf, "dna-r10-min"
        )
    with pytest.raises(ValueError, match="calibration diverges"):
        producer_runtime.validate_profile_signal_receipt(
            {**coherent, "calibration_fields": {**coherent["calibration_fields"], "sampling_rate": 4000}},
            paf, "dna-r10-min",
        )


def test_producer_reads_only_its_active_broker_owned_reference_alias(tmp_path: Path) -> None:
    reference = tmp_path / "reference.fasta"
    reference.write_text(">plasmid\nAACCGGTT\n", encoding="ascii")
    descriptor = os.open(reference, os.O_RDONLY)
    metadata = {
        "parents": [{
            "alias": "reference.fasta",
            "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
            "size_bytes": reference.stat().st_size,
        }]
    }
    parents = producer_runtime.BrokerParents(metadata, [descriptor])
    alias = parents.paths["reference.fasta"]
    try:
        with pytest.raises(ValueError, match="bounded regular-file policy"):
            producer_runtime._read_reference_window(alias, "plasmid", 1, 8)
        setattr(producer_runtime, "_ACTIVE_BROKER_PARENTS", parents)
        assert producer_runtime._read_reference_window(alias, "plasmid", 1, 8) == ("AACCGGTT", 8)
    finally:
        setattr(producer_runtime, "_ACTIVE_BROKER_PARENTS", None)
        parents.close()


def test_producer_rejects_sam_alignment_shape_that_diverges_from_truth(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sam"
    destination = tmp_path / "normalized.sam"
    source.write_text(
        "@SQ\tSN:input-1\tLN:8\n"
        "generated-1\t0\tinput-1\t1\t255\t1M\t*\t0\t0\tAACCGGTT\t*\n",
        encoding="ascii",
    )
    with pytest.raises(ValueError, match="SAM .*invalid"):
        producer_runtime._normalize_sam(
            source, destination, generated_id="generated-1", contig="plasmid",
            contig_length=100, start=10, orientation="forward", sequence="AACCGGTT",
        )


def test_plot_tracks_allows_exact_governed_output_root() -> None:
    command = renderer_runtime.build_plot_tracks_argv(
        "/tmp/comparison.commands", "/output"
    )
    assert command[command.index("-o") + 1] == "/output"


def test_renderer_creates_real_and_simulated_tracks_before_shared_x_output(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    parents = {}
    for name in (
        "real.blow5", "real.blow5.idx", "real-mapping.paf.gz",
        "real-mapping.paf.gz.tbi", "real-moves.bam", "reference.fasta",
        "simulated.blow5", "simulated.blow5.idx", "simulated_reads.fasta",
        "simulated_normalized.paf",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode())
        parents[name] = path
    parents["simulated_reads.fasta"].write_text(
        ">generated-read-0001\nAACCGGTT\n", encoding="ascii"
    )
    calls: list[list[str]] = []
    command_files: list[str] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(command)
        commands_file = Path(command[command.index("-f") + 1])
        command_files.append(commands_file.read_text(encoding="utf-8"))
        output_dir = Path(command[command.index("-o") + 1])
        (output_dir / "comparison.html").write_text(
            "<html><body>Bokeh combined</body></html>", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(renderer_runtime, "run_bounded_command", fake_run)
    monkeypatch.setattr(
        renderer_runtime,
        "selected_read_fasta",
        lambda _bam, read_id, destination: destination.write_text(
            f">{read_id}\nAACCGGTT\n", encoding="ascii"
        ),
        raising=False,
    )
    monkeypatch.setattr(
        renderer_runtime,
        "bounded_real_reference_mapping",
        lambda source, read_id, _work, contig, start, end: (
            source,
            {"selected_read_id": read_id, "region": f"{contig}:{start}-{end}"},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        renderer_runtime,
        "indexed_simulated_reference_mapping",
        lambda source, _work: (
            source,
            {"parent_sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        ),
        raising=False,
    )
    receipt = renderer_runtime.render_comparison(
        output=output,
        real_blow5=parents["real.blow5"],
        real_mapping=parents["real-mapping.paf.gz"],
        real_moves=parents["real-moves.bam"],
        reference_fasta=parents["reference.fasta"],
        simulated_blow5=parents["simulated.blow5"],
        simulated_fasta=parents["simulated_reads.fasta"],
        simulated_mapping=parents["simulated_normalized.paf"],
        real_read_id="read-1",
        profile_id="dna-r10-min",
        contig="plasmid",
        start=5,
        end=12,
        orientation="reverse",
        molecule_type="dna",
        real_kmer_length=2,
        simulated_kmer_length=9,
        base_shift=0,
        render_params={"scale": "none", "point_size": 0.5, "base_width": 10,
                       "base_limit": 1000, "signal_sample_limit": 100000,
                       "fixed_width": False, "show_samples": True,
                       "show_base_colours": True, "remove_signal_outliers": False},
    )

    assert [command[1] for command in calls] == ["plot_tracks"]
    assert "--shared_x" in calls[0]
    assert "--auto_height" in calls[0]
    assert len(command_files) == 1
    track_lines = command_files[0].splitlines()
    assert track_lines[:2] == ["num_commands=2", "plot_heights=*,*"]
    assert len(track_lines) == 4
    assert all(" plot_pileup " in line for line in track_lines[2:])
    assert all(f"--file {parents['reference.fasta']}" in line for line in track_lines[2:])
    assert all("--region plasmid:5-12" in line for line in track_lines[2:])
    assert f"--slow5 {parents['real.blow5']}" in track_lines[2]
    assert f"--slow5 {parents['simulated.blow5']}" in track_lines[3]
    assert "--kmer_length 2" in track_lines[2]
    assert "--kmer_length 9" in track_lines[3]
    html = (output / "comparison.html").read_text(encoding="utf-8")
    assert "REAL · INSTRUMENT ACQUIRED · read-1" in html
    assert "SIMULATED IDEAL · SQUIGULATOR 0.5.0 · dna-r10-min" in html
    assert "not instrument-acquired evidence" in html
    assert receipt["stage_order"] == ["real_track", "simulated_track", "plot_tracks"]


def _governed_html(body: str) -> str:
    return (
        "<html><body>Bokeh"
        "REAL · INSTRUMENT ACQUIRED · read-1"
        "SIMULATED IDEAL · SQUIGULATOR 0.5.0 · dna-r10-min"
        "Simulated signal is model-derived from the selected reference and profile. "
        "It is not instrument-acquired evidence."
        f"{body}</body></html>"
    )


def test_renderer_allows_inert_url_text_inside_inline_bokeh_script(tmp_path: Path) -> None:
    path = tmp_path / "comparison.html"
    path.write_text(
        _governed_html('<script>const docs = "https://example.invalid/help";</script>'),
        encoding="utf-8",
    )
    receipt = renderer_runtime.validate_comparison_html(
        path, real_read_id="read-1", profile_id="dna-r10-min"
    )
    assert receipt["size_bytes"] == path.stat().st_size


def test_renderer_passes_retained_parent_descriptors_to_child_commands(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(renderer_runtime, "run_bounded_command", fake_run)
    renderer_runtime._run(["squigualiser", "--version"], parent_fds=(41, 42))
    assert captured["parent_fds"] == (41, 42)


def test_renderer_kills_child_when_combined_logs_cross_eight_mib() -> None:
    command = [
        sys.executable,
        "-c",
        "import os,time; os.write(1,b'x'*(8*1024*1024)); os.write(2,b'y'); time.sleep(10)",
    ]
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="comparison renderer command log ceiling exceeded"):
        renderer_runtime._run(command, timeout=30)

    assert time.monotonic() - started < 3
    exc = worker_module._comparison_container_failure(
        "squigualiser_comparison_renderer",
        "RuntimeError: comparison renderer command log ceiling exceeded\n",
    )
    assert worker_module._comparison_failure_reason(exc) == "log_limit"


def test_renderer_timeout_kills_descendant_after_group_leader_exits(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    leader = (
        "import pathlib,subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid))"
    )
    child_pid: int | None = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            renderer_runtime._run([sys.executable, "-c", leader], timeout=0.5)
        child_pid = int(child_pid_path.read_text(encoding="ascii"))
        status = Path(f"/proc/{child_pid}/stat")
        deadline = time.monotonic() + 1
        while status.exists() and status.read_text(encoding="ascii").split()[2] != "Z":
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert not status.exists() or status.read_text(encoding="ascii").split()[2] == "Z"
    finally:
        if child_pid is not None:
            try:
                status = Path(f"/proc/{child_pid}/stat")
                if status.exists() and status.read_text(encoding="ascii").split()[2] != "Z":
                    os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("active", [
    '<script src="https://example.invalid/x.js"></script>',
    '<link rel="stylesheet" href="//example.invalid/x.css">',
    '<iframe srcdoc="x"></iframe>', '<object data="x"></object>', '<embed src="x">',
    '<style>@import "//example.invalid/x.css";</style>',
    '<style>.x{background:url(//example.invalid/x.png)}</style>',
    '<meta http-equiv="refresh" content="0;url=https://example.invalid/">',
])
def test_renderer_rejects_actual_active_resource_forms(tmp_path: Path, active: str) -> None:
    path = tmp_path / "comparison.html"
    path.write_text(_governed_html(active), encoding="utf-8")
    with pytest.raises(RuntimeError, match="external active resource"):
        renderer_runtime.validate_comparison_html(
            path, real_read_id="read-1", profile_id="dna-r10-min"
        )


@pytest.mark.asyncio
async def test_comparison_worker_retains_every_real_and_generated_parent_before_ready(
    tmp_path: Path, monkeypatch
) -> None:
    files: dict[str, Path] = {}
    for name in ("reference.fasta", "mapping.paf.gz", "mapping.paf.gz.tbi",
                 "real.blow5", "real.blow5.idx", "filtered_moves.bam"):
        path = tmp_path / name
        path.write_bytes((name + "-authority").encode())
        files[name] = path
    files["reference.fasta"].write_text(">plasmid\n" + "ACGT" * 600 + "\n", encoding="ascii")
    raw_manifest = {"artifacts": [
        {"kind": "blow5", "path": str(files["real.blow5"]),
         "sha256": hashlib.sha256(files["real.blow5"].read_bytes()).hexdigest(),
         "bytes": files["real.blow5"].stat().st_size},
        {"kind": "blow5_index", "path": str(files["real.blow5.idx"]),
         "sha256": hashlib.sha256(files["real.blow5.idx"].read_bytes()).hexdigest(),
         "bytes": files["real.blow5.idx"].stat().st_size},
    ]}
    raw = SimpleNamespace(id="raw-1", run_id="run-1", observed_generation=1,
        state="ready", format="blow5", artifact_manifest=raw_manifest,
        manifest_sha256=hashlib.sha256(json.dumps(raw_manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    source = SimpleNamespace(id="moves-1", run_id="run-1", observed_generation=1,
        raw_representation_id="raw-1", validation_state="ready", molecule_type="dna",
        artifact_sha256="a" * 64, artifact_size_bytes=10, input_file_id="input-1",
        source_job_id="job-1", external_registration_receipt_id=None,
        read_inventory_sha256="b" * 64, validation_receipt={"managed_outputs": {
            "filtered_move_bam": str(files["filtered_moves.bam"])},
            "managed_output_sha256s": {
                "filtered_move_bam_sha256": hashlib.sha256(files["filtered_moves.bam"].read_bytes()).hexdigest(),
                "filtered_move_bam_size_bytes": files["filtered_moves.bam"].stat().st_size}})
    profile = SimpleNamespace(id="profile-1", molecule_type="dna", kmer_length=2,
                              signal_move_offset=0, calibration_artifact_id="calibration-1",
                              basecall_model_id="model-1", approval_receipt={"base_shift_value": 0})
    mapping = SimpleNamespace(id="mapping-1", mode="signal_to_reference", state="ready",
        run_id="run-1", observed_generation=1, raw_representation_id="raw-1",
        move_source_id="moves-1", mapping_profile_id="profile-1", reference_revision_id="rev-1",
        alignment_job_id="alignment-1", alignment_session_id="alignment-session-1",
        parent_mapping_job_id="read-mapping-1")
    read_mapping = SimpleNamespace(id="read-mapping-1", mode="signal_to_read", state="ready",
        run_id="run-1", observed_generation=1, raw_representation_id="raw-1",
        move_source_id="moves-1", mapping_profile_id="profile-1")
    viewer = SimpleNamespace(id="viewer-1", run_id="run-1", observed_generation=1,
        raw_representation_id="raw-1", mapping_profile_id="profile-1",
        alignment_job_id="alignment-1", alignment_session_id="alignment-session-1",
        signal_state={"read_mapping_job_id": "read-mapping-1",
                      "reference_mapping_job_id": "mapping-1"})
    mapping_artifact = SimpleNamespace(id="mapping-artifact-1", mapping_job_id="mapping-1",
        kind="realign_paf", managed_relative_path=str(files["mapping.paf.gz"]),
        sha256=hashlib.sha256(files["mapping.paf.gz"].read_bytes()).hexdigest(),
        size_bytes=files["mapping.paf.gz"].stat().st_size,
        validation_receipt={"index_sha256": hashlib.sha256(files["mapping.paf.gz.tbi"].read_bytes()).hexdigest(),
                            "index_size_bytes": files["mapping.paf.gz.tbi"].stat().st_size},
        parent_identities={"raw_manifest_sha256": raw.manifest_sha256,
                           "move_bam_sha256": source.artifact_sha256,
                           "move_read_inventory_sha256": source.read_inventory_sha256})
    job = SimpleNamespace(id="comparison-1", viewer_session_id="viewer-1",
        claim_token="token-1", state="running",
        cancel_requested_at=None, lease_expires_at=datetime.now(UTC).replace(tzinfo=None, year=2099),
        run_id="run-1", observed_generation=1, raw_representation_id="raw-1",
        mapping_artifact_id="mapping-artifact-1", reference_revision_id="rev-1",
        selected_read_id="read-1", reference_contig="plasmid", reference_start=100,
        reference_end=120, simulation_orientation="forward", sequence_basis="managed_reference",
        simulation_settings={"operator_owned": {"profile_id": "dna-r10-min", "seed": 7},
                             "profile": {"kmer_length": 9}}, render_params={"scale": "none",
        "point_size": 0.5, "fixed_width": False, "base_width": 10, "base_limit": 1000,
        "signal_sample_limit": 100000, "show_samples": True, "show_base_colours": True,
        "remove_signal_outliers": False}, stage_receipts={}, generated_read_id=None)
    reference_revision = SimpleNamespace(id="rev-1", artifact_id="reference-artifact-1",
                                         topology="linear", coordinate_contract="one_based_closed")
    reference_artifact = SimpleNamespace(id="reference-artifact-1", managed_relative_path="reference.fasta",
        sha256=hashlib.sha256(files["reference.fasta"].read_bytes()).hexdigest(),
        size_bytes=files["reference.fasta"].stat().st_size)
    rows = {(OntSignalComparisonJob, "comparison-1"): job,
            (service.OntSignalViewerSession, "viewer-1"): viewer,
            (OntSignalMappingArtifact, "mapping-artifact-1"): mapping_artifact,
            (OntSignalMappingJob, "mapping-1"): mapping,
            (OntSignalMappingJob, "read-mapping-1"): read_mapping,
            (OntRawSignalRepresentation, "raw-1"): raw,
            (OntMoveTableSource, "moves-1"): source,
            (OntSignalMappingProfile, "profile-1"): profile,
            (InputFile, "input-1"): SimpleNamespace(directory=str(tmp_path), filename="unused.bam")}
    domain_rows = {(MolBioNGSReferenceRevision, "rev-1"): reference_revision,
                   (MolBioNGSReferenceArtifact, "reference-artifact-1"): reference_artifact}

    class Result:
        rowcount = 1

    class Session:
        def __init__(self, values): self.values = values
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def get(self, model, identifier): return self.values.get((model, identifier))
        async def execute(self, statement):
            for column, value in getattr(statement, "_values", {}).items():
                key = getattr(column, "key", str(column)); resolved = getattr(value, "value", value)
                if key in {"state", "generated_read_id", "stage_receipts", "output_manifest"}:
                    setattr(job, key, resolved)
            return Result()
        async def commit(self): return None
        async def rollback(self): return None
        def add_all(self, _values): return None
        def add(self, _value): return None

    class Factory:
        def __init__(self, values): self.values = values
        def __call__(self): return Session(self.values)

    class Parent:
        def __init__(self, alias, path):
            self.alias, self.sha256, self.size_bytes = alias, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size

    class Parents:
        instance = None
        def __init__(self, _roots): self.parents = []; self.closed = False; Parents.instance = self
        def __enter__(self): return self
        def __exit__(self, *_args): self.closed = True
        def assert_unbroken(self): assert not self.closed
        async def pin_async(self, path, *, alias, expected_sha256, expected_size):
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
            assert path.stat().st_size == expected_size
            parent = Parent(alias, path); self.parents.append(parent); return parent
        def subset(self, aliases):
            return SimpleNamespace(parents=tuple(parent for parent in self.parents if parent.alias in aliases))

    worker = OntSignalWorker(Factory(rows), Factory(domain_rows))
    monkeypatch.setattr(worker_module, "RetainedParentSet", Parents)
    monkeypatch.setattr(worker, "_governed_parent_roots", lambda: (tmp_path,))
    monkeypatch.setattr(worker, "_output_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(worker_module, "get_molbio_ngs_reference_root", lambda: tmp_path)
    monkeypatch.setattr(worker, "_resolve_selected_raw_partitions_async",
        lambda *_args: _async_value(([(files["real.blow5"], files["real.blow5.idx"])], {"blow5": []})))

    calls = []
    span_lookups = []

    def selected_span(_artifact, read_id, contig, start, end):
        span_lookups.append((read_id, contig, start, end))
        return {"contig": "plasmid", "start": 1, "end": 2200, "strand": "forward"}

    monkeypatch.setattr(
        service, "_selected_read_span_from_indexed_artifact", selected_span
    )

    async def fake_invoke(parents, arguments, kind, _item, _token, output, _allowed=None):
        aliases = {parent.alias for parent in parents.parents}; calls.append((kind, aliases, arguments))
        if kind == "squigulator_producer":
            assert aliases == {"reference.fasta"}
            assert arguments[arguments.index("--window-start") + 1] == "92"
            assert arguments[arguments.index("--window-end") + 1] == "128"
            generated = "generated-read-1"
            payloads = {
                "simulation_input.fasta": ">input\nACGT\n", "simulation_coordinate_map.json": "{}\n",
                "simulated.blow5": "blow5", "simulated.blow5.idx": "index",
                "simulated_reads.fasta": f">{generated}\nACGT\n", "simulated_read_id_map.json": "{}\n",
                "simulated_source.paf": "source\n", "simulated_normalized.paf": "normalized\n",
                "simulated_source.sam": "source\n", "simulated_normalized.sam": "normalized\n",
            }
            for name, value in payloads.items(): (output / name).write_text(value)
            artifacts = [{"kind": worker_module.COMPARISON_PRODUCER_FILENAMES[name], "filename": name,
                          "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                          "size_bytes": (output / name).stat().st_size, "media_type": "text/plain",
                          "validation_receipt": {}} for name in payloads]
            (output / "producer_manifest.json").write_text(json.dumps({"schema": "bms.ont-squigulator-producer-manifest.v1",
                "generated_read_id_relation": {"input_sequence_id": "input", "generated_read_id": generated},
                "artifacts": artifacts, "parents": {"reference_fasta_sha256": reference_artifact.sha256}}))
        else:
            assert {"simulated.blow5", "simulated.blow5.idx", "simulated_reads.fasta",
                    "simulated_normalized.paf", "producer_manifest.json"} <= aliases
            manifest_index = arguments.index("--producer-manifest")
            assert arguments[manifest_index + 1] == "/parents/producer_manifest.json"
            assert arguments[arguments.index("--start") + 1] == "100"
            assert arguments[arguments.index("--end") + 1] == "120"
            assert arguments[arguments.index("--real-kmer-length") + 1] == "2"
            assert arguments[arguments.index("--simulated-kmer-length") + 1] == "9"
            (output / "comparison.html").write_text("<html>Bokeh REAL SIMULATED</html>")
            producer = json.loads((output / "producer_manifest.json").read_text())
            html_path = output / "comparison.html"
            manifest = {"schema": "bms.ont-signal-comparison-manifest.v1", "parents": {},
                "artifacts": [*producer["artifacts"], {"kind": "comparison_html", "filename": "comparison.html",
                    "sha256": hashlib.sha256(html_path.read_bytes()).hexdigest(), "size_bytes": html_path.stat().st_size,
                    "media_type": "text/html", "validation_receipt": {}}]}
            (output / "comparison_manifest.json").write_text(json.dumps(manifest))
        return {"returncode": 0, "runtime_identity": {"stage": kind}}

    async def _async_value(value): return value
    monkeypatch.setattr(worker, "_invoke", fake_invoke)
    monkeypatch.setattr(worker, "_comparison_runtime_identity", lambda stage: {"stage": stage})

    await worker._process_comparison("comparison-1", "token-1")

    assert [kind for kind, _aliases, _arguments in calls] == [
        "squigulator_producer", "squigualiser_comparison_renderer"
    ]
    assert span_lookups == [("read-1", "plasmid", 100, 120)]
    assert job.state == "ready"
    assert job.generated_read_id == "generated-read-1"
    assert job.output_manifest["parents"]["real_blow5"] == {
        "routing_sha256": None,
        "blow5": [{
            "sha256": hashlib.sha256(files["real.blow5"].read_bytes()).hexdigest(),
            "index_sha256": hashlib.sha256(files["real.blow5.idx"].read_bytes()).hexdigest(),
        }],
    }
    assert Parents.instance is not None and Parents.instance.closed is True


def test_canonical_ont_docs_preserve_acquired_authority_and_do_not_overclaim_live_squigulator() -> None:
    docs = (REPO_ROOT / "docs/Lab_Automation_MolBio_and_Sequencing.md").read_text(encoding="utf-8")
    assert "workflows/ngs/ont_methylation_analysis.nf" in docs
    assert "workflows/nanopore_methylation.nf" not in docs
    assert "Read and Signal Workbench" in docs
    assert "acquired signal" in docs
    assert "Squigualiser" in docs
    assert "live acceptance" in docs


def test_runtime_source_denominator_v2_covers_comparison_surface_and_preserves_v1() -> None:
    import hashlib
    import rfc8785

    v1_path = REPO_ROOT / "schemas/ngs_molbio_runtime/runtime-source-denominator-v1.json"
    v2_path = REPO_ROOT / "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json"
    v1 = _load(v1_path)
    v2 = _load(v2_path)
    assert v1["schema"] == "bms.ngs-molbio.runtime-source-denominator.v1"
    required = {
        "docker/ont-squigulator.Dockerfile",
        "docker/ont-squigualiser-comparison.Dockerfile",
        "platform/api/migrations/add_ont_signal_comparisons.py",
        "platform/api/migrations/ont_signal_comparison_schema_contract.py",
        "platform/api/config/ont_signal_workbench/squigulator_ideal_comparison_schema_v1.json",
        "platform/frontend/src/components/ngs/OntSignalIdealComparison.tsx",
        "platform/frontend/tests/vitest/ontSignalIdealComparison.test.tsx",
        "docs/Lab_Automation_MolBio_and_Sequencing.md",
        "nextflow.config",
        "platform/api/runtime_policy.py",
        "scripts/biomodstack_dev_sync.py",
        "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json",
    }
    assert required <= set(v2["paths"])
    unsigned = {key: value for key, value in v2.items() if key != "content_sha256"}
    assert v2["content_sha256"] == hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()


def test_capability_inventory_v2_adds_squigulator_without_relabeling_squigualiser() -> None:
    inventory = _load(REPO_ROOT / "platform/api/config/ngs_molbio/capability_inventory_v2.json")
    schema = _load(REPO_ROOT / "schemas/ngs_molbio/capability-inventory-v2.schema.json")

    assert inventory["schema"] == "bms.ngs-molbio.capability-inventory.v2"
    assert inventory["schema_version"] == 2
    assert len(inventory["capabilities"]) == 22
    assert inventory["content_sha256"] == _canonical_sha256(inventory)
    assert schema["properties"]["capabilities"]["minItems"] == 22
    assert schema["properties"]["capabilities"]["maxItems"] == 22
    by_id = {item["capability_id"]: item for item in inventory["capabilities"]}
    squigulator = by_id["ngs.ont.squigulator_ideal_comparison"]
    assert squigulator["parameter_schema_id"] == "bms.ont-squigulator-ideal-comparison.v1"
    assert squigulator["canonical_source_destination"] == "/ngs"
    assert squigulator["viewer_destination"].startswith("/ngs?")
    assert "ngs_reference" in squigulator["accepted_source_roles"]
    assert "ngs_instrument_signal" in squigulator["accepted_source_roles"]
    assert all("squigulator" not in item["capability_id"] for item in inventory["capabilities"] if item["capability_id"] == "ngs.ont.squigualiser")
    for capability_id in ("ngs.ont.basecall_dna", "ngs.ont.basecall_rna"):
        row = by_id[capability_id]
        assert "emit_moves" in row["classified_parameter_keys"]
        assert "emit_moves" not in row["unclassified_parameter_keys"]
