"""Actual routed workflow and scratch immutable-retention qualification."""
import json
from contextlib import asynccontextmanager
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from molbio_models import MolBioBase, MolecularOperation, MolecularRevision, NucleotideSequence
from molbio_database import get_molbio_session
from routers import molbio_ops, molbio_golden_gate_design, nucleotide_sequences
from services.assembly.golden_gate_workflow_types import REQUEST_ADAPTER, WorkflowResult, SaveDesignRequest
from services.assembly.golden_gate_workflow import run_workflow, freeze_selection
from test_golden_gate_design_core import request, binding, A, B


@asynccontextmanager
async def client_store(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "workups.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def dependency():
        async with sessions() as session:
            yield session
    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.include_router(molbio_golden_gate_design.router)
    app.include_router(nucleotide_sequences.router)
    app.dependency_overrides[get_molbio_session] = dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client, sessions
    finally:
        await engine.dispose()


def test_discriminated_closed_settings():
    schema = REQUEST_ADAPTER.json_schema()
    assert set(schema['discriminator']['mapping']) == {'assemble_parts', 'split_target', 'evaluate_overhangs', 'optimize_overhangs'}
    with pytest.raises(ValueError):
        REQUEST_ADAPTER.validate_python({'task': 'evaluate_overhangs', 'junctions': [], 'fidelity': {'undocumented': True}})
    assert 'tm_settings' not in schema['$defs']['PrimerSelectionSettings']['properties']


@pytest.mark.asyncio
async def test_http_evaluate_optimize_and_options(tmp_path):
    async with client_store(tmp_path) as (client, _):
        options = await client.get('/api/molbio/assembly/golden-gate/options')
        assert options.status_code == 200, options.text
        assert len(options.json()['raw_design']['datasets']) == 5
        assert all('observations' not in dataset for dataset in options.json()['raw_design']['datasets'])
        score = await client.post('/api/molbio/assembly/golden-gate/design', json={
            'task': 'evaluate_overhangs', 'junctions': 'GGAG TGAC TCCC TACT CCAT AATG AGCC TTCG GCTT GGTA CGCT'.split(),
            'fidelity': {'dataset_id': 'pryor2020-s002'}})
        assert score.status_code == 200, score.text
        assert score.json()['evaluation']['f_set'] == 0.8092502583687903
        optimized = await client.post('/api/molbio/assembly/golden-gate/design', json={
            'task': 'optimize_overhangs', 'candidate_domain': ['AAAA', 'AAAC', 'AATG', 'CCGT', 'TGAC'],
            'junction_count': 3, 'end_length': 4, 'fixed': ['CATT'], 'required': ['AAAC'], 'excluded': ['ACGG'],
            'fidelity': {'dataset_id': 'pryor2020-s002'}})
        assert optimized.status_code == 200, optimized.text
        assert optimized.json()['search_result']['solutions']


@pytest.mark.parametrize('enzyme,spacer,width', [('BsaI','A',4), ('BbsI','AC',4), ('SapI','A',3)])
@pytest.mark.parametrize('topology', ['linear', 'circular'])
def test_real_target_split_exact_reconstruction(enzyme, spacer, width, topology):
    sequence = 'AATG' + A + 'GGAG' + B
    req = REQUEST_ADAPTER.validate_python(dict(task='split_target', target=dict(id='target', source=dict(kind='inline', sequence=sequence, topology=topology)),
        enzyme=binding(enzyme), windows=[dict(start=20, end=21), dict(start=80, end=81)],
        fixed_positions=[20,80], preparation=dict(kind='synthesis', clamp='TT', spacer=spacer),
        terminal_right_fusion='TGAC' if width == 4 else 'TGA', search=dict(ranking_mode='lexicographic', unique_classes=False, exclude_palindromes=False)))
    result = run_workflow(req)
    assert result.solutions, result.diagnostics
    assert result.solutions[0].design.solutions[0].sequence == sequence
    assert result.solutions[0].design.solutions[0].exact_target_match
    frozen = freeze_selection(result, result.selected_solution_id)
    replay = run_workflow(REQUEST_ADAPTER.validate_python(frozen.request.model_dump()))
    assert replay.solutions[0].design.solutions[0].sequence == sequence


@pytest.mark.asyncio
async def test_save_retry_reopen_frozen_without_design_or_source_projection(tmp_path, monkeypatch):
    async with client_store(tmp_path) as (client, sessions):
        preview = await client.post('/api/molbio/assembly/golden-gate/design', json=request().model_dump(mode='json'))
        assert preview.status_code == 200, preview.text
        result = WorkflowResult.model_validate(preview.json())
        save_request = SaveDesignRequest(selection=freeze_selection(result, 'fixed'), name='Frozen GG', idempotency_key='same')
        saved = await client.post('/api/molbio/assembly/golden-gate/design/save', json=save_request.model_dump(mode='json'))
        assert saved.status_code == 200, saved.text
        operation_id = saved.json()['operation_id']
        shelf = await client.get('/api/sequences/assembly-workups?limit=50&offset=0')
        assert shelf.status_code == 200, shelf.text
        assert shelf.json()[0]['engine'] == 'golden_gate_design'
        assert shelf.json()[0]['fragment_count'] == 2
        assert shelf.json()[0]['primer_count'] == 2
        async with sessions() as session:
            operation = await session.get(MolecularOperation, operation_id)
            assert operation.parameters['dna_references']
            assert A not in json.dumps(operation.parameters)
            row = await session.get(NucleotideSequence, saved.json()['product_document_id'])
            assert row.features and row.primers
            assert set(row.operation_params) == {'operation_id', 'schema_version', 'engine', 'engine_version', 'fragment_count', 'primer_count'}
            row.sequence = 'AAAA'
            await session.commit()
        def forbidden(*args, **kwargs):
            raise AssertionError('Reopen/retry must not invoke design/search')
        monkeypatch.setattr('services.assembly.golden_gate_workflow_persistence.run_workflow', forbidden)
        reopened = await client.get('/api/molbio/assembly/golden-gate/design/' + operation_id)
        retried = await client.post('/api/molbio/assembly/golden-gate/design/save', json=save_request.model_dump(mode='json'))
        assert reopened.status_code == retried.status_code == 200
        assert reopened.json() == retried.json() == saved.json()
        async with sessions() as session:
            operations = list((await session.scalars(select(MolecularOperation))).all())
            assert len(operations) == 1


@pytest.mark.asyncio
async def test_registered_revision_contract_and_project_adapters(tmp_path):
    from jsonschema import Draft202012Validator
    from urllib.parse import urlencode
    from services.ngs_molbio_capabilities import capability_record, capability_parameter_schema
    from services.global_experiments.adapters import MolBioRevisionAdapter, MolBioOperationAdapter, registry
    from services.assembly.golden_gate_workflow_persistence import save_workup
    async with client_store(tmp_path) as (client, sessions):
        native = run_workflow(REQUEST_ADAPTER.validate_python(request().model_dump()))
        async with sessions() as session:
            first = await save_workup(session, SaveDesignRequest(selection=freeze_selection(native, 'fixed'), name='Seed', idempotency_key='seed'))
            revisions = list((await session.scalars(select(MolecularRevision).where(MolecularRevision.operation_id == first.operation_id))).all())
        sources = {r.provenance.get('material_id'): r for r in revisions if r.provenance}
        data = request().model_dump(mode='json')
        for source in data['sources']:
            source['source'] = dict(kind='molecular_revision', revision_id=sources['source:' + source['id']].id)
        routed = await client.post('/api/molbio/assembly/golden-gate/design', json=data)
        assert routed.status_code == 200, routed.text
        result = WorkflowResult.model_validate(routed.json())
        assert result.solutions[0].design.solutions[0].sequence == native.solutions[0].design.solutions[0].sequence
        cap = capability_record('molbio.assembly.golden_gate_design')
        schema = capability_parameter_schema(cap['capability_id'])
        save = SaveDesignRequest(selection=freeze_selection(result, 'fixed'), name='Revision design', idempotency_key='revision').model_dump(mode='json')
        assert not list(Draft202012Validator(schema).iter_errors(save))
        response = await client.post(cap['native_mapping']['source'].removeprefix('POST '), json=save)
        assert response.status_code == 200, response.text
        value = response.json()
        # Exercise the actual already-registered Project source/result adapters.
        assert registry.get(MolBioRevisionAdapter.adapter_id)
        assert registry.get(MolBioOperationAdapter.adapter_id)
        revision_receipt = await MolBioRevisionAdapter(molbio_session_factory=sessions).verify(None,
            urlencode(dict(sequence_id=value['product_document_id'], revision_id=value['product_revision_id'])))
        operation_receipt = await MolBioOperationAdapter(molbio_session_factory=sessions).verify(None, value['operation_id'])
        assert revision_receipt['metadata']['revision_id'] == value['product_revision_id']
        assert operation_receipt['metadata']['operation_kind'] == 'golden_gate_design'
        assert 'operation_id=' in operation_receipt['reopen_uri']


def test_native_automatic_primer_selection_preserves_requested_and_effective():
    data = request().model_dump(mode='json')
    data['automatic_primers'] = [dict(part_id='insert', settings=dict(primer_min_length=20, primer_max_length=20,
        product_min_length=len(B), product_max_length=len(B), flank_search_span=20,
        gc_min_percent=0, gc_max_percent=100, gc_clamp_min=0, max_poly_x=100, tm_max_delta_c=100))]
    result = run_workflow(REQUEST_ADAPTER.validate_python(data))
    assert not result.diagnostics
    assert result.requested.parts[1].preparation.reverse_anneal_length == 21
    assert result.solutions[0].fixed_request.parts[1].preparation.reverse_anneal_length == 20
    assert result.solutions[0].design.preparations[1].pcr_verification == 'verified'


@pytest.mark.asyncio
async def test_fresh_process_read_and_concurrent_retry(tmp_path):
    import asyncio
    import subprocess
    import sys
    async with client_store(tmp_path) as (client, _):
        native = run_workflow(REQUEST_ADAPTER.validate_python(request().model_dump()))
        body = SaveDesignRequest(selection=freeze_selection(native, 'fixed'), name='Concurrent', idempotency_key='concurrent').model_dump(mode='json')
        first, second = await asyncio.gather(*[client.post('/api/molbio/assembly/golden-gate/design/save', json=body) for _ in range(2)])
        assert first.status_code == second.status_code == 200, (first.text, second.text)
        assert first.json() == second.json()
        output = tmp_path / 'fresh.json'
        script = '''import asyncio, json, sys
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from services.assembly import golden_gate_workflow_persistence as owner
async def main():
    def forbidden(*a, **k): raise AssertionError("reopen attempted computation")
    owner.run_workflow = forbidden
    engine=create_async_engine("sqlite+aiosqlite:///"+sys.argv[1])
    async with async_sessionmaker(engine)() as session:
        value=await owner.read_workup(session,sys.argv[2])
        Path(sys.argv[3]).write_text(value.model_dump_json())
    await engine.dispose()
asyncio.run(main())
'''
        process = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'workups.db'), first.json()['operation_id'], str(output)], capture_output=True, text=True)
        assert process.returncode == 0, process.stderr
        assert json.loads(output.read_text()) == first.json()


