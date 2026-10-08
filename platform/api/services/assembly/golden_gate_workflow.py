"""Compose native physical design, empirical search, edits and worksheets locally."""
from __future__ import annotations

from collections.abc import Callable
from services.assembly import golden_gate_fidelity as fidelity
from services.assembly.golden_gate_design import design_golden_gate, preparation_geometry, _positions, _map_features
from services.assembly.golden_gate_design_types import (
    GoldenGateDesignRequest, Material, InlineSource, Source, Part, Region, Target,
    PCRPreparation, SynthesisPreparation, Tail,
)
from services.assembly.golden_gate_workflow_types import (
    WorkflowRequest, WorkflowResult, WorkflowCandidate, AssembleTask, EvaluateTask,
    OptimizeTask, SplitTask, EditOutcome, FrozenSelection,
)
from services.assembly.golden_gate_domestication import propose_domestication, apply_domestication_proposal
from services.assembly.golden_gate_reaction import calculate_reaction
from services.assembly.golden_gate import resolve_golden_gate_enzyme
from services.assembly.common import reverse_complement
from services.assembly.types import AssemblyError


def material_for(source: Source, resolver: Callable[[str], Material] | None) -> Material:
    if isinstance(source.source, InlineSource):
        return Material(**source.source.model_dump(exclude={'kind'}))
    if resolver is None:
        raise AssemblyError('Immutable revision resolver required')
    return resolver(source.source.revision_id)


def _score(design, settings, request):
    parts = {p.id: p for p in request.parts}
    inventory = []
    # Each digest end instance remains visible, including removable background.
    retained = {x.retained_material_id for x in design.preparations}
    junction_by_end = {}
    if design.solutions:
        for i, junction in enumerate(design.solutions[0].junctions):
            junction_by_end[(junction.left_fragment_id, 'right')] = str(i)
            junction_by_end[(junction.right_fragment_id, 'left')] = str(i)
    for digest in design.digests:
        for fragment, material_id in zip(digest.fragments, digest.fragment_material_ids):
            for side in ('left', 'right'):
                end = getattr(fragment, side + '_end')
                inventory.append(fidelity.EndInstance(
                    instance_id=f'{material_id}:{side}', sequence=end.overhang_sequence_5to3,
                    role=(parts[digest.part_id].role or 'insert') if material_id in retained else 'dropout',
                    intended_junction_id=junction_by_end.get((digest.part_id, side)) if material_id in retained else None,
                    polarity=end.kind, removed=fragment.fragment_index in digest.removed_fragment_indices))
    # Prepared inputs have no digest record, but their physical ends still count.
    digested = {d.part_id for d in design.digests}
    materials = {m.id: m for m in design.materials}
    for preparation in design.preparations:
        if preparation.part_id in digested:
            continue
        material = materials[preparation.source_material_id]
        for side in ('left', 'right'):
            end = getattr(parts[preparation.part_id].preparation, side + '_end')
            inventory.append(fidelity.EndInstance(instance_id=f'{material.id}:{preparation.part_id}:{side}',
                sequence=end.overhang if end else None, role='insert',
                intended_junction_id=junction_by_end.get((preparation.part_id, side)),
                polarity=end.type if end else None))
    junctions = [j.overhang_sequence for j in design.solutions[0].junctions if j.overhang_sequence] if design.solutions else []
    return fidelity.evaluate_overhangs(junctions, settings.dataset_id,
        condition_use=settings.condition_use, inventory=inventory,
        inventory_complete=True,
        include_pair_observations=settings.include_pair_observations)


