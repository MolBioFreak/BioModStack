from __future__ import annotations

from pathlib import Path

from services import nextflow


NEXTFLOW_SOURCE = Path(__file__).resolve().parents[1] / "services" / "nextflow.py"


def test_bounded_log_tail_retains_useful_tail_by_lines_and_bytes() -> None:
    retained = nextflow._BoundedLogTail(max_bytes=24, max_lines=3, max_line_chars=100)
    for line in ["old-1\n", "old-2\n", "diagnostic-a\n", "fatal-tail\n"]:
        retained.append(line)

    assert list(retained) == ["diagnostic-a\n", "fatal-tail\n"]
    assert retained.byte_size <= 24
    assert len(retained) <= 3
    assert retained.tail(1) == ["fatal-tail\n"]


def test_bounded_log_tail_truncates_one_oversized_utf8_line() -> None:
    retained = nextflow._BoundedLogTail(max_bytes=32, max_lines=10, max_line_chars=100)

    retained.append("prefix-" + "é" * 20 + "-fatal\n")

    assert retained.byte_size <= 32
    assert len(retained) == 1
    assert list(retained)[0].endswith("-fatal\n")


def test_incremental_log_read_is_chunk_bounded(tmp_path: Path) -> None:
    log_path = tmp_path / "nextflow.log"
    log_path.write_bytes(b"first-line\n" + b"x" * 50 + b"\nlast-line\n")

    chunk, next_offset = nextflow.read_incremental_log_chunk(log_path, offset=0, max_bytes=16)

    assert chunk == b"first-line\nxxxxx"
    assert next_offset == 16


def test_incremental_log_read_does_not_rewind_append_only_offset(tmp_path: Path) -> None:
    log_path = tmp_path / "nextflow.log"
    log_path.write_bytes(b"old\n" * 20)

    chunk, next_offset = nextflow.read_incremental_log_chunk(log_path, offset=10_000, max_bytes=8)

    assert chunk == b""
    assert next_offset == 10_000


def test_incremental_reader_handles_split_utf8_and_final_eof_once(tmp_path: Path) -> None:
    log_path = tmp_path / "nextflow.log"
    expected = "alpha-éclair\nomega-🧬-final"
    log_path.write_bytes(expected.encode("utf-8"))
    reader = nextflow._IncrementalLogReader(
        log_path, offset=0, max_read_bytes=7, max_line_chars=1024
    )

    observed: list[str] = []
    while reader.offset < log_path.stat().st_size:
        observed.extend(reader.read_available())
    observed.extend(reader.read_available(final=True))
    observed.extend(reader.read_available(final=True))

    assert "".join(observed) == expected


def test_multi_megabyte_log_is_read_in_bounded_chunks_without_rewrite(tmp_path: Path) -> None:
    log_path = tmp_path / "nextflow.log"
    expected = (("0123456789abcdef" * 256) + "\n").encode("utf-8") * 768
    log_path.write_bytes(expected)
    original_stat = log_path.stat()
    reader = nextflow._IncrementalLogReader(
        log_path, offset=0, max_read_bytes=64 * 1024, max_line_chars=16 * 1024
    )

    observed: list[str] = []
    calls = 0
    while reader.offset < len(expected):
        before = reader.offset
        observed.extend(reader.read_available())
        assert 0 < reader.offset - before <= 64 * 1024
        calls += 1
    observed.extend(reader.read_available(final=True))

    assert calls > 10
    assert "".join(observed).encode("utf-8") == expected
    assert log_path.read_bytes() == expected
    assert log_path.stat().st_ino == original_stat.st_ino


def test_nextflow_log_caps_are_configurable_and_enforce_minimum(monkeypatch) -> None:
    monkeypatch.setenv("BMS_NEXTFLOW_RETAINED_LOG_MAX_BYTES", "12345")
    assert nextflow._bounded_env_int("BMS_NEXTFLOW_RETAINED_LOG_MAX_BYTES", 99999, 4096) == 12345

    monkeypatch.setenv("BMS_NEXTFLOW_RETAINED_LOG_MAX_BYTES", "5")
    assert nextflow._bounded_env_int("BMS_NEXTFLOW_RETAINED_LOG_MAX_BYTES", 99999, 4096) == 4096

    monkeypatch.setenv("BMS_NEXTFLOW_RETAINED_LOG_MAX_BYTES", "not-an-int")
    assert nextflow._bounded_env_int("BMS_NEXTFLOW_RETAINED_LOG_MAX_BYTES", 99999, 4096) == 99999


def test_launcher_uses_byte_bounded_tails_and_never_compacts_active_log() -> None:
    source = NEXTFLOW_SOURCE.read_text(encoding="utf-8")

    assert "full_log = _BoundedLogTail(" in source
    assert "attempt_log = _BoundedLogTail(" in source
    assert "read_incremental_log_chunk(" in source
    assert "compact_log_file" not in source
    assert "BMS_NEXTFLOW_LOG_MAX_BYTES" not in source
    assert "chunk = reader.read()" not in source
