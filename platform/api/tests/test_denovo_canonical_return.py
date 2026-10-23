"""Canonical native return/import/readback on private filesystem and SQLite.

Only producer file bytes are inert fixtures. Worker inventory, publication rename,
importer, Project adapter and selected-source receiving are production owners.
"""
import gzip
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from database import Job, JobArtifact, Design
from services.remote_execution import executor as ex, result_generation as gen
from services.remote_execution.contracts import RemoteResultManifest
from services.result_ingester import ingest_job_results
from services.rfd4_proteina_results import read_published_generation_page, read_publication_record
from services.global_experiments.adapters import NativeBinderJobResultAdapter
from tools import bms_remote_worker as worker
from test_remote_lifecycle_gaps import store
from test_md_selected_model import ensemble


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def cif_ensemble():
    import io
    from Bio.PDB import MMCIFIO, PDBParser
    writer = MMCIFIO()
    writer.set_structure(PDBParser(QUIET=True).get_structure('ensemble', io.StringIO(ensemble().decode())))
    stream = io.StringIO(); writer.save(stream)
    return stream.getvalue().encode()


def native_tree(output, model, population, compressed=True):
    root = output / 'collected' / ('protein_cad_native' if model in {'disco', 'laproteina'} else model)
    root.mkdir(parents=True)
    document = 'native/exact.cif.gz' if compressed else 'native/exact.cif'
    (root / 'native').mkdir()
    (root / document).write_bytes(gzip.compress(cif_ensemble(), mtime=0) if compressed else cif_ensemble())
    if model in {'disco', 'laproteina'}:
        rows = [] if population == 'zero' else [
            {'design_id': 'native-only', 'structure_path': document, 'metadata_path': 'raw/metadata/one.json', 'sequence': 'AG'},
            {'design_id': 'sequence-only', 'structure_path': None, 'metadata_path': 'raw/metadata/two.json', 'sequence': 'ACDE'}]
        write(root / 'raw/metadata/one.json', {'native_summary_confidence': {'score': None}, 'native_identity': {'seed': 0}})
        write(root / 'raw/metadata/two.json', {'designed_sequence': 'ACDE', 'missing': ['structure']})
        write(root / 'design_manifest.json', rows)
        write(root / f'{model}_publication.json', {'schema': f'{model}_native_v1', 'execution_failed': True,
              'native_returncode': 23, 'emitted_sample_count': len(rows), 'diagnostics': []})
    else:
        common = {'model_id': model, 'contract': model + '_v1', 'artifacts': [{'path': document}],
                  'population_completeness': 'partial', 'execution_status': 'failed'}
        if model == 'rfd4_proteina':
            write(root / 'manifest.json', {**common, 'records': [] if population == 'zero' else [
                {'example_id': 'same-native-id', 'document': document, 'population': 'completed_rollout'}],
                'events': [{'event': 'search_scoring', 'population': 'ranking_intermediate', 'reward': 0}]})
        else:
            write(root / 'complexa-results.json', {**common, 'documents': [] if population == 'zero' else [
                {'path': document, 'role': 'lookahead'}], 'observations': [{'values': {'loss': None}}]})
    return root