def _reaction(request, design, diagnostics):
    if request is None:
        return None
    from services.assembly.golden_gate_design import design_material
    lengths = {p.part_id: len(design_material(design, p.retained_material_id).sequence)
        for p in design.preparations if p.retained_material_id is not None}
    effective = []
    for part in request.parts:
        length = lengths.get(part.part_id)
        if length is None:
            diagnostics.append(f'{part.part_id}: reaction row has no retained design material; supplied length remains operator evidence')
            effective.append(part)
        else:
            if part.length_bp is not None and part.length_bp != length:
                diagnostics.append(f'{part.part_id}: requested reaction length {part.length_bp} bp differs from retained fragment {length} bp; operator-supplied worksheet length retained')
            effective.append(part if part.length_bp is not None else part.model_copy(update={'length_bp': length}))
    return calculate_reaction(request.model_copy(update={'parts': tuple(effective)}))


def _assemble(request: AssembleTask, resolver):
    edits = []
    sources = list(request.sources)
    seen = set()
    for edit in request.domestication:
        if edit.source_id in seen:
            raise AssemblyError('One domestication request per source is required')
        seen.add(edit.source_id)
        index = next((i for i, s in enumerate(sources) if s.id == edit.source_id), None)
        if index is None:
            raise AssemblyError('Unknown domestication source')
        original = material_for(sources[index], resolver)
        if edit.settings.enabled:
            from services.restriction_catalog import catalog_authority
            catalog = catalog_authority.require()
            for site in edit.settings.unwanted_sites:
                record = catalog.by_id.get(site.enzyme_id)
                if record is None or site.recognition_sequence not in {record.recognition.site_iupac, *record.recognition.site_alternatives_iupac}:
                    raise AssemblyError('Domestication motif does not match the selected catalog enzyme')
        proposal = propose_domestication(original.sequence, settings=edit.settings, circular=original.topology == 'circular')
        accepted = edit.accepted_sequence is not None
        if accepted:
            sequence = apply_domestication_proposal(original.sequence, proposal, accepted=True)
            if sequence != edit.accepted_sequence:
                raise AssemblyError('Accepted sequence does not match the authoritative explicit proposal')
            sources[index] = Source(id=edit.source_id, source=InlineSource(**original.model_copy(update={'sequence': sequence}).model_dump()))
        edits.append(EditOutcome(source_id=edit.source_id, proposal=proposal, accepted=accepted, original=original))
    core = GoldenGateDesignRequest.model_validate(request.model_dump(exclude={'fidelity', 'domestication', 'reaction', 'automatic_primers'}))
    core = core.model_copy(update={'sources': sources})
    diagnostics = [f'{e.source_id}: PCR requires the explicitly accepted edited template; these oligos are not an internal-mutagenesis protocol for the original source'
        for e in edits if e.accepted and any(p.source_id == e.source_id and p.preparation.kind == 'pcr' for p in core.parts)]
    parts = list(core.parts)
    source_map = {s.id: s for s in sources}
    for selection in request.automatic_primers:
        from routers.molbio_ops import PrimerDesignRequest, design_primer_pairs_for_request
        index = next((i for i, p in enumerate(parts) if p.id == selection.part_id), None)
        if index is None or not isinstance(parts[index].preparation, PCRPreparation):
            raise AssemblyError('Automatic primer selection requires a PCR part')
        part = parts[index]
        prep = part.preparation
        assert isinstance(prep, PCRPreparation)
        material = material_for(source_map[part.source_id], resolver)
        payload = ''.join(material.sequence[p] for p in _positions(prep.region, material))
        if part.orientation == 'reverse':
            payload = reverse_complement(payload)
        enzyme = resolve_golden_gate_enzyme(enzyme_id=core.enzyme.enzyme_id, catalog_id=core.enzyme.catalog_id,
            expected_catalog_sha256=core.enzyme.catalog_sha256)
        native = PrimerDesignRequest(**selection.settings.model_dump(), sequence=payload, sequence_type='dna', is_circular=False,
            target_start=selection.settings.primer_max_length - 4,
            target_end=len(payload) - selection.settings.primer_max_length + 4,
            overhang_forward=prep.left.clamp + enzyme.site + prep.left.spacer + prep.left.fusion,
            overhang_reverse=prep.right.clamp + enzyme.site + prep.right.spacer + reverse_complement(prep.right.fusion),
            tm_settings=core.primer_settings)
        pairs = design_primer_pairs_for_request(native, part.name)
        eligible = [pair for pair in pairs.pairs if pair.product_start == 0 and pair.product_end == len(payload)]
        if len(eligible) < selection.pair_rank:
            diagnostics.append(f'{part.id}: native primer search found no requested boundary-preserving pair; explicit footprints retained')
        else:
            pair = eligible[selection.pair_rank - 1]
            parts[index] = part.model_copy(update={'preparation': prep.model_copy(update={
                'forward_anneal_length': pair.forward.anneal_length, 'reverse_anneal_length': pair.reverse.anneal_length})})
    core = core.model_copy(update={'parts': parts})
    design = design_golden_gate(core, resolve_revision=resolver)
    candidate = WorkflowCandidate(id='fixed', fixed_request=core, design=design, fidelity=_score(design, request.fidelity, core))
    return WorkflowResult(requested=request, solutions=[candidate], selected_solution_id='fixed' if design.solutions else None,
        edits=edits, reaction=_reaction(request.reaction, design, diagnostics), diagnostics=diagnostics)


