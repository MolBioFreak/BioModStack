"""Pure raw Golden Gate preparation using the existing BMS scientific owners.

No persistence, edits, optimization, external requests or empirical scoring.
PCR/synthesis regions are the payload *between* explicit fusion sequences.
Right fusion labels are on the final top reference axis, not oligo letters.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from typing import Callable, Literal, Sequence

from routers.molbio_ops import calculate_primer_tm_result
from services.molbio_ops import pcr_product, reverse_complement
from services.primer_qc import evaluate_primer_pair_qc, evaluate_primer_qc
from services.restriction_analysis import analyze_sequence
from services.restriction_catalog import catalog_authority
from services.restriction_digest import DigestEnd, DigestFragment, simulate_digest

from .common import overhangs_compatible
from .golden_gate import TypeIISEnzyme, resolve_golden_gate_enzyme, simulate_golden_gate
from .golden_gate_design_types import (
    DesignMaterial, DigestOutcome, Feature, Geometry, GoldenGateDesignRequest,
    GoldenGateDesignResult, InlineSource, Mapping, Material, Part,
    PreparationOutcome, Primer, Region, Solution, SourceScreen,
    PCRPreparation, SynthesisPreparation, PreparedPreparation,
)
from .ligation import simulate_ligation
from .types import AssemblyError, AssemblyFragment, FragmentEnd, EndType

RAW_ENZYMES = ("BsaI", "BsmBI", "Esp3I", "BbsI", "SapI")


def preparation_geometry(enzyme: TypeIISEnzyme) -> Geometry:
    """Derive offsets from catalog authority, never an enzyme-name offset map."""
    event = enzyme.record.cleavage.events[0]
    spacer = event.top_offset - len(enzyme.site)
    if spacer < 0 or event.bottom_offset <= event.top_offset:
        raise AssemblyError("Raw preparation adapter requires downstream five-prime cleavage")
    return Geometry(site=enzyme.site, top_offset=event.top_offset,
                    bottom_offset=event.bottom_offset, spacer_length=spacer,
                    overhang_length=event.overhang_length_nt)


def _positions(region: Region, material: Material) -> list[int]:
    n = len(material.sequence)
    if region.start >= n or region.end > n:
        raise AssemblyError("Selected interval is outside its source")
    if region.wraps_origin:
        if material.topology != "circular" or region.end > region.start:
            raise AssemblyError("Origin-wrapping interval requires a circular source and end <= start")
        return list(range(region.start, n)) + list(range(region.end))
    if region.end <= region.start:
        raise AssemblyError("Selected interval must be nonempty")
    return list(range(region.start, region.end))


def _mappings(positions: Sequence[int | None], parent: str, strand: Literal[-1, 1] = 1) -> list[Mapping]:
    """Compress coordinate runs; synthesized bases have no invented parent slice."""
    runs: list[Mapping] = []
    i = 0
    while i < len(positions):
        first = positions[i]
        if first is None:
            i += 1
            continue
        j = i + 1
        while j < len(positions) and positions[j] == first + (j - i) * strand:
            j += 1
        a, b = first, first + (j - i - 1) * strand
        runs.append(Mapping(output_start=i, output_end=j, parent_id=parent,
                            parent_start=min(a, b), parent_end=max(a, b) + 1, strand=strand))
        i = j
    return runs


def _map_features(features: list[Feature], parent: Material, positions: Sequence[int | None], strand: Literal[-1, 1] = 1) -> list[Feature]:
    lookup: dict[int, list[int]] = {}
    for out, coordinate in enumerate(positions):
        if coordinate is not None:
            lookup.setdefault(coordinate, []).append(out)
    mapped = []
    for feature in features:
        # Match the shared GenBank owner: ordered location parts, each traversed
        # biologically on its own strand. Reverse orientation must not swap exons.
        chunks = []
        for segment in feature.segments:
            coordinates = _positions(segment, parent)
            if segment.wraps_origin:
                split = len(parent.sequence) - segment.start
                chunks.extend((coordinates[:split], coordinates[split:]))
            else:
                chunks.append(coordinates)
        original = [p for chunk in chunks for p in (reversed(chunk) if feature.strand == -1 else chunk)]
        retained_indices = [i for i, p in enumerate(original) if p in lookup]
        retained = list(dict.fromkeys(out for i in retained_indices for out in lookup[original[i]]))
        if not retained:
            continue
        direction = (feature.strand or 1) * strand
        segments = []
        start = previous = retained[0]
        for coordinate in retained[1:]:
            if coordinate != previous + direction:
                segments.append(Region(start=min(start, previous), end=max(start, previous) + 1))
                start = coordinate
            previous = coordinate
        segments.append(Region(start=min(start, previous), end=max(start, previous) + 1))
        intact = len(retained_indices) == len(original)
        internal_loss = any(b != a + 1 for a, b in zip(retained_indices, retained_indices[1:]))
        status = feature.status if intact else "disrupted" if internal_loss or feature.status == "disrupted" else "truncated"
        qualifiers = dict(feature.qualifiers)
        codon_start = feature.codon_start
        if feature.type == "CDS" and not intact:
            if feature.strand:
                old = codon_start if codon_start is not None else qualifiers.get("codon_start", 1)
                if isinstance(old, list):
                    old = old[0] if old else None
                if old in (1, 2, 3, "1", "2", "3"):
                    codon_start = (int(old) - 1 - retained_indices[0]) % 3 + 1
                    if "codon_start" in qualifiers:
                        previous_value = qualifiers["codon_start"]
                        qualifiers["codon_start"] = ([str(codon_start)] if isinstance(previous_value, list)
                            else str(codon_start) if isinstance(previous_value, str) else codon_start)
            # Original translation remains on the source; it no longer describes
            # this truncated/disrupted feature. This annotation never blocks use.
            qualifiers.pop("translation", None)
        mapped.append(feature.model_copy(update={
            "segments": segments, "strand": feature.strand * strand, "status": status,
            "codon_start": codon_start, "qualifiers": qualifiers,
            "frame_preserved": (feature.frame_preserved if feature.frame_preserved is not None else True)
            if intact and feature.type == "CDS" else (False if feature.type == "CDS" else None),
        }))
    return mapped


def _digest_end(value: DigestEnd) -> FragmentEnd:
    if value.kind == "no_cut_circular":
        raise AssemblyError("An uncut circular molecule has no ligatable ends")
    kind: EndType = "sticky_5" if value.kind == "five_prime_overhang" else "sticky_3" if value.kind == "three_prime_overhang" else "blunt"
    return FragmentEnd(type=kind, overhang=value.overhang_sequence_5to3 or "",
                       protruding_strand=value.protruding_strand)


def digest_fragment_to_assembly(fragment: DigestFragment, *, part: Part) -> AssemblyFragment:
    """Public Digest → Assembly adapter preserving physical protruding strands."""
    return AssemblyFragment(id=part.id, name=part.name, role=part.role,
                            sequence=fragment.top_strand_sequence,
                            orientation=part.orientation, circular=fragment.topology == "circular",
                            left_end=_digest_end(fragment.left_end), right_end=_digest_end(fragment.right_end),
                            source_start=fragment.top_start_boundary_normalized,
                            source_end=fragment.top_end_boundary_normalized,
                            source_wraps_origin=fragment.wraps_origin,
                            metadata={"source_segments": fragment.source_segments})


def design_material(result: GoldenGateDesignResult, material_id: str) -> DesignMaterial:
    """Resolve source/preparation/digest references without duplicated wire DNA.

    Persistence/export callers can materialize individual native digest rows
    with their exact intermediate parent and mapped annotations on demand.
    """
    for material in result.materials:
        if material.id == material_id:
            return material
    for digest in result.digests:
        if material_id not in digest.fragment_material_ids:
            continue
        physical = digest.fragments[digest.fragment_material_ids.index(material_id)]
        parent = design_material(result, digest.input_material_id)
        coords = [i % len(parent.sequence) for i in range(physical.top_start_boundary, physical.top_end_boundary)]
        return DesignMaterial(id=material_id, stage="digest", parent_id=parent.id,
            transformation="digest", sequence=physical.top_strand_sequence, topology=physical.topology,
            mappings=_mappings(coords, parent.id), features=_map_features(parent.features, parent, coords),
            left_end=None if physical.topology == "circular" else _digest_end(physical.left_end),
            right_end=None if physical.topology == "circular" else _digest_end(physical.right_end))
    raise KeyError(material_id)


def _scan(sequence: str, topology: Literal["linear", "circular"], enzyme: TypeIISEnzyme):
    return analyze_sequence(sequence=sequence, topology=topology, catalog=enzyme.catalog,
                            records=(enzyme.record,), include_possible_sites=True)


def _digest(material: DesignMaterial, enzyme: TypeIISEnzyme):
    receipt = {k: v for k, v in catalog_authority.readiness().items()
               if k not in {"required", "ready", "status"}}
    receipt["digest_enabled"] = True
    return simulate_digest(
        sequence=material.sequence, topology=material.topology, catalog=enzyme.catalog,
        records=(enzyme.record,), selected_enzyme_ids=(enzyme.enzyme_id,),
        source_receipt={"kind": "inline_dna", "name": material.id, "sequence_id": None,
                        "revision_id": None, "revision_number": None,
                        "content_sha256": sha256(material.sequence.encode("ascii")).hexdigest(),
                        "content_length": len(material.sequence), "topology": material.topology},
        catalog_receipt=receipt, persisted_identity=False,
    )


def design_golden_gate(
    request: GoldenGateDesignRequest,
    *,
    resolve_revision: Callable[[str], Material] | None = None,
) -> GoldenGateDesignResult:
    """Compute fixed selected preparations. Resolve each immutable revision once.

    A resolver must return the exact immutable revision, including its topology
    and annotations. Persistence/source authorization remain at the route owner.
    No score, QC warning, missing vendor recommendation or recut site is a gate.
    """
    enzyme = resolve_golden_gate_enzyme(enzyme_id=request.enzyme.enzyme_id,
        catalog_id=request.enzyme.catalog_id, expected_catalog_sha256=request.enzyme.catalog_sha256)
    geometry = preparation_geometry(enzyme)
    if any(p.preparation.kind in {"pcr", "synthesis"} for p in request.parts) and enzyme.name not in RAW_ENZYMES:
        raise AssemblyError("Raw tail adapter not qualified for this enzyme; prepared mode remains available")
    if not request.parts or len({p.id for p in request.parts}) != len(request.parts):
        raise AssemblyError("Parts must be nonempty and have distinct instance IDs")
    if len({s.id for s in request.sources}) != len(request.sources):
        raise AssemblyError("Source IDs must be distinct")
    materials: list[DesignMaterial] = []
    sources: dict[str, DesignMaterial] = {}
    revision_materials: dict[str, DesignMaterial] = {}
    for source in request.sources:
        if isinstance(source.source, InlineSource):
            value = Material(**source.source.model_dump(exclude={"kind"}))
        else:
            revision = source.source.revision_id
            if revision in revision_materials:
                sources[source.id] = revision_materials[revision]
                continue
            if resolve_revision is None:
                raise AssemblyError("A molecular-revision resolver is required")
            value = Material.model_validate(resolve_revision(revision))
        material = DesignMaterial(**value.model_dump(), id=f"source:{source.id}", stage="source", transformation="source")
        # Validate feature coordinates even if the selected part will drop them.
        for feature in value.features:
            for segment in feature.segments:
                _positions(segment, value)
        sources[source.id] = material
        materials.append(material)
        if not isinstance(source.source, InlineSource):
            revision_materials[source.source.revision_id] = material
    source_screens = []
    for material in materials:
        scan = _scan(material.sequence, material.topology, enzyme)
        source_screens.append(SourceScreen(source_material_id=material.id,
            occurrences=list(scan.occurrences), warnings=list(scan.warnings)))
    primers, outcomes, digests, fragments = [], [], [], []
    retained_rows = []
    diagnostics: list[str] = []
    for part in request.parts:
        if part.source_id not in sources:
            raise AssemblyError(f"Unknown source_id {part.source_id}")
        source = sources[part.source_id]
        prep = part.preparation
        prepared = source
        primer_ids = []
        verification, pcr_diagnostics, pair_qc = "not_applicable", [], None
        if isinstance(prep, PreparedPreparation):
            fragment = AssemblyFragment(id=part.id, name=part.name, sequence=source.sequence,
                orientation=part.orientation, circular=source.topology == "circular", role=part.role,
                left_end=prep.left_end, right_end=prep.right_end)
            fragments.append(fragment)
            retained_rows.append((source, None, part))
            outcomes.append(PreparationOutcome(part_id=part.id, source_material_id=source.id,
                prepared_material_id=source.id, retained_material_id=source.id))
            continue
        expected = None
        if isinstance(prep, (PCRPreparation, SynthesisPreparation)):
            positions = _positions(prep.region, source)
            strand = -1 if part.orientation == "reverse" else 1
            payload = "".join(source.sequence[p] for p in positions)
            if strand == -1:
                payload = reverse_complement(payload)
                positions.reverse()
            for tail in (prep.left, prep.right):
                if len(tail.spacer) != geometry.spacer_length or len(tail.fusion) != geometry.overhang_length:
                    raise AssemblyError("Spacer/fusion lengths disagree with catalog cleavage geometry")
            left_tail = prep.left.clamp + enzyme.site + prep.left.spacer + prep.left.fusion
            right_tail = prep.right.clamp + enzyme.site + prep.right.spacer + reverse_complement(prep.right.fusion)
            sequence = left_tail + payload + reverse_complement(right_tail)
            coords = [None] * len(left_tail) + positions + [None] * len(right_tail)
            prepared = DesignMaterial(id=f"preparation:{part.id}", sequence=sequence, topology="linear",
                stage="prepared", parent_id=source.id, transformation=prep.kind,
                mappings=_mappings(coords, source.id, strand),
                features=_map_features(source.features, source, coords, strand))
            expected = prep.left.fusion + payload
            if prep.kind == "pcr":
                if max(prep.forward_anneal_length, prep.reverse_anneal_length) > len(payload):
                    raise AssemblyError("Primer footprint is longer than selected template region")
                footprints = (payload[:prep.forward_anneal_length], reverse_complement(payload[-prep.reverse_anneal_length:]))
                full = (left_tail + footprints[0], right_tail + footprints[1])
                native_template = source.sequence if strand == 1 else reverse_complement(source.sequence)
                for direction, tail, footprint, oligo in zip(("forward", "reverse"), (prep.left, prep.right), footprints, full):
                    primer_id = f"{part.id}:{direction}"
                    primer_ids.append(primer_id)
                    selected_positions = positions[:len(footprint)] if direction == "forward" else positions[-len(footprint):][::-1]
                    primer_strand = strand if direction == "forward" else -strand
                    primers.append(Primer(id=primer_id, part_id=part.id, direction=direction,
                        full_sequence=oligo, clamp=tail.clamp, recognition_site=enzyme.site,
                        spacer=tail.spacer, fusion=tail.fusion if direction == "forward" else reverse_complement(tail.fusion),
                        annealing_sequence=footprint,
                        tm=calculate_primer_tm_result(footprint, "dna", request.primer_settings),
                        qc=evaluate_primer_qc(oligo, template_sequence=native_template,
                            circular_template=source.topology == "circular",
                            min_binding_anneal_length=prep.qc_min_binding_anneal_length),
                        qc_template_orientation=part.orientation,
                        footprint_mappings=_mappings(selected_positions, source.id, primer_strand)))
                pair_qc = evaluate_primer_pair_qc(*full)
                # Selected interval is explicit, so do not silently use a different
                # full-template native product. Keep native ambiguity as evidence.
                try:
                    amplified = pcr_product(native_template, *full, circular=source.topology == "circular")
                    verification = "verified" if amplified.sequence == sequence else "mismatch"
                    if verification == "mismatch":
                        pcr_diagnostics.append("Native full-template PCR differs from explicitly selected footprint product")
                except ValueError as exc:
                    verification = "ambiguous"
                    pcr_diagnostics.append(str(exc))
                for direction, footprint in zip(("forward", "reverse"), footprints):
                    a = len(left_tail) if direction == "forward" else len(left_tail) + len(payload) - len(footprint)
                    prepared.features.append(Feature(id=f"{part.id}:{direction}:binding", type="primer_bind",
                        name=f"{part.name} {direction}", segments=[Region(start=a, end=a + len(footprint))],
                        strand=1 if direction == "forward" else -1))
            materials.append(prepared)
        simulation = _digest(prepared, enzyme)
        retained_index = prep.retained_fragment_index if prep.kind == "donor" else None
        if expected is not None:
            assert isinstance(prep, (PCRPreparation, SynthesisPreparation))
            matches = [row.fragment_index for row in simulation.fragments
                       if row.top_strand_sequence == expected
                       and row.left_end.overhang_sequence_5to3 == prep.left.fusion
                       and row.right_end.overhang_sequence_5to3 == reverse_complement(prep.right.fusion)]
            if len(matches) == 1:
                retained_index = matches[0]
            else:
                diagnostics.append(f"{part.id}: digest does not contain one intact intended payload; select actual fragments or explicitly edit source")
        valid_indices = {f.fragment_index for f in simulation.fragments}
        removed = prep.removed_fragment_indices
        if not set(removed) <= valid_indices or len(set(removed)) != len(removed):
            raise AssemblyError("Removed digest fragment selection is invalid")
        if retained_index is not None and (retained_index not in valid_indices or retained_index in removed):
            raise AssemblyError("Retained digest fragment selection is invalid or also removed")
        digests.append(DigestOutcome(part_id=part.id, input_material_id=prepared.id,
            fragment_material_ids=[f"digest:{part.id}:{f.fragment_index}" for f in simulation.fragments],
            fragments=list(simulation.fragments), occurrences=list(simulation.occurrences),
            cleavages=list(simulation.cleavages), retained_fragment_index=retained_index,
            removed_fragment_indices=removed,
            background_fragment_indices=sorted(valid_indices - set(removed) - {retained_index}),
            warnings=list(simulation.warnings)))
        retained_id = None
        if retained_index is not None:
            physical = simulation.fragments[retained_index]
            retained_id = f"digest:{part.id}:{retained_index}"
            # Tail-bearing molecules already use the requested orientation.
            assembly_part = part if prep.kind == "donor" else part.model_copy(update={"orientation": "forward"})
            fragment = digest_fragment_to_assembly(physical, part=assembly_part)

            fragments.append(fragment)
            retained_rows.append((prepared, physical, assembly_part))
        outcomes.append(PreparationOutcome(part_id=part.id, source_material_id=source.id,
            prepared_material_id=prepared.id, retained_material_id=retained_id, primer_ids=primer_ids,
            pcr_verification=verification, pcr_diagnostics=pcr_diagnostics, pair_qc=pair_qc))
        diagnostics.extend(f"{part.id}: {message}" for message in pcr_diagnostics)
    solutions = []
    if len(fragments) == len(request.parts):
        circular = request.target.topology == "circular"
        # Historical all-prepared requests keep their existing validation owner.
        if all(p.preparation.kind == "prepared" for p in request.parts) and len(fragments) >= 2:
            product = simulate_golden_gate(fragments, enzyme=enzyme, circular=circular)
        else:
            product = simulate_ligation(fragments, circular=circular, mode="golden_gate")
        origin = request.target.display_origin
        if (not circular and origin) or origin >= len(product.sequence):
            raise AssemblyError("Display origin is outside the circular product")
        product_features, product_mappings = [], []
        offset = 0
        # Map oriented duplex bottom coordinates on reversal, not RC(top).
        for oriented, (parent, physical, part) in zip(product.fragments, retained_rows):
            if physical is None:
                coords = list(range(len(parent.sequence)))
                if part.orientation == "reverse":
                    left, right = part.preparation.left_end, part.preparation.right_end
                    a = len(left.overhang) if left.protruding_strand == "top" else 0
                    b = len(coords) - (len(right.overhang) if right.protruding_strand == "top" else 0)
                    coords = coords[a:b]
                    if left.protruding_strand == "bottom":
                        coords = [None] * len(left.overhang) + coords
                    if right.protruding_strand == "bottom":
                        coords += [None] * len(right.overhang)
            else:
                a, b = (physical.bottom_start_boundary, physical.bottom_end_boundary) if part.orientation == "reverse" else (physical.top_start_boundary, physical.top_end_boundary)
                coords = [i % len(parent.sequence) for i in range(a, b)]
            strand = -1 if part.orientation == "reverse" else 1
            if strand == -1:
                coords.reverse()
            features = _map_features(parent.features, parent, coords, strand)
            for feature in features:
                product_features.append(feature.model_copy(update={"id": f"{part.id}:{feature.id}",
                    "segments": [Region(start=s.start + offset, end=s.end + offset) for s in feature.segments]}))
            for mapping in _mappings(coords, parent.id, strand):
                product_mappings.append(mapping.model_copy(update={"output_start": mapping.output_start + offset, "output_end": mapping.output_end + offset}))
            offset += len(oriented.sequence)
        sequence = product.sequence[origin:] + product.sequence[:origin]
        if origin:
            unrotated = Material(sequence=product.sequence, topology="circular", features=product_features)
            product_features = _map_features(product_features, unrotated, list(range(origin, len(sequence))) + list(range(origin)))
            rotated = []
            for mapping in product_mappings:
                for a, b in ((max(mapping.output_start, origin), mapping.output_end), (mapping.output_start, min(mapping.output_end, origin))):
                    if b <= a:
                        continue
                    start = (a - origin) % len(sequence)
                    pa = mapping.parent_start + a - mapping.output_start if mapping.strand == 1 else mapping.parent_end - (b - mapping.output_start)
                    rotated.append(mapping.model_copy(update={"output_start": start, "output_end": start + b - a,
                        "parent_start": pa, "parent_end": pa + b - a}))
            product_mappings = sorted(rotated, key=lambda m: m.output_start)
        scan = _scan(sequence, request.target.topology, enzyme)
        warnings = list(product.warnings)
        if scan.occurrences:
            warnings.append("Intended product contains residual/recreated recognition sites and may be recut")
        classes = Counter(min(j.overhang_sequence, reverse_complement(j.overhang_sequence)) for j in product.junctions if j.overhang_sequence)
        for overhang, count in classes.items():
            if count > 1 or overhang == reverse_complement(overhang):
                warnings.append(f"Repeated/palindromic junction class {overhang}: structural alternative ligations are possible")
        for fragment in product.fragments:
            if overhangs_compatible(fragment.right_end, fragment.left_end)[0]:
                warnings.append(f"{fragment.id}: ends permit self-ligation")
        exact = None if request.target.exact_sequence is None else sequence == request.target.exact_sequence
        if exact is False:
            warnings.append("Product differs from the requested exact sequence at the selected display origin")
        solutions.append(Solution(id="fixed", sequence=sequence, topology=request.target.topology,
            display_origin=origin, part_material_ids=[row.retained_material_id for row in outcomes],
            mappings=product_mappings, features=product_features, junctions=product.junctions,
            occurrences=list(scan.occurrences), exact_target_match=exact, warnings=list(dict.fromkeys(warnings))))
    return GoldenGateDesignResult(enzyme=request.enzyme, geometry=geometry, primer_settings=request.primer_settings,
        materials=materials, source_screens=source_screens, preparations=outcomes, primers=primers, digests=digests, solutions=solutions,
        selected_solution_id="fixed" if solutions else None, diagnostics=diagnostics,
        limitations=["Only intended fixed-order full-digest product modeled; alternative orders, subsets and partial digestion not enumerated",
            "Reactive background is listed, not silently purified; no kinetic, nick-closure, activity or yield model",
            "No empirical scoring or sequence editing in this preparation service",
            "PCR footprints are explicitly selected; full-template ambiguity/QC is evidence, not a uniqueness claim"])