def stage_worker_return(job, output, attempt):
    attempt.mkdir(parents=True, exist_ok=True)
    envelope = {'output_directory': str(output), 'job_id': job.id, 'attempt_id': job.remote_attempt_id,
                'source_revision': job.execution_source_revision, 'source_tree': job.execution_source_tree}
    write(attempt / 'execution-envelope.json', envelope)
    job.execution_bundle_sha256 = worker.sha256_file(attempt / 'execution-envelope.json')
    manifest = worker.build_result_manifest(attempt, envelope, 23)
    parsed = RemoteResultManifest.model_validate(manifest)
    raw = worker.canonical_bytes(manifest)
    incoming = gen.staging_path(job, hashlib.sha256(raw).hexdigest())
    shutil.copytree(output, incoming)
    (incoming / 'result-manifest.json').write_bytes(raw)
    return parsed, incoming


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['disco', 'laproteina', 'rfd4_proteina', 'complexa'])
@pytest.mark.parametrize('population', ['zero', 'partial'])
@pytest.mark.parametrize('parent', [False, True])
@pytest.mark.parametrize('compressed', [False, True])
async def test_native_worker_import_cold_project_and_exact_document(store, tmp_path, monkeypatch, model, population, parent, compressed):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = tmp_path / 'worker-output'
    native_tree(output, model, population, compressed)
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id = 'protein_modification_experimental' if parent else ('protein_cad_experimental' if model in {'disco', 'laproteina'} else model)
        job.mode = 'de_novo_design' if parent else ('design' if model in {'disco', 'laproteina'} else 'generate')
        job.params = {'generator_model_id': model} if parent else {'backend': model}
        job.status = 'failed'
        job.output_dir = str(tmp_path / 'local')
        manifest, incoming = stage_worker_return(job, output, tmp_path / 'attempt')
        assert all('pdb_files' not in member.relative_path for member in manifest.artifacts)
        await session.commit()
        ex._publish_result_generation(job, incoming)
        assert await ingest_job_results(job.id, job.output_dir, session, commit=False) == 0
        await session.commit()
        await ex._recover_result_generation(session, job)
    shutil.rmtree(output)
    async with store() as fresh:
        job = await fresh.get(Job, 'job')
        page = await read_published_generation_page(job, fresh)
        assert page['model_id'] == model and page['execution_status'] == 'failed'
        assert not list(await fresh.scalars(select(Design)))
        adapter = NativeBinderJobResultAdapter(job.model_id)
        search = await adapter.search(fresh, query='job', limit=10)
        assert [r.entity_id for r in search] == ['job']
        receipt = await adapter.verify(fresh, 'job')
        assert receipt['metadata']['job_status'] == 'failed'
        for state in ('running', 'cancelled', 'completed', 'failed'):
            job.status = state
            await fresh.flush()
            assert (await adapter.verify(fresh, 'job'))['metadata']['canonical_state'] == state
            assert [r.canonical_state for r in await adapter.search(fresh, query='job', limit=10)] == [state]
        if population == 'partial':
            ref = page['records'][0]['candidate_ref']
            assert ref['kind'] == 'native_document'
            artifact = await fresh.get(JobArtifact, ref['artifact_id'])
            raw = Path(artifact.storage_path).read_bytes()
            assert (gzip.decompress(raw) if compressed else raw) == cif_ensemble()
            from services.md import starting_structures as sources
            exact = sources.StartingStructureSourceRef.model_validate({'kind': 'candidate', 'id': job.id,
                'candidate_ref': {**ref, 'model': 2}})
            if compressed:
                # Compressed document receiving/materialization is the shared source
                # owner's API. MD currently refuses compressed input (failure retained
                # in canonical-fourth.xml); do not weaken that owner here.
                from services.binder_source_materialization import resolve_structure_source, StructureSourceRequest, materialize_source_bytes
                from services.binder_diagnostic_selection import CandidateDocument
                path, identity = await resolve_structure_source(StructureSourceRequest(job_id=job.id,
                    document=CandidateDocument(artifact_id=artifact.id), model_number=2), fresh)
                assert path == Path(artifact.storage_path)
                derivative_root = tmp_path / 'derived'; derivative_root.mkdir(exist_ok=True)
                derivative = materialize_source_bytes(raw, '.cif.gz', derivative_root,
                    output_format='cif', model_number=2, verified_original_sha256=artifact.sha256)
                assert derivative['model_number'] == 2
                assert identity['artifact_sha256'] == hashlib.sha256(raw).hexdigest()
            else:
                resolved = await sources.resolve_source(exact, fresh)
                try:
                    assert sources.read_resolved_structure(resolved).data == sources.select_structure_model(cif_ensemble(), 'cif', 2)
                finally:
                    resolved.close()
            if model in {'disco', 'laproteina'}:
                sequence = page['records'][1]
                assert sequence['candidate_ref']['kind'] == 'native_sequence'
                reread, publication = await read_publication_record(job, fresh, sequence['candidate_ref'])
                assert reread['sequence'] == 'ACDE' and publication['manifest_sha256']
        else:
            assert all(not r['structures'] for r in page['records'])
        assert await ingest_job_results(job.id, job.output_dir, fresh) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['prepared', 'prior_moved', 'new_moved', 'flushed', 'commit'])
