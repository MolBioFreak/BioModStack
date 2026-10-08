"""ProtonPottsMPNN settings/input adapter; custody, Jobs and scheduling stay shared."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from scripts.lib.protonpottsmpnn_contract import CONTRACT, normalize_params, normalize_request, parameter_schema

MODES = frozenset({'redesign'})
DIRECTORY = 'protonpottsmpnn_design'
REQUEST_FIELD = 'protonpottsmpnn_design_request'
INPUT_FIELD = 'protonpottsmpnn_design_input'
SCIENCE_FIELDS = frozenset(parameter_schema()['properties'])
# Shared continuation metadata is not a second scientific contract.
TRANSPORT_FIELDS = frozenset({REQUEST_FIELD, INPUT_FIELD, 'num_parallel_jobs', 'job_name',
    'workflow_adapter', '_global_resource_admission', '_global_dispatch_authority',
    'pdb_paths', 'source_identity_json', 'selected_input_dir', 'selected_input_manifest',
    'source_selection_manifest_path', 'selection_source_type', 'selection_source_job_id',
    'selected_input_source_job_id', 'lineage_root_job_id', 'iteration_source_root_job_id',
    'iteration_source_job_id', 'iteration_source_design_ids', 'native_sources',
    'source_selection_count', 'source_design_id', 'source_pdb_path', 'source_stage_job_id', 'remote_result_policy', 'cpus'})


def normalize_design_params(mode, params):
    if mode not in MODES:
        raise ValueError('ProtonPottsMPNN supports redesign')
    unknown = set(params) - SCIENCE_FIELDS - TRANSPORT_FIELDS
    if unknown:
        raise ValueError(f'Unknown ProtonPottsMPNN settings: {sorted(unknown)}')
    science = normalize_params({k:v for k,v in params.items() if k in SCIENCE_FIELDS})
    return {**science, **{k:copy.deepcopy(v) for k,v in params.items() if k in TRANSPORT_FIELDS}}


def science_params(mode, params):
    if mode not in MODES:
        raise ValueError('ProtonPottsMPNN supports redesign')
    return normalize_params({k:v for k,v in params.items() if k in SCIENCE_FIELDS})


def prepare_design_request(mode, params):
    science = science_params(mode, params)
    return {'contract': CONTRACT, 'options': {k:v for k,v in science.items() if k != 'target_pdb'}}


def read_prepared_request(mode, params, *, allowed_roots=None):
    from scripts.lib.portable_inputs import _contained
    from services.ligandmpnn_interface_publication import regular_bytes
    request = Path(params[REQUEST_FIELD])
    roots = [Path(p).resolve() for p in allowed_roots] if allowed_roots is not None else [request.parent.resolve()]
    request = _contained(request, roots)
    source = _contained(Path(params[INPUT_FIELD]), roots)
    document = json.loads(regular_bytes(request))
    expected = prepare_design_request(mode, params)
    expected['source'] = {'requested_path': science_params(mode, params)['target_pdb'],
                          'sha256': hashlib.sha256(regular_bytes(source)).hexdigest()}
    if document != normalize_request(document) or document != expected:
        raise ValueError('ProtonPottsMPNN retained request/source differs from selected settings')
    return document


def prepare_for_job(mode, params, directory, *, allowed_roots, retain_prepared=False):
    import shutil
    from scripts.lib.portable_inputs import _contained
    from services.ligandmpnn_interface_publication import regular_bytes
    roots = [Path(p).resolve() for p in allowed_roots]
    fields = (REQUEST_FIELD, INPUT_FIELD)
    retained = any(params.get(k) for k in fields)
    if retained:
        if not all(params.get(k) for k in fields):
            raise ValueError('Prepared request and source must travel together')
        document = read_prepared_request(mode, params, allowed_roots=roots)
        source = _contained(Path(params[INPUT_FIELD]), roots)
        if retain_prepared or Path(params[REQUEST_FIELD]).parent.resolve() == Path(directory).resolve():
            return {k:str(Path(params[k]).resolve()) for k in fields}
    else:
        source = _contained(Path(science_params(mode, params)['target_pdb']), roots)
        data = regular_bytes(source)
        document = prepare_design_request(mode, params)
        document['source'] = {'requested_path': science_params(mode, params)['target_pdb'],
                              'sha256': hashlib.sha256(data).hexdigest()}
    destination = Path(directory)
    ancestor = destination
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    _contained(ancestor, roots)
    if source.suffix.lower() not in {'.pdb', '.cif', '.mmcif', '.ent'}:
        raise ValueError('ProtonPottsMPNN requires a PDB/mmCIF structure')
    destination.mkdir(parents=True, exist_ok=False)
    owned = destination / ('source' + source.suffix.lower())
    owned.write_bytes(regular_bytes(source))
    (destination / 'request.json').write_text(json.dumps(document, sort_keys=True, allow_nan=False) + '\n')
    return {REQUEST_FIELD:str((destination/'request.json').resolve()), INPUT_FIELD:str(owned.resolve())}


def prepared_source_path(job):
    """Controller-retained source; never reopen request.source.requested_path."""
    from paths import resolve_runtime_data_path
    from services.ligandmpnn_interface_publication import regular_bytes
    params = job.params or {}
    if not params.get(INPUT_FIELD):
        raise ValueError('Retained ProtonPottsMPNN source is unavailable')
    source = resolve_runtime_data_path(params[INPUT_FIELD])
    data = regular_bytes(source)
    if params.get(REQUEST_FIELD):
        request = json.loads(regular_bytes(resolve_runtime_data_path(params[REQUEST_FIELD])))
        if hashlib.sha256(data).hexdigest() != request['source']['sha256']:
            raise ValueError('Retained ProtonPottsMPNN source digest differs')
    return source


def result_contract(mode):
    if mode not in MODES:
        raise ValueError('ProtonPottsMPNN supports redesign')
    return {'native_contract_authority': 'platform/api/services/protonpottsmpnn_design.py:read_design_result',
            'contract':CONTRACT, 'mode':mode, 'relative_output_dir':DIRECTORY, 'primary_document':'manifest.json'}


def selected_assets():
    return ({'kind':'image', 'relative_path':'protonpottsmpnn.sif'},)


def read_design_result(output_root):
    from services.ligandmpnn_interface_publication import regular_bytes
    root = Path(output_root).resolve()
    document = json.loads(regular_bytes(root/'manifest.json'))
    if document.get('contract') != CONTRACT:
        raise ValueError('Not a ProtonPottsMPNN publication')
    request = normalize_request(document['request'])
    if request != document['request'] or document['source'] != request['source']:
        raise ValueError('ProtonPottsMPNN result source/request mismatch')
    seen = set()
    for row in document['designs']:
        if not isinstance(row['design_id'], str) or row['design_id'] in seen:
            raise ValueError('Duplicate native design identity')
        seen.add(row['design_id'])
        ci = row['criteria_index']
        if type(ci) is not int or not 0 <= ci < len(request['options']['criteria']):
            raise ValueError('Invalid native criteria index')
        if not isinstance(row['native_design_id'], str) or not isinstance(row['native'], dict):
            raise ValueError('Invalid native design record')
    for relative in document['artifacts']:
        path = root / relative
        if Path(relative).is_absolute() or not path.resolve().is_relative_to(root):
            raise ValueError('Native artifact escapes publication')
        regular_bytes(path)
    return document


async def read_result(session, job_id):
    from database import Job
    from services.protonpottsmpnn_publication import read_published_native_results
    job = await session.get(Job, job_id)
    if job is None or job.model_id != 'protonpottsmpnn' or job.mode != 'redesign':
        from fastapi import HTTPException
        raise HTTPException(404, 'ProtonPottsMPNN result Job not found')
    return await read_published_native_results(job, session)
