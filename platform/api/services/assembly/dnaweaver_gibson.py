"""DNA Weaver purchase planning with mandatory pydna validation for Gibson assembly."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
import re
from typing import Any

from pydna.assembly2 import Assembly
from pydna.dseqrecord import Dseqrecord

from .common import orient_fragment
from .gibson import simulate_gibson
from .pydna_gibson import _candidate_sequence
from .types import AssemblyError, AssemblyFragment, AssemblyProduct

ENGINE = "dnaweaver"
VALIDATOR_ENGINE = "pydna"
DNA_ALPHABET = re.compile(r"^[ACGT]+$")


@dataclass(slots=True)
class DnaWeaverGibsonPlan:
    engine: str
    engine_version: str
    validator_engine: str
    validator_version: str
    product: AssemblyProduct
    estimated_price: float | None
    lead_time_days: float | None
    source_intervals: list[dict[str, int]]
    pydna_exact_candidate_count: int
    warnings: list[str]


def _normalize_target(sequence: str) -> str:
    normalized = "".join(sequence.split()).upper()
    if not normalized or not DNA_ALPHABET.fullmatch(normalized):
        raise AssemblyError("DNA Weaver target must contain only A, C, G, and T")
    return normalized


def _dnaweaver() -> Any:
    try:
        import dnaweaver as dw
    except Exception as exc:  # pragma: no cover - depends on deployment package state
        raise AssemblyError(
            "DNA Weaver is unavailable in this API runtime; rebuild with the BioModStack API dependency set"
        ) from exc
    return dw


def _validate_settings(
    target: str,
    *,
    min_fragment_length: int,
    max_fragment_length: int,
    overlap_length: int,
) -> None:
    if not 2 <= min_fragment_length <= max_fragment_length:
        raise AssemblyError("DNA Weaver fragment lengths must satisfy 2 <= minimum <= maximum")
    if not 15 <= overlap_length <= 80:
        raise AssemblyError("DNA Weaver Gibson overlap must be between 15 and 80 nt")
    if len(target) < 2 * min_fragment_length:
        raise AssemblyError("Target is too short for a two-fragment purchase plan at the selected minimum length")


def _ordered_vendor_fragments(
    target: str,
    cuts: list[int],
    *,
    circular: bool,
    overlap_length: int,
) -> tuple[list[AssemblyFragment], list[dict[str, int]]]:
    boundaries = [0, *cuts, len(target)]
    fragments: list[AssemblyFragment] = []
    intervals: list[dict[str, int]] = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        core = target[start:end]
        if index < len(boundaries) - 1:
            overlap = target[end : end + overlap_length]
        elif circular:
            overlap = target[:overlap_length]
        else:
            overlap = ""
        if len(overlap) != overlap_length and (index < len(boundaries) - 1 or circular):
            raise AssemblyError("DNA Weaver selected a boundary too close to the target terminus for the requested overlap")
        fragments.append(
            AssemblyFragment(
                id=f"dnaweaver-{index:02d}",
                name=f"DNA Weaver vendor fragment {index:02d}",
                sequence=core + overlap,
                metadata={
                    "preparation": "ready_linear",
                    "procurement": "vendor_purchase",
                    "planner": ENGINE,
                    "source_core_start": start,
                    "source_core_end": end,
                    "terminal_overlap_length": len(overlap),
                },
            )
        )
        intervals.append({"start": start, "end": end})
    return fragments, intervals


def _validate_with_pydna(
    fragments: list[AssemblyFragment],
    *,
    target: str,
    circular: bool,
    overlap_length: int,
) -> tuple[AssemblyProduct, int]:
    product = simulate_gibson(
        fragments,
        circular=circular,
        minimum_overlap=overlap_length,
        preferred_overlap=overlap_length,
        maximum_overlap=overlap_length,
    )
    expected = _candidate_sequence(target, circular=circular)
    observed = _candidate_sequence(product.sequence, circular=circular)
    if observed != expected:
        raise AssemblyError("DNA Weaver ordered fragments do not deterministically reconstruct the requested target")

    try:
        records = []
        for fragment in fragments:
            oriented = orient_fragment(fragment)
            record = Dseqrecord(oriented.sequence, circular=False)
            record.name = oriented.name
            records.append(record)
        assembly = Assembly(records, limit=overlap_length, use_fragment_order=True, use_all_fragments=True)
        candidates = (
            assembly.assemble_circular(only_adjacent_edges=True, max_assemblies=10)
            if circular
            else assembly.assemble_linear(only_adjacent_edges=True, max_assemblies=10)
        )
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise AssemblyError(f"pydna validation of the DNA Weaver plan failed: {detail}") from None

    exact = sum(
        _candidate_sequence(str(candidate.seq), circular=circular) == expected
        for candidate in candidates
    )
    if exact < 1:
        raise AssemblyError("pydna did not produce an exact requested target from the DNA Weaver purchase plan")
    return product, exact


def plan_vendor_gibson(
    target_sequence: str,
    *,
    circular: bool = True,
    min_fragment_length: int = 500,
    max_fragment_length: int = 1500,
    overlap_length: int = 30,
    vendor_name: str = "Configured commercial DNA vendor",
    price_per_bp: float = 0.15,
    lead_time_days: float = 10.0,
) -> DnaWeaverGibsonPlan:
    """Plan ready-linear vendor fragments, then require exact pydna validation.

    DNA Weaver selects the economical cut positions. BMS constructs the explicit
    order sequences including terminal Gibson homology and preserves them as the
    authoritative purchase specification. pydna is a required independent gate.
    """
    target = _normalize_target(target_sequence)
    _validate_settings(
        target,
        min_fragment_length=min_fragment_length,
        max_fragment_length=max_fragment_length,
        overlap_length=overlap_length,
    )
    if price_per_bp < 0 or lead_time_days < 0:
        raise AssemblyError("Vendor price and lead time must be non-negative")

    dw = _dnaweaver()
    try:
        vendor = dw.CommercialDnaOffer(
            name=vendor_name,
            sequence_constraints=[dw.SequenceLengthConstraint(max_length=max_fragment_length + overlap_length)],
            pricing=dw.PerBasepairPricing(price_per_bp),
            lead_time=lead_time_days,
        )
        station = dw.DnaAssemblyStation(
            name="BioModStack DNA Weaver Gibson planning",
            assembly_method=dw.GibsonAssemblyMethod(
                overhang_selector=dw.FixedSizeSegmentSelector(overlap_length),
                min_segment_length=min_fragment_length,
                max_segment_length=max_fragment_length,
                max_fragments=50,
                duration=0,
            ),
            supplier=vendor,
            coarse_grain=10,
            fine_grain=1,
        )
        quote = station.get_quote(target, with_assembly_plan=True)
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise AssemblyError(f"DNA Weaver planning failed: {detail}") from None
    if not quote.accepted or not quote.assembly_plan:
        raise AssemblyError(f"DNA Weaver could not produce a vendor plan: {quote.message or 'no accepted plan'}")

    cuts = sorted(segment[1] for segment in quote.assembly_plan)[:-1]
    fragments, intervals = _ordered_vendor_fragments(
        target,
        cuts,
        circular=circular,
        overlap_length=overlap_length,
    )
    if len(fragments) < 2:
        raise AssemblyError("DNA Weaver produced fewer than two purchase fragments")
    product, exact_count = _validate_with_pydna(
        fragments,
        target=target,
        circular=circular,
        overlap_length=overlap_length,
    )
    return DnaWeaverGibsonPlan(
        engine=ENGINE,
        engine_version=version("dnaweaver"),
        validator_engine=VALIDATOR_ENGINE,
        validator_version=version("pydna"),
        product=product,
        estimated_price=quote.price,
        lead_time_days=quote.lead_time,
        source_intervals=intervals,
        pydna_exact_candidate_count=exact_count,
        warnings=[],
    )