def _split_core(request: SplitTask, material: Material, cuts: list[dict], width: int) -> GoldenGateDesignRequest:
    sequence, n = material.sequence, len(material.sequence)
    circular = material.topology == 'circular'
    starts = [x['position'] for x in cuts]
    if not circular:
        if request.terminal_right_fusion is None or len(request.terminal_right_fusion) != width:
            raise AssemblyError('Linear target requires an explicit terminal_right_fusion; its bottom protrusion is outside the exact top-strand target')
        starts = [0, *starts]
    parts = []
    for i, start in enumerate(starts):
        end = starts[(i + 1) % len(starts)] if circular or i + 1 < len(starts) else n
        body_start = (start + width) % n if circular else start + width
        distance = (end - start) % n if circular and len(starts) > 1 else end - start if not circular else n
        if distance <= width:
            raise AssemblyError('Selected split leaves no payload between fusion windows')
        left = (sequence + sequence[:width])[start:start + width]
        right = (sequence + sequence[:width])[end:end + width] if end < n else request.terminal_right_fusion
        if circular:
            right = (sequence + sequence[:width])[end:end + width]
        kwargs = dict(region=Region(start=body_start, end=end, wraps_origin=circular and body_start > end),
            left=Tail(clamp=request.preparation.clamp, spacer=request.preparation.spacer, fusion=left),
            right=Tail(clamp=request.preparation.clamp, spacer=request.preparation.spacer, fusion=right))
        if request.preparation.kind == 'pcr':
            preparation = PCRPreparation(**kwargs, forward_anneal_length=request.preparation.forward_anneal_length,
                reverse_anneal_length=request.preparation.reverse_anneal_length,
                qc_min_binding_anneal_length=request.preparation.qc_min_binding_anneal_length)
        else:
            preparation = SynthesisPreparation(**kwargs)
        parts.append(Part(id=f'part-{i + 1}', source_id=request.target.id, name=f'Target part {i + 1}', preparation=preparation))
    origin = (request.display_origin - starts[0]) % n if circular else request.display_origin
    expected = sequence[request.display_origin:] + sequence[:request.display_origin] if circular else sequence
    return GoldenGateDesignRequest(sources=[request.target], parts=parts, enzyme=request.enzyme, primer_settings=request.primer_settings,
        target=Target(topology=material.topology, display_origin=origin, exact_sequence=expected))


