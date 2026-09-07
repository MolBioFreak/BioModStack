"""Admission stores biological declarations, never future artifact evidence."""
from copy import deepcopy

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from database import Job
from routers import jobs
from schemas import JobCreate
from services import core_protein_scientific_contract as contract
from test_core_protein_scientific_admission import admission


@pytest.fixture(autouse=True)
def artifact_root(monkeypatch, tmp_path):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))


def payload(tmp_path, overrides):
    pdb = tmp_path / 'input.pdb'
    pdb.write_text('ATOM      1  CA  ALA A   1      10.000  10.000  10.000  1.00 20.00           C\n'
                   'ATOM      2  CA  GLY B   2      11.000  10.000  10.000  1.00 20.00           C\nEND\n')
    return JobCreate(name='declared', model_id='fampnn', mode='binder_design',
                     params={'input_pdb': str(pdb), 'design_chain': 'A', 'target_chain': 'B'},
                     fampnn_analysis_overrides=overrides)


@pytest.mark.asyncio
async def test_typed_override_admission_persists_only_declaration(admission, monkeypatch, tmp_path):
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('fampnn', 'binder_design')}))
    request = payload(tmp_path, {'summary': [{'chain_id': 'B', 'author_number': 2}], 'mutation': []})
    response = await jobs._create_job(request, BackgroundTasks(), admission)
    admission.expire_all()
    row = await admission.get(Job, response.id)
    declaration = row.provenance['fampnn_analysis_declaration']
    assert row.params['fampnn_analysis_declaration'] == declaration
    assert declaration['declaration'] == 'binder_role_residues'
    assert declaration['summary_override'] == ['B:2:']
    assert declaration['mutation_override'] == []
    assert declaration['summary'] == ['A:1:']
    assert declaration['sequence_design'] == ['A:1:']
    assert 'artifact_binding' not in str(declaration)
    assert 'sha256' not in str(declaration)
    assert 'fampnn_analysis_policy' not in row.params
    original = deepcopy(row.provenance)
    transported = contract.workflow_params(row, {'fampnn_analysis_declaration': {'forged': True}})
    assert transported['fampnn_analysis_declaration'] == declaration
    assert row.provenance == original
    import json
    from services.nextflow import build_nextflow_command
    command = build_nextflow_command('fampnn', 'binder_design', row.params, str(tmp_path / 'command'), job_id=row.id)
    from pathlib import Path
    import hashlib
    declaration_path = Path(command[command.index('--fampnn_analysis_declaration_path') + 1])
    assert json.loads(declaration_path.read_text()) == declaration
    assert command[command.index('--fampnn_analysis_declaration_sha256') + 1] == hashlib.sha256(declaration_path.read_bytes()).hexdigest()
    assert '--fampnn_analysis_declaration' not in command


def test_large_declaration_uses_job_owned_file(tmp_path):
    import json
    import hashlib
    from pathlib import Path
    from services.nextflow import build_nextflow_command
    declaration = {'input_domain': [f'A:{i}:' for i in range(30000)]}
    command = build_nextflow_command('fampnn', 'design',
        {'fampnn_analysis_declaration': declaration}, str(tmp_path / 'command'), job_id='large')
    assert max(len(arg.encode()) for arg in command) < 131072
    path = Path(command[command.index('--fampnn_analysis_declaration_path') + 1])
    assert path.parent == tmp_path / 'command'
    assert json.loads(path.read_text()) == declaration
    assert command[command.index('--fampnn_analysis_declaration_sha256') + 1] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize('override', [
    {'mutation': [{'chain_id': 'B', 'author_number': 2}]},
    {'summary': [{'chain_id': 'Z', 'author_number': 9}]},
])
async def test_forbidden_override_has_zero_writes(admission, monkeypatch, tmp_path, override):
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('fampnn', 'binder_design')}))
    def no_add(*args, **kwargs):
        raise AssertionError('invalid policy must fail before writes')
    monkeypatch.setattr(admission, 'add', no_add)
    with pytest.raises(HTTPException) as exc:
        await jobs._create_job(payload(tmp_path, override), BackgroundTasks(), admission)
    assert exc.value.status_code == 422
    assert not list((await admission.execute(select(Job))).scalars())
    assert not (tmp_path / 'results').exists()


