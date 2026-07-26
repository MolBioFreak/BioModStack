"""DNA Weaver purchase planning with mandatory pydna validation for Gibson assembly."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import version
import json
import re
from typing import Any

from pydna.assembly2 import Assembly
from pydna.dseqrecord import Dseqrecord

from build_identity import current_build_identity

from .common import orient_fragment
from .gibson import simulate_gibson
from .pydna_gibson import _candidate_sequence
from .types import AssemblyError, AssemblyFragment, AssemblyProduct

ENGINE = "dnaweaver"
VALIDATOR_ENGINE = "pydna"
RECEIPT_SCHEMA_VERSION = "dnaweaver-gibson-plan-v4"
PLANNER_IMPLEMENTATION = "biomodstack.services.assembly.dnaweaver_gibson"
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
    target_checksum: str
    plan_checksum: str
    receipt_schema_version: str
    planner_implementation_revision: str
    selected_product_checksum: str
    target_attestation: dict[str, Any]
    planning_parameters: dict[str, Any]
    manufacturability_profile: str
    quality_checks: list[dict[str, Any]]
    order_ready: bool
    warnings: list[str]
    quote_evidence: dict[str, Any]


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


def _json_evidence(value: Any) -> Any:
    """Project third-party quote metadata into deterministic JSON evidence."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_evidence(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_evidence(item) for item in value]
    return str(value)


def _quote_record(quote: Any) -> dict[str, Any]:
    source = getattr(quote, "source", None)
    sequence = str(quote.sequence).upper()
    return {
        "accepted": bool(quote.accepted),
        "sequence": sequence,
        "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
        "price": float(quote.price),
        "lead_time": float(quote.lead_time),
        "deadline": _json_evidence(quote.deadline),
        "message": str(quote.message or ""),
        "metadata": _json_evidence(quote.metadata),
        "quote_id": _json_evidence(quote.id),
        "source": {
            "name": str(getattr(source, "name", "")),
            "type": (
                f"{type(source).__module__}.{type(source).__qualname__}"
                if source is not None
                else None
            ),
        },
    }


def _quote_evidence(quote: Any) -> dict[str, Any]:
    evidence = _quote_record(quote)
    evidence["assembly_plan"] = [
        {
            "source_interval": [int(start), int(end)],
            **_quote_record(child),
        }
        for (start, end), child in sorted(quote.assembly_plan.items())
    ]
    return evidence


def _validate_settings(
    target: str,
    *,
    min_fragment_length: int,
    max_fragment_length: int,
    overlap_length: int,
) -> None:
    if not 2 <= min_fragment_length <= max_fragment_length:
        raise AssemblyError(
            "DNA Weaver fragment lengths must satisfy 2 <= minimum <= maximum"
        )
    if not 15 <= overlap_length <= 80:
        raise AssemblyError("DNA Weaver Gibson overlap must be between 15 and 80 nt")
    if len(target) < 2 * min_fragment_length:
        raise AssemblyError(
            "Target is too short for a two-fragment purchase plan at the selected minimum length"
        )


