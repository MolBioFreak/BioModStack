from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np


API_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = API_ROOT / "services" / "sequence_alignment.py"
SPEC = importlib.util.spec_from_file_location("sequence_alignment_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
sequence_alignment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sequence_alignment
SPEC.loader.exec_module(sequence_alignment)


class _LenOverflowAlignments:
    def __len__(self) -> int:
        raise OverflowError("number of optimal alignments is larger than 9223372036854775807")

    def __iter__(self):
        alignment = sequence_alignment.Align.Alignment(
            ["AAAAAA", "AAA"],
            np.array([[0, 3], [0, 3]], dtype=int),
        )
        alignment.score = 42.0
        yield alignment


class _LenOverflowAligner:
    def align(self, reference: str, query: str) -> _LenOverflowAlignments:
        return _LenOverflowAlignments()


def test_select_candidate_does_not_count_all_optimal_alignments(monkeypatch) -> None:
    monkeypatch.setattr(sequence_alignment, "_build_aligner", lambda settings: _LenOverflowAligner())

    candidate = sequence_alignment._select_candidate(
        "AAAAAA",
        "AAA",
        sequence_alignment.AlignmentSettings(mode="placement", strand="forward"),
    )

    assert candidate.alignment.score == 42.0
    assert candidate.reference_sequence == "AAAAAA"
    assert candidate.query_sequence == "AAA"
    assert candidate.strand == "forward"
