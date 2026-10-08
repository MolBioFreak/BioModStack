from __future__ import annotations

from pathlib import Path

from services import nextflow


NEXTFLOW_SOURCE = Path(__file__).resolve().parents[1] / "services" / "nextflow.py"


def test_bounded_log_buffer_retains_useful_tail_by_lines_and_bytes() -> None:
    retained = nextflow.BoundedLogBuffer(max_bytes=24, max_lines=3)
    for line in ["old-1\n", "old-2\n", "diagnostic-a\n", "fatal-tail\n"]:
        retained.append(line)

    assert list(retained) == ["diagnostic-a\n", "fatal-tail\n"]
    assert retained.byte_size <= 24
    assert len(retained) <= 3
    assert retained.tail(1) == ["fatal-tail\n"]


def test_bounded_log_buffer_truncates_one_oversized_utf8_line() -> None:
    retained = nextflow.BoundedLogBuffer(max_bytes=17, max_lines=10)

    retained.append("prefix-" + "é" * 20 + "-fatal\n")

    assert retained.byte_size <= 17
    assert len(retained) == 1
    assert list(retained)[0].endswith("-fatal\n")


def test_incremental_log_read_is_chunk_bounded(tmp_path: Path) -> None:
    log_path = tmp_path / "nextflow.log"
    log_path.write_bytes(b"first-line\n" + b"x" * 50 + b"\nlast-line\n")

    chunk, next_offset = nextflow.read_incremental_log_chunk(log_path, offset=0, max_bytes=16)

    assert chunk == b"first-line\nxxxxx"
    assert next_offset == 16


def test_incremental_log_read_recovers_when_compaction_shrinks_below_offset(tmp_path: Path) -> None:
    log_path = tmp_path / "nextflow.log"
    log_path.write_bytes(b"old\n" * 20)

    chunk, next_offset = nextflow.read_incremental_log_chunk(log_path, offset=10_000, max_bytes=8)

    assert chunk == b""
    assert next_offset == log_path.stat().st_size


def test_nextflow_log_compaction_keeps_tail_and_bounds_file(tmp_path: Path) -> None:
    log_path = tmp_path / "nextflow.log"
    log_path.write_bytes(b"obsolete\n" * 100 + b"CUDA out of memory\nfinal diagnostic\n")

    compacted = nextflow.compact_log_file(log_path, max_bytes=64)

    assert compacted is True
    assert log_path.stat().st_size <= 64
    tail = log_path.read_text(encoding="utf-8")
    assert "CUDA out of memory" in tail
    assert tail.endswith("final diagnostic\n")


def test_nextflow_log_caps_are_configurable_and_enforce_minimum(monkeypatch) -> None:
    monkeypatch.setenv("BMS_NEXTFLOW_LOG_MAX_BYTES", "12345")
    assert nextflow._bounded_env_int("BMS_NEXTFLOW_LOG_MAX_BYTES", 99999, 4096) == 12345

    monkeypatch.setenv("BMS_NEXTFLOW_LOG_MAX_BYTES", "5")
    assert nextflow._bounded_env_int("BMS_NEXTFLOW_LOG_MAX_BYTES", 99999, 4096) == 4096

    monkeypatch.setenv("BMS_NEXTFLOW_LOG_MAX_BYTES", "not-an-int")
    assert nextflow._bounded_env_int("BMS_NEXTFLOW_LOG_MAX_BYTES", 99999, 4096) == 99999


def test_launcher_uses_byte_bounded_buffers_reads_and_active_log_compaction() -> None:
    source = NEXTFLOW_SOURCE.read_text(encoding="utf-8")

    assert "full_log = BoundedLogBuffer(" in source
    assert "attempt_log = BoundedLogBuffer(" in source
    assert "read_incremental_log_chunk(" in source
    assert "compact_log_file(log_path, log_file_max_bytes)" in source
    assert "chunk = reader.read()" not in source
