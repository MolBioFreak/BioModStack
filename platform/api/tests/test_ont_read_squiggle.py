from __future__ import annotations

import uuid
from array import array
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pod5
import pysam
import pytest

from services.ont_read_report import OntReadReportError
from services.ont_read_squiggle import list_reads, read_squiggle

READ_ID = "3f1c2b8e-1a2b-4c3d-8e9f-0a1b2c3d4e5f"


def _write_pod5(path: Path, samples: int) -> None:
    signal = (np.arange(samples) % 200).astype(np.int16)
    read = pod5.Read(
        read_id=uuid.UUID(READ_ID),
        end_reason=pod5.EndReason(pod5.EndReasonEnum.SIGNAL_POSITIVE, False),
        calibration=pod5.Calibration(offset=0.0, scale=1.0),
        pore=pod5.Pore(channel=1, well=1, pore_type="r10"),
        read_number=1, start_sample=0, median_before=100.0,
        num_reads_since_mux_change=0, time_since_mux_change=0.0,
        run_info=pod5.RunInfo(
            acquisition_id="a", acquisition_start_time=datetime(2026, 1, 1, tzinfo=timezone.utc), adc_max=2047, adc_min=-2048,
            context_tags={}, experiment_name="t", flow_cell_id="f", flow_cell_product_code="p", protocol_name="p",
            protocol_run_id="r", protocol_start_time=datetime(2026, 1, 1, tzinfo=timezone.utc), sample_id="s", sample_rate=5000,
            sequencing_kit="k", sequencer_position="x", sequencer_position_type="y", software="s", system_name="s",
            system_type="s", tracking_id={},
        ),
        signal=signal,
    )
    with pod5.Writer(path) as writer:
        writer.add_read(read)


def _write_bam(path: Path, sequence: str, moves: list[int], trimmed: int) -> None:
    header = {"HD": {"VN": "1.6"}}
    with pysam.AlignmentFile(str(path), "wb", header=header) as handle:
        read = pysam.AlignedSegment(handle.header)
        read.query_name = READ_ID
        read.query_sequence = sequence
        read.is_unmapped = True
        read.query_qualities = pysam.qualitystring_to_array("5" * len(sequence))
        read.set_tag("qs", 20.0)
        read.set_tag("ts", trimmed)
        read.set_tag("mv", array("b", [6, *moves]))
        read.set_tag("MM", "A+a?,1;")  # second A is called
        read.set_tag("ML", [240])
        handle.write(read)


def test_squiggle_segments_signal_per_base_with_modification_probability(tmp_path: Path) -> None:
    sequence = "CAGAT"
    moves = [1, 0, 1, 1, 0, 0, 1, 1]  # base starts at blocks 0, 2, 3, 6, 7
    trimmed = 12
    pod5_dir = tmp_path / "pod5"
    pod5_dir.mkdir()
    _write_pod5(pod5_dir / "reads.pod5", samples=trimmed + len(moves) * 6 + 30)
    bam = tmp_path / "calls.bam"
    _write_bam(bam, sequence, moves, trimmed)

    listed = list_reads(bam)
    assert listed["total_primary_reads"] == 1
    assert listed["reads"][0]["modified_calls"] == {"A+6mA": 1}

    view = read_squiggle(bam, pod5_dir, READ_ID, start_base=1, bases=10)
    assert view["schema"] == "bms.ngs.read-squiggle.v1"
    assert (view["start_base"], view["end_base"]) == (1, 5)
    assert view["sample_offset"] == trimmed + 2 * 6
    assert [(b["base"], b["sample_start"], b["sample_end"]) for b in view["bases"]] == [
        ("A", 0, 6), ("G", 6, 24), ("A", 24, 30), ("T", 30, 36),
    ]
    assert len(view["signal_pa"]) == 36
    assert view["signal_pa"][0] == float(view["sample_offset"] % 200)
    assert view["bases"][2]["modifications"] == {"A+6mA": round(240.5 / 256, 4)}
    assert view["bases"][0]["modifications"] == {}


def test_squiggle_refuses_reads_without_moves_or_signal(tmp_path: Path) -> None:
    pod5_dir = tmp_path / "pod5"
    pod5_dir.mkdir()
    bam = tmp_path / "calls.bam"
    _write_bam(bam, "CAGAT", [1, 0, 1, 1, 0, 0, 1, 1], 0)
    with pytest.raises(OntReadReportError, match="not in this run's POD5"):
        read_squiggle(bam, pod5_dir, READ_ID)
    with pytest.raises(OntReadReportError, match="not in this run's basecall BAM"):
        read_squiggle(bam, pod5_dir, "00000000-0000-0000-0000-000000000000")
    with pytest.raises(OntReadReportError, match="UUID"):
        read_squiggle(bam, pod5_dir, "../etc/passwd")
    _write_pod5(pod5_dir / "reads.pod5", samples=200)
    _write_bam(bam, "CAG", [1, 0, 1, 1, 0, 0, 1, 1], 0)
    with pytest.raises(OntReadReportError, match="move table does not match"):
        read_squiggle(bam, pod5_dir, READ_ID)
