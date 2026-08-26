from __future__ import annotations

import asyncio
import array
import hashlib
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Column, DateTime, String, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

for optional_module in ("pysam", "pyslow5"):
    try:
        __import__(optional_module)
    except ImportError:
        stub = types.ModuleType(optional_module)
        stub.Open = None  # type: ignore[attr-defined]
        stub.TabixFile = None  # type: ignore[attr-defined]
        sys.modules[optional_module] = stub

from services import ont_signal_worker as worker_module
from services import ont_raw_signal
from services.ont_signal_worker import OntSignalWorker

ContainerCleanupError = getattr(worker_module, "ContainerCleanupError", RuntimeError)
OutputLimitExceeded = getattr(worker_module, "OutputLimitExceeded", RuntimeError)
RetainedParentSet = getattr(worker_module, "RetainedParentSet", None)


RUNTIME_PATH = Path(__file__).parents[3] / "scripts" / "ont_signal_runtime.py"
SPEC = importlib.util.spec_from_file_location("ont_signal_runtime_contract", RUNTIME_PATH)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def test_runtime_denominator_covers_alignment_session_authority() -> None:
    denominator_path = (
        Path(__file__).parents[3]
        / "schemas/ngs_molbio_runtime/runtime-source-denominator-v1.json"
    )
    denominator = json.loads(denominator_path.read_text(encoding="utf-8"))

    assert "platform/api/services/ngs_alignment_sessions.py" in denominator["paths"]


def test_runtime_denominator_covers_source_pin_authority() -> None:
    denominator_path = (
        Path(__file__).parents[3]
        / "schemas/ngs_molbio_runtime/runtime-source-denominator-v1.json"
    )
    denominator = json.loads(denominator_path.read_text(encoding="utf-8"))

    assert "platform/api/config/ngs_molbio/source_pin_v1.json" in denominator["paths"]


def test_profile_sourced_base_shift_requires_bound_profile_authority() -> None:
    with pytest.raises(RuntimeError, match="profile-sourced base shift"):
        worker_module._effective_base_shift({
            "base_shift_source": "profile",
            "base_shift_value": 0,
        })

    assert worker_module._effective_base_shift({
        "base_shift_source": "profile",
        "base_shift_value": 0,
        "base_shift_profile_id": "mapping-profile-1",
        "base_shift_profile_sha256": "a" * 64,
        "base_shift_effective_value": 7,
    }) == 7
    assert worker_module._effective_base_shift({
        "base_shift_source": "explicit",
        "base_shift_value": -3,
    }) == -3


def test_mapping_profile_render_args_bind_exact_kmer_length() -> None:
    assert worker_module._mapping_profile_render_args(
        SimpleNamespace(kmer_length=2)
    ) == ["--kmer-length", "2"]
    with pytest.raises(RuntimeError, match="k-mer length"):
        worker_module._mapping_profile_render_args(SimpleNamespace(kmer_length=0))


def test_raw_and_workbench_read_leases_share_one_break_generation(
    tmp_path: Path,
) -> None:
    previous_handler = signal.getsignal(signal.SIGIO)
    source = tmp_path / "parent.bin"
    source.write_bytes(b"retained")
    parents = RetainedParentSet()
    try:
        parents.pin(
            source,
            alias="parent.bin",
            expected_sha256=hashlib.sha256(b"retained").hexdigest(),
            expected_size=len(b"retained"),
        )
        assert ont_raw_signal.pin_conversion_source_descriptors(
            {"source_authorities": []}
        ) == []
        handler = signal.getsignal(signal.SIGIO)
        assert callable(handler)
        handler(signal.SIGIO, None)
        assert ont_raw_signal.source_lease_break_requested()
        with pytest.raises(RuntimeError, match="read lease was broken"):
            parents.assert_unbroken()
    finally:
        parents.close()
        signal.signal(signal.SIGIO, previous_handler)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path, kind: str, **extra: Any) -> dict[str, Any]:
    artifact = ont_raw_signal._file_artifact(
        path,
        f"{kind}-{path.name}",
        kind=kind,
    )
    artifact.update(extra)
    return artifact


