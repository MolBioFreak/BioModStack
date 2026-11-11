"""Golden Gate suggestions for supplied parts and imposed standard overhangs.

Both modes reduce to an ordered list of pieces joined at junction slots. A piece is a payload
(source region, optionally preceded by linker bases) carrying a top-axis overhang label on each side.
The labels are ADDED to the payload as PCR/synthesis tails (scarring), unlike the scarless target split.
Fixed labels (destination-vector closure ends, per-part overhangs, the user's standard set) are kept;
free slots go through the existing joint optimizer. The result is run through the existing
AssembleTask workflow (physical preparation -> real digest -> ligation, scored by the existing
fidelity owner). Everything is advisory: nothing here refuses on score or missing data.
"""
from __future__ import annotations

import itertools
from typing import Callable

from services.restriction_catalog import catalog_authority
from . import golden_gate_fidelity as fidelity
from .common import reverse_complement
from .feature_mapping import map_features, region_positions
from .feature_types import Material, Region
from .golden_gate import resolve_golden_gate_enzyme
from .golden_gate_design import _digest, _scan, preparation_geometry
from .golden_gate_design_types import (
    DesignMaterial, DonorPreparation, EnzymeBinding, InlineSource, PCRPreparation, Part, Source,
    SynthesisPreparation, Tail, Target,
)
from .golden_gate_suggest import (
    DATASET_BY_ENZYME, NO_DATA, EnzymeReport, PartSpec, Proposal, SuggestRequest, SuggestResult,
    _windows, default_retained_fragment_index,
)
from .golden_gate_suggest_prepared import PartReport, PreparedInfo, classify_part, order_by_overhangs, order_rows, order_summary
from .golden_gate_workflow import material_for, run_workflow
from .golden_gate_workflow_types import AssembleTask, CutWindow, FidelitySettings
from .types import AssemblyError


class _Piece:
    def __init__(self, source: Source, material: Material, region: Region, orientation: str, name: str,
                 left: str | None = None, right: str | None = None, linker: str = "",
                 prepared: PreparedInfo | None = None, index: int = 0):
        self.source, self.material, self.region, self.orientation, self.name = source, material, region, orientation, name
        self.left, self.right, self.linker = left, right, linker.upper()
        self.prepared, self.index = prepared, index

    def payload(self) -> str:
        if self.prepared is not None:
            return reverse_complement(self.prepared.payload) if self.orientation == "reverse" else self.prepared.payload
        positions = region_positions(self.region, self.material)
        text = "".join(self.material.sequence[p] for p in positions)
        return self.linker + (reverse_complement(text) if self.orientation == "reverse" else text)


def _region(material: Material, window: CutWindow | None) -> Region:
    n = len(material.sequence)
    if window is None:
        return Region(start=0, end=n)
    return Region(start=window.start, end=window.end, wraps_origin=material.topology == "circular" and window.end <= window.start)


def _cut_positions(n: int, circular: bool, request: SuggestRequest, width: int) -> list[int]:
    windows, fixed = _windows(n, circular, width, request, [(w.start, w.end) for w in request.protected_regions])
    protected = [(w.start, w.end) for w in request.protected_regions]
    blocked = lambda p: any(a <= p < b for a, b in protected)
    cuts = []
    for window, f in zip(windows, fixed):
        p = f if f is not None else (window.start + window.end) // 2
        if f is None and blocked(p):
            p = next((q for d in range(1, n) for q in (p - d, p + d) if 0 < q < n and not blocked(q)), p)
        cuts.append(p)
    if circular and not request.fixed_boundaries and not blocked(0):
        cuts[0] = 0          # first piece starts at the target origin, not at the middle of an even-spacing window
    return sorted(set(cuts))


def _target_pieces(request: SuggestRequest, material: Material, vector: bool, width: int) -> list[_Piece]:
    n, circular = len(material.sequence), material.topology == "circular" and not vector
    cuts = _cut_positions(n, circular, request, width)
    starts = cuts if circular else [0, *[c for c in cuts if c > 0]]
    pieces = []
    for i, start in enumerate(starts):
        last = i + 1 == len(starts)
        end = starts[(i + 1) % len(starts)] if circular else (n if last else starts[i + 1])
        wraps = circular and end <= start
        pieces.append(_Piece(request.target, material, Region(start=start, end=end, wraps_origin=wraps),
                             "forward", f"Target part {i + 1}"))
    return pieces


