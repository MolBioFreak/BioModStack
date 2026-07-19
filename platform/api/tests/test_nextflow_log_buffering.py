from __future__ import annotations

from pathlib import Path

from services.nextflow import _BoundedLogTail


NEXTFLOW_SOURCE = Path(__file__).resolve().parents[1] / "services" / "nextflow.py"


def test_bounded_log_tail_caps_line_count_and_line_size() -> None:
    tail = _BoundedLogTail(max_lines=3, max_line_chars=24)

    tail.append("first\n")
    tail.append("second\n")
    tail.append("x" * 100 + "\n")
    tail.append("last\n")

    lines = list(tail)
    assert len(lines) == 3
    assert lines[0] == "second\n"
    assert lines[-1] == "last\n"
    assert len(lines[1]) <= 24
    assert "truncated" in lines[1]
    assert tail.tail(2) == [lines[1], "last\n"]


def test_nextflow_execution_keeps_bounded_tails_and_compacts_durable_log_in_place() -> None:
    source = NEXTFLOW_SOURCE.read_text(encoding="utf-8")

    assert "full_log = BoundedLogBuffer(" in source
    assert "attempt_log = BoundedLogBuffer(" in source
    assert "append_control_log(" in source
    assert "full_log.tail(20)" in source
    assert "compact_log_file(log_path, log_file_max_bytes)" in source
    assert 'open(log_path, "w", encoding="utf-8")' not in source
