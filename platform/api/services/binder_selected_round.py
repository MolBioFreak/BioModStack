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

from fastapi import HTTPException
from sqlalchemy import select

from database import Design, Job, JobArtifact
from schemas import CandidateRef, JobCreate, BinderRoundStepReference, SelectedRoundRequest
from services import binder_round as rounds

KEY = 'selected_rounds'
TERMINAL = {'completed', 'failed', 'cancelled'}
MODES = {'proteinmpnn': 'design', 'fampnn': 'binder_design', 'caliby_binder': 'design',
         'protonpottsmpnn': 'redesign', 'binder_refinement': 'refine',
         'protenix': 'complex', 'boltz2': 'complex', 'esmfold2': 'predict',
         'boltz_cp_experimental': 'design', 'molecular_dynamics': 'simulate'}


def wire(ref):
    return CandidateRef.model_validate(ref).model_dump(mode='json', exclude_none=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def invocation_mode(invocation):
    """Explicit native modes for v2; historical callers retain their mode."""
    return getattr(invocation, 'mode', None) or MODES[invocation.model_id]


def invocation_key(invocation):
    return getattr(invocation, 'invocation_id', None) or invocation.model_id


def normalize(request):
    from model_registry import get_registry
    from services.binder_round_inputs import BOUND_INPUTS, BLIND_FIXED_PARAMETERS
    result = request.model_copy(deep=True)
    registry = get_registry()
    for stage in result.round_plan.stages:
        invocations = stage.models if stage.kind != 'maturation' else [stage]
        for invocation in invocations:
            model = invocation.model_id
            if model == 'molecular_dynamics':
                # The existing native MD owner validates its intent after exact
                # source binding; do not merge generic model catalog defaults.
                continue
            if result.round_plan.schema_version == 2 and stage.kind != 'maturation' and 'execution_target_id' not in invocation.model_fields_set:
                invocation.execution_target_id = result.execution_target_id
            from services.sequence_designer_settings import normalize_historical_sequence_settings
            invocation.params = normalize_historical_sequence_settings(model, invocation.params)
            mode = invocation_mode(invocation)
            if model == 'caliby_experimental':
                from services.caliby_native import operation_settings
                invocation.params = operation_settings(mode, invocation.params)
                continue
            definition = registry.get_internal_model_definition(model)
            if definition is None or not any(m.id == mode for m in definition.modes):
                raise ValueError(f'Unsupported model/mode {model}/{mode}')
            known = {field.name for field in definition.params}
            if stage.kind == 'prediction':
                # Native chemistry bypasses no source owner: the ordinary model
                # compiler validates it after exact component binding.
                known |= {'boltz2': {'constraints', 'properties', 'templates'},
                          'esmfold2': {'covalent_bonds', 'distogram_conditioning'}}.get(model, set())
            if set(invocation.params) - known:
                raise ValueError(f'Unknown {model} settings: {sorted(set(invocation.params) - known)}')
            # Roles are operator settings; physical source paths are server-owned.
            bound = BOUND_INPUTS - {'binder_chains', 'target_chains', 'design_chain', 'target_chain'}
            bound |= {'target_pdb'}
            if model == 'boltz_cp_experimental':
                bound.add('input_path')
            if any(invocation.params.get(key) for key in bound):
                raise ValueError('Round inputs are bound from the selected candidate and independent target')
            if stage.kind == 'prediction' and any(invocation.params.get(key) for key in BLIND_FIXED_PARAMETERS):
                raise ValueError('Blind prediction does not accept pose conditioning')
            defaults = {f.name: deepcopy(f.default) for f in definition.params
                        if f.default is not None and f.name not in bound}
            if stage.kind == 'prediction':
                defaults.update({key: False for key in BLIND_FIXED_PARAMETERS if key in known})
            # Omission is source inheritance, not an authored empty override.
            # Keep it sparse across normalization and retained-plan replay.
            if stage.kind == 'prediction':
                defaults.pop('covalent_bonds', None)
            invocation.params = {**defaults, **invocation.params}
            if model in {'protonpottsmpnn', 'ligandmpnn'}:
                # Final native validation runs after exact source/roles bind.
                # LigandMPNN's chemistry validator also needs real coordinates.
                errors = []  # Model owner validates the complete source-bound request.
            elif model == 'protenix' and stage.kind == 'prediction':
                # Validate the native field shapes now, but entity/atom references
                # only after the source owner supplies ordered complex_components.
                # The ordinary Job/compiler still runs full validate_chemistry.
                from pydantic import TypeAdapter
                from scripts.protenix_chemistry import ProtenixChemistry
                chemistry = {'covalent_bonds', 'constraint'}
                for key in chemistry & invocation.params.keys():
                    TypeAdapter(ProtenixChemistry.model_fields[key].annotation).validate_python(invocation.params[key])
                errors = registry.validate_job_params(model, mode,
                    {key: value for key, value in invocation.params.items() if key not in chemistry})
            else:
                errors = registry.validate_job_params(model, mode, invocation.params)
            errors = [e for e in errors if e not in {f'Missing required parameter: {k}'
                      for k in bound | {'binder_chain', 'binder_chains', 'target_chains', 'design_chain', 'target_chain'}}]
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


async def native_operation_records(session, owner):
    """Adapt existing verified native publications, never annotate their custody."""
    if owner.model_id == 'caliby_experimental':
        from services.caliby_native_publication import read_published_native_results, DIRECTORY
        primary = 'caliby_results.json'
    elif owner.model_id == 'ligandmpnn':
        from services.ligandmpnn_design_publication import read_published_native_results, DIRECTORY
        primary = 'manifest.json'
    else:
        raise ValueError('Unsupported native operation publication')
    publication = await read_published_native_results(owner, session)
    manifest = next(a for a in publication['artifacts'] if a['path'] == primary)
    result = []
    for record in publication['records']:
        structures = ([record['structure']] if owner.model_id == 'caliby_experimental' else
                      [a for a in record['artifacts'] if a['kind'] == 'structure'])
        if structures:
            refs = [{'kind': 'native_document', 'job_id': owner.id, 'artifact_id': a['artifact_id']} for a in structures]
        else:
            producer = record['producer']
            native_id = json.dumps([producer['name'], producer['batch_idx'], producer['design_idx']], separators=(',', ':'))
            refs = [{'kind': 'native_sequence', 'job_id': owner.id, 'publication_ref': manifest['artifact_id'],
                     'native_output_id': native_id}]
        result.extend((ref, record) for ref in refs)
    return publication, result


async def operation_document(session, owner, ref):
    _, records = await native_operation_records(session, owner)
    matches = [record for candidate, record in records if candidate.get('artifact_id') == ref['artifact_id']]
    if len(matches) != 1:
        raise ValueError('Selected native document is not an exact published record')
    artifact = await session.get(JobArtifact, ref['artifact_id'])
    # The model reader has already checked attempt/path/hash/provenance equality.
    return Path(artifact.storage_path), {'artifact_id': artifact.id, 'artifact_sha256': artifact.sha256,
        'owner_job_id': owner.id, 'producer_model_id': owner.model_id,
        'lineage_root_job_id': (owner.provenance or {}).get(rounds.STEP, {}).get('root_job_id', owner.id),
        'producer_document': {'sha256': artifact.sha256, 'native_record': matches[0]}}


def retained_candidate_directory(root, ref):
    """Recover an exact prior source snapshot after producer cleanup."""
    for previous in [rounds.progress_of(root), *rounds.progress_of(root).get(KEY, [])]:
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


def retained_rows(root):
    for progress in [rounds.progress_of(root), *rounds.progress_of(root).get(KEY, [])]:
        yield from progress.get('selection', {}).get('resolved', [])
        yield from progress.get('resolved_outputs', {}).values()


async def retained_native_row(session, root, owner, ref):
    """Reuse the existing retained source only after normal byte cleanup.

    Live bytes still traverse their native reader; malformed/tampered publication
    errors never select this path. Job/attempt/artifact identity remains required
    by the original publication contract, not inferred from a display label.
    """
    artifact_id = ref.get('artifact_id') or ref.get('publication_ref')
    if not artifact_id:
        return None
    artifact = await session.get(JobArtifact, artifact_id)
    if artifact is None or Path(artifact.storage_path).exists():
        return None
    for row in retained_rows(root):
        if row.get('ref') != ref or not row.get('source_publication'):
            continue
        expected = row['source_publication']
        actual = publication_identity(owner, artifact)
        if expected != actual:
            raise ValueError('Retained native publication identity changed')
        restored = deepcopy(row)
        if row.get('selection_dir'):
            directory = retained_candidate_directory(root, ref)
            restored['selection_dir'] = str(directory)
            restored['path'] = json.loads((directory / 'selection_manifest.json').read_text())['designs'][0]['selection_pdb_path']
        return restored
    return None


def publication_identity(owner, artifact):
    return {'artifact_id': artifact.id, 'owner_job_id': artifact.owner_job_id,
            'attempt': artifact.attempt, 'owner_attempt': owner.retry_count or 0,
            'remote_attempt_id': owner.remote_attempt_id, 'sha256': artifact.sha256,
            'logical_path': artifact.logical_path, 'provenance': deepcopy(artifact.provenance)}


async def resolve_ref(session, root, ref, *, snapshot=True, output_format="pdb"):
    from services.binder_continuation import resolve_root, resolve_selection, resolve_candidate_documents, snapshot_selection
    from services.binder_round_inputs import candidate_roles
    from services.binder_diagnostic_selection import CandidateDocument, declared_targets
    ref = wire(ref)
    owner, owner_root = await resolve_root(session, ref['job_id'])
    if owner_root.id != root.id:
        raise ValueError('Selected candidate crosses the source-root boundary')
    if snapshot and ref['kind'] in {'native_document', 'native_sequence', 'native_observation'}:
        retained = await retained_native_row(session, root, owner, ref)
        if retained is not None:
            return retained
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
        if owner.model_id in {'caliby_experimental', 'ligandmpnn'}:
            path, identity = await operation_document(session, owner, ref)
        else:
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
            try:
                directory = snapshot_selection(owner, root, [], native_sources=[(path, identity)], output_format=output_format)
            except FileNotFoundError:
                directory = retained_candidate_directory(root, ref)
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
    elif ref['kind'] == 'native_sequence' and owner.model_id == 'ligandmpnn':
        _, records = await native_operation_records(session, owner)
        matches = [record for candidate, record in records if candidate == ref]
        if len(matches) != 1:
            raise ValueError('Native sequence identity is missing or ambiguous')
        artifact = await session.get(JobArtifact, ref['publication_ref'])
        row.update(sequence=matches[0]['native_output']['sequence'], native_record=deepcopy(matches[0]),
                   binder_chains=prior.get('binder_chains', []), target_chains=prior.get('target_chains', []),
                   publication_sha256=artifact.sha256)
    elif ref['kind'] == 'native_sequence' and owner.model_id in {'proteinmpnn', 'fampnn'}:
        from services.binder_native_selection import read_native_sequence_candidates
        artifact, records = await read_native_sequence_candidates(session, owner, verify_bytes=True)
        matches = [record for record in records if record['candidate_ref'] == ref]
        if len(matches) != 1:
            raise ValueError('Native sequence identity is missing or ambiguous')
        record = matches[0]
        row.update(sequence=record['sequence'], native_record=deepcopy(record),
                   binder_chains=prior.get('binder_chains', []), target_chains=prior.get('target_chains', []),
                   publication_sha256=artifact.sha256)
        # A sequence index is not a coordinate output, even when its producer
        # recorded a source backbone. Structure consumers must choose a pose.
    elif ref['kind'] in {'native_sequence', 'native_observation'} and (owner.provenance or {}).get('native_generation_publication'):
        from services.rfd4_proteina_results import read_publication_record
        output, publication = await read_publication_record(owner, session, ref)
        row.update(sequence=output.get('sequence'), binder_chains=output.get('binder_chains', []),
                   target_chains=output.get('target_chains', []), native_record=output,
                   publication_sha256=publication['manifest_sha256'])
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
        for previous in [rounds.progress_of(root), *rounds.progress_of(root).get(KEY, [])]:
            retained_rows = list(previous.get('selection', {}).get('resolved', []))
            retained_rows += list(previous.get('resolved_outputs', {}).values())
            match = next((r for r in retained_rows if r.get('ref') == ref
                          and any(t.get('components') for t in r.get('targets', []))), None)
            if match:
                targets = deepcopy(match['targets'])
                break
    row['targets'] = targets
    artifact_id = ref.get('artifact_id') or ref.get('publication_ref')
    if artifact_id:
        artifact = await session.get(JobArtifact, artifact_id)
        if artifact is not None:
            row['source_publication'] = publication_identity(owner, artifact)
    return row


async def output_refs(session, child):
    refs = [{'kind': 'design', 'job_id': child.id, 'design_id': d.id}
            for d in await session.scalars(select(Design).where(Design.job_id == child.id).order_by(Design.id))]
    if (child.provenance or {}).get('native_generation_publication'):
        from services.rfd4_proteina_results import read_published_generation_page
        offset = 0
        while True:
            page = await read_published_generation_page(child, session, offset=offset, limit=1000)
            refs.extend(wire(record['candidate_ref']) for record in page['records'])
            offset += len(page['records'])
            if offset >= page['total']:
                break
            if not page['records']:
                raise ValueError('Native publication page ended before its declared total')
    if child.model_id in {'caliby_experimental', 'ligandmpnn'}:
        _, records = await native_operation_records(session, child)
        refs.extend(ref for ref, _ in records)
    if child.model_id == 'protonpottsmpnn':
        # No result yet is not a fabricated empty successful publication.
        from services.protonpottsmpnn_design import DIRECTORY
        manifest = await session.scalar(select(JobArtifact.id).where(JobArtifact.owner_job_id == child.id,
            JobArtifact.attempt == (child.retry_count or 0), JobArtifact.logical_path == DIRECTORY + '/manifest.json'))
        if not manifest and child.status == 'completed':
            raise FileNotFoundError('Native sequence publication is not registered yet')
        if manifest:
            artifact, rows = await native_sequences(session, child)
            refs.extend({'kind': 'native_sequence', 'job_id': child.id, 'publication_ref': artifact.id,
                         'native_output_id': row['design_id']} for row in rows)
    if child.model_id in {'proteinmpnn', 'fampnn'}:
        from services.binder_native_selection import read_native_sequence_candidates
        try:
            _, records = await read_native_sequence_candidates(session, child)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            if not refs and child.status == 'completed':
                raise FileNotFoundError('Native sequence publication is not registered yet') from exc
        else:
            # Native membership is authoritative; its selected subset may also
            # have Designs. Keep coordinates for those exact projected records.
            designs = list(await session.scalars(select(Design).where(Design.job_id == child.id)))
            projected = {(d.provenance or {}).get('sequence_design_candidate', {}).get('name') for d in designs}
            refs.extend(wire(record['candidate_ref']) for record in records
                        if not (record.get('selected') and record['native_output_id'] in projected))
    # Registered molecular outputs not projected as Designs remain reusable.
    design_paths = {d.pdb_path for d in await session.scalars(select(Design).where(Design.job_id == child.id))}
    for artifact in await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == child.id,
            JobArtifact.attempt == (child.retry_count or 0)).order_by(JobArtifact.logical_path)):
        doc = (artifact.provenance or {}).get('native_document')
        if doc and artifact.storage_path not in design_paths and artifact.media_type in {'chemical/x-pdb', 'chemical/x-mmcif'}:
            refs.append({'kind': 'native_document', 'job_id': child.id, 'artifact_id': artifact.id})
    # One registered coordinate projection and its Design are the same source;
    # sequence rows sharing that backbone remain distinct native candidates.
    unique = {}
    for ref in refs:
        if ref['kind'] == 'native_document':
            artifact = await session.get(JobArtifact, ref['artifact_id'])
            if artifact is not None and artifact.storage_path in design_paths:
                continue
        unique[digest(ref)] = ref
    return list(unique.values())