async def test_native_import_prepostcommit_recovery(store, tmp_path, monkeypatch, fault):
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id, job.mode, job.params = 'protein_cad_experimental', 'design', {'backend': 'disco'}
        job.output_dir = str(tmp_path / 'local')
        local = Path(job.output_dir); local.mkdir()
        (local / 'historical.txt').write_text('keep historical bytes')
        await session.commit()
        output = tmp_path / 'worker'
        native_tree(output, 'disco', 'partial')
        _, incoming = stage_worker_return(job, output, tmp_path / 'attempt')
        await session.commit()
        def crash(name):
            if name == fault:
                raise RuntimeError('injected ' + name)
        monkeypatch.setattr(gen, '_checkpoint', crash)
        try:
            ex._publish_result_generation(job, incoming)
            await ingest_job_results(job.id, job.output_dir, session, commit=False)
            if fault == 'flushed':
                await session.flush()
                raise RuntimeError('injected flushed')
            await session.commit()
            if fault == 'commit':
                raise RuntimeError('injected commit')
        except RuntimeError:
            pass
        monkeypatch.setattr(gen, '_checkpoint', lambda name: None)
        await session.rollback()
        job = await session.get(Job, 'job', populate_existing=True)
        assert await ex._recover_result_generation(session, job)
        assert not await ex._recover_result_generation(session, job)
    async with store() as fresh:
        job = await fresh.get(Job, 'job')
        artifacts = list(await fresh.scalars(select(JobArtifact)))
        if fault == 'commit':
            assert artifacts and (await read_published_generation_page(job, fresh))['total'] == 2
            assert (incoming.with_name(incoming.name + '.previous') / 'historical.txt').read_text() == 'keep historical bytes'
        else:
            assert not artifacts and 'native_generation_publication' not in (job.provenance or {})
            assert (Path(job.output_dir) / 'historical.txt').read_text() == 'keep historical bytes'


@pytest.mark.asyncio
async def test_worker_return_retains_md_original_and_selected_after_cleanup(store, tmp_path, monkeypatch):
    md_job_id = '11111111-1111-4111-8111-111111111111'
    from scripts.bms_md.aggregate_children import retain_source_snapshots
    from services.md import starting_structures as sources
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    original = tmp_path / "producer's-original.pdb"; original.write_bytes(ensemble())
    selected = tmp_path / 'selected.pdb'; selected.write_bytes(sources.select_structure_model(ensemble(), 'pdb', 2))
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    source_ref = {'kind': 'candidate', 'id': 'deleted-producer', 'candidate_ref': {
        'kind': 'native_document', 'job_id': 'deleted-producer', 'artifact_id': 'exact-nonprimary', 'target_state': 'bound', 'model': 2}}
    provenance = {'source_ref': source_ref, 'model_number': 2, 'original_format': 'pdb',
        'snapshot': {'structure': str(selected), 'structure_sha256': digest(selected), 'structure_bytes': selected.stat().st_size},
        'original_snapshot': {'path': str(original), 'sha256': digest(original), 'bytes': original.stat().st_size}}
    output = tmp_path / 'worker'
    retain_source_snapshots(provenance, output)
    async with store() as session:
        job = await session.get(Job, 'job')
        job.id = md_job_id
        job.model_id, job.mode, job.params = 'molecular_dynamics', 'simulate', {'md_source_provenance': provenance}
        job.output_dir = str(tmp_path / 'local')
        manifest, incoming = stage_worker_return(job, output, tmp_path / 'attempt')
        names = {a.relative_path for a in manifest.artifacts}
        assert names == {'retained_source.json', 'inputs/structure.pdb', 'inputs/original_structure.pdb'}
        await session.commit()
        ex._publish_result_generation(job, incoming)
        await session.commit()
        await ex._recover_result_generation(session, job)
    original.unlink(); selected.unlink(); shutil.rmtree(output)
    async with store() as fresh:
        ref = sources.StartingStructureSourceRef.model_validate({**source_ref, 'snapshot_job_id': md_job_id})
        resolved = await sources.resolve_source(ref, fresh)
        try:
            assert Path(resolved.original_runtime_path).read_bytes() == ensemble()
            assert sources.read_resolved_structure(resolved).data == sources.select_structure_model(ensemble(), 'pdb', 2)
            assert resolved.source_ref.candidate_ref.model == 2
            assert resolved.source_ref.candidate_ref.artifact_id == 'exact-nonprimary'
        finally:
            resolved.close()
