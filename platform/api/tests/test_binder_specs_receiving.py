"""Integrated binder receiving proofs. Scratch stores; no model or service start."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from database import Design, Job
from model_registry import get_registry
from routers import jobs, models
from schemas import BinderRoundRequest
from services import core_protein_scientific_contract as scientific
from services.binder_round_inputs import MODES, normalize_request, prediction_request
from services.nextflow import compile_job_nextflow_invocation
from test_binder_continuation import selected
from test_project_workflow_setups import setup_store

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('model', ['proteinmpnn', 'fampnn', 'caliby_binder', 'protenix', 'boltz2', 'esmfold2'])
def test_every_round_owner_uses_real_advertised_mode_when_enabled_or_retained(model, enabled):
    request = BinderRoundRequest.model_validate({'enabled': enabled,
        'sequence_design': {'model_id': 'fampnn', 'params': {}},
        'prediction': {'model_id': 'protenix', 'params': {}}})
    stage = request.sequence_design if model in {'proteinmpnn', 'fampnn', 'caliby_binder'} else request.prediction
    stage.model_id = model
    normalized = normalize_request(request, get_registry())
    definition = get_registry().get_internal_model_definition(model)
    assert MODES[model] in {mode.id for mode in definition.modes}
    assert normalized.enabled is enabled
    if model == 'esmfold2':
        assert MODES[model] == 'predict'
        assert scientific.admission_revision(model, MODES[model]) == scientific.REVISION


def test_served_real_yaml_slider_metadata_survives_list_detail_and_modes():
    app = FastAPI()
    app.include_router(models.router, prefix='/api/models')
    with TestClient(app) as client:
        listed = client.get('/api/models?include_experimental=true').json()
        for model, field_name in [('protenix', 'protenix_n_step'), ('caliby_binder', 'caliby_temperature')]:
            definition = get_registry().get_model(model)
            expected = next(p for p in definition.params if p.name == field_name)
            detail = client.get(f'/api/models/{model}').json()
            row = next(m for m in listed if m['id'] == model)
            for document in (detail, row):
                actual = next(p for p in document['params'] if p['name'] == field_name)
                assert actual == expected.model_dump()
                assert actual['ui_control'] == 'slider'
                assert actual['minimum'] is not None and actual['maximum'] is not None
                assert actual['step'] is not None
            assert client.get(f'/api/models/{model}/modes').json()['modes'] == detail['modes']
        response = client.get('/api/models/bindcraft2/native-settings', params={'selectors': json.dumps({'relax_accepted_designs': True, 'copies': 2})})
        assert response.status_code == 200, response.text
        inventory = response.json()['settings']
        assert {'core', 'modality', 'target', 'copies', 'relax_accepted_designs'} <= set(inventory['display_selector_fields'])
        assert inventory['display']['values']['relax_steps'] == 200


@pytest.mark.asyncio
async def test_ordinary_esmf_round_request_admission_compiler_and_real_native_wrapper(selected, monkeypatch):
    _, session, root, owner, tmp_path = selected
    monkeypatch.setenv('BMS_CORE_RUNTIME_MODE', '0')
    design = await session.get(Design, 'd1')
    target = tmp_path / 'independent-target.pdb'
    target.write_text(Path(design.pdb_path).read_text().replace('ALA A', 'GLY B'))
    requested = normalize_request({
        'binder_chains': ['A'], 'target_chains': ['B'],
        'sequence_design': {'model_id': 'fampnn', 'params': {}},
        'prediction': {'model_id': 'esmfold2', 'params': {
            'num_loops': 7, 'num_sampling_steps': 23, 'num_diffusion_samples': 3,
            'seed': 0, 'esmf_use_msa': False,
        }},
    })
    child, binding = prediction_request(root, owner, design, requested, ['A'], ['B'],
        {'target_path': str(target), 'chains': ['B']})
    before = deepcopy(child.model_dump())
    assert (child.model_id, child.mode, child.execution_target_id) == ('esmfold2', 'predict', root.execution_target_id)
    assert child.params['complex_components'] == [
        {'id': 'A', 'type': 'protein', 'sequence': 'A'},
        {'id': 'B', 'type': 'protein', 'sequence': 'G'},
    ]
    assert child.params['sequence'] == 'A'  # Valid binder summary, not another native component.
    assert [component['role'] for component in binding['input_components']] == ['binder', 'target']
    assert child.params['lineage_root_job_id'] == root.id
    assert child.params['iteration_source_job_id'] == owner.id
    assert not {'input_pdb', 'pdb_sequence_path', 'template', 'restraints'} & child.params.keys()
    response = await jobs._create_job(child, BackgroundTasks(), session)
    persisted = await session.get(Job, response.id)
    assert scientific.revision_for_job(persisted) == scientific.REVISION
    assert (persisted.model_id, persisted.mode, persisted.execution_target_id) == ('esmfold2', 'predict', root.execution_target_id)
    assert persisted.lineage_root_job_id == root.id
    assert persisted.params['complex_components'] == before['params']['complex_components']
    assert persisted.params['seed'] == before['params']['seed'] == 0
    output = tmp_path / 'compiled'
    invocation = compile_job_nextflow_invocation(persisted, deepcopy(persisted.params), str(output))
    assert invocation.entrypoint == 'workflows/structure_prediction.nf'
    for generated in invocation.generated_inputs:
        generated.materialize(output)
    native = invocation.native_parameters
    component_path = Path(native['esmf_complex_components_file'])
    from scripts.run_esmfold2_inference import load_components_file
    components = load_components_file(str(component_path))
    assert [(c['id'], c['sequence']) for c in components] == [('A', 'A'), ('B', 'G')]
    assert native['sequence_input'] == 'A'  # Admission/summary, not another scientific component.
    assert not native.get('esmf_sequence')
    spec = importlib.util.spec_from_file_location('binder_esmf_wrapper', ROOT / 'scripts/run_esmfold2_inference.py')
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    # The Nextflow branch removes only its path-input key and supplies the parsed
    # component document to this actual CPU-only native argv/receipt owner.
    wire = {**native, 'esmf_complex_components': components}
    wire.pop('esmf_complex_components_file')
    argv, receipt = wrapper.compile_workflow_request(wire, {})
    flags = dict(zip(argv[::2], argv[1::2]))
    actual = json.loads(flags['--complex-components-json'])
    assert [(c['id'], c['sequence']) for c in actual] == [('A', 'A'), ('B', 'G')]
    assert flags.get('--sequence') in (None, '')
    for key, expected in [('num_loops', 7), ('num_sampling_steps', 23), ('num_diffusion_samples', 3), ('seed', 0)]:
        assert receipt['settings'][key]['effective'] == expected
    for component in actual:
        scope = 'component:' + component['id']
        assert receipt['settings'][scope + '.msa_format']['scope'] == scope
        assert receipt['settings'][scope + '.msa_max_sequences']['scope'] == scope
    assert receipt['sources'] == []
    assert 'blind_pose' not in invocation.command
