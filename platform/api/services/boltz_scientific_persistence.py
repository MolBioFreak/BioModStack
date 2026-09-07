"""Private marked Boltz adapter: verified native snapshots -> compact Design data.

Producer candidate/document IDs are never rewritten to database IDs. No general
canonical-record setter is exposed; records can only follow native verification.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid

from services.core_protein_result_contract import (
    CandidateIntegrityError, validate_candidate_accounting, validate_persisted_publication,
)
from services.core_protein_scientific_contract import validate_metric
from services.frustrampnn.contracts import canonical_json_bytes


def _snapshot(root, key):
    """Open every component relative to an owned directory, refusing symlinks."""
    if not isinstance(key, str) or not key or '\\' in key:
        raise ValueError('invalid publication artifact key')
    parts = PurePosixPath(key)
    if parts.is_absolute() or parts.as_posix() != key or any(p in ('.', '..') for p in parts.parts):
        raise ValueError('noncanonical publication artifact key')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts.parts[:-1]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        data_fd = os.open(parts.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(data_fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('publication artifact is not regular')
            content = stream.read()
        if not content:
            raise ValueError('empty publication artifact')
        return {'path': str(root / key), 'sha256': hashlib.sha256(content).hexdigest()}, content
    finally:
        os.close(fd)


def _json(content):
    from write_sequence_producer_manifest import _reject_duplicate_keys, _reject_json_constant
    return json.loads(content, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_json_constant)


def _verified_publication(job, root):
    """Read-only exact-byte verification shared by ingestion and consumers.

    This allocates no database identities and never promotes persisted claims.
    """
    # Same scripts-only shared verifier used by the publisher; imported lazily to
    # preserve the API's legacy import path and behavior.
    from services import aligned_error_utils  # registers the shared scripts root
    from paths import get_data_root, resolve_runtime_data_path
    from lib.boltz_native_identity import verify_boltz_native_identity
    from write_sequence_producer_manifest import _SEQUENCE_FIELDS, _load_metadata
    import base64

    if not isinstance(job.output_dir, str) or not job.output_dir:
        raise ValueError('missing trusted job result root')
    owned_root = Path(job.output_dir)
    owned_root = resolve_runtime_data_path(owned_root) if owned_root.is_absolute() else get_data_root() / owned_root
    if root != owned_root:
        raise ValueError('foreign job result root')
    from services.boltz_launch_authority import KEY, digest, MAX_BYTES
    authority = (job.provenance or {}).get(KEY)
    if not isinstance(authority, dict) or set(authority) != {
        'schema_name', 'schema_version', 'job_id', 'attempt', 'result_root',
        'request_sha256', 'tasks', 'input_files', 'model_id', 'mode'}:
        raise ValueError('missing or invalid Boltz launch authority')
    if (authority['schema_name'] != KEY or type(authority['schema_version']) is not int
        or authority['schema_version'] != 1 or type(authority['attempt']) is not int
        or authority['attempt'] != job.retry_count or authority['job_id'] != str(job.id)
        or authority['model_id'] != job.model_id or authority['mode'] != job.mode
        or authority['result_root'] != str(root)
        or authority['request_sha256'] != digest(canonical_json_bytes(job.params or {}))):
        raise ValueError('Boltz launch authority job/input binding changed')
    authority_bytes = canonical_json_bytes(authority)
    if len(authority_bytes) > MAX_BYTES:
        raise ValueError('Boltz launch authority exceeds byte bound')
    tasks = authority['tasks']
    if not isinstance(tasks, list) or not tasks or len(tasks) > 10000:
        raise ValueError('invalid launch task inventory')
    expected_tasks = {}
    import re
    for task in tasks:
        if (not isinstance(task, dict) or set(task) != {'namespace', 'owner', 'metadata', 'input_sha256'}
            or not isinstance(task['namespace'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', task['namespace'])
            or task['namespace'] in expected_tasks):
            raise ValueError('invalid or duplicate launch task identity')
        expected_tasks[task['namespace']] = task
    workflow_artifact, workflow_bytes = _snapshot(root, 'scientific/boltz_workflow_inventory.json')
    if len(workflow_bytes) > MAX_BYTES:
        raise ValueError('workflow task inventory exceeds byte bound')
    workflow = _json(workflow_bytes)
    expected_workflow = dict(schema_name='boltz_workflow_inventory', schema_version=1,
        job_id=authority['job_id'], attempt=authority['attempt'], result_root=authority['result_root'],
        launch_sha256=digest(authority_bytes), tasks=tasks)
    if canonical_json_bytes(workflow) != canonical_json_bytes(expected_workflow):
        raise ValueError('independent workflow task inventory does not match launch authority')
    prepared = {}
    # Discovery locates producer manifests only. Candidate inventory/IDs and all
    # artifacts come exclusively from their exact declarations, never file scans.
    manifests = sorted((root / 'scientific/boltz').glob('*/producer_candidates.json'))
    if not manifests:
        raise ValueError('missing Boltz producer candidate declaration')
    if {p.parent.name for p in manifests} != set(expected_tasks):
        raise ValueError('expected and observed Boltz task membership differ')
    directories = list((root / 'scientific/boltz').iterdir())
    if {p.name for p in directories} != set(expected_tasks):
        raise ValueError('extra or foreign Boltz task directory')
    task_bindings = {}
    for manifest_path in manifests:
        expected_task = expected_tasks[manifest_path.parent.name]
        binding_artifact, binding_bytes = _snapshot(root,
            (manifest_path.parent.relative_to(root) / 'boltz_task_binding.json').as_posix())
        expected_binding = dict(schema_name='boltz_task_binding', schema_version=1,
            job_id=authority['job_id'], attempt=authority['attempt'], result_root=authority['result_root'],
            launch_sha256=digest(authority_bytes), task=expected_task)
        if len(binding_bytes) > MAX_BYTES or canonical_json_bytes(_json(binding_bytes)) != canonical_json_bytes(expected_binding):
            raise ValueError('Boltz producer task attempt binding differs from launch authority')
        task_bindings[manifest_path.parent.name] = binding_artifact
        selector = manifest_path.relative_to(root).as_posix()
        manifest_artifact, content = _snapshot(root, selector)
        manifest = _json(content)
        if not isinstance(manifest, dict) or set(manifest) != {'schema_name', 'schema_version', 'candidates'}:
            raise ValueError('producer manifest requires exact fields')
        sequence = manifest['schema_name'] == 'sequence_structure_producer_candidates'
        if manifest['schema_name'] not in {'sequence_structure_producer_candidates', 'structure_producer_candidates'} or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
            raise ValueError('unsupported producer manifest schema')
        entries = manifest['candidates']
        if not isinstance(entries, list) or not entries:
            raise ValueError('missing Boltz producer candidate inventory')
        base = manifest_path.parent.relative_to(root) / 'predictions'
        for entry in entries:
            fields = {'producer_method', 'producer_sample', 'producer_rank', 'producer_output_key',
                'producer_artifact_sha256', 'source_format', 'protein_science_contract_revision', 'boltz_native_identity'}
            if sequence:
                fields |= _SEQUENCE_FIELDS
            if not isinstance(entry, dict) or set(entry) != fields:
                raise ValueError('producer candidate requires exact fields')
            if entry['producer_method'] != 'boltz' or entry['source_format'] != 'pdb' or type(entry['protein_science_contract_revision']) is not int or entry['protein_science_contract_revision'] != 1:
                raise ValueError('unsupported native Boltz producer candidate')
            rank = entry['producer_rank']
            if rank is not None and (type(rank) is not int or rank < 0):
                raise ValueError('invalid producer rank')
            document = entry['producer_output_key']
            candidate = document
            structure_key = document
            if sequence:
                metadata = {k:entry[k] for k in _SEQUENCE_FIELDS}
                if (expected_task['owner'] not in ('BoltzFromSequenceTask', 'BoltzFromSequenceWithMSATask')
                    or canonical_json_bytes({k:v for k,v in metadata.items() if k != 'producer_rank'})
                    != canonical_json_bytes({k:v for k,v in expected_task['metadata'].items() if k != 'producer_rank'})):
                    raise ValueError('producer input identity differs from launch task')
                _load_metadata(base64.b64encode(json.dumps(metadata).encode()).decode())
                candidate = entry['producer_artifact_id']
                prefix = entry['producer_artifact_key'] + '/'
                if not isinstance(document, str) or not document.startswith(prefix):
                    raise ValueError('foreign producer document key')
                structure_key = document[len(prefix):]
            elif expected_task['owner'] != 'BoltzFromComplex' or entry['producer_sample'] != expected_task['namespace']:
                raise ValueError('complex producer sample differs from launch task')
            if not isinstance(document, str) or not document or document in prepared:
                raise ValueError('duplicate or invalid producer document')
            # Native transport flattens artifacts; no basename fallback for claims.
            if not isinstance(structure_key, str) or PurePosixPath(structure_key).name != structure_key:
                raise ValueError('invalid producer structure key')
            structure, source = _snapshot(root, (base / structure_key).as_posix())
            if structure['sha256'] != entry['producer_artifact_sha256']:
                raise ValueError('producer structure hash mismatch')
            claimed = entry['boltz_native_identity']
            descriptors = {'ledger':claimed['processed_structure'], 'pae':claimed['aligned_error'],
                           'plddt':claimed['vectors'][0], 'metrics':claimed['confidence']}
            artifacts = {'structure':structure, 'manifest':manifest_artifact}
            snapshots = {}
            for role, descriptor in descriptors.items():
                key = descriptor['artifact_key']
                if not isinstance(key, str) or PurePosixPath(key).name != key:
                    raise ValueError('invalid native artifact key')
                artifact, data = _snapshot(root, (base / key).as_posix())
                if artifact['sha256'] != descriptor['artifact_sha256']:
                    raise ValueError('native artifact hash mismatch')
                artifacts[role], snapshots[role] = artifact, data
            native = verify_boltz_native_identity(claimed=claimed, source=source, structure_name=structure_key,
                ledger_bytes=snapshots['ledger'], pae_bytes=snapshots['pae'], plddt_bytes=snapshots['plddt'],
                confidence_bytes=snapshots['metrics'], candidate_id=candidate, document_id=document)
            confidence = _json(snapshots['metrics'])
            trusted_source = {'artifact_sha256':artifacts['metrics']['sha256'], 'candidate_id':candidate, 'document_id':document}
            records = []
            for key, unit, scope in [('ptm', 'dimensionless', 'overall'), ('complex_plddt', 'fraction', 'complex')]:
                value = confidence[key]
                if type(value) not in (int, float) or not 0 <= value <= 1:
                    raise ValueError('native scalar outside supported domain')
                records.append(validate_metric(dict(metric_key=key, state='ok', value=value, reason_code=None,
                    unit=unit, direction='higher_is_better', scope=scope, producer_version=native['provider_revision'],
                    derivation_version='boltz-native-scalar-v1', source=trusted_source), expected_source=trusted_source))
            block = dict(schema_name='boltz_scientific_design', schema_version=1,
                candidate_id=candidate, document_id=document,
                producer={k:entry[k] for k in ('producer_method','producer_sample','producer_rank','producer_output_key')},
                artifacts=artifacts, metrics=records,
                identity={'provider_revision':native['provider_revision'], 'source_axis':native['source_axis'],
                    'chain_key_namespace':native['confidence']['chain_key_namespace'],
                    'matrix_key':native['aligned_error']['matrix_key'], 'vector_key':'plddt', 'vector_unit':'fraction'})
            prepared[document] = dict(artifacts=artifacts, block=block, native=native, snapshots=snapshots)
    ids = list(prepared)
    summary = validate_candidate_accounting(stage_id='boltz', requested_count=None, generated_ids=ids,
        dispositions=[{'candidate_id':i, 'disposition':'selected'} for i in ids],
        expected_publication_ids=ids, persisted_ids=ids)
    receipt = {'summary':summary, 'manifest':next(iter(prepared.values()))['artifacts']['manifest'],
        'workflow_inventory': workflow_artifact, 'task_bindings': task_bindings,
        'candidates':{i:p['artifacts'] for i,p in prepared.items()}}
    return prepared, receipt


def _prepare(job, root, existing):
    prepared, receipt = _verified_publication(job, root)
    for candidate in prepared.values():
        candidate['id'] = str(uuid.uuid4())
        candidate['block'] = dict(candidate['block'], design_id=candidate['id'])
    prior = (job.provenance or {}).get('core_protein_candidate_publication')
    if prior is not None or existing:
        if canonical_json_bytes(prior) != canonical_json_bytes(receipt):
            raise ValueError('candidate replay evidence changed')
        validate_persisted_publication(job, existing, root)
        for row in existing:
            expected = dict(prepared[row.name]['block'], design_id=row.id)
            if canonical_json_bytes((row.confidence_metrics or {}).get('core_protein_scientific')) != canonical_json_bytes(expected):
                raise ValueError('canonical scalar replay changed')
    return prepared, receipt


def _revalidate(root, receipt):
    expected = receipt['workflow_inventory']
    current, _ = _snapshot(root, 'scientific/boltz_workflow_inventory.json')
    if current != expected:
        raise ValueError('prepared workflow inventory bytes changed')
    for expected in receipt['task_bindings'].values():
        current, _ = _snapshot(root, Path(expected['path']).relative_to(root).as_posix())
        if current != expected:
            raise ValueError('prepared task binding bytes changed')
    for artifacts in receipt['candidates'].values():
        for expected in artifacts.values():
            key = Path(expected['path']).relative_to(root).as_posix()
            current, _ = _snapshot(root, key)
            if current != expected:
                raise ValueError('prepared publication bytes changed')


async def ingest_verified_boltz(job, root, session, *, commit):
    from sqlalchemy import select, delete
    from database import Design
    with session.no_autoflush:
        existing = list((await session.execute(select(Design).where(
            Design.job_id == job.id, Design.source_stage.is_(None)))).scalars())
        try:
            prepared, receipt = _prepare(job, root, existing)
            _revalidate(root, receipt)
        except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
            raise CandidateIntegrityError('boltz_publication_invalid', str(exc)) from exc
    if existing:
        return 0
    # No mutation or autoflush can precede validation of the LAST candidate.
    await session.execute(delete(Design).where(Design.job_id == job.id, Design.source_stage.is_not(None)))
    for document, candidate in prepared.items():
        artifacts = candidate['artifacts']
        session.add(Design(id=candidate['id'], job_id=job.id, name=document,
            pdb_path=artifacts['structure']['path'], json_path=artifacts['metrics']['path'],
            # Existing review/analysis admission needs the actual artifact, not
            # a copied readiness claim. Scientific reads still reverify native
            # ownership through the Job before using these legacy descriptors.
            review_profile_id='structure_prediction_v1', review_contract_version=1,
            review_contract_source='boltz_native_publication',
            aligned_error_path=artifacts['pae']['path'], aligned_error_format='boltz_pae_npz',
            aligned_error_key=candidate['native']['aligned_error']['matrix_key'],
            stage_family='boltz', stage_mode=job.mode, artifact_group='boltz', artifact_class='structure_prediction',
            confidence_metrics={'core_protein_scientific_contract':1, 'core_protein_candidate_artifacts':artifacts,
                                'core_protein_scientific':candidate['block']}))
    job.provenance = {**(job.provenance or {}), 'core_protein_candidate_publication':receipt}
    if commit:
        await session.commit()
    else:
        await session.flush()
    return len(prepared)