def _partitioned_representation(tmp_path: Path) -> tuple[SimpleNamespace, Path, Path, Path, Path, Path]:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    first_fingerprint = "1" * 64
    second_fingerprint = "2" * 64
    first = outputs / f"{first_fingerprint}.blow5"
    second = outputs / f"{second_fingerprint}.blow5"
    first.write_bytes(b"first-blow5")
    second.write_bytes(b"second-blow5")
    first_index = Path(f"{first}.idx")
    second_index = Path(f"{second}.idx")
    first_index.write_bytes(b"first-index")
    second_index.write_bytes(b"second-index")
    routing = tmp_path / "routing.json"
    routing.write_text(
        json.dumps(
            {
                "read_to_group": {"read-1": first_fingerprint, "read-2": second_fingerprint},
                "groups": {
                    first_fingerprint: {
                        "blow5": first.name,
                        "index": first_index.name,
                    },
                    second_fingerprint: {
                        "blow5": second.name,
                        "index": second_index.name,
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    artifacts = [
        _artifact(first, "blow5", partition_fingerprint=first_fingerprint),
        _artifact(first_index, "blow5_index", partition_fingerprint=first_fingerprint),
        _artifact(second, "blow5", partition_fingerprint=second_fingerprint),
        _artifact(second_index, "blow5_index", partition_fingerprint=second_fingerprint),
        _artifact(routing, "read_routing"),
    ]
    manifest = {"artifacts": artifacts}
    representation = SimpleNamespace(
        format="blow5",
        state="ready",
        validation_receipts={"adjacent_index": True},
        artifact_manifest=manifest,
        manifest_sha256=hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest(),
    )
    return representation, first, first_index, second, second_index, routing


def test_worker_resolves_only_selected_governed_routing_partitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    representation, first, first_index, second, second_index, routing = _partitioned_representation(tmp_path)
    hashed_paths: list[Path] = []

    def stable_identity(path: Path) -> tuple[str, int]:
        hashed_paths.append(path)
        return _sha(path), path.stat().st_size

    monkeypatch.setattr(
        OntSignalWorker,
        "_stable_file_identity",
        staticmethod(stable_identity),
    )

    selected, identities = OntSignalWorker._resolve_selected_raw_partitions(
        representation, ["read-2"]
    )

    assert selected == [(second, second_index)]
    assert identities["routing_sha256"] == _sha(routing)
    assert set(hashed_paths) == {routing, second, second_index}
    assert first not in hashed_paths
    assert first_index not in hashed_paths
    assert identities["blow5"] == [
        {
            "sha256": _sha(second),
            "index_sha256": _sha(second_index),
        }
    ]


def test_worker_move_raw_bundle_includes_every_adjacent_index(tmp_path: Path) -> None:
    representation, first, first_index, second, second_index, _routing = _partitioned_representation(tmp_path)

    selected, _identities = OntSignalWorker._resolve_selected_raw_partitions(representation, None)

    assert selected == [(first, first_index), (second, second_index)]
    assert all(index == Path(f"{blow5}.idx") for blow5, index in selected)


def test_runtime_inventory_routes_each_read_to_its_exact_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.blow5"
    second = tmp_path / "second.blow5"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    inventories = {
        str(first): ["read-1", "read-3"],
        str(second): ["read-2"],
    }

    class FakeSlow5:
        def __init__(self, path: str, _mode: str) -> None:
            self.path = path

        def get_read_ids(self) -> tuple[list[str], int]:
            read_ids = inventories[self.path]
            return read_ids, len(read_ids)

        def close(self) -> None:
            return None

    monkeypatch.setattr(runtime.pyslow5, "Open", FakeSlow5)

    read_ids, identities, partitions = runtime.blow5_ids([first, second])

    assert read_ids == ["read-1", "read-2", "read-3"]
    assert identities == {str(first): _sha(first), str(second): _sha(second)}
    assert partitions == {
        "read-1": first,
        "read-2": second,
        "read-3": first,
    }


def test_worker_rejects_hash_contract_mismatch() -> None:
    with pytest.raises(RuntimeError, match="immutable parent hash contract"):
        OntSignalWorker._require_hash_contract(
            "mapping",
            {"mapping_sha256": "a" * 64},
            {"mapping_sha256": "b" * 64},
        )


def test_move_evidence_accepts_exact_partial_final_block_rule() -> None:
    record = SimpleNamespace(
        query_name="read-1",
        query_sequence="ACGT",
        get_tags=lambda: [("mv", [6, 1, 1, 1, 1]), ("ts", 3), ("ns", 28)],
    )

    evidence = runtime.validate_move_record(
        record,
        raw_signal_samples=28,
        molecule_type="dna",
        basecall_model_id="dna_r10.4.1_e8.2_400bps_sup@v4.3.0",
    )

    assert evidence == {"stride": 6, "move_count": 4, "final_block_remainder": 1}


@pytest.mark.parametrize(
    ("moves", "sequence", "ns", "raw_samples", "message"),
    [
        ([6, 1, 0, 1], "ACGT", 22, 22, "query sequence"),
        ([6, 1, 2, 1, 0], "ACGT", 28, 28, "legal 0/1"),
        ([0, 1, 1, 1, 1], "ACGT", 4, 4, "stride"),
        ([6, 1, 1, 1, 1], "ACGT", 29, 28, "raw-signal sample length"),
    ],
)
def test_move_evidence_rejects_malformed_tags(
    moves: list[int], sequence: str, ns: int, raw_samples: int, message: str
) -> None:
    record = SimpleNamespace(
        query_name="read-1",
        query_sequence=sequence,
        get_tags=lambda: [("mv", moves), ("ts", 3), ("ns", ns)],
    )

    with pytest.raises(ValueError, match=message):
        runtime.validate_move_record(
            record,
            raw_signal_samples=raw_samples,
            molecule_type="dna",
            basecall_model_id="dna_r10.4.1_e8.2_400bps_sup@v4.3.0",
        )


def test_move_evidence_rejects_model_molecule_mismatch() -> None:
    record = SimpleNamespace(
        query_name="read-1",
        query_sequence="ACGT",
        get_tags=lambda: [("mv", [6, 1, 1, 1, 1]), ("ts", 0), ("ns", 24)],
    )
    with pytest.raises(ValueError, match="model/molecule"):
        runtime.validate_move_record(
            record,
            raw_signal_samples=24,
            molecule_type="rna",
            basecall_model_id="dna_r10.4.1_e8.2_400bps_sup@v4.3.0",
        )


def test_rna_calibration_orientation_and_upstream_flag_are_bound() -> None:
    assert runtime.calibration_sequence("ACGT", "dna") == "ACGT"
    assert runtime.calibration_sequence("ACGT", "rna") == "TGCA"
    assert runtime.calibration_sequence("ACGU", "rna") == "TGCA"

    command = runtime.calculate_offsets_command(
        Path("baseline.paf"),
        Path("sample.fasta"),
        Path("sample.blow5"),
        100,
        "rna",
    )
    assert command[-1] == "--rna"
    assert "--rna" not in runtime.calculate_offsets_command(
        Path("baseline.paf"),
        Path("sample.fasta"),
        Path("sample.blow5"),
        100,
        "dna",
    )


def test_calibration_fasta_index_is_removed_as_ephemeral_output(tmp_path: Path) -> None:
    fasta = tmp_path / "sample.fasta"
    fasta.write_text(">read\nACGT\n", encoding="utf-8")
    index = tmp_path / "sample.fasta.fai"
    index.write_text("read\t4\t6\t4\t5\n", encoding="utf-8")

    runtime.remove_calibration_fasta_index(fasta)

    assert not index.exists()


def test_calibration_fasta_index_cleanup_rejects_symlink(tmp_path: Path) -> None:
    fasta = tmp_path / "sample.fasta"
    fasta.write_text(">read\nACGT\n", encoding="utf-8")
    target = tmp_path / "outside.fai"
    target.write_text("authority", encoding="utf-8")
    index = tmp_path / "sample.fasta.fai"
    index.symlink_to(target)

    with pytest.raises(ValueError, match="unsafe calibration FASTA index"):
        runtime.remove_calibration_fasta_index(fasta)

    assert target.read_text(encoding="utf-8") == "authority"


def test_lease_recovery_receipt_preserves_legacy_and_appends() -> None:
    recovered_at = datetime(2026, 8, 24, 18, 31, 10)
    legacy = {"expired_attempt": 1, "max_attempts": 3, "recovered_at": "legacy"}

    receipts = worker_module._append_lease_recovery_receipt(
        {
            "request_identity_sha256": "9" * 64,
            "lease_recovery": legacy,
        },
        expired_attempt=2,
        recovered_at=recovered_at,
        max_attempts=3,
    )

    assert receipts["lease_recovery"] == legacy
    assert receipts["lease_recoveries"] == [
        {
            "expired_attempt": 2,
            "max_attempts": 3,
            "recovered_at": recovered_at.isoformat(),
        }
    ]


def test_worker_managed_output_root_is_retained_parent_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "results" / "ont_signal_workbench"
    monkeypatch.setattr(worker_module, "get_allowed_roots", lambda: {})
    monkeypatch.setattr(worker_module, "get_results_dir", lambda: tmp_path / "results")
    monkeypatch.delenv(
        worker_module.ont_signal_workbench.EXTERNAL_MOVE_BAM_ROOT_ENV,
        raising=False,
    )
    monkeypatch.setenv(
        worker_module.ont_raw_signal.BLOW5_STAGING_ROOT_ENV,
        str(tmp_path / "raw" / "representations"),
    )

    assert output_root in OntSignalWorker._governed_parent_roots()


@pytest.mark.asyncio
async def test_view_processing_uses_governed_parent_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    governed_roots = (tmp_path / "managed",)
    captured: dict[str, object] = {}

    class FakeParents:
        def __init__(self, roots: tuple[Path, ...]) -> None:
            captured["roots"] = roots

        def __enter__(self) -> "FakeParents":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    async def fake_process_retained(
        _item_id: str, _token: str, parents: FakeParents
    ) -> None:
        captured["parents"] = parents

    worker = OntSignalWorker(None, None)
    monkeypatch.setattr(worker, "_governed_parent_roots", lambda: governed_roots)
    monkeypatch.setattr(worker_module, "RetainedParentSet", FakeParents)
    monkeypatch.setattr(worker, "_process_view_retained", fake_process_retained)

    await worker._process_view("view-1", "token-1")

    assert captured["roots"] == governed_roots
    assert isinstance(captured["parents"], FakeParents)


def test_legacy_bam_model_provenance_requires_exact_exhaustive_read_groups() -> None:
    model = "dna_r10.4.1_e8.2_400bps_sup@v4.3.0"
    header = {
        "RG": [
            {"ID": "rg-1", "DS": f"basecall_model={model}"},
            {"ID": "rg-2", "PM": f"model={model}"},
        ]
    }

    model_id, models_by_read_group = runtime.models_from_header(header)

    assert model_id == model
    assert models_by_read_group == {"rg-1": model, "rg-2": model}
    runtime.require_record_read_group(
        SimpleNamespace(query_name="read-1", has_tag=lambda tag: tag == "RG", get_tag=lambda _tag: "rg-1"),
        models_by_read_group,
        model_id,
    )
    for invalid_header in (
        {"RG": [{"ID": "rg-1", "DS": f"basecall_model={model}"}, {"ID": "rg-2", "DS": "runid=one"}]},
        {"RG": [{"ID": "rg-1", "DS": f"basecall_model={model}"}, {"ID": "rg-2", "DS": "basecall_model=dna_other@v1"}]},
        {"RG": [{"ID": "rg-1", "DS": f"basecall_model={model} model=dna_other@v1"}]},
        {"RG": [{"ID": "rg-1", "DS": f"basecall_model={model}"}, {"ID": "rg-1", "DS": f"basecall_model={model}"}]},
        {"RG": [{"DS": f"basecall_model={model}"}]},
    ):
        with pytest.raises(ValueError, match="@RG"):
            runtime.models_from_header(invalid_header)
    for invalid_record in (
        SimpleNamespace(query_name="missing", has_tag=lambda _tag: False, get_tag=lambda _tag: None),
        SimpleNamespace(query_name="unbound", has_tag=lambda tag: tag == "RG", get_tag=lambda _tag: "rg-missing"),
    ):
        with pytest.raises(ValueError, match="read group"):
            runtime.require_record_read_group(invalid_record, models_by_read_group, model_id)


def _valid_reform_line() -> str:
    return "read-1\t9\t3\t9\t+\tread-1\t2\t0\t2\t2\t2\t255\tss:Z:3,3,\n"


def _reform_authority() -> dict[str, dict[str, Any]]:
    return {
        "read-1": runtime.reform_coordinate_authority(
            sequence_length=3,
            ts=0,
            ns=9,
            moves=[3, 1, 1, 1],
            kmer_length=2,
            signal_move_offset=1,
        )
    }


def _realign_parent_reform(
    query_length: int,
    query_start: int,
    query_end: int,
    ss: str,
) -> dict[str, dict[str, Any]]:
    return {
        "read-1": {
            "signal_length": query_length,
            "signal_start": query_start,
            "signal_end": query_end,
            "sequence_length": len(ss.rstrip(",").split(",")),
            "ss": ss,
        }
    }


def test_paf_validator_accepts_exact_reform_inventory(tmp_path: Path) -> None:
    paf = tmp_path / "reform.paf"
    paf.write_text(_valid_reform_line(), encoding="utf-8")

    result = runtime.validate_paf(
        paf,
        (b"ss:Z:",),
        {"read-1"},
        molecule_type="dna",
        paf_kind="reform",
        reform_authority=_reform_authority(),
    )

    assert result["record_count"] == 1
    assert result["read_ids"] == ["read-1"]


def test_paf_receipt_omits_full_read_inventory() -> None:
    validation = {
        "record_count": 60_784,
        "read_ids": [f"read-{index:05d}" for index in range(60_784)],
        "read_inventory_sha256": "a" * 64,
    }

    receipt = runtime.paf_receipt(validation)

    assert receipt == {
        "record_count": 60_784,
        "read_inventory_sha256": "a" * 64,
    }
    assert "read_ids" not in receipt
    assert len(runtime.canonical(receipt)) < 1024 * 1024


def test_pileup_uses_only_pinned_supported_mode_specific_flags() -> None:
    command = ["squigualiser", "plot_pileup"]
    runtime.apply_mode_specific_render_options(
        command,
        mode="pileup",
        fixed_width=True,
        base_width=12,
        loose_bound=False,
        show_samples=True,
    )

    assert command == [
        "squigualiser",
        "plot_pileup",
        "--base_width",
        "12",
        "--plot_num_samples",
    ]
    assert "--fixed_width" not in command
    assert "--no_samples" not in command


def test_render_command_binds_approved_kmer_length_and_rna_mode() -> None:
    command = ["squigualiser", "plot"]
    runtime.apply_mapping_render_options(
        command,
        kmer_length=2,
        molecule_type="rna",
    )

    assert command == [
        "squigualiser",
        "plot",
        "--kmer_length",
        "2",
        "--rna",
    ]


def test_scaledpa_rendering_is_rejected_without_exact_sc_sh_authority() -> None:
    with pytest.raises(ValueError, match="sc/sh authority"):
        runtime.validate_render_scale("scaledpA")
    for scale in ("none", "medmad", "znorm"):
        runtime.validate_render_scale(scale)


@pytest.mark.parametrize(
    "line",
    [
        "read-1\tx\t3\t9\t+\tread-1\t2\t0\t2\t2\t2\t255\tss:Z:3,3,\n",
        "read-1\t9\t3\t10\t+\tread-1\t2\t0\t2\t2\t2\t255\tss:Z:3,3,\n",
        "read-1\t9\t3\t9\t?\tread-1\t2\t0\t2\t2\t2\t255\tss:Z:3,3,\n",
        "read-1\t9\t3\t9\t+\tread-1\t2\t0\t2\t2\t2\t255\tss:Z:2,3,\n",
        "read-1\t9\t3\t9\t+\tread-1\t2\t0\t2\t1\t2\t255\tss:Z:3,3,\n",
    ],
)
def test_paf_validator_rejects_malformed_numeric_coordinate_strand_span_or_ss(
    tmp_path: Path, line: str
) -> None:
    paf = tmp_path / "bad.paf"
    paf.write_text(line, encoding="utf-8")
    with pytest.raises(ValueError):
        runtime.validate_paf(
            paf,
            (b"ss:Z:",),
            {"read-1"},
            molecule_type="dna",
            paf_kind="reform",
            reform_authority=_reform_authority(),
        )


def test_reform_validator_rejects_shifted_or_truncated_parent_binding(tmp_path: Path) -> None:
    authority = _reform_authority()
    for index, line in enumerate((
        "read-1\t10\t4\t10\t+\tread-1\t2\t0\t2\t2\t2\t255\tss:Z:3,3,\n",
        "read-1\t9\t3\t6\t+\tread-1\t1\t0\t1\t1\t1\t255\tss:Z:3,\n",
    )):
        paf = tmp_path / f"shifted-{index}.paf"
        paf.write_text(line, encoding="utf-8")
        with pytest.raises(ValueError, match="authority"):
            runtime.validate_paf(
                paf,
                (b"ss:Z:",),
                {"read-1"},
                molecule_type="dna",
                paf_kind="reform",
                reform_authority=authority,
            )


def test_reform_validator_rejects_redistributed_per_kmer_durations(tmp_path: Path) -> None:
    paf = tmp_path / "redistributed-reform.paf"
    paf.write_text(
        "read-1\t9\t3\t9\t+\tread-1\t2\t0\t2\t2\t2\t255\tss:Z:2,4,\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="per-k-mer"):
        runtime.validate_paf(
            paf,
            (b"ss:Z:",),
            {"read-1"},
            molecule_type="dna",
            paf_kind="reform",
            reform_authority=_reform_authority(),
        )


def test_realign_validator_checks_primary_bam_coordinate_authority(tmp_path: Path) -> None:
    paf = tmp_path / "realign.paf"
    paf.write_text(
        "read-1\t12\t0\t12\t-\tchr1\t2\t10\t12\t2\t2\t255\tss:Z:6,6,\n",
        encoding="utf-8",
    )
    authority = {
        "read-1": {
            "contig": "chr1",
            "strand": "-",
            "reference_start": 10,
            "reference_end": 12,
            "reference_span": 2,
            "cigar": [[0, 2]],
        }
    }
    runtime.validate_paf(
        paf,
        (b"ss:Z:",),
        {"read-1"},
        molecule_type="dna",
        paf_kind="realign",
        reference_lengths={"chr1": 100},
        alignment_authority=authority,
        reform_authority=_realign_parent_reform(12, 0, 12, "6,6,"),
    )

    authority["read-1"]["reference_start"] = 11
    with pytest.raises(ValueError, match="parent reform"):
        runtime.validate_paf(
            paf,
            (b"ss:Z:",),
            {"read-1"},
            molecule_type="dna",
            paf_kind="realign",
            reference_lengths={"chr1": 100},
            alignment_authority=authority,
            reform_authority=_realign_parent_reform(12, 0, 12, "6,6,"),
        )


def test_realign_validator_clips_cigar_topology_to_pinned_emitted_span(
    tmp_path: Path,
) -> None:
    paf = tmp_path / "realign-terminal-trim.paf"
    paf.write_text(
        "read-1\t6\t0\t6\t+\tchr1\t1\t10\t11\t1\t1\t255\tss:Z:6,\n",
        encoding="utf-8",
    )
    runtime.validate_paf(
        paf,
        (b"ss:Z:",),
        {"read-1"},
        molecule_type="dna",
        paf_kind="realign",
        reference_lengths={"chr1": 100},
        alignment_authority={
            "read-1": {
                "contig": "chr1",
                "strand": "+",
                "reference_start": 10,
                "reference_end": 12,
                "cigar": [[0, 2]],
            }
        },
        reform_authority=_realign_parent_reform(6, 0, 6, "6,"),
    )


def test_realign_validator_binds_parent_reform_coordinates_and_durations(
    tmp_path: Path,
) -> None:
    alignment = {
        "read-1": {
            "contig": "chr1",
            "strand": "+",
            "reference_start": 10,
            "reference_end": 12,
            "cigar": [[0, 2]],
        }
    }
    invalid_lines = (
        "read-1\t10\t4\t10\t+\tchr1\t2\t10\t12\t2\t2\t255\tss:Z:3,3,\n",
        "read-1\t9\t3\t9\t+\tchr1\t2\t10\t12\t2\t2\t255\tss:Z:2,4,\n",
        "read-1\t9\t3\t6\t+\tchr1\t1\t10\t11\t1\t1\t255\tss:Z:3,\n",
    )
    for index, line in enumerate(invalid_lines):
        paf = tmp_path / f"unbound-realign-{index}.paf"
        paf.write_text(line, encoding="utf-8")
        with pytest.raises(ValueError, match="parent reform"):
            runtime.validate_paf(
                paf,
                (b"ss:Z:",),
                {"read-1"},
                molecule_type="dna",
                paf_kind="realign",
                reference_lengths={"chr1": 100},
                alignment_authority=alignment,
                reform_authority=_reform_authority(),
            )


def test_realign_validator_orients_cigar_topology_for_reverse_alignment(
    tmp_path: Path,
) -> None:
    paf = tmp_path / "realign-reverse.paf"
    paf.write_text(
        "read-1\t18\t0\t18\t-\tchr1\t4\t10\t14\t4\t4\t255\tss:Z:6,6,6,1D\n",
        encoding="utf-8",
    )
    runtime.validate_paf(
        paf,
        (b"ss:Z:",),
        {"read-1"},
        molecule_type="dna",
        paf_kind="realign",
        reference_lengths={"chr1": 100},
        alignment_authority={
            "read-1": {
                "contig": "chr1",
                "strand": "-",
                "reference_start": 10,
                "reference_end": 15,
                "cigar": [[0, 1], [2, 1], [0, 3]],
            }
        },
        reform_authority=_realign_parent_reform(18, 0, 18, "6,6,6,"),
    )


def test_realign_validator_rejects_ss_topology_that_diverges_from_bam_cigar(
    tmp_path: Path,
) -> None:
    paf = tmp_path / "realign-wrong-topology.paf"
    paf.write_text(
        "read-1\t18\t0\t18\t+\tchr1\t3\t10\t13\t3\t3\t255\tss:Z:9,1D9,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="parent reform"):
        runtime.validate_paf(
            paf,
            (b"ss:Z:",),
            {"read-1"},
            molecule_type="dna",
            paf_kind="realign",
            reference_lengths={"chr1": 100},
            alignment_authority={
                "read-1": {
                    "contig": "chr1",
                    "strand": "+",
                    "reference_start": 10,
                    "reference_end": 13,
                    "cigar": [[0, 1], [1, 1], [0, 1], [2, 1]],
                }
            },
            reform_authority=_realign_parent_reform(27, 0, 27, "9,9,9,"),
        )


def test_cigar_topology_skips_pinned_reference_skip_operation() -> None:
    assert runtime._cigar_topology([[0, 1], [3, 5], [0, 1]], "rna") == [("M", 2)]


def test_region_selection_uses_tabix_indexed_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.paf.gz"
    mapping.write_bytes(b"bgzip-placeholder")
    Path(f"{mapping}.tbi").write_bytes(b"index-placeholder")
    calls: list[tuple[str, int, int]] = []

    class FakeTabix:
        def __init__(self, filename: str) -> None:
            assert filename == str(mapping)

        def __enter__(self) -> "FakeTabix":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def fetch(self, contig: str, start: int, end: int):
            calls.append((contig, start, end))
            yield "read-1\t12\t0\t12\t+\tchr1\t2\t9\t11\t2\t2\t255\tss:Z:6,6,"

    monkeypatch.setattr(runtime.pysam, "TabixFile", FakeTabix)

    selected = runtime.reads_overlapping_region(mapping, "chr1:10-20", 5, "forward")

    assert selected == ["read-1"]
    assert calls == [("chr1", 9, 20)]


def test_runtime_run_terminates_on_log_limit() -> None:
    with pytest.raises(RuntimeError, match="log output limit"):
        runtime.run(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 4096)"],
            deadline_seconds=5,
            max_log_bytes=128,
        )


def test_runtime_run_terminates_on_deadline() -> None:
    with pytest.raises(RuntimeError, match="deadline"):
        runtime.run(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            deadline_seconds=0.05,
            max_log_bytes=1024,
        )


def test_render_rejects_every_unexpected_output_file(tmp_path: Path) -> None:
    output = tmp_path / "render"
    output.mkdir()
    report = output / "render_manifest.json"
    (output / "plot.html").write_text("<html><head></head><body>ok</body></html>", encoding="utf-8")
    (output / "unexpected.txt").write_text("not governed", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected output"):
        runtime.safe_render_artifacts(output, report, {})


def test_worker_output_monitor_rejects_total_limit(tmp_path: Path) -> None:
    (tmp_path / "large.bin").write_bytes(b"x" * 129)
    with pytest.raises(OutputLimitExceeded):
        OntSignalWorker._output_tree_size(tmp_path, 128)


def test_container_command_sets_worker_label_and_finite_fsize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    broker = tmp_path / "broker"
    broker.mkdir(mode=0o700)
    digest = worker_module.APPROVED_OCI_DIGEST.removeprefix("sha256:")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE", f"sha256:{digest}")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE_DIGEST", digest)
    monkeypatch.setenv("BMS_CONTAINER_RUNTIME", "podman")
    monkeypatch.setattr(
        worker_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=f"sha256:{digest}\n", stderr=""
        ),
    )
    worker = OntSignalWorker(None, None)

    command = worker._container_command(
        output,
        broker,
        kind="mapping",
    )

    assert "io.biomodstack.owner=ont-signal-worker" in command
    assert "--pull=never" in command
    assert "--ulimit" in command
    assert any(value.startswith("fsize=") for value in command)
    assert "--rm" not in command
    assert "type=bind,src=" + str(output) + ",dst=/output" in command
    assert "type=bind,src=" + str(broker) + ",dst=/broker" in command
    assert not any("/proc/" in value and "/fd/" in value for value in command)


def test_container_command_fails_closed_when_approved_image_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    broker = tmp_path / "broker"
    broker.mkdir(mode=0o700)
    digest = worker_module.APPROVED_OCI_DIGEST.removeprefix("sha256:")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE", f"sha256:{digest}")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE_DIGEST", digest)
    monkeypatch.setenv("BMS_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(
        worker_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="image is absent"
        ),
    )

    with pytest.raises(RuntimeError, match="local approved Squigualiser image"):
        OntSignalWorker(None, None)._container_command(output, broker, kind="mapping")


def test_squigualiser_build_script_rejects_image_id_outside_runtime_policy(tmp_path: Path) -> None:
    runtime = tmp_path / "docker"
    runtime.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "if sys.argv[1] == 'build': raise SystemExit(0)\n"
        "if sys.argv[1:3] == ['image', 'inspect']:\n"
        "    print(os.environ['FAKE_IMAGE_ID'])\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(64)\n",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    script = Path(__file__).parents[3] / "scripts" / "build_ont_squigualiser_runtime.sh"
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "BMS_CONTAINER_RUNTIME": "docker",
        "FAKE_IMAGE_ID": "sha256:" + "0" * 64,
    }
    result = subprocess.run(
        ["bash", str(script)], env=environment, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "does not match the approved runtime policy" in result.stderr


def test_squigualiser_build_script_accepts_image_id_from_runtime_policy(tmp_path: Path) -> None:
    runtime = tmp_path / "docker"
    runtime.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "if sys.argv[1] == 'build': raise SystemExit(0)\n"
        "if sys.argv[1:3] == ['image', 'inspect']:\n"
        "    print(os.environ['FAKE_IMAGE_ID'])\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(64)\n",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    script = Path(__file__).parents[3] / "scripts" / "build_ont_squigualiser_runtime.sh"
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "BMS_CONTAINER_RUNTIME": "docker",
        "FAKE_IMAGE_ID": worker_module.APPROVED_OCI_DIGEST,
    }
    result = subprocess.run(
        ["bash", str(script)], env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert f"BMS_ONT_SQUIGUALISER_IMAGE={worker_module.APPROVED_OCI_DIGEST}" in result.stdout


def test_retained_parent_keeps_exact_inode_bytes_and_command_never_reopens_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert RetainedParentSet is not None, "retained-parent lifecycle is missing"
    source = tmp_path / "moves.bam"
    original = b"immutable-original-generation"
    source.write_bytes(original)
    expected_sha256 = hashlib.sha256(original).hexdigest()
    parents = RetainedParentSet()
    retained = parents.pin(
        source,
        alias="original_moves.bam",
        expected_sha256=expected_sha256,
        expected_size=len(original),
    )
    replacement = tmp_path / "replacement.bam"
    replacement.write_bytes(b"mutable-replacement")
    source.unlink()
    source.symlink_to(replacement)

    assert os.pread(retained.fd, len(original), 0) == original
    assert retained.sha256 == expected_sha256
    assert retained.size_bytes == len(original)

    output = tmp_path / "output-retained"
    broker = tmp_path / "broker-retained"
    output.mkdir()
    broker.mkdir(mode=0o700)
    digest = worker_module.APPROVED_OCI_DIGEST.removeprefix("sha256:")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE", f"sha256:{digest}")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE_DIGEST", digest)
    monkeypatch.setenv("BMS_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(
        worker_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=f"sha256:{digest}\n", stderr=""
        ),
    )
    command = OntSignalWorker(None, None)._container_command(
        output, broker, kind="mapping"
    )
    rendered = "\0".join(command)
    assert str(source) not in rendered
    assert str(replacement) not in rendered
    assert "python3\0/opt/bms/ont_signal_runtime.py\0broker" in rendered
    parents.close()
    with pytest.raises(OSError):
        os.fstat(retained.fd)


@pytest.mark.parametrize("terminal", [None, RuntimeError("failed"), asyncio.CancelledError()])
def test_retained_parent_descriptors_close_on_every_terminal_exit(
    tmp_path: Path, terminal: BaseException | None
) -> None:
    assert RetainedParentSet is not None, "retained-parent lifecycle is missing"
    source = tmp_path / "parent.bin"
    source.write_bytes(b"parent")
    descriptor = -1
    with pytest.raises(type(terminal)) if terminal is not None else _does_not_raise():
        with RetainedParentSet() as parents:
            retained = parents.pin(
                source,
                alias="parent.bin",
                expected_sha256=hashlib.sha256(b"parent").hexdigest(),
                expected_size=6,
            )
            descriptor = retained.fd
            if terminal is not None:
                raise terminal
    with pytest.raises(OSError):
        os.fstat(descriptor)


class _does_not_raise:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: Any) -> bool:
        return False


