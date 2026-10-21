"""Retain emitted model documents at the existing JobArtifact custody owner.

The roots below are the native modules' publishDir contracts, not a recursive
search of inputs/work/cache. A document is an observation, never an accepted
Design. Exact relative producer paths remain distinct, including trajectories.
"""
import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from database import JobArtifact
from services.bindcraft2_publication import _regular

PUBLICATION_KEY = 'binder_retained_publication'
PREFIX = 'binder/retained/'
# Keep these bound to modules/{rfantibody,boltzgen,ppiflow}.nf.
ROOTS = {
    'rfantibody': ('run/rfantibody/output', 'run/rfantibody/traj'),
    'antibody_denovo': ('run/rfantibody/output', 'run/rfantibody/traj', 'collected/boltzgen_raw'),
    'template_antibody_denovo': ('run/rfantibody/output', 'run/rfantibody/traj', 'collected/boltzgen_raw'),
    'boltzgen': ('collected/boltzgen_raw',),
    'ppiflow': ('ppiflow_generation',),
    'binder_refinement': ('collected/binder_refinement',),
    'fampnn': ('pdb_files',),
    'proteinmpnn': ('pdb_files', 'run/mpnn/raw/seqs'),
    'protenix': ('pdb_files/predictions', 'run/protenix/producer'),
    'boltz2': ('scientific/boltz', 'pdb_files/predictions'),
}
SUFFIXES = {'.pdb', '.cif', '.mmcif', '.trb', '.json', '.jsonl', '.csv', '.npz', '.fa', '.fasta'}


def _document(root, name, info, model, files):
    """Read only paired, inventoried producer annotations; never infer scores."""
    import json
    from antibody_pipeline_contract import normalize_antibody_artifact_class
    path = Path(name)
    producer = ('rfantibody' if name.startswith('run/rfantibody/') else
                'boltzgen' if name.startswith('collected/boltzgen_raw/') else model)
    payload = {}
    for sidecar in (path.with_name('generator_' + path.stem + '.json'),
                    Path(name + '.sample.json')):
        key = sidecar.as_posix()
        if key in files:
            try:
                _, data = _regular(root, key)
                value = json.loads(data)
                if isinstance(value, dict):
                    payload = value
                    break
            except (ValueError, UnicodeError):
                pass  # Missing annotation is uncertainty, not an import gate.
    declared_producer = payload.get('terminal_producer') or payload.get('producer')
    if isinstance(declared_producer, str):
        producer = declared_producer
    artifact_class = normalize_antibody_artifact_class(payload.get('artifact_class'))
    if artifact_class is None:
        artifact_class = ('backbone_complex' if producer in {'ppiflow', 'rfantibody'} else
                          'sequence_designed_complex' if producer in {'fampnn', 'proteinmpnn'} else
                          'structure_prediction' if producer in {'protenix', 'boltz2'} else None)
    return {**payload, 'path': name, 'sha256': info['sha256'],
            'producer_model_id': producer, 'artifact_class': artifact_class,
            'native_output_id': payload.get('native_output_id', name),
            'native_rows': [], 'validation_state': 'unvalidated'}


async def publish_retained_documents(session, job, output_path):
    if job.model_id not in ROOTS:
        return None
    from services.binder_publication_roots import publication_root
    root = publication_root(job, output_path)
    files = {}
    for relative in ROOTS.get(job.model_id, ()):
        directory = root / relative
        if not directory.exists():
            continue
        if directory.is_symlink():
            raise ValueError('Unsafe retained native output directory')
        for path in sorted(directory.rglob('*')):
            if path.is_symlink():
                raise ValueError('Unsafe retained native output document')
            if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                continue
            name = path.relative_to(root).as_posix()
            _, data = _regular(root, name)
            files[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    if not files:
        return None
    receipt = {'schema': 'binder.retained-publication.v1', 'model_id': job.model_id,
               'root': str(root), 'campaign_root': '.', 'attempt': job.retry_count or 0,
               'remote_attempt_id': job.remote_attempt_id, 'files': files}
    previous = (job.provenance or {}).get(PUBLICATION_KEY)
    if previous is not None and previous != receipt:
        raise ValueError('Retained native publication replay changed')
    existing = list((await session.scalars(select(JobArtifact).where(
        JobArtifact.owner_job_id == job.id, JobArtifact.logical_path.startswith(PREFIX)))).all())
    owned = {a.logical_path.removeprefix(PREFIX): a for a in existing}
    if len(owned) != len(existing) or (previous is not None and set(owned) != set(files)):
        raise ValueError('Retained native artifact inventory changed')
    if previous is None and owned:
        raise ValueError('Retained native artifacts have no publication')
    for name, info in files.items():
        expected = (str(root / name), info['sha256'], info['bytes'], receipt['attempt'])
        artifact = owned.get(name)
        if artifact is not None:
            if (artifact.storage_path, artifact.sha256, artifact.bytes, artifact.attempt) != expected:
                raise ValueError('Retained native artifact changed')
            continue
        suffix = Path(name).suffix.lower()
        media_type = ('chemical/x-pdb' if suffix == '.pdb' else
                      'chemical/x-mmcif' if suffix in {'.cif', '.mmcif'} else
                      'application/octet-stream')
        document = _document(root, name, info, job.model_id, files)
        session.add(JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id,
            attempt=receipt['attempt'], logical_path=PREFIX + name, storage_path=str(root / name),
            sha256=info['sha256'], bytes=info['bytes'], media_type=media_type,
            provenance={'model_id': job.model_id, 'native_document': document}))
    job.provenance = {**(job.provenance or {}), PUBLICATION_KEY: receipt}
    await session.flush()
    return receipt
