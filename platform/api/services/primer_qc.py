"""Exact primer and oligo QC metrics for the molecular toolkit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.molbio_ops import (
    PrimerBinding,
    _bases_overlap,
    _find_pattern_positions_canonical,
    clean_sequence,
    reverse_complement,
)


@dataclass(slots=True)
class PrimerQcMetrics:
    sequence: str
    sequence_type: Literal["dna", "rna"]
    length: int
    gc_percent: float
    max_self_complement: int
    three_prime_self_complement: int
    max_hairpin_stem: int
    hairpin_loop_size: int | None
    binding_site_count: int | None
    off_target_site_count: int | None
    binding_positions: list[dict[str, int | bool]]
    warnings: list[str]


@dataclass(slots=True)
class PrimerPairQcMetrics:
    heterodimer_complement: int
    three_prime_heterodimer: int
    warnings: list[str]


def _calculate_gc_percent(sequence: str) -> float:
    if not sequence:
        return 0.0
    gc = sequence.count("G") + sequence.count("C")
    return round((gc / len(sequence)) * 100.0, 2)


def _dna_alphabet(sequence: str) -> str:
    return clean_sequence(sequence).replace("U", "T")


def _longest_contiguous_complement(left: str, right: str) -> int:
    """Longest perfectly paired run anywhere in an antiparallel left/right duplex."""
    left_sequence = _dna_alphabet(left)
    right_sequence = reverse_complement(_dna_alphabet(right))
    best = 0
    for offset in range(-len(right_sequence) + 1, len(left_sequence)):
        run = 0
        for left_index in range(max(0, offset), min(len(left_sequence), offset + len(right_sequence))):
            if left_sequence[left_index] == right_sequence[left_index - offset]:
                run += 1
                best = max(best, run)
            else:
                run = 0
    return best


def _three_prime_dimer_length(left: str, right: str) -> int:
    """Longest antiparallel duplex in which both 3' terminal bases are paired.

    left[-n:] pairs with right[-n:] exactly when left[-n:] is the reverse
    complement of right[-n:]. This is a geometric paired-run metric, not an
    extension prediction: fully complementary primers need not leave an unpaired
    template beyond either 3' end.
    """
    left_sequence = _dna_alphabet(left)
    right_sequence = _dna_alphabet(right)
    for length in range(min(len(left_sequence), len(right_sequence)), 0, -1):
        if left_sequence[-length:] == reverse_complement(right_sequence[-length:]):
            return length
    return 0


def _find_hairpin(sequence: str, *, min_stem: int = 3, min_loop: int = 3, max_loop: int = 12) -> tuple[int, int | None]:
    cleaned = _dna_alphabet(sequence)
    for stem_length in range(len(cleaned) // 2, min_stem - 1, -1):
        for left_start in range(0, len(cleaned) - stem_length):
            left_end = left_start + stem_length
            for loop_size in range(min_loop, max_loop + 1):
                right_start = left_end + loop_size
                right_end = right_start + stem_length
                if right_end > len(cleaned):
                    continue
                if cleaned[left_start:left_end] == reverse_complement(cleaned[right_start:right_end]):
                    return stem_length, loop_size
    return 0, None


def _primer_binding_sites(
    template: str,
    primer: str,
    *,
    reverse: bool,
    circular: bool,
    sequence_type: str,
    min_anneal_length: int,
) -> list[PrimerBinding]:
    """Every distinct 3'-anchored site with at least ``min_anneal_length`` paired 3' bases.

    One template scan finds each exact 3' core; each hit is then extended toward
    the primer's 5' end. Partial 3' matches are reported alongside the intended
    full-length site because they are the mispriming risk this check exists for.
    """
    length = len(template)
    core_length = max(1, min(len(primer), min_anneal_length))
    # Complement once per strand, not once per extended base at every hit.
    oriented_primer = reverse_complement(primer, sequence_type) if reverse else primer
    query = oriented_primer[:core_length] if reverse else oriented_primer[-core_length:]
    sites: list[PrimerBinding] = []
    for position in _find_pattern_positions_canonical(template, query, circular=circular):
        anneal_length = core_length
        while anneal_length < len(primer):
            if reverse:
                template_index = position + anneal_length
                expected = oriented_primer[anneal_length]
            else:
                template_index = position - (anneal_length - core_length) - 1
                expected = oriented_primer[-(anneal_length + 1)]
            if circular:
                if anneal_length + 1 > length:
                    break
                template_index %= length
            elif not 0 <= template_index < length:
                break
            if not _bases_overlap(template[template_index], expected):
                break
            anneal_length += 1
        start = position if reverse else position - (anneal_length - core_length)
        if circular:
            start %= length
        sites.append(PrimerBinding(
            start=start,
            end=start + anneal_length,
            anneal_length=anneal_length,
            overhang_length=len(primer) - anneal_length,
        ))
    return sites


def evaluate_primer_qc(
    sequence: str,
    *,
    sequence_type: Literal["dna", "rna"] = "dna",
    template_sequence: str | None = None,
    circular_template: bool = False,
    min_binding_anneal_length: int = 12,
) -> PrimerQcMetrics:
    return _evaluate_primer_qc_canonical(
        clean_sequence(sequence), sequence_type=sequence_type,
        template_sequence=clean_sequence(template_sequence) if template_sequence else None,
        circular_template=circular_template, min_binding_anneal_length=min_binding_anneal_length,
    )


def _evaluate_primer_qc_canonical(
    cleaned: str, *, sequence_type: Literal["dna", "rna"] = "dna",
    template_sequence: str | None = None, circular_template: bool = False,
    min_binding_anneal_length: int = 12,
) -> PrimerQcMetrics:
    if not cleaned:
        raise ValueError("Primer sequence contains no valid nucleotide characters")

    max_self = _longest_contiguous_complement(cleaned, cleaned)
    three_prime_self = _three_prime_dimer_length(cleaned, cleaned)
    hairpin_stem, hairpin_loop = _find_hairpin(cleaned)
    warnings: list[str] = []

    if max_self >= 8:
        warnings.append(f"Strong self-complementarity detected ({max_self} contiguous bases)")
    elif max_self >= 6:
        warnings.append(f"Moderate self-complementarity detected ({max_self} contiguous bases)")

    if three_prime_self >= 4:
        warnings.append(f"3' self-complementarity is elevated ({three_prime_self} contiguous bases)")

    if hairpin_stem >= 5:
        warnings.append(f"Hairpin stem detected ({hairpin_stem} bp stem, loop {hairpin_loop})")

    binding_positions: list[dict[str, int | bool]] = []
    binding_site_count: int | None = None
    off_target_site_count: int | None = None
    if template_sequence:
        forward_sites = _primer_binding_sites(
            template_sequence,
            cleaned,
            reverse=False,
            circular=circular_template,
            sequence_type=sequence_type,
            min_anneal_length=min_binding_anneal_length,
        )
        reverse_sites = _primer_binding_sites(
            template_sequence,
            cleaned,
            reverse=True,
            circular=circular_template,
            sequence_type=sequence_type,
            min_anneal_length=min_binding_anneal_length,
        )
        for site in forward_sites:
            binding_positions.append({
                "start": site.start,
                "end": site.end,
                "strand": 1,
                "anneal_length": site.anneal_length,
                "overhang_length": site.overhang_length,
                "reverse_primer_binding": False,
            })
        for site in reverse_sites:
            binding_positions.append({
                "start": site.start,
                "end": site.end,
                "strand": -1,
                "anneal_length": site.anneal_length,
                "overhang_length": site.overhang_length,
                "reverse_primer_binding": True,
            })
        binding_positions.sort(key=lambda item: (int(item["start"]), int(item["strand"])))  # type: ignore[arg-type]
        binding_site_count = len(binding_positions)
        off_target_site_count = max(binding_site_count - 1, 0)
        if binding_site_count == 0:
            warnings.append("No annealing site found on the current template")
        elif binding_site_count > 1:
            warnings.append(f"Multiple template binding sites detected ({binding_site_count})")

    return PrimerQcMetrics(
        sequence=cleaned,
        sequence_type=sequence_type,
        length=len(cleaned),
        gc_percent=_calculate_gc_percent(cleaned),
        max_self_complement=max_self,
        three_prime_self_complement=three_prime_self,
        max_hairpin_stem=hairpin_stem,
        hairpin_loop_size=hairpin_loop,
        binding_site_count=binding_site_count,
        off_target_site_count=off_target_site_count,
        binding_positions=binding_positions,
        warnings=warnings,
    )


def evaluate_primer_pair_qc(
    forward_primer: str,
    reverse_primer: str,
) -> PrimerPairQcMetrics:
    heterodimer = _longest_contiguous_complement(forward_primer, reverse_primer)
    three_prime_heterodimer = _three_prime_dimer_length(forward_primer, reverse_primer)
    warnings: list[str] = []
    if heterodimer >= 8:
        warnings.append(f"Strong heterodimer complementarity detected ({heterodimer} contiguous bases)")
    elif heterodimer >= 6:
        warnings.append(f"Moderate heterodimer complementarity detected ({heterodimer} contiguous bases)")
    if three_prime_heterodimer >= 4:
        warnings.append(f"3' heterodimer complementarity is elevated ({three_prime_heterodimer} contiguous bases)")

    return PrimerPairQcMetrics(
        heterodimer_complement=heterodimer,
        three_prime_heterodimer=three_prime_heterodimer,
        warnings=warnings,
    )