def _linkers(request: SuggestRequest, pieces: list[_Piece]) -> None:
    if len(request.linkers) > len(pieces):
        raise AssemblyError("More linkers than pieces")
    for piece, linker in zip(pieces, request.linkers):
        if piece.prepared is None:        # a pre-configured part is used exactly as supplied
            piece.linker = linker.upper()


def _piece_source(piece: _Piece, index: int) -> tuple[Source, Region, str]:
    """The Source/Region/orientation the physical design needs. Linker bases are prepended to the
    oriented payload in a derived inline source (features follow through the shared mapper)."""
    if not piece.linker:
        return piece.source, piece.region, piece.orientation
    strand = -1 if piece.orientation == "reverse" else 1
    positions = region_positions(piece.region, piece.material)
    if strand == -1:
        positions.reverse()
    coords = [None] * len(piece.linker) + positions
    features = map_features(piece.material.features, piece.material, coords, strand)
    sequence = piece.payload()
    source = Source(id=f"{piece.source.id}:piece-{index + 1}",
                    source=InlineSource(sequence=sequence, topology="linear", features=features))
    return source, Region(start=0, end=len(sequence)), "forward"


def _vector_ends(request: SuggestRequest, vector_material: Material, enzyme, resolve_revision):
    prepared = DesignMaterial(id="vector", stage="source", transformation="source",
                              sequence=vector_material.sequence, topology=vector_material.topology)
    fragments = _digest(prepared, enzyme).fragments
    if request.vector_region is not None:
        index = default_retained_fragment_index(vector_material, enzyme, (request.vector_region.start, request.vector_region.end))
    else:
        index = max(range(len(fragments)), key=lambda i: len(fragments[i].top_strand_sequence), default=None)
    if index is None or fragments[index].topology == "circular":
        raise AssemblyError("The destination vector has no digest fragment to keep with this enzyme")
    fragment = fragments[index]
    left, right = fragment.left_end, fragment.right_end
    if left.kind != "five_prime_overhang" or right.kind != "five_prime_overhang":
        raise AssemblyError("The kept vector fragment must have two cohesive (5' overhang) ends")
    return index, [f.fragment_index for f in fragments], left.overhang_sequence_5to3, reverse_complement(right.overhang_sequence_5to3)


def _slots(request: SuggestRequest, pieces: list[_Piece], vector_labels, circular: bool, width: int):
    """Slot i is the junction left of piece i; the last slot closes on slot 0 (circular, no vector) or is the
    right end (linear / vector). Returns (fixed: {slot: (label, origin)}, n_slots, right_slot_of(i))."""
    k = len(pieces)
    wrap = circular and vector_labels is None
    n_slots = k if wrap else k + 1
    right_of = (lambda i: (i + 1) % k) if wrap else (lambda i: i + 1)
    fixed: dict[int, tuple[str, str]] = {}

    def put(slot, label, origin):
        label = label.upper()
        if len(label) != width:
            raise AssemblyError(f"Overhang {label} must be {width} nt for this enzyme")
        if slot in fixed and fixed[slot][0] != label:
            raise AssemblyError(f"Junction {slot + 1} is given as both {fixed[slot][0]} and {label}")
        fixed.setdefault(slot, (label, origin))

    if vector_labels is not None:
        put(0, vector_labels[1], "vector")
        put(k, vector_labels[0], "vector")
    for i, piece in enumerate(pieces):
        if piece.left:
            put(i, piece.left, "prepared" if piece.prepared else "user")
        if piece.right:
            put(right_of(i), piece.right, "prepared" if piece.prepared else "user")
    free = [s for s in range(n_slots) if s not in fixed]
    standard = request.standard_overhangs or []
    if len(standard) > len(free):
        raise AssemblyError(f"{len(standard)} standard overhangs given for {len(free)} free junctions")
    for slot, label in zip(free, standard):
        put(slot, label, "standard")
    return fixed, n_slots, right_of


def _topology(request: SuggestRequest, pieces: list[_Piece], vector_material) -> str:
    if vector_material is not None:
        return "circular"
    return pieces[0].material.topology if not request.parts else ("circular" if request.circular else "linear")