@pytest.mark.parametrize('key', ['fampnn_analysis_policy', 'fampnn_analysis_declaration',
                               'fampnn_analysis_declaration_path', 'fampnn_analysis_declaration_sha256'])
def test_compiled_authority_forbidden_in_nested_extras(key, tmp_path):
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='server-owned'):
        JobCreate(name='forged', model_id='fampnn', mode='design', params={'extras': [{key: {}}]})


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resubmit', 'resume'])
async def test_declared_attempt_retry_retains_authority(admission, monkeypatch, tmp_path, operation):
    from fastapi import Request, Response
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('fampnn', 'binder_design')}))
    response = await jobs._create_job(payload(tmp_path, {'mutation': []}), BackgroundTasks(), admission)
    original = await admission.get(Job, response.id)
    original.status = 'failed'
    await admission.commit()
    before = deepcopy(original.provenance)
    req = Request({'type': 'http', 'headers': []})
    if operation == 'resubmit':
        result = await jobs.resubmit_job(original.id, req, Response(), admission)
    else:
        result = await jobs.resume_job(original.id, req, Response(), request=None, session=admission)
    new = await admission.get(Job, result['new_job_id'])
    assert new.provenance['fampnn_analysis_declaration'] == before['fampnn_analysis_declaration']
    assert new.params['fampnn_analysis_declaration'] == before['fampnn_analysis_declaration']
    assert original.provenance == before


@pytest.mark.asyncio
@pytest.mark.parametrize('forbidden', [False, True])
async def test_real_child_inherits_parent_biology_not_revision(admission, monkeypatch, tmp_path, forbidden):
    from services.fampnn_policy_admission import compile_declaration
    parent_request = payload(tmp_path, {})
    declaration = compile_declaration('fampnn', 'binder_design', parent_request.params)
    declaration['allow_summary_override'] = False
    parent = Job(id='parent-scope', name='parent', model_id='protein_modification_experimental',
                 mode='de_novo_design', status='running', params={},
                 provenance={'fampnn_analysis_declaration': declaration})
    admission.add(parent)
    await admission.commit()
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('fampnn_child', 'sequence_design')}))
    child_request = JobCreate(name='sequence-child', model_id='fampnn_child', mode='sequence_design',
        parent_job_id=parent.id, child_stage='fampnn',
        params={'pdb_paths': parent_request.params['input_pdb'], 'fampnn_checkpoint': 'fampnn_0_0.pt'},
        fampnn_analysis_overrides={'summary': []} if forbidden else {'mutation': []})
    if forbidden:
        with pytest.raises(HTTPException) as exc:
            await jobs._create_job(child_request, BackgroundTasks(), admission)
        assert exc.value.status_code == 422
        assert len(list((await admission.execute(select(Job))).scalars())) == 1
    else:
        response = await jobs._create_job(child_request, BackgroundTasks(), admission)
        row = await admission.get(Job, response.id)
        assert row.provenance['fampnn_analysis_declaration']['owner'] == 'protein_design'
        assert row.provenance['fampnn_analysis_declaration']['mutation_override'] == []
        assert contract.revision_for_job(row) == 1
        assert contract.revision_for_job(parent) is None


@pytest.mark.asyncio
async def test_unactivated_override_is_not_silently_ignored(admission, monkeypatch, tmp_path):
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset())
    with pytest.raises(HTTPException) as exc:
        await jobs._create_job(payload(tmp_path, {'mutation': []}), BackgroundTasks(), admission)
    assert exc.value.status_code == 422
    assert not list((await admission.execute(select(Job))).scalars())


