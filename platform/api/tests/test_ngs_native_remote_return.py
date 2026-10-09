"""Native return with real writers, rsync bytes, validators and DB publication.

Scientific payloads are explicitly synthetic; no Dorado/GPU or provider run is
claimed. Only the SSH endpoint is replaced by a local command transport shim.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from database import Job, Design
from model_registry import selected_execution_metadata
from services import job_result_roots, ont_ngs_completion as ngs
from services.remote_execution import executor as ex, result_generation as gen, transport
from services.remote_execution.contracts import RemoteResultManifest, RemoteAttemptStatus
from services.remote_stage_receipts import write_remote_stage_receipt, validate_remote_stage_receipts
from tools.bms_remote_worker import build_result_manifest, atomic_json, envelope_path
from test_remote_lifecycle_gaps import store
from test_ont_ngs_capability_lifecycle import _write_terminal_product_tree


@pytest.fixture
def loopback_transport(tmp_path, monkeypatch):
    # Run the real SSH command/rsync server protocol locally, without sockets.
    shim = tmp_path / 'ssh-fixture'
    shim.write_text('#!' + sys.executable + '\nimport os,sys\nargs = sys.argv[sys.argv.index("rsync"):] if "rsync" in sys.argv else sys.argv[2:]\nos.execvp("sh", ["sh", "-c", " ".join(args)])\n')
    shim.chmod(0o700)
    monkeypatch.setattr(transport, '_ssh_base', lambda _connection: [str(shim), 'fixture-host'])
    connection = SimpleNamespace(username='fixture', host='fixture-host', provision_operation_id=None)
    return connection


def stage(monkeypatch, root, attempt, name, outputs, *, job_id='job'):
    monkeypatch.delenv('BMS_COMPONENT_CONTEXT', raising=False)
    for key, value in {'BMS_REMOTE_EXECUTION': '1', 'BMS_REMOTE_ATTEMPT_ID': attempt,
                       'BMS_REMOTE_JOB_ID': job_id, 'BMS_REMOTE_OUTPUT_ROOT': str(root)}.items():
        monkeypatch.setenv(key, value)
    write_remote_stage_receipt(job_id=job_id, stage=name, status='complete', outputs=outputs,
                               job_root_relative=True)


def seal(worker, output, job, *, exit_code=0):
    envelope = dict(output_directory=str(output), job_id=job.id, attempt_id=job.remote_attempt_id,
                    source_revision=job.execution_source_revision, source_tree=job.execution_source_tree)
    atomic_json(envelope_path(worker), envelope)
    # The test uses the real envelope bytes as launch-bound identity.
    job.execution_bundle_sha256 = hashlib.sha256(envelope_path(worker).read_bytes()).hexdigest()
    payload = build_result_manifest(worker, envelope, exit_code)
    atomic_json(output / 'result-manifest.json', payload)
    raw = (output / 'result-manifest.json').read_bytes()
    status = RemoteAttemptStatus(job_id=job.id, attempt_id=job.remote_attempt_id,
        state='succeeded' if exit_code == 0 else 'failed', exit_code=exit_code, quiescent=True,
        result_manifest_sha256=hashlib.sha256(raw).hexdigest())
    return payload, raw, status


async def transfer(connection, worker, job, status):
    incoming = gen.staging_path(job, status.result_manifest_sha256)
    incoming.parent.mkdir(parents=True, exist_ok=True)
    manifest = await ex._fetch_result_manifest(connection, str(worker), incoming, job, status)
    records = [a for a in manifest.artifacts if not (incoming / a.relative_path).is_file()]
    if records:
        # This uses actual rsync, the durable supervisor, and existing transfer
        # handoff/quiescence. No mocked package copy or fabricated return receipt.
        gen.begin_transfer(incoming)
        await transport.rsync_selected_from_remote(connection, str(worker), incoming,
            [a.relative_path for a in records], max_file_bytes=max(a.size_bytes for a in records))
        gen.end_transfer(incoming)
    return ex._verify_result_package(incoming, job, status), incoming


def prepare_job(job, tmp_path, monkeypatch, workflow='ont_basecall_dna', mode='basecall_dna', input_mode='pod5'):
    results = tmp_path / 'results'
    results.mkdir(exist_ok=True)
    monkeypatch.setenv('BMS_RESULTS_DIR', str(results))
    job.model_id, job.mode = 'nanopore', mode
    job.params = {'ont_workflow_id': workflow, 'ont_input_mode': input_mode}
    job.output_dir = str(results / 'job')
    job.remote_attempt_id = str(uuid4())
    job.nextflow_run_id = 'remote:' + job.remote_attempt_id
    job.remote_state = 'returning'
    job.awaiting_input = False
    return results


def bind_contract(job, remote):
    contract = ex.resolve_job_result_contract(job)
    job.provenance = dict(job.provenance or {}, remote_execution_receipt={
        'remote_attempt_dir': str(remote.parent), 'local_result_root': job.output_dir,
        'expected_result_contract_sha256': hashlib.sha256(json.dumps(contract,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()})


@pytest.mark.asyncio
@pytest.mark.parametrize('empty', [False, True])
async def test_directory_wire_transfer_and_collector(store, tmp_path, monkeypatch, loopback_transport, empty):
    async with store() as session:
        job = await session.get(Job, 'job')
        prepare_job(job, tmp_path, monkeypatch)
        worker = tmp_path / 'worker'
        output = worker / 'results'
        native = output / 'collector'
        (native / 'demux').mkdir(parents=True)
        (native / 'unselected').mkdir()
        if not empty:
            (native / 'demux' / 'reads.txt').write_bytes(b'reads')
        stage(monkeypatch, native, job.remote_attempt_id, 'dorado_demux', ['demux'])
        payload, raw, status = seal(worker, output, job)
        assert payload['directories'] == ['collector/demux']
        bind_contract(job, output)
        manifest, incoming = await transfer(loopback_transport, output, job, status)
        assert (incoming / 'result-manifest.json').read_bytes() == raw
        assert (incoming / 'collector/demux').is_dir()
        assert not (incoming / 'collector/unselected').exists()
        status = status.model_copy(update={'native_output_directory': str(native)})
        relative, read_root, view = ex._native_result_view(job, status, incoming, manifest)
        assert str(relative) == 'collector' and view.directories == ['demux']
        receipts = validate_remote_stage_receipts(output_root=read_root, job_id=job.id,
            attempt_id=job.remote_attempt_id, manifest=view)
        assert receipts[0]['outputs'] == ['demux']
        shutil.rmtree(read_root / 'demux')
        with pytest.raises(ValueError, match='directory is missing'):
            validate_remote_stage_receipts(output_root=read_root, job_id=job.id,
                attempt_id=job.remote_attempt_id, manifest=view)
        with pytest.raises(ex.RemoteExecutionError, match='directory is missing'):
            ex._verify_result_package(incoming, job, status)


@pytest.mark.asyncio
async def test_v1_received_bytes_and_unsupported_empty_directory(store, tmp_path, monkeypatch, loopback_transport):
    async with store() as session:
        job = await session.get(Job, 'job')
        prepare_job(job, tmp_path, monkeypatch)
        worker = tmp_path / 'worker'
        output = worker / 'results'
        (output / 'empty').mkdir(parents=True)
        stage(monkeypatch, output, job.remote_attempt_id, 'native', ['empty'])
        payload, _, status = seal(worker, output, job)
        payload.pop('directories')
        payload.pop('generation')  # Historical absent defaults must not alter received identity.
        payload['schema'] = 'bms.remote-result-manifest.v1'
        raw = ('\n' + json.dumps(payload, indent=3) + '\n').encode()
        (output / 'result-manifest.json').write_bytes(raw)
        status = status.model_copy(update={'result_manifest_sha256': hashlib.sha256(raw).hexdigest()})
        manifest, incoming = await transfer(loopback_transport, output, job, status)
        assert (incoming / 'result-manifest.json').read_bytes() == raw
        assert 'directories' not in manifest.model_dump(by_alias=True)
        assert 'directories' not in json.loads(manifest.model_dump_json(by_alias=True))
        with pytest.raises(ValueError, match='not integrity manifested'):
            validate_remote_stage_receipts(output_root=incoming, job_id=job.id,
                attempt_id=job.remote_attempt_id, manifest=manifest)
        for changed in ({**payload, 'directories': []}, {**payload, 'schema': 'bms.remote-result-manifest.v2'}):
            with pytest.raises(ValueError):
                RemoteResultManifest.model_validate(changed)


@pytest.mark.asyncio
@pytest.mark.parametrize('anchor_state', ['valid', 'missing', 'invalid', 'zero'])
async def test_dorado_real_return_fresh_consumers(store, tmp_path, monkeypatch, loopback_transport, anchor_state):
    from services.ont_barcode_units import load_barcode_units, load_barcode_unit
    from services.ont_barcode_batches import _source_snapshot, _validate_source_products
    from services.ont_signal_workbench import _derive_source_runtime_identity
    async with store() as session:
        job = await session.get(Job, 'job')
        prepare_job(job, tmp_path, monkeypatch)
        worker = tmp_path / 'worker'
        output = worker / 'results'
        _, params = _write_terminal_product_tree(output)
        job.params = dict(job.params, **params)
        if anchor_state == 'missing':
            (output / 'basecall/dorado_runtime_provenance.json').unlink()
        elif anchor_state == 'invalid':
            (output / 'basecall/dorado_runtime_provenance.json').write_text('{}')
        elif anchor_state == 'zero':
            for name in ['demux/demux_manifest.json', 'demux/per_barcode_units.json']:
                path = output / name
                value = json.loads(path.read_bytes())
                value['units'] = []
                if 'source_calls' in value:
                    value['source_calls']['read_count'] = value['total_reads'] = 0
                path.write_text(json.dumps(value))
            path = output / 'basecall/dorado_runtime_provenance.json'
            value = json.loads(path.read_bytes())
            value['calls_bam']['read_count'] = 0
            path.write_text(json.dumps(value))
        stage(monkeypatch, output, job.remote_attempt_id, 'dorado_demux', ['demux'])
        # Misleading protein files must not be scanned into fake Designs.
        (output / 'all_designs.csv').write_text('name,sequence,pdb_path\nwrong,AAAA,wrong.pdb\n')
        (output / 'wrong.pdb').write_text('HEADER synthetic non-NGS sidecar\n')
        _, raw, status = seal(worker, output, job)
        bind_contract(job, output)
        await session.commit()
        manifest, incoming = await transfer(loopback_transport, output, job, status)
        assert not Path(job.output_dir).exists()
        assert await ex._finalize_pulled_results(session, job, status, manifest, incoming)
        await ex._recover_result_generation(session, job)
    shutil.rmtree(worker)
    async with store() as session:
        fresh = await session.get(Job, 'job')
        assert (fresh.status, fresh.queue_status, fresh.remote_state) == ('completed', 'completed', 'ingested')
        assert await session.scalar(select(func.count(Design.id))) == 0
        assert fresh.provenance['result_integrity']['result_kind'] == 'ngs_native'
        assert (Path(fresh.child_output_dir) / 'result-manifest.json').read_bytes() == raw
        assert fresh.provenance['remote_result_generation']['manifest_sha256'] == hashlib.sha256(raw).hexdigest()
        from fastapi import BackgroundTasks
        before = dict(fresh.provenance)
        tasks = BackgroundTasks()
        assert await ex.request_remote_result_pull(session, fresh, tasks, automatic=True) is False
        assert not tasks.tasks and fresh.provenance == before

        assert _derive_source_runtime_identity(fresh, '3' * 64)['authority_state'] == 'legacy_unknown'
        if anchor_state in {'valid', 'zero'}:
            anchor = fresh.provenance['ont_dorado_terminal_products']
            assert 'ont_dorado_products_unavailable' not in fresh.provenance
            assert anchor['identities']['unit_count'] == (1 if anchor_state == 'valid' else 0)
            products = _validate_source_products(_source_snapshot(fresh, Path(fresh.child_output_dir)))
            assert products
            if anchor_state == 'valid':
                manifest_path = Path(fresh.child_output_dir) / 'demux/demux_manifest.json'
                units = load_barcode_units(manifest_path, Path(fresh.child_output_dir),
                    expected_manifest_sha256=anchor['products']['demux_manifest']['sha256'])
                assert len(units) == 1
                unit = load_barcode_unit(manifest_path, Path(fresh.child_output_dir), 'barcode01',
                    expected_manifest_sha256=anchor['products']['demux_manifest']['sha256'])
                assert Path(unit['bam_path']).read_bytes() == b'anchored-barcode-bam'
                assert unit['bam_sha256'] == hashlib.sha256(Path(unit['bam_path']).read_bytes()).hexdigest()
        else:
            assert 'ont_dorado_terminal_products' not in fresh.provenance
            assert fresh.provenance['ont_dorado_products_unavailable']['reason']


@pytest.mark.asyncio
async def test_fastq_real_return_absent_destination_fresh_package(store, tmp_path, monkeypatch, loopback_transport):
    from ont_ngs_completion_fixture import configure_valid_ont_terminal_completion
    from services.ngs_alignment_sessions import build_ngs_package_artifacts
    async with store() as session:
        job = await session.get(Job, 'job')
        job_id = str(uuid4())
        job.id = job_id
        from database import ExecutionTarget
        (await session.get(ExecutionTarget, 'target')).leased_job_id = job_id
        prepare_job(job, tmp_path, monkeypatch, 'ont_fastq_qc', 'fastq_qc', 'fastq')
        configure_valid_ont_terminal_completion(monkeypatch, job, tmp_path, production_validation=True)
        job.stage_progress = None
        original = Path(job.output_dir)
        worker = tmp_path / 'worker'
        output = worker / 'results'
        worker.mkdir()
        original.rename(output)
        # Complete synthetic native tables for the real result-view projection.
        from services.ont_ngs_results import _SUMMARY_METRIC_KEYS, _ALIGNMENT_METRIC_KEYS
        for filename, keys in [('fastq_qc_summary.tsv', _SUMMARY_METRIC_KEYS),
                               ('fastq_alignment_stats.tsv', _ALIGNMENT_METRIC_KEYS)]:
            values: dict[str, object] = dict.fromkeys(keys, 0)
            values.update({key: value for key, value in dict(
                reference_name='eGFP_plasmid', reference_length=5570,
                consensus_name='synthetic', consensus_status='not_produced',
                fastq_minimap2_preset='map-ont', fastq_minimap2_allow_secondary=False,
                igv_report_status='not_produced', expected_plasmid_size=5570,
                igv_report_max_sites=1, igv_track_window_bp=1).items() if key in keys})
            (output / 'fastq_qc' / filename).write_text('metric\tvalue\n' + ''.join(
                f'{key}\t{value}\n' for key, value in sorted(values.items())))
        (output / 'fastq_qc/fastq_coverage.tsv').write_text('reference\tposition\tdepth\n' + ''.join(
            f'eGFP_plasmid\t{position}\t0\n' for position in range(1, 5571)))
        (output / 'fastq_qc/read_lengths.tsv').write_text('read_id\tlength_bp\nsynthetic\t4\n')
        manifest_path = output / 'fastq_qc/qc_manifest.json'
        native = json.loads(manifest_path.read_bytes())
        for artifact in native['artifacts']:
            if artifact['state'] == 'present':
                path = manifest_path.parent / artifact['path']
                artifact.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    actual_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
        manifest_path.write_text(json.dumps(native))
        verification_path = output / 'verification/qc_manifest.json'
        verification = json.loads(verification_path.read_bytes())
        verification['variants'] = []
        profiles = json.loads((Path(__file__).resolve().parents[3] /
            'config/ngs/construct_verify_profiles.json').read_bytes())
        profile = profiles['profiles']['plasmid_strict_v1']
        verification['threshold_profile'] = dict(id='plasmid_strict_v1', version=profile['version'],
            calibration_status=profile['calibration_status'], public_accuracy_validated=False,
            sha256=hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest(), values=profile)
        stats = next(row for row in native['artifacts'] if row['kind'] == 'alignment_stats')
        verification['inputs']['alignment_stats'].update(sha256=stats['sha256'], size_bytes=stats['size_bytes'])
        verification_path.write_text(json.dumps(verification))
        for name, suffixes in ngs._REQUIRED_STAGE_OUTPUT_SUFFIXES.items():
            stage(monkeypatch, output, job.remote_attempt_id, name, list(suffixes), job_id=job_id)
        _, raw, status = seal(worker, output, job)
        bind_contract(job, output)
        await session.commit()
        manifest, incoming = await transfer(loopback_transport, output, job, status)
        assert not original.exists()
        assert await ex._finalize_pulled_results(session, job, status, manifest, incoming)
        await ex._recover_result_generation(session, job)
    shutil.rmtree(worker)
    async with store() as session:
        job = await session.get(Job, job_id)
        assert job.status == 'completed' and job.remote_state == 'ingested'
        integrity = job.provenance['result_integrity']
        assert integrity['result_kind'] == 'ngs_sequence_qc'
        descriptors = build_ngs_package_artifacts(job.id,
            source_reference_sha256=job.params['reference_sequence_sha256'],
            workflow_id='ont_fastq_qc', input_mode='fastq', source_input_path=job.params['fastq_path'],
            verify_source_input=True, job_output_dir=Path(job.child_output_dir))
        assert ngs.canonical_ngs_package_authority(descriptors)['artifact_set_sha256'] == integrity['artifact_set_sha256']
        from routers import ngs_alignment_sessions as routes
        from starlette.requests import Request
        package = await routes.list_ngs_package_artifacts(job.id, authorized_job=job)
        assert package['job_id'] == job.id
        selected = next(item for item in package['artifacts'] if item['state'] == 'present')
        request = Request({'type': 'http', 'method': 'GET', 'headers': [],
                           'path': '/api/jobs/job/ngs-artifacts', 'query_string': b''})
        response = await routes.get_ngs_package_artifact(job.id, selected['artifact_id'], request,
                                                         authorized_job=job)
        received = b''.join([chunk async for chunk in response.body_iterator])
        assert hashlib.sha256(received).hexdigest() == selected['sha256']
        assert len(received) == selected['size_bytes']
        result = await routes.get_job_scoped_ngs_result(job.id, request, authorized_job=job,
            variant_offset=0, artifact_offset=0, page_size=64, collection='all')
        assert result['job']['id'] == job.id
        assert result['coverage']['source_row_count'] == 5570

        assert (Path(job.child_output_dir) / 'result-manifest.json').read_bytes() == raw
        assert await session.scalar(select(func.count(Design.id))) == 0


def test_destination_does_not_weaken_persisted_serving(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    job = SimpleNamespace(output_dir=str(tmp_path / 'absent'), child_output_dir=None)
    assert job_result_roots.resolve_job_result_destination(job) == tmp_path / 'absent'
    with pytest.raises(job_result_roots.JobResultRootError):
        job_result_roots.resolve_persisted_job_result_root(job)
    (tmp_path / 'absent').symlink_to(tmp_path / 'outside')
    with pytest.raises(job_result_roots.JobResultRootError, match='symlink'):
        job_result_roots.resolve_job_result_destination(job)


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'a//b', 'a/./b', 'a\\b', 'a\x00b'])
def test_directory_paths_are_closed(path):
    payload = dict(schema='bms.remote-result-manifest.v2', directories=[path], artifacts=[],
        job_id='job', attempt_id='attempt', exit_code=0, completed_at='2026-01-01T00:00:00Z',
        source_revision='a'*40, source_tree='b'*40, execution_envelope_sha256='c'*64)
    with pytest.raises(ValueError):
        RemoteResultManifest.model_validate(payload)


@pytest.mark.parametrize('workflow,mode,input_mode', [
    ('ont_basecall_dna', 'basecall_dna', 'pod5'), ('ont_basecall_rna', 'basecall_rna', 'pod5'),
    ('ont_fastq_qc', 'fastq_qc', 'fastq'), ('ont_plasmid_qc', 'plasmid_qc', 'bam'),
    ('ont_construct_screening', 'construct_screening', 'fastq'),
    ('wf_clone_validation', 'clone_validation', 'fastq'),
    ('ont_methylation_analysis', 'methylation_analysis', 'bam'),
    ('ont_pooled_reference_assignment', 'pooled_reference_assignment', 'fastq'),
])
def test_native_metadata_is_shared(workflow, mode, input_mode):
    params = {'ont_workflow_id': workflow, 'ont_input_mode': input_mode}
    job = SimpleNamespace(model_id='nanopore', mode=mode, params=params)
    metadata = selected_execution_metadata('nanopore', mode, params, f'workflows/{workflow}.nf')
    contract = ex.resolve_job_result_contract(job)
    assert json.loads(metadata.result_contract_json) == contract
    assert contract['model_id'] == 'nanopore' and contract['mode'] == mode
    assert contract['workflow_id'] == workflow
    if 'pooled' in workflow:
        assert contract['completion_path'] == 'pooled_review'
    assert not any(item.field == 'result_contract' for item in metadata.blockers)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['bytes', 'source', 'attempt', 'symlink', 'product-path', 'anchor-conflict', 'commit'])
async def test_return_integrity_and_transaction_failures_keep_prior_generation(
        store, tmp_path, monkeypatch, loopback_transport, fault):
    from fastapi import HTTPException
    async with store() as session:
        job = await session.get(Job, 'job')
        prepare_job(job, tmp_path, monkeypatch)
        destination = Path(job.output_dir)
        destination.mkdir()
        (destination / 'prior.txt').write_bytes(b'previous-canonical-result')
        worker = tmp_path / 'worker'
        output = worker / 'results'
        _, params = _write_terminal_product_tree(output)
        job.params = dict(job.params, **params)
        if fault == 'product-path':
            for relative in ['demux/demux_manifest.json', 'demux/per_barcode_units.json']:
                path = output / relative
                payload = json.loads(path.read_bytes())
                payload['units'][0]['bam_path'] = '/outside/bam'
                path.write_text(json.dumps(payload))
        stage(monkeypatch, output, job.remote_attempt_id, 'dorado_demux', ['demux'])
        _, _, status = seal(worker, output, job)
        if fault == 'anchor-conflict':
            job.provenance = {'ont_dorado_terminal_products': {'foreign': 'immutable'}}
        bind_contract(job, output)
        await session.commit()
        manifest, incoming = await transfer(loopback_transport, output, job, status)
        if fault in {'bytes', 'source', 'attempt', 'symlink'}:
            if fault == 'bytes':
                (incoming / 'basecall/dorado_preflight.json').write_bytes(b'changed')
            elif fault == 'source':
                job.execution_source_revision = 'f' * 40
            elif fault == 'attempt':
                job.remote_attempt_id = str(uuid4())
            else:
                (incoming / 'basecall/dorado_preflight.json').unlink()
                (incoming / 'basecall/dorado_preflight.json').symlink_to(output / 'basecall/dorado_preflight.json')
            with pytest.raises(ex.RemoteExecutionError):
                ex._verify_result_package(incoming, job, status)
        elif fault == 'product-path':
            with pytest.raises(HTTPException, match='confined'):
                await ex._finalize_pulled_results(session, job, status, manifest, incoming)
        elif fault == 'anchor-conflict':
            with pytest.raises(ngs.OntNgsCompletionError, match='immutable'):
                await ex._finalize_pulled_results(session, job, status, manifest, incoming)
        else:
            async def failed_commit():
                raise RuntimeError('injected SQLite commit failure')
            with monkeypatch.context() as transaction_fault:
                transaction_fault.setattr(session, 'commit', failed_commit)
                with pytest.raises(RuntimeError, match='SQLite commit failure'):
                    await ex._finalize_pulled_results(session, job, status, manifest, incoming)
            # Real publication happened, but the DB never committed its marker.
            assert not (destination / 'prior.txt').exists()
        await session.rollback()
        job = await session.get(Job, 'job', populate_existing=True)
        await ex._recover_result_generation(session, job)
        assert job.status == 'running' and job.remote_state == 'returning'
        assert (destination / 'prior.txt').read_bytes() == b'previous-canonical-result'
        assert 'remote_result_generation' not in job.provenance
        assert incoming.is_dir()  # Failure evidence retained for existing retry.
        assert await session.scalar(select(func.count(Design.id))) == 0


def test_file_directory_collisions_and_duplicates():
    payload = dict(schema='bms.remote-result-manifest.v2', directories=[], artifacts=[],
        job_id='job', attempt_id='attempt', exit_code=0, completed_at='2026-01-01T00:00:00Z',
        source_revision='a'*40, source_tree='b'*40, execution_envelope_sha256='c'*64)
    record = {'relative_path': 'file', 'size_bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest(), 'role': 'result'}
    for directories, artifacts in [(['file'], [record]), (['file/child'], [record]),
                                   (['same', 'same'], []), ([], [record, dict(record, relative_path='file/child')])]:
        with pytest.raises(ValueError):
            RemoteResultManifest.model_validate(dict(payload, directories=directories, artifacts=artifacts))


def test_signal_and_pooled_are_not_ordinary_fastq_contracts():
    params = dict(ont_workflow_id='ont_plasmid_qc', ont_input_mode='bam', run_fastq_qc=False,
                  source_move_source_id='move', source_external_move_registration_receipt_id='registration')
    assert ngs.ont_native_result_contract('nanopore', 'plasmid_qc', params)['completion_path'] == 'signal_alignment'
    params = dict(ont_workflow_id='ont_pooled_reference_assignment', ont_input_mode='fastq')
    assert ngs.ont_native_result_contract('nanopore', 'pooled_reference_assignment', params)['completion_path'] == 'pooled_review'


@pytest.mark.asyncio
async def test_v1_file_only_publication_keeps_exact_wire_identity(store, tmp_path, monkeypatch, loopback_transport):
    async with store() as session:
        job = await session.get(Job, 'job')
        prepare_job(job, tmp_path, monkeypatch)
        worker = tmp_path / 'worker'
        output = worker / 'results'
        output.mkdir(parents=True)
        (output / 'calls.bam').write_bytes(b'synthetic-native-byte-control')
        stage(monkeypatch, output, job.remote_attempt_id, 'basecall', ['calls.bam'])
        payload, _, status = seal(worker, output, job)
        payload.pop('directories')
        payload.pop('generation')
        payload['schema'] = 'bms.remote-result-manifest.v1'
        raw = json.dumps(payload, indent=2).encode() + b'\n'
        (output / 'result-manifest.json').write_bytes(raw)
        status = status.model_copy(update={'result_manifest_sha256': hashlib.sha256(raw).hexdigest()})
        bind_contract(job, output)
        await session.commit()
        manifest, incoming = await transfer(loopback_transport, output, job, status)
        assert await ex._finalize_pulled_results(session, job, status, manifest, incoming)
        await ex._recover_result_generation(session, job)
        await session.refresh(job)
        assert job.status == 'completed'
        assert (Path(job.child_output_dir) / 'result-manifest.json').read_bytes() == raw
        assert job.provenance['remote_result_generation']['manifest_sha256'] == hashlib.sha256(raw).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_state', ['failed', 'cancelled'])
async def test_real_diagnostic_collection_does_not_replace_canonical_results(
        store, tmp_path, monkeypatch, loopback_transport, terminal_state):
    from database import ExecutionTarget
    from fastapi import BackgroundTasks
    worker = tmp_path / 'worker'
    output = worker / 'results'
    async with store() as session:
        job = await session.get(Job, 'job')
        prepare_job(job, tmp_path, monkeypatch)
        canonical = Path(job.output_dir)
        canonical.mkdir()
        (canonical / 'successful-result.txt').write_bytes(b'prior-success')
        output.mkdir(parents=True)
        (output / 'diagnostic.log').write_bytes(b'native command failed honestly')
        (output / 'empty-output').mkdir()
        stage(monkeypatch, output, job.remote_attempt_id, 'native', ['empty-output'])
        _, raw, status = seal(worker, output, job, exit_code=17)
        status = status.model_copy(update={'state': terminal_state})
        bind_contract(job, output)
        job.status = job.queue_status = job.remote_state = terminal_state
        job.error_message = 'original native failure'
        job.provenance = dict(job.provenance, remote_execution_receipt=dict(
            job.provenance['remote_execution_receipt'], state=terminal_state, exit_code=17,
            result_manifest_sha256=status.result_manifest_sha256))
        (await session.get(ExecutionTarget, 'target')).leased_job_id = 'successor'
        await session.commit()
    async def provider_already_proved(*args, **kwargs):
        pass  # No provider/rental in this isolated filesystem/DB test.
    async def terminal_observation(*args):
        return status
    monkeypatch.setattr(ex, '_prove_pull_endpoint', provider_already_proved)
    monkeypatch.setattr(ex, 'remote_status', terminal_observation)
    monkeypatch.setattr(ex, '_connection_for_attempt', lambda *_args: (loopback_transport, str(worker)))
    tasks = BackgroundTasks()
    async with store() as session:
        await ex.request_remote_diagnostic_pull(session, await session.get(Job, 'job'), tasks)
    await tasks()  # Real collect_remote_results, archive publication and commit.
    shutil.rmtree(worker)
    async with store() as session:
        job = await session.get(Job, 'job')
        assert (job.status, job.queue_status, job.remote_state) == (terminal_state,) * 3
        assert job.error_message == 'original native failure'
        assert (canonical / 'successful-result.txt').read_bytes() == b'prior-success'
        assert not (canonical / 'diagnostic.log').exists()
        record = job.provenance['remote_diagnostics']
        assert record['state'] == 'returned', record
        archive = Path(record['output_dir'])
        assert (archive / 'diagnostic.log').read_bytes() == b'native command failed honestly'
        assert (archive / 'result-manifest.json').read_bytes() == raw
        assert (archive / 'empty-output').is_dir()
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'successor'
        again = BackgroundTasks()
        await ex.request_remote_diagnostic_pull(session, job, again)
        assert not again.tasks
