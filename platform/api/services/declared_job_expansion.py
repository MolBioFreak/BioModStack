"""Reviewed host-root expansion authority for the existing native follow-on.

This does not schedule science. The ordinary preview compiler, Job admission,
seed materializer and batch completion CAS remain the only owners.
"""
from copy import deepcopy
from pathlib import Path

from component_runtime import canonical_bytes, digest
from database import Job

AUTHORITY = 'services/nextflow.py:maybe_trigger_mutation_seed_refinement'
KEY = 'mutation_seed_expansion'
DERIVED_INPUT_FIELDS = frozenset({'rfantibody_input_pdbs', 'fampnn_collected_pdbs',
    'selected_input_dir', 'iteration_selection_dir', 'source_selection_manifest_path',
    'selected_input_manifest', 'manual_mutation_fixed_positions_json'})


def job_binding(job):
    """Scientific/source/placement fields, excluding mutable execution progress."""
    fields = ('id', 'name', 'model_id', 'mode', 'params', 'parent_job_id',
              'execution_target_id', 'execution_source_revision', 'execution_source_tree',
              'stage_family', 'stage_mode', 'child_stage', 'output_dir')
    bound = deepcopy({key: getattr(job, key, None) for key in fields})
    from services.execution_ownership import release_scheduler_gpu_assignment, EXECUTION_ATTEMPTS_PARAM
    bound['params'] = release_scheduler_gpu_assignment(bound['params'])
    bound['params'].pop(EXECUTION_ATTEMPTS_PARAM, None)
    bound['params'].pop('_mutation_seed_refinement_triggered', None)
    return bound


def input_binding(job):
    from paths import get_data_root, get_inputs_dir, get_results_dir
    from scripts.lib.portable_inputs import discover_native_input_references
    import yaml
    return discover_native_input_references(job.model_id, job.mode, job.params, (),
        output_dir=Path(job.output_dir),
        allowed_roots=(get_data_root(), get_inputs_dir(), get_results_dir()), yaml_loader=yaml.safe_load)


