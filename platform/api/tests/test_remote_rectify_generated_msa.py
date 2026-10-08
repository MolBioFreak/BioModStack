"""R03 offline native service integration; HTTP and SSH are explicit doubles.

The real selected-plan builder, native PDB roster, provider/cache/ticket controller,
SQLite ledger, worker command handler and CLI hydrator are exercised. No science
inference or live provider acceptance is claimed.
"""
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
from component_runtime import ComponentRuntime, SourceIdentity, canonical_bytes, digest
from services import model_msa_handoff as handoff
from services import nextflow
from tools import bms_remote_worker as worker


@pytest.fixture
def context(tmp_path):
    params = dict(structure_validator='protenix_v2', protenix_use_msa=True,
                  msa_provider='colabfold_api', protenix_msa_backend='colabfold_api',
                  colabfold_pairing_mode='unpaired', colabfold_use_env=False,
                  protenix_seeds='7,9', run_frustrampnn=False)
    identity = SourceIdentity('a' * 40, 'b' * 40)
    plan = nextflow.build_selected_execution_plan(model_id='antibody_child', mode='validation_batch',
        entrypoint='workflows/antibody_child.nf', requested=canonical_bytes(params),
        effective=canonical_bytes(params), native_parameters=canonical_bytes(params), source_identity=identity)
    assert any(row.logical_id == 'protenix:generated_msa' for row in plan.metadata.external_services)
    assert any(row.relative_path == 'scripts/lib/component_adapter.py' for row in plan.metadata.dependencies)
    output = tmp_path / 'worker/results'
    output.mkdir(parents=True)
    value = dict(ledger_path=str(tmp_path / 'worker/component.sqlite'), artifact_root=str(output),
        attempt_id='attempt', root_job_id='root', target_id='remote-target', lease_id='lease',
        plan_sha256=plan.plan_sha256, source_identity={'revision': identity.revision, 'tree': identity.tree},
        execution_plan=plan.to_dict(), working_directory=str(ROOT))
    path = tmp_path / 'worker/context.json'
    path.write_text(json.dumps(value))
    return value, path


def runtime(context):
    value, path = context
    from scripts.lib.component_adapter import runtime_from_environment
    return runtime_from_environment(path)


def native_roster(tmp_path):
    # PDB fixture is a producer boundary, not a claimed predicted structure.
    path = tmp_path / 'candidate_0007.pdb'
    path.write_text(''.join(f'ATOM  {i:5d}  CA  {res:3s} {chain}{pos:4d}    {float(i):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C\n'
        for i, (chain, pos, res) in enumerate([('H', 1, 'ALA'), ('H', 2, 'CYS'),
        ('H', 3, 'ASP'), ('H', 4, 'GLU'), ('T', 1, 'PHE'), ('T', 2, 'GLY'),
        ('T', 3, 'HIS'), ('T', 4, 'ILE')], 1)) + 'END\n')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/prep_protenix_batch.py'),
        '--pdb_files', str(path), '--out_json', str(tmp_path / 'input.json'), '--seeds', '7,9',
        '--default_binder_chains', 'H', '--target_chains', 'T', '--epitope_residues', 'T:2'],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / 'input.json').read_text())
    assert [x['proteinChain']['sequence'] for x in payload[0]['sequences']] == ['ACDE', 'FGHI']
    assert payload[0]['modelSeeds'] == [7, 9]
    return payload


