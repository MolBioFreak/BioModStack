"""Producer-owned inventory for one native family-alignment execution."""
import json
from pathlib import Path


def write_result_manifest(work: Path, *, job_id: str, attempt: int,
                          run_identity: str | None, artifacts: list, status: str):
    """Describe every published leaf; the manifest itself is the enclosing seal.

    Attempt number alone is not unique after interrupted re-entry; the producer's
    unique work directory suffix is the publication identity, not a path identity.
    """
    publication_id = work.name.rsplit('-', 1)[-1]
    manifest = {
        'schema': 'bms.family-msa.result-manifest.v1',
        'job_id': job_id, 'attempt': attempt, 'publication_id': publication_id,
        'run_identity': run_identity, 'status': status,
        'artifacts': [
            {'id': a.id, 'role': a.provenance['role'], 'logical_path': a.logical_path,
             'sha256': a.sha256, 'bytes': a.bytes, 'media_type': a.media_type}
            for a in artifacts
        ],
    }
    data = (json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode('utf8')
    path = work / 'result-manifest.json'
    path.write_bytes(data)
    return path, data
