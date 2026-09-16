"""TEST structures, not inference: real producer/parser/ingester/SQLite/profile replay."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, Design, Job
from services.analysis_registry import build_fampnn_psce_profile_signature, normalize_fampnn_psce_profile_params, get_analysis_definition
from services.analysis_subprocess import _compute_fampnn_psce_profile
from services.result_ingester import ingest_loose_files, _extract_fampnn_metrics
from services.structure_utils import fampnn_psce_authority

ROOT = Path(__file__).resolve().parents[3]


def fixture_pdb(path):
    # Unequal chain lengths, alanine only scored with CB, and distinct CB/OG.
    rows = []
    for chain, resnum, name, cb, other in [('A', 1, 'ALA', 1, None), ('A', 2, 'SER', 2, 4), ('B', 1, 'SER', 8, 12)]:
        for atom, score in [('N', 0), ('CA', 0), ('C', 0), ('O', 0), ('CB', cb)] + ([('OG', other)] if other is not None else []):
            serial = len(rows) + 1
            rows.append(f'ATOM  {serial:5d} {atom:^4s} {name} {chain}{resnum:4d}    {serial:8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{score:6.2f}          {atom[0]:>2s}\n')
    path.write_text(''.join(rows) + 'END\n')


@pytest.mark.asyncio
@pytest.mark.parametrize('scope,expected', [('A', 4.0), ('all_chains', 8.0)])
async def test_producer_ingest_persist_filter_profile_replay(tmp_path, monkeypatch, scope, expected):
    output = tmp_path / 'outputs'
    output.mkdir()
    pdb = output / 'test_seq_0.pdb'
    fixture_pdb(pdb)
    subprocess.run([sys.executable, str(ROOT/'scripts/analyse_fampnn.py'), '--input_dir', str(output), '--out_dir', str(output), '--chain_id', scope, '--ignore_cbeta'], check=True)
    sidecar = json.loads(pdb.with_suffix('.json').read_text())
    assert sidecar['fampnn_avg_psce'] == expected
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/db.sqlite')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            job = Job(id='test-fampnn', name='TEST fixture', model_id='fampnn_child', mode='sequence_design', stage_family='fampnn', status='completed', params={}, output_dir=str(output))
            session.add(job)
            await session.commit()
            assert await ingest_loose_files(job.id, output, session, current_job=job) == 1
            await session.commit()
        async with sessions() as session:
            design = (await session.scalars(select(Design))).one()
            assert design.fampnn_psce == expected
            assert design.confidence_metrics['fampnn']['psce_policy'] == sidecar['psce_policy']
            result, summary, _ = _compute_fampnn_psce_profile(design, normalize_fampnn_psce_profile_params({}))
            assert summary['avg_psce'] == expected
            assert result['scope'] == scope
            assert set(result['chains']) == ({'A'} if scope == 'A' else {'A', 'B'})
            assert summary['residue_count'] == (1 if scope == 'A' else 2)
            # Exercise actual persisted analysis queue, execution and cache reuse.
            from services import analysis_subprocess
            from services.analysis_runs import request_design_analysis
            monkeypatch.setattr(analysis_subprocess, 'async_session', sessions)
            run, reused = await request_design_analysis(session, design, 'fampnn_psce_profile', {})
            assert not reused
            run_id = run.id
            assert await analysis_subprocess._run_analysis(run_id) == 0
            session.expire_all()
            design = (await session.scalars(select(Design))).one()
            replay, reused = await request_design_analysis(session, design, 'fampnn_psce_profile', {})
            assert reused and replay.id == run_id and replay.status == 'completed'
            from paths import resolve_allowed_path
            cached = json.loads(resolve_allowed_path(replay.artifact_manifest['result_json']).read_text())
            assert cached['policy'] == sidecar['psce_policy']
            assert set(cached['chains']) == set(result['chains'])
            signature = build_fampnn_psce_profile_signature(design, {}, session)
            explicit = {'chain_id': 'all_chains', 'ignore_cbeta': False}
            other, other_summary, _ = _compute_fampnn_psce_profile(design, explicit)
            assert other_summary['avg_psce'] == pytest.approx(14 / 3)
            assert other_summary['residue_count'] == 3
            differing, reused = await request_design_analysis(session, design, 'fampnn_psce_profile', explicit)
            assert not reused and differing.id != run_id
            assert await analysis_subprocess._run_analysis(differing.id) == 0
            await session.refresh(differing)
            assert build_fampnn_psce_profile_signature(design, explicit, session) != signature
            assert _compute_fampnn_psce_profile(design, {})[1] == summary
            # Filter owner consumes exactly the producer scalar, as does SQL filtering.
            import importlib.util
            spec = importlib.util.spec_from_file_location('test_filter_fampnn', ROOT/'scripts/filter_fampnn.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            rows = module.load_json_data(str(output))
            assert len(module.filter_designs(rows, expected, None)) == 1
            assert len(module.filter_designs(rows, expected - 0.01, None)) == 0
            assert (await session.scalars(select(Design).where(Design.fampnn_psce <= expected))).one().id == design.id
            # Exercise typed HTTP GET/POST policy transport, including historical reanalysis.
            import httpx
            from fastapi import FastAPI
            from routers.analyses import router
            from database import get_session
            app = FastAPI()
            app.include_router(router)
            async def isolated_session():
                yield session
            app.dependency_overrides[get_session] = isolated_session
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                url = f"/designs/{design.id}/analyses/fampnn_psce_profile"
                response = await client.get(url, params=explicit)
                assert response.status_code == 200, response.text
                assert response.json()['run_id'] == differing.id
                assert response.json()['result']['ignore_cbeta'] is False
                assert (await client.get(url)).json()['run_id'] == run_id
                design.confidence_metrics = {'fampnn': {'fampnn_avg_psce': expected}}
                design.provenance = {}
                await session.commit()
                requested = await client.post(url, json={'params': {}})
                assert requested.status_code == 200, requested.text
                assert await analysis_subprocess._run_analysis(requested.json()['run_id']) == 0
                session.expire_all()
                unknown = await client.get(url)
                assert unknown.json()['result']['reason'] == 'historical_psce_policy_unknown'
                requested = await client.post(url, json={'params': {'chain_id': 'B', 'ignore_cbeta': True}})
                assert requested.status_code == 200, requested.text
                assert await analysis_subprocess._run_analysis(requested.json()['run_id']) == 0
                session.expire_all()
                explicit_read = await client.get(url, params={'chain_id': 'B', 'ignore_cbeta': True})
                assert explicit_read.status_code == 200, explicit_read.text
                assert explicit_read.json()['result']['scope'] == 'B'
                assert explicit_read.json()['result']['chains']['B']['avg_psce'] == 12
                assert (await session.scalars(select(Design))).one().fampnn_psce == expected

    finally:
        await engine.dispose()


def test_complete_sidecar_does_not_parse_again(tmp_path, monkeypatch):
    owner = fampnn_psce_authority()
    payload = {'sequence': 'S', 'fampnn_avg_psce': 4., 'fampnn_max_residue_psce': 4., 'fampnn_min_residue_psce': 4., 'psce_policy': owner.psce_policy('A', True)}
    monkeypatch.setattr(owner, 'compute_psce_profile', lambda *a: pytest.fail('duplicate parse'))
    assert _extract_fampnn_metrics(payload, tmp_path/'absent.pdb')['avg_psce'] == 4.


def test_historical_policy_is_unknown_and_scalar_not_reinterpreted(tmp_path):
    pdb = tmp_path/'historical.pdb'
    fixture_pdb(pdb)
    payload = {'sequence': 'S', 'fampnn_avg_psce': 0.19}
    design = Design(id='old', name='historical', pdb_path=str(pdb), fampnn_psce=.19, confidence_metrics={'fampnn': payload})
    result, summary, _ = _compute_fampnn_psce_profile(design, {})
    assert result['status'] == 'unavailable'
    assert result['scope'] is None
    assert result['reason'] == 'historical_psce_policy_unknown'
    metrics = _extract_fampnn_metrics(payload, pdb)
    assert metrics['avg_psce'] == .19
    assert metrics['max_residue_psce'] is None
    from routers.designs import _compute_fampnn_response_metrics
    response = _compute_fampnn_response_metrics(design, include_structure_fallback=True)
    assert response == {'fampnn_psce': .19, 'fampnn_max_residue_psce': None, 'fampnn_min_residue_psce': None}
    assert 'psce_policy' not in metrics
    assert _extract_fampnn_metrics({'chain_avg_psce': {'A': 4, 'B': 12}})['avg_psce'] is None
    with pytest.raises(ValueError, match='both chain_id'):
        _compute_fampnn_psce_profile(design, {'ignore_cbeta': False})
    result, summary, _ = _compute_fampnn_psce_profile(design, {'chain_id': 'B', 'ignore_cbeta': True})
    assert summary['avg_psce'] == 12
    assert design.fampnn_psce == .19
    assert get_analysis_definition('fampnn_psce_profile').version != '2026-03-23-v1'


@pytest.mark.parametrize('params', [{'chain_id': ''}, {'ignore_cbeta': 'false'}, {'scope': 'A'}])
def test_invalid_policy_requests_rejected(params):
    with pytest.raises(ValueError):
        normalize_fampnn_psce_profile_params(params)


def test_malformed_science_not_silently_dropped(tmp_path):
    path = tmp_path/'bad.pdb'
    fixture_pdb(path)
    path.write_text(path.read_text().replace('  4.00', ' -4.00'))
    owner = fampnn_psce_authority()
    with pytest.raises(ValueError, match='nonnegative'):
        owner.compute_psce_profile(path, owner.psce_policy('A', True))


def test_policy_resolves_actual_provenance_without_guessing_and_rejects_conflict():
    from services.structure_utils import resolve_fampnn_psce_policy
    owner = fampnn_psce_authority()
    policy = owner.psce_policy('Z', True)
    design = Design(provenance={'ppiflow': {'fampnn': {'psce_policy': policy}}})
    assert resolve_fampnn_psce_policy(design, {}) == policy
    design.confidence_metrics = {'fampnn': {'psce_policy': owner.psce_policy('A', False)}}
    with pytest.raises(ValueError, match='Conflicting'):
        resolve_fampnn_psce_policy(design, {})
