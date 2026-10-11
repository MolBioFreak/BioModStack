"""Placement belongs to the adapter, never the immutable child compiler."""
import copy
from pathlib import Path

import pytest

from component_runtime import ComponentRequest
from services import nextflow
from scripts.lib.component_adapter import resource_bound_command
from test_remote_bundle_path_gaps import roots
from test_md_native_orchestration import _native, _context


@pytest.mark.parametrize('config_flag', [None, '-c', '-C'])
@pytest.mark.parametrize('absolute', [False, True])
def test_child_source_placement_preserves_compilation(roots, tmp_path, monkeypatch, config_flag, absolute):
    out, params = _native(roots, monkeypatch)
    parent, context, _ = _context(out, params, monkeypatch)
    requested = dict(md_job_config=parent.native_parameters['md_job_config'],
        md_engine='gromacs', md_replica_index=0, md_replica_count=2, md_replica_seed=7)
    request = ComponentRequest.capture(parent_job_id='parent', stage='md_replica', child_key='0',
        payload=dict(model_id='molecular_dynamics', mode='replica', name='replica',
                     parent_job_id='parent', child_stage='md_replica', pinned_gpu=0, params=requested))
    child = tmp_path/'separate child'; child.mkdir()
    context.update(child_id=request.component_id, child_output_dir=str(out/'components'/request.component_id.replace(':', '-')),
                   child_working_directory=str(child), working_directory=str(nextflow.PROJECT_ROOT),
                   resource_lock_path=str(tmp_path/'compute.lock'))
    invocation = nextflow.compile_component_nextflow_invocation(request, context)
    original = copy.deepcopy(invocation)
    command = list(invocation.command)
    run = command.index('run') + 1
    if absolute:
        command[run] = str(tmp_path/'retained source/workflow.nf')
    if config_flag:
        command += [config_flag, 'config/child.config,' + str(tmp_path/'absolute.config')]
    command += ['-resume', 'retained-session', '--label', 'run']
    before = list(command)
    placed = resource_bound_command(command, invocation, context, child)
    assert command == before and invocation == original
    assert placed[placed.index('run') + 1] == (before[run] if absolute else str(nextflow.PROJECT_ROOT/before[run]))
    assert placed[placed.index('-w') + 1] == str(child/'work')
    assert placed[placed.index('-resume') + 1] == 'retained-session'
    assert placed[placed.index('--label') + 1] == 'run'
    if config_flag:
        expected = str(nextflow.PROJECT_ROOT/'config/child.config') + ',' + str(tmp_path/'absolute.config')
        assert expected in placed
    assert (str(nextflow.PROJECT_ROOT/'nextflow.config') in placed) == (config_flag != '-C')
    assert placed[-2:] == ['-c', str(child/'component-resources.config')]
    assert 'cpus = 8' in (child/'component-resources.config').read_text()
    # Already-placed root/local/legacy/resume argv keeps its existing owner.
    assert resource_bound_command(command, None, context, child)[:-2] == command
