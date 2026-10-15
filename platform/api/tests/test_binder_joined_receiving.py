"""Offline joined receiving; fixture coordinates are NOT inference evidence.

Real writers/serializers are reused below. No observation/parser is stubbed.
The protein graph additionally runs real Nextflow with explicitly inert science.
"""
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from database import Job, JobArtifact, Design
from services.remote_execution import executor as ex
from services.remote_execution.contracts import RemoteResultManifest
from services.binder_result_observation import observe_retained_binder_results
from services.binder_native_selection import resolve_native_source
from services import binder_source_materialization as materializer
from tools import bms_remote_worker as worker
from test_remote_lifecycle_gaps import store, receipt

REPO = Path(__file__).resolve().parents[3]


def write_outputs(root, model, scratch, monkeypatch):
    """Reuse existing writer fixtures, keeping their inert provenance explicit."""
    root.mkdir(parents=True, exist_ok=True)
    if model == 'bindcraft2':
        from test_bindcraft2_publication import campaign
        campaign(root / 'bindcraft2/campaign')
        from test_binder_custody_repair import inert_cif
        for path in (root / 'bindcraft2/campaign').rglob('*.cif'):
            path.write_bytes(inert_cif(design='t_seq0'))
    elif model == 'ppiflow':
        import importlib
        import os
        monkeypatch.syspath_prepend(str(REPO / 'tests'))
        fixture = importlib.import_module('test_ppiflow_target_producer_mapping')
        native_source = Path(os.environ['BMS_TEST_PPIFLOW_SOURCE'])
        native_io = fixture.native_io.__wrapped__(native_source)
        fixture.test_actual_parser_crop_dataset_writer_and_projection(
            scratch, native_source, native_io, False)
        publication = root / 'ppiflow_generation'; publication.mkdir()
        shutil.copytree(scratch / 'result/candidates', publication / 'candidates')
        for name in ('samples.jsonl', 'opaque-key_0.pdb', 'opaque-key_0.pdb.sample.json'):
            shutil.copyfile(scratch / 'result' / name, publication / name)
    elif model == 'boltzgen':
        from test_boltzgen_candidate_accounting import published
        out = published(root)
        # Raw publication receives every emitted source, not filter winners.
        shutil.copytree(root / 'native', root / 'collected/boltzgen_raw')
    elif model == 'rfantibody':
        # Existing native document parser fixture, explicitly not RF inference.
        from test_binder_custody_repair import inert_cif
        folder = root / 'run/rfantibody/output'; folder.mkdir(parents=True)
        for index in range(2):
            (folder / f'sample-{index}.cif').write_bytes(inert_cif())
    elif model == 'binder_refinement':
        from test_selected_binder_publication import publish_fixture
        publish_fixture(root, model, 'ppiflow', [
            {'staged_name': f'source-{i}.pdb', 'source_meta': {
                'id': f'source-{i}', 'design_id': f'design-{i}', 'structure_state': 'target'}}
            for i in range(2)])
    elif model in {'fampnn', 'proteinmpnn'}:
        from test_selected_binder_publication import PDB, script
        native = scratch / 'inert-native.pdb'; native.write_text(PDB)
        for i in range(2):
            path = scratch / f'sample-{i}.pdb'; shutil.copyfile(native, path)
            script('publish_binder_refinement').publish(path, {
                'terminal_producer': model, 'native_output_id': f'sample:{i}',
                'artifact_class': 'sequence_designed_complex'}, root / 'pdb_files')
    elif model == 'protenix':
        from test_protenix_native_confidence import make_publication
        _, _, publication = make_publication(scratch)
        shutil.copytree(publication, root, dirs_exist_ok=True)
    elif model == 'boltz2':
        from test_boltz_scientific_persistence import publication
        publication(root, count=2)
    elif model == 'ligandmpnn':
        from test_sequence_native_publication import emitted_result
        returned, _, _, _, params = emitted_result(scratch, monkeypatch, model,
            'ligand_aware', write_outputs=False)
        shutil.copytree(returned, root, dirs_exist_ok=True)
        return params
    return {}


