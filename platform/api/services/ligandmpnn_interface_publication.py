"""Selected LigandMPNN evidence custody; native metrics live only in result.json."""
import hashlib
import json
import stat
import uuid
from pathlib import Path

from sqlalchemy import select

from database import JobArtifact
from paths import get_inputs_dir
from services.ligandmpnn_interface_selection import read_selected_attachment

KEY = 'ligandmpnn_interface_selection'
FILES = ('result.json', 'masked_complex.pdb', 'masked_without_binder.pdb')


def regular_bytes(path: Path) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode) or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('selected input or native result is not a regular file')
    return path.read_bytes()


def materialize(selection, sources: dict[str, bytes], *, source_identities: dict | None = None,
                original_sources: dict[str, bytes] | None = None, lineage_root_job_id: str | None = None):
    """Private immutable-by-digest snapshots under managed inputs; no source edits."""
    from services.ligandmpnn_interface_selection import compile_selected_manifest
    directory = get_inputs_dir() / 'ligandmpnn_interface_context' / uuid.uuid4().hex
    generated, manifest = compile_selected_manifest(selection, sources, directory)
    if original_sources and source_identities:
        source_identities = {key: dict(value) for key, value in source_identities.items()}
        for index, candidate in enumerate(selection.candidate_ids):
            relative = f'inputs/ligandmpnn_interface_context/{index:03d}/original{source_identities[candidate]["format"]}'
            generated.append((relative, original_sources[candidate]))
            source_identities[candidate]['snapshot_path'] = str(directory / relative)
    directory.mkdir(parents=True, exist_ok=False)
    for relative, content in generated:
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o400)
    return {'schema': 'bms.ligandmpnn.interface-context.selection.v1',
            'source_job_id': selection.source_job_id, 'round_id': selection.round_id,
            'lineage_root_job_id': lineage_root_job_id or selection.source_job_id,
            'candidate_ids': selection.candidate_ids, 'settings': selection.settings.model_dump(),
            'manifest': manifest, 'manifest_sha256': hashlib.sha256(regular_bytes(Path(manifest))).hexdigest(),
            'sources': {candidate: {'path': str(directory / f'inputs/ligandmpnn_interface_context/{index:03d}/source.pdb'),
                                    'sha256': hashlib.sha256(sources[candidate]).hexdigest(),
                                    **({'original': source_identities[candidate]} if source_identities else {}),
                                    'request': str(directory / f'inputs/ligandmpnn_interface_context/{index:03d}/request.json'),
                                    'request_sha256': hashlib.sha256(regular_bytes(directory / f'inputs/ligandmpnn_interface_context/{index:03d}/request.json')).hexdigest()}
                        for index, candidate in enumerate(selection.candidate_ids)}}


def verify_binding(binding: dict):
    if binding.get('schema') != 'bms.ligandmpnn.interface-context.selection.v1':
        raise ValueError('missing selected interface-context binding')
    manifest_path = Path(binding['manifest'])
    if hashlib.sha256(regular_bytes(manifest_path)).hexdigest() != binding['manifest_sha256']:
        raise ValueError('selected manifest changed')
    records = json.loads(regular_bytes(manifest_path))
    if len(records) != len(binding['candidate_ids']) or set(binding['sources']) != set(binding['candidate_ids']):
        raise ValueError('selected roster changed')
    for index, (candidate, record) in enumerate(zip(binding['candidate_ids'], records)):
        source = binding['sources'][candidate]
        if record != {'invocation_id': f'{index:03d}', 'request_path': source['request'], 'source_path': source['path']}:
            raise ValueError('selected invocation changed')
        if hashlib.sha256(regular_bytes(Path(source['path']))).hexdigest() != source['sha256'] or hashlib.sha256(regular_bytes(Path(source['request']))).hexdigest() != source['request_sha256']:
            raise ValueError('selected input changed')
        original = source.get('original')
        if original is not None and (original['owner_job_id'] != binding['source_job_id']
                or original['format'] not in {'.pdb', '.cif', '.mmcif'}
                or len(original['sha256']) != 64):
            raise ValueError('original selected structure identity changed')
        if original and original.get('snapshot_path'):
            if hashlib.sha256(regular_bytes(Path(original['snapshot_path']))).hexdigest() != original['sha256']:
                raise ValueError('original selected structure snapshot changed')
        request = json.loads(regular_bytes(Path(source['request'])))
        if (request['candidate_id'] != candidate or request['round_id'] != binding['round_id'] or
            request['source_sha256'] != source['sha256'] or request['structure_path'] != source['path'] or
            any(request[k] != v for k, v in binding['settings'].items())):
            raise ValueError('selected native request changed')


