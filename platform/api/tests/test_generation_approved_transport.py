"""CPU-only approval/admission/bundle tests; no native model or worker runs.

Archive packaging uses the checkout's committed source, not a candidate release.
Runtime blobs are explicitly inert transport fixtures, never install acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

import pytest
from fastapi import HTTPException, BackgroundTasks
from sqlalchemy import select
from database import Job, ExecutionTarget
from component_runtime import SourceIdentity

import routers.jobs as jobs
from schemas import JobCreate
from services.nextflow import compile_job_nextflow_invocation
from services.remote_execution import bundle
from test_boltzgen_generation_launch import admission, target  # noqa: F401
from test_remote_bundle_runtime_gaps import bundle_assignment_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode,path_key', [
    ('ppiflow', 'protein_binder', 'ppiflow_generation_request'),
    ('ppiflow', 'antibody_binder', 'ppiflow_generation_request'),
    ('ppiflow', 'nanobody_binder', 'ppiflow_generation_request'),
    ('boltzgen', 'protein_binder', 'boltzgen_yaml_config'),
    ('boltzgen', 'peptide_binder', 'boltzgen_yaml_config'),
    ('boltzgen', 'nanobody_binder', 'boltzgen_yaml_config'),
])
async def test_approved_prepared_directory_reaches_real_bundle(
        model, mode, path_key, target, admission, tmp_path, monkeypatch):
    session, tasks = admission, BackgroundTasks()
    roots = {'inputs': target.parent, 'results': tmp_path / 'results'}
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: roots['inputs'])
    capabilities = {
        'critical_runtime_binding': {'paths': {'python': '/fixture/runtime/python/bin/python',
            'nextflow': '/fixture/runtime/bin/nextflow'}, 'environment': {}},
    }
    capabilities['gpu_count'] = 1
    selected_target = ExecutionTarget(id='vast:fixture', provider='vast', provider_instance_id='fixture',
        active=True, state='ready', capabilities=capabilities,
        provider_metadata={'inventory': {'checked_at': datetime.utcnow().isoformat(),
            'status': 'complete', 'present': True, 'running': True}})
    session.add(selected_target)
    await session.commit()
    identity = SourceIdentity.from_checkout(Path(__file__).resolve().parents[3])
    # Existing dirty-source admission is outside this transport-only fixture.
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: (identity.revision, identity.tree))
    from test_boltzgen_runtime_regressions import _write_pdb
    framework = target.parent / 'framework.pdb'
    _write_pdb(framework, 'H', [1, 2, 3])
    light = target.parent / 'light.pdb'
    _write_pdb(light, 'L', [1, 2, 3])
    framework.write_text('\n'.join(line for path in (framework, light)
        for line in path.read_text().splitlines() if line.startswith('ATOM')) + '\nEND\n')
    light.unlink()
    if model == 'ppiflow':
        params = {'target_pdb': str(target), 'samples_per_target': 1, 'self_condition': False}
        if mode == 'protein_binder':
            params.update(target_chain='A', binder_chain='B')
        else:
            params.update(framework_pdb=str(framework), antigen_chain='A',
                          heavy_chain='H', specified_hotspots='A1')
            if mode == 'antibody_binder':
                params['light_chain'] = 'L'
    else:
        params = {'target_pdb': str(target), 'target_chains': 'A', 'boltzgen_num_designs': 1}
        if mode == 'nanobody_binder':
            params['boltzgen_nanobody_scaffold_specs'] = [{
                'name': 'fixture', 'spec': {'path': str(framework),
                    'include': [{'chain': {'id': 'H'}}],
                    'design': [{'chain': {'id': 'H', 'res_index': '1..2'}}]}}]
        else:
            params.update(boltzgen_scaffold_path=str(framework), boltzgen_scaffold_chain='H',
                          boltzgen_scaffold_design_ranges='1..2')
    raw = JobCreate(name='transport fixture', model_id=model, mode=mode,
                    params=params, execution_target_id=selected_target.id)
    with pytest.raises(HTTPException) as review:
        await jobs._create_job(raw, tasks, session)
    assert review.value.status_code == 409
    assert review.value.detail['code'] == 'remote_prepared_job_review_required'
    assert not list(await session.scalars(select(Job.id)))
    prepared_body = review.value.detail['job_request']
    request = JobCreate.model_validate(prepared_body)
    reviewed_directory = Path(request.params[path_key])
    initial_files = {p.relative_to(reviewed_directory).as_posix(): p.read_bytes()
                     for p in reviewed_directory.rglob('*') if p.is_file()}
    assert initial_files
    target.unlink()  # Launch must never reopen an original after preparation.
    framework.unlink()

    preview = jobs._execution_plan_preview(request)
    assert preview['admissible'], preview['blockers']
    approved = JobCreate.model_validate(prepared_body | {
        'execution_plan_approval': preview['approval_digest'],
    })
    response = await jobs._create_job(approved, tasks, session)
    job = await session.get(Job, response.id)
    job.provenance = {**job.provenance, **bundle_assignment_fixture()}
    assert job.params[path_key] == str(reviewed_directory)
    assert job.provenance['execution_plan_approval']['approval_digest'] == preview['approval_digest']
    assert job.provenance['execution_plan_approval']['input_request']['params'][path_key] == str(reviewed_directory)

    invocation = compile_job_nextflow_invocation(job, job.params, job.output_dir)
    invocation.materialize_inputs(job.output_dir)
    # Use the actual launch-time plan, not a manufactured preview-plan rebind.
    plan = invocation.execution_plan
    assert plan.bind_invocation(invocation).effective_json == plan.effective_json
    assert json.loads(plan.effective_json)[path_key] == str(reviewed_directory)

    monkeypatch.setattr(bundle, 'get_inputs_dir', lambda: roots['inputs'])
    monkeypatch.setattr(bundle, 'get_results_dir', lambda: roots['results'])
    monkeypatch.setattr(bundle, 'get_data_root', lambda: tmp_path / 'data')
    runtime_files = []
    prefixes = {'image': 'containers', 'weights': 'weights', 'runtime_data': 'data',
                'reference_database': 'data', 'database': 'data'}
    for dependency in plan.dependencies:
        if dependency.kind not in prefixes or not dependency.relative_path:
            continue
        relative = prefixes[dependency.kind] + '/' + dependency.relative_path
        local = tmp_path / 'runtime-fixtures' / relative
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b'INERT TRANSPORT TEST FIXTURE; NOT A NATIVE MODEL\n')
        runtime_files.append((local, relative))
    # Runtime inventory/provisioning has separate tests; do not launch these bytes.
    monkeypatch.setattr(bundle, '_runtime_assets', lambda *_, **__: runtime_files)
    inventory_checks = []
    verify_approved = bundle.verify_approved_native_inputs

    def observe_verification(job, runtime_references, input_hashes=None):
        if input_hashes is not None:
            inventory_checks.append(dict(input_hashes))
        return verify_approved(job, runtime_references, input_hashes)

    monkeypatch.setattr(bundle, 'verify_approved_native_inputs', observe_verification)
    dispatch = bundle.prepare_remote_bundle(job=job, target=selected_target,
        command=list(invocation.command), native_invocation=invocation)
    expected_members = job.provenance['execution_plan_approval']['input_identities']
    assert inventory_checks and expected_members
    for member in expected_members:
        # No absent-key skip may conceal a copy/approval identity mismatch.
        assert inventory_checks[-1][member['source_path']] == (member['sha256'], member['size_bytes'])
    relocated = dispatch.envelope.path_map[str(reviewed_directory)]
    assert relocated.startswith(dispatch.remote_attempt_dir + '/bundle/inputs/')
    assert str(target) not in dispatch.envelope.path_map
    assert str(framework) not in dispatch.envelope.path_map
    assert {p.relative_to(reviewed_directory).as_posix(): p.read_bytes()
            for p in reviewed_directory.rglob('*') if p.is_file()} == initial_files
    assert dispatch.envelope.expected_result_contract == json.loads(plan.metadata.result_contract_json)
    assert dispatch.envelope.command
    context_transfer = next((item for item in dispatch.input_transfers
                             if item.source.name == 'component-context.json'), None)
    if context_transfer is not None:
        context = json.loads(context_transfer.source.read_text())
        assert context['plan_sha256'] == plan.plan_sha256
        command = context['root_command']
    else:
        command = dispatch.envelope.command
    assert relocated in command
    assert str(reviewed_directory) not in command
    staged_source = next(reviewed_directory.rglob('*.pdb'))
    original_source = staged_source.read_bytes()
    staged_source.write_bytes(original_source + b'REMARK changed input\n')
    with pytest.raises(bundle.RemoteBundleError):
        verify_approved(job, {})

    if model == 'ppiflow':
        # A self-consistent model snapshot must still match saved approval,
        # not merely its mutable internal request hashes.
        import hashlib
        from services.ppiflow_generation import read_prepared_ppiflow_generation_request
        request_file = reviewed_directory / 'request.json'
        original_request = request_file.read_bytes()
        payload = json.loads(original_request)
        changed_binding = next(row for row in payload['source_bindings']
                               if reviewed_directory / row['path'] == staged_source)
        changed_binding.update(sha256=hashlib.sha256(staged_source.read_bytes()).hexdigest(),
                               size=staged_source.stat().st_size)
        request_file.write_text(json.dumps(payload))
        assert read_prepared_ppiflow_generation_request(mode, job.params, reviewed_directory)
        with pytest.raises(bundle.RemoteBundleError, match='bytes or membership changed'):
            verify_approved(job, {})
        request_file.write_bytes(original_request)

    staged_source.write_bytes(original_source)
    verify_approved(job, {})
    additional = reviewed_directory / 'unreviewed.pdb'
    additional.write_bytes(b'REMARK unreviewed directory member\n')
    with pytest.raises(bundle.RemoteBundleError, match='bytes or membership changed'):
        verify_approved(job, {})
    additional.unlink()
    staged_source.unlink()
    with pytest.raises(bundle.RemoteBundleError):
        verify_approved(job, {})
    staged_source.write_bytes(original_source)
    verify_approved(job, {})
    changed_inventory = dict(inventory_checks[-1])
    member = expected_members[0]
    changed_inventory[member['source_path']] = (member['sha256'], member['size_bytes'] + 1)
    with pytest.raises(bundle.RemoteBundleError, match='changed during bundle inventory'):
        verify_approved(job, {}, changed_inventory)


def test_legacy_ppiflow_result_contract_is_not_initial_generation():
    from services.ppiflow_generation import generation_result_contract
    assert generation_result_contract('generator_backbone_refine') is None
    job = Job(model_id='ppiflow', mode='generator_backbone_refine', params={}, provenance={})
    assert bundle.resolve_job_result_contract(job)['analysis_contract_id'] == 'ppiflow_maturation_v1'
