"""Private launch-owned Boltz input roster. No workflow/output can mint authority."""
import base64
import hashlib
import json
from pathlib import Path
import re

from services.core_protein_scientific_contract import revision_for_job
from services.frustrampnn.contracts import canonical_json_bytes

KEY = 'boltz_launch_authority'
TRANSPORT = ('boltz_launch_authority_path', 'boltz_launch_authority_sha256')
TRANSPORT_FILENAME = '.boltz-launch-authority.json'
MAX_BYTES = 2 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sequence_metadata(sequence, name):
    # sequenceProducerMetadata's legacy-pair semantics: identity IS the already
    # resolved task name, not a second derived identifier algorithm.
    sequence, name = sequence.strip(), name.strip()
    if not sequence or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        raise ValueError('unsafe Boltz sequence input')
    metadata = dict(producer_artifact_id=name, producer_artifact_key=name, producer_sample=name,
        producer_sequence=sequence, producer_fold=None, producer_rank=None,
        producer_submission_id=name, producer_submission_name=name,
        original_submission_identity=dict(id=name, name=name))
    from services import aligned_error_utils  # shared scripts import owner
    from write_sequence_producer_manifest import _load_metadata
    _load_metadata(base64.b64encode(canonical_json_bytes(metadata)).decode())
    return metadata


def command_params(command):
    params = {}
    for index, token in enumerate(command):
        if token.startswith('--'):
            key = token[2:]
            if index + 1 == len(command) or command[index + 1].startswith('--'):
                raise ValueError('valueless Boltz launch flag')
            if key in params and params[key] != command[index + 1]:
                raise ValueError('conflicting Boltz launch flags')
            params[key] = command[index + 1]
    return params


def validate_launch_settings(job):
    from model_registry import get_registry
    registry = get_registry()
    requested = dict(job.params or {})
    # Existing complex input owner supplies components rather than a redundant
    # primary sequence. Keep the registry's other validation unchanged.
    if not requested.get('sequence') and any(requested.get(k) for k in
        ('complex_components', 'complex_json_path', 'sequence_input', 'sequence_batch_entries')):
        requested['sequence'] = requested.get('sequence_input') or 'complex-input-owner'
    errors = registry.validate_job_params(job.model_id, job.mode, requested)
    for definition in registry.get_model(job.model_id).params:
        if definition.name not in requested:
            continue
        value = requested[definition.name]
        expected_type = {'integer': int, 'boolean': bool, 'string': str, 'text': str}.get(definition.type)
        if expected_type and type(value) is not expected_type:
            errors.append(f'{definition.name} requires typed {definition.type}')
    if errors:
        raise ValueError('invalid typed Boltz launch settings: ' + '; '.join(errors))


def build_authority(job, command):
    if revision_for_job(job) is None or job.model_id != 'boltz2' or job.mode not in ('predict', 'complex'):
        return None
    params = command_params(command)
    if any(key in params for key in TRANSPORT):
        raise ValueError('Boltz launch authority transport is server-owned')
    root = str(Path(job.output_dir).absolute())
    if params.get('out_dir', root) != root or params.get('job_id', str(job.id)) != str(job.id):
        raise ValueError('foreign Boltz launch binding')
    attempt = job.retry_count
    if type(attempt) is not int or attempt < 0:
        raise ValueError('invalid persisted Boltz attempt')
    files = {}

    def snapshot(path):
        path = str(Path(path).absolute())
        with Path(path).open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            raise ValueError('Boltz input snapshot size invalid')
        files[path] = dict(sha256=digest(data), content_base64=base64.b64encode(data).decode())
        return data

    count = int(params.get('num_parallel_jobs', '1'))
    if not 1 <= count <= 10000:
        raise ValueError('Boltz task count outside bound')
    complex_input = params.get('complex_json_path')
    batch = params.get('sequence_batch_json_path') if params.get('complex_batch_dir') else None
    tasks = []
    if batch or complex_input:
        entries = json.loads(snapshot(batch)) if batch else [dict(name=params.get('sequence_name', 'complex_pred'), complex_json=complex_input)]
        if not isinstance(entries, list) or not entries:
            raise ValueError('empty complex input inventory')
        for entry in entries:
            data = snapshot(entry['complex_json'])
            value = json.loads(data)
            if not isinstance(value, dict) or not isinstance(value.get('components'), list) or not value['components']:
                raise ValueError('invalid complex input snapshot')
            for component in value['components']:
                if not isinstance(component, dict):
                    raise ValueError('invalid complex component input')
                if component.get('msa_path'):
                    if not Path(component['msa_path']).is_absolute():
                        raise ValueError('complex MSA snapshot requires resolved absolute input path')
                    snapshot(component['msa_path'])
            for index in range(1 if batch else count):
                name = entry['name'] + (f'_job{index}' if not batch and count > 1 else '')
                tasks.append(dict(namespace=name, owner='BoltzFromComplex', metadata=None, input_sha256=digest(data)))
    else:
        sequence = params.get('sequence_input', '')
        name = params.get('sequence_name', 'predicted')
        for index in range(count):
            task_name = name + (f'_job{index}' if count > 1 else '')
            metadata = sequence_metadata(sequence, task_name)
            tasks.append(dict(namespace=task_name, owner='BoltzFromSequenceWithMSATask' if params.get('boltz_use_msa') == 'true' else 'BoltzFromSequenceTask',
                metadata=metadata, input_sha256=digest(metadata['producer_sequence'].encode())))
    if params.get('msa_path'):
        snapshot(params['msa_path'])
    if not tasks or len(tasks) > 10000 or len({t['namespace'] for t in tasks}) != len(tasks):
        raise ValueError('Boltz task identities must be unique')
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', t['namespace']) for t in tasks):
        raise ValueError('unsafe Boltz task namespace')
    authority = dict(schema_name='boltz_launch_authority', schema_version=1, job_id=str(job.id),
        model_id=job.model_id, mode=job.mode,
        attempt=attempt, result_root=root, request_sha256=digest(canonical_json_bytes(job.params or {})),
        tasks=sorted(tasks, key=lambda task: task['namespace']), input_files=files)
    if len(canonical_json_bytes(authority)) > MAX_BYTES:
        raise ValueError('Boltz launch authority exceeds byte bound')
    return authority


def transport(authority):
    import os
    import tempfile
    data = canonical_json_bytes(authority)
    root = Path(authority['result_root'])
    root.mkdir(parents=True, exist_ok=True)
    target = root / TRANSPORT_FILENAME
    fd, temporary = tempfile.mkstemp(prefix='.boltz-authority-', suffix='.tmp', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return ['--' + TRANSPORT[0], str(target), '--' + TRANSPORT[1], digest(data)]
