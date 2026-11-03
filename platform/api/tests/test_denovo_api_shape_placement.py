"""Selected placement reaches the real Shape compiler, not request bytes."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.nextflow import compile_job_nextflow_invocation


@pytest.mark.parametrize('placement,override,expected', [
    ('local', None, 16), ('remote', None, 48), ('remote-multi', None, 16),
    ('missing', None, None), ('wrong-target', None, None),
    ('local', 24, 24), ('remote', 24, 24),
])
def test_selected_shape_capacity_reaches_actual_command_without_rewriting_request(tmp_path, monkeypatch,
                                                                               placement, override, expected):
    from routers import gpu
    observed = []
    def local_observation():
        observed.append('local')
        assert placement == 'local', 'a worker launch must never inspect controller GPUs'
        return [SimpleNamespace(index=0, memory_total_mb=98304, memory_used_mb=0),
                SimpleNamespace(index=3, memory_total_mb=16384, memory_used_mb=16000)]
    monkeypatch.setattr(gpu, 'get_gpu_stats', local_observation)
    params = {'shape_request': {'sequence_policy': 'skip', 'sequences_per_backbone': 0}}
    if override is not None:
        params['shape_gpu_memory_gib'] = override
    before = deepcopy(params)
    is_remote = placement != 'local'
    assignment = dict(execution_target_id='worker', gpu_indices=[3], admission_snapshot={'devices': [
        {'gpu_index': 0, 'memory_total_mb': 98304, 'memory_used_mb': 0},
        {'gpu_index': 3, 'memory_total_mb': 49152, 'memory_used_mb': 49000},
        {'gpu_index': 7, 'memory_total_mb': 16384, 'memory_used_mb': 1}]})
    if placement == 'remote-multi':
        assignment['gpu_indices'] = [3, 7]
    elif placement == 'missing':
        assignment['admission_snapshot'] = {}
    elif placement == 'wrong-target':
        assignment['execution_target_id'] = 'old-worker'
    job = SimpleNamespace(id='shape', model_id='protein_modification_experimental', mode='shape_blueprint',
        params=params, provenance={'remote_execution_assignment': assignment} if is_remote else {},
        execution_target_id='worker' if is_remote else None, assigned_gpu=3,
        execution_source_revision=None, execution_source_tree=None)
    invocation = compile_job_nextflow_invocation(job, params, str(tmp_path / 'out'))
    assert invocation.native_parameters.get('shape_gpu_memory_gib') == expected
    command = list(invocation.command)
    if expected is None:
        assert '--shape_gpu_memory_gib' not in command
    else:
        assert float(command[command.index('--shape_gpu_memory_gib') + 1]) == expected
    assert params == before
    assert json.loads(invocation.requested_json) == before
    assert observed == (['local'] if placement == 'local' and override is None else [])