def _send_rights(sock: socket.socket, metadata: dict[str, Any], fds: list[int]) -> None:
    ancillary = [] if not fds else [
        (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds).tobytes())
    ]
    sock.sendmsg([json.dumps(metadata).encode()], ancillary)


def test_runtime_broker_receives_exact_retained_generation_and_builds_private_aliases(
    tmp_path: Path
) -> None:
    receiver = getattr(runtime, "receive_fd_request", None)
    assert receiver is not None, "SCM_RIGHTS broker receiver is missing"
    source = tmp_path / "source.bam"
    original = b"descriptor-generation"
    source.write_bytes(original)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        metadata = {
            "schema": "bms.ont-signal-fd-broker.v1",
            "operation_argv": ["reform", "--original-bam", "/parents/source.bam"],
            "parents": [{
                "alias": "source.bam",
                "sha256": hashlib.sha256(original).hexdigest(),
                "size_bytes": len(original),
            }],
        }
        source.unlink()
        source.symlink_to(tmp_path / "unrelated")
        _send_rights(left, metadata, [descriptor])
        with receiver(right, timeout_seconds=0.2) as request:
            assert request.operation_argv[0] == "reform"
            assert request.read_alias("source.bam") == original
            request.verify_aliases()
            alias = request.alias_path("source.bam")
            assert os.readlink(alias) == f"/proc/self/fd/{request.fds[0]}"
    finally:
        left.close()
        right.close()
        os.close(descriptor)


