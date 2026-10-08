"""Bounded pydna-backed Gibson assembly design."""
from __future__ import annotations

import hashlib
from importlib.metadata import version

from pydna.assembly2 import Assembly
from pydna.design import assembly_fragments, primer_design
from pydna.dseqrecord import Dseqrecord
from pydna.tm import tm_default

from services.primer_qc import evaluate_primer_pair_qc, evaluate_primer_qc

from .common import fragment_provenance_payload, orient_fragment
from .gibson import simulate_gibson
from .types import (
    AssemblyError,
    AssemblyFragment,
    AssemblyProduct,
    DesignedFragment,
    FragmentPreparation,
    GeneratedPrimer,
    GibsonCandidate,
    GibsonDesignResult,
    OrientedFragment,
)

ENGINE = "pydna"
ENGINE_VERSION = version("pydna")
MAX_CANDIDATES = 10
MAX_TOTAL_NT = 2_000_000
MAX_FRAGMENTS = 50


def _sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def _canonical_circular_rotation(sequence: str) -> str:
    """Return the lexicographically least rotation in linear time."""
    if not sequence:
        return sequence
    doubled = sequence + sequence
    length = len(sequence)
    left, right, offset = 0, 1, 0
    while left < length and right < length and offset < length:
        left_base = doubled[left + offset]
        right_base = doubled[right + offset]
        if left_base == right_base:
            offset += 1
            continue
        if left_base > right_base:
            left = left + offset + 1
            if left <= right:
                left = right + 1
        else:
            right = right + offset + 1
            if right <= left:
                right = left + 1
        offset = 0
    start = min(left, right)
    return doubled[start : start + length]


def _candidate_sequence(sequence: str, *, circular: bool) -> str:
    normalized = sequence.upper()
    return _canonical_circular_rotation(normalized) if circular else normalized


def _source_provenance(
    fragments: list[OrientedFragment],
    preparations: list[FragmentPreparation],
) -> list[dict[str, object]]:
    provenance = fragment_provenance_payload(fragments)
    for item, fragment, preparation in zip(provenance, fragments, preparations):
        item["preparation"] = preparation
        item["sequence_length"] = len(fragment.sequence)
        item["sequence_sha256"] = _sha256(fragment.sequence)
    return provenance


def _validate_request(
    fragments: list[AssemblyFragment],
    preparations: list[FragmentPreparation],
    *,
    circular: bool,
    overlap: int,
    target_tm: float,
    min_anneal: int,
    max_candidates: int,
) -> None:
    if not 2 <= len(fragments) <= MAX_FRAGMENTS:
        raise AssemblyError(
            f"pydna Gibson design requires 2 to {MAX_FRAGMENTS} ordered fragments"
        )
    if len(preparations) != len(fragments):
        raise AssemblyError("Each Gibson fragment requires one preparation value")
    invalid_preparations = sorted(set(preparations) - {"pcr", "ready_linear"})
    if invalid_preparations:
        raise AssemblyError(
            "Unsupported Gibson fragment preparation: " + ", ".join(invalid_preparations)
        )
    adjacent_preparations = list(zip(preparations, preparations[1:]))
    if circular:
        adjacent_preparations.append((preparations[-1], preparations[0]))
    if any(left == right == "ready_linear" for left, right in adjacent_preparations):
        raise AssemblyError(
            "pydna cannot design an overlap between adjacent ready_linear fragments; "
            "at least one fragment at each junction must use pcr preparation"
        )
    if any(fragment.circular for fragment in fragments):
        raise AssemblyError("Gibson design fragments must be provided as linear molecules")
    if not 15 <= overlap <= 80:
        raise AssemblyError("Gibson overlap must be between 15 and 80 nt")
    if not 45.0 <= target_tm <= 72.0:
        raise AssemblyError("Primer target Tm must be between 45 and 72 °C")
    if not 10 <= min_anneal <= 30:
        raise AssemblyError("Minimum primer annealing length must be between 10 and 30 nt")
    if not 1 <= max_candidates <= MAX_CANDIDATES:
        raise AssemblyError("Gibson design supports between 1 and 10 candidates")


def _primer_result(
    fragment: OrientedFragment,
    direction: str,
    primer,
) -> GeneratedPrimer:
    full_sequence = str(primer.seq).upper()
    annealing_sequence = str(primer.footprint).upper()
    tail_sequence = full_sequence[: len(full_sequence) - len(annealing_sequence)]
    primer_id = f"{fragment.id}:{direction}"
    qc = evaluate_primer_qc(
        full_sequence,
        template_sequence=fragment.sequence,
        min_binding_anneal_length=min(12, len(annealing_sequence)),
    )
    return GeneratedPrimer(
        id=primer_id,
        fragment_id=fragment.id,
        fragment_name=fragment.name,
        direction=direction,  # type: ignore[arg-type]
        full_sequence=full_sequence,
        annealing_sequence=annealing_sequence,
        tail_sequence=tail_sequence,
        tm=round(float(tm_default(annealing_sequence)), 2),
        warnings=list(qc.warnings),
    )


def _designed_input_fragment(
    source: OrientedFragment,
    sequence: str,
) -> AssemblyFragment:
    return AssemblyFragment(
        id=source.id,
        name=source.name,
        sequence=sequence,
        orientation="forward",
        role=source.role,
        metadata={"source_orientation": source.orientation},
    )


