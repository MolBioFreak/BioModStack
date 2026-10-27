"""Pinned native MUSCLE samples through the Project HTTP route and an independent oracle."""
import json
import os
import subprocess

import httpx
import pytest
from fastapi import FastAPI
from test_family_msa_native_pair_compare import synthetic_roster, parse_fasta, oracle


@pytest.mark.asyncio
async def test_native_muscle_samples_project_http(tmp_path, monkeypatch):
    from routers import family_msa_results as route
    image = os.environ.get('BMS_TEST_FAMILY_MSA_SIF')
    if not image:
        pytest.skip('set BMS_TEST_FAMILY_MSA_SIF to pinned engine image')
    seqs = synthetic_roster()
    rows = [{'row_id': f'r{i}', 'header': 'same label', 'sequence': s,
             'source_member_id': f'm{i}', 'occurrence_id': 'o', 'source_positions': list(range(len(s))),
             'transformation_provenance': 'synthetic original'}
            for i, s in enumerate(seqs)]
    inp = tmp_path / 'input.fasta'
    inp.write_text(''.join(f'>r{i}\n{s}\n' for i, s in enumerate(seqs)))
    output = tmp_path / 'ensemble.@.fasta'
    proc = subprocess.run(['apptainer', 'exec', '--cleanenv', '--bind', f'{tmp_path}:{tmp_path}', image,
                           'muscle', '-align', str(inp), '-output', str(output), '-threads', '1',
                           '-amino', '-stratified', '-replicates', '2'], capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode(errors='replace')
    paths = sorted(tmp_path.glob('ensemble.*.fasta'))
    assert len(paths) == 8
    entries = [{'id': f'a{i}', 'role': 'alignment', 'logical_path': f'attempt/{p.name}'} for i, p in enumerate(paths)]
    entries.append({'id': 'receipt', 'role': 'receipt'})
    blobs = {f'a{i}': p.read_bytes() for i, p in enumerate(paths)}
    blobs['receipt'] = json.dumps({'requested': {'roster': {'molecule': 'protein', 'rows': rows}}}).encode()
    async def project_result(request, session, project, receipt):
        assert (project, receipt) == ('p', 'retained')
        async def read(key): return blobs[key]
        return entries, read
    monkeypatch.setattr(route, 'project_result', project_result)
    app = FastAPI(); app.include_router(route.router)
    app.dependency_overrides[route.get_experiment_session] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/family-msa/projects/p/ensemble-support', json={'receipt_id': 'retained', 'reference_artifact_id': 'a0', 'member_artifact_ids': ['a3', 'a1', 'a0']})
        assert response.status_code == 200, response.text
        report = response.json()['report']
        reference = oracle(parse_fasta(blobs['a0']))
        for sample, aid in zip(report['samples'], ['a3', 'a1', 'a0']):
            pairs = oracle(parse_fasta(blobs[aid]))
            assert (sample['artifact_id'], sample['numerator'], sample['denominator']) == (aid, len(reference & pairs), len(reference))
            assert sample['logical_path'] == entries[int(aid[1:])]['logical_path']
        assert report['sample_count'] == 3
        assert report['numerator'] == sum(x['numerator'] for x in report['samples'])
        assert report['denominator'] == sum(x['denominator'] for x in report['samples'])
        assert 'not probability' in report['interpretation']
        bad = await client.post('/api/family-msa/projects/p/ensemble-support', json={'receipt_id': 'retained', 'reference_artifact_id': 'a0', 'member_artifact_ids': ['missing']})
        assert bad.status_code == 404