@pytest.mark.parametrize("fd_delta", [-1, 1])
def test_runtime_broker_rejects_missing_or_extra_descriptors(
    tmp_path: Path, fd_delta: int
) -> None:
    receiver = getattr(runtime, "receive_fd_request", None)
    assert receiver is not None, "SCM_RIGHTS broker receiver is missing"
    paths = [tmp_path / "one", tmp_path / "two"]
    for index, path in enumerate(paths):
        path.write_bytes(f"parent-{index}".encode())
    descriptors = [os.open(path, os.O_RDONLY | os.O_NOFOLLOW) for path in paths]
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        metadata = {
            "schema": "bms.ont-signal-fd-broker.v1",
            "operation_argv": ["reform"],
            "parents": [{
                "alias": "one",
                "sha256": _sha(paths[0]),
                "size_bytes": paths[0].stat().st_size,
            }],
        }
        supplied = [] if fd_delta < 0 else descriptors
        _send_rights(left, metadata, supplied)
        with pytest.raises(ValueError, match="descriptor count"):
            with receiver(right, timeout_seconds=0.2):
                pass
    finally:
        left.close()
        right.close()
        for descriptor in descriptors:
            os.close(descriptor)


def test_runtime_broker_rejects_digest_mismatch_and_alias_substitution(tmp_path: Path) -> None:
    receiver = getattr(runtime, "receive_fd_request", None)
    assert receiver is not None, "SCM_RIGHTS broker receiver is missing"
    source = tmp_path / "parent"
    source.write_bytes(b"parent")
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)

    def metadata(digest: str) -> dict[str, Any]:
        return {
            "schema": "bms.ont-signal-fd-broker.v1",
            "operation_argv": ["reform"],
            "parents": [{"alias": "parent", "sha256": digest, "size_bytes": 6}],
        }

    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        _send_rights(left, metadata("0" * 64), [descriptor])
        with pytest.raises(ValueError, match="digest"):
            with receiver(right, timeout_seconds=0.2):
                pass
    finally:
        left.close(); right.close()

    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        _send_rights(left, metadata(_sha(source)), [descriptor])
        with receiver(right, timeout_seconds=0.2) as request:
            alias = request.alias_path("parent")
            alias.unlink()
            alias.symlink_to("/dev/null")
            with pytest.raises(ValueError, match="alias"):
                request.verify_aliases()
    finally:
        left.close(); right.close(); os.close(descriptor)