def _supplied_piece(index: int, spec: PartSpec, material: Material, enzyme, enzyme_id: str, geometry, reports: list[PartReport]) -> _Piece:
    name = spec.name or spec.source.id
    report, info = classify_part(index=index, name=name, material=material, enzyme=enzyme, enzyme_id=enzyme_id,
                                 geometry=geometry, requested=spec.kind, retained_index=spec.retained_fragment_index)
    left, right = spec.left_overhang, spec.right_overhang
    if info is not None:
        problem = None
        if spec.region is not None:
            problem = "A pre-configured part is used whole: remove the region, or mark the part as bare DNA."
        left, right = info.left, info.right
        if spec.orientation == "reverse":
            left, right = reverse_complement(right), reverse_complement(left)
        for given, carried, side in ((spec.left_overhang, left, "left"), (spec.right_overhang, right, "right")):
            if given and given.upper() != carried:
                problem = f"The {side} overhang is given as {given.upper()} but the part carries {carried}."
        if problem:
            report, info = report.model_copy(update=dict(kind="invalid", message=problem)), None
    reports.append(report)
    return _Piece(spec.source, material, _region(material, spec.region), spec.orientation, name, left, right,
                  prepared=info, index=index)


def _pieces(request: SuggestRequest, resolve_revision, vector_given: bool, width: int, *, enzyme=None, enzyme_id: str = "",
            geometry=None, reports: list[PartReport] | None = None) -> list[_Piece]:
    if request.parts:
        mine: list[PartReport] = [] if reports is None else reports
        pieces = [_supplied_piece(i, spec, material_for(spec.source, resolve_revision), enzyme, enzyme_id, geometry, mine)
                  for i, spec in enumerate(request.parts)]
        bad = [r for r in mine if r.kind in ("ambiguous", "invalid")]
        if bad:
            raise AssemblyError(" ".join(f"{r.name}: {r.message}" for r in bad))
    else:
        material = material_for(request.target, resolve_revision)
        pieces = _target_pieces(request, material, vector_given, width)
    _linkers(request, pieces)
    return pieces


def suggest_parts(request: SuggestRequest, *, resolve_revision: Callable[[str], Material] | None = None) -> SuggestResult:
    if not request.parts and request.target is None:
        raise AssemblyError("Give parts, or a target with standard_overhangs")
    catalog = catalog_authority.require()
    vector_material = material_for(request.vector, resolve_revision) if request.vector else None
    reports, found, diagnostics, part_reports = [], [], [], []
    for name in request.enzymes:
        try:
            enzyme = resolve_golden_gate_enzyme(enzyme_id=name, catalog_id=catalog.catalog_id, expected_catalog_sha256=catalog.content_sha256)
            geometry = preparation_geometry(enzyme)
        except AssemblyError as exc:
            reports.append(EnzymeReport(enzyme_id=name, name=name, empirical=NO_DATA, status=f"not usable: {exc}"))
            continue
        dataset = DATASET_BY_ENZYME.get(enzyme.name)
        report = dict(enzyme_id=name, name=enzyme.name, spacer_length=geometry.spacer_length,
                      overhang_length=geometry.overhang_length, empirical=dataset or NO_DATA)
        try:
            mine: list[PartReport] = []
            try:
                pieces = _pieces(request, resolve_revision, vector_material is not None, geometry.overhang_length,
                                 enzyme=enzyme, enzyme_id=name, geometry=geometry, reports=mine)
            finally:
                part_reports += mine
            report["internal_sites"] = sites = sum(len(_scan(p.payload(), "linear", enzyme).occurrences) for p in pieces)
            made = _for_enzyme(request, enzyme, name, geometry, dataset, pieces, vector_material, sites, resolve_revision)
            found += made
            reports.append(EnzymeReport(**report, status=f"{len(made)} proposal(s)"))
        except (AssemblyError, ValueError) as exc:
            reports.append(EnzymeReport(**report, status=f"no proposal: {exc}"))
    found.sort(key=lambda x: x[:3])
    proposals = [p.model_copy(update={"rank": i + 1}) for i, (*_, p) in enumerate(found[:request.proposals])]
    if not proposals:
        diagnostics.append("No enzyme produced a design for these parts and constraints; see per-enzyme status")
    return SuggestResult(proposals=proposals, enzymes=reports, diagnostics=diagnostics, part_reports=part_reports)


def _seam_offenders(enzyme, payloads: list[str], labels: dict[int, str], wrap: bool) -> set[int]:
    """Slots whose label forms a recognition site with the payload bases beside it in the product.

    Slot s sits between payload s-1 and payload s (wrapping when the product is a ring without a vector).
    """
    count = len(payloads)
    reach = max(len(enzyme.site) - 1, 1)
    offenders = set()
    for slot, label in labels.items():
        left = payloads[(slot - 1) % count][-reach:] if wrap or slot > 0 else ""
        right = payloads[slot % count][:reach] if wrap or slot < count else ""
        window = (left + label + right).upper()
        start, end = len(left), len(left) + len(label)
        if any(o.site_start < end and o.site_end_unwrapped > start for o in _scan(window, "linear", enzyme).occurrences):
            offenders.add(slot)
    return offenders


