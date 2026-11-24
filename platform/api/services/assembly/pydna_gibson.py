"""Bounded pydna-backed Gibson assembly design."""

from __future__ import annotations

import hashlib
from importlib.metadata import version
import json

from pydna.assembly2 import Assembly
from pydna.design import assembly_fragments, primer_design
from pydna.dseqrecord import Dseqrecord
from pydna.tm import tm_default

from services.primer_qc import evaluate_primer_pair_qc, evaluate_primer_qc

from .common import fragment_provenance_payload, orient_fragment
from services.molbio_ops import _resolve_primer_binding_sites_canonical, reverse_complement

from .gibson import effective_sequence, simulate_gibson
from .gibson_recut import RecutResult, is_synthetic, recut_synthetic
from .gibson_tm import TM_ASSUMPTIONS, TM_MODEL, primer_tm
from .types import (
    AssemblyError,
    AssemblyFragment,
    AssemblyProduct,
    DesignedFragment,
    FragmentPreparation,
    GeneratedPrimer,
    GibsonCandidate,
    GibsonDesignResult,
    JunctionSpec,
    OrientedFragment,
)

ENGINE = "pydna"
ENGINE_VERSION = version("pydna")
MAX_CANDIDATES = 10
MAX_TOTAL_NT = 2_000_000
MAX_FRAGMENTS = 50


def _sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def _candidate_checksum(
    sequence_identity: str,
    *,
    circular: bool,
    source_provenance: list[dict[str, object]],
    preparations: list[FragmentPreparation],
    overlap: int,
    target_tm: float,
    min_anneal: int,
    junctions: list[list[object]] | None = None,
) -> str:
    """Bind candidate identity to topology and reproducible design provenance."""
    payload = {
        "schema": "pydna_gibson_candidate_v2",
        "engine": ENGINE,
        "engine_version": ENGINE_VERSION,
        "sequence_identity": sequence_identity,
        "circular": circular,
        "source_provenance": source_provenance,
        "preparations": preparations,
        "overlap": overlap,
        "target_tm": target_tm,
        "min_anneal": min_anneal,
    }
    if junctions:
        payload["junctions"] = junctions
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
    minimum = 1 if circular else 2
    if not minimum <= len(fragments) <= MAX_FRAGMENTS:
        raise AssemblyError(
            f"pydna Gibson design requires 2 to {MAX_FRAGMENTS} ordered fragments"
            + (" (or one fragment to circularize)" if circular else "")
        )
    if len(preparations) != len(fragments):
        raise AssemblyError("Each Gibson fragment requires one preparation value")
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for fragment in fragments:
        if fragment.id in seen_ids:
            duplicate_ids.add(fragment.id)
        seen_ids.add(fragment.id)
    if duplicate_ids:
        raise AssemblyError(
            "Gibson fragment IDs must be unique: " + ", ".join(sorted(duplicate_ids))
        )
    invalid_preparations = sorted(set(preparations) - {"pcr", "ready_linear"})
    if invalid_preparations:
        raise AssemblyError(
            "Unsupported Gibson fragment preparation: "
            + ", ".join(invalid_preparations)
        )
    adjacent_preparations = list(zip(preparations, preparations[1:]))
    if circular:
        adjacent_preparations.append((preparations[-1], preparations[0]))
    if any(fragment.circular for fragment in fragments):
        raise AssemblyError(
            "Gibson design fragments must be provided as linear molecules"
        )
    if not 15 <= overlap <= 80:
        raise AssemblyError("Gibson overlap must be between 15 and 80 nt")
    if not 45.0 <= target_tm <= 72.0:
        raise AssemblyError("Primer target Tm must be between 45 and 72 °C")
    if not 10 <= min_anneal <= 30:
        raise AssemblyError(
            "Minimum primer annealing length must be between 10 and 30 nt"
        )
    if not 1 <= max_candidates <= MAX_CANDIDATES:
        raise AssemblyError("Gibson design supports between 1 and 10 candidates")


def _primer_result(
    fragment: OrientedFragment,
    direction: str,
    primer,
) -> GeneratedPrimer:
    if isinstance(primer, tuple):
        full_sequence, annealing_sequence = primer
    else:
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
        tm_model=TM_MODEL,
        tm_assumptions=dict(TM_ASSUMPTIONS),
    )


