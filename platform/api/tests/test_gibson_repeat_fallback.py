"""Repeat-bearing targets: the stock pydna assembler refuses them, the overlap planner designs them."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from services.assembly.pydna_gibson import design_gibson  # noqa: E402
from services.assembly.types import AssemblyFragment  # noqa: E402


def dna(seed: int, length: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(length))


def repeat_target(seed: int = 1, unit: int = 60, copies: int = 3, gap: int = 200) -> str:
    """Unique stretches interleaved with copies of one repeat unit (what makes pydna ambiguous)."""
    repeat = dna(seed + 1000, unit)
    parts: list[str] = []
    for k in range(copies):
        parts += [dna(seed * 31 + k, gap), repeat]
    parts.append(dna(seed * 31 + 99, gap))
    return "".join(parts)


def split(sequence: str, count: int) -> list[AssemblyFragment]:
    cuts = [round(i * len(sequence) / count) for i in range(count)] + [len(sequence)]
    return [
        AssemblyFragment(id=f"f{i + 1}", name=f"f{i + 1}", sequence=sequence[cuts[i]:cuts[i + 1]])
        for i in range(count)
    ]


def rotations(sequence: str) -> str:
    return sequence + sequence


@pytest.mark.parametrize(
    ("count", "preparations"),
    [
        (2, ["pcr", "pcr"]),
        (3, ["pcr", "pcr", "pcr"]),
        (4, ["pcr", "pcr", "pcr", "pcr"]),
        (4, ["pcr", "ready_linear", "pcr", "ready_linear"]),
    ],
)
def test_repeat_bearing_target_is_designed_not_refused(count: int, preparations: list[str]) -> None:
    target = repeat_target()
    result = design_gibson(split(target, count), preparations=preparations, circular=True, overlap=30, min_anneal=18)
    selected = next(c for c in result.candidates if c.checksum == result.selected_candidate_checksum)
    product = selected.product.sequence.upper()
    assert len(product) == len(target) and product in rotations(target)
    assert len(selected.product.junctions) == count
    assert all(j.overlap_length >= 15 for j in selected.product.junctions)
    assert any("overlap planner" in warning for warning in result.warnings)


def test_target_the_stock_assembler_handles_is_unchanged_and_not_flagged() -> None:
    target = dna(7, 900)
    result = design_gibson(split(target, 3), preparations=["pcr"] * 3, circular=True, overlap=28)
    assert not any("overlap planner" in warning for warning in result.warnings)