@pytest.mark.asyncio
async def test_explicit_domestication_recalculates_and_retains_original(tmp_path, monkeypatch):
    from services.assembly.golden_gate_workflow_persistence import save_workup
    data = request(kind='synthesis').model_dump(mode='json')
    original = 'GGTCTC' + B[6:]
    data['sources'][1]['source']['sequence'] = original
    data['sources'][1]['source']['features'] = []
    data['target']['exact_sequence'] = None
    data['domestication'] = [dict(source_id='insert', settings=dict(enabled=True,
        editable_regions=[dict(start=0,end=6)], unwanted_sites=[dict(enzyme_id='BsaI',recognition_sequence='GGTCTC')], max_edits=1))]
    proposal = run_workflow(REQUEST_ADAPTER.validate_python(data))
    assert proposal.edits[0].proposal.status == 'proposal'
    assert not proposal.edits[0].accepted
    assert proposal.requested.sources[1].source.sequence == original
    data['domestication'][0]['accepted_sequence'] = proposal.edits[0].proposal.proposed_sequence
    accepted = run_workflow(REQUEST_ADAPTER.validate_python(data))
    assert accepted.selected_solution_id == 'fixed'
    assert accepted.solutions[0].fixed_request.sources[1].source.sequence != original
    assert accepted.edits[0].accepted
    def no_search(*a, **k): raise AssertionError('Save must not repeat edit search')
    monkeypatch.setattr('services.assembly.golden_gate_workflow.propose_domestication', no_search)
    async with client_store(tmp_path) as (_, sessions):
        async with sessions() as session:
            saved = await save_workup(session, SaveDesignRequest(selection=freeze_selection(accepted,'fixed'), name='Edited',idempotency_key='edited'))
            revisions = list((await session.scalars(select(MolecularRevision).where(MolecularRevision.operation_id == saved.operation_id))).all())
        assert saved.result.edits == accepted.edits
        assert any(r.snapshot['sequence'] == original for r in revisions)
        derived = next(r for r in revisions if r.provenance.get('transformation') == 'accepted_edit' and r.provenance.get('material_id') == 'source:insert')
        assert derived.provenance['parent_revision_id']
        assert derived.snapshot['sequence'] == data['domestication'][0]['accepted_sequence']