def prediction_request(root, row, target, model, params, placement, *, scope='binder', selected_chains=None, mode=None):
    from services.binder_round_inputs import source_components
    if scope == 'whole_composition' and row.get('components'):
        binder = deepcopy(row['components'])
    elif row.get('sequence'):
        binder = [{'id': (row.get('binder_chains') or ['binder'])[0], 'type': 'protein', 'sequence': row['sequence']}]
    else:
        if scope == 'whole_composition' and row.get('path'):
            from services.binder_source_materialization import materialize_source_bytes, structure_suffix
            path = Path(row['path'])
            directory = Path(row['selection_dir']) / 'composition'
            directory.mkdir(exist_ok=True)
            prepared = materialize_source_bytes(path.read_bytes(), structure_suffix(path), directory,
                                                output_format='native')
            recipe = prepared['native_recipe']
            binder = deepcopy(recipe['complex_components'])
            # The native chemistry owner reports unresolved atoms/identities;
            # never manufacture ligand chemistry from a coordinate label.
            row = {**row, 'covalent_bonds': recipe.get('covalent_bonds', []),
                   'unresolved_chemistry': recipe.get('unresolved', [])}
        elif not row.get('path') or not row.get('binder_chains'):
            raise ValueError('Prediction needs the selected binder sequence or declared structural binder roles')
        else:
            binder = source_components(row['path'], row['binder_chains'], 'binder')
    bonds = deepcopy(row.get('covalent_bonds', [])) if scope == 'whole_composition' else []
    if selected_chains is not None:
        remap, copies, selected = {}, {}, []
        for old, component in enumerate(binder, 1):
            ids = component.get('copy_ids') or component['id']
            ids = ids if isinstance(ids, list) else [ids]
            whole = isinstance(component['id'], str) and component['id'] in selected_chains
            kept = [i for i, chain in enumerate(ids, 1) if whole or chain in selected_chains]
            if not kept:
                continue
            remap[old] = len(selected) + 1
            copies[old] = ({i: i for i in range(1, max(component.get('count', 1), len(ids)) + 1)}
                           if whole else {i: new for new, i in enumerate(kept, 1)})
            if len(kept) != len(ids):
                component = {**component, 'copy_ids': [ids[i - 1] for i in kept], 'count': len(kept)}
                if isinstance(component['id'], list):
                    component['id'] = component['copy_ids']
            selected.append(component)
        selected_bonds = []
        for bond in bonds:
            left, right = bond['left_entity'], bond['right_entity']
            if left not in remap or right not in remap:
                continue
            def source_copies(entity, side):
                value = bond.get(side + '_copy')
                component = binder[entity - 1]
                ids = component.get('copy_ids') or component['id']
                count = max(component.get('count', 1), len(ids) if isinstance(ids, list) else 1)
                return [value] if value is not None else range(1, count + 1)
            if all(bond.get(side + '_copy') is None for side in ('left', 'right')) and all(
                    list(copies[e]) == list(source_copies(e, side)) for e, side in ((left, 'left'), (right, 'right'))):
                selected_bonds.append({**bond, 'left_entity': remap[left], 'right_entity': remap[right]})
                continue
            for lc, rc in zip(source_copies(left, 'left'), source_copies(right, 'right')):
                if lc in copies[left] and rc in copies[right]:
                    selected_bonds.append({**bond, 'left_entity': remap[left], 'right_entity': remap[right],
                                           'left_copy': copies[left][lc], 'right_copy': copies[right][rc]})
        binder, bonds = selected, selected_bonds
    targets = deepcopy((target or {}).get('components') or [])
    if not targets and scope != 'whole_composition':
        raise ValueError('No independently declared target components are available')
    components, mapping = [], []
    for role, group in [('binder', binder), ('target', targets)]:
        for component in group:
            label = component['id'] if scope == 'whole_composition' else (chr(65 + len(components)) if len(components) < 26 else f'C{len(components)}')
            components.append({**{k:v for k,v in component.items() if k not in {'source_residues', 'source'}}, 'id': label})
            mapping.append({'id': label, 'source_chain': component['id'], 'role': role,
                            'sequence': component.get('sequence')})
    ref = row['ref']
    params = deepcopy(params)
    # Strip source-pose conditioning, not native chemistry or MSA controls.
    from services.binder_round_inputs import BLIND_FIXED_PARAMETERS
    for key in BLIND_FIXED_PARAMETERS:
        if key in params:
            params[key] = False
    for key in ('constraint', 'templates', 'template_path', 'initial_guess', 'pocket', 'contact', 'distogram_conditioning', 'esmf_distogram_conditioning'):
        params.pop(key, None)
    if model == 'boltz2' and isinstance(params.get('constraints'), list):
        # Bond chemistry is not pose conditioning. Pocket/contact restraints are.
        params['constraints'] = [entry for entry in params['constraints']
                                 if not isinstance(entry, dict) or not ({'pocket', 'contact'} & entry.keys())]
    for component in components:
        for key in ('templatesPath', 'template', 'initial_guess', 'structure', 'coordinates'):
            component.pop(key, None)
    if 'covalent_bonds' not in params and bonds:
        params['covalent_bonds'] = bonds
    params.update(complex_components=components,
        sequence=('' if model == 'esmfold2' else ':').join(c.get('sequence', '') for c in components if c.get('type') == 'protein'),
        sequence_name=ref.get('design_id') or ref.get('native_output_id') or ref.get('native_record_id') or ref.get('artifact_id'),
        lineage_root_job_id=root.id, iteration_source_root_job_id=root.id,
        iteration_source_job_id=row['owner_job_id'], source_stage_job_id=row['owner_job_id'],
        selection_source_job_id=row['owner_job_id'], source_selection_count=1,
        selection_source_type='selected_designs' if ref['kind'] == 'design' else 'selected_native_sequences',
        iteration_source_design_ids=[ref['design_id']] if ref['kind'] == 'design' else [])
    if ref['kind'] == 'design':
        params['source_design_id'] = ref['design_id']
    child = JobCreate(name='selected-prediction', model_id=model, mode=mode or MODES[model], params=params,
                      execution_target_id=placement)
    return child, {'target_binding': target, 'input_components': mapping,
                   'unresolved_chemistry': deepcopy(row.get('unresolved_chemistry', [])),
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


async def md_request(session, root, row, params, placement, request, step_id):
    """Bind a native MD intent through its existing preview/launch owner.

    No prediction requirement, alternate runner, or scientific defaults live here.
    The parent-owned router hunk exposes its existing request builder verbatim.
    """
    from routers.molecular_dynamics import _compile_native_preview, _native_md_job_request
    from services.md.starting_structures import MdNativeLaunchIntent, MdLaunchRequest, StartingStructureSourceRef, resolve_source, read_resolved_structure
    if not isinstance(params.get('intent'), dict):
        raise ValueError('MD operation requires its existing native launch intent')
    document = deepcopy(params['intent'])
    document.update(name=f'binder-round-{step_id}', execution_target_id=placement,
                    launch_context_id=request.launch_context_id,
                    execution_policy=request.execution_policy.model_dump(mode='json'))
    if not isinstance(document.get('input'), dict):
        raise ValueError('Native MD intent requires its typed input')
    if document['input'].get('kind') == 'guided':
        source_ref = StartingStructureSourceRef(kind='candidate', id=row['owner_job_id'], candidate_ref=row['ref'])
        resolved = await resolve_source(source_ref, session)
        try:
            sha = read_resolved_structure(resolved).sha256
        finally:
            resolved.close()
        saved_source = document['input'].get('source_ref') or {}
        # Keep the prepared Design spelling only for the same primary document,
        # selected MODEL and consumed bytes. A Design ID alone does not identify
        # an alternate document or ensemble member.
        selected = row['ref']
        same_design_source = (
            saved_source.get('kind') == 'design'
            and saved_source.get('id') == selected.get('design_id')
            and not selected.get('document')
            and not selected.get('artifact_id')
            and not selected.get('target_state')
            and saved_source.get('model_number') == selected.get('model')
            and document['input'].get('expected_source_sha256') == sha
        )
        if not same_design_source:
            document['input'].update(source_ref=source_ref.model_dump(mode='json'), expected_source_sha256=sha)
    intent = MdNativeLaunchIntent.model_validate(document)
    preview, resolved, _ = await _compile_native_preview(intent, session)
    try:
        launch = MdLaunchRequest(schema_version='bms.md.launch-request.v1', intent=intent,
                                preview_digest=preview.preview_digest)
        child = _native_md_job_request(launch, preview, resolved)
        return child, {'route': 'md_native', 'md_launch_request': launch.model_dump(mode='json'),
                       'md_preview': preview.model_dump(mode='json')}
    finally:
        if resolved is not None:
            resolved.close()


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
                if getattr(stage.input, 'invocation_id', None) and parent['metadata'].get('invocation_id') != stage.input.invocation_id:
                    continue
                dependency_key = stage.id + ':' + parent_id
                disposition = retained.setdefault('dependencies', {}).setdefault(dependency_key, {})
                child = await session.get(Job, parent['job_id']) if parent.get('job_id') else None
                disposition.update(parent_step_id=parent_id, source_job_id=child.id if child else None,
                                   attempt=(child.retry_count or 0) if child else None)
                if child is None or child.status not in TERMINAL:
                    disposition['state'] = 'awaiting_predecessor'
                    continue
                try:
                    parent.update(state=child.status, outputs=await output_refs(session, child))
                except FileNotFoundError:
                    # An unsuccessful ordinary Job need not publish an output.
                    # Keep that terminal observation distinct from valid empty
                    # success, without rerunning its science automatically.
                    disposition['state'] = ('predecessor_' + child.status if child.status in {'failed', 'cancelled'}
                                            else 'awaiting_publication')
                    continue
                except (ValueError, OSError, HTTPException) as exc:
                    disposition.update(state='publication_error', error=str(exc.detail if isinstance(exc, HTTPException) else exc))
                    retained['errors'][dependency_key] = disposition['error']
                    continue
                disposition.pop('error', None)
                retained['errors'].pop(dependency_key, None)
                disposition['state'] = ('resolved_outputs' if parent['outputs'] else
                                        ('predecessor_' + child.status if child.status in {'failed', 'cancelled'} else 'valid_empty'))
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
                # An explicit ensemble is one ordered invocation, not one copy
                # for every independently selected candidate.
                if getattr(invocation, 'input_groups', []) and row is not sources[0][0]:
                    continue
                whole = getattr(stage.input, 'scope', 'binder') == 'whole_composition'
                targets = row['targets'] if stage.kind == 'prediction' and not whole else [None]
                if not targets:
                    retained['errors'][stage.id + ':' + digest(row['ref'])] = 'No independently declared target input is available'
                for target in targets:
                    for sample in range(invocation.params.get('num_parallel_jobs', 1) or 1):
                        key = digest([retained['round_id'], stage.id, row['ref'], invocation.model_id, target, sample])
                        if request.round_plan.schema_version == 2:
                            key = digest([retained['round_id'], stage.id, row['ref'], invocation_key(invocation),
                                          stage.input.model_dump(mode='json'), target, sample,
                                          [g.model_dump(mode='json') for g in getattr(invocation, 'input_groups', [])]])
                        step_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'bms:selected-round:' + key))
                        if step_id in steps:
                            continue
                        try:
                            model = invocation.model_id
                            mode = invocation_mode(invocation)
                            placement = (invocation.execution_target_id if request.round_plan.schema_version == 2
                                         and 'execution_target_id' in invocation.model_fields_set else request.execution_target_id)
                            binding = {}
                            if stage.kind == 'md':
                                child, binding = await md_request(session, root, row, invocation.params, placement, request, step_id)
                            elif stage.kind == 'prediction':
                                child, binding = prediction_request(root, row, target, model, invocation.params, placement,
                                    scope=getattr(stage.input, 'scope', 'binder'), selected_chains=getattr(stage.input, 'chains', None), mode=mode)
                            elif model == 'caliby_experimental':
                                from services.caliby_native import bind_operation
                                groups, refs = [], []
                                for group in getattr(invocation, 'input_groups', []):
                                    states = []
                                    for state in group.states:
                                        state_key = digest(wire(state.candidate_ref))
                                        if state_key not in retained['resolved_outputs']:
                                            retained['resolved_outputs'][state_key] = await resolve_ref(session, root, state.candidate_ref, output_format='native')
                                        resolved = retained['resolved_outputs'][state_key]
                                        if not resolved.get('path'):
                                            raise ValueError('Caliby requires selected coordinates, not an ancestral sequence backbone')
                                        refs.append(resolved['ref'])
                                        states.append({'state_id': state.state_id, 'path': resolved['path'],
                                            **(state.settings.model_dump(mode='json') if state.settings is not None else {})})
                                    groups.append({'ensemble_id': group.ensemble_id, 'states': states})
                                if not groups:
                                    if not row.get('path'):
                                        raise ValueError('Caliby requires selected coordinates')
                                    groups = [{'ensemble_id': digest(row['ref']), 'states': [{'state_id': digest(row['ref']), 'path': row['path']}]}]
                                    refs = [row['ref']]
                                child = JobCreate(name='selected-caliby', model_id=model, mode=mode,
                                    params=bind_operation(mode, invocation.params, groups), execution_target_id=placement)
                                binding = {'source_refs': refs}
                            elif model == 'ligandmpnn':
                                if not row.get('selection_dir'):
                                    raise ValueError('LigandMPNN requires selected coordinates')
                                directory = Path(row['selection_dir'])
                                manifest = json.loads((directory / 'selection_manifest.json').read_text())
                                child = JobCreate(name='selected-ligandmpnn', model_id=model, mode=mode,
                                    params={**deepcopy(invocation.params), 'target_pdb': manifest['designs'][0]['selection_pdb_path']},
                                    execution_target_id=placement)
                            else:
                                if not row.get('selection_dir'):
                                    raise ValueError('This native model requires a structure; the selected output is sequence-only')
                                operation = {'binder_refinement': 'refine', 'caliby_binder': 'caliby'}.get(model, model)
                                owner = await session.get(Job, row['owner_job_id'])
                                directory = (Path(row['selection_dir']) if model == 'protonpottsmpnn'
                                             else structural_selection(row, root.id))
                                explicit_stage = next(s for s in retained.get('submitted_request', retained['request'])['round_plan']['stages'] if s['id'] == stage.id)
                                explicit = (explicit_stage['params'] if stage.kind == 'maturation' else
                                    next(m['params'] for m in explicit_stage['models'] if
                                         (m.get('invocation_id') or m['model_id']) == invocation_key(invocation)))
                                params = role_defaults(model, invocation.params, explicit, row)
                                if (model == 'binder_refinement' and params.get('ppiflow_task') == 'protein_binder_partial'
                                        and 'ppiflow_input_model' not in explicit):
                                    from scripts.run_ppiflow_protein_partial import prepared_model
                                    prepared = json.loads((directory / 'selection_manifest.json').read_text())
                                    params['ppiflow_input_model'] = prepared_model(prepared['designs'][0]['selection_pdb_path'])
                                base = model_request(operation=operation, params=params, source=owner,
                                    root=root, selection_dir=directory, execution_target_id=placement)
                                child = individual_model_requests(base, operation, directory)[0]
                                child.mode = mode
                            child.name = f'binder-round-{step_id}'
                            if stage.kind != 'md':
                                child.params['num_parallel_jobs'] = 1
                            child.execution_policy = request.execution_policy.model_copy(deep=True)
                            child.binder_round_step = BinderRoundStepReference(root_job_id=root.id, step_id=step_id)
                            if stage.kind != 'md':
                                child = normalize_job_request(child)
                            metadata = {'schema_version': 1, 'round_id': retained['round_id'], 'step_id': step_id,
                                'stage_id': stage.id, 'stage': stage.kind, 'model_id': model, 'root_job_id': root.id,
                                'invocation_id': getattr(invocation, 'invocation_id', None), 'mode': mode,
                                'source_job_id': row['owner_job_id'], 'source_ref': row['ref'],
                                'source_design_id': row['ref'].get('design_id'),
                                'backbone_design_id': row['ref'].get('design_id'),
                                'effective_params': deepcopy(child.params),
                                'parent_step_ids': parents, 'sample_index': sample,
                                'independent_targets': deepcopy(row['targets']),
                                'target_state': target.get('name') if target else None,
                                'binder_chains': row.get('binder_chains', []), 'target_chains': row.get('target_chains', []), **binding}
                            if getattr(invocation, 'route', 'job') == 'boltz_api':
                                from services.boltz_api_jobs import build_boltz_api_input, BOLTZ_API_MODEL
                                metadata.update(route='boltz_api', provider_model=BOLTZ_API_MODEL,
                                    launch_context_id=request.launch_context_id, execution_target_id=placement,
                                    approved_estimate_fingerprint=invocation.approved_estimate_fingerprint,
                                    provider_input=build_boltz_api_input(sequence=child.params.get('sequence', ''),
                                        primary_chain_id='A', complex_components=child.params['complex_components'],
                                        num_samples=child.params.get('boltz_diffusion_samples', 1),
                                        use_msa=child.params.get('boltz_use_msa', True)))
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
        row['dependencies'] = deepcopy(retained.get('dependencies', {}))
        row['steps'] = []
        for identity, step in retained['steps'].items():
            child = await session.get(Job, step['job_id']) if step.get('job_id') else None
            if child is None:
                child = await session.scalar(select(Job).where(Job.provenance[rounds.STEP]['step_id'].as_string() == identity,
                    Job.provenance[rounds.STEP]['root_job_id'].as_string() == root.id))
            outputs = deepcopy(step.get('outputs', []))
            publication_error = None
            if child and not retained.get('outputs_retained'):
                try:
                    outputs = await output_refs(session, child)
                except (ValueError, OSError, HTTPException) as exc:
                    publication_error = str(exc.detail if isinstance(exc, HTTPException) else exc)
            projected = {**deepcopy(step['metadata']), 'state': child.status if child else step['state'],
                         'job_id': child.id if child else step.get('job_id'),
                         'outputs': outputs}
            if projected['state'] == 'review_required':
                projected['request'] = deepcopy(step['request'])
            if publication_error:
                projected['publication_error'] = publication_error
            for key in ('review', 'error', 'superseded_by', 'publication_error'):
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
    return await rounds.reconcile_selected(session, experiments, root_id, round_id, retry=retry)


async def response(session, retained):
    from routers.jobs import get_job
    refs = retained['selection']['candidate_refs']
    return {'operation': 'round', 'source_job_id': retained['source_job_id'], 'root_job_id': retained['root_job_id'],
        'round_id': retained['round_id'], 'state': retained['state'],
        'selected_design_count': sum(r['kind'] == 'design' for r in refs),
        'selected_native_count': sum(r['kind'] in {'native_document', 'native_observation'} for r in refs),
        'selected_sequence_count': sum(r['kind'] == 'native_sequence' for r in refs),
        'launched_jobs': [await get_job(s['job_id'], session) for s in retained['steps'] if s['job_id']], 'round': retained}
