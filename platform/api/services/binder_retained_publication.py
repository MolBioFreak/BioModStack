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
}
SUFFIXES = {'.pdb', '.cif', '.mmcif', '.trb', '.json', '.jsonl', '.csv', '.npz', '.fa', '.fasta'}


async def publish_retained_documents(session, job, output_path):
    root = Path(output_path).absolute()
    if not job.output_dir or Path(job.output_dir).absolute() != root or root.is_symlink():
        raise ValueError('Retained native output root differs from Job owner')
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
        producer = ('rfantibody' if name.startswith('run/rfantibody/') else
                    'boltzgen' if name.startswith('collected/boltzgen_raw/') else job.model_id)
        document = {'path': name, 'sha256': info['sha256'], 'producer_model_id': producer,
                    'native_output_id': name, 'native_rows': [], 'validation_state': 'unvalidated'}
        session.add(JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id,
            attempt=receipt['attempt'], logical_path=PREFIX + name, storage_path=str(root / name),
            sha256=info['sha256'], bytes=info['bytes'], media_type=media_type,
            provenance={'model_id': job.model_id, 'native_document': document}))
    job.provenance = {**(job.provenance or {}), PUBLICATION_KEY: receipt}
    await session.flush()
    return receipt