@pytest.mark.asyncio
async def test_split_save_freezes_candidate_and_origin_features(tmp_path, monkeypatch):
    from services.assembly.golden_gate_workflow_persistence import save_workup
    sequence = 'AATG' + A + 'GGAG' + B
    data = dict(task='split_target', target=dict(id='target',source=dict(kind='inline',sequence=sequence,topology='circular',
        features=[dict(id='junction-cds',type='CDS',name='crosses cut',segments=[dict(start=10,end=90)],strand=1,codon_start=2)])),
        enzyme=binding('BsaI'), windows=[dict(start=20,end=21),dict(start=80,end=81)],display_origin=9,
        preparation=dict(clamp='TT',spacer='A'),search=dict(ranking_mode='lexicographic',unique_classes=False,exclude_palindromes=False,seed=17))
    result = run_workflow(REQUEST_ADAPTER.validate_python(data))
    expected = result.solutions[0].design.solutions[0]
    def forbidden(*a, **k): raise AssertionError('Save reran optimizer')
    monkeypatch.setattr('services.assembly.golden_gate_fidelity.optimize_sequence_windows', forbidden)
    async with client_store(tmp_path) as (_, sessions):
        async with sessions() as session:
            saved = await save_workup(session, SaveDesignRequest(selection=freeze_selection(result,result.selected_solution_id),name='Split',idempotency_key='split'))
        actual = saved.result.solutions[0].design.solutions[0]
        assert actual.sequence == sequence[9:] + sequence[:9] == expected.sequence
        assert actual.features == expected.features
        assert saved.result.requested == result.requested
        assert saved.selection.authored_request.search.seed == 17