def test_runtime_broker_socket_timeout_fails_closed() -> None:
    receiver = getattr(runtime, "receive_fd_request", None)
    assert receiver is not None, "SCM_RIGHTS broker receiver is missing"
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(TimeoutError, match="broker"):
            with receiver(right, timeout_seconds=0.01):
                pass
    finally:
        left.close(); right.close()


def test_runtime_broker_retains_parent_fds_across_trusted_child_exec(tmp_path: Path) -> None:
    source = tmp_path / "child-parent"
    payload = b"child-exact-generation"
    source.write_bytes(payload)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    metadata = {
        "schema": "bms.ont-signal-fd-broker.v1",
        "operation_argv": ["reform"],
        "parents": [{
            "alias": "child-parent",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }],
    }
    try:
        _send_rights(left, metadata, [descriptor])
        with runtime.receive_fd_request(right, timeout_seconds=0.2) as request:
            runtime._ACTIVE_BROKER_REQUEST = request
            try:
                receipt = runtime.run([
                    sys.executable,
                    "-c",
                    "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
                    str(request.alias_path("child-parent")),
                ])
            finally:
                runtime._ACTIVE_BROKER_REQUEST = None
        assert receipt["stdout_tail"] == payload.decode()
    finally:
        left.close(); right.close(); os.close(descriptor)


