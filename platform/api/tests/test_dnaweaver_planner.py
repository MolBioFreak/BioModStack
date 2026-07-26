from __future__ import annotations

import hashlib
import random
from dataclasses import replace

import pytest

from services.assembly.dnaweaver_gibson import plan_vendor_gibson as _plan_vendor_gibson
from services.assembly.types import AssemblyError


@pytest.fixture(autouse=True)
def _exact_build_revision(monkeypatch) -> None:
    monkeypatch.setenv("BMS_BUILD_SHA", "a" * 40)


def _target(seed: int = 71, length: int = 900) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(length))


def plan_vendor_gibson(target: str, **kwargs):
    kwargs.setdefault(
        "target_attestation",
        {
            "sequence_id": "target-sequence-1",
            "revision_id": "target-revision-1",
            "revision_number": 1,
            "revision_sha256": hashlib.sha256(target.encode("ascii")).hexdigest(),
        },
    )
    return _plan_vendor_gibson(target, **kwargs)


@pytest.mark.parametrize(
    "attestation", [None, {}, {"sequence_id": "target-sequence-1"}]
)
def test_order_ready_plan_requires_immutable_target_attestation(attestation) -> None:
    target = _target(seed=61)
    with pytest.raises(AssemblyError, match="persisted immutable target revision"):
        _plan_vendor_gibson(
            target,
            circular=False,
            min_fragment_length=250,
            max_fragment_length=450,
            overlap_length=30,
            target_attestation=attestation,
        )


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
    assert len(plan.product.fragments) >= 2
    assert len(plan.product.junctions) == len(plan.product.fragments)
    assert plan.product.junctions[-1].right_fragment_id == plan.product.fragments[0].id
    assert all(
        fragment.metadata["preparation"] == "ready_linear"
        for fragment in plan.product.fragments
    )
    assert all(
        fragment.metadata["procurement"] == "vendor_purchase"
        for fragment in plan.product.fragments
    )
    assert len(plan.product.sequence) == len(target)
    assert plan.product.sequence == target
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


def test_plan_receipt_binds_exact_api_build_revision(monkeypatch) -> None:
    target = _target(seed=99)
    common = {
        "circular": False,
        "min_fragment_length": 250,
        "max_fragment_length": 450,
        "overlap_length": 30,
    }
    monkeypatch.setenv("BMS_BUILD_SHA", "a" * 40)
    first = plan_vendor_gibson(target, **common)
    monkeypatch.setenv("BMS_BUILD_SHA", "b" * 40)
    second = plan_vendor_gibson(target, **common)

    assert first.planner_implementation_revision == "a" * 40
    assert second.planner_implementation_revision == "b" * 40
    assert first.plan_checksum != second.plan_checksum


def test_plan_receipt_binds_complete_persisted_order_evidence(monkeypatch) -> None:
    import services.assembly.dnaweaver_gibson as planner

    target = _target(seed=109)
    common = {
        "circular": False,
        "min_fragment_length": 250,
        "max_fragment_length": 450,
        "overlap_length": 30,
    }
    original = planner._ordered_vendor_fragments_from_quote
    baseline = plan_vendor_gibson(target, **common)

    def mutate_authority(*args, **kwargs):
        fragments, intervals = original(*args, **kwargs)
        fragments[0].name = f"{fragments[0].name}-tampered"
        fragments[0].metadata["procurement"] = "different-procurement-route"
        intervals[0] = {**intervals[0], "start": intervals[0]["start"] + 1}
        return fragments, intervals

    monkeypatch.setattr(
        planner, "_ordered_vendor_fragments_from_quote", mutate_authority
    )
    mutated = plan_vendor_gibson(target, **common)

    assert mutated.plan_checksum != baseline.plan_checksum


def test_plan_receipt_binds_junction_and_full_supplier_quote_evidence(
    monkeypatch,
) -> None:
    import services.assembly.dnaweaver_gibson as planner

    target = _target(seed=117)
    common = {
        "circular": True,
        "min_fragment_length": 250,
        "max_fragment_length": 450,
        "overlap_length": 30,
    }
    baseline = plan_vendor_gibson(target, **common)
    original_validate = planner._validate_with_pydna

    def mutate_junction(*args, **kwargs):
        product, count = original_validate(*args, **kwargs)
        product.junctions[0] = replace(
            product.junctions[0], notes=[*product.junctions[0].notes, "tampered"]
        )
        return product, count

    monkeypatch.setattr(planner, "_validate_with_pydna", mutate_junction)
    junction_mutated = plan_vendor_gibson(target, **common)
    assert junction_mutated.plan_checksum != baseline.plan_checksum

    monkeypatch.setattr(planner, "_validate_with_pydna", original_validate)
    original_quote_evidence = planner._quote_evidence

    def mutate_quote(quote):
        evidence = original_quote_evidence(quote)
        return {**evidence, "message": "tampered supplier quote"}

    monkeypatch.setattr(planner, "_quote_evidence", mutate_quote)
    quote_mutated = plan_vendor_gibson(target, **common)
    assert quote_mutated.plan_checksum != baseline.plan_checksum


@pytest.mark.parametrize("revision", [None, "unknown", "not-a-sha", "a" * 64])
def test_order_ready_plan_requires_exact_git_implementation_revision(
    monkeypatch, revision: str | None
) -> None:
    if revision is None:
        monkeypatch.delenv("BMS_BUILD_SHA", raising=False)
    else:
        monkeypatch.setenv("BMS_BUILD_SHA", revision)

    with pytest.raises(AssemblyError, match="exact.*implementation revision"):
        plan_vendor_gibson(
            _target(seed=131),
            circular=False,
            min_fragment_length=250,
            max_fragment_length=450,
            overlap_length=30,
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