def _for_enzyme(request, enzyme, name, geometry, dataset, pieces, vector_material, sites, resolve_revision):
    width = geometry.overhang_length
    vector_info = _vector_ends(request, vector_material, enzyme, resolve_revision) if vector_material is not None else None
    topology = _topology(request, pieces, vector_material)
    ordering_notes: list[str] = []
    if request.parts and request.order == "auto" and len(pieces) > 1:
        if any(p.left is None or p.right is None for p in pieces):
            raise AssemblyError("Automatic ordering needs every part to carry overhangs (pre-configured parts, or overhangs given per part)")
        ring = topology == "circular" and vector_material is None
        order, found_orders = order_by_overhangs([(p.left, p.right) for p in pieces], [p.name for p in pieces], circular=ring,
                                                 start=vector_info[2] if vector_info else None,
                                                 end=vector_info[3] if vector_info else None,
                                                 flippable=[p.prepared is not None for p in pieces])
        arranged = []
        for i, flipped in order:
            piece = pieces[i]
            if flipped:
                piece.left, piece.right = reverse_complement(piece.right), reverse_complement(piece.left)
                piece.orientation = "forward" if piece.orientation == "reverse" else "reverse"
                ordering_notes.append(f"{piece.name} was used as its reverse complement so that its overhangs chain.")
            arranged.append(piece)
        pieces = arranged
        if found_orders > 1:
            ordering_notes.append("More than one order joins these overhangs; the first valid order was used. Check the order or fix more overhangs.")
    elif request.parts and len(pieces) > 1 and all(p.prepared is not None for p in pieces):
        for a, b in zip(pieces, pieces[1:] + ([pieces[0]] if topology == "circular" and vector_material is None else [])):
            if a.right != b.left:
                raise AssemblyError(f"{a.name} ends with {a.right} but {b.name} starts with {b.left}; reorder the parts or choose automatic order")
    fixed, n_slots, right_of = _slots(request, pieces, vector_info[2:4] if vector_info else None, topology == "circular", width)
    free = [s for s in range(n_slots) if s not in fixed]
    settings = fidelity.SearchSettings(seed=request.seed, alternatives=request.proposals,
                                       ranking_mode="empirical" if dataset else "lexicographic")
    if free:
        locked = [fixed[s][0] for s in sorted(fixed)]
        payloads = [piece.payload() for piece in pieces]
        wrap = topology == "circular" and vector_material is None
        excluded: set[str] = set()
        while True:
            result = fidelity.optimize_overhangs(["".join(x) for x in itertools.product("ACGT", repeat=width)], n_slots,
                                                 dataset, end_length=width, fixed=locked, excluded=sorted(excluded),
                                                 settings=settings)
            if not result["solutions"]:
                raise AssemblyError("; ".join(result["diagnostics"]) or "optimizer found no junction set with the fixed overhangs")
            assignments, blocked = [], set()
            for solution in result["solutions"]:
                labels = {s: fixed[s][0] for s in fixed} | dict(zip(free, solution["junctions"][len(locked):]))
                bad = _seam_offenders(enzyme, payloads, {s: labels[s] for s in free}, wrap)
                if bad:
                    blocked |= {labels[s] for s in bad}
                else:
                    assignments.append((labels, solution.get("f_set"), solution.get("log_f_set")))
            if not blocked:
                break
            # a label that would form a recognition site with its neighbours cannot be ligated cleanly: search again without it
            excluded |= blocked
    else:
        assignments = [({s: fixed[s][0] for s in fixed}, None, None)]
    from .golden_gate_suggest import _preparation_tails
    _, spacer = _preparation_tails(request, enzyme, geometry)
    binding = EnzymeBinding(enzyme_id=name, catalog_id=catalog_authority.require().catalog_id,
                            catalog_sha256=catalog_authority.require().content_sha256)
    made = []
    for labels, f_set, log_f in assignments:
        made.append(_proposal(request, enzyme, name, binding, dataset, pieces, vector_material, vector_info, labels,
                              fixed, n_slots, right_of, spacer, topology, sites, log_f, resolve_revision, ordering_notes))
    return made


