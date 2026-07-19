from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_construct_topology_evidence.py"


def load_module():
    assert SCRIPT.is_file(), "missing topology-evidence builder"
    spec = importlib.util.spec_from_file_location("build_construct_topology_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_origin_spanning_reads_are_deduplicated() -> None:
    module = load_module()
    sam_rows = [
        "read-a\t0\tref\t501\t60\t500M500S\t*\t0\t0\t*\t*",
        "read-a\t2048\tref\t1\t60\t500S500M\t*\t0\t0\t*\t*",
        "read-b\t0\tref\t506\t60\t495M505S\t*\t0\t0\t*\t*",
        "read-b\t2048\tref\t5\t60\t495S505M\t*\t0\t0\t*\t*",
        "read-c\t0\tref\t300\t60\t100M\t*\t0\t0\t*\t*",
    ]
    evidence = module.derive_topology_evidence(
        reference_length=1000,
        sam_rows=sam_rows,
        breakpoint_rows=[],
        secondary_rows=[{"non_boundary_split_reads": "0", "aligned_dimer_reads": "20"}],
        edge_window_bp=100,
    )
    assert evidence["state"] == "present"
    assert evidence["origin_spanning_reads"] == 2
    assert evidence["mapped_unique_reads"] == 3
    assert evidence["secondary_anomaly_fraction"] == 0.0


def test_topology_provenance_records_actual_samtools_wrapper(tmp_path: Path, monkeypatch) -> None:
    module = load_module()
    reference = tmp_path / "reference.fasta"
    bam = tmp_path / "aligned.bam"
    output = tmp_path / "topology.json"
    reference.write_text(">ref\nACGT\n", encoding="utf-8")
    bam.write_bytes(b"bam")
    captured = {}

    def fake_run(argv, **_kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    prefix = ["apptainer", "exec", "/containers/samtools.sif", "samtools"]
    args = ["--reference-fasta", str(reference), "--alignment-bam", str(bam), "--out", str(output)]
    for part in prefix:
        args.extend(["--samtools-command", part])

    assert module.main(args) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert captured["argv"] == [*prefix, "view", "-F", "260", str(bam)]
    assert payload["provenance"]["samtools_command"] == [
        *prefix,
        "view",
        "-F",
        "260",
        "<alignment_bam>",
    ]


def test_full_length_linear_reads_are_not_circular_origin_spanning_evidence() -> None:
    module = load_module()
    evidence = module.derive_topology_evidence(
        reference_length=1000,
        sam_rows=[
            "linear-a\t0\tref\t1\t60\t1000M\t*\t0\t0\t*\t*",
            "linear-b\t0\tref\t1\t60\t1000M\t*\t0\t0\t*\t*",
        ],
        breakpoint_rows=[],
        secondary_rows=[],
        edge_window_bp=100,
    )

    assert evidence["state"] == "present"
    assert evidence["mapped_unique_reads"] == 2
    assert evidence["origin_spanning_reads"] == 0


def test_split_primary_and_supplementary_segments_support_circular_origin() -> None:
    module = load_module()
    evidence = module.derive_topology_evidence(
        reference_length=1000,
        sam_rows=[
            "wrap-a\t0\tref\t501\t60\t500M500S\t*\t0\t0\t*\t*",
            "wrap-a\t2048\tref\t1\t60\t500S500M\t*\t0\t0\t*\t*",
        ],
        breakpoint_rows=[],
        secondary_rows=[],
        edge_window_bp=100,
    )

    assert evidence["origin_spanning_reads"] == 1


def test_duplicated_query_intervals_are_not_origin_wrap_evidence() -> None:
    module = load_module()
    evidence = module.derive_topology_evidence(
        reference_length=1000,
        sam_rows=[
            "duplicate-a\t0\tref\t1\t60\t100M900S\t*\t0\t0\t*\t*",
            "duplicate-a\t2048\tref\t901\t60\t100M900S\t*\t0\t0\t*\t*",
        ],
        breakpoint_rows=[],
        secondary_rows=[],
        edge_window_bp=100,
    )

    assert evidence["origin_spanning_reads"] == 0


def test_split_segments_must_have_valid_clipping_strand_order_and_mapq() -> None:
    module = load_module()
    invalid_pairs = [
        [
            "low-mapq\t0\tref\t501\t10\t500M500S\t*\t0\t0\t*\t*",
            "low-mapq\t2048\tref\t1\t10\t500S500M\t*\t0\t0\t*\t*",
        ],
        [
            "strand\t0\tref\t501\t60\t500M500S\t*\t0\t0\t*\t*",
            "strand\t2064\tref\t1\t60\t500S500M\t*\t0\t0\t*\t*",
        ],
        [
            "order\t0\tref\t1\t60\t500M500S\t*\t0\t0\t*\t*",
            "order\t2048\tref\t501\t60\t500S500M\t*\t0\t0\t*\t*",
        ],
        [
            "unclipped\t0\tref\t501\t60\t500M\t*\t0\t0\t*\t*",
            "unclipped\t2048\tref\t1\t60\t500S500M\t*\t0\t0\t*\t*",
        ],
    ]

    for sam_rows in invalid_pairs:
        evidence = module.derive_topology_evidence(
            reference_length=1000,
            sam_rows=sam_rows,
            breakpoint_rows=[],
            secondary_rows=[],
            edge_window_bp=100,
        )
        assert evidence["origin_spanning_reads"] == 0


def test_secondary_topology_counts_reject_rounding_and_nonfinite_text() -> None:
    module = load_module()
    for raw in ("2.9", "2.0", "NaN", "Infinity", "-1"):
        try:
            module.derive_topology_evidence(
                reference_length=1000,
                sam_rows=["linear-a\t0\tref\t1\t60\t1000M\t*\t0\t0\t*\t*"],
                breakpoint_rows=[],
                secondary_rows=[{"non_boundary_split_reads": raw, "aligned_dimer_reads": "10"}],
                edge_window_bp=100,
            )
        except ValueError as exc:
            assert "non_boundary_split_reads" in str(exc)
        else:
            raise AssertionError(f"accepted malformed count {raw!r}")


def test_non_boundary_dimer_evidence_is_quantified() -> None:
    module = load_module()
    evidence = module.derive_topology_evidence(
        reference_length=1000,
        sam_rows=["read-a\t0\tref\t1\t60\t1000M\t*\t0\t0\t*\t*"],
        breakpoint_rows=[
            {
                "breakpoint_status": "split_supported",
                "confidence": "high",
                "primary_breakpoint_in_boundary_window": "0",
            }
        ],
        secondary_rows=[{"non_boundary_split_reads": "5", "aligned_dimer_reads": "10"}],
        edge_window_bp=100,
    )
    assert evidence["secondary_anomaly_fraction"] == 0.5
    assert evidence["contradictory_breakpoint_evidence"] is True


def test_missing_alignment_evidence_is_explicitly_unavailable() -> None:
    module = load_module()
    evidence = module.derive_topology_evidence(
        reference_length=1000,
        sam_rows=[],
        breakpoint_rows=[],
        secondary_rows=[],
        edge_window_bp=100,
    )
    assert evidence["state"] == "unavailable"
    assert evidence["reason"] == "NO_MAPPED_ALIGNMENT_EVIDENCE"