async def seal_return(store, tmp_path, native, source, attempt, model, params, state, generation):
    envelope = dict(output_directory=str(source), attempt_id='attempt', job_id='job',
                    source_revision='a' * 40, source_tree='b' * 40)
    worker.envelope_path(attempt).write_text(json.dumps(envelope))
    raw = worker.build_result_manifest(attempt, envelope, 1, generation=generation)
    encoded = json.dumps(raw).encode()
    (source / 'result-manifest.json').write_bytes(encoded)
    status = receipt(state).model_copy(update=dict(exit_code=1, generation=generation,
        completed_at=RemoteResultManifest.model_validate(raw).completed_at,
        native_output_directory=str(native), result_manifest_sha256=hashlib.sha256(encoded).hexdigest()))
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id = model
        job.mode = 'ligand_aware' if model == 'ligandmpnn' else 'protein_binder'
        job.output_dir = str(tmp_path / 'original-controller-output')
        job.params = ({'bc2_campaign_dir': str((native if generation else Path(job.output_dir)) / 'bindcraft2')}
                      if model == 'bindcraft2' else params)
        job.status = job.queue_status = job.remote_state = state
        job.execution_bundle_sha256 = raw['execution_envelope_sha256']
        job.provenance = {'remote_execution_receipt': {
            **status.model_dump(mode='json'), 'remote_attempt_dir': str(attempt),
            'terminal_status': status.model_dump(mode='json')}}
        archive = ex._diagnostic_destination(job.id, ex._pull_identity(job), status.result_manifest_sha256)
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, archive)
        shutil.rmtree(attempt)
        ex._verify_result_package(archive, job, status)
        await session.commit()
    return archive / native.relative_to(source)


@pytest.mark.asyncio
@pytest.mark.parametrize('state,generation', [('failed', 0), ('cancelled', 2)])
@pytest.mark.parametrize('model', ['bindcraft2', 'boltzgen', 'rfantibody', 'ppiflow',
    'binder_refinement', 'fampnn', 'proteinmpnn', 'protenix', 'boltz2', 'ligandmpnn'])