@pytest.mark.asyncio
@pytest.mark.parametrize('stock', [None, {'value': 20, 'unit': 'ng/uL'}])
async def test_reaction_owner_uses_retained_length_and_preserves_unknowns(tmp_path, stock):
    from fractions import Fraction
    data = request().model_dump(mode='json')
    data['reaction'] = dict(settings=dict(total_volume_uL=10), parts=[dict(part_id='insert',amount=dict(value=0.1,unit='pmol'),stock=stock)])
    result = run_workflow(REQUEST_ADAPTER.validate_python(data))
    from services.assembly.golden_gate_workflow_types import AssembleTask
    assert isinstance(result.requested, AssembleTask)
    assert result.requested.reaction is not None and result.reaction is not None
    assert result.requested.reaction.parts[0].length_bp is None
    assert result.reaction.request.parts[0].length_bp == len(B) + 4
    row = result.reaction.rows[0]
    assert row.requested_mass_ng == pytest.approx(float(Fraction(1,10) * 660 * (len(B)+4) / 1000))
    if stock is None:
        assert row.transfer_volume_uL is None
    else:
        assert row.transfer_volume_uL == pytest.approx(float(Fraction(1,10) * 660 * (len(B)+4) / 1000 / 20))
    async with client_store(tmp_path) as (client, _):
        body = SaveDesignRequest(selection=freeze_selection(result,'fixed'), name='Worksheet',idempotency_key='worksheet').model_dump(mode='json')
        response = await client.post('/api/molbio/assembly/golden-gate/design/save',json=body)
        assert response.status_code == 200, response.text
        assert response.json()['result']['reaction'] == result.reaction.model_dump(mode='json')


def test_production_router_is_mounted():
    from main import app
    paths = list(app.openapi()['paths'])
    for path in ('/api/molbio/assembly/golden-gate/design', '/api/molbio/assembly/golden-gate/design/save', '/api/molbio/assembly/golden-gate/design/{operation_id}'):
        assert paths.count(path) == 1
