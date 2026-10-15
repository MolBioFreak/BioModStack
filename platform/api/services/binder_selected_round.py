"""Selected instances in the existing binder-round Job progress owner.

Only orchestration references/settings are retained here. Native publications and
Designs remain the result authority; ordinary Job/Project submission owns execution.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from database import Design, Job, JobArtifact
from schemas import CandidateRef, JobCreate, BinderRoundStepReference, SelectedRoundRequest
from services import binder_round as rounds

KEY = 'selected_rounds'
TERMINAL = {'completed', 'failed', 'cancelled'}
MODES = {'proteinmpnn': 'design', 'fampnn': 'binder_design', 'caliby_binder': 'design',
         'protonpottsmpnn': 'redesign', 'binder_refinement': 'refine',
         'protenix': 'complex', 'boltz2': 'complex', 'esmfold2': 'predict'}


def wire(ref):
    return CandidateRef.model_validate(ref).model_dump(mode='json', exclude_none=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def normalize(request):
    from model_registry import get_registry
    from services.binder_round_inputs import BOUND_INPUTS, BLIND_FIXED_PARAMETERS
    result = request.model_copy(deep=True)
    registry = get_registry()
    for stage in result.round_plan.stages:
        invocations = stage.models if stage.kind != 'maturation' else [stage]
        for invocation in invocations:
            model = invocation.model_id
            definition = registry.get_internal_model_definition(model)
            if definition is None or not any(m.id == MODES[model] for m in definition.modes):
                raise ValueError(f'Unsupported model/mode {model}/{MODES[model]}')
            known = {field.name for field in definition.params}
            if set(invocation.params) - known:
                raise ValueError(f'Unknown {model} settings: {sorted(set(invocation.params) - known)}')
            # Roles are operator settings; physical source paths are server-owned.
            bound = BOUND_INPUTS - {'binder_chains', 'target_chains', 'design_chain', 'target_chain'}
            bound |= {'target_pdb'}
            if any(invocation.params.get(key) for key in bound):
                raise ValueError('Round inputs are bound from the selected candidate and independent target')
            if stage.kind == 'prediction' and any(invocation.params.get(key) for key in BLIND_FIXED_PARAMETERS):
                raise ValueError('Blind prediction does not accept pose conditioning')
            defaults = {f.name: deepcopy(f.default) for f in definition.params
                        if f.default is not None and f.name not in bound}
            if stage.kind == 'prediction':
                defaults.update({key: False for key in BLIND_FIXED_PARAMETERS if key in known})
            invocation.params = {**defaults, **invocation.params}
            if model == 'protonpottsmpnn':
                from scripts.lib.protonpottsmpnn_contract import normalize_options, parameter_schema
                scientific = set(parameter_schema()['properties']) - {'target_pdb'}
                invocation.params.update(normalize_options({k:v for k,v in invocation.params.items() if k in scientific}))
                errors = []  # Full native option schema above; source bound at create.
            else:
                errors = registry.validate_job_params(model, MODES[model], invocation.params)
            errors = [e for e in errors if e not in {f'Missing required parameter: {k}'
                      for k in bound | {'binder_chains', 'target_chains', 'design_chain', 'target_chain'}}]
            if errors:
                raise ValueError(str(errors))
    return result


async def selection_refs(session, source, request):
    refs = []
    if request.selection:
        if request.selection.candidate_refs is not None:
            refs = [wire(r) for r in request.selection.candidate_refs]
        else:
            from routers.jobs import _saved_review_filter_entries
            entry = next((r for r in _saved_review_filter_entries(source)
                          if r['id'] == request.selection.saved_set_id
                          and r.get('revision', 1) == request.selection.revision), None)
            if entry is None:
                raise HTTPException(404, 'Saved selection revision not found')
            refs = deepcopy(entry.get('candidate_refs') or [])
            if not refs:
                for identity in entry.get('design_ids', []):
                    design = await session.get(Design, identity)
                    if design is None:
                        raise HTTPException(404, 'Selected Design not found')
                    refs.append({'kind': 'design', 'job_id': design.job_id, 'design_id': identity})
    if set(request.candidate_documents) - set(request.design_ids):
        raise ValueError('Candidate documents must be keyed by selected Design IDs')
    legacy = []
    for identity in request.design_ids:
        design = await session.get(Design, identity)
        if design is None:
            raise HTTPException(404, 'Selected Design not found')
        ref = {'kind': 'design', 'job_id': design.job_id, 'design_id': identity}
        if identity in request.candidate_documents:
            ref['document'] = request.candidate_documents[identity]
        legacy.append(ref)
    legacy.extend({'kind': 'native_document', **r} for r in request.native_sources)
    # A dual representation must describe the same frozen set, never add inputs.
    if refs and legacy and [wire(r) for r in refs] != [wire(r) for r in legacy]:
        raise ValueError('Selection representations disagree')
    refs = [wire(r) for r in refs or legacy]
    if not refs or len({digest(r) for r in refs}) != len(refs):
        raise ValueError('Select a nonempty set of distinct exact candidates')
    return refs


async def native_sequences(session, owner):
    """Use the existing verified sequence-only publication, without Design rows."""
    from services.protonpottsmpnn_design import read_result, DIRECTORY
    result = await read_result(session, owner.id)
    artifact = await session.scalar(select(JobArtifact).where(
        JobArtifact.owner_job_id == owner.id, JobArtifact.attempt == (owner.retry_count or 0),
        JobArtifact.logical_path == DIRECTORY + '/manifest.json'))
    if artifact is None:
        raise ValueError('Native sequence publication is not registered')
    return artifact, result['designs']


def retained_candidate_directory(root, ref):
    """Recover an exact prior source snapshot after producer cleanup."""
    for previous in rounds.progress_of(root).get(KEY, []):
        rows = list(previous.get('selection', {}).get('resolved', []))
        rows += list(previous.get('resolved_outputs', {}).values())
        for row in rows:
            if row.get('ref') != ref or not row.get('selection_dir'):
                continue
            directory = Path(row['selection_dir'])
            try:
                manifest = json.loads((directory / 'selection_manifest.json').read_text())
                binding = manifest.pop('retained_input_binding')
                if hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest() != binding['manifest_sha256']:
                    continue
                if all(hashlib.sha256((directory / name).read_bytes()).hexdigest() == sha
                       for name, sha in binding['files'].items()):
                    return directory
            except (OSError, ValueError, KeyError):
                continue
    raise FileNotFoundError('Exact retained candidate source is unavailable')


async def resolve_ref(session, root, ref, *, snapshot=True, output_format="pdb"):
    from services.binder_continuation import resolve_root, resolve_selection, resolve_candidate_documents, snapshot_selection
    from services.binder_round_inputs import candidate_roles
    from services.binder_diagnostic_selection import CandidateDocument, declared_targets
    ref = wire(ref)
    owner, owner_root = await resolve_root(session, ref['job_id'])
    if owner_root.id != root.id:
        raise ValueError('Selected candidate crosses the source-root boundary')
    row = {'ref': ref, 'owner_job_id': owner.id}
    prior = (owner.provenance or {}).get(rounds.STEP) or {}
    if ref['kind'] == 'design':
        _, _, designs = await resolve_selection(session, owner.id, [ref['design_id']])
        design = designs[0]
        if design.job_id != owner.id:
            raise ValueError('Selected Design owner differs from reference')
        document = ref.get('document') or {}
        selectors = {design.id: CandidateDocument(**{k:v for k,v in document.items() if k != 'model'})} if document else {}
        documents = await resolve_candidate_documents(session, designs, selectors)
        if design.id in documents and document.get('model') is not None:
            path, identity = documents[design.id]
            documents[design.id] = (path, {**identity, 'model': document['model']})
        if document.get('model') is not None:
            # Existing document ownership is preserved; exact model extraction is
            # performed by the source parser below, not silently defaulted.
            row['model'] = document['model']
        try:
            binder, target = candidate_roles(owner, design, SimpleNamespace(binder_chains=[], target_chains=[]))
        except ValueError:
            binder, target = prior.get('binder_chains', []), prior.get('target_chains', [])
        if prior.get('stage') == 'maturation':
            # Flow writes a new role map; ancestral chain defaults must not be
            # reapplied to its renumbered/masked output. The publication owner
            # retains these actual roles on the emitted Design.
            from services.binder_round_inputs import chains
            binder = chains((design.provenance or {}).get('binder_chains'))
            target = chains((design.provenance or {}).get('target_chains'))
        if design.id in documents:
            declared = documents[design.id][1].get('producer_document') or {}
            binder = declared.get('binder_chains') or binder
            target = declared.get('target_chains') or target
        row.update(binder_chains=binder, target_chains=target, artifact_class=design.artifact_class)
        if snapshot:
            try:
                directory = snapshot_selection(owner, root, designs, candidate_documents=documents, output_format=output_format)
            except FileNotFoundError:
                directory = retained_candidate_directory(root, ref)
            row['selection_dir'] = str(directory)
            manifest = json.loads((directory / 'selection_manifest.json').read_text())
            row['path'] = manifest['designs'][0]['selection_pdb_path']
    elif ref['kind'] == 'native_document':
        from services.binder_native_selection import resolve_native_source
        path, identity = await resolve_native_source(session, owner.id, ref['artifact_id'])
        if ref.get('target_state') is not None and ref['target_state'] != identity.get('target_state'):
            raise ValueError('Selected native target state differs from document')
        if ref.get('model') is not None:
            identity = {**identity, 'model': ref['model']}
        document = identity.get('producer_document') or {}
        row.update(binder_chains=document.get('binder_chains') or prior.get('binder_chains', []),
                   target_chains=document.get('target_chains') or prior.get('target_chains', []))
        if ref.get('model') is not None:
            row['model'] = ref['model']
        if snapshot:
            directory = snapshot_selection(owner, root, [], native_sources=[(path, identity)], output_format=output_format)
            row['selection_dir'] = str(directory)
            row['path'] = json.loads((directory / 'selection_manifest.json').read_text())['designs'][0]['selection_pdb_path']
    elif ref['kind'] == 'native_observation' and owner.model_id == 'boltzgen':
        from services.boltzgen_candidate_publication import read_published_generation_page
        match, offset = None, 0
        while True:
            page = await read_published_generation_page(owner, session, offset=offset, limit=1000)
            for record in page['records']:
                if record.get('candidate_ref') == ref:
                    if match is not None:
                        raise ValueError('Native observation identity is ambiguous')
                    match = record
            offset += len(page['records'])
            if offset >= page['total'] or not page['records']:
                break
        if match is None:
            raise ValueError('Selected observation is outside the registered native publication')
        row.update(binder_chains=[], target_chains=[], native_record=match)
    elif ref['kind'] == 'native_observation' and owner.model_id == 'bindcraft2':
        from services.bindcraft2_publication import read_published_native_results
        from services.bindcraft2_native_results import _rows
        _, receipt = await read_published_native_results(owner, session)
        artifact = await session.get(JobArtifact, ref['publication_ref'])
        if artifact is None or artifact.owner_job_id != owner.id or artifact.attempt != (owner.retry_count or 0):
            raise ValueError('Selected observation publication differs from its owner')
        relative = artifact.logical_path.removeprefix('bindcraft2/native/')
        entry = receipt.get('files', {}).get(relative)
        if not entry or artifact.sha256 != entry['sha256']:
            raise ValueError('Selected observation is outside the native publication')
        name = Path(artifact.logical_path).name
        stages = {'!_Trajectories.csv': 'trajectory', 'trajectories.csv': 'trajectory',
                  '!_Refolded.csv': 'draw', 'candidates.csv': 'draw',
                  '!_Ranked.csv': 'retained', 'ranked.csv': 'retained', 'accepted.csv': 'retained'}
        if name not in stages:
            raise ValueError('Select a registered native candidate table')
        records = _rows(Path(artifact.storage_path), None, stages[name])
        matches = [r for r in records if r.design == ref['native_record_id']]
        if len(matches) != 1:
            raise ValueError('Native observation identity is missing or ambiguous')
        row.update(sequence=matches[0].sequence, binder_chains=[], target_chains=[],
                   publication_sha256=artifact.sha256)
        # A body-less observation stays selectable/rankable. No Design or pose
        # is synthesized; an actual downstream model still owns its input needs.
    elif owner.model_id == 'protonpottsmpnn':
        artifact, outputs = await native_sequences(session, owner)
        if ref['publication_ref'] != artifact.id:
            raise ValueError('Selected native publication differs from registered owner')
        identity = ref.get('native_output_id') or ref.get('native_record_id')
        output = next((r for r in outputs if r['design_id'] == identity), None)
        if output is None:
            raise HTTPException(404, 'Selected native sequence/observation not found')
        native = output['native']
        row.update(sequence=native.get('canonical_sequence'), binder_chains=[native['binder_chain']],
                   target_chains=prior.get('target_chains', []), publication_sha256=artifact.sha256)
    else:
        raise ValueError('No native sequence/observation record contract for this producer')
    # Target bindings are frozen independently of generated coordinates. This
    # preserves CSV source membership and prior target branches across requeue.
    if prior.get('target_binding'):
        targets = [deepcopy(prior['target_binding'])]
    elif prior.get('independent_targets'):
        targets = deepcopy(prior['independent_targets'])
    else:
        try:
            targets = await declared_targets(owner, session)
        except (ValueError, OSError) as exc:
            targets = [{'owner_job_id': owner.id, 'unavailable_reason': str(exc)}]
        identity = ref.get('design_id')
        targets = [t for t in targets if not t.get('source_design_ids') or identity in t['source_design_ids']]
    if snapshot:
        from services.binder_round_inputs import source_components, chains
        for target in targets:
            if not target.get('components') and target.get('target_path'):
                from paths import resolve_allowed_path, resolve_runtime_data_path
                try:
                    target_path = Path(target['target_path'])
                    # Uploaded inputs use the file API's managed namespace, not cwd.
                    path = (resolve_allowed_path(str(target_path))
                            if target_path.parts[:1] == ('inputs',)
                            else resolve_runtime_data_path(target_path))
                    ids = chains(target.get('chains'))
                    from services.binder_diagnostic_selection import independent_structure_components
                    target['components'] = independent_structure_components(path, ids)
                    target['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                except (ValueError, OSError) as exc:
                    # Target availability is relevant only to a consumer that
                    # actually requests it, never a gate on selected redesign.
                    target['unavailable_reason'] = str(exc)
    if snapshot and not any(t.get('components') for t in targets):
        # Same exact candidate's retained independent inputs survive cleanup.
        # This is custody reuse, not extraction from its generated pose.
        for previous in rounds.progress_of(root).get(KEY, []):
            retained_rows = list(previous.get('selection', {}).get('resolved', []))
            retained_rows += list(previous.get('resolved_outputs', {}).values())
            match = next((r for r in retained_rows if r.get('ref') == ref
                          and any(t.get('components') for t in r.get('targets', []))), None)
            if match:
                targets = deepcopy(match['targets'])
                break
    row['targets'] = targets
    return row


async def output_refs(session, child):
    refs = [{'kind': 'design', 'job_id': child.id, 'design_id': d.id}
            for d in await session.scalars(select(Design).where(Design.job_id == child.id).order_by(Design.id))]
    if child.model_id == 'protonpottsmpnn':
        # No result yet is not a fabricated empty successful publication.
        from services.protonpottsmpnn_design import DIRECTORY
        manifest = await session.scalar(select(JobArtifact.id).where(JobArtifact.owner_job_id == child.id,
            JobArtifact.attempt == (child.retry_count or 0), JobArtifact.logical_path == DIRECTORY + '/manifest.json'))
        if manifest:
            artifact, rows = await native_sequences(session, child)
            refs.extend({'kind': 'native_sequence', 'job_id': child.id, 'publication_ref': artifact.id,
                         'native_output_id': row['design_id']} for row in rows)
    # Registered molecular outputs not projected as Designs remain reusable.
    design_paths = {d.pdb_path for d in await session.scalars(select(Design).where(Design.job_id == child.id))}
    for artifact in await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == child.id,
            JobArtifact.attempt == (child.retry_count or 0)).order_by(JobArtifact.logical_path)):
        doc = (artifact.provenance or {}).get('native_document')
        if doc and artifact.storage_path not in design_paths and artifact.media_type in {'chemical/x-pdb', 'chemical/x-mmcif'}:
            refs.append({'kind': 'native_document', 'job_id': child.id, 'artifact_id': artifact.id})
    return refs


def prediction_request(root, row, target, model, params, placement):
    from services.binder_round_inputs import source_components
    if row.get('sequence'):
        binder = [{'id': (row.get('binder_chains') or ['binder'])[0], 'type': 'protein', 'sequence': row['sequence']}]
    else:
        if not row.get('path') or not row.get('binder_chains'):
            raise ValueError('Prediction needs the selected binder sequence or declared structural binder roles')
        binder = source_components(row['path'], row['binder_chains'], 'binder')
    targets = deepcopy(target.get('components') or [])
    if not targets:
        raise ValueError('No independently declared target components are available')
    components, mapping = [], []
    for role, group in [('binder', binder), ('target', targets)]:
        for component in group:
            label = chr(65 + len(components)) if len(components) < 26 else f'C{len(components)}'
            components.append({**{k:v for k,v in component.items() if k not in {'source_residues', 'source'}}, 'id': label})
            mapping.append({'id': label, 'source_chain': component['id'], 'role': role,
                            'sequence': component.get('sequence')})
    ref = row['ref']
    params = deepcopy(params)
    params.update(complex_components=components,
        sequence=''.join(c['sequence'] for c in binder) if model == 'esmfold2' else ':'.join(c.get('sequence', '') for c in components if c.get('type') == 'protein'),
        sequence_name=ref.get('design_id') or ref.get('native_output_id') or ref.get('native_record_id') or ref.get('artifact_id'),
        lineage_root_job_id=root.id, iteration_source_root_job_id=root.id,
        iteration_source_job_id=row['owner_job_id'], source_stage_job_id=row['owner_job_id'],
        selection_source_job_id=row['owner_job_id'], source_selection_count=1,
        selection_source_type='selected_designs' if ref['kind'] == 'design' else 'selected_native_sequences',
        iteration_source_design_ids=[ref['design_id']] if ref['kind'] == 'design' else [])
    if ref['kind'] == 'design':
        params['source_design_id'] = ref['design_id']
    child = JobCreate(name='selected-prediction', model_id=model, mode=MODES[model], params=params,
                      execution_target_id=placement)
    return child, {'target_binding': target, 'input_components': mapping,
                   'binder_chains': [m['id'] for m in mapping if m['role'] == 'binder'],
                   'target_chains': [m['id'] for m in mapping if m['role'] == 'target']}


def structural_selection(row, root_id):
    """Derive PDB only for its consuming branch, from retained exact bytes.

    The native snapshot remains independent, including an alternate/MODEL choice.
    Conversion failure belongs to this branch, not sibling native consumers.
    """
    import tempfile
    from paths import resolve_runtime_data_path
    from services.binder_continuation import _write_source_identity
    source = Path(row['selection_dir'])
    manifest = json.loads((source / 'selection_manifest.json').read_text())
    if all(Path(item['selection_pdb_path']).suffix.lower() == '.pdb' for item in manifest['designs']):
        return source
    directory = Path(tempfile.mkdtemp(prefix='pdb-branch-', dir=source.parent))
    try:
        for index, item in enumerate(manifest['designs']):
            original = resolve_runtime_data_path(item.get('native_source_structure_path') or item['selection_pdb_path'])
            raw = original.read_bytes()
            expected = item.get('native_source_structure_sha256') or item['selection_structure_sha256']
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('Retained source bytes changed')
            destination = directory / str(index)
            destination.mkdir()
            selected = Path(item['selection_pdb_path'])
            if hashlib.sha256(selected.read_bytes()).hexdigest() != item['selection_structure_sha256']:
                raise ValueError('Retained selected-model bytes changed')
            from routers.jobs import _cif_selection_pdb
            consumed = _cif_selection_pdb(selected, destination / 'derived.pdb')
            item['native_source_structure_path'] = str(original)
            item['native_source_structure_sha256'] = expected
            item['selection_pdb_path'] = str(consumed)
            item['selection_structure_sha256'] = hashlib.sha256(consumed.read_bytes()).hexdigest()
        manifest['source_selection_manifest_path'] = str(directory / 'selection_manifest.json')
        (directory / 'selection_manifest.json').write_text(json.dumps(manifest, sort_keys=True))
        _write_source_identity(directory, root_id)
        return directory
    except Exception:
        import shutil
        shutil.rmtree(directory)
        raise


def role_defaults(model, params, explicit, row):
    """Approved producer-role initialization; saved edits/clears win."""
    result = deepcopy(params)
    fields = {'proteinmpnn': {'design_chain': 'binder_chains'},
              'fampnn': {'design_chain': 'binder_chains', 'target_chain': 'target_chains'},
              'caliby_binder': {'binder_chains': 'binder_chains', 'target_chains': 'target_chains'},
              'binder_refinement': {'binder_chains': 'binder_chains', 'target_chains': 'target_chains'},
              'protonpottsmpnn': {'binder_chain': 'binder_chains'}}.get(model, {})
    for field, role in fields.items():
        if field not in explicit and row.get(role):
            result[field] = ','.join(row[role])
    return result


async def plan(session, root, retained):
    from routers.jobs import normalize_job_request
    from services.binder_continuation import model_request, individual_model_requests
    request = SelectedRoundRequest.model_validate(retained['request'])
    steps = retained['steps']
    for stage in request.round_plan.stages:
        sources = [(row, []) for row in retained['selection']['resolved']] if stage.input.selected else []
        if stage.input.stage_id:
            for parent_id, parent in list(steps.items()):
                if parent['metadata']['stage_id'] != stage.input.stage_id or parent.get('superseded_by'):
                    continue
                child = await session.get(Job, parent['job_id']) if parent.get('job_id') else None
                if child is None:
                    continue
                parent.update(state=child.status, outputs=await output_refs(session, child))
                for ref in parent['outputs']:
                    key = digest(ref)
                    if key not in retained['resolved_outputs']:
                        try:
                            retained['resolved_outputs'][key] = await resolve_ref(session, root, ref, output_format='native')
                            retained['errors'].pop(key, None)
                        except (ValueError, OSError, HTTPException) as exc:
                            retained['errors'][key] = str(exc.detail if isinstance(exc, HTTPException) else exc)
                            continue
                    sources.append((retained['resolved_outputs'][key], [parent_id]))
        invocations = stage.models if stage.kind != 'maturation' else [stage]
        for row, parents in sources:
            for invocation in invocations:
                targets = row['targets'] if stage.kind == 'prediction' else [None]
                if not targets:
                    retained['errors'][stage.id + ':' + digest(row['ref'])] = 'No independently declared target input is available'
                for target in targets:
                    for sample in range(invocation.params.get('num_parallel_jobs', 1) or 1):
                        key = digest([retained['round_id'], stage.id, row['ref'], invocation.model_id, target, sample])
                        step_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'bms:selected-round:' + key))
                        if step_id in steps:
                            continue
                        try:
                            model = invocation.model_id
                            binding = {}
                            if stage.kind == 'prediction':
                                child, binding = prediction_request(root, row, target, model, invocation.params, request.execution_target_id)
                            else:
                                if not row.get('selection_dir'):
                                    raise ValueError('This native model requires a structure; the selected output is sequence-only')
                                operation = {'binder_refinement': 'refine', 'caliby_binder': 'caliby'}.get(model, model)
                                owner = await session.get(Job, row['owner_job_id'])
                                directory = (Path(row['selection_dir']) if model == 'protonpottsmpnn'
                                             else structural_selection(row, root.id))
                                explicit_stage = next(s for s in retained.get('submitted_request', retained['request'])['round_plan']['stages'] if s['id'] == stage.id)
                                explicit = (explicit_stage['params'] if stage.kind == 'maturation' else
                                    next(m['params'] for m in explicit_stage['models'] if m['model_id'] == model))
                                params = role_defaults(model, invocation.params, explicit, row)
                                if (model == 'binder_refinement' and params.get('ppiflow_task') == 'protein_binder_partial'
                                        and 'ppiflow_input_model' not in explicit):
                                    from scripts.run_ppiflow_protein_partial import prepared_model
                                    prepared = json.loads((directory / 'selection_manifest.json').read_text())
                                    params['ppiflow_input_model'] = prepared_model(prepared['designs'][0]['selection_pdb_path'])
                                base = model_request(operation=operation, params=params, source=owner,
                                    root=root, selection_dir=directory, execution_target_id=request.execution_target_id)
                                child = individual_model_requests(base, operation, directory)[0]
                            child.name = f'binder-round-{step_id}'
                            child.params['num_parallel_jobs'] = 1
                            child.execution_policy = request.execution_policy.model_copy(deep=True)
                            child.binder_round_step = BinderRoundStepReference(root_job_id=root.id, step_id=step_id)
                            child = normalize_job_request(child)
                            metadata = {'schema_version': 1, 'round_id': retained['round_id'], 'step_id': step_id,
                                'stage_id': stage.id, 'stage': stage.kind, 'model_id': model, 'root_job_id': root.id,
                                'source_job_id': row['owner_job_id'], 'source_ref': row['ref'],
                                'source_design_id': row['ref'].get('design_id'),
                                'backbone_design_id': row['ref'].get('design_id'),
                                'effective_params': deepcopy(child.params),
                                'parent_step_ids': parents, 'sample_index': sample,
                                'independent_targets': deepcopy(row['targets']),
                                'target_state': target.get('name') if target else None,
                                'binder_chains': row.get('binder_chains', []), 'target_chains': row.get('target_chains', []), **binding}
                            steps[step_id] = {'metadata': metadata, 'request': child.model_dump(mode='json'),
                                             'state': 'prepared', 'job_id': None, 'outputs': []}
                            retained['errors'].pop(step_id, None)
                        except (ValueError, OSError, HTTPException) as exc:
                            retained['errors'][step_id] = str(exc.detail if isinstance(exc, HTTPException) else exc)


async def read_selected(session, root, *, round_id=None):
    result = []
    for retained in rounds.progress_of(root).get(KEY, []):
        if round_id is not None and retained['round_id'] != round_id:
            continue
        row = {k: deepcopy(retained[k]) for k in ('round_id', 'source_job_id', 'root_job_id', 'state', 'request', 'selection')}
        row['selection'].pop('resolved', None)
        row['errors'] = deepcopy(retained['errors'])
        row['steps'] = []
        for identity, step in retained['steps'].items():
            child = await session.get(Job, step['job_id']) if step.get('job_id') else None
            if child is None:
                child = await session.scalar(select(Job).where(Job.provenance[rounds.STEP]['step_id'].as_string() == identity,
                    Job.provenance[rounds.STEP]['root_job_id'].as_string() == root.id))
            projected = {**deepcopy(step['metadata']), 'state': child.status if child else step['state'],
                         'job_id': child.id if child else step.get('job_id'),
                         'outputs': await output_refs(session, child) if child else []}
            for key in ('review', 'error', 'superseded_by'):
                if key in step:
                    projected[key] = deepcopy(step[key])
            row['steps'].append(projected)
        result.append(row)
    return result


async def submit(session, experiments, request):
    from services.binder_continuation import resolve_root
    source, root = await resolve_root(session, request.source_job_id)
    root_id = root.id
    root = await rounds.lock_root(session, root_id)
    progress = rounds.progress_of(root)
    intents = progress.setdefault(KEY, [])
    # Compare submitted intent before source resolution or default expansion so
    # exact replay survives cleanup and registry default changes.
    intent_digest = digest(request.model_dump(mode='json'))
    retained = next((r for r in intents if r['idempotency_key'] == request.idempotency_key), None)
    if retained:
        if retained['intent_digest'] != intent_digest:
            raise HTTPException(409, 'Selected round idempotency key has a different intent')
        identity = retained['round_id']
        await session.rollback()
    else:
        normalized = normalize(request)
        refs = await selection_refs(session, source, request)
        resolved = [await resolve_ref(session, root, ref, output_format='native') for ref in refs]
        identity = str(uuid.uuid4())
        retained = {'round_id': identity, 'root_job_id': root.id, 'source_job_id': source.id,
            'submitted_request': request.model_dump(mode='json'),
            'idempotency_key': request.idempotency_key, 'intent_digest': intent_digest,
            'request': normalized.model_dump(mode='json'), 'state': 'pending', 'steps': {}, 'errors': {},
            'selection': {'candidate_refs': refs, 'resolved': resolved}, 'resolved_outputs': {}}
        intents.append(retained)
        rounds.save(root, progress)
        await session.commit()
    return await reconcile(session, experiments, root_id, identity)


async def reconcile(session, experiments, root_id, round_id, *, retry=False):
    from routers.jobs import submit_selected_child_jobs
    root = await rounds.lock_root(session, root_id)
    progress = rounds.progress_of(root)
    retained = next((r for r in progress.get(KEY, []) if r['round_id'] == round_id), None)
    if retained is None:
        raise HTTPException(404, 'Selected round not found')
    if retry:
        retained['errors'] = {}
        for old_id, step in list(retained['steps'].items()):
            child = await session.get(Job, step['job_id']) if step.get('job_id') else None
            if step.get('superseded_by') or child is None or child.status not in {'failed', 'cancelled'}:
                continue
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'bms:selected-round:retry:' + old_id))
            replacement = deepcopy(step)
            replacement.update(state='prepared', job_id=None, outputs=[])
            for key in ('review', 'error'):
                replacement.pop(key, None)
            replacement['metadata'].update(step_id=identity, retry_of_step_id=old_id, retry_of_job_id=child.id)
            replacement['request'].update(name=f'binder-round-{identity}', launch_context_id=None,
                execution_plan_approval=None, binder_round_step={'root_job_id': root_id, 'step_id': identity})
            for key in ('workflow_adapter', '_global_resource_admission', '_global_dispatch_authority'):
                replacement['request']['params'].pop(key, None)
            step['superseded_by'] = identity
            retained['steps'][identity] = replacement
    await plan(session, root, retained)
    rounds.save(root, progress)
    await session.commit()
    for identity in list(retained['steps']):
        root = await rounds.lock_root(session, root_id)
        progress = rounds.progress_of(root)
        retained = next(r for r in progress[KEY] if r['round_id'] == round_id)
        step = retained['steps'][identity]
        child = await session.scalar(select(Job).where(Job.provenance[rounds.STEP]['root_job_id'].as_string() == root_id,
            Job.provenance[rounds.STEP]['step_id'].as_string() == identity))
        if child:
            step.update(job_id=child.id, state=child.status)
            rounds.save(root, progress)
            await session.commit()
            continue
        if step.get('superseded_by') or step['state'] == 'review_required' or (step['state'] == 'error' and not retry):
            await session.rollback()
            continue
        tasks = BackgroundTasks()
        try:
            response = await submit_selected_child_jobs([JobCreate.model_validate(step['request'])], tasks, session, experiments,
                destination_launch_context_id=retained['request']['launch_context_id'], idempotency_key=identity,
                response_context={'operation': 'round', 'root_job_id': root_id, 'round_id': round_id,
                                  'binder_round_step_id': identity, 'source_job_id': retained['source_job_id']})
            outcome = {'state': 'queued', 'job_id': response[0].id}
        except HTTPException as exc:
            if exc.status_code == 409 and isinstance(exc.detail, dict) and exc.detail.get('code') == 'remote_prepared_job_review_required':
                outcome = {'state': 'review_required', 'review': exc.detail}
                prepared = exc.detail.get('job_request') or exc.detail.get('job_requests', [None])[0]
                if prepared:
                    outcome['request'] = prepared
            else:
                await session.rollback()
                outcome = {'state': 'error', 'error': str(exc.detail)}
        except Exception as exc:
            await session.rollback()
            outcome = {'state': 'error', 'error': str(exc)}
        root = await rounds.lock_root(session, root_id)
        progress = rounds.progress_of(root)
        retained = next(r for r in progress[KEY] if r['round_id'] == round_id)
        retained['steps'][identity].update(outcome)
        rounds.save(root, progress)
        await session.commit()
        await tasks()
    root = await rounds.lock_root(session, root_id)
    progress = rounds.progress_of(root)
    retained = next(r for r in progress[KEY] if r['round_id'] == round_id)
    states = {s['state'] for s in retained['steps'].values() if not s.get('superseded_by')}
    retained['state'] = ('needs_retry' if retained['errors'] or 'error' in states else
        'review_required' if 'review_required' in states else
        'running' if states - TERMINAL else 'completed_with_errors' if states & {'failed', 'cancelled'} else 'completed')
    rounds.save(root, progress)
    await session.commit()
    return (await read_selected(session, root, round_id=round_id))[0]


async def response(session, retained):
    from routers.jobs import get_job
    refs = retained['selection']['candidate_refs']
    return {'operation': 'round', 'source_job_id': retained['source_job_id'], 'root_job_id': retained['root_job_id'],
        'round_id': retained['round_id'], 'state': retained['state'],
        'selected_design_count': sum(r['kind'] == 'design' for r in refs),
        'selected_native_count': sum(r['kind'] in {'native_document', 'native_observation'} for r in refs),
        'selected_sequence_count': sum(r['kind'] == 'native_sequence' for r in refs),
        'launched_jobs': [await get_job(s['job_id'], session) for s in retained['steps'] if s['job_id']], 'round': retained}
