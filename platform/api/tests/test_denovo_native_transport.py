"""Exact native compiler transport and idempotent shared ingress."""
import copy
import json
import pytest
from component_runtime import SourceIdentity
from services.nextflow import compile_nextflow_invocation
from services.rfd3_generation import normalize_generation_params, GenerationContractError, request_sha256


def compile(model, mode, params, tmp_path):
    return compile_nextflow_invocation(model, mode, params, str(tmp_path / 'out'), job_id='fixture',
        source_identity=SourceIdentity('a' * 40, 'b' * 40))


@pytest.mark.parametrize('parent', [True, False])
def test_complexa_exact_request_and_primary_pair(tmp_path, parent):
    params = {'complexa_request': {'task': 'AME', 'task_name': 'native', 'reward': None,
        'seed': 0, 'args': {'self_cond': False}}}
    if parent: params['generator'] = 'complexa'
    invocation = compile('protein_modification_experimental' if parent else 'complexa',
        'de_novo_design' if parent else 'generate', params, tmp_path)
    assert invocation.entrypoint == 'workflows/complexa.nf'
    assert not (tmp_path / 'out').exists()
    payload = json.loads(invocation.generated_inputs[0].payload)
    assert payload == params['complexa_request']
    assert invocation.native_parameters['complexa_request'] == payload
    selected = {dep.relative_path for dep in invocation.execution_plan.metadata.dependencies if dep.kind == 'weights'}
    assert selected == {'complexa/complexa_ame.ckpt', 'complexa/complexa_ame_ae.ckpt'}


@pytest.mark.parametrize('backend,key,value', [
    ('laproteina', 'pcad_laproteina_settings_json', {'seed': 0, 'self_cond': False, 'scale_bb_ca': None}),
    ('disco', 'pcad_disco_settings_json', {'seeds': [0, 19], 'noisy_guidance': {'enabled': False}}),
    ('disco', 'pcad_disco_jobs_json', [{'name': "native's", 'sequences': [{'dnaSequence': {'sequence': 'ACGT', 'count': 1}}]}]),
])
def test_cad_typed_json_transport_once(tmp_path, backend, key, value):
    invocation = compile('protein_modification_experimental', 'de_novo_design',
        {'generator': backend, key: value}, tmp_path)
    assert invocation.entrypoint == 'workflows/protein_cad_experimental.nf'
    index = invocation.command.index('--' + key)
    assert json.loads(invocation.command[index + 1]) == value
    assert invocation.native_parameters[key] == value


def test_rfd3_double_normalization_preserves_verified_request():
    normalized, request, digest = normalize_generation_params({'num_designs': 2.7}, job_name='native')
    normalized.update(workflow_adapter='rfd3', frustrampnn_requiredness='optional')
    again, retained, retained_digest = normalize_generation_params(normalized, job_name='native')
    assert again == normalized and request == retained and digest == retained_digest
    for mutate in ('digest', 'rehash_science', 'unknown', 'derived_alias'):
        changed = copy.deepcopy(normalized)
        if mutate == 'digest': changed['rfd3_generation_request_sha256'] = '0' * 64
        elif mutate == 'rehash_science':
            changed['rfd3_generation_request']['generation']['num_designs'] = 3
            changed['rfd3_generation_request_sha256'] = request_sha256(changed['rfd3_generation_request'])
        elif mutate == 'unknown': changed['invented_science'] = 2
        else: changed['rfd_num_designs'] = 3
        with pytest.raises(GenerationContractError): normalize_generation_params(changed, job_name='native')
