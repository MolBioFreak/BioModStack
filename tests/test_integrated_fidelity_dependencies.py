"""CPU-only integrated selected dependency contracts; no native inference."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform/api'))
from model_registry import selected_execution_metadata
from native_components import PROCESS_CONTRACTS

FAM = {'lib/FampnnNativeControls.groovy', 'lib/FampnnAnalysisPolicy.groovy',
       'scripts/fampnn_native_runtime.py', 'scripts/fampnn_native_binding.py',
       'scripts/prep_fampnn_constraints_generic.py', 'scripts/maturation_native_adapter.py',
       'scripts/maturation_correspondence.py', 'scripts/analyse_fampnn.py',
       'scripts/analyse_fampnn_seq_probs.py'}

@pytest.mark.parametrize('model,mode,entry,params,stage,required', [
    ('fampnn', 'design', 'protein_sequence_design', {}, 'RunFAMPNN', FAM),
    ('fampnn_child', 'sequence_design', 'fampnn_child', {}, 'RunFAMPNN', FAM),
    ('template_antibody_denovo', 'maturation_child', 'maturation_child',
     {'maturation_redesign_enabled': True, 'ppiflow_mode': 'maturation'}, 'RunMaturationFAMPNN', FAM),
    ('esmfold2', 'blind_pose', 'binder_blind_pose', {}, 'BinderBlindPoseESMFold2',
     {'scripts/run_esmfold2_batch.py', 'scripts/run_esmfold2_inference.py'}),
    ('antibody_child', 'validation_batch', 'antibody_child', {'structure_validator': 'boltz2'},
     'BatchBoltzValidation', {'scripts/run_boltz_wrapper.py', 'scripts/lib/boltz_fidelity.py',
                            'scripts/publish_boltz_samples.py'}),
    ('antibody_child', 'validation_batch', 'antibody_child', {'structure_validator': 'esmfold2'},
     'BatchESMFold2Validation', {'scripts/run_esmfold2_batch.py', 'scripts/run_esmfold2_inference.py'}),
])
def test_selected_metadata_keeps_actual_native_helpers(model, mode, entry, params, stage, required):
    metadata = selected_execution_metadata(model, mode, params, f'workflows/{entry}.nf')
    components = [*metadata.static_components, *metadata.dynamic_templates]
    component = next(row for row in components if row.component_key == stage)
    selected = {row.logical_id for row in metadata.dependencies}
    for path in required:
        assert (ROOT / path).is_file()
        assert 'support_tool:' + path in selected
        assert 'support_tool:' + path in component.dependency_ids

@pytest.mark.parametrize('entry', ['structure_prediction', 'complex_prediction'])
def test_direct_boltz_selected_helpers(entry):
    metadata = selected_execution_metadata('boltz2', 'predict',
        {'pred_method': 'boltz', 'boltz_use_msa': False, 'run_frustrampnn': False},
        f'workflows/{entry}.nf')
    selected = {row.relative_path: row.logical_id for row in metadata.dependencies if row.kind == 'support_tool'}
    process = 'BoltzFromComplex' if entry == 'complex_prediction' else 'BoltzFromSequenceTask'
    component = next(row for row in metadata.static_components if row.component_key == process)
    for helper in ('scripts/run_boltz_wrapper.py', 'scripts/lib/boltz_fidelity.py'):
        assert helper in selected
        assert selected[helper] in component.dependency_ids


def test_legacy_esm_batch_selects_same_process_as_dispatcher():
    metadata = selected_execution_metadata('esmfold2', 'predict',
        {'pred_method': 'esmfold2', 'sequence_batch_json_path': '/logical/manifest.json',
         'run_frustrampnn': False}, 'workflows/structure_prediction.nf')
    assert 'ESMFold2MSAPredict' in {row.component_key for row in metadata.static_components}


@pytest.mark.parametrize('owner,required', [
    ('modules/shape_blueprint.nf:RunShapeFAMPNN', FAM | {'scripts/fampnn_shape_analysis.py'}),
    ('workflows/protein_local_redesign.nf:PrepProteinLocalMPNN', {'scripts/proteinmpnn_native_binding.py'}),
    ('workflows/protein_local_redesign.nf:PrepProteinLocalFAMPNN', FAM),
    ('modules/shape_blueprint.nf:RunShapeBoltzValidator', {'scripts/lib/boltz_fidelity.py'}),
    ('modules/rf3.nf:RunRF3', {'scripts/run_rf3.py', 'scripts/lib/filtering/rf3_association.py'}),
    ('modules/af2.nf:RunAF2', {'scripts/run_af2_initial_guess.py'}),
    ('modules/boltz_cp_experimental.nf:RunBoltzCPExperimental', {'scripts/fold_cp_input.py'}),
])
def test_embedded_owner_helper_declarations(owner, required):
    assert required <= set(PROCESS_CONTRACTS[owner][3])
    assert all((ROOT / name).is_file() for name in required)

@pytest.mark.parametrize('owner', [
    'modules/antibody_batch.nf:BatchESMFold2Validation',
    'modules/antibody_batch.nf:BatchBoltzValidation',
    'modules/rf3.nf:RunRF3',
])
def test_changed_native_outputs_match_selected_declarations(owner):
    source, process = owner.split(':')
    body = (ROOT / source).read_text().split('process ' + process + ' {', 1)[1]
    output = body.split('output:', 1)[1].split('script:', 1)[0]
    actual = [' '.join(line.strip().split()) for line in output.splitlines()
              if line.strip().startswith(('path ', 'path(', 'tuple '))]
    declared = [' '.join(line.split()) for line in PROCESS_CONTRACTS[owner][2]]
    assert set(actual) == set(declared)