def test_runtime_identity_is_bound_to_the_staged_approved_oci_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = worker_module.APPROVED_OCI_DIGEST.removeprefix("sha256:")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE", f"sha256:{approved}")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE_DIGEST", approved)

    identity = OntSignalWorker._runtime_identity()

    assert identity["image"] == f"sha256:{approved}"
    assert identity["image_digest"] == approved
    assert identity["upstream_version"] == "0.7.0"
    assert identity["upstream_commit"] == "5a2404f1f43bc3227a85475c59b2b77970078b2e"
    assert len(identity["policy_manifest_sha256"]) == 64

    arbitrary = "d" * 64
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE", f"sha256:{arbitrary}")
    monkeypatch.setenv("BMS_ONT_SQUIGUALISER_IMAGE_DIGEST", arbitrary)
    with pytest.raises(RuntimeError, match="approved runtime policy"):
        OntSignalWorker._runtime_identity()


class _FailedCleanupProcess:
    returncode = 19

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"cleanup denied"

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_cleanup_failure_is_fatal_and_preserves_retryable_container_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = OntSignalWorker(None, None)
    worker._active_container = ("podman", "bms-ont-signal-mapping-deadbeef")

    async def fake_create(*_args: Any, **_kwargs: Any) -> _FailedCleanupProcess:
        return _FailedCleanupProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    with pytest.raises(ContainerCleanupError, match="cleanup denied"):
        await worker._remove_active_container()
    assert worker._active_container == ("podman", "bms-ont-signal-mapping-deadbeef")
    assert worker._stop.is_set()


