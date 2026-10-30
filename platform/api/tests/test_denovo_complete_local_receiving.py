"""Real producer/SQLite/Project receiving of inert partial RFD3 native evidence."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import event, select

from database import Job, RFD3LocalRedesignArtifact, RFD3LocalRedesignCandidate, RFD3LocalRedesignRequest
from services.result_ingester import ingest_job_results, validate_rfd3_local_redesign_manifest, _local_redesign_canonical_sha
from tests.rfd3_native_fixture import write_native_result
from tests.test_core_protein_candidates import setup
from tests.test_rfd3_remote_return_publication import request_row, MANIFEST


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_INPUTS', str(tmp_path / 'inputs'))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'results'))


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
@pytest.mark.parametrize('requested,generated,unpaired,incomplete', [
    (3, 1, False, False), (2, 0, True, False), (1, 2, True, False), (1, 1, False, True),
])
async def test_partial_zero_unexpected_and_trajectory_evidence_survives_cold_receiving(
        tmp_path, status, requested, generated, unpaired, incomplete):
    request, digest, manifest_digest, root = write_native_result(tmp_path,
        job_id='rfd', request_id='req', num_designs=requested, generated=generated,
        unpaired=unpaired, trajectories=incomplete, incomplete_trajectories=incomplete,
        execution_status=status)
    sealed = (root / MANIFEST).read_bytes()
    manifest = json.loads(sealed)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add_all([Job(id='rfd', name='TEST native partial', model_id='protein_local_redesign',
                mode='local_redesign', status=status, output_dir=str(root), params={}), request_row(request, digest)])
            await session.commit()
            assert await ingest_job_results('rfd', str(root), session) == generated
            assert await ingest_job_results('rfd', str(root), session) == generated
        async with factory() as fresh:
            record = await fresh.get(RFD3LocalRedesignRequest, 'req')
            assert record.status == status and record.result_manifest_sha256 == manifest_digest
            artifacts = list(await fresh.scalars(select(RFD3LocalRedesignArtifact)))
            assert len(list(await fresh.scalars(select(RFD3LocalRedesignCandidate)))) == generated
            assert sum(row.role == 'unpaired_native_output' for row in artifacts) == int(unpaired)
            assert all(row.candidate_id is None for row in artifacts if row.role == 'unpaired_native_output')
            for artifact in artifacts:
                assert hashlib.sha256(Path(artifact.storage_path).read_bytes()).hexdigest() == artifact.content_sha256
            from services.global_experiments.adapters import registry
            adapter = registry.get('bms.core-job.protein_local_redesign.adapter.v1')
            receipt = await adapter.verify(fresh, 'req')
            assert receipt['content_digest'] == manifest_digest
            assert receipt['metadata']['candidate_count'] == generated
            assert receipt['metadata']['canonical_state'] == status
            writes = []
            def observed(connection, cursor, statement, parameters, context, many):
                if statement.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}:
                    writes.append(statement)
            event.listen(engine.sync_engine, 'before_cursor_execute', observed)
            try:
                from routers.jobs import get_rfd3_local_redesign_result
                for _ in range(2):
                    result = await get_rfd3_local_redesign_result('rfd', fresh)
                    assert result['execution_status'] == status
                    assert result['population'] == {
                        'requested': requested, 'generated': generated, 'failed': None,
                        'missing': max(0, requested - generated),
                        'unexpected': max(0, generated - requested), 'unpaired_artifacts': int(unpaired)}
                    assert result['population_completeness'] == ('complete' if requested == generated else 'partial')
                    if incomplete:
                        assert result['capabilities']['trajectories'] == {
                            'requested': True, 'available': False, 'reason': 'requested_artifacts_unavailable'}
                assert not writes and not fresh.dirty
            finally:
                event.remove(engine.sync_engine, 'before_cursor_execute', observed)
        assert (root / MANIFEST).read_bytes() == sealed
        assert manifest['population']['failed'] is None
    finally:
        await engine.dispose()


def test_legacy_v1_manifest_remains_readable_without_population_reinterpretation(tmp_path):
    request, digest, _, root = write_native_result(tmp_path, job_id='rfd', request_id='req')
    path = root / MANIFEST
    manifest = json.loads(path.read_bytes())
    manifest['schema'] = 'bms.rfd3.local-redesign.result.v1'
    manifest.pop('population')
    manifest.pop('manifest_sha256')
    manifest['manifest_sha256'] = _local_redesign_canonical_sha(manifest)
    path.write_text(json.dumps(manifest))
    result = validate_rfd3_local_redesign_manifest(root, request_row(request, digest))
    assert result['manifest']['schema'] == manifest['schema']
    assert len(result['candidates']) == 1


@pytest.mark.parametrize('subdir', ['', 'rfd3_sources'])
def test_real_source_mapping_and_native_source_document_reach_receiver(tmp_path, monkeypatch, subdir):
    from scripts.rfd3_local_redesign.source_mapping import materialize_source, lower_native
    from scripts.rfd3_local_redesign.contract import canonical_json
    real_run = subprocess.run
    def mapped_producer(command, **kwargs):
        source = Path(command[command.index('--source-file') + 1])
        native_input = Path(command[command.index('--native-input') + 1])
        receipt_path = Path(command[command.index('--preparation-receipt') + 1])
        request_path = Path(command[command.index('--request') + 1])
        # The real workflow publishes the derived source under rfd3_sources/ and records that relative path.
        native_source = native_input.parent / subdir / 'rfd3_source_native.cif'
        native_source.parent.mkdir(exist_ok=True)
        runtime_input = f'{subdir}/{native_source.name}' if subdir else native_source.name
        mapping = materialize_source(source, native_source)
        request = json.loads(request_path.read_bytes())
        receipt = json.loads(receipt_path.read_bytes())
        native = lower_native(request['rfd3'], mapping, runtime_input)
        native_payload = {receipt['design_id']: native}
        native_input.write_text(canonical_json(native_payload) + '\n')
        receipt.update(source_mapping=mapping, native_rfd3=native,
            native_input_sha256=_local_redesign_canonical_sha(native_payload),
            runtime_input={'path': runtime_input, 'sha256': mapping['native_sha256']})
        receipt_path.write_text(json.dumps(receipt))
        return real_run([*command, '--native-source', str(native_source)], **kwargs)
    monkeypatch.setattr(subprocess, 'run', mapped_producer)
    request, digest, manifest_digest, root = write_native_result(tmp_path, job_id='rfd', request_id='req')
    proof = validate_rfd3_local_redesign_manifest(root, request_row(request, digest))
    assert proof['digest'] == manifest_digest
    assert proof['preparation_receipt']['source_mapping']['original_sha256'] == request['input']['sha256']
    assert any(row['role'] == 'native_source_structure' for row in proof['descriptors'].values())


@pytest.mark.asyncio
async def test_freshly_produced_native_candidates_pass_the_completion_usability_check(tmp_path):
    from services.result_state_integrity import _rfd3_candidates_are_usable
    request, digest, _manifest_digest, root = write_native_result(tmp_path, job_id='rfd', request_id='req',
        num_designs=2, generated=2)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add_all([Job(id='rfd', name='TEST native usable', model_id='protein_local_redesign',
                mode='local_redesign', status='completed', output_dir=str(root), params={}), request_row(request, digest)])
            await session.commit()
            assert await ingest_job_results('rfd', str(root), session) == 2
            # Producer-sealed manifests carry relative storage paths; the receiver stores absolute ones.
            assert await _rfd3_candidates_are_usable(session, 'rfd', str(root))
    finally:
        await engine.dispose()


def test_each_advertised_native_generation_project_result_adapter_is_registered():
    from services.protein_project_capabilities import _NATIVE_GENERATION_SETUPS, protein_capability_record
    from services.global_experiments.adapters import registry
    for capability_id in _NATIVE_GENERATION_SETUPS:
        capability = protein_capability_record(capability_id)
        for adapter_id in capability['result_adapter_ids']:
            assert registry.get(adapter_id).adapter_id == adapter_id