def design_gibson(
    fragments: list[AssemblyFragment],
    *,
    preparations: list[FragmentPreparation],
    circular: bool,
    overlap: int = 28,
    target_tm: float = 60.0,
    min_anneal: int = 15,
    max_candidates: int = MAX_CANDIDATES,
) -> GibsonDesignResult:
    """Design and independently validate one bounded ordered Gibson assembly."""
    _validate_request(
        fragments,
        preparations,
        circular=circular,
        overlap=overlap,
        target_tm=target_tm,
        min_anneal=min_anneal,
        max_candidates=max_candidates,
    )
    oriented = [orient_fragment(fragment) for fragment in fragments]
    total_nt = sum(len(fragment.sequence) for fragment in oriented)
    if total_nt > MAX_TOTAL_NT:
        raise AssemblyError(
            f"Gibson design input is {total_nt} nt; the maximum is {MAX_TOTAL_NT} nt"
        )

    try:
        pydna_inputs = []
        for fragment, preparation in zip(oriented, preparations):
            record = Dseqrecord(fragment.sequence, circular=False)
            record.name = fragment.name
            if preparation == "pcr":
                record = primer_design(record, limit=min_anneal, target_tm=target_tm)
            pydna_inputs.append(record)

        pydna_designed = assembly_fragments(
            pydna_inputs,
            overlap=overlap,
            circular=circular,
        )
        if len(pydna_designed) != len(oriented):
            raise AssemblyError(
                "pydna could not preserve every ordered source fragment; "
                "short linker-like inputs are not supported by this MVP"
            )

        assembly = Assembly(
            pydna_designed,
            limit=overlap,
            use_fragment_order=True,
            use_all_fragments=True,
        )
        pydna_products = (
            assembly.assemble_circular(
                only_adjacent_edges=True,
                max_assemblies=max_candidates,
            )
            if circular
            else assembly.assemble_linear(
                only_adjacent_edges=True,
                max_assemblies=max_candidates,
            )
        )
    except AssemblyError:
        raise
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise AssemblyError(f"pydna Gibson design failed: {detail}") from None

    primers: list[GeneratedPrimer] = []
    designed_fragments: list[DesignedFragment] = []
    validation_inputs: list[AssemblyFragment] = []
    warnings: list[str] = []
    for source, preparation, designed in zip(oriented, preparations, pydna_designed):
        designed_sequence = str(designed.seq).upper()
        primer_ids: list[str] = []
        if preparation == "pcr":
            forward = _primer_result(source, "forward", designed.forward_primer)
            reverse = _primer_result(source, "reverse", designed.reverse_primer)
            pair_qc = evaluate_primer_pair_qc(
                forward.full_sequence,
                reverse.full_sequence,
            )
            forward.warnings.extend(pair_qc.warnings)
            reverse.warnings.extend(pair_qc.warnings)
            primers.extend((forward, reverse))
            primer_ids = [forward.id, reverse.id]
            for primer in (forward, reverse):
                warnings.extend(
                    f"{primer.id}: {message}" for message in primer.warnings
                )
        designed_fragments.append(
            DesignedFragment(
                id=source.id,
                name=source.name,
                preparation=preparation,
                sequence=designed_sequence,
                checksum=_sha256(designed_sequence),
                primer_ids=primer_ids,
            )
        )
        validation_inputs.append(_designed_input_fragment(source, designed_sequence))

    try:
        validated = simulate_gibson(
            validation_inputs,
            circular=circular,
            minimum_overlap=overlap,
            preferred_overlap=overlap,
            maximum_overlap=overlap,
        )
    except AssemblyError as exc:
        raise AssemblyError(
            f"Designed fragments failed independent Gibson validation: {exc}"
        ) from None

    intended_sequence = "".join(fragment.sequence for fragment in oriented)
    intended_key = _candidate_sequence(intended_sequence, circular=circular)
    validated_key = _candidate_sequence(validated.sequence, circular=circular)
    if validated_key != intended_key:
        raise AssemblyError(
            "Designed fragments did not reconstruct the intended ordered product"
        )

    selected_product = AssemblyProduct(
        mode="gibson",
        sequence=intended_key,
        circular=circular,
        fragments=oriented,
        junctions=validated.junctions,
        warnings=list(dict.fromkeys(warnings + validated.warnings)),
        validation_notes=["Independently validated with services.assembly.gibson.simulate_gibson"],
    )

    unique_sequences = {
        _candidate_sequence(str(product.seq.watson), circular=circular)
        for product in pydna_products
    }
    ordered_sequences = sorted(
        unique_sequences,
        key=lambda sequence: (sequence != intended_key, _sha256(sequence)),
    )[:max_candidates]
    if intended_key not in ordered_sequences:
        raise AssemblyError(
            "pydna produced no exact candidate for the intended ordered assembly"
        )

    candidates: list[GibsonCandidate] = []
    for sequence in ordered_sequences:
        exact_match = sequence == intended_key
        product = selected_product if exact_match else AssemblyProduct(
            mode="gibson",
            sequence=sequence,
            circular=circular,
            fragments=oriented,
            junctions=validated.junctions,
            warnings=list(selected_product.warnings),
            validation_notes=["Alternate ordered product enumerated by pydna"],
        )
        candidates.append(
            GibsonCandidate(
                checksum=_sha256(sequence),
                product=product,
                exact_match=exact_match,
            )
        )

    selected_checksum = _sha256(intended_key)
    return GibsonDesignResult(
        engine=ENGINE,
        engine_version=ENGINE_VERSION,
        circular=circular,
        overlap=overlap,
        target_tm=target_tm,
        min_anneal=min_anneal,
        primers=primers,
        designed_fragments=designed_fragments,
        candidates=candidates,
        selected_candidate_checksum=selected_checksum,
        warnings=list(dict.fromkeys(selected_product.warnings)),
        source_provenance=_source_provenance(oriented, preparations),
    )