def _designed_input_fragment(
    source: OrientedFragment,
    sequence: str,
    preparation: str = "pcr",
) -> AssemblyFragment:
    # A PCR product is blunt; a supplied ready fragment keeps its retained ends.
    keep = preparation == "ready_linear" and sequence == source.sequence
    return AssemblyFragment(
        id=source.id,
        name=source.name,
        sequence=sequence,
        orientation="forward",
        role=source.role,
        left_end=source.left_end if keep else None,
        right_end=source.right_end if keep else None,
        metadata={"source_orientation": source.orientation},
    )


SHORT_FRAGMENT = 40
MIN_HOMOLOGY = 15


class _StockAssemblerRefused(AssemblyError):
    """The stock pydna path could not design or enumerate this target (repeats make its overlap
    search ambiguous). design_gibson answers it with the overlap planner instead of refusing."""


PLANNER_FALLBACK_NOTE = (
    "pydna's own assembler could not resolve this target unambiguously (repeated sequence), so the "
    "overlap planner designed the primers; the design was still validated independently by simulate_gibson"
)


def _existing_homology(left: str, right: str) -> int:
    """Longest exact terminal overlap (>= 15 nt, <= 80) a pair already shares."""
    top = min(len(left) - 1, len(right) - 1, 80)
    for length in range(top, MIN_HOMOLOGY - 1, -1):
        if left[-length:] == right[:length]:
            return length
    return 0


def _site(sequence: str, annealing: str, *, reverse: bool, circular: bool) -> dict[str, int] | None:
    sites = _resolve_primer_binding_sites_canonical(
        sequence, annealing, reverse, circular, "dna", len(annealing)
    )
    if not sites:
        return None
    return {"start": sites[0].start, "end": sites[0].start + sites[0].anneal_length,
            "strand": -1 if reverse else 1}


JUNCTION_DISTRIBUTIONS = ("split", "left", "right")


def _resolve_junction_specs(
    oriented: list[OrientedFragment],
    circular: bool,
    junctions: list[JunctionSpec] | None,
) -> dict[int, tuple[int | None, str | None]]:
    """Map the user's junction choices onto fragment indices; only real choices are returned."""
    if not junctions:
        return {}
    by_id = {item.id: index for index, item in enumerate(oriented)}
    parts = [index for index, item in enumerate(oriented) if len(item.sequence) > SHORT_FRAGMENT]
    resolved: dict[int, tuple[int | None, str | None]] = {}
    seen: set[str] = set()
    for spec in junctions:
        index = by_id.get(spec.after_fragment_id)
        if index is None:
            raise AssemblyError(f"Junction after '{spec.after_fragment_id}': no fragment has that id")
        name = oriented[index].name
        if spec.after_fragment_id in seen:
            raise AssemblyError(f"Junction after '{name}' is given more than once")
        seen.add(spec.after_fragment_id)
        if index not in parts:
            raise AssemblyError(
                f"'{name}' is {len(oriented[index].sequence)} nt, so it is carried as a linker on its neighbours' "
                f"primers; set the junction on the fragment before the linker instead"
            )
        if not circular and parts.index(index) == len(parts) - 1:
            raise AssemblyError(f"There is no junction after '{name}': it is the last fragment of a linear assembly")
        if spec.overlap is not None and not 15 <= spec.overlap <= 80:
            raise AssemblyError(f"Junction after '{name}': overlap must be between 15 and 80 nt")
        if spec.distribution is not None and spec.distribution not in JUNCTION_DISTRIBUTIONS:
            raise AssemblyError(
                f"Junction after '{name}': distribution must be one of {', '.join(JUNCTION_DISTRIBUTIONS)}"
            )
        if spec.overlap is not None or spec.distribution is not None:
            resolved[index] = (spec.overlap, spec.distribution)
    return resolved


def _recut_requested(oriented: list[OrientedFragment], circular: bool,
                     specs: dict[int, tuple[int | None, str | None]]) -> RecutResult:
    """Move the ends of BMS-synthesised fragments so each join the user gave an overlap has that overlap."""
    parts = [index for index, item in enumerate(oriented) if len(item.sequence) > SHORT_FRAGMENT]
    requests = [
        (a, parts[(parts.index(a) + 1) % len(parts)], wanted)
        for a, (wanted, _distribution) in sorted(specs.items()) if wanted is not None
    ]
    return recut_synthetic(oriented, [is_synthetic(item) for item in oriented], requests, circular, _existing_homology)


