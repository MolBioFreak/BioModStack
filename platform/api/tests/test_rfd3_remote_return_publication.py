"""Inert native evidence through the real producer, return, SQLite and read API."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from sqlalchemy import select

from database import Job, RFD3LocalRedesignRequest, RFD3LocalRedesignCandidate, RFD3LocalRedesignArtifact
from services.result_ingester import ingest_job_results, validate_rfd3_local_redesign_manifest, _local_redesign_canonical_sha
from tests.rfd3_native_fixture import write_native_result
from tests.test_core_protein_candidates import setup

MANIFEST = 'collected/protein_local_redesign/rfd3_result_manifest.json'


@pytest.fixture(autouse=True)
def isolated_roots(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_INPUTS', str(tmp_path / 'inputs'))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'results'))


def produce_return(tmp_path, monkeypatch, *, trajectories=False, zero=False):
    """Move only CLI placement to worker; canonical request bytes stay untouched."""
    real_run = subprocess.run
    worker = tmp_path / 'worker'
    controller = tmp_path / 'results' / 'rfd'
    sealed = {}

    def on_worker(command, **kwargs):
        worker_output = worker / 'output'
        shutil.copytree(controller, worker_output)
        source = Path(command[command.index('--source-file') + 1])
        worker_source = worker / 'inputs' / source.name
        worker_source.parent.mkdir()
        shutil.copyfile(source, worker_source)
        translated = [str(worker_output / Path(arg).relative_to(controller))
                      if str(arg).startswith(str(controller) + '/') else
                      str(worker_source) if arg == str(source) else arg for arg in command]
        if zero:
            index = translated.index('--cif-file')
            del translated[index:index + 2]
            result = real_run(translated, capture_output=True, text=True)
            assert result.returncode != 0
            assert 'at least one native RFD3 candidate CIF artifact is required' in result.stderr
            assert not (worker_output / MANIFEST).exists()
            raise ZeroYield
        result = real_run(translated, **kwargs)
        sealed.update({str(p.relative_to(worker_output)): p.read_bytes()
                       for p in worker_output.rglob('*') if p.is_file()})
        shutil.rmtree(controller)
        shutil.copytree(worker_output, controller)
        shutil.rmtree(worker)
        return result

    with monkeypatch.context() as patcher:
        patcher.setattr(subprocess, 'run', on_worker)
        values = write_native_result(tmp_path, job_id='rfd', request_id='req', trajectories=trajectories)
    assert not worker.exists()
    assert all((controller / name).read_bytes() == content for name, content in sealed.items())
    return values


class ZeroYield(Exception):
    pass


def request_row(request, digest):
    return RFD3LocalRedesignRequest(request_id='req', job_id='rfd', request_sha256=digest,
        profile_id=request['profile_id'], profile_registry_sha256=request['profile_registry_sha256'],
        redesign_mode=request['redesign_mode'], sequence_policy=request['sequence_policy'],
        status='queued', request_json=request)


@pytest.mark.asyncio
@pytest.mark.parametrize('trajectories', [False, True])
@pytest.mark.parametrize('placement', ['returned', 'historical_local'])
async def test_worker_producer_return_ingest_replay_readback(tmp_path, monkeypatch, trajectories, placement):
    if placement == 'returned':
        request, digest, manifest_digest, root = produce_return(tmp_path, monkeypatch, trajectories=trajectories)
    else:
        request, digest, _, root = write_native_result(tmp_path, job_id='rfd', request_id='req', trajectories=trajectories)
        manifest_digest = historical_absolute_manifest(root)
    sealed = (root / MANIFEST).read_bytes()
    native_request = (root / 'requests/request.json').read_bytes()
    manifest = json.loads(sealed)
    if placement == 'returned':
        assert all(a['storage_path'] == a['relative_path'] for a in manifest['artifacts'] if a['role'] != 'source_structure')
    assert next(a for a in manifest['artifacts'] if a['role'] == 'source_structure')['storage_path'] == request['input']['path']
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add_all([Job(id='rfd', name='TEST return', model_id='protein_local_redesign',
                mode='local_redesign', status='completed', output_dir=str(root), params={}), request_row(request, digest)])
            await session.commit()
            # Existing native contract returns candidate count on replay, not newly inserted count.
            assert await ingest_job_results('rfd', str(root), session) == 1
            assert await ingest_job_results('rfd', str(root), session) == 1
        async with factory() as session:
            record = await session.get(RFD3LocalRedesignRequest, 'req')
            assert record.request_json == request and record.request_sha256 == digest
            assert record.result_manifest_sha256 == manifest_digest
            candidates = list((await session.scalars(select(RFD3LocalRedesignCandidate))).all())
            assert len(candidates) == 1
            assert candidates[0].candidate_id == manifest['candidates'][0]['candidate_id']
            assert candidates[0].artifact_manifest_sha256 == manifest['candidates'][0]['artifact_manifest_sha256']
            artifacts = list((await session.scalars(select(RFD3LocalRedesignArtifact))).all())
            assert len(artifacts) == len(manifest['artifacts']) + 1
            for artifact in artifacts:
                path = Path(artifact.storage_path)
                assert path == (Path(request['input']['path']) if artifact.role == 'source_structure' else root / artifact.relative_path)
                assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.content_sha256
            from routers.jobs import get_rfd3_local_redesign_result
            projection = await get_rfd3_local_redesign_result('rfd', session)
            assert projection['request']['result_manifest_sha256'] == manifest_digest
            assert projection['candidates'][0]['candidate_id'] == candidates[0].candidate_id
            assert projection['capabilities']['trajectories']['available'] is trajectories
            assert validate_rfd3_local_redesign_manifest(root, record)['digest'] == manifest_digest
        assert (root / MANIFEST).read_bytes() == sealed
        assert (root / 'requests/request.json').read_bytes() == native_request
    finally:
        await engine.dispose()


@pytest.mark.parametrize('damage', ['candidate', 'request', 'source', 'manifest', 'symlink', 'hardlink', 'traversal', 'storage', 'source_binding', 'candidate_digest'])
def test_return_tampering_preserves_existing_integrity(tmp_path, monkeypatch, damage):
    request, digest, _, root = produce_return(tmp_path, monkeypatch)
    manifest_path = root / MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    candidate = next(a for a in manifest['artifacts'] if a['role'] == 'structure')
    path = root / candidate['relative_path']
    if damage == 'candidate':
        path.write_bytes(b'changed')
    elif damage == 'request':
        with (root / 'requests/request.json').open('ab') as stream:
            stream.write(b' ')
    elif damage == 'source':
        Path(request['input']['path']).write_bytes(b'wrong source')
    elif damage == 'manifest':
        manifest['candidates'][0]['candidate_id'] = 'changed'
        manifest_path.write_text(json.dumps(manifest))
    elif damage == 'hardlink':
        (tmp_path / 'linked.cif.gz').hardlink_to(path)
    elif damage == 'symlink':
        outside = tmp_path / 'outside.cif.gz'
        shutil.copyfile(path, outside)
        path.unlink()
        path.symlink_to(outside)
    else:
        if damage == 'traversal':
            candidate['relative_path'] = '../outside.cif.gz'
            candidate['storage_path'] = candidate['relative_path']
        elif damage == 'storage':
            candidate['storage_path'] = '/unavailable/worker/other.cif.gz'
        elif damage == 'candidate_digest':
            manifest['candidates'][0]['artifact_manifest_sha256'] = '0' * 64
        else:
            other = tmp_path / 'inputs/other.pdb'
            shutil.copyfile(request['input']['path'], other)
            next(a for a in manifest['artifacts'] if a['role'] == 'source_structure')['storage_path'] = str(other)
        manifest['manifest_sha256'] = _local_redesign_canonical_sha({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((RuntimeError, OSError)):
        validate_rfd3_local_redesign_manifest(root, request_row(request, digest))


def test_zero_yield_remains_producer_failure(tmp_path, monkeypatch):
    with pytest.raises(ZeroYield):
        produce_return(tmp_path, monkeypatch, zero=True)


def historical_absolute_manifest(root):
    path = root / MANIFEST
    manifest = json.loads(path.read_bytes())
    # Recreate the historical producer's absolute descriptor representation.
    for artifact in manifest['artifacts']:
        if artifact['role'] != 'source_structure':
            artifact['storage_path'] = str(root / artifact['relative_path'])
    by_path = {a['relative_path']: a for a in manifest['artifacts']}
    for candidate in manifest['candidates']:
        candidate['artifacts'] = [by_path[a['relative_path']] for a in candidate['artifacts']]
        candidate['artifact_manifest_sha256'] = _local_redesign_canonical_sha(candidate['artifacts'])
    manifest['manifest_sha256'] = _local_redesign_canonical_sha({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
    path.write_text(json.dumps(manifest))
    return manifest['manifest_sha256']