@pytest.mark.asyncio
@pytest.mark.parametrize('forbidden', [False, True])
async def test_local_parent_authorized_region_is_not_whole_design_chain(admission, monkeypatch, tmp_path, forbidden):
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('protein_modification_experimental', 'region_redesign')}))
    request = payload(tmp_path, {})
    source = tmp_path / 'input.pdb'
    source.write_text(source.read_text().replace('END\n',
        'ATOM      3  CA  SER A   3      12.000  10.000  10.000  1.00 20.00           C\nEND\n'))
    request.model_id = 'protein_modification_experimental'
    request.mode = 'region_redesign'
    request.params = {'input_pdb': str(source), 'design_chains': 'A', 'context_chains': 'B',
        'region_mode': 'manual_ranges', 'redesign_ranges': 'A1,A3', 'sequence_redesign_ranges': 'A3',
        'seq_method': 'fampnn', 'structure_validators': ['esmfold2']}
    from services.fampnn_policy_admission import FampnnAnalysisOverrides
    request.fampnn_analysis_overrides = FampnnAnalysisOverrides.model_validate({
        'summary': [{'chain_id': 'B', 'author_number': 2}],
        'mutation': [{'chain_id': 'A', 'author_number': 1 if forbidden else 3}]})
    if forbidden:
        monkeypatch.setattr(admission, 'add', lambda *a: pytest.fail('forbidden override wrote a row'))
        with pytest.raises(HTTPException) as exc:
            await jobs._create_job(request, BackgroundTasks(), admission)
        assert exc.value.status_code == 422
        assert not list((await admission.execute(select(Job))).scalars())
    else:
        result = await jobs._create_job(request, BackgroundTasks(), admission)
        admission.expire_all()
        row = await admission.get(Job, result.id)
        declaration = row.provenance['fampnn_analysis_declaration']
        assert declaration['owner'] == 'protein_local_redesign'
        assert declaration['declaration'] == 'sequence_redesign_positions_spec'
        assert declaration['summary'] == ['A:3:']
        assert declaration['sequence_design'] == ['A:3:']
        assert declaration['summary_override'] == ['B:2:']
        assert declaration['mutation_override'] == ['A:3:']
        assert not any(k in str(declaration) for k in ('sha256', 'artifact_binding'))
        import json
        from pathlib import Path
        from services.nextflow import build_nextflow_command
        command = build_nextflow_command(row.model_id, row.mode, row.params, str(tmp_path / 'command'), job_id=row.id)
        assert command[command.index('--plr_seq_method') + 1] == 'fampnn'
        transported = json.loads(Path(command[command.index('--fampnn_analysis_declaration_path') + 1]).read_text())
        from fampnn_policy_resolution import prep_receipt, resolve_declaration
        # Actual materialized PDB bytes and source-owned residue-object pairs;
        # no native candidate identity is supplied until native files exist.
        prepared = tmp_path / 'prepared'
        prepared.mkdir()
        prepared_pdb = prepared / 'input.pdb'
        prepared_pdb.write_bytes(source.read_bytes())
        receipt = prep_receipt(source, prepared_pdb, [(v, v) for v in declaration['input_domain']])
        scopes = resolve_declaration(transported, {'input': receipt}, prepared)
        assert scopes['inputs']['input']['summary'] == ['A:3:']
        assert scopes['inputs']['input']['mutation_override'] == ['A:3:']
        assert 'producer_candidate_ids' not in scopes['inputs']['input']['artifact_binding']


@pytest.mark.parametrize('override', [{'mutation': [{'chain_id': 'A', 'author_number': True}]},
                                     {'summary': [], 'inputs': {}},
                                     {'summary': [{'chain_id': 'A', 'author_number': 1, 'source_sha256': 'x'}]}])
def test_override_schema_is_closed_and_strict(tmp_path, override):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        payload(tmp_path, override)
