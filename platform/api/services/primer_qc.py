"""Exact primer and oligo QC metrics for the molecular toolkit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.molbio_ops import clean_sequence, resolve_primer_binding_sites, reverse_complement


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


def _longest_contiguous_complement(left: str, right: str, *, anchor_left_3: bool = False, anchor_right_3: bool = False) -> int:
    left_sequence = clean_sequence(left)
    cleaned_right = clean_sequence(right)
    if anchor_left_3 and anchor_right_3:
        for length in range(min(len(left_sequence), len(cleaned_right)), 0, -1):
            right_three_prime_complement = reverse_complement(
                cleaned_right[-length:]
            )[::-1]
            if left_sequence[-length:] == right_three_prime_complement:
                return length
        return 0

    right_sequence = reverse_complement(cleaned_right)
    best = 0

    for offset in range(-len(right_sequence) + 1, len(left_sequence)):
        run = 0
        run_start_left = -1
        run_end_left = -1
        run_start_right = -1
        run_end_right = -1
        for left_index in range(len(left_sequence)):
            right_index = left_index - offset
            if 0 <= right_index < len(right_sequence) and left_sequence[left_index] == right_sequence[right_index]:
                if run == 0:
                    run_start_left = left_index
                    run_start_right = right_index
                run += 1
                run_end_left = left_index
                run_end_right = right_index
                left_anchored = not anchor_left_3 or run_end_left == len(left_sequence) - 1
                right_anchored = not anchor_right_3 or run_end_right == len(right_sequence) - 1
                if left_anchored and right_anchored:
                    best = max(best, run)
            else:
                run = 0

    return best


def _find_hairpin(sequence: str, *, min_stem: int = 3, min_loop: int = 3, max_loop: int = 12) -> tuple[int, int | None]:
    cleaned = clean_sequence(sequence)
    best_stem = 0
    best_loop: int | None = None

    for stem_length in range(len(cleaned) // 2, min_stem - 1, -1):
        for left_start in range(0, len(cleaned) - stem_length):
            left_end = left_start + stem_length
            for loop_size in range(min_loop, max_loop + 1):
                right_start = left_end + loop_size
                right_end = right_start + stem_length
                if right_end > len(cleaned):
                    continue
                left_stem = cleaned[left_start:left_end]
                right_stem = cleaned[right_start:right_end]
                if left_stem == reverse_complement(right_stem):
                    return stem_length, loop_size
        if best_stem:
            break

    return best_stem, best_loop


def evaluate_primer_qc(
    sequence: str,
    *,
    sequence_type: Literal["dna", "rna"] = "dna",
    template_sequence: str | None = None,
    circular_template: bool = False,
    min_binding_anneal_length: int = 12,
) -> PrimerQcMetrics:
    cleaned = clean_sequence(sequence)
    if not cleaned:
        raise ValueError("Primer sequence contains no valid nucleotide characters")

    max_self = _longest_contiguous_complement(cleaned, cleaned)
    three_prime_self = _longest_contiguous_complement(cleaned, cleaned, anchor_left_3=True, anchor_right_3=True)
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
        forward_sites = resolve_primer_binding_sites(
            template_sequence,
            cleaned,
            reverse=False,
            circular=circular_template,
            sequence_type=sequence_type,
            min_anneal_length=min_binding_anneal_length,
        )
        reverse_sites = resolve_primer_binding_sites(
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
    three_prime_heterodimer = _longest_contiguous_complement(
        forward_primer,
        reverse_primer,
        anchor_left_3=True,
        anchor_right_3=True,
    )
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