@pytest.fixture
def provider(tmp_path, monkeypatch):
    # Reuse explicitly labelled native HTTP response fixtures, not service mocks.
    spec = importlib.util.spec_from_file_location('r03_http_fixtures', ROOT / 'tests/test_msa_api_client.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    import biomodstack_msa_api as api
    from services import msa_provider_setup as setup
    transport = fixture.FixtureHTTP(*fixture.cf_success(sequences=['ACDE', 'FGHI'], use_env=False))
    client = api.MSAClient(config=api.ClientConfig(controller_root=tmp_path / 'controller-authority',
        poll_seconds=0, safe_attempts=1), transport=transport, sleep=lambda _: None)
    monkeypatch.setattr(api, 'prepare_msa', client.prepare_msa)
    monkeypatch.setattr(setup, 'cache_root', lambda: tmp_path / 'provider-cache')
    config = tmp_path / 'controller.json'
    config.write_text(json.dumps(dict(role='msa_controller',
        machine_id=Path('/etc/machine-id').read_text().strip(), qualified_single_egress=True,
        egress_identity='OFFLINE-FIXTURE-NOT-NETWORK-PROOF', state_dir=str(tmp_path / 'controller-state'))))
    monkeypatch.setattr(setup, 'controller_config_path', lambda: config)
    return transport, fixture


def native_waiter(context, tmp_path):
    value, path = context
    return subprocess.Popen([sys.executable, str(ROOT / 'scripts/prepare_protenix_msa.py'),
        '--input_json', str(tmp_path / 'input.json'), '--output_json', str(tmp_path / 'prepared.json'),
        '--out_dir', str(tmp_path / 'native-msa'), '--backend', 'colabfold_api',
        '--generated-service', 'protenix:generated_msa'],
        env=dict(os.environ, BMS_COMPONENT_CONTEXT=str(path)), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)


def pending(owner, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(str(process.communicate()))
        requests = owner.pending_external_services()
        if requests:
            return requests[0]
        time.sleep(.02)
    pytest.fail('native waiter did not journal its generated roster')


def deliver(owner, request, package, sha):
    destination = owner.artifact_root / 'external-services' / request['request_id'] / sha
    shutil.copytree(package, destination)
    return handoff.accept_generated_msa(owner, request['request_id'], sha)


def test_generated_cache_miss_relocation_native_consumer(context, tmp_path, provider):
    payload = native_roster(tmp_path)
    original = (tmp_path / 'input.json').read_bytes()
    owner = runtime(context)
    process = native_waiter(context, tmp_path)
    try:
        request = pending(owner, process)
        assert request['native_input'] == payload
        assert owner.external_service(request['request_id'])['result'] is None
        package = tmp_path / 'controller/prepared'
        sha = handoff.prepare_generated_msa(request, package)
        assert [x[0] for x in provider[0].calls] == ['POST', 'GET', 'GET']
        assert json.loads((package / 'msa-inputs.json').read_bytes())['provenance']['cache_hit'] is False
        # Relocation plus deletion of controller package: native only sees worker bytes.
        receipt = deliver(owner, request, package, sha)
        shutil.rmtree(package)
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, out + err
        prepared = json.loads((tmp_path / 'prepared.json').read_bytes())
        for wrapper in prepared[0]['sequences']:
            msa = Path(wrapper['proteinChain'].pop('unpairedMsaPath'))
            assert msa.is_relative_to(owner.artifact_root)
            assert msa.read_text().startswith('>101') or msa.read_text().startswith('>102')
        assert prepared == payload
        assert (tmp_path / 'input.json').read_bytes() == original
        # Fresh ledger connection resumes the exact completed input, no request reissue.
        reopened = runtime(context)
        assert reopened.pending_external_services() == ()
        assert reopened.submit_external_service('protenix:generated_msa', payload) == request['request_id']
        assert reopened.external_service(request['request_id'])['result'] == receipt
        # Controller crash before delivery simply reuses cache, never another POST.
        handoff.prepare_generated_msa(request, tmp_path / 'controller/replayed')
        assert len(provider[0].calls) == 3
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize('change', ['sequence', 'chain', 'settings', 'request', 'bytes'])
def test_generated_rejects_changed_identity(context, tmp_path, provider, change):
    payload = native_roster(tmp_path)
    owner = runtime(context)
    key = owner.submit_external_service('protenix:generated_msa', payload)
    request = owner.pending_external_services()[0]
    package = tmp_path / 'host'
    sha = handoff.prepare_generated_msa(request, package)
    manifest_path = package / 'msa-inputs.json'
    manifest = json.loads(manifest_path.read_bytes())
    if change == 'bytes':
        artifact = package / manifest['artifacts'][0]['path']
        artifact.write_bytes(artifact.read_bytes() + b'>extra\nACDE\n')
    else:
        if change == 'sequence': manifest['model_input'][0]['sequences'][0]['proteinChain']['sequence'] = 'AAAA'
        elif change == 'chain': manifest['model_input'][0]['sequences'][0]['proteinChain']['id'] = ['X']
        elif change == 'settings': manifest['settings']['colabfold_use_env'] = True
        else: manifest['external_service_request_id'] = '0' * 64
        manifest_path.write_text(json.dumps(manifest))
        # Even rehashed data cannot replace the request's sealed native identity.
        sha = handoff.digest(manifest_path.read_bytes())
    with pytest.raises(ValueError):
        deliver(owner, request, package, sha)
    assert owner.external_service(key)['result'] is None


@pytest.mark.parametrize('stop_kind', ['cancel', 'failure'])
def test_native_waiter_stops_without_publishing(context, tmp_path, stop_kind):
    native_roster(tmp_path)
    owner = runtime(context)
    process = native_waiter(context, tmp_path)
    try:
        request = pending(owner, process)
        if stop_kind == 'cancel': owner.request_cancel()
        else: owner.fail_external_service(request['request_id'])
        out, err = process.communicate(timeout=10)
        assert process.returncode != 0
        assert ('cancellation requested' if stop_kind == 'cancel' else 'controller MSA preparation failed') in err
        assert not (tmp_path / 'prepared.json').exists()
    finally:
        if process.poll() is None: process.kill()
        process.communicate(timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_kind', ['task', 'fence'])
async def test_controller_stop_joins_provider_operation(monkeypatch, tmp_path, stop_kind):
    import biomodstack_msa_api as api
    started, stopped = threading.Event(), threading.Event()
    lost = False
    def operation(*args):
        event = api._preparation_stop.get()
        started.set()
        try:
            assert event.wait(5)
            api._check_preparation_stop()
        finally:
            stopped.set()
    async def fence():
        if lost: raise asyncio.CancelledError()
    monkeypatch.setattr(handoff, 'prepare_generated_msa', operation)
    task = asyncio.create_task(handoff.prepare_generated_msa_on_controller({}, tmp_path, fence))
    assert await asyncio.to_thread(started.wait, 2)
    if stop_kind == 'task': task.cancel()
    else: lost = True
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 3)
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_actual_ticket_pending_and_recovery(context, tmp_path, provider):
    transport, fixture = provider
    import biomodstack_msa_api as api
    native = native_roster(tmp_path)
    owner = runtime(context)
    owner.submit_external_service('protenix:generated_msa', native)
    request = owner.pending_external_services()[0]
    transport.responses = [fixture.response({'id': 'retained-ticket', 'status': 'PENDING'}),
                           fixture.response(b'', status=503)]
    async def fence(): pass
    assert await handoff.prepare_generated_msa_on_controller(request, tmp_path / 'first', fence) is None
    assert owner.pending_external_services()[0] == request
    transport.responses = fixture.cf_success(sequences=['ACDE', 'FGHI'], use_env=False)[1:]
    sha = await handoff.prepare_generated_msa_on_controller(request, tmp_path / 'second', fence)
    deliver(owner, request, tmp_path / 'second', sha)
    assert [call[0] for call in transport.calls].count('POST') == 1
    assert len(transport.calls) == 4


def test_missing_plan_and_cancelled_delivery_rejected(context, tmp_path, provider):
    owner = runtime(context)
    native = native_roster(tmp_path)
    with pytest.raises(ValueError, match='selected plan'):
        owner.submit_external_service('boltz2:generated_msa', native)
    owner.submit_external_service('protenix:generated_msa', native)
    request = owner.pending_external_services()[0]
    sha = handoff.prepare_generated_msa(request, tmp_path / 'host')
    owner.request_cancel()
    with pytest.raises(RuntimeError, match='cancellation'):
        deliver(owner, request, tmp_path / 'host', sha)


def worker_attempt(context):
    value, path = context
    attempt = path.parent
    envelope = dict(job_id=value['root_job_id'], attempt_id=value['attempt_id'],
        output_directory=value['artifact_root'], working_directory=str(ROOT),
        environment={'BMS_COMPONENT_CONTEXT': str(path)}, files=[{
            'relative_path': 'inputs/component-context.json', 'sha256': worker.sha256_file(path),
            'size_bytes': path.stat().st_size}])
    worker.atomic_json(attempt / worker.ENVELOPE_FILE, envelope)
    state = worker.base_status(envelope, 'running')
    # Real process/start-tick evidence for the fixture owner, not a PID-only mock.
    state.update(supervisor_pid=os.getpid(), supervisor_start_ticks=worker.process_start_ticks(os.getpid()))
    worker.atomic_json(attempt / worker.STATUS_FILE, state)
    return attempt, dict(attempt_id=value['attempt_id'], expected_boot_id=worker.boot_id(),
                        lease_id=value['lease_id'], plan_sha256=value['plan_sha256'])


@pytest.mark.parametrize('field', ['attempt_id', 'expected_boot_id', 'lease_id', 'plan_sha256', 'context'])
def test_worker_service_rejects_foreign_authority(context, tmp_path, field):
    attempt, args = worker_attempt(context)
    if field == 'context':
        context[1].write_text(context[1].read_text() + ' ')
    else:
        args[field] = 'foreign'
    with pytest.raises(RuntimeError, match='authority|context differs'):
        worker.external_service_control(attempt, **args)


@pytest.mark.asyncio
@pytest.mark.parametrize('interference', ['none', 'settings', 'cancel', 'lease'])
async def test_controller_worker_native_delivery_with_real_database(context, tmp_path, provider, monkeypatch, interference):
    from datetime import datetime, timezone
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Base, Job, ExecutionTarget
    from services.remote_execution import executor as ex
    native = native_roster(tmp_path)
    owner = runtime(context)
    owner.submit_external_service('protenix:generated_msa', native)
    attempt, args = worker_attempt(context)
    value = context[0]
    authority = {k: value[k] for k in ('attempt_id', 'root_job_id', 'target_id', 'lease_id',
                                      'source_identity', 'plan_sha256', 'artifact_root')}
    authority['external_services'] = value['execution_plan']['metadata']['external_services']
    epoch = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'controller.sqlite'))
    async with engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(ExecutionTarget(id='remote-target', provider='vast', provider_instance_id='offline',
            leased_job_id='root', lease_acquired_at=epoch))
        db.add(Job(id='root', name='fixture', model_id='antibody_child', mode='validation_batch',
            status='running', queue_status='running', remote_state='running',
            execution_target_id='remote-target', remote_attempt_id='attempt', nextflow_run_id='remote:attempt',
            params={}, provenance={'remote_execution_receipt': {'component_context_identity': authority,
                    'lease_acquired_at': epoch.isoformat()}}))
        await db.commit()
    monkeypatch.setattr(ex, '_connection_for_attempt', lambda *a: (None, str(attempt)))
    monkeypatch.setattr(ex, '_worker_argv', lambda c, command, directory, *a: [command, '--attempt-dir', directory, *a])
    calls = []
    async def command(connection, argv, **kwargs):
        calls.append(argv[0])
        if argv[0] == 'mkdir':
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(stdout='')
        parsed = worker.parser().parse_args(argv)
        result = worker.external_service_control(Path(parsed.attempt_dir), attempt_id=parsed.attempt_id,
            expected_boot_id=parsed.expected_boot_id, lease_id=parsed.lease_id, plan_sha256=parsed.plan_sha256,
            request_id=getattr(parsed, 'request_id', None), manifest_sha256=getattr(parsed, 'manifest_sha256', None))
        if interference == 'settings' and argv[0] == 'external-service-status':
            result['requests'][0]['service']['settings_json']['colabfold_use_env'] = True
        return SimpleNamespace(stdout=json.dumps(result))
    async def transfer(connection, source, destination, **kwargs):
        from services.remote_execution.transport import _run_owned
        operation = await _run_owned([sys.executable, '-c',
            'import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True)',
            str(source), str(destination)], kwargs['ownership_directory'], timeout=10)
        assert operation.returncode == 0
        if interference in {'cancel', 'lease'}:
            async with sessions() as other:
                if interference == 'cancel':
                    (await other.get(Job, 'root')).queue_status = 'cancelling'
                else:
                    (await other.get(ExecutionTarget, 'remote-target')).lease_acquired_at = epoch.replace(second=1)
                await other.commit()
    monkeypatch.setattr(ex, 'get_data_root', lambda: tmp_path / 'host-data')
    monkeypatch.setattr(ex, 'run_remote', command)
    monkeypatch.setattr(ex, 'rsync_to_remote', transfer)
    try:
        async with sessions() as db:
            job = await db.get(Job, 'root')
            status = SimpleNamespace(state='running', boot_id=worker.boot_id())
            if interference == 'settings':
                with pytest.raises(ex.RemoteExecutionError, match='provider/settings'):
                    await ex._service_remote_external_inputs(db, job, status)
                assert provider[0].calls == []
            elif interference != 'none':
                with pytest.raises(asyncio.CancelledError):
                    await ex._service_remote_external_inputs(db, job, status)
            else:
                await ex._service_remote_external_inputs(db, job, status)
                assert calls == ['external-service-status', 'mkdir', 'external-service-deliver']
                process = native_waiter(context, tmp_path)
                out, err = process.communicate(timeout=10)
                assert process.returncode == 0, out + err
                assert json.loads((tmp_path / 'prepared.json').read_bytes())[0]['modelSeeds'] == [7, 9]
                # Repeated read after controller restart cannot repeat provider search.
                await ex._service_remote_external_inputs(db, job, status)
                assert calls[-1] == 'external-service-status'
                assert len(provider[0].calls) == 3
        if interference != 'none':
            assert owner.pending_external_services()
            assert 'external-service-deliver' not in calls
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ordinary_jobs_make_no_service_io(monkeypatch):
    from services.remote_execution import executor as ex
    from unittest.mock import AsyncMock
    session = SimpleNamespace(get=AsyncMock(side_effect=AssertionError('unexpected database read')))
    job = SimpleNamespace(provenance={'remote_execution_receipt': {'component_context_identity': {
        'external_services': []}}})
    await ex._service_remote_external_inputs(session, job, SimpleNamespace(state='running'))
    session.get.assert_not_called()


