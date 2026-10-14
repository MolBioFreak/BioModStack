"""Shared launcher/managed MD task inputs through real child compilation."""
import copy
import json
from pathlib import Path

import pytest

from component_runtime import ComponentRequest
from services import nextflow
from scripts.lib.component_adapter import native_resource_config
from scripts.lib import portable_inputs
from scripts.bms_md.contract import prepare_verified_worker_inputs
from test_remote_bundle_path_gaps import roots
from test_md_native_orchestration import _native, _context


def _executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 97\n')
    path.chmod(0o755)
    return path


def test_nextflow_bound_launcher_and_legacy_precedence(tmp_path, monkeypatch):
    home = tmp_path/'home'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(nextflow.shutil, 'which', lambda name: None)
    for key in ('BMS_NEXTFLOW_BIN', 'BMS_NEXTFLOW_EXECUTABLE'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match='No executable Nextflow'):
        nextflow.resolve_nextflow_executable()
    managed = _executable(home/'.local/lib/nextflow'/nextflow.resolve_nextflow_version()/'nextflow')
    assert nextflow.resolve_nextflow_executable() == str(managed)
    bound = _executable(tmp_path/'release/bin/bms-nextflow')
    monkeypatch.setenv('BMS_NEXTFLOW_EXECUTABLE', str(bound))
    assert nextflow.resolve_nextflow_executable() == str(bound)
    legacy = _executable(tmp_path/'legacy')
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', str(legacy))
    assert nextflow.resolve_nextflow_executable() == str(legacy)
    legacy.unlink()
    with pytest.raises(RuntimeError, match='BMS_NEXTFLOW_BIN is not an executable'):
        nextflow.resolve_nextflow_executable()
    monkeypatch.delenv('BMS_NEXTFLOW_BIN')
    bound.chmod(0o644)
    with pytest.raises(RuntimeError, match='BMS_NEXTFLOW_EXECUTABLE is not an executable'):
        nextflow.resolve_nextflow_executable()
    monkeypatch.delenv('BMS_NEXTFLOW_EXECUTABLE')
    managed.unlink()
    monkeypatch.setattr(nextflow.shutil, 'which', lambda name: str(legacy))
    _executable(legacy)
    assert nextflow.resolve_nextflow_executable() == str(legacy)


@pytest.mark.parametrize('bad', ['sibling', 'escape', 'file_link', 'parent_link', 'oversize', 'array', 'relative'])
def test_work_config_keeps_bounded_input_checks(roots, tmp_path, monkeypatch, bad):
    work = tmp_path/'attempt/work'; work.mkdir(parents=True)
    monkeypatch.setenv('BMS_WORK', str(work))
    config = work/'normalized.json'; config.write_text('{}')
    if bad == 'sibling':
        config = work.parent/'outside.json'; config.write_text('{}')
    elif bad == 'escape':
        (work.parent/'outside.json').write_text('{}'); config = work/'../outside.json'
    elif bad == 'file_link':
        target = config; config = work/'link.json'; config.symlink_to(target)
    elif bad == 'parent_link':
        directory = work/'real'; directory.mkdir(); (directory/'config.json').write_text('{}')
        (work/'link').symlink_to(directory, target_is_directory=True); config = work/'link/config.json'
    elif bad == 'oversize':
        with config.open('wb') as stream: stream.truncate(16*1024*1024+1)
    elif bad == 'array':
        config.write_text('[]')
    elif bad == 'relative':
        config = Path('normalized.json')
    with pytest.raises(ValueError, match='bounded managed input|must be an object'):
        nextflow._native_plan_metadata_settings('molecular_dynamics', {
            'md_job_config': str(config), 'work_dir': str(config.parent)})


def test_work_root_is_md_only_and_server_owned(roots, tmp_path, monkeypatch):
    work = tmp_path/'attempt/work'; work.mkdir(parents=True)
    config = work/'normalized.json'; config.write_text('{}')
    monkeypatch.setenv('BMS_WORK', str(work))
    assert nextflow._native_plan_metadata_settings('molecular_dynamics', {'md_job_config': str(config)})['md_config'] == {}
    with pytest.raises(ValueError, match='bounded managed input'):
        nextflow._native_plan_metadata_settings('conformational_mapping', {'cm_request_path': str(config)})
    monkeypatch.setenv('BMS_WORK', str(tmp_path/'different-work'))
    with pytest.raises(ValueError, match='bounded managed input'):
        nextflow._native_plan_metadata_settings('molecular_dynamics', {'md_job_config': str(config), 'work_dir': str(work)})


