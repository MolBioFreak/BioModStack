"""Known callback-dependent workflows must never run a partial remote recipe."""
import pytest
from services.remote_execution.bundle import compile_remote_dependencies, RemoteBundleError


@pytest.mark.parametrize('workflow', [
    'conformational_mapping', 'protein_design', 'boltz_cp_experimental',
    'antibody_denovo', 'protein_local_redesign', 'ppiflow_generator_design',
])
def test_callback_workflows_rejected_without_disabling_requested_science(workflow):
    command = ['nextflow', 'run', f'/source/workflows/{workflow}.nf', '--run_frustrampnn', 'true']
    original = list(command)
    with pytest.raises(RemoteBundleError, match='No local fallback'):
        compile_remote_dependencies('protenix', 'gpu', command)
    assert command == original


@pytest.mark.parametrize('workflow', ['structure_prediction', 'complex_prediction'])
def test_self_contained_prediction_keeps_required_stage(workflow):
    command = ['nextflow', 'run', f'/source/workflows/{workflow}.nf', '--run_frustrampnn', 'true']
    compiled, params = compile_remote_dependencies('protenix', 'gpu', command)
    assert params['run_frustrampnn'] is True
    assert compiled[compiled.index('--run_frustrampnn') + 1] == 'true'
