"""Real child compiler/descriptor contract, no scheduler or scientific execution."""
import pytest

from model_registry import selected_execution_metadata
from services import gpu_config, msa_server, nextflow
from services.result_contracts import resolve_result_contract


@pytest.mark.parametrize('selector', ['protenix', 'protenix_v2'])
@pytest.mark.parametrize('msa', [False, True])
def test_child_compilation_retains_native_validator_and_dependencies(tmp_path, monkeypatch, selector, msa):
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr(gpu_config, 'read_scheduler_config', lambda: {})
    monkeypatch.setattr(msa_server, 'read_server_settings', lambda: {})
    source = tmp_path / 'candidate.pdb'
    source.write_text('END\n')  # Compiler-only fixture; no molecular validity claim.
    params = dict(pdb_paths=str(source), structure_validator=selector,
                  protenix_use_msa=msa, protenix_msa_backend='colabfold_api',
                  protenix_seeds='0,17', protenix_n_sample=3,
                  protenix_use_template=False, protenix_anchor_target=False,
                  run_frustrampnn=False, run_immunogenicity_scoring=False,
                  run_thermompnn=False)
    invocation = nextflow.compile_nextflow_invocation('antibody_child', 'validation_batch', params, str(tmp_path / 'out'))
    assert invocation.entrypoint == 'workflows/antibody_child.nf'
    assert invocation.execution_plan is not None
    plan = invocation.execution_plan.to_dict()
    metadata = plan['metadata']
    assert not [b for b in metadata['blockers'] if b['field'] in {'dependency_closure', 'result_contract'}]
    assert 'BatchProtenixValidation' in {c['component_key'] for c in metadata['static_components']}
    dependencies = {d['relative_path'] for d in metadata['dependencies']}
    assert {'scripts/protenix_constraint_utils.py', 'scripts/protenix_chemistry.py',
            'scripts/prepare_protenix_exact_templates.py', 'scripts/run_protenix_inference.py'} <= dependencies
    assert plan['effective_json']['protenix_seeds'] == '0,17'
    assert plan['effective_json']['protenix_n_sample'] == 3


@pytest.mark.parametrize('mode', ['validation', 'validation_batch'])
def test_child_result_uses_existing_prediction_contract(mode):
    contract = resolve_result_contract(model_type='antibody_child', stage_mode=mode)
    assert contract.analysis_contract_id == 'structure_prediction_v1'
    assert 'antibody_closeout_report' not in contract.required_artifacts
    assert resolve_result_contract(model_type='antibody_child', stage_mode='unrelated').analysis_contract_id is None
    assert resolve_result_contract(model_type='unrelated', stage_mode=mode).analysis_contract_id is None


@pytest.mark.parametrize('mode', ['flexible', 'conditioned', 'frozen'])
@pytest.mark.parametrize('requested', [False, True, None])
def test_geometry_normalization_preserves_generic_template_intent(mode, requested):
    from routers.jobs import _normalize_structure_geometry_params
    params = {'protenix_target_geometry_mode': mode}
    if requested is not None:
        params['protenix_use_template'] = requested
    normalized = _normalize_structure_geometry_params(params)
    assert normalized['protenix_anchor_target'] is (mode != 'flexible')
    assert ('protenix_use_template' in normalized) is (requested is not None)
    if requested is not None:
        assert normalized['protenix_use_template'] is requested


def test_generic_templates_do_not_invent_target_anchoring():
    from routers.jobs import _normalize_structure_geometry_params
    assert _normalize_structure_geometry_params({'protenix_use_template': True}) == {'protenix_use_template': True}


def test_parent_protenix_selector_reaches_selected_component():
    metadata = selected_execution_metadata('antibody_denovo', 'antibody_denovo_pipeline',
        {'structure_validator': 'protenix', 'protenix_use_msa': False, 'run_frustrampnn': False},
        'workflows/antibody_denovo.nf')
    assert 'BatchProtenixValidation' in {c.component_key for c in metadata.static_components}
    assert not any('Unsupported native structure_validator: protenix' in b.reason for b in metadata.blockers)
