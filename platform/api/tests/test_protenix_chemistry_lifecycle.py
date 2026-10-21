"""Chemistry survives real HTTP admission, SQLite readback and retry.

All stores/results are disposable; existing admission fixture disables runtime
execution. No model, MSA provider or GPU work is launched.
"""
from copy import deepcopy
import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job
from routers import jobs
from services import nextflow
from scripts.prepare_protenix_msa import load_native_protenix_input
from test_core_protein_scientific_admission import admission


def params():
    return {
        'protenix_model_weights': 'protenix-v2', 'protenix_seeds': '42',
        'protenix_n_sample': 5, 'protenix_n_step': 200, 'protenix_n_cycle': 10,
        'protenix_use_msa': True, 'protenix_use_template': False,
        'msa_provider': 'colabfold_api', 'protenix_msa_backend': 'colabfold_api',
        'msa_cache_only': True,  # Explicit supplied-MSA, no provider submission.
        'run_frustrampnn': False,
        'sequence': 'ACD:YAQGTFTSDYSILLDKKAQAAFIEYLLEGG',
        'sequence_name': 'chemistry-roundtrip',
        'complex_components': [
            {'id': 'A', 'type': 'protein', 'sequence': 'ACD', 'count': 1,
             'unpairedMsa': '>query\nACD\n'},
            {'id': 'B', 'type': 'peptide', 'name': 'Retatrutide deposited segment',
             'sequence': 'YAQGTFTSDYSILLDKKAQAAFIEYLLEGG', 'count': 1,
             'modifications': [{'ptmPosition': 2, 'ptmType': 'CCD_AIB'},
                               {'ptmPosition': 13, 'ptmType': 'CCD_2ML'},
                               {'ptmPosition': 20, 'ptmType': 'CCD_AIB'}],
             'unpairedMsa': ''},
        ],
        'covalent_bonds': [],
        'constraint': {'contact': [{'entity1': 1, 'copy1': 1, 'position1': 1,
                                    'entity2': 2, 'copy2': 1, 'position2': 1,
                                    'max_distance': 8.0}]},
    }


@pytest.mark.asyncio
async def test_http_sqlite_clone_resubmit_compiled_chemistry(admission, monkeypatch, tmp_path):
    # Fresh sessions for every HTTP request prove this is persisted state, not
    # a shared SQLAlchemy identity-map echo.
    sessions = async_sessionmaker(admission.bind, expire_on_commit=False)
    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    async def isolated_session():
        async with sessions() as session:
            yield session
    app.dependency_overrides[jobs.get_session] = isolated_session
    app.dependency_overrides[jobs.get_experiment_session] = isolated_session
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    science = params()
    science['unexpected_nested_science'] = {'iterations': 999}
    payload = {'name': 'chemistry-roundtrip', 'model_id': 'protenix', 'mode': 'complex',
               'params': science, 'execution_target_id': None}
    identities = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        created = await client.post('/api/jobs', json=payload)
        assert created.status_code == 201, created.text
        original_id = created.json()['id']
        identities.append(original_id)
        detail = await client.get('/api/jobs/' + original_id)
        assert detail.status_code == 200, detail.text
        for key, value in science.items():
            assert detail.json()['params'][key] == value
        clone = deepcopy(payload)
        clone['name'] = 'renamed-clone'
        clone['params'] = {key: detail.json()['params'][key] for key in science}
        cloned = await client.post('/api/jobs', json=clone)
        assert cloned.status_code == 201, cloned.text
        identities.append(cloned.json()['id'])
        async with sessions() as session:
            original = await session.get(Job, original_id)
            original.status = original.queue_status = 'failed'
            await session.commit()
        retried = await client.post('/api/jobs/' + original_id + '/resubmit', json={})
        assert retried.status_code == 200, retried.text
        identities.append(retried.json()['new_job_id'])
        for job_id in identities:
            fresh = await client.get('/api/jobs/' + job_id)
            assert fresh.status_code == 200, fresh.text
            for key, value in science.items():
                assert fresh.json()['params'][key] == value
            async with sessions() as session:
                row = await session.get(Job, job_id)
                for key, value in science.items():
                    assert row.params[key] == value
                assert 'unexpected_nested_science' in row.provenance['parameter_diagnostics']['settings_outside_catalog']
                requested = row.provenance['core_protein_requested_params']
                for key in ('complex_components', 'covalent_bonds', 'constraint'):
                    assert requested[key] == science[key]
            output = tmp_path / ('compiled-' + job_id)
            invocation = nextflow.compile_nextflow_invocation('protenix', 'complex', fresh.json()['params'], str(output))
            invocation.materialize_inputs(output)
            native = load_native_protenix_input(invocation.native_parameters)[0]
            assert native['sequences'][1]['proteinChain']['modifications'] == science['complex_components'][1]['modifications']
            assert native['constraint'] == science['constraint']
            assert native['covalent_bonds'] == []
            assert native['modelSeeds'] == [42]
        bad = deepcopy(payload)
        bad['params']['complex_components'][1]['modifications'][0]['ptmPosition'] = '2'
        rejected = await client.post('/api/jobs', json=bad)
        assert rejected.status_code == 422, rejected.text
    assert len(set(identities)) == 3