async def read_selected(job, session):
    binding = (job.params or {}).get(KEY)
    if not isinstance(binding, dict):
        raise ValueError('job has no selected interface-context request')
    verify_binding(binding)
    root = Path(job.output_dir).absolute()
    rows = list((await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id)) ).all())
    by_path = {row.logical_path: row for row in rows}
    receipts = []
    for index, candidate in enumerate(binding['candidate_ids']):
        source = binding['sources'][candidate]
        result_dir = root / 'ligandmpnn_interface_context' / f'{index:03d}' / 'context_result'
        receipt = read_selected_attachment(result_dir, candidate_id=candidate,
                            round_id=binding['round_id'], source=regular_bytes(Path(source['path'])))
        for name, descriptor in receipt['artifacts'].items():
            logical = f'ligandmpnn_interface_context/{index:03d}/context_result/{name}'
            row = by_path.get(logical)
            path = result_dir / name
            if (row is None or row.storage_path != str(path) or row.sha256 != descriptor['sha256']
                    or row.bytes != descriptor['bytes'] or
                    hashlib.sha256(regular_bytes(path)).hexdigest() != row.sha256):
                raise ValueError('registered native artifact differs from exact result bytes')
        receipts.append(receipt)
    return {'schema': 'bms.ligandmpnn.interface-context.publication.v1',
            'job_id': job.id, 'source_job_id': binding['source_job_id'],
            'lineage_root_job_id': binding.get('lineage_root_job_id'),
            'round_id': binding['round_id'], 'settings': binding['settings'],
            'source_identities': {candidate: binding['sources'][candidate].get('original')
                                  for candidate in binding['candidate_ids']}, 'records': receipts}


async def publish_selected(job, root: Path, session):
    binding = (job.params or {}).get(KEY)
    if not isinstance(binding, dict):
        raise ValueError('missing selected interface-context request')
    verify_binding(binding)
    root = Path(root).absolute()
    existing = list((await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all())
    by_path = {row.logical_path: row for row in existing}
    receipts = []
    for index, candidate in enumerate(binding['candidate_ids']):
        source = binding['sources'][candidate]
        result_dir = root / 'ligandmpnn_interface_context' / f'{index:03d}' / 'context_result'
        receipt = read_selected_attachment(result_dir, candidate_id=candidate,
                            round_id=binding['round_id'], source=regular_bytes(Path(source['path'])))
        receipts.append(receipt)
        for name, descriptor in receipt['artifacts'].items():
            path = result_dir / name
            content = regular_bytes(path)
            if hashlib.sha256(content).hexdigest() != descriptor['sha256'] or len(content) != descriptor['bytes']:
                raise ValueError('native result changed during publication')
            logical = f'ligandmpnn_interface_context/{index:03d}/context_result/{name}'
            row = by_path.get(logical)
            if row is None:
                row = JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id, attempt=0,
                                  logical_path=logical, storage_path=str(path),
                                  sha256=descriptor['sha256'], bytes=descriptor['bytes'],
                                  media_type='application/json' if name.endswith('.json') else 'chemical/x-pdb',
                                  provenance={'candidate_id': candidate, 'round_id': binding['round_id'],
                                              'source_sha256': source['sha256'], 'model_id': 'ligandmpnn'})
                session.add(row)
                by_path[logical] = row
            elif row.storage_path != str(path) or row.sha256 != descriptor['sha256'] or row.bytes != descriptor['bytes']:
                raise ValueError('native publication replay differs')
    await session.flush()
    # Provenance indexes the native files; only the model-owned reader exposes
    # numerical samples. Do not duplicate native metrics in the Job JSON.
    publication = {'schema': 'bms.ligandmpnn.interface-context.publication.v1',
                   'job_id': job.id, 'source_job_id': binding['source_job_id'],
                   **({'lineage_root_job_id': binding['lineage_root_job_id']} if 'lineage_root_job_id' in binding else {}),
                   'round_id': binding['round_id'], 'settings': binding['settings'],
                   'source_identities': {candidate: binding['sources'][candidate].get('original')
                                         for candidate in binding['candidate_ids']},
                   'records': [{key: value for key, value in receipt.items() if key != 'conditions'}
                               for receipt in receipts]}
    prior = (job.provenance or {}).get('ligandmpnn_interface_publication')
    if prior is not None and prior != publication:
        raise ValueError('native publication replay changed')
    job.provenance = {**(job.provenance or {}), 'ligandmpnn_interface_publication': publication}
    reopened = await read_selected(job, session)
    if [{key: value for key, value in receipt.items() if key != 'conditions'}
        for receipt in reopened['records']] != publication['records']:
        raise ValueError('native publication readback differs')
    return publication