def test_generated_work_config_complete_child_and_portable_consumer(roots, tmp_path, monkeypatch):
    # Real native request, materializer, root metadata and component admission.
    # Only image identity/scientific fixture bytes are inert (_native).
    for key, value in {'BMS_DATA': roots['data'], 'BMS_WEIGHTS': roots['weights'],
                       'BMS_CONTAINER_DIR': roots['containers'], 'BMS_WORK': tmp_path/'attempt/work',
                       'BMS_MSA_CACHE': roots['data']/'msa-cache'}.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv('BMS_NEXTFLOW_BIN', raising=False)
    launcher = _executable(tmp_path/'release/bin/bms-nextflow')
    monkeypatch.setenv('BMS_NEXTFLOW_EXECUTABLE', str(launcher))
    out, params = _native(roots, monkeypatch)
    parent, context, _ = _context(out, params, monkeypatch)
    original_config = Path(parent.native_parameters['md_job_config'])
    config = tmp_path/'attempt/work/ab/task/normalized_config.json'
    config.parent.mkdir(parents=True)
    document = json.loads(original_config.read_text())
    document.pop('windows', None)
    config.write_text(json.dumps(document))
    before = config.read_bytes()
    requested = dict(md_job_config=str(config), md_preparation_bundle=str(config.parent/'preparation_bundle'),
        md_engine='gromacs', md_replica_index=0, md_replica_count=2, md_replica_seed=document['random_seed'])
    request = ComponentRequest.capture(parent_job_id='parent', stage='md_replica', child_key='0', required=True,
        payload=dict(model_id='molecular_dynamics', mode='replica', name='replica', parent_job_id='parent',
                     child_stage='md_replica', pinned_gpu=0, params=requested))
    original_request = copy.deepcopy(request.payload)
    child = out/'components'/request.component_id.replace(':', '-')
    context = dict(context, child_id=request.component_id, child_output_dir=str(child),
                   child_working_directory=str(tmp_path/'component-work'))
    invocation = nextflow.compile_component_nextflow_invocation(request, context)
    snapshot = nextflow.component_native_parent_snapshot(invocation, request, context)
    invocation.materialize_inputs(child)
    assert invocation.command[0] == str(launcher)
    assert invocation.command[invocation.command.index('-w')+1] == str(tmp_path/'component-work/work')
    assert invocation.native_parameters['gpu_id'] == 0
    assert snapshot['params']['md_job_config'] == str(config)
    assert request.payload == original_request and config.read_bytes() == before
    assert any(d.selector == 'md_gromacs_container' for d in invocation.execution_plan.dependencies)
    resource_config = native_resource_config(invocation.execution_plan.to_dict(), context['resources'],
                                            str(tmp_path/'compute.lock'), nextflow.PROJECT_ROOT)
    assert 'cpus = 8' in resource_config
    refs = portable_inputs.discover_native_input_references('molecular_dynamics', 'replica',
        {'md_job_config': str(config)}, invocation.generated_inputs, output_dir=child,
        allowed_roots=[*roots.values(), tmp_path/'attempt/work'])
    received = tmp_path/'receiver'; received.mkdir()
    import shutil
    bindings, copied = [], {}
    for ref in refs:
        source = ref['source_path']
        if source not in copied:
            target = received/f'{len(copied)}-{Path(source).name}'
            shutil.copyfile(source, target); copied[source] = target
        bindings.append({'reference': ref, 'path': str(copied[source])})
    binding = received/'bindings.json'
    binding.write_text(json.dumps({'schema': portable_inputs.SCHEMA, 'roots': [str(received)], 'bindings': bindings}))
    monkeypatch.setenv(portable_inputs.ENV, str(binding))
    archived = portable_inputs.resolve_input_path(str(config))
    archived_bytes = archived.read_bytes()
    shutil.rmtree(roots['inputs']); shutil.rmtree(out); shutil.rmtree(tmp_path/'attempt')
    consumed = prepare_verified_worker_inputs(archived, received/'private')
    assert consumed['stages'] == document['stages']
    assert consumed['random_seed'] == document['random_seed']
    assert archived.read_bytes() == archived_bytes
    assert Path(consumed['input']['coordinates']).is_file()
    # Same portable owner still rejects ambiguous bindings and modified bytes.
    target = copied[next(ref['source_path'] for ref in refs if ref['role'] == 'coordinates')]
    payload = json.loads(binding.read_text())
    conflicting = copy.deepcopy(next(row for row in payload['bindings'] if row['reference']['role'] == 'coordinates'))
    conflicting['reference']['sha256'] = '0'*64
    payload['bindings'].append(conflicting)
    binding.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='Ambiguous portable input binding'):
        prepare_verified_worker_inputs(archived, received/'ambiguous')
    payload['bindings'].pop(); binding.write_text(json.dumps(payload))
    target.write_bytes(b'changed')
    with pytest.raises(ValueError, match='digest/size mismatch'):
        prepare_verified_worker_inputs(archived, received/'corrupt')