def test_compiler_selected_native_generated_service(context, tmp_path, monkeypatch, provider):
    value, _ = context
    params = value['execution_plan']['effective_json']
    native = native_roster(tmp_path)
    params = dict(params, pdb_paths=str(tmp_path / 'candidate_0007.pdb'))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    invocation = nextflow.compile_nextflow_invocation('antibody_child', 'validation_batch', params,
        str(tmp_path / 'compiled-results'), 'root', source_identity=SourceIdentity(**value['source_identity']),
        execution_context=nextflow.NativeCompilerExecutionContext(0, (0,), 'cpu'))
    assert invocation.entrypoint == 'workflows/antibody_child.nf'
    assert nextflow.needs_component_runtime(invocation)
    services = invocation.execution_plan.metadata.external_services
    selected = [row for row in services if handoff.generated_msa_service_supported(row)]
    assert len(selected) == 1
    assert json.loads(selected[0].settings_json)['colabfold_use_env'] is False
    assert '--protenix_use_msa' in invocation.command
    assert invocation.command[invocation.command.index('--protenix_use_msa') + 1] == 'true'
    root = tmp_path / 'compiled-worker'
    root.mkdir()
    owner = ComponentRuntime(tmp_path / 'compiled.sqlite', artifact_root=root,
        attempt_id='compiled', root_job_id='root', target_id='remote-target', lease_id='lease',
        source_identity=value['source_identity'], plan_sha256=invocation.execution_plan.plan_sha256,
        execution_plan=invocation.execution_plan.to_dict())
    owner.submit_external_service('protenix:generated_msa', native)
    request = owner.pending_external_services()[0]
    sha = handoff.prepare_generated_msa(request, tmp_path / 'compiled-package')
    deliver(owner, request, tmp_path / 'compiled-package', sha)