def run_workflow(request: WorkflowRequest, *, resolve_revision: Callable[[str], Material] | None = None) -> WorkflowResult:
    if isinstance(request, AssembleTask):
        return _assemble(request, resolve_revision)
    settings = request.fidelity
    if isinstance(request, EvaluateTask):
        result = fidelity.evaluate_overhangs(request.junctions, settings.dataset_id, condition_use=settings.condition_use,
            inventory=[fidelity.EndInstance(**x.model_dump()) for x in request.inventory], inventory_complete=request.inventory_complete,
            include_pair_observations=settings.include_pair_observations)
        return WorkflowResult(requested=request, evaluation=result)
    search = fidelity.SearchSettings(**request.search.model_dump())
    if isinstance(request, OptimizeTask):
        result = fidelity.optimize_overhangs(request.candidate_domain, request.junction_count, settings.dataset_id,
            end_length=request.end_length, fixed=request.fixed, required=request.required, excluded=request.excluded,
            condition_use=settings.condition_use, settings=search)
        return WorkflowResult(requested=request, search_result=result)
    material = material_for(request.target, resolve_revision)
    enzyme = resolve_golden_gate_enzyme(enzyme_id=request.enzyme.enzyme_id, catalog_id=request.enzyme.catalog_id,
        expected_catalog_sha256=request.enzyme.catalog_sha256)
    width = preparation_geometry(enzyme).overhang_length
    result = fidelity.optimize_sequence_windows(material.sequence, [(w.start, w.end) for w in request.windows], settings.dataset_id,
        end_length=width, topology=material.topology, fixed_positions=request.fixed_positions, fixed_overhangs=request.fixed_overhangs,
        required=request.required, excluded=request.excluded, protected_regions=[(w.start, w.end) for w in request.protected_regions],
        frame_constraints=[(w.start, w.end, w.origin, w.phase) for w in request.frame_constraints],
        min_fragment_length=request.min_fragment_length, max_fragment_length=request.max_fragment_length,
        target_fragment_length=request.target_fragment_length, condition_use=settings.condition_use, settings=search)
    solutions, diagnostics = [], []
    for i, choice in enumerate(result['solutions']):
        try:
            core = _split_core(request, material, choice['cuts'], width)
            design = design_golden_gate(core, resolve_revision=resolve_revision)
        except AssemblyError as exc:
            diagnostics.append(f'candidate-{i}: {exc}')
            continue
        if not design.solutions:
            diagnostics.extend(design.diagnostics)
            continue
        if not design.solutions[0].exact_target_match:
            raise AssemblyError('Internal split reconstruction differs from exact target')
        # Exact reconstruction warrants target feature mapping, including fusion bases.
        positions = list(range(request.display_origin, len(material.sequence))) + list(range(request.display_origin))
        mapped = _map_features(material.features, material, positions) + [f for f in design.solutions[0].features if f.type == 'primer_bind' and f.id.endswith(':binding')]
        design = design.model_copy(update={'solutions': [design.solutions[0].model_copy(update={'features': mapped})]})
        solutions.append(WorkflowCandidate(id=f'candidate-{i}', fixed_request=core, design=design, fidelity=_score(design, settings, core)))
    return WorkflowResult(requested=request, search_result=result, solutions=solutions,
        selected_solution_id=solutions[0].id if solutions else None, diagnostics=diagnostics,
        reaction=_reaction(request.reaction, solutions[0].design, diagnostics) if solutions else (calculate_reaction(request.reaction) if request.reaction else None))


def freeze_selection(result: WorkflowResult, solution_id: str) -> FrozenSelection:
    selected = next((s for s in result.solutions if s.id == solution_id), None)
    if selected is None or not selected.design.solutions:
        raise AssemblyError('Selected candidate has no physical product')
    requested = result.requested
    return FrozenSelection(solution_id=selected.id, edit_evidence=result.edits, authored_request=requested, request=selected.fixed_request, fidelity=requested.fidelity, reaction=getattr(requested, 'reaction', None),
        original_sources=requested.sources if isinstance(requested, AssembleTask) and any(e.accepted for e in result.edits) else [],
        accepted_edits=[e for e in requested.domestication if e.accepted_sequence is not None] if isinstance(requested, AssembleTask) else [])