def _ordered_vendor_fragments_from_quote(
    target: str,
    assembly_plan: dict[tuple[int, int], Any],
    *,
    circular: bool,
    overlap_length: int,
) -> tuple[list[AssemblyFragment], list[dict[str, int]]]:
    segments = sorted(assembly_plan.items())
    if len(segments) < 2:
        raise AssemblyError("DNA Weaver produced fewer than two purchase fragments")

    flank_left = overlap_length // 2
    flank_right = overlap_length - flank_left
    fragments: list[AssemblyFragment] = []
    intervals: list[dict[str, int]] = []
    for index, ((start, end), quote) in enumerate(segments, start=1):
        purchase_sequence = str(quote.sequence).upper()
        if circular and index == 1:
            purchase_sequence = target[-flank_left:] + purchase_sequence
        if circular and index == len(segments):
            purchase_sequence = purchase_sequence + target[:flank_right]
        fragments.append(
            AssemblyFragment(
                id=f"dnaweaver-{index:02d}",
                name=f"DNA Weaver vendor fragment {index:02d}",
                sequence=purchase_sequence,
                orientation="forward",
                metadata={
                    "preparation": "ready_linear",
                    "procurement": "vendor_purchase",
                    "planner": ENGINE,
                    "source_core_start": int(start),
                    "source_core_end": int(end),
                    "terminal_overlap_length": overlap_length,
                    "dnaweaver_quote_segment": [int(start), int(end)],
                    "dnaweaver_quote_sequence_length": len(str(quote.sequence)),
                    "dnaweaver_quote_sequence_sha256": hashlib.sha256(
                        str(quote.sequence).upper().encode("ascii")
                    ).hexdigest(),
                },
            )
        )
        intervals.append(
            {
                "fragment_index": index,
                "start": int(start),
                "end": int(end),
                "core_length": int(end) - int(start),
                "order_length": len(purchase_sequence),
            }
        )
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
        raise AssemblyError(
            "DNA Weaver ordered fragments do not deterministically reconstruct the requested target"
        )

    try:
        records = []
        for fragment in fragments:
            oriented = orient_fragment(fragment)
            record = Dseqrecord(oriented.sequence, circular=False)
            record.name = oriented.name
            records.append(record)
        assembly = Assembly(
            records,
            limit=overlap_length,
            use_fragment_order=True,
            use_all_fragments=True,
        )
        candidates = (
            assembly.assemble_circular(only_adjacent_edges=True, max_assemblies=10)
            if circular
            else assembly.assemble_linear(only_adjacent_edges=True, max_assemblies=10)
        )
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        if "Too many assemblies" in detail:
            raise AssemblyError(
                "BLOCKER: pydna candidate explosion indicates repetitive or non-unique overlap architecture; "
                "choose different junctions, longer unique overlaps, or a hierarchical assembly route"
            ) from None
        raise AssemblyError(
            f"pydna validation of the DNA Weaver plan failed: {detail}"
        ) from None

    exact = sum(
        _candidate_sequence(str(candidate.seq), circular=circular) == expected
        for candidate in candidates
    )
    if exact < 1:
        raise AssemblyError(
            "pydna did not produce an exact requested target from the DNA Weaver purchase plan"
        )
    return product, exact


def _occurrence_count(sequence: str, motif: str, *, circular: bool) -> int:
    if not motif:
        return 0
    searchable = sequence + sequence[: len(motif) - 1] if circular else sequence
    limit = len(sequence) if circular else len(sequence) - len(motif) + 1
    return sum(searchable.startswith(motif, index) for index in range(max(limit, 0)))


def _longest_homopolymer(sequence: str) -> int:
    return max(
        (len(match.group(0)) for match in re.finditer(r"([ACGT])\1*", sequence)),
        default=0,
    )