@pytest.mark.asyncio
async def test_cancel_second_controller_operation_while_first_holds_flock(context, tmp_path, provider, monkeypatch):
    """U04: real independent owner holds the existing controller submission.lock."""
    import multiprocessing
    from biomodstack_msa_controller import prepare
    from services.msa_provider_setup import controller_config_path
    config = controller_config_path()
    parent, child = multiprocessing.get_context('fork').Pipe()
    def first_operation():
        def held():
            child.send('held')
            assert child.recv() == 'release'
            return {'first': 'completed'}
        prepare(config, {'fixture-owner': 'first'}, held, resume_same_request=True)
        child.send('completed')
    first = multiprocessing.get_context('fork').Process(target=first_operation)
    first.start()
    task = None
    try:
        assert await asyncio.to_thread(parent.poll, 5)
        assert parent.recv() == 'held'
        state_dir = Path(json.loads(config.read_bytes())['state_dir'])
        original = (state_dir / 'active.json').read_bytes()
        owner = runtime(context)
        owner.submit_external_service('protenix:generated_msa', native_roster(tmp_path))
        request = owner.pending_external_services()[0]
        async def fence(): pass
        import biomodstack_msa_controller as controller
        entered = threading.Event()
        flock = controller.fcntl.flock
        def observe_flock(fd, operation):
            if getattr(fd, 'name', '').endswith('submission.lock'):
                entered.set()
            return flock(fd, operation)
        monkeypatch.setattr(controller.fcntl, 'flock', observe_flock)
        task = asyncio.create_task(handoff.prepare_generated_msa_on_controller(request, tmp_path / 'queued', fence))
        assert await asyncio.to_thread(entered.wait, 3), 'second operation never reached controller flock'
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        stopped_while_first_held = bool(done)
        assert first.is_alive()
        assert (state_dir / 'active.json').read_bytes() == original
        assert provider[0].calls == []
        parent.send('release')
        assert await asyncio.to_thread(parent.poll, 5)
        assert parent.recv() == 'completed'
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert stopped_while_first_held, 'cancelled controller thread blocked indefinitely behind unrelated flock'
        assert not (state_dir / 'active.json').exists()
    finally:
        if first.is_alive():
            try: parent.send('release')
            except BrokenPipeError: pass
        await asyncio.to_thread(first.join, 5)
        if first.is_alive(): first.kill(); first.join()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        parent.close(); child.close()