async def declarations(request, session, *, lock=False):
    trigger = request.params.get('mutation_seed_refinement_trigger')
    if trigger is None:
        return []
    from routers.jobs import _resolve_antibody_root_job
    if (not isinstance(trigger, dict) or request.model_id not in {'boltz2', 'protenix'}
            or request.mode != 'complex' or not request.params.get('mutagenesis_variants')):
        raise ValueError('Mutation seed expansion requires its explicit seed-builder variant request')
    allowed = {'source_job_id', 'root_job_id', 'name_suffix', 'param_overrides',
               'manual_mutation_mode', 'manual_mutation_method'}
    if set(trigger) - allowed or trigger.get('manual_mutation_mode') != 'seeded_refinement':
        raise ValueError('Invalid mutation seed expansion declaration')
    overrides = trigger.get('param_overrides') or {}
    if not isinstance(overrides, dict) or DERIVED_INPUT_FIELDS.intersection(overrides):
        raise ValueError('Mutation seed expansion inputs are derived, not overrideable paths')
    if overrides.get('num_parallel_jobs', 1) != 1 or 'mutagenesis_variants' in overrides:
        raise ValueError('Mutation seed expansion declares exactly one refinement root')
    source, root = await _resolve_antibody_root_job(session, str(trigger.get('source_job_id') or ''))
    if lock:
        from sqlalchemy import select
        await session.execute(select(Job).where(Job.id.in_([source.id, root.id]))
                              .order_by(Job.id).with_for_update())
    # Refresh rather than trusting an identity-map copy retained by a caller.
    await session.refresh(source)
    if root is not source:
        await session.refresh(root)
    from services.execution_ownership import cancellation_intent_requested
    if any(cancellation_intent_requested(row) or row.status in {'cancelled', 'canceled'}
           for row in (source, root)):
        raise ValueError('Reviewed expansion source/root was cancelled')
    if (root.id != trigger.get('root_job_id')
            or root.execution_target_id != request.execution_target_id
            or source.execution_target_id != request.execution_target_id):
        raise ValueError('Mutation seed expansion root/source/placement mismatch')
    from database import Design
    from routers.jobs import _resolve_design_structure_path, _mutagenesis_variant_job_params, _execution_plan_preview
    import hashlib
    variants = request.params['mutagenesis_variants']
    if not isinstance(variants, list) or any(not isinstance(row, dict) or
            not isinstance(row.get('source_design_id'), str) for row in variants):
        raise ValueError('Seed variants require explicit native source design identities')
    source_designs = []
    for design_id in sorted({row.get('source_design_id') for row in variants}):
        design = await session.get(Design, design_id, populate_existing=True, with_for_update=lock)
        if design is None:
            raise ValueError('Reviewed seed source design is unavailable')
        _, design_root = await _resolve_antibody_root_job(session, design.job_id)
        if design_root.id != root.id:
            raise ValueError('Seed source design belongs to an unrelated root')
        path = _resolve_design_structure_path(design.pdb_path)
        source_designs.append({'id': design.id, 'job_id': design.job_id, 'path': str(path),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    seed_plans = []
    params = deepcopy(request.params)
    params.pop('mutagenesis_variants')
    params.pop('mutation_seed_refinement_trigger')
    params.pop('num_parallel_jobs', None)
    for index, variant in enumerate(variants):
        seed = request.model_copy(deep=True)
        seed.params = _mutagenesis_variant_job_params(params, variant, index)
        seed.execution_plan_approval = None
        planned = _execution_plan_preview(seed)
        if not planned['admissible']:
            raise ValueError('A selected seed variant execution plan is unsupported')
        seed_plans.append(planned)
    from routers.jobs import _build_antibody_iteration_job
    from types import SimpleNamespace
    static = _build_antibody_iteration_job(root_job=root, source_job=source,
        action='ui_refinement', selection_dir=Path(source.output_dir), design_ids=[],
        name_suffix=trigger.get('name_suffix') or 'mutation_seeded_refinement',
        param_overrides={**trigger.get('param_overrides', {}),
            'manual_mutation_mode': 'seeded_refinement',
            'manual_mutation_method': trigger.get('manual_mutation_method') or 'cdr_indels'})
    # Only future result-derived references are excluded. All static native
    # input fields (including target/fixed-position overrides) use the same
    # typed native reference discovery as the actual child, never path walking.
    static_params = deepcopy(static.params)
    for key in ('rfantibody_input_pdbs', 'fampnn_collected_pdbs', 'selected_input_dir',
                'iteration_selection_dir', 'source_selection_manifest_path', 'selected_input_manifest'):
        static_params.pop(key, None)
    static_inputs = input_binding(SimpleNamespace(model_id=static.model_id, mode=static.mode,
        params=static_params, output_dir=source.output_dir))
    return [{'authority': AUTHORITY, 'child_model': 'template_antibody_denovo',
        'child_mode': 'antibody_refinement_pipeline', 'max_children': 1,
        'selection_rule': 'all ingested designs from successful reviewed variants, in variant order, after all variants terminate',
        'input_derivation': 'routers/jobs.py:_materialize_seed_selection_from_completed_designs',
        'request_derivation': 'routers/jobs.py:_build_antibody_iteration_job:ui_refinement',
        'trigger': deepcopy(trigger), 'root': job_binding(root), 'source': job_binding(source),
        'root_inputs': input_binding(root), 'source_inputs': input_binding(source),
        'source_designs': source_designs, 'seed_plans': seed_plans,
        'child_static_params': static_params, 'child_static_inputs': static_inputs,
        'variants': deepcopy(variants)}]


def retain(coordinator, variants, preview):
    expansions = preview.get('declared_expansions', []) if preview else []
    if not expansions:
        return
    coordinator.provenance = {**(coordinator.provenance or {}), KEY: {
        'preview': deepcopy(preview),
        'members': [job_binding(row) for row in variants],
        'coordinator_id': coordinator.id,
    }}
    coordinator.params = {**coordinator.params,
        'mutation_seed_refinement_trigger': deepcopy(expansions[0]['trigger'])}


async def validate(coordinator, session):
    """Revalidate explicit authority before filesystem preparation, not lineage."""
    from schemas import JobCreate
    from sqlalchemy import select
    retained = (coordinator.provenance or {}).get(KEY)
    if not isinstance(retained, dict) or retained.get('coordinator_id') != coordinator.id:
        raise ValueError('Missing reviewed mutation seed expansion authority')
    preview = retained['preview']
    authority = {key: preview[key] for key in ('schema', 'request', 'plan', 'input_identities',
                                              'generated_inputs', 'declared_expansions')}
    if digest(authority) != preview['approval_digest'] or not preview['admissible']:
        raise ValueError('Corrupt parent expansion approval')
    request = JobCreate.model_validate(preview['request'])
    current = await declarations(request, session, lock=True)
    if current != preview['declared_expansions']:
        raise ValueError('Reviewed expansion root/source/settings/inputs changed')
    source = preview['plan']['source_identity']
    if (coordinator.execution_target_id != request.execution_target_id
            or (coordinator.execution_source_revision, coordinator.execution_source_tree)
            != (source['revision'], source['tree'])
            or coordinator.params.get('mutation_seed_refinement_trigger') != current[0]['trigger']):
        raise ValueError('Expansion coordinator identity changed')
    rows = list((await session.scalars(select(Job).where(Job.batch_id == coordinator.batch_id,
        Job.job_phase == 'inference').order_by(Job.id).with_for_update()
        .execution_options(populate_existing=True))).all())
    expected = {row['id']: row for row in retained['members']}
    if {row.id for row in rows} != set(expected):
        raise ValueError('Expansion batch membership changed')
    for row in rows:
        bound = job_binding(row)
        # The coordinator's consumption flag is lifecycle, not science.
        bound['params'].pop('_mutation_seed_refinement_triggered', None)
        if bound != expected[row.id]:
            raise ValueError('Reviewed seed variant settings/source/placement changed')
    return current[0]


async def approve_derived(request, coordinator, session):
    """Called only after the native producer constructs the declared child."""
    from routers.jobs import _execution_plan_preview, ApprovedExecutionPlan
    declaration = await validate(coordinator, session)
    if (request.model_id, request.mode, request.execution_target_id) != (
            declaration['child_model'], declaration['child_mode'], coordinator.execution_target_id):
        raise ValueError('Derived child is outside the reviewed root expansion')
    from database import Design
    from sqlalchemy import select
    from uuid import uuid5, NAMESPACE_URL
    from paths import get_inputs_dir
    from routers.jobs import (_build_antibody_iteration_job, _resolve_design_structure_path,
                              _locked_spec_from_design_pdb, _build_selection_manifest_item)
    import json
    selection = get_inputs_dir() / 'design_selections' / 'antibody' / str(uuid5(
        NAMESPACE_URL, f'bms:mutation-seed-refinement:{coordinator.id}'))
    if selection.is_symlink() or not selection.is_dir():
        raise ValueError('Derived seed selection is unavailable')
    member_ids = [row['id'] for row in coordinator.provenance[KEY]['members']]
    members = [await session.get(Job, key) for key in member_ids]
    if any(row.status not in {'completed', 'failed'} or row.awaiting_input for row in members):
        raise ValueError('Seed batch is not terminal')
    designs = []
    fixed = {}
    manifest_path = selection / 'selection_manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('Derived selection manifest must be a regular file')
    manifest = json.loads(manifest_path.read_bytes())
    manifest_items = []
    for row in members:
        if row.status != 'completed':
            continue
        found = list((await session.scalars(select(Design).where(Design.job_id == row.id)
                    .order_by(Design.id))).all())
        if not found:
            raise ValueError('Completed seed variant has not been ingested')
        for design in found:
            designs.append(design)
            original = _resolve_design_structure_path(design.pdb_path)
            selected = selection / f'{len(designs):03d}_{design.id}.pdb'
            if selected.is_symlink() or not selected.is_file() or selected.read_bytes() != original.read_bytes():
                raise ValueError('Derived seed bytes differ from the completed variant')
            meta = row.params.get('mutation_variant') or {}
            manifest_items.append(_build_selection_manifest_item(design, source_path=original,
                selection_path=selected, selection_entry_mode='copy',
                extra={'mutation_variant': meta}))
            spec = _locked_spec_from_design_pdb(original, str(meta.get('binder_chain_id') or ''),
                                               meta.get('mutation')) if meta else ''
            if spec:
                fixed[selected.stem] = spec
    if not designs or {p.name for p in selection.glob('*.pdb')} != {
            f'{index:03d}_{row.id}.pdb' for index, row in enumerate(designs, 1)}:
        raise ValueError('Derived seed membership differs from approved successful variants')
    fixed_path = selection / 'mutation_fixed_positions.json'
    if (manifest.get('root_job_id') != declaration['root']['id']
            or manifest.get('source_job_id') != declaration['source']['id']
            or manifest.get('action') != 'mutation_seeded_refinement'
            or manifest.get('design_count') != len(designs)
            or manifest.get('designs') != manifest_items):
        raise ValueError('Derived selection manifest differs from native seed provenance')
    if fixed_path.is_symlink() or json.loads(fixed_path.read_bytes()) != fixed:
        raise ValueError('Derived fixed positions differ from reviewed mutation rules')
    trigger = declaration['trigger']
    overrides = {**trigger.get('param_overrides', {}), 'manual_mutation_mode': 'seeded_refinement',
        'manual_mutation_method': trigger.get('manual_mutation_method') or 'cdr_indels',
        'manual_mutation_fixed_positions_json': str(fixed_path)}
    root = await session.get(Job, declaration['root']['id'])
    source_job = await session.get(Job, declaration['source']['id'])
    expected = _build_antibody_iteration_job(root_job=root, source_job=source_job,
        action='ui_refinement', selection_dir=selection, design_ids=[row.id for row in designs],
        name_suffix=trigger.get('name_suffix') or 'mutation_seeded_refinement', param_overrides=overrides)
    if canonical_bytes(request.model_dump(mode='json')) != canonical_bytes(expected.model_dump(mode='json')):
        raise ValueError('Derived child request differs from the reviewed expansion rule')
    preview = _execution_plan_preview(request)
    retained = coordinator.provenance[KEY]['preview']
    if preview['plan']['source_identity'] != retained['plan']['source_identity']:
        raise ValueError('Derived child source differs from reviewed parent')
    # The actual native-derived request and seed bytes are bound by the ordinary
    # preview. This is an explicit expansion receipt, not an operator signature.
    preview['expansion_approval'] = {'parent_job_id': coordinator.id,
        'parent_approval_digest': retained['approval_digest'],
        'declaration_sha256': digest(declaration),
        'derived_request_sha256': digest(request.model_dump(mode='json')),
        'derived_input_identities': preview['input_identities']}
    return ApprovedExecutionPlan(canonical_bytes(request.model_dump(mode='json')), canonical_bytes(preview))
