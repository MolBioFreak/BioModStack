from __future__ import annotations

import hashlib
import random

import pytest

from services.assembly.dnaweaver_gibson import plan_vendor_gibson
from services.assembly.types import AssemblyError


def _target(seed: int = 71, length: int = 900) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(length))


def test_dnaweaver_plan_orders_preoverlapped_fragments_and_pydna_validates_target() -> (
    None
):
    target = _target()

    plan = plan_vendor_gibson(
        target,
        circular=True,
        min_fragment_length=250,
        max_fragment_length=450,
        overlap_length=30,
    )

    assert plan.engine == "dnaweaver"
    assert plan.engine_version == "0.3.10"
    assert plan.validator_engine == "pydna"
    assert plan.validator_version == "5.5.16"
    assert plan.pydna_exact_candidate_count >= 1
    assert plan.product.circular is True
    assert plan.product.sequence == target
    assert len(plan.product.fragments) >= 2
    assert all(
        fragment.metadata["preparation"] == "ready_linear"
        for fragment in plan.product.fragments
    )
    assert all(
        fragment.metadata["procurement"] == "vendor_purchase"
        for fragment in plan.product.fragments
    )
    assert len(plan.product.sequence) == len(target)
    assert len(plan.target_checksum) == 64
    assert len(plan.plan_checksum) == 64
    assert plan.order_ready is True
    assert plan.manufacturability_profile == "generic_synthetic_dna_v1"
    assert plan.estimated_price == pytest.approx(
        sum(len(fragment.sequence) for fragment in plan.product.fragments) * 0.08
    )
    assert all(
        fragment.metadata["dnaweaver_quote_segment"]
        == [
            plan.source_intervals[index]["start"],
            plan.source_intervals[index]["end"],
        ]
        for index, fragment in enumerate(plan.product.fragments)
    )
    assert all(check["status"] == "pass" for check in plan.quality_checks)
    flank = 15
    for index, fragment in enumerate(plan.product.fragments):
        quoted_sequence = fragment.sequence
        if index == 0:
            quoted_sequence = quoted_sequence[flank:]
        if index == len(plan.product.fragments) - 1:
            quoted_sequence = quoted_sequence[:-flank]
        assert (
            len(quoted_sequence) == fragment.metadata["dnaweaver_quote_sequence_length"]
        )
        assert (
            hashlib.sha256(quoted_sequence.encode("ascii")).hexdigest()
            == fragment.metadata["dnaweaver_quote_sequence_sha256"]
        )


def test_linear_order_sequences_equal_dnaweaver_supplier_quote_sequences() -> None:
    plan = plan_vendor_gibson(
        _target(seed=88),
        circular=False,
        min_fragment_length=250,
        max_fragment_length=450,
        overlap_length=30,
    )

    assert plan.product.circular is False
    for fragment in plan.product.fragments:
        assert (
            len(fragment.sequence)
            == fragment.metadata["dnaweaver_quote_sequence_length"]
        )
        assert (
            hashlib.sha256(fragment.sequence.encode("ascii")).hexdigest()
            == fragment.metadata["dnaweaver_quote_sequence_sha256"]
        )


def test_repetitive_target_fails_closed_as_ambiguous_overlap_blocker() -> None:
    target = ("ATGCGTACGATCGTACGCTAGCTAGCATCGATCG" * 30)[:900]

    with pytest.raises(AssemblyError, match="BLOCKER.*non-unique overlap"):
        plan_vendor_gibson(
            target,
            circular=True,
            min_fragment_length=250,
            max_fragment_length=450,
            overlap_length=30,
        )