@pytest.mark.asyncio
async def test_service_transfer_cancellation_joins_actual_process(tmp_path):
    from services.remote_execution import transport
    marker = tmp_path / 'transport-pid'
    writes = tmp_path / 'transport-writes'
    program = ('import os,signal,time; from pathlib import Path; '
               'signal.signal(signal.SIGTERM, lambda *_: None); '
               f'Path({str(marker)!r}).write_text(str(os.getpid()))\n'
               'while True:\n'
               f' with Path({str(writes)!r}).open("a") as stream: stream.write("x")\n'
               ' time.sleep(.02)\n')
    from services.remote_execution.result_generation import begin_transfer, prepare_transfer, transfer_marker
    ownership = tmp_path / 'owned-transfer'
    begin_transfer(ownership)
    operation = asyncio.create_task(transport._run_owned([sys.executable, '-c', program], ownership, timeout=30))
    lost = False
    async def fence():
        if lost: raise asyncio.CancelledError()
    task = asyncio.create_task(handoff.await_controller_service_operation(operation, fence))
    deadline = asyncio.get_running_loop().time() + 5
    while not marker.exists() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(.02)
    assert marker.exists()
    pid = int(marker.read_text())
    lost = True
    # A second task cancellation while transport joins must not detach cleanup.
    await asyncio.sleep(.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert operation.done()
    assert not Path(f'/proc/{pid}').exists()
    size = writes.stat().st_size
    await asyncio.sleep(.1)
    assert writes.stat().st_size == size
    assert json.loads(transfer_marker(ownership).read_bytes())['phase'] == 'quiescent'
    prepare_transfer(ownership)
    assert not transfer_marker(ownership).exists()


@pytest.mark.asyncio
async def test_msa_upload_selects_existing_durable_transfer_owner(tmp_path, monkeypatch):
    from services.remote_execution import transport
    from unittest.mock import AsyncMock
    owner = tmp_path / 'ownership'
    source = tmp_path / 'source'
    source.mkdir()
    owned = AsyncMock(return_value=transport.CommandResult(0, '', ''))
    unowned = AsyncMock(side_effect=AssertionError('upload bypassed durable transfer owner'))
    monkeypatch.setattr(transport, '_run_owned', owned)
    monkeypatch.setattr(transport, '_run', unowned)
    monkeypatch.setattr(transport, '_ssh_base', lambda c: ['ssh', 'fixture-host'])
    connection = SimpleNamespace(username='fixture', host='fixture.invalid', provision_operation_id=None)
    await transport.rsync_to_remote(connection, source, '/fixture/destination', delete=False, ownership_directory=owner)
    assert owned.call_args.args[1] == owner
    argv = owned.call_args.args[0]
    assert '--delete' not in argv and argv[-1] == 'fixture@fixture.invalid:/fixture/destination'
    unowned.assert_not_called()