def _proposal(request, enzyme, name, binding, dataset, pieces, vector_material, vector_info, labels, fixed, n_slots,
              right_of, spacer, topology, sites, log_f, resolve_revision, ordering_notes=()):
    sources: dict[str, Source] = {}
    parts: list[Part] = []
    if vector_material is not None:
        index, all_indices, _, _ = vector_info
        sources[request.vector.id] = request.vector
        parts.append(Part(id="vector", source_id=request.vector.id, name="Destination vector", role="backbone",
                          preparation=DonorPreparation(retained_fragment_index=index,
                                                       removed_fragment_indices=[i for i in all_indices if i != index])))
    payload_lengths = {}
    for i, piece in enumerate(pieces):
        source, region, orientation = _piece_source(piece, i)
        sources.setdefault(source.id, source)
        if piece.prepared is not None:
            preparation = DonorPreparation(retained_fragment_index=piece.prepared.retained_fragment_index,
                                           removed_fragment_indices=piece.prepared.removed_fragment_indices)
        else:
            kwargs = dict(region=region, left=Tail(clamp=request.clamp, spacer=spacer, fusion=labels[i]),
                          right=Tail(clamp=request.clamp, spacer=spacer, fusion=labels[right_of(i)]))
            anneal = min(20, len(piece.payload()))
            preparation = (PCRPreparation(**kwargs, forward_anneal_length=anneal, reverse_anneal_length=anneal)
                           if request.preparation_kind == "pcr" else SynthesisPreparation(**kwargs))
        parts.append(Part(id=f"part-{i + 1}", source_id=source.id, name=piece.name, orientation=orientation, preparation=preparation))
        payload_lengths[f"part-{i + 1}"] = len(piece.payload())
    three_prime = preparation_geometry(enzyme).polarity == "three_prime"
    expected = None if vector_material is not None else "".join(
        piece.payload() + labels[right_of(i)] if three_prime and topology == "linear" else labels[i] + piece.payload()
        for i, piece in enumerate(pieces))
    origin = len(expected) - len(labels[0]) if expected is not None and three_prime and topology == "circular" else 0
    task = AssembleTask(sources=list(sources.values()), parts=parts, enzyme=binding,
                        target=Target(topology=topology, display_origin=origin, exact_sequence=expected),
                        fidelity=FidelitySettings(dataset_id=dataset))
    result = run_workflow(task, resolve_revision=resolve_revision)
    candidate = result.solutions[0]
    design = candidate.design
    if not design.solutions:
        raise AssemblyError("; ".join(design.diagnostics) or "the prepared parts do not assemble into a product")
    prepared_rows, pcr = order_rows(design, parts, payload_lengths), []
    for row in prepared_rows:
        prefix, _, number = row["part_id"].partition("-")
        row["input_index"] = pieces[int(number) - 1].index if prefix == "part" and number.isdigit() else None
        if row["primers"]:
            pcr.append(dict(part_id=row["part_id"], tailed_length=row["tailed_length"], payload_length=row["payload_length"]))
    imposed = all(s in fixed for s in range(n_slots))
    slots = []
    for s in range(n_slots):
        origin = fixed[s][1] if s in fixed else "optimizer"
        left_piece = f"part-{s + 1}" if s < len(pieces) else ("vector" if vector_material is not None else None)
        left_of = f"part-{s}" if s > 0 else ("vector" if vector_material is not None else None)
        slots.append(dict(index=s, overhang=labels[s], origin=origin, between=[left_of, left_piece]))
    notes = []
    if vector_material is not None and request.vector_region is None:
        notes.append("Destination vector: the largest digest fragment is kept; give vector_region to choose another")
    if any(p.linker for p in pieces):
        notes.append("payload_length includes linker bases; for PCR the linker must be carried in the primer 5' tail")
    notes.extend(ordering_notes)
    if any(p.prepared is not None for p in pieces):
        notes.append("Pre-configured parts are used exactly as supplied; no flanks, linkers or overhangs were added to them.")
    summary = order_summary(prepared_rows, request.preparation_kind)
    cuts = [p.region.start for p in pieces] if not request.parts else []
    return (sites, 0 if dataset else 1, -(log_f if log_f is not None else (candidate.fidelity.get("log_f_set") or 0)) if dataset else 0,
            Proposal(rank=0, enzyme_id=name, enzyme_name=enzyme.name, empirical=dataset or NO_DATA,
                     ranking_mode="empirical" if dataset else "lexicographic", internal_sites=sites,
                     junction_mode="imposed" if imposed else "chosen", junctions=[labels[s] for s in range(n_slots)],
                     cuts=cuts, fragment_lengths=[len(p.payload()) for p in pieces],
                     f_set=candidate.fidelity.get("f_set"), candidate=candidate, pcr_products=pcr,
                     assemble_request=task.model_dump(mode="json"), junction_slots=slots,
                     prepared_fragments=prepared_rows, final_sequence=design.solutions[0].sequence, notes=notes,
                     part_order=[p.index for p in pieces], order_summary=summary))