def _apply_recut_notes(info, specs, recut: RecutResult) -> None:
    """Say what happened at joins whose overlap the user set and the fragments already shared."""
    for pair, (kind, length, span, _note) in list(info.items()):
        a = pair[0]
        if kind != "homology" or a not in specs or specs[a][0] is None:
            continue
        if a in recut.refused:
            info[pair] = (kind, length, span, recut.refused[a])
        elif a in recut.done:
            info[pair] = (kind, length, span, "; ".join(recut.done[a]))
        elif specs[a][0] == length:
            info[pair] = (kind, length, span, None)


def _custom_plan(oriented, preparations, circular, overlap, specs=None):
    """Tails only for junctions that lack homology; short parts ride on neighbours.

    ``specs`` maps a part index to the user's (overlap, distribution) for the join after it.
    Returns (part indices, effective sequences, forward tails, reverse tails (as written on the
    primer), junction gaps, and per-junction (distribution, overlap, span, note) keyed by the
    (left part, right part) pair).
    """
    specs = specs or {}
    eff = [effective_sequence(item)[0] for item in oriented]
    parts = [i for i, item in enumerate(oriented) if len(item.sequence) > SHORT_FRAGMENT]
    if not parts:
        raise AssemblyError("Gibson design needs at least one fragment longer than 40 nt")
    fwd = {i: "" for i in parts}
    rev = {i: "" for i in parts}
    junctions = []
    info: dict[tuple[int, int], tuple[str, int, int, str | None]] = {}
    count = len(parts)
    pairs = [(k, (k + 1) % count) for k in range(count if circular else count - 1)]
    for k, m in pairs:
        a, b = parts[k], parts[m]
        gap_indices = (range(a + 1, b) if b > a else list(range(a + 1, len(oriented))) + list(range(0, b)))
        gap = "".join(eff[g] for g in gap_indices)
        homology = 0 if gap else _existing_homology(eff[a], eff[b])
        junctions.append((a, b, gap, homology))
        user = a in specs
        chosen, distribution = specs.get(a, (None, None))
        if homology:
            note = (
                f"The fragments already share {homology} nt of terminal homology, so the overlap you set "
                f"for this junction was not applied"
            ) if user else None
            info[(a, b)] = ("homology", homology, homology, note)
            continue
        ov = chosen or overlap
        half = -(-ov // 2)
        pa, pb = preparations[a] == "pcr", preparations[b] == "pcr"
        where = f"Junction {oriented[a].name} → {oriented[b].name}"
        if a == b:
            if not pa:
                raise AssemblyError("Single-fragment closure without terminal homology needs pcr preparation")
            if user and len(eff[a]) <= ov:
                raise AssemblyError(f"{where}: an overlap of {ov} nt is not shorter than the fragment ({len(eff[a])} nt)")
            if gap:
                if user and distribution in ("left", "right"):
                    raise AssemblyError(f"{where}: with linkers around a single fragment only 'split' is available")
                fwd[a] = (eff[a] + gap)[-ov:]
                rev[a] = reverse_complement(gap[:half])
                distribution = "split"
            elif distribution == "left":
                rev[a] = reverse_complement(eff[a][:ov])
            elif distribution == "right":
                fwd[a] = eff[a][-ov:]
            else:
                fwd[a] = eff[a][-(ov // 2):]
                rev[a] = reverse_complement(eff[a][:half])
                distribution = "split"
        else:
            if distribution is None:
                if pa and pb:
                    distribution = "split"
                elif pb:
                    distribution = "right"
                elif pa:
                    distribution = "left"
                else:
                    raise AssemblyError(
                        "pydna cannot design an overlap between adjacent ready_linear fragments "
                        f"'{oriented[a].name}' and '{oriented[b].name}' that share no terminal homology; "
                        "at least one fragment at each junction must use pcr preparation"
                    )
            elif distribution == "split" and not (pa and pb):
                lacking = oriented[b].name if pa else oriented[a].name
                raise AssemblyError(f"{where}: 'split' needs both fragments to be PCR products; {lacking} is ready_linear")
            elif distribution == "left" and not pa:
                raise AssemblyError(
                    f"{where}: 'left' puts the overlap on the reverse primer of {oriented[a].name}, "
                    f"but {oriented[a].name} is ready_linear"
                )
            elif distribution == "right" and not pb:
                raise AssemblyError(
                    f"{where}: 'right' puts the overlap on the forward primer of {oriented[b].name}, "
                    f"but {oriented[b].name} is ready_linear"
                )
            if user:
                left_need = ov if distribution == "right" else ov // 2 if distribution == "split" else 0
                right_need = ov if distribution == "left" else half if distribution == "split" else 0
                if left_need >= len(eff[a]) or right_need >= len(eff[b]):
                    raise AssemblyError(
                        f"{where}: an overlap of {ov} nt is not shorter than the fragment that has to carry it"
                    )
            if distribution == "split":
                fwd[b] = eff[a][-(ov // 2):] + gap
                rev[a] = reverse_complement(gap + eff[b][:half])
            elif distribution == "right":
                fwd[b] = eff[a][-ov:] + gap
            else:
                rev[a] = reverse_complement(gap + eff[b][:ov])
        info[(a, b)] = (distribution, ov, ov + (len(gap) if distribution == "split" else 0), None)
    return parts, eff, fwd, rev, junctions, info


def _custom_product(parts, eff, junctions, circular):
    first = parts[0]
    seq = eff[first]
    by_pair = {(a, b): (gap, h) for a, b, gap, h in junctions}
    for prev, cur in zip(parts, parts[1:]):
        gap, h = by_pair[(prev, cur)]
        seq += eff[cur][h:] if h else gap + eff[cur]
    if circular:
        gap, h = by_pair[(parts[-1], first)]
        seq = seq[:-h] if h else seq + gap
    return seq


def _needs_custom_design(oriented, preparations, circular) -> bool:
    count = len(oriented)
    if count == 1 or any(len(item.sequence) <= SHORT_FRAGMENT for item in oriented):
        return True
    eff = [effective_sequence(item)[0] for item in oriented]
    if any(item.left_end or item.right_end for item in oriented):
        return True
    pairs = [(i, (i + 1) % count) for i in range(count if circular else count - 1)]
    if any(preparations[i] == preparations[j] == "ready_linear" for i, j in pairs):
        return True
    return any(_existing_homology(eff[i], eff[j]) for i, j in pairs)


def _custom_amplicons(oriented, preparations, circular, overlap, target_tm, min_anneal, specs=None):
    """Design path for overlap-bearing, short, single and sticky-end inputs."""
    parts, eff, fwd, rev, junctions, info = _custom_plan(oriented, preparations, circular, overlap, specs)
    entries = {}
    for i in parts:
        if preparations[i] != "pcr":
            continue
        core = eff[i]
        record = primer_design(Dseqrecord(core, circular=False), limit=min_anneal, target_tm=target_tm)
        f_anneal = str(record.forward_primer.footprint).upper()
        r_anneal = str(record.reverse_primer.footprint).upper()
        f_full, r_full = fwd[i] + f_anneal, rev[i] + r_anneal
        entries[i] = (
            fwd[i] + core + reverse_complement(rev[i]), (f_full, f_anneal), (r_full, r_anneal),
        )
    product = _custom_product(parts, eff, junctions, circular)
    return parts, eff, entries, product, info


def design_gibson(
    fragments: list[AssemblyFragment],
    *,
    preparations: list[FragmentPreparation],
    circular: bool,
    overlap: int = 28,
    target_tm: float = 60.0,
    min_anneal: int = 15,
    max_candidates: int = MAX_CANDIDATES,
    junctions: list[JunctionSpec] | None = None,
    planner_fallback: bool = True,
) -> GibsonDesignResult:
    """Design and independently validate one bounded ordered Gibson assembly.

    ``junctions`` optionally sets the overlap length and which primers carry it for the join after
    named fragments; every other join follows ``overlap``. A target the stock pydna assembler cannot
    resolve (repeats) is designed by the overlap planner instead of being refused, unless
    ``planner_fallback`` is False (Suggest chooses its own repeat-aware breakpoints and keeps the refusal).
    """
    arguments = dict(
        preparations=preparations, circular=circular, overlap=overlap, target_tm=target_tm,
        min_anneal=min_anneal, max_candidates=max_candidates, junctions=junctions,
    )
    try:
        return _design_gibson(fragments, **arguments)
    except _StockAssemblerRefused as refusal:
        if not planner_fallback:
            raise AssemblyError(str(refusal)) from None
        try:
            return _design_gibson(fragments, **arguments, force_custom=True, note=PLANNER_FALLBACK_NOTE)
        except AssemblyError as exc:
            raise AssemblyError(f"{refusal}; the overlap planner could not design it either: {exc}") from None


def _design_gibson(
    fragments: list[AssemblyFragment],
    *,
    preparations: list[FragmentPreparation],
    circular: bool,
    overlap: int,
    target_tm: float,
    min_anneal: int,
    max_candidates: int,
    junctions: list[JunctionSpec] | None,
    force_custom: bool = False,
    note: str | None = None,
) -> GibsonDesignResult:
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

    specs = _resolve_junction_specs(oriented, circular, junctions)
    supplied = oriented
    recut: RecutResult | None = None
    if any(chosen is not None for chosen, _distribution in specs.values()):
        recut = _recut_requested(oriented, circular, specs)
        oriented = recut.fragments
    custom = force_custom or bool(specs) or _needs_custom_design(oriented, preparations, circular)
    pydna_products = []
    info: dict[tuple[int, int], tuple[str, int, int, str | None]] = {}
    entries: dict[int, tuple] = {}
    custom_product = ""
    parts = list(range(len(oriented)))
    if custom:
        try:
            parts, _eff, entries, custom_product, info = _custom_amplicons(
                oriented, preparations, circular, overlap, target_tm, min_anneal, specs
            )
        except AssemblyError:
            raise
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise AssemblyError(f"Gibson design failed: {detail}") from None
        if recut is not None:
            _apply_recut_notes(info, specs, recut)
        designed_seq = {
            i: (entries[i][0] if i in entries else oriented[i].sequence) for i in parts
        }
    else:
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
                raise _StockAssemblerRefused(
                    "pydna could not preserve every ordered source fragment"
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
            raise _StockAssemblerRefused(f"pydna Gibson design failed: {detail}") from None
        designed_seq = {}
        for i, designed in enumerate(pydna_designed):
            designed_seq[i] = str(designed.seq).upper()
            if preparations[i] == "pcr":
                entries[i] = (
                    designed_seq[i],
                    (str(designed.forward_primer.seq).upper(), str(designed.forward_primer.footprint).upper()),
                    (str(designed.reverse_primer.seq).upper(), str(designed.reverse_primer.footprint).upper()),
                )

    primers: list[GeneratedPrimer] = []
    designed_fragments: list[DesignedFragment] = []
    validation_inputs: list[AssemblyFragment] = []
    warnings: list[str] = [note] if note else []
    if recut is not None and recut.changed:
        warnings.append(
            f"Overlap re-cut on fragments BMS synthesised ({', '.join(recut.changed)}) to give the overlap you set; "
            f"order the designed sequences, not the ones you started with"
        )
    for index, (source, preparation) in enumerate(zip(oriented, preparations)):
        carried = index not in designed_seq
        designed_sequence = source.sequence if carried else designed_seq[index]
        primer_ids: list[str] = []
        if index in entries:
            _, f_pair, r_pair = entries[index]
            forward = _primer_result(source, "forward", f_pair)
            reverse = _primer_result(source, "reverse", r_pair)
            pair_qc = evaluate_primer_pair_qc(
                forward.full_sequence,
                reverse.full_sequence,
            )
            forward.warnings.extend(pair_qc.warnings)
            reverse.warnings.extend(pair_qc.warnings)
            core = effective_sequence(source)[0]
            for primer, reverse_flag in ((forward, False), (reverse, True)):
                primer.template_site = _site(core, primer.annealing_sequence, reverse=reverse_flag, circular=False)
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
        if not carried:
            validation_inputs.append(_designed_input_fragment(source, designed_sequence, preparation))

    order = [(parts[k], parts[(k + 1) % len(parts)]) for k in range(len(parts) if circular else len(parts) - 1)]
    preferred_overlaps = [specs.get(pair[0], (None, None))[0] for pair in order] if specs else None
    try:
        validated = simulate_gibson(
            validation_inputs,
            circular=circular,
            minimum_overlap=min(overlap, MIN_HOMOLOGY) if custom else overlap,
            preferred_overlap=overlap,
            maximum_overlap=max([80, *(item[2] for item in info.values())]) if custom else overlap,
            preferred_overlaps=preferred_overlaps,
        )
    except AssemblyError as exc:
        raise AssemblyError(
            f"Designed fragments failed independent Gibson validation: {exc}"
        ) from None

    tails = {primer.id: len(primer.tail_sequence) for primer in primers}
    for junction, pair in zip(validated.junctions, order):
        if pair in info and info[pair][0] == "homology":
            junction.distribution = "homology"
            if info[pair][3]:
                junction.notes.append(info[pair][3])
            continue
        carries_left = tails.get(f"{oriented[pair[0]].id}:reverse", 0) > 0
        carries_right = tails.get(f"{oriented[pair[1]].id}:forward", 0) > 0
        junction.distribution = (
            "split" if carries_left and carries_right else "left" if carries_left else "right" if carries_right else None
        )

    intended_sequence = custom_product if custom else "".join(
        fragment.sequence for fragment in oriented
    )
    intended_key = _candidate_sequence(intended_sequence, circular=circular)
    validated_key = _candidate_sequence(validated.sequence, circular=circular)
    if validated_key != intended_key:
        raise AssemblyError(
            "Designed fragments did not reconstruct the intended ordered product"
        )

    for primer in primers:
        primer.product_site = _site(
            intended_sequence, primer.annealing_sequence,
            reverse=primer.direction == "reverse", circular=circular,
        )
    source_provenance = _source_provenance(supplied, preparations)
    for item, original, designed in zip(source_provenance, supplied, oriented):
        if designed.sequence != original.sequence:  # the supplied bases stay on record next to the re-cut ones
            item["recut_sequence_length"] = len(designed.sequence)
            item["recut_sequence_sha256"] = _sha256(designed.sequence)
    selected_product = AssemblyProduct(
        mode="gibson",
        sequence=intended_sequence,
        circular=circular,
        fragments=supplied,
        junctions=validated.junctions,
        warnings=list(dict.fromkeys(warnings + validated.warnings)),
        validation_notes=[
            "Independently validated with services.assembly.gibson.simulate_gibson"
        ],
    )

    if custom:
        ordered_sequences = [intended_key]
    else:
        unique_sequences = {
            _candidate_sequence(str(product.seq.watson), circular=circular)
            for product in pydna_products
        }
        ordered_sequences = sorted(
            unique_sequences,
            key=lambda sequence: (sequence != intended_key, _sha256(sequence)),
        )[:max_candidates]
        if intended_key not in ordered_sequences:
            raise _StockAssemblerRefused(
                "pydna produced no exact candidate for the intended ordered assembly"
            )

    junction_payload = [[oriented[index].id, spec[0], spec[1]] for index, spec in sorted(specs.items())] or None
    candidates: list[GibsonCandidate] = []
    for sequence in ordered_sequences:
        exact_match = sequence == intended_key
        product = (
            selected_product
            if exact_match
            else AssemblyProduct(
                mode="gibson",
                sequence=sequence,
                circular=circular,
                fragments=[],
                junctions=[],
                warnings=[
                    "Alternate pydna product was not independently characterized"
                ],
                validation_notes=[
                    "Alternate product identity enumerated by pydna; fragment and junction provenance unavailable"
                ],
            )
        )
        candidates.append(
            GibsonCandidate(
                checksum=_candidate_checksum(
                    sequence,
                    circular=circular,
                    source_provenance=source_provenance,
                    preparations=preparations,
                    overlap=overlap,
                    target_tm=target_tm,
                    min_anneal=min_anneal,
                    junctions=junction_payload,
                ),
                product=product,
                exact_match=exact_match,
            )
        )

    selected_checksum = _candidate_checksum(
        intended_key,
        circular=circular,
        source_provenance=source_provenance,
        preparations=preparations,
        overlap=overlap,
        target_tm=target_tm,
        min_anneal=min_anneal,
        junctions=junction_payload,
    )
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
        source_provenance=source_provenance,
    )
