"""Real converter / HTTP receiver regressions. Synthetic scores, no inference."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI
from sqlalchemy import select

from database import Design, Job, get_session
from experiment_database import get_experiment_session
from routers import frustrampnn as fr
from services.frustrampnn import jobs as children
from services.plr_validator_scores import validator_scores
from test_frustrampnn_child_jobs import child_db, _pdb
from prep_boltz_yaml import process_pdb_files


def atom(serial, name, residue, chain, position, element, record='ATOM'):
    return (f'{record:6s}{serial:5d} {name:^4s} {residue:3s} {chain}{position:4d}    '
            f'{float(serial):8.3f}{0.:8.3f}{0.:8.3f}{1.:6.2f}{20.:6.2f}          {element:>2s}\n')


def test_actual_loop_converter_retains_ccd_polymers_and_connectivity(tmp_path):
    source = tmp_path / 'candidate.pdb'
    source.write_text(atom(1, 'CA', 'CYS', 'A', 7, 'C') + atom(2, 'SG', 'CYS', 'A', 7, 'S')
        + atom(3, 'CA', 'MSE', 'A', 8, 'C', 'HETATM')
        + atom(4, 'FE', 'HEM', 'A', 50, 'FE', 'HETATM')
        + atom(5, 'ZN', 'ZN', 'Z', 1, 'ZN', 'HETATM')
        + atom(6, 'P', 'DA', 'D', 1, 'P') + atom(7, 'P', 'U', 'R', 1, 'P')
        + 'CONECT    2    4\nCONECT    4    2\nEND\n')
    before = source.read_bytes()
    process_pdb_files(str(tmp_path), str(tmp_path / 'out'))
    result = yaml.safe_load((tmp_path / 'out/candidate.yaml').read_text())
    assert result['sequences'] == [
        {'protein': {'id': ['A'], 'sequence': 'CM', 'msa': 'empty', 'modifications': [{'position': 2, 'ccd': 'MSE'}]}},
        {'ligand': {'id': ['A_2'], 'ccd': 'HEM'}},
        {'ligand': {'id': ['Z'], 'ccd': 'ZN'}},
        {'dna': {'id': ['D'], 'sequence': 'A'}},
        {'rna': {'id': ['R'], 'sequence': 'U'}}]
    assert result['constraints'] == [{'bond': {'atom1': ['A', 1, 'SG'], 'atom2': ['A_2', 1, 'FE']}}]
    assert source.read_bytes() == before


@pytest.mark.parametrize('value', [0, 0.2, 1, 93.06, None, False])
def test_protenix_declared_percent_not_value_autodetected(tmp_path, value):
    folder = tmp_path / 'validation/protenix_v2/candidate/seed_42/predictions'
    folder.mkdir(parents=True)
    (folder / 'candidate_summary_confidence_sample_0.json').write_text(json.dumps(
        {'plddt': value, 'ranking_score': 0, 'ptm': 0, 'has_clash': False}))
    columns, raw = validator_scores(tmp_path, 'candidate', ['protenix_v2'])
    if value is None or value is False:
        assert 'plddt_overall' not in columns
    else:
        assert columns['plddt_overall'] == value
        assert raw['protenix_v2']['plddt'] == value
    assert columns['conf_score'] == 0 and columns['has_clash'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('placement', ['omitted', 'local', 'worker'])
@pytest.mark.parametrize('published', [True, False])
async def test_loop_route_real_child_lineage_placement_and_copy(child_db, monkeypatch, placement, published):
    from datetime import datetime
    from database import ExecutionTarget
    from routers import jobs as jobs_route
    import paths
    sessions, results = child_db
    monkeypatch.setattr(paths, 'get_results_dir', lambda: results)
    source_root = results / 'loop'
    review = source_root / '.bms-review-import/test/candidate'
    review.mkdir(parents=True)
    original = _pdb().replace(b'END\n', atom(5, 'H', 'GLY', 'A', 1, 'H').encode() + b'END\n')
    candidate = review / 'candidate.pdb'
    candidate.write_bytes(original)
    async with sessions() as session:
        source = Job(id='loop', name='loop', params={}, model_id='protein_local_redesign', mode='design',
            output_dir=str(source_root), status='completed', queue_status='completed',
            execution_target_id='vast:old-source', awaiting_payload={'candidate_dir': str(review)})
        session.add(source)
        if published:
            session.add(Design(id='exact-design', job_id='loop', name='candidate', pdb_path=str(candidate)))
        if placement == 'worker':
            session.add(ExecutionTarget(id='vast:fixture', provider='vast', provider_instance_id='fixture',
                active=True, state='ready', provider_metadata={'inventory': {'status': 'complete',
                'present': True, 'running': True, 'checked_at': datetime.utcnow().isoformat()}}))
        await session.commit()
        app = FastAPI()
        app.include_router(fr.router)
        app.include_router(jobs_route.router, prefix='/api/jobs')
        async def dependency():
            yield session
        app.dependency_overrides[get_session] = dependency
        app.dependency_overrides[get_experiment_session] = dependency
        body = {'names': ['candidate.pdb']}
        if placement != 'omitted':
            body['execution_target_id'] = 'vast:fixture' if placement == 'worker' else None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/frustrampnn/jobs/loop/analyze-loop-candidates', json=body)
            if placement == 'worker':
                assert response.status_code == 409, response.text
                detail = response.json()['detail']
                assert detail['code'] == 'remote_prepared_job_review_required'
                # Inspect the actual standard prepared requests, not a mocked creator.
                prepared = list(results.glob('frustrampnn_*/inputs/frustrampnn_job.json'))
                assert len(prepared) == 1
                child = Job(**json.loads(prepared[0].read_text()))
                payload = children.prepared_child_request(child).model_dump(mode='json')
                preview = await client.post('/api/jobs/execution-plan/preview', json=payload)
                assert preview.status_code == 200, preview.text
                assert preview.json()['admissible'], preview.text
                payload['execution_plan_approval'] = preview.json()['approval_digest']
                accepted = await client.post('/api/jobs', json=payload)
                assert accepted.status_code == 201, accepted.text
            else:
                assert response.status_code == 202, response.text
                assert response.json()['analyses'][0]['candidate'] == candidate.name
        ids = list((await session.execute(select(Job.id).where(Job.model_id == 'frustrampnn'))).scalars())
        assert len(ids) == 1
        child_id = ids[0]
    # Reopen the private store and read the persisted source lineage and bytes.
    async with sessions() as session:
        child = await session.get(Job, child_id)
        assert child.parent_job_id == 'loop'
        assert child.selection_source_job_id == 'loop'
        assert child.execution_target_id == ('vast:fixture' if placement == 'worker' else None)
        selected = child.params[children.ENVELOPE_KEY]['selection'][0]
        assert selected['source_job_id'] == 'loop'
        assert selected['design_id'] == ('exact-design' if published else None)
        assert selected['producer_coordinates']['original_source_sha256'] == hashlib.sha256(original).hexdigest()
        copied = (Path(child.output_dir) / selected['snapshot_relative_path']).read_bytes()
        assert copied == fr._heavy_atoms_only(original)
        assert selected['sha256'] == hashlib.sha256(copied).hexdigest()
    assert candidate.read_bytes() == original
