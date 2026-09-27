import pytest
from services import core_protein_scientific_contract as contract
from test_core_protein_scientific_admission import admission


@pytest.mark.asyncio
async def test_admission_rejects_native_plddt_before_queue(admission, monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from routers import jobs
    from schemas import JobCreate
    from sqlalchemy import select
    from database import Job
    with pytest.raises(HTTPException, match='native pLDDT'):
        await jobs._create_job(JobCreate(name='synthetic-admission', model_id='boltzgen', mode='ntp_binder',
                              params={'ntp_type': 'ATP', 'min_plddt': 0}), BackgroundTasks(), admission)
    assert list((await admission.execute(select(Job))).scalars()) == []


@pytest.mark.asyncio
async def test_admission_persists_effective_native_rank(admission, monkeypatch):
    from fastapi import BackgroundTasks
    from routers import jobs
    from schemas import JobCreate
    from database import Job
    response = await jobs._create_job(JobCreate(name='synthetic-admission', model_id='boltzgen', mode='ntp_binder',
                              params={'ntp_type': 'ATP', 'boltzgen_rank_design_ptm_weight': 2}), BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    assert job.params['boltzgen_effective_rank'][0] == {'name': 'design_ptm', 'weight': 2., 'higher_is_better': True, 'unit': 'fraction'}
    assert contract.workflow_params(job, job.params)['boltzgen_effective_rank'] == job.params['boltzgen_effective_rank']


def test_known_native_plddt_incompatibility_is_rejected_before_transport():
    from types import SimpleNamespace
    job = SimpleNamespace(model_id='boltzgen', mode='peptide_binder', provenance={contract.REVISION_KEY: 1})
    for key in ['min_plddt', 'boltzgen_min_plddt']:
        with pytest.raises(ValueError, match='native pLDDT'):
            contract.workflow_params(job, {key: 0})


def test_effective_rank_is_native_declared_and_typed():
    from types import SimpleNamespace
    job = SimpleNamespace(model_id='boltzgen', mode='peptide_binder', provenance={contract.REVISION_KEY: 1})
    params = contract.workflow_params(job, {'boltzgen_rank_design_ptm_weight': 2, 'boltzgen_rank_filter_rmsd_weight': None})
    assert params['boltzgen_metrics_override'] == 'design_ptm=2.0 affinity_probability=1.0 filter_rmsd=none'
    assert params['boltzgen_effective_rank'] == [
        {'name': 'design_ptm', 'higher_is_better': True, 'weight': 2., 'unit': 'fraction'},
        {'name': 'affinity_probability', 'higher_is_better': True, 'weight': 1., 'unit': 'fraction'}]
    for value in [True, '2', 0, float('inf')]:
        with pytest.raises(ValueError):
            contract.workflow_params(job, {'boltzgen_rank_design_ptm_weight': value})
    with pytest.raises(ValueError):
        contract.workflow_params(job, {'boltzgen_metrics_override': 'unknown=2'})


@pytest.mark.parametrize('mode,protocol', [('protein_binder', 'protein-anything'),
    ('peptide_binder', 'peptide-anything'), ('nanobody_binder', 'nanobody-anything')])
def test_public_generation_typed_mapping_preserves_native_values(mode, protocol):
    from services.boltzgen_request_compatibility import normalize_boltzgen_generation_request
    original = {'target_pdb': '/owned/target.pdb', 'alpha': 0., 'boltzgen_noise_scale': 0.,
                'boltzgen_skip_inverse_folding': False, 'min_plddt': None}
    result = normalize_boltzgen_generation_request(mode, original)
    assert result['boltzgen_protocol'] == protocol
    assert result['boltzgen_target_pdb_path'] == original['target_pdb']
    assert result['boltzgen_generation_mode'] == mode
    assert result['boltzgen_alpha'] == 0.
    assert result['boltzgen_noise_scale'] == 0.
    assert result['boltzgen_skip_inverse_folding'] is False
    assert result['boltzgen_min_plddt'] is None
    assert normalize_boltzgen_generation_request(mode, result) == result
    assert 'target_pdb' in original
    assert normalize_boltzgen_generation_request(mode, {'protocol': 'auto'})['boltzgen_protocol'] == 'auto'


def test_public_generation_inputs_do_not_coerce_frameworks_or_aliases():
    from services.boltzgen_request_compatibility import normalize_boltzgen_generation_request as normalize
    for params in [
        {'target_pdb': 'one.pdb', 'boltzgen_target_pdb_path': 'two.pdb'},
        {'target_chains': ['a']},
        {'nanobody_framework': 'XXX'},
        {'scaffold_path': 'file.pdb'},
        {'scaffold_path': 'file.pdb', 'scaffold_chain': 'h', 'scaffold_design_ranges': '1..3', 'binder_sequence': 'AAA'},
    ]:
        with pytest.raises(ValueError):
            normalize('protein_binder', params)
    request = {'scaffold_path': 'file.pdb', 'scaffold_chain': 'h', 'scaffold_design_ranges': '1..3'}
    result = normalize('protein_binder', request)
    assert result['boltzgen_scaffold_chain'] == 'h'
    assert result['boltzgen_scaffold_design_ranges'] == '1..3'


@pytest.mark.asyncio
async def test_generic_generation_never_resolves_nanobody_scaffolds(monkeypatch):
    from services import boltzgen_scaffolding as scaffolding
    async def unexpected(*args, **kwargs):
        raise AssertionError('unselected VHH preparation')
    monkeypatch.setattr(scaffolding, 'resolve_nanobody_scaffold_specs', unexpected)
    params = {'diffusion_method': 'boltzgen', 'boltzgen_mode': 'protein_binder', 'boltzgen_scaffold_path': '/owned/scaffold.pdb'}
    assert await scaffolding.prepare_boltzgen_params_for_launch(params) == (params, [])