async def test_actual_join_offline_retry_sources(store, tmp_path, monkeypatch, model, state, generation):
    attempt = tmp_path / 'worker'; source = attempt / 'results'
    native = source / 'generation-2' if generation else source
    scratch = tmp_path / 'writer'; scratch.mkdir()
    params = write_outputs(native, model, scratch, monkeypatch)
    if generation:
        (source / 'older-generation.pdb').write_text('REMARK not current science\n')
    root = await seal_return(store, tmp_path, native, source, attempt, model, params, state, generation)
    assert not attempt.exists()
    async def forbidden(*args, **kwargs):
        pytest.fail('offline import contacted science, worker, or success finalization')
    monkeypatch.setattr(ex, 'launch_remote_job', forbidden)
    monkeypatch.setattr(ex, '_prove_pull_endpoint', forbidden)
    monkeypatch.setattr(ex, 'remote_status', forbidden)
    monkeypatch.setattr('services.nextflow.launch_nextflow_job', forbidden)
    monkeypatch.setattr('services.result_state_integrity.finalize_successful_job', forbidden)
    monkeypatch.setattr(materializer, 'to_allowed_relative', str)
    monkeypatch.setattr('routers.jobs.get_inputs_dir', lambda: tmp_path / 'inputs')

    # The old unjoined helper rejects this actual archive before any publication.
    async with store() as session:
        job = await session.get(Job, 'job')
        assert Path(job.output_dir) != root
        with pytest.raises(ValueError, match='root.*Job owner'):
            await observe_retained_binder_results(session, job, root)

    for repeat in range(2):
        tasks = BackgroundTasks()
        async with store() as session:
            await ex.request_remote_diagnostic_pull(session, await session.get(Job, 'job'), tasks)
        await tasks()
        async with store() as session:
            job = await session.get(Job, 'job')
            assert job.provenance['remote_diagnostics']['state'] == 'returned', job.provenance
            assert (job.status, job.queue_status, job.remote_state) == (state, state, state)
            assert job.output_dir == str(tmp_path / 'original-controller-output')
            assert not list((await session.scalars(select(Design))).all())
            observed = await observe_retained_binder_results(session, job, root)
            rows = list((await session.scalars(select(JobArtifact))).all())
            ids = {a.id for a in rows}
            if repeat:
                assert ids == original_ids
                assert observed == original_observed
            else:
                original_ids, original_observed = ids, observed
            assert rows and all(Path(a.storage_path).is_relative_to(root) for a in rows)
            if model == 'ligandmpnn':
                assert observed['native_document']['records']
                assert not any(a.media_type.startswith('chemical/') for a in rows)
                continue
            structures = [a for a in rows if a.media_type in {'chemical/x-pdb', 'chemical/x-mmcif'}]
            assert structures and len(observed['outputs']) == len(structures)
            for artifact in structures:
                path, identity = await resolve_native_source(session, job.id, artifact.id)
                assert identity['artifact_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
                dest = tmp_path / f'materialized-{repeat}-{artifact.id}'; dest.mkdir()
                prepared = materializer.materialize_source_bytes(path.read_bytes(), path.suffix,
                    dest, output_format='native')
                assert prepared['inspection_error'] is None
                assert prepared['original_sha256'] == artifact.sha256
                from services.binder_selected_round import resolve_ref
                selected = await resolve_ref(session, job, {'kind': 'native_document',
                    'job_id': job.id, 'artifact_id': artifact.id}, output_format='native')
                assert Path(selected['path']).read_bytes() == path.read_bytes()
                assert selected['owner_job_id'] == job.id
                if model == 'ppiflow':
                    # Both canonical snapshots AND the retained native filename
                    # must initialize follow-ons from emitted roles, not R/C.
                    from services.binder_selected_round import role_defaults, prediction_request
                    from services.binder_continuation import model_request, individual_model_requests
                    assert selected['binder_chains'] == ['A']
                    assert selected['target_chains'] == ['B']
                    for designer in ('proteinmpnn', 'fampnn'):
                        defaults = role_defaults(designer, {}, {}, selected)
                        request = model_request(operation=designer, params=defaults, source=job,
                            root=job, selection_dir=Path(selected['selection_dir']), execution_target_id=None)
                        child = individual_model_requests(request, designer, Path(selected['selection_dir']))[0]
                        assert child.params['design_chain'] == 'A'
                        if designer == 'fampnn':
                            assert child.params['target_chain'] == 'B'
                        assert role_defaults(designer, {'design_chain': 'edited'}, {'design_chain': 'edited'}, selected)['design_chain'] == 'edited'
                    target = {'name': 'independent-input', 'components': [
                        {'id': 'R', 'type': 'protein', 'sequence': 'AGS'}]}
                    for predictor in ('boltz2', 'protenix'):
                        child, binding = prediction_request(job, selected, target, predictor, {}, None)
                        assert child.params['sequence'] == 'AA:AGS'
                        assert binding['input_components'] == [
                            {'id': 'A', 'source_chain': 'A', 'role': 'binder', 'sequence': 'AA'},
                            {'id': 'B', 'source_chain': 'R', 'role': 'target', 'sequence': 'AGS'}]
                if model == 'binder_refinement':
                    assert identity['producer_document']['artifact_class'] == 'backbone_complex'
                if model in {'fampnn', 'proteinmpnn'}:
                    assert identity['producer_document']['artifact_class'] == 'sequence_designed_complex'
            if model == 'bindcraft2':
                from services.bindcraft2_publication import read_published_native_results
                _, publication = await read_published_native_results(job, session)
                assert publication['observations_only'] and not publication['candidates']


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_after_one,expected,redesign,anchors', [
    (False, 2, False, False), (True, 1, False, False),
    (False, 4, True, False), (False, 4, True, True)])