def _quality_checks(
    target: str,
    fragments: list[AssemblyFragment],
    *,
    circular: bool,
    overlap_length: int,
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    checks: list[dict[str, Any]] = []

    def add(check_id: str, status: str, detail: str, **evidence: Any) -> None:
        checks.append(
            {"check_id": check_id, "status": status, "detail": detail, **evidence}
        )

    piece_count = len(fragments)
    if piece_count > 10:
        add(
            "one_pot_piece_count",
            "blocker",
            f"{piece_count} fragments exceeds the generic 10-piece one-pot safety gate; use a hierarchical assembly plan",
            fragment_count=piece_count,
        )
    elif piece_count > 5:
        add(
            "one_pot_piece_count",
            "advisory",
            f"{piece_count} fragments is a high-risk one-pot Gibson assembly; consider a hierarchical route",
            fragment_count=piece_count,
        )
    else:
        add(
            "one_pot_piece_count",
            "pass",
            f"{piece_count} fragments is within the generic one-pot screening range",
            fragment_count=piece_count,
        )

    for index, fragment in enumerate(fragments, start=1):
        gc_percent = (
            100.0
            * (fragment.sequence.count("G") + fragment.sequence.count("C"))
            / len(fragment.sequence)
        )
        homopolymer = _longest_homopolymer(fragment.sequence)
        status = "pass"
        reasons: list[str] = []
        if gc_percent < 20.0 or gc_percent > 80.0:
            status = "blocker"
            reasons.append(
                f"whole-fragment GC {gc_percent:.1f}% is outside the generic 20–80% synthesis screen"
            )
        if homopolymer >= 10:
            status = "blocker"
            reasons.append(f"contains a {homopolymer}-nt homopolymer")
        add(
            f"fragment_{index:02d}_generic_synthesis",
            status,
            "; ".join(reasons)
            if reasons
            else f"generic whole-fragment GC/homopolymer screen passed ({gc_percent:.1f}% GC)",
            fragment_id=fragment.id,
            gc_percent=round(gc_percent, 2),
            longest_homopolymer=homopolymer,
        )

    junction_count = piece_count if circular else piece_count - 1
    for index in range(junction_count):
        overlap = fragments[index].sequence[-overlap_length:]
        occurrences = _occurrence_count(target, overlap, circular=circular)
        gc_percent = 100.0 * (overlap.count("G") + overlap.count("C")) / len(overlap)
        status = "pass"
        reasons: list[str] = []
        if occurrences != 1:
            status = "blocker"
            reasons.append(
                f"overlap occurs {occurrences} times in the target and is not junction-unique"
            )
        if gc_percent < 25.0 or gc_percent > 75.0:
            status = "blocker"
            reasons.append(
                f"overlap GC {gc_percent:.1f}% is outside the generic 25–75% junction screen"
            )
        add(
            f"junction_{index + 1:02d}_uniqueness_complexity",
            status,
            "; ".join(reasons)
            if reasons
            else "junction overlap is unique and passes the generic GC screen",
            overlap_sequence=overlap,
            occurrence_count=occurrences,
            gc_percent=round(gc_percent, 2),
        )

    order_ready = not any(check["status"] == "blocker" for check in checks)
    warnings = [
        f"{check['status'].upper()}: {check['detail']}"
        for check in checks
        if check["status"] != "pass"
    ]
    return checks, order_ready, warnings


def _plan_checksum(
    *,
    target_checksum: str,
    circular: bool,
    planning_parameters: dict[str, Any],
    fragments: list[AssemblyFragment],
    planner_version: str,
    validator_version: str,
    selected_product_checksum: str,
    target_attestation: dict[str, Any],
    manufacturability_profile: str,
    quality_checks: list[dict[str, Any]],
    order_ready: bool,
    planner_implementation_revision: str,
    source_intervals: list[dict[str, int]],
    estimated_price: float,
    estimated_lead_time_days: float,
    pydna_exact_candidate_count: int,
    junction_evidence: list[dict[str, Any]],
    quote_evidence: dict[str, Any],
) -> str:
    canonical = {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "planner_implementation": PLANNER_IMPLEMENTATION,
        "planner_implementation_revision": planner_implementation_revision,
        "target_checksum": target_checksum,
        "target_attestation": target_attestation,
        "topology": "circular" if circular else "linear",
        "selected_product_checksum": selected_product_checksum,
        "planning_parameters": planning_parameters,
        "planner": {"engine": ENGINE, "version": planner_version},
        "validator": {"engine": VALIDATOR_ENGINE, "version": validator_version},
        "manufacturability_profile": manufacturability_profile,
        "quality_checks": quality_checks,
        "order_ready": order_ready,
        "estimated_price": estimated_price,
        "estimated_lead_time_days": estimated_lead_time_days,
        "pydna_exact_candidate_count": pydna_exact_candidate_count,
        "source_intervals": source_intervals,
        "junctions": junction_evidence,
        "supplier_quote": quote_evidence,
        "ordered_fragments": [
            {
                "id": fragment.id,
                "name": fragment.name,
                "sequence": fragment.sequence,
                "sequence_sha256": hashlib.sha256(
                    fragment.sequence.encode("ascii")
                ).hexdigest(),
                "length": len(fragment.sequence),
                "source_core_start": fragment.metadata.get("source_core_start"),
                "source_core_end": fragment.metadata.get("source_core_end"),
                "terminal_overlap_length": fragment.metadata.get(
                    "terminal_overlap_length"
                ),
                "preparation": fragment.metadata.get("preparation"),
                "procurement": fragment.metadata.get("procurement"),
                "quote_metadata": fragment.metadata,
            }
            for fragment in fragments
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def plan_vendor_gibson(
    target_sequence: str,
    *,
    circular: bool = True,
    min_fragment_length: int = 500,
    max_fragment_length: int = 1500,
    overlap_length: int = 30,
    vendor_name: str = "Configured commercial DNA vendor",
    price_per_bp: float = 0.08,
    lead_time_days: float = 10.0,
    target_attestation: dict[str, Any] | None = None,
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

    target_checksum = hashlib.sha256(target.encode("ascii")).hexdigest()
    attestation = dict(target_attestation or {})
    if not (
        isinstance(attestation.get("sequence_id"), str)
        and attestation["sequence_id"]
        and isinstance(attestation.get("revision_id"), str)
        and attestation["revision_id"]
        and isinstance(attestation.get("revision_number"), int)
        and attestation["revision_number"] >= 1
        and attestation.get("revision_sha256") == target_checksum
    ):
        raise AssemblyError(
            "Order-ready DNA Weaver planning requires a persisted immutable target revision whose SHA-256 matches the requested target"
        )

    dw = _dnaweaver()
    try:
        vendor = dw.CommercialDnaOffer(
            name=vendor_name,
            sequence_constraints=[
                dw.SequenceLengthConstraint(
                    max_length=max_fragment_length + overlap_length
                )
            ],
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
        raise AssemblyError(
            f"DNA Weaver could not produce a vendor plan: {quote.message or 'no accepted plan'}"
        )

    fragments, intervals = _ordered_vendor_fragments_from_quote(
        target,
        quote.assembly_plan,
        circular=circular,
        overlap_length=overlap_length,
    )
    product, exact_count = _validate_with_pydna(
        fragments,
        target=target,
        circular=circular,
        overlap_length=overlap_length,
    )
    # Canonical circular identity is validation-only. Publish and persist the exact
    # submitted target string so coordinates, exports, and provenance retain its
    # requested origin after deterministic and pydna validation have passed.
    product.sequence = target
    planner_version = version("dnaweaver")
    validator_version = version("pydna")
    planning_parameters = {
        "min_fragment_length": min_fragment_length,
        "max_fragment_length": max_fragment_length,
        "overlap_length": overlap_length,
        "vendor_name": vendor_name,
        "price_per_bp": price_per_bp,
        "lead_time_days": lead_time_days,
    }
    selected_product_checksum = hashlib.sha256(
        json.dumps(
            {
                "sequence_sha256": target_checksum,
                "topology": "circular" if circular else "linear",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt_target_attestation = target_attestation or {
        "sequence_id": None,
        "revision_id": None,
        "revision_number": None,
        "revision_sha256": target_checksum,
    }
    manufacturability_profile = "generic_synthetic_dna_v1"
    planner_implementation_revision = current_build_identity()["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", planner_implementation_revision):
        raise AssemblyError(
            "Order-ready DNA Weaver planning requires an exact 40-character Git implementation revision"
        )
    quality_checks, order_ready, quality_warnings = _quality_checks(
        target,
        fragments,
        circular=circular,
        overlap_length=overlap_length,
    )
    estimated_price = (
        sum(len(fragment.sequence) for fragment in fragments) * price_per_bp
    )
    estimated_lead_time_days = float(quote.lead_time)
    junction_evidence = [asdict(junction) for junction in product.junctions]
    quote_evidence = _quote_evidence(quote)
    plan_checksum = _plan_checksum(
        target_checksum=target_checksum,
        circular=circular,
        planning_parameters=planning_parameters,
        fragments=fragments,
        planner_version=planner_version,
        validator_version=validator_version,
        selected_product_checksum=selected_product_checksum,
        target_attestation=receipt_target_attestation,
        manufacturability_profile=manufacturability_profile,
        quality_checks=quality_checks,
        order_ready=order_ready,
        planner_implementation_revision=planner_implementation_revision,
        source_intervals=intervals,
        estimated_price=estimated_price,
        estimated_lead_time_days=estimated_lead_time_days,
        pydna_exact_candidate_count=exact_count,
        junction_evidence=junction_evidence,
        quote_evidence=quote_evidence,
    )
    return DnaWeaverGibsonPlan(
        engine=ENGINE,
        engine_version=planner_version,
        validator_engine=VALIDATOR_ENGINE,
        validator_version=validator_version,
        product=product,
        estimated_price=estimated_price,
        lead_time_days=estimated_lead_time_days,
        source_intervals=intervals,
        pydna_exact_candidate_count=exact_count,
        target_checksum=target_checksum,
        plan_checksum=plan_checksum,
        receipt_schema_version=RECEIPT_SCHEMA_VERSION,
        planner_implementation_revision=planner_implementation_revision,
        selected_product_checksum=selected_product_checksum,
        target_attestation=receipt_target_attestation,
        planning_parameters=planning_parameters,
        manufacturability_profile=manufacturability_profile,
        quality_checks=quality_checks,
        order_ready=order_ready,
        warnings=quality_warnings,
        quote_evidence=quote_evidence,
    )