FenceBase = declarative_base()


class FenceRow(FenceBase):
    __tablename__ = "fence_rows"

    id = Column(String, primary_key=True)
    state = Column(String, nullable=False)
    reason_code = Column(String, nullable=False)
    claim_token = Column(String, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    cancel_requested_at = Column(DateTime, nullable=True)
    failure_code = Column(String, nullable=True)
    failure_message = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


@pytest.mark.asyncio
async def test_expired_lease_recovery_is_cas_fenced_against_concurrent_renewal(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recover-fence.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(FenceBase.metadata.create_all)
    ordinary_factory = async_sessionmaker(engine, expire_on_commit=False)
    observed_expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    renewed_expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
    async with ordinary_factory() as session:
        session.add(
            FenceRow(
                id="job-recovered-cas",
                state="running",
                reason_code="worker_claimed",
                claim_token="claim-original",
                lease_expires_at=observed_expiry,
            )
        )
        await session.commit()

    raced = False

    class RacingSession(AsyncSession):
        async def execute(self, statement: Any, *args: Any, **kwargs: Any):
            nonlocal raced
            result = await super().execute(statement, *args, **kwargs)
            if not raced and getattr(statement, "is_select", False):
                raced = True
                async with ordinary_factory() as competitor:
                    await competitor.execute(
                        update(FenceRow)
                        .where(
                            FenceRow.id == "job-recovered-cas",
                            FenceRow.state == "running",
                            FenceRow.claim_token == "claim-original",
                            FenceRow.lease_expires_at == observed_expiry,
                        )
                        .values(lease_expires_at=renewed_expiry)
                    )
                    await competitor.commit()
            return result

    racing_factory = async_sessionmaker(
        engine, class_=RacingSession, expire_on_commit=False
    )
    worker = OntSignalWorker(racing_factory, racing_factory)

    await worker._recover_expired_table(FenceRow, "state", datetime.now(UTC).replace(tzinfo=None))

    async with ordinary_factory() as session:
        row = await session.get(FenceRow, "job-recovered-cas")
        assert row is not None
        assert row.state == "running"
        assert row.claim_token == "claim-original"
        assert row.lease_expires_at == renewed_expiry
    await engine.dispose()


@pytest.mark.asyncio
async def test_late_cancellation_fences_failure_publication(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fence.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(FenceBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(tzinfo=None)
    async with factory() as session:
        session.add(
            FenceRow(
                id="job-1",
                state="running",
                reason_code="cancellation_requested",
                claim_token="claim-1",
                lease_expires_at=now + timedelta(minutes=5),
                cancel_requested_at=now,
            )
        )
        await session.commit()

    worker = OntSignalWorker(factory, factory)
    await worker._fail(FenceRow, "state", "job-1", "claim-1", RuntimeError("late failure"))

    async with factory() as session:
        row = await session.get(FenceRow, "job-1")
        assert row is not None
        assert row.state == "cancelled"
        assert row.reason_code == "cancelled"
        assert row.claim_token is None
        assert row.failure_code is None
        assert row.completed_at is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_cancel_requested_recovery_publishes_terminal_completed_at(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recover-cancel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(FenceBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(tzinfo=None)
    async with factory() as session:
        session.add(
            FenceRow(
                id="job-expired-cancel",
                state="running",
                reason_code="cancellation_requested",
                claim_token="claim-expired",
                lease_expires_at=now - timedelta(minutes=1),
                cancel_requested_at=now - timedelta(minutes=2),
            )
        )
        await session.commit()

    worker = OntSignalWorker(factory, factory)
    await worker._recover_expired_table(FenceRow, "state", now)

    async with factory() as session:
        row = await session.get(FenceRow, "job-expired-cancel")
        assert row is not None
        assert row.state == "cancelled"
        assert row.reason_code == "cancelled_after_expired_lease"
        assert row.completed_at == now
    await engine.dispose()


def test_broker_governed_adjacent_blow5_index_alias_is_accepted(tmp_path: Path) -> None:
    blow5 = tmp_path / "raw.blow5"
    index = tmp_path / "raw.blow5.idx"
    blow5.write_bytes(b"raw-parent")
    index.write_bytes(b"index-parent")
    fds = [os.open(path, os.O_RDONLY | os.O_NOFOLLOW) for path in (blow5, index)]
    metadata = {
        "operation_argv": ["select-region"],
        "parents": [
            {"alias": "raw.blow5", "sha256": _sha(blow5), "size_bytes": blow5.stat().st_size},
            {"alias": "raw.blow5.idx", "sha256": _sha(index), "size_bytes": index.stat().st_size},
        ],
    }
    request = runtime.BrokerRequest(metadata, fds)
    previous = runtime._ACTIVE_BROKER_REQUEST
    runtime._ACTIVE_BROKER_REQUEST = request
    try:
        assert runtime.raw_parent_hashes([request.alias_path("raw.blow5")]) == [{
            "sha256": _sha(blow5),
            "index_sha256": _sha(index),
        }]
    finally:
        runtime._ACTIVE_BROKER_REQUEST = previous
        request.close()


def test_worker_uses_short_private_broker_socket_path_for_deep_output_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deep_parent = tmp_path
    for index in range(6):
        deep_parent = deep_parent / (f"deep-{index}-" + "x" * 24)
    output_dir = deep_parent / "output"
    output_dir.mkdir(parents=True)
    worker = OntSignalWorker(None, None)
    seen: dict[str, Path] = {}

    def fake_container_command(output: Path, broker: Path, *, kind: str) -> list[str]:
        seen["broker"] = broker
        assert len(str(broker / "parents.sock").encode()) < 108
        return ["runtime", "run"]

    async def fake_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(worker, "_container_command", fake_container_command)
    monkeypatch.setattr(worker, "_execute", fake_execute)
    result = asyncio.run(
        worker._invoke(
            object(),  # type: ignore[arg-type]
            ["select-region"],
            "view",
            "item",
            "claim",
            output_dir,
        )
    )

    assert result == {"ok": True}
    assert seen["broker"].parent == Path("/tmp")
    assert not seen["broker"].exists()
