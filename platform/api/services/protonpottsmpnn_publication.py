"""Native sequence/state/energy publication through existing JobArtifact ownership."""
import hashlib
import json
from paths import resolve_runtime_data_path
from services.ligandmpnn_interface_publication import regular_bytes
from services.caliby_native_publication import publication_root, native_files, native_artifacts
from services.protonpottsmpnn_design import (DIRECTORY, REQUEST_FIELD, INPUT_FIELD,
    read_design_result, prepare_design_request, science_params)


def _read(job, root):
    document = read_design_result(root / DIRECTORY)
    if (job.model_id, job.mode) != ('protonpottsmpnn', 'redesign'):
        raise ValueError('Native publication differs from Job model/mode')
    expected = prepare_design_request(job.mode, job.params or {})
    request = document['request']
    if {k:v for k,v in request.items() if k != 'source'} != expected:
        raise ValueError('Native result settings differ from Job science')
    if request['source']['requested_path'] != science_params(job.mode, job.params or {})['target_pdb']:
        raise ValueError('Native result requested source differs from Job')
    # Retained evidence is checked when available; historical cleanup is not a new gate.
    params = job.params or {}
    for field in (REQUEST_FIELD, INPUT_FIELD):
        if not params.get(field):
            continue
        try:
            data = regular_bytes(resolve_runtime_data_path(params[field]))
        except (OSError, ValueError):
            continue
        if field == REQUEST_FIELD:
            if json.loads(data) != request:
                raise ValueError('Native result differs from retained request')
        elif hashlib.sha256(data).hexdigest() != request['source']['sha256']:
            raise ValueError('Native result differs from retained source')
    return document


async def publish_native_results(job, root, session):
    root = publication_root(job, root)
    files = native_files(root, DIRECTORY)
    _read(job, root)
    await native_artifacts(job, root, DIRECTORY, files, session, publish=True)
    return 0  # Structureless native sequences are not Design/PDB records.


async def read_published_native_results(job, session):
    root = publication_root(job)
    files = native_files(root, DIRECTORY)
    await native_artifacts(job, root, DIRECTORY, files, session)
    return _read(job, root)  # Preserve exact manifest shape and native metric semantics.
