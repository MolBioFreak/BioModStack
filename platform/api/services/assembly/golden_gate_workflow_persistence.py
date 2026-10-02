"""Frozen Golden Gate workups using existing operation/revision/edge owners.

No preview cache, tokens, new tables, or reoptimization on save/read. Product and
intermediate DNA are read from immutable revisions, not mutable shelf projections.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import hashlib
import json
import uuid
from sqlalchemy import select
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from molbio_models import MolecularOperation, MolecularRevision, NucleotideSequence
from services.molbio_persistence import (
    begin_immediate_molbio_write, create_operation, record_sequence_revision,
    _record_inline_sequence_input, add_operation_edges,
)
from services.assembly.golden_gate_design import design_material
from services.assembly.golden_gate_design_types import Material, Feature, Region, InlineSource
from services.assembly.golden_gate_workflow_types import (
    SaveDesignRequest, SavedDesign, AssembleTask, SplitTask, WorkflowResult, FrozenSelection,
)
from services.assembly.golden_gate_workflow import run_workflow, material_for

KIND = 'golden_gate_design'


def _feature(value, index):
    if 'segments' in value:
        return Feature.model_validate({key: item for key, item in value.items() if key in Feature.model_fields})
    # Existing toolkit's native simple feature representation.
    return Feature(id=str(value.get('id', index)), type=value.get('type', 'misc_feature'),
        name=value.get('name', ''), segments=[Region(start=value['start'], end=value['end'],
            wraps_origin=value.get('wraps_origin', False))], strand=value.get('strand', 1),
        qualifiers=value.get('qualifiers', {}), codon_start=value.get('codon_start'))


async def resolve_sources(session, sources):
    resolved, revisions = {}, {}
    for source in sources:
        if isinstance(source.source, InlineSource):
            continue
        revision_id = source.source.revision_id
        if revision_id in resolved:
            continue
        revision = await session.get(MolecularRevision, revision_id)
        if revision is None:
            raise HTTPException(404, 'Immutable molecular revision not found')
        snapshot = revision.snapshot
        dna = snapshot.get('sequence')
        if not isinstance(dna, str) or hashlib.sha256(dna.encode()).hexdigest() != revision.content_sha256 or len(dna) != revision.content_length:
            raise HTTPException(409, 'Immutable source content identity mismatch')
        if snapshot.get('sequence_type', 'dna') != 'dna':
            raise HTTPException(422, 'Golden Gate requires DNA sources')
        resolved[revision_id] = Material(sequence=dna, topology='circular' if snapshot.get('is_circular') else 'linear',
            features=[_feature(f, i) for i, f in enumerate(snapshot.get('features') or [])])
        revisions[revision_id] = revision
    return resolved, revisions


def request_sources(request):
    if hasattr(request, 'sources'):
        return request.sources
    if hasattr(request, 'target'):
        return [request.target]
    return []


def _row(material, *, name, operation_id, description=None, product=False, primers=None):
    return NucleotideSequence(id=str(uuid.uuid4()), name=name, description=description,
        sequence=material.sequence, sequence_type='dna', is_circular=material.topology == 'circular',
        length=len(material.sequence), features=[{**f.model_dump(mode='json'),
            'start': f.segments[0].start, 'end': f.segments[-1].end} for f in material.features if f.segments],
        primers=primers or [], version=1, operation=KIND if product else None,
        operation_params={'operation_id': operation_id, 'schema_version': 'bms.golden-gate-workup-reference.v1'} if product else None)


async def _record_intermediate(session, sequence, *, operation, provenance, created_by=None):
    # Immutable history only: intermediates are outputs, not shelf projections or
    # falsely attested unchanged inline inputs.
    return await record_sequence_revision(session, sequence, change_kind='operation_intermediate',
        operation_id=operation.id, provenance=provenance)


async def read_workup(session, operation_id):
    operation = await session.get(MolecularOperation, operation_id)
    if operation is None or operation.operation_kind != KIND:
        raise HTTPException(404, 'Golden Gate design operation not found')
    payload = copy.deepcopy(operation.parameters['workup'])
    resolved = {}
    for reference in operation.parameters['dna_references']:
        rid = reference['revision_id']
        if rid not in resolved:
            revision = await session.get(MolecularRevision, rid)
            if revision is None or hashlib.sha256(revision.snapshot['sequence'].encode()).hexdigest() != revision.content_sha256:
                raise HTTPException(409, 'Retained molecular material integrity mismatch')
            resolved[rid] = revision.snapshot['sequence']
        target = payload
        for key in reference['path'][:-1]:
            target = target[key]
        target[reference['path'][-1]] = resolved[rid]
    # One replay owner; response projections are reconstructed only on demand.
    if operation.parameters.get('replay_owner') == 'selection':
        payload['result']['requested'] = copy.deepcopy(payload['selection']['authored_request'] or {
            **payload['selection']['request'], 'fidelity': payload['selection']['fidelity'], 'reaction': payload['selection']['reaction']})
        payload['result']['solutions'][0]['fixed_request'] = copy.deepcopy(payload['selection']['request'])
        payload['result']['edits'] = copy.deepcopy(payload['selection']['edit_evidence'])
    return SavedDesign.model_validate(payload)


async def save_workup(session, request: SaveDesignRequest):
    fingerprint = hashlib.sha256(json.dumps(request.model_dump(mode='json', exclude={'idempotency_key'}), sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    await begin_immediate_molbio_write(session)
    prior = (await session.execute(select(MolecularOperation).where(
        MolecularOperation.idempotency_key == request.idempotency_key))).scalar_one_or_none()
    if prior is not None:
        if prior.operation_kind != KIND or prior.request_fingerprint != fingerprint:
            raise HTTPException(409, 'Idempotency key already belongs to a different request')
        return await read_workup(session, prior.id)
    selection = request.selection
    resolved, source_revisions = await resolve_sources(session, selection.request.sources + selection.original_sources + (request_sources(selection.authored_request) if selection.authored_request else []))
    # Explicit accepted edits are fixed material, not a request to search again.
    originals = {s.id: s for s in selection.original_sources}
    effective = {s.id: s for s in selection.request.sources}
    for edit in selection.accepted_edits:
        if edit.source_id not in originals or edit.source_id not in effective or edit.accepted_sequence is None:
            raise HTTPException(422, 'Accepted edit requires original and derived source material')
        original = material_for(originals[edit.source_id], resolved.__getitem__)
        derived = material_for(effective[edit.source_id], resolved.__getitem__)
        if derived.sequence != edit.accepted_sequence or len(original.sequence) != len(derived.sequence):
            raise HTTPException(422, 'Fixed accepted substitution material does not match the selection')
    fixed = AssembleTask(**selection.request.model_dump(), fidelity=selection.fidelity, reaction=selection.reaction)
    # Authoritative physical reconstruction from fixed preparations, never search.
    result = await run_in_threadpool(run_workflow, fixed, resolve_revision=resolved.__getitem__)
    if not result.solutions or not result.solutions[0].design.solutions:
        raise HTTPException(422, 'The selected fixed preparation has no physical product')
    if selection.authored_request is not None:
        result = result.model_copy(update={'requested': selection.authored_request})
    result = result.model_copy(update={'edits': selection.edit_evidence,
        'edit_evidence_authority': 'operator_supplied_frozen' if selection.edit_evidence else 'native_preview'})
    candidate = result.solutions[0]
    design = candidate.design
    if isinstance(selection.authored_request, SplitTask):
        from services.assembly.golden_gate_design import _map_features
        target = material_for(selection.authored_request.target, resolved.__getitem__)
        origin = selection.authored_request.display_origin
        expected = target.sequence[origin:] + target.sequence[:origin]
        if design.solutions[0].sequence != expected:
            raise HTTPException(422, 'Fixed split product differs from the selected exact target')
        positions = list(range(origin, len(target.sequence))) + list(range(origin))
        features = _map_features(target.features, target, positions) + [f for f in design.solutions[0].features if f.type == 'primer_bind' and f.id.endswith(':binding')]
        design = design.model_copy(update={'solutions': [design.solutions[0].model_copy(update={'features': features})]})
        candidate = candidate.model_copy(update={'design': design})
        result = result.model_copy(update={'solutions': [candidate]})
    candidate = candidate.model_copy(update={'id': selection.solution_id})
    result = result.model_copy(update={'solutions': [candidate], 'selected_solution_id': selection.solution_id})
    product = design.solutions[0]
    operation = await create_operation(session, operation_kind=KIND, implementation='services.assembly.golden_gate_workflow',
        idempotency_key=request.idempotency_key, request_fingerprint=fingerprint,
        parameters={}, warnings=design.diagnostics + product.warnings, provenance={'source': 'api', 'fixed_selection': True})
    material_revisions = {}
    source_map = {s.id: s for s in selection.request.sources}
    original_revisions = {}
    for source_id, source in originals.items():
        if isinstance(source.source, InlineSource):
            material = material_for(source, None)
            original_revisions[source_id] = await _record_inline_sequence_input(session,
                _row(material, name=f'{request.name} original {source_id}', operation_id=operation.id),
                operation=operation, provenance={'source_id': source_id}, created_by=None)
        else:
            original_revisions[source_id] = source_revisions[source.source.revision_id]
    # Source/preparation states remain distinct even when sequence bytes coincide.
    for material in design.materials:
        source = source_map.get(material.id.removeprefix('source:')) if material.stage == 'source' else None
        if material.stage == 'source' and material.id.removeprefix('source:') in original_revisions and material.id.removeprefix('source:') not in {e.source_id for e in selection.accepted_edits}:
            revision = original_revisions[material.id.removeprefix('source:')]
        elif source is not None and not isinstance(source.source, InlineSource):
            revision = source_revisions[source.source.revision_id]
        else:
            parent = material_revisions.get(material.parent_id)
            if material.stage == 'source':
                parent = original_revisions.get(material.id.removeprefix('source:'))
            retain = _record_inline_sequence_input if material.stage == 'source' and parent is None else _record_intermediate
            revision = await retain(session,
                _row(material, name=f'{request.name} {material.id}', operation_id=operation.id), operation=operation,
                provenance={'material_id': material.id, 'transformation': 'accepted_edit' if parent and material.stage == 'source' else material.transformation,
                    'parent_revision_id': parent.id if parent else None,
                    'mappings': [m.model_dump(mode='json') for m in material.mappings]}, created_by=None)
        material_revisions[material.id] = revision
    # Every digest outcome is retained, not only the intended/purified subset.
    for digest in design.digests:
        for mid in digest.fragment_material_ids:
            material = design_material(design, mid)
            material_revisions[mid] = await _record_intermediate(session,
                _row(material, name=f'{request.name} {mid}', operation_id=operation.id), operation=operation,
                provenance={'material_id': mid, 'transformation': 'digest',
                    'parent_revision_id': material_revisions[material.parent_id].id,
                    'mappings': [m.model_dump(mode='json') for m in material.mappings],
                    'left_end': asdict(material.left_end) if material.left_end else None,
                    'right_end': asdict(material.right_end) if material.right_end else None}, created_by=None)
    product_primers = []
    for primer in design.primers:
        feature = next((f for f in product.features if f.type == 'primer_bind' and f.id.endswith(primer.id + ':binding')), None)
        if feature is not None and feature.segments:
            product_primers.append({'id': primer.id, 'name': primer.id, 'sequence': primer.full_sequence,
                'sequence_type': 'dna', 'start': feature.segments[0].start, 'end': feature.segments[-1].end,
                'strand': feature.strand, 'tm': primer.tm.tm,
                'tm_algorithm': design.primer_settings.algorithm, 'tm_salt_correction': design.primer_settings.salt_correction,
                'tm_settings': design.primer_settings.model_dump(mode='json'),
                'sites': [{'start': s.start, 'end': s.end, 'strand': feature.strand} for s in feature.segments],
                'provenance': {'operation_id': operation.id, 'design_primer_id': primer.id, 'tm_basis': 'annealing_footprint'}})
    product_row = _row(Material(sequence=product.sequence, topology=product.topology, features=product.features),
        name=request.name, description=request.description, operation_id=operation.id, product=True,
        primers=product_primers)
    product_row.operation_params = {**product_row.operation_params, 'engine': 'golden_gate_design', 'engine_version': '1',
        'fragment_count': len(selection.request.parts), 'primer_count': len(design.primers)}
    session.add(product_row)
    product_revision = await record_sequence_revision(session, product_row, change_kind='operation_result', operation_id=operation.id,
        provenance={'selected_solution_id': selection.solution_id})
    edited_ids = {f'source:{edit.source_id}' for edit in selection.accepted_edits}
    await add_operation_edges(session, operation,
        input_revisions=[(r, 'original_source', {'source_id': sid}) for sid, r in original_revisions.items()] +
            [(r, 'source', {'material_id': mid}) for mid, r in material_revisions.items() if mid.startswith('source:') and mid not in edited_ids],
        output_revisions=[(r, 'intermediate', {'material_id': mid}) for mid, r in material_revisions.items()
            if not mid.startswith('source:') or mid in edited_ids] +
            [(product_revision, 'product', {'document_id': product_row.id})])
    saved = SavedDesign(operation_id=operation.id, product_document_id=product_row.id,
        product_revision_id=product_revision.id, selection=selection, result=result)
    payload = saved.model_dump(mode='json')
    references = []
    # Externalize DNA to exact immutable state identities, retaining portable readback.
    def externalize(path, revision):
        node = payload
        for key in path[:-1]:
            node = node[key]
        if node[path[-1]] != revision.snapshot['sequence']:
            raise RuntimeError('DNA normalization identity mismatch')
        node[path[-1]] = None
        references.append({'path': path, 'revision_id': revision.id})
    for prefix in [('selection', 'request'), ('result', 'solutions', 0, 'fixed_request')]:
        for i, source in enumerate(selection.request.sources):
            if isinstance(source.source, InlineSource):
                externalize([*prefix, 'sources', i, 'source', 'sequence'], material_revisions[f'source:{source.id}'])
        if selection.request.target.exact_sequence == product.sequence:
            externalize([*prefix, 'target', 'exact_sequence'], product_revision)
    for i, source in enumerate(selection.original_sources):
        if isinstance(source.source, InlineSource):
            externalize(['selection', 'original_sources', i, 'source', 'sequence'], original_revisions[source.id])
    for i, edit in enumerate(selection.accepted_edits):
        externalize(['selection', 'accepted_edits', i, 'accepted_sequence'], material_revisions[f'source:{edit.source_id}'])
    prefix = ['result', 'solutions', 0, 'design']
    for i, material in enumerate(design.materials):
        externalize([*prefix, 'materials', i, 'sequence'], material_revisions[material.id])
    for i, digest in enumerate(design.digests):
        for j, mid in enumerate(digest.fragment_material_ids):
            externalize([*prefix, 'digests', i, 'fragments', j, 'top_strand_sequence'], material_revisions[mid])
    externalize([*prefix, 'solutions', 0, 'sequence'], product_revision)
    for prefix in [('result', 'edits'), ('selection', 'edit_evidence')]:
        for i, evidence in enumerate(selection.edit_evidence):
            original_revision = original_revisions.get(evidence.source_id) or material_revisions[f'source:{evidence.source_id}']
            externalize([*prefix, i, 'original', 'sequence'], original_revision)
            if evidence.accepted and evidence.proposal.proposed_sequence is not None:
                externalize([*prefix, i, 'proposal', 'proposed_sequence'], material_revisions[f'source:{evidence.source_id}'])
    # Requested controls are retained exactly, including split/search/primer inputs.
    for prefix, authored in [(('result', 'requested'), result.requested), (('selection', 'authored_request'), selection.authored_request)]:
        if authored is None:
            continue
        for i, source in enumerate(request_sources(authored)):
            if not isinstance(source.source, InlineSource):
                continue
            revision = original_revisions.get(source.id) or material_revisions[f'source:{source.id}']
            path = [*prefix, 'target', 'source', 'sequence'] if isinstance(authored, SplitTask) else [*prefix, 'sources', i, 'source', 'sequence']
            externalize(path, revision)
        if isinstance(authored, AssembleTask):
            if authored.target.exact_sequence == product.sequence:
                externalize([*prefix, 'target', 'exact_sequence'], product_revision)
            for i, edit in enumerate(authored.domestication):
                if edit.accepted_sequence is not None:
                    externalize([*prefix, 'domestication', i, 'accepted_sequence'], material_revisions[f'source:{edit.source_id}'])
    aliases = [('result', 'requested'), ('result', 'solutions', 0, 'fixed_request'), ('result', 'edits')]
    references = [reference for reference in references if not any(tuple(reference['path'][:len(prefix)]) == prefix for prefix in aliases)]
    payload['result']['requested'] = None
    payload['result']['solutions'][0]['fixed_request'] = None
    payload['result']['edits'] = None
    operation.parameters = {'workup': payload, 'dna_references': references, 'replay_owner': 'selection',
        'summary': {'name': request.name, 'fragment_count': len(selection.request.parts), 'primer_count': len(design.primers)}}
    await session.commit()
    return await read_workup(session, operation.id)
