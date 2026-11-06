"""Real native migrations/guards: HTTP save, cold replay and revision reuse.

PL931 request is retained from the demonstrated browser failure (golden-gate-http.json).
No live records, create_all-only schema, or mocked scientific computation.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import asyncio
import io
import json
from zipfile import ZipFile

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from molbio_database import create_molbio_engine, init_molbio_db, get_molbio_session, make_molbio_session_factory
from molbio_models import MolecularOperation, MolecularRevision, MolecularOperationOutput, NucleotideSequence
from routers import molbio_ops, molbio_golden_gate_design, nucleotide_sequences
from services.assembly import golden_gate_workflow_persistence as owner
from services.assembly.golden_gate_workflow import freeze_selection
from services.assembly.golden_gate_workflow_types import WorkflowResult, SaveDesignRequest
from services.assembly.golden_gate_workflow_wire import project_workflow, expand_workflow
from test_golden_gate_wire_receiving import fixture
from test_golden_gate_design_core import request

BASE = '/api/molbio/assembly/golden-gate/design'
PL931_BROWSER_REQUEST = {'schema_version': 'bms.golden-gate-wire.v1',
 'kind': 'request',
 'payload': {'schema_version': 'bms.golden-gate-design.v1',
             'task': 'split_target',
             'target': {'id': 'target',
                        'source': {'sequence': {'sequence_ref': 0},
                                   'topology': 'circular',
                                   'features': [{'id': 'record-4-feature-2',
                                                 'type': 'terminator',
                                                 'name': 'lambda t0 terminator',
                                                 'segments': [{'start': 207,
                                                               'end': 302,
                                                               'wraps_origin': False}],
                                                 'strand': -1,
                                                 'qualifiers': {'label': ['lambda t0 terminator'],
                                                                'note': ['transcription terminator from '
                                                                         'phage lambda']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None},
                                                {'id': 'record-4-feature-3',
                                                 'type': 'CDS',
                                                 'name': 'NeoR/KanR',
                                                 'segments': [{'start': 332,
                                                               'end': 1127,
                                                               'wraps_origin': False}],
                                                 'strand': -1,
                                                 'qualifiers': {'codon_start': ['1'],
                                                                'gene': ["aph(3')-II (or nptII)"],
                                                                'product': ['aminoglycoside '
                                                                            'phosphotransferase from Tn5'],
                                                                'label': ['NeoR/KanR'],
                                                                'note': ['confers resistance to neomycin, '
                                                                         'kanamycin, and G418 '
                                                                         '(Geneticin(R))'],
                                                                'translation': ['MIEQDGLHAGSPAAWVERLFGYDWAQQTIGCSDAAVFRLSAQGRPVLFVKTDLSGALNELQDEAARLSWLATTGVPCAAVLDVVTEAGRDWLLLGEVPGQDLLSSHLAPAEKVSIMADAMRRLHTLDPATCPFDHQAKHRIERARTRMEAGLVDQDDLDEEHQGLAPAELFARLKARMPDGEDLVVTHGDACLPNIMVENGRFSGFIDCGRLGVADRYQDIALATRDIAEELGGEWADRFLVLYGIAAPDSQRIAFYRLLDEFF']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None},
                                                {'id': 'record-4-feature-4',
                                                 'type': 'enhancer',
                                                 'name': 'CMV enhancer',
                                                 'segments': [{'start': 1491,
                                                               'end': 1871,
                                                               'wraps_origin': False}],
                                                 'strand': 1,
                                                 'qualifiers': {'label': ['CMV enhancer'],
                                                                'note': ['human cytomegalovirus immediate '
                                                                         'early enhancer']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None},
                                                {'id': 'record-4-feature-5',
                                                 'type': 'promoter',
                                                 'name': 'CMV promoter',
                                                 'segments': [{'start': 1871,
                                                               'end': 2075,
                                                               'wraps_origin': False}],
                                                 'strand': 1,
                                                 'qualifiers': {'label': ['CMV promoter'],
                                                                'note': ['human cytomegalovirus (CMV) '
                                                                         'immediate early promoter']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None},
                                                {'id': 'record-4-feature-6',
                                                 'type': 'polyA_signal',
                                                 'name': 'bGH poly(A) signal',
                                                 'segments': [{'start': 3386,
                                                               'end': 3611,
                                                               'wraps_origin': False}],
                                                 'strand': 1,
                                                 'qualifiers': {'label': ['bGH poly(A) signal'],
                                                                'note': ['bovine growth hormone '
                                                                         'polyadenylation signal']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None},
                                                {'id': 'record-4-feature-7',
                                                 'type': 'rep_origin',
                                                 'name': 'f1 ori',
                                                 'segments': [{'start': 3656,
                                                               'end': 4085,
                                                               'wraps_origin': False}],
                                                 'strand': 1,
                                                 'qualifiers': {'direction': ['RIGHT'],
                                                                'label': ['f1 ori'],
                                                                'note': ['f1 bacteriophage origin of '
                                                                         'replication; arrow indicates '
                                                                         'direction of (+) strand '
                                                                         'synthesis']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None},
                                                {'id': 'record-4-feature-8',
                                                 'type': 'rep_origin',
                                                 'name': 'SV40 ori',
                                                 'segments': [{'start': 4154,
                                                               'end': 4290,
                                                               'wraps_origin': False}],
                                                 'strand': 1,
                                                 'qualifiers': {'label': ['SV40 ori'],
                                                                'note': ['SV40 origin of replication']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None},
                                                {'id': 'record-4-feature-9',
                                                 'type': 'polyA_signal',
                                                 'name': 'SV40 poly(A) signal',
                                                 'segments': [{'start': 4423,
                                                               'end': 4545,
                                                               'wraps_origin': False}],
                                                 'strand': 1,
                                                 'qualifiers': {'label': ['SV40 poly(A) signal'],
                                                                'note': ['SV40 polyadenylation signal']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None},
                                                {'id': 'record-4-feature-10',
                                                 'type': 'rep_origin',
                                                 'name': 'ori',
                                                 'segments': [{'start': 4995,
                                                               'end': 5584,
                                                               'wraps_origin': False}],
                                                 'strand': -1,
                                                 'qualifiers': {'direction': ['LEFT'],
                                                                'label': ['ori'],
                                                                'note': ['high-copy-number '
                                                                         'ColE1/pMB1/pBR322/pUC origin of '
                                                                         'replication']},
                                                 'codon_start': None,
                                                 'status': 'intact',
                                                 'frame_preserved': None}],
                                   'kind': 'inline'}},
             'enzyme': {'enzyme_id': 'BsaI',
                        'catalog_id': 'biopython-rebase-404-bms-v1',
                        'catalog_sha256': 'e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f'},
             'windows': [{'start': 0, 'end': 698}, {'start': 2094, 'end': 3490}],
             'fixed_positions': [None, None],
             'fixed_overhangs': [],
             'required': [],
             'excluded': [],
             'protected_regions': [],
             'frame_constraints': [],
             'min_fragment_length': 1,
             'max_fragment_length': None,
             'target_fragment_length': None,
             'display_origin': 0,
             'preparation': {'kind': 'synthesis',
                             'clamp': 'TT',
                             'spacer': 'A',
                             'forward_anneal_length': 20,
                             'reverse_anneal_length': 20,
                             'qc_min_binding_anneal_length': 12},
             'terminal_right_fusion': None,
             'primer_settings': {'schema_version': 'primer-tm-settings-v1',
                                 'algorithm': 'nn_santalucia_hicks_2004',
                                 'salt_correction': 'owczarzy_2008',
                                 'primer_concentration_nM': 250,
                                 'template_concentration_nM': 0,
                                 'na_mM': 50,
                                 'k_mM': 0,
                                 'tris_mM': 0,
                                 'mg_mM': 1.5,
                                 'dntps_mM': 0.6,
                                 'dmso_percent': 0,
                                 'formamide_percent': 0,
                                 'self_complementary': False},
             'fidelity': {'dataset_id': 'pryor2020-s001',
                          'condition_use': 'reference',
                          'include_pair_observations': False},
             'search': {'seed': 0,
                        'evaluation_budget': 10000,
                        'restarts': 8,
                        'exact_limit': 10000,
                        'alternatives': 3,
                        'unique_classes': True,
                        'exclude_palindromes': True,
                        'ranking_mode': 'empirical'},
             'reaction': None},
 'sequences': ['GAAGATCCTTTGATCTTTTCTACGGGGTCTGACGCTCAGTGGAACGAAAACTCACGTTAAGGGATTTTGGTCATGAGATTATCAAAAAGGATCTTCACCTAGATCCTTTTAAATTAAAAATGAAGTTTTAAATCAATCTAAAGTATATATGAGTAAACTTGGTCTGACAGTTACCAATGCTTGACATTGGTCATGGCTAGTGCTTGGATTCTCACCAATAAAAAACGCCCGGCGGCAACCGAGCGTTCTGAACAAATCCAGATGGAGTTCTGAGGTCATTACTGGATCTATCAACAGGAGTCCAAGCCAATTCTCGAACCCCAGAGTCCCGCTCAGAAGAACTCGTCAAGAAGGCGATAGAAGGCGATGCGCTGCGAATCGGGAGCGGCGATACCGTAAAGCACGAGGAAGCGGTCAGCCCATTCGCCGCCAAGCTCTTCAGCAATATCACGGGTAGCCAACGCTATGTCCTGATAGCGGTCCGCCACACCCAGCCGGCCACAGTCGATGAATCCAGAAAAGCGGCCATTTTCCACCATGATATTCGGCAAGCAGGCATCGCCGTGGGTCACGACGAGATCCTCGCCGTCGGGCATACGCGCCTTGAGCCTGGCGAACAGTTCGGCTGGCGCGAGCCCCTGATGCTCTTCGTCCAGATCATCCTGATCGACAAGACCGGCTTCCATCCGAGTACGTGCTCGCTCGATGCGATGTTTCGCTTGGTGGTCGAATGGGCAGGTAGCCGGATCAAGCGTATGCAGCCGCCGCATTGCATCAGCCATGATGGATACTTTCTCGGCAGGAGCAAGGTGAGATGACAGGAGATCCTGCCCCGGCACTTCGCCCAATAGCAGCCAGTCCCTTCCCGCTTCAGTGACAACGTCGAGCACAGCCGCGCAAGGAACGCCCGTCGTGGCCAGCCACGATAGCCGCGCTGCCTCGTCTTGCAGTTCATTCAGGGCACCGGACAGGTCGGTCTTGACAAAAAGAACCGGGCGACCCTGCGCTGACAGCCGGAACACGGCGGCATCAGAGCAGCCGATTGTCTGTTGTGCCCAGTCATAGCCGAATAGCCTCTCCACCCAAGCCGCCGGAGAACCTGCGTGCAATCCATCTTGTTCAATCATGCGAAACGATCCTCATCCTGTCTCTTGATCAGATCTTGATCCCCTGCGCCATCAGATCCTTGGCGGCAAGAAAGCCATCCAGTTTACTTTGCAGGGCTTCCCAACCTTACCAGAGGGCGGCCCAACTGGCAATTCCGACGTCGATCTCCCGATCCCCTATGGTGCACTCTCAGTACAATCTGCTCTGATGCCGCATAGTTAAGCCAGTATCTGCTCCCTGCTTGTGTGTTGGAGGTCGCTGAGTAGTGCGCGAGCAAAATTTAAGCTACAACAAGGCAAGGCTTGACCGACAATTGCATGAAGAATCTGCTTAGGGTTAGGCGTTTTGCGCTGCTTCGCGATGTACGGGCCAGATATACGCGTTGACATTGATTATTGACTAGTTATTAATAGTAATCAATTACGGGGTCATTAGTTCATAGCCCATATATGGAGTTCCGCGTTACATAACTTACGGTAAATGGCCCGCCTGGCTGACCGCCCAACGACCCCCGCCCATTGACGTCAATAATGACGTATGTTCCCATAGTAACGCCAATAGGGACTTTCCATTGACGTCAATGGGTGGAGTATTTACGGTAAACTGCCCACTTGGCAGTACATCAAGTGTATCATATGCCAAGTACGCCCCCTATTGACGTCAATGACGGTAAATGGCCCGCCTGGCATTATGCCCAGTACATGACCTTATGGGACTTTCCTACTTGGCAGTACATCTACGTATTAGTCATCGCTATTACCATGGTGATGCGGTTTTGGCAGTACATCAATGGGCGTGGATAGCGGTTTGACTCACGGGGATTTCCAAGTCTCCACCCCATTGACGTCAATGGGAGTTTGTTTTGGCACCAAAATCAACGGGACTTTCCAAAATGTCGTAACAACTCCGCCCCATTGACGCAAATGGGCGGTAGGCGTGTACGGTGGGAGGTCTATATAAGCAGAGCTCTCTGGCTAACTAGAGAACCCACTGCTTACTGGCTTATCGAAATTAATACGACTCACTATAGGGAGTCCCAAGCTGGCTAGCGCCACCATGGAAACGGACACCCTGCTGCTGTGGGTGCTGTTGTTGTGGGTGCCAGGATCTACAGGCGACGGTACCTGCCCCAGCCAGTGCAGCTGCTCCGGCACAGAAGTGCATTGCCAGAGAAAGTCCCTGGCCTCTGTGCCTGCCGGCATTCCTACCACAACCAGAGTGCTGTACCTGCACGTGAACGAGATCACCAAGTTCGAGCCCGGCGTGTTCGACAGACTGGTCAATCTCCAGCAGCTGTACCTCGGCGGCAATCAGCTTTCTGCTCTGCCCGATGGGGTGTTCGATAGGCTGACCCAGCTGACCAGACTGGACCTGTATAACAATCAGCTGACCGTGCTGCCAGCCGGCGTTTTCGATCGGCTCGTGAATCTCCAGACTCTGGACCTGCACAACAACCAGTTGAAGTCTATCCCCAGAGGGGCCTTCGACAACCTGAAGTCTCTGACCCACATCTGGCTGTTCGGCAACCCCTGGGATTGCGCCTGTAGCGACATCCTGTATCTGTCTGGCTGGCTGGGACAGCACGCCGGCAAAGAACAAGGACAGGCTGTGTGCAGCGGCACCAATACTCCAGTCAGAGCCGTGACCGAGGCCAGCACAAGCCCTTCTAAATGCCCTACCGGTAGGTGGCCAGAGTCTCCAAAGGCACAGGCTTCTTCCGTCCCAACAGCTCAGCCACAGGCTGAGGGAAGCCTGGCTAAAGCCACTACAGCCCCTGCTACAACACGAAATACAGGTAGGGGAGGAGAGGAGAAGAAGAAAGAGAAAGAGAAAGAGGAACAGGAGGAGCGGGAAACCAAGACCCCTGAATGTCCTCGGTGGCCCGAGTCCCCAAAGGCTCAGGCCAGCAGCGTGCCCACCGCCCAGCCTCAGGCCGAGGGCAGCCTGGCCAAGGCCACAACAGCCCCTGCCACCACCAGAAACACCGGCAGAGGCGGAGAAGAAAAGAAAAAGGAAAAAGAGAAGGAGGAGCAGGAGGAAAGAGAGACAAAGACCCCTGAGTGCCCTGGATCCTCTACACAGGGCGAAGTGACCAGAAAGGTGACAGTGAACGTGCTGAGCCCTAGATACGAGATCGTGATCATCACCGTGGTCGCCGCCGCTGTGATCATGGGCACCGCCGGCCTGAGCACCTACCTGTATAACAGACAGCGGAAGATCAAGAAATGAGCGGCCGCTCGAGTCTAGAGGGCCCGTTTAAACCCGCTGATCAGCCTCGACTGTGCCTTCTAGTTGCCAGCCATCTGTTGTTTGCCCCTCCCCCGTGCCTTCCTTGACCCTGGAAGGTGCCACTCCCACTGTCCTTTCCTAATAAAATGAGGAAATTGCATCGCATTGTCTGAGTAGGTGTCATTCTATTCTGGGGGGTGGGGTGGGGCAGGACAGCAAGGGGGAGGATTGGGAAGTCAATAGCAGGCATGCTGGGGATGCGGTGGGCTCTATGGCTTCTGAGGCGGAAAGAACCAGCTGGGGCTCTAGGGGGTATCCCCACGCGCCCTGTAGCGGCGCATTAAGCGCGGCGGGTGTGGTGGTTACGCGCAGCGTGACCGCTACACTTGCCAGCGCCCTAGCGCCCGCTCCTTTCGCTTTCTTCCCTTCCTTTCTCGCCACGTTCGCCGGCTTTCCCCGTCAAGCTCTAAATCGGGGGCTCCCTTTAGGGTTCCGATTTAGTGCTTTACGGCACCTCGACCCCAAAAAACTTGATTAGGGTGATGGTTCACGTAGTGGGCCATCGCCCTGATAGACGGTTTTTCGCCCTTTGACGTTGGAGTCCACGTTCTTTAATAGTGGACTCTTGTTCCAAACTGGAACAACACTCAACCCTATCTCGGTCTATTCTTTTGATTTATAAGGGATTTTGCCGATTTCGGCCTATTGGTTAAAAAATGAGCTGATTTAACAAAAATTTAACGCGAATTAATTCTGTGGAATGTGTGTCAGTTAGGCTCAATTAGTCAGCAACCATAGTCCCGCCCCTAACTCCGCCCATCCCGCCCCTAACTCCGCCCAGTTCCGCCCATTCTCCGCCCCATGGCTGACTAATTTTTTTTATTTATGCAGAGGCCGAGGCCGCCTCTGCCTCTGAGCTATTCCAGAAGTAGTGAGGAGGCTTTTTTGGAGGCCTAGGCTTTTGCAAAAAGCTCCCGGGAGCTTGTATATCCATTTTCGGAGGTTGGGCTTCGGAATCGTTTTCCGGGACGCCGGCTGGATGATCCTCCAGCGCGGGGATCTCATGCTGGAGTTCTTCGCCCACCCCAACTTGTTTATTGCAGCTTATAATGGTTACAAATAAAGCAATAGCATCACAAATTTCACAAATAAAGCATTTTTTTCACTGCATTCTAGTTGTGGTTTGTCCAAACTCATCAATGTATCTTATCATGTCTGTATACCGTCGACCTCTAGCTAGAGCTTGGCGTAATCATGGTCATAGCTGTTTCCTGTGTGAAATTGTTATCCGCTCACAATTCCACACAACATACGAGCCGGAAGCATAAAGTGTAAAGCCTGGGGTGCCTAATGAGTGAGCTAACTCACATTAATTGCGTTGCGCTCACTGCCCGCTTTCCAGTCGGGAAACCTGTCGTGCCAGCTGCATTAATGAATCGGCCAACGCGCGGGGAGAGGCGGTTTGCGTATTGGGCGCTCTTCCGCTTCCTCGCTCACTGACTCGCTGCGCTCGGTCGTTCGGCTGCGGCGAGCGGTATCAGCTCACTCAAAGGCGGTAATACGGTTATCCACAGAATCAGGGGATAACGCAGGAAAGAACATGTGAGCAAAAGGCCAGCAAAAGGCCAGGAACCGTAAAAAGGCCGCGTTGCTGGCGTTTTTCCATAGGCTCCGCCCCCCTGACGAGCATCACAAAAATCGACGCTCAAGTCAGAGGTGGCGAAACCCGACAGGACTATAAAGATACCAGGCGTTTCCCCCTGGAAGCTCCCTCGTGCGCTCTCCTGTTCCGACCCTGCCGCTTACCGGATACCTGTCCGCCTTTCTCCCTTCGGGAAGCGTGGCGCTTTCTCATAGCTCACGCTGTAGGTATCTCAGTTCGGTGTAGGTCGTTCGCTCCAAGCTGGGCTGTGTGCACGAACCCCCCGTTCAGCCCGACCGCTGCGCCTTATCCGGTAACTATCGTCTTGAGTCCAACCCGGTAAGACACGACTTATCGCCACTGGCAGCAGCCACTGGTAACAGGATTAGCAGAGCGAGGTATGTAGGCGGTGCTACAGAGTTCTTGAAGTGGTGGCCTAACTACGGCTACACTAGAAGAACAGTATTTGGTATCTGCGCTCTGCTGAAGCCAGTTACCTTCGGAAAAAGAGTTGGTAGCTCTTGATCCGGCAAACAAACCACCGCTGGTAGCGGTGGTTTTTTTGTTTGCAAGCAGCAGATTACGCGCAGAAAAAAAGGATCTCAA'],
 'materials': []}


@asynccontextmanager
async def native_store(path, initialize=False):
    engine = create_molbio_engine('sqlite+aiosqlite:///' + str(path))
    if initialize:
        await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    async def dependency():
        async with sessions() as session:
            yield session
    app = FastAPI()
    for router in (molbio_ops.router, molbio_golden_gate_design.router, nucleotide_sequences.router):
        app.include_router(router)
    app.dependency_overrides[get_molbio_session] = dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://native') as client:
            yield client, sessions
    finally:
        await engine.dispose()


async def history(sessions):
    # Exact native history, including audit/outbox, must not change on retry/conflict.
    async with sessions() as session:
        tables = ['molecular_operations', 'molecular_revisions', 'molecular_operation_inputs',
                  'molecular_operation_outputs', 'molecular_documents', 'nucleotide_sequences',
                  'molbio_audit_events', 'molbio_outbox_events']
        return {table: [tuple(row) for row in (await session.execute(text('SELECT * FROM ' + table + ' ORDER BY id'))).all()]
                for table in tables}


def forbidden(*args, **kwargs):
    raise AssertionError('Retained read/retry must not compute science')


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['pl931-browser', 'moved-public', 'edited-browser'])
async def test_native_http_save_cold_read_retry_and_revision_reuse(tmp_path, monkeypatch, case):
    normalized = case != 'moved-public'
    body = expand_workflow(deepcopy(PL931_BROWSER_REQUEST), 'request') if case == 'pl931-browser' else fixture('edited' if case == 'edited-browser' else 'split')
    if case == 'moved-public':
        body.update(windows=[dict(start=24,end=25),dict(start=84,end=85)], fixed_positions=[24,84], display_origin=9)
    path = tmp_path / 'native.db'
    async with native_store(path, initialize=True) as (client, sessions):
        async with sessions() as session:
            assert (await session.execute(text('PRAGMA foreign_keys'))).scalar_one() == 1
            guards = (await session.execute(text("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"))).all()
            assert any(row[0] == 'molbio_immutable_molecular_operations_update' for row in guards)
        if case == 'edited-browser':
            proposal = await client.post(BASE, json=body)
            assert proposal.status_code == 200, proposal.text
            body['domestication'][0]['accepted_sequence'] = proposal.json()['edits'][0]['proposal']['proposed_sequence']
        suffix = '?view=normalized' if normalized else ''
        wire = lambda value, kind: project_workflow(value, kind).model_dump(mode='json') if normalized else value
        response = await client.post(BASE + suffix, json=wire(body, 'request'))
        assert response.status_code == 200, response.text
        preview = expand_workflow(response.json(), 'result') if normalized else response.json()
        result = WorkflowResult.model_validate(preview)
        selected = next(s for s in result.solutions if s.id == result.selected_solution_id)
        save = SaveDesignRequest(selection=freeze_selection(result, selected.id), name=case, idempotency_key=case).model_dump(mode='json')
        # Save reconstructs fixed preparations, never repeats split/edit optimization.
        with monkeypatch.context() as m:
            m.setattr('services.assembly.golden_gate_fidelity.optimize_sequence_windows', forbidden)
            m.setattr('services.assembly.golden_gate_workflow.propose_domestication', forbidden)
            response = await client.post(BASE + '/save' + suffix, json=wire(save, 'save'))
        assert response.status_code == 200, response.text
        saved = expand_workflow(response.json(), 'saved') if normalized else response.json()
        assert saved['selection'] == save['selection']
        assert saved['result']['requested'] == result.requested.model_dump(mode='json')
        assert saved['result']['solutions'][0]['design'] == selected.design.model_dump(mode='json')
        assert saved['result']['edits'] == preview['edits']
        initial_history = await history(sessions)
    # Fully dispose the original engine; reconnect to the migrated native file.
    async with native_store(path) as (client, sessions):
        with monkeypatch.context() as m:
            m.setattr(owner, 'run_workflow', forbidden)
            for suffix in ('', '?view=normalized'):
                reopened = await client.get(BASE + '/' + saved['operation_id'] + suffix)
                assert reopened.status_code == 200, reopened.text
                actual = expand_workflow(reopened.json(), 'saved') if suffix else reopened.json()
                assert actual == saved
                replay = await client.post(BASE + '/save' + suffix, json=project_workflow(save,'save').model_dump(mode='json') if suffix else save)
                assert replay.status_code == 200, replay.text
                assert (expand_workflow(replay.json(),'saved') if suffix else replay.json()) == saved
            exported = await client.get(BASE + '/' + saved['operation_id'] + '/export')
            assert exported.status_code == 200, exported.text
            with ZipFile(io.BytesIO(exported.content)) as archive:
                assert json.loads(archive.read('workflow.json'))['result'] == saved['result']
            retries = await asyncio.gather(*[client.post(BASE + '/save', json=save) for _ in range(2)])
            assert all(r.status_code == 200 and r.json() == saved for r in retries)
            conflict = deepcopy(save); conflict['name'] += ' changed'
            response = await client.post(BASE + '/save', json=conflict)
            assert response.status_code == 409
        assert await history(sessions) == initial_history
        detail = await client.get('/api/molbio/operations/' + saved['operation_id'])
        assert detail.status_code == 200, detail.text
        outputs = detail.json()['outputs']
        assert next(o for o in outputs if o['role'] == 'product')['revision_id'] == saved['product_revision_id']
        retained_design = WorkflowResult.model_validate(saved['result']).solutions[0].design
        expected_ids = {m.id for m in retained_design.materials if m.stage != 'source'}
        expected_ids |= {mid for d in retained_design.digests for mid in d.fragment_material_ids}
        expected_ids |= {f"source:{e['source_id']}" for e in save['selection']['accepted_edits']}
        async with sessions() as session:
            stored_outputs = (await session.scalars(select(MolecularOperationOutput).where(MolecularOperationOutput.operation_id == saved['operation_id']))).all()
            assert {o.snapshot['material_id'] for o in stored_outputs if o.role == 'intermediate'} == expected_ids
            for output in stored_outputs:
                revision = await session.get(MolecularRevision, output.revision_id)
                assert revision.operation_id == saved['operation_id']
                assert hashlib.sha256(revision.snapshot['sequence'].encode()).hexdigest() == revision.content_sha256
                if output.role == 'intermediate':
                    mid = output.snapshot['material_id']
                    assert revision.snapshot['sequence'] == owner.design_material(retained_design, mid).sequence
                response = await client.get(f'/api/molbio/sequences/{revision.document_id}/revisions/{revision.id}')
                assert response.status_code == 200, response.text
                assert response.json()['snapshot'] == revision.snapshot
            operation = await session.get(MolecularOperation, saved['operation_id'])
            references = deepcopy(operation.parameters['dna_references'])
            assert references
            for ref in references:
                assert await session.get(MolecularRevision, ref['revision_id']) is not None
            current_guards = (await session.execute(text("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"))).all()
            assert current_guards == guards
            assert (await session.execute(text('PRAGMA foreign_key_check'))).all() == []
        # Existing guards still reject both mutation forms on both history owners.
        for table, identity in [('molecular_operations', saved['operation_id']), ('molecular_revisions', saved['product_revision_id'])]:
            for sql in [f"UPDATE {table} SET id=id WHERE id=:id", f"DELETE FROM {table} WHERE id=:id"]:
                async with sessions() as session:
                    with pytest.raises(IntegrityError, match='immutable'):
                        await session.execute(text(sql), dict(id=identity))
                    await session.rollback()
        assert await history(sessions) == initial_history
        # Feed retained source identities back through the public planner and Save.
        reuse = deepcopy(saved['selection']['request'])
        async with sessions() as session:
            for source in reuse['sources']:
                ref = next(r for r in references if r['path'] == ['selection','request','sources',reuse['sources'].index(source),'source','sequence'])
                source['source'] = dict(kind='molecular_revision', revision_id=ref['revision_id'])
        response = await client.post(BASE, json=reuse)
        assert response.status_code == 200, response.text
        reused_result = WorkflowResult.model_validate(response.json())
        assert reused_result.selected_solution_id is not None
        reuse_save = SaveDesignRequest(selection=freeze_selection(reused_result, reused_result.selected_solution_id), name='reused', idempotency_key='reused').model_dump(mode='json')
        response = await client.post(BASE + '/save', json=reuse_save)
        assert response.status_code == 200, response.text
        reused = response.json()
        assert reused['result']['solutions'][0]['design']['solutions'][0]['sequence'] == saved['result']['solutions'][0]['design']['solutions'][0]['sequence']
        detail = await client.get('/api/molbio/operations/' + reused['operation_id'])
        assert {s['source']['revision_id'] for s in reuse['sources']} <= {r['revision_id'] for r in detail.json()['inputs']}


@pytest.mark.asyncio
async def test_native_save_failure_rolls_back_entire_history(tmp_path, monkeypatch):
    async with native_store(tmp_path / 'rollback.db', initialize=True) as (client, sessions):
        response = await client.post(BASE, json=request().model_dump(mode='json'))
        assert response.status_code == 200, response.text
        result = WorkflowResult.model_validate(response.json())
        save = SaveDesignRequest(selection=freeze_selection(result,'fixed'), name='atomic', idempotency_key='atomic').model_dump(mode='json')
        before = await history(sessions)
        original = owner.record_sequence_revision
        async def fail_product(*args, **kwargs):
            if kwargs['change_kind'] == 'operation_result':
                raise ValueError('injected failure after intermediate retention')
            return await original(*args, **kwargs)
        with monkeypatch.context() as m:
            m.setattr(owner, 'record_sequence_revision', fail_product)
            response = await client.post(BASE + '/save', json=save)
            assert response.status_code == 422, response.text
        assert await history(sessions) == before
        response = await client.post(BASE + '/save', json=save)
        assert response.status_code == 200, response.text
