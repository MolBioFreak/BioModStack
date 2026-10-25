"""Separate La retained-output evaluation; shared source custody owns bytes."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import shutil

REQUEST_FIELD = 'laproteina_evaluation_request'
DIRECTORY = 'laproteina_evaluation'
RESULT_CONTRACT = 'laproteina_evaluation_v1'
SOURCE_SLOT_FIELDS = {'samples_slot': ('file', None), 'config_slot': ('file', None)}


def parameter_schema(mode='evaluate'):
    from scripts.lib.laproteina_contract import evaluation_schema
    return evaluation_schema()


def normalize_job_request(mode, request):
    from jsonschema import Draft202012Validator
    if mode != 'evaluate':
        raise ValueError('La retained-output operation is evaluate')
    errors = list(Draft202012Validator(parameter_schema()).iter_errors(request))
    if errors:
        raise ValueError('; '.join(error.message for error in errors))
    return copy.deepcopy(request)


def normalize_params(mode, params):
    return {**copy.deepcopy(params), REQUEST_FIELD: normalize_job_request(mode, params[REQUEST_FIELD])}


def source_slots_for_params(mode, params):
    request = normalize_job_request(mode, params[REQUEST_FIELD])
    return tuple(dict.fromkeys(request[k] for k in SOURCE_SLOT_FIELDS))


def prepare_for_job(mode, params, directory, *, resolved_sources, allowed_roots, source_identities=None):
    from scripts.lib.portable_inputs import _contained
    request = normalize_job_request(mode, params[REQUEST_FIELD])
    identity = hashlib.sha256(json.dumps({'request': request, 'sources': source_identities}, sort_keys=True).encode()).hexdigest()
    dest = Path(directory) / DIRECTORY / identity
    dest.mkdir(parents=True, exist_ok=True)
    bindings = {}
    roots = [Path(p).resolve() for p in allowed_roots]
    for index, slot in enumerate(source_slots_for_params(mode, params)):
        source = _contained(resolved_sources[slot], roots)
        target = dest / 'sources' / str(index) / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            for member in source.rglob('*'):
                _contained(member, roots)
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(source, target)
        bindings[slot] = target.relative_to(dest).as_posix()
    execution = {'schema': 'laproteina_evaluation_request_v1',
        'source_directory': bindings[request['samples_slot']], 'config_path': bindings[request['config_slot']],
        'job_id': request['job_id'], 'ncpus': request['ncpus'],
        'source_identities': copy.deepcopy(source_identities or {})}
    if 'metrics' in request:
        execution['metrics'] = request['metrics']
    (dest / 'request.json').write_text(json.dumps(execution, indent=2) + '\n')
    return {**normalize_params(mode, params), 'laproteina_evaluation_input_closure': str(dest)}


def bind_for_job(mode, params, directory):
    # Ordinary preparation has already frozen source bytes through the shared owner.
    return normalize_params(mode, params), ()


def generation_settings(mode='evaluate'):
    from services.native_generation_sources import native_sources_schema
    return {'model_id': 'laproteina', 'mode': mode, 'request_field': REQUEST_FIELD,
        'parameter_schema': parameter_schema(mode), 'native_sources_schema': native_sources_schema(),
        'evaluation_scope': 'Separate retained-output operation; omitted metrics inherit retained config. Empty evaluation is not scientific metrics success.'}