async def test_actual_protein_graph_partial_samples_survive_return(store, tmp_path, monkeypatch,
                                                                  fail_after_one, expected, redesign, anchors):
    import os
    import importlib
    monkeypatch.syspath_prepend(str(REPO / 'tests'))
    graph = importlib.import_module('test_ppiflow_protein_partial')
    native = Path(os.environ['BMS_TEST_PPIFLOW_SOURCE'])
    graph_root = tmp_path / 'graph'; graph_root.mkdir()
    monkeypatch.setenv('NXF_HOME', str(tmp_path / 'nextflow-home'))
    monkeypatch.setenv('NXF_OFFLINE', 'true')
    # Actual workflow/module/parser/installed writer; model/interpolant leaves
    # are explicitly inert inside the reused graph fixture.
    graph.test_actual_nextflow_module_shell_with_inert_science(native, graph_root, 2,
                                                              fail_after_one, expected, redesign, anchors)
    attempt = tmp_path / 'worker'; source = attempt / 'results'
    shutil.copytree(graph_root / 'out', source)
    root = await seal_return(store, tmp_path, source, source, attempt, 'binder_refinement',
                             {}, 'failed', 0)
    shutil.rmtree(graph_root)  # no work/source trees survive selection
    tasks = BackgroundTasks()
    async with store() as session:
        await ex.request_remote_diagnostic_pull(session, await session.get(Job, 'job'), tasks)
    await tasks()
    monkeypatch.setattr(materializer, 'to_allowed_relative', str)
    async with store() as session:
        job = await session.get(Job, 'job')
        assert job.provenance['remote_diagnostics']['state'] == 'returned', job.provenance
        observed = await observe_retained_binder_results(session, job, root)
        total = expected + (2 if redesign else 0)
        assert len(observed['outputs']) == total
        assert not list((await session.scalars(select(Design))).all())
        native_ids = set()
        for ref in observed['outputs']:
            path, identity = await resolve_native_source(session, job.id, ref['artifact_id'])
            doc = identity['producer_document']
            if doc['producer_model_id'] == 'fampnn':
                assert doc['artifact_class'] == 'sequence_designed_complex'
                assert doc['sequence_semantics'] == 'native_fampnn_output'
                assert doc['parent_artifact_class'] == 'backbone_complex'
                assert doc['source_residue_mapping']
                assert doc['chain_roles_namespace'] == 'output'
                assert list(doc['designed_chain_sequences'].values()) == ['RL' if anchors else 'RA']
                assert 'sample_index' not in doc
            else:
                assert doc['artifact_class'] == 'backbone_complex'
                assert doc['sequence_semantics'] == 'movable_positions_masked_alanine'
                assert doc['effective_settings']['ppiflow_input_model'] == (1 if anchors else 7)
            assert len(doc['residue_mapping']) == 4
            native_ids.add(doc['native_output_id'])
            target = tmp_path / ref['artifact_id']; target.mkdir()
            prepared = materializer.materialize_source_bytes(path.read_bytes(), '.pdb', target,
                                                             output_format='pdb')
            assert prepared['inspection_error'] is None
        assert len(native_ids) == total
        assert job.status == job.queue_status == 'failed'


