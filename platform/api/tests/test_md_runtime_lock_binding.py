"""Managed MD lock resolution and real bundle relocation; no engine execution."""
import hashlib
import json

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from services import nextflow
from services.remote_execution import bundle, cache
from services.remote_execution.contracts import WorkflowProvisionSelection
from test_md_typed_launch import _Catalog, _UnusedSession, _intent_payload
from test_remote_bundle_runtime_gaps import package, compile_native


@pytest.fixture
def md_package(package, monkeypatch):
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    roots, release, job, target, _ = package
    job.model_id, job.mode = 'molecular_dynamics', 'simulate'
    job.params = {'md_config': {'engine': 'gromacs', 'replicas': 1}}
    for name in ('md-preparation-v1.sif', 'gromacs-md-2025.3.sif', 'md-analysis-1.0.0.sif'):
        (roots['containers'] / name).write_bytes(b'inert runtime fixture; never executed')
    lock = roots['data'] / 'md-preparation/env-v1-explicit.txt'
    lock.parent.mkdir()
    lock.write_bytes(b'@EXPLICIT\nhttps://fixture.invalid/ambertools-24.8.conda#exact\n')
    return roots, job, target, lock


@pytest.mark.asyncio
async def test_configured_md_preview_resolves_real_runtime_leaves(md_package, monkeypatch):
    from routers import molecular_dynamics as md
    roots, job, target, lock = md_package
    # Source archive fixture supplies actual selected support-tool bytes.
    compile_native(job, job.params)
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    monkeypatch.setattr(md, 'get_chemistry_catalog', lambda: _Catalog())
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a' * 40, 'b' * 40))
    selection = WorkflowProvisionSelection.model_validate({'kind': 'workflow', 'workflow_request': {
        'workflow_type': 'molecular_dynamics', 'request': {
            'schema_version': 'bms.md.launch-preview-request.v1', 'intent': _intent_payload()}}})
    plan = await nextflow.compile_native_workflow_provision_request(
        selection.workflow_request, Request({'type': 'http', 'headers': []}), _UnusedSession())
    target = SimpleNamespace(id=target.id, host='fixture', port=22, username='root',
        remote_root=target.remote_root, host_key_sha256='e' * 64)
    preview, entries = cache.independent_preview(selection, target, compiled_plan=plan)
    assert not preview.blockers
    assert preview.plan_sha256 == plan.plan_sha256
    assert json.loads(plan.native_parameters_json)['md_preparation_runtime_lock'] == str(lock)
    records, _ = cache.workflow_plan(selection, compiled_plan=plan)
    selected = next(row for row in records if row.remote_destination == 'data/md-preparation/env-v1-explicit.txt')
    assert selected.source == lock
    assert selected.sha256 == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert selected.size_bytes == lock.stat().st_size
    assert 'md_preparation_runtime_lock' not in json.loads(plan.requested_json)
    lock.unlink()
    unavailable, _ = cache.independent_preview(selection, target, compiled_plan=plan)
    assert any(reason.startswith('host_asset_unavailable:') for reason in unavailable.blockers)


def test_md_compiler_lock_relocates_through_actual_bundle(md_package):
    roots, job, target, lock = md_package
    original = lock.read_bytes()
    # Caller paths cannot override the server-owned placement.
    params = dict(job.params, md_preparation_runtime_lock='/untrusted/operator.lock')
    command = compile_native(job, params)
    assert command[command.index('--md_preparation_runtime_lock') + 1] == str(lock)
    assert job.native_invocation.execution_plan.complete, job.native_invocation.execution_plan.blockers
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
        native_invocation=job.native_invocation)
    context_transfer = next(t for t in prepared.input_transfers if t.source.name == 'component-context.json')
    context = json.loads(context_transfer.source.read_bytes())
    argv = context['root_command']
    relocated = argv[argv.index('--md_preparation_runtime_lock') + 1]
    transfer = next(t for t in prepared.runtime_transfers if t.origin == lock)
    assert relocated == transfer.remote_destination
    assert relocated.endswith('/data/md-preparation/env-v1-explicit.txt')
    assert str(lock) not in ' '.join(argv)
    assert not any(t.source == lock for t in prepared.input_transfers)
    assert transfer.source.read_bytes() == original == lock.read_bytes()
    record = next(r for r in prepared.envelope.files
                  if r.relative_path == 'runtime/data/md-preparation/env-v1-explicit.txt')
    assert record.sha256 == hashlib.sha256(original).hexdigest()


def test_md_lock_retains_managed_containment(md_package, tmp_path):
    roots, job, target, lock = md_package
    compile_native(job, job.params)
    outside = tmp_path / 'outside.lock'
    outside.write_bytes(lock.read_bytes())
    lock.unlink()
    lock.symlink_to(outside)
    with pytest.raises(bundle.RemoteBundleError, match='escapes managed storage'):
        bundle._runtime_assets(job.model_id, job.mode, job.native_invocation.native_parameters,
            native_invocation=job.native_invocation)