@pytest.mark.asyncio
async def test_real_parser_import_interruption_rolls_back_and_retries(store, tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    attempt = tmp_path / 'worker'; source = attempt / 'results'
    scratch = tmp_path / 'writer'; scratch.mkdir()
    write_outputs(source, 'binder_refinement', scratch, monkeypatch)
    root = await seal_return(store, tmp_path, source, source, attempt, 'binder_refinement', {}, 'failed', 0)
    flush = AsyncSession.flush
    interrupted = []
    async def interrupt(session, *args, **kwargs):
        if not interrupted and any(isinstance(row, JobArtifact) for row in session.new):
            interrupted.append(True)
            raise RuntimeError('inert storage interruption after real native parsing')
        return await flush(session, *args, **kwargs)
    monkeypatch.setattr(AsyncSession, 'flush', interrupt)
    async def forbidden(*args, **kwargs):
        pytest.fail('import retry attempted scientific execution or worker contact')
    monkeypatch.setattr(ex, '_prove_pull_endpoint', forbidden)
    monkeypatch.setattr(ex, 'launch_remote_job', forbidden)
    monkeypatch.setattr('services.result_state_integrity.finalize_successful_job', forbidden)
    for outcome in ('failed', 'returned'):
        tasks = BackgroundTasks()
        async with store() as session:
            await ex.request_remote_diagnostic_pull(session, await session.get(Job, 'job'), tasks)
        await tasks()
        async with store() as session:
            job = await session.get(Job, 'job')
            assert job.provenance['remote_diagnostics']['state'] == outcome
            rows = list((await session.scalars(select(JobArtifact))).all())
            assert bool(rows) == (outcome == 'returned')
            assert job.status == job.queue_status == 'failed'
            assert not list((await session.scalars(select(Design))).all())
    assert interrupted


def test_local_bc2_cannot_relocate_an_unverified_worker_path(tmp_path):
    from services.bindcraft2_publication import native_campaign_root, PublicationError
    root = tmp_path / 'owned'; (root / 'bindcraft2/campaign').mkdir(parents=True)
    job = Job(id='local', model_id='bindcraft2', output_dir=str(root),
              params={'bc2_campaign_dir': '/foreign/results/bindcraft2'},
              provenance={'remote_execution_receipt': {'native_output_directory': '/foreign/results'}})
    with pytest.raises(PublicationError, match='escapes'):
        native_campaign_root(job, root)


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['bytes', 'attempt', 'manifest', 'old-root', 'symlink'])
async def test_returned_publication_custody_controls(store, tmp_path, monkeypatch, damage):
    attempt = tmp_path / 'worker'; source = attempt / 'results'; native = source / 'generation-2'
    scratch = tmp_path / 'writer'; scratch.mkdir()
    write_outputs(native, 'binder_refinement', scratch, monkeypatch)
    (source / 'older-generation.pdb').write_text('REMARK old generation\n')
    root = await seal_return(store, tmp_path, native, source, attempt, 'binder_refinement', {}, 'failed', 2)
    tasks = BackgroundTasks()
    async with store() as session:
        await ex.request_remote_diagnostic_pull(session, await session.get(Job, 'job'), tasks)
    await tasks()
    async with store() as session:
        job = await session.get(Job, 'job')
        assert job.provenance['remote_diagnostics']['state'] == 'returned'
        path = next(root.rglob('generator_*.json'))
        if damage == 'bytes':
            path.write_bytes(path.read_bytes() + b' ')
        elif damage == 'attempt':
            job.remote_attempt_id = 'another-attempt'
        elif damage == 'manifest':
            manifest = root.parent / 'result-manifest.json'
            manifest.write_bytes(manifest.read_bytes() + b' ')
        elif damage == 'symlink':
            original = path.read_bytes(); path.unlink()
            target = tmp_path / 'foreign.json'; target.write_bytes(original)
            path.symlink_to(target)
        with pytest.raises((ValueError, ex.RemoteExecutionError)):
            await observe_retained_binder_results(session, job, root.parent if damage == 'old-root' else root)
        assert job.status == job.queue_status == 'failed'


@pytest.mark.asyncio
async def test_unsupported_model_keeps_diagnostic_behavior(store, tmp_path):
    async with store() as session:
        job = await session.get(Job, 'job'); job.model_id = 'unrelated-model'
        assert not (await observe_retained_binder_results(session, job, tmp_path))['available']
    attempt = tmp_path / 'worker'; source = attempt / 'results'; source.mkdir(parents=True)
    (source / 'native.log').write_text('inert diagnostic log')
    await seal_return(store, tmp_path, source, source, attempt, 'unrelated-model', {}, 'failed', 0)
    tasks = BackgroundTasks()
    async with store() as session:
        await ex.request_remote_diagnostic_pull(session, await session.get(Job, 'job'), tasks)
    await tasks()
    async with store() as session:
        job = await session.get(Job, 'job')
        assert job.provenance['remote_diagnostics']['state'] == 'returned'
        assert not list((await session.scalars(select(JobArtifact))).all())
        assert job.status == job.queue_status == 'failed'
