"""Policy owner fixtures: synthetic coordinates and real CPU publication/SQLite/HTTP."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from database import Job, Design, get_session
from routers import jobs
from services import bindcraft2_publication as bc, boltzgen_candidate_publication as bg, ppiflow_generation as pp
from services.core_protein_result_contract import native_record_index
from test_project_manager_adapters import stores
from test_generation_publication_integration import pp_output
from test_bindcraft2_selected_cif import real_campaign


def pp_fixture(output, count):
    import hashlib
    rows = pp_output(output, count)
    root = output / 'ppiflow_generation'
    receipt = json.loads((root / 'generation_receipt.json').read_text())
    receipt.update(requested_samples=count, unemitted_samples=0)
    (root / 'generation_receipt.json').write_text(json.dumps(receipt))
    for row in rows:
        path = root / row['path']
        path.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
        row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (root / (row['path'] + '.sample.json')).write_text(json.dumps(row))
    if rows:
        (root / 'samples.jsonl').write_text('\n'.join(json.dumps(row) for row in rows) + '\n')


def boltz_output(output, count):
    from filter_boltzgen import run_strict_filter
    source = output / 'native'
    source.mkdir()
    filtered = output / 'collected/boltzgen_filtered'
    filtered.mkdir(parents=True)
    pdbs, jsons = [], []
    for i in range(count):
        name = f'candidate-{i:03}'
        pdb = source / (name + '.pdb')
        pdb.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
        meta = source / ('confidence_' + name + '.json')
        meta.write_text(json.dumps({'design_id': name, 'design_ptm': .9, 'affinity_probability': .8,
                                   'filter_rmsd': 0., 'source': 'boltzgen', 'metrics_source': 'npz'}))
        pdbs.append(str(pdb)); jsons.append(str(meta))
    run_strict_filter(SimpleNamespace(pdbs=pdbs, jsons=jsons, out_dir=str(filtered), filter_biased='false',
        metrics_override=None, additional_filters=None, size_buckets=None, boltzgen_min_plddt=None,
        boltzgen_min_conf_score=None, boltzgen_max_rmsd=None, budget=max(1, count), alpha=0))


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['ppiflow', 'boltzgen'])
@pytest.mark.parametrize('count', [0, 4, 20])
async def test_paged_receiving_chain_bounded_opens_and_reverify(stores, monkeypatch, model, count):
    root, _, core = stores
    output = root / 'results/generation'
    output.mkdir(parents=True)
    (pp_fixture if model == 'ppiflow' else boltz_output)(output, count)
    owner = pp if model == 'ppiflow' else bg
    async with core() as db:
        job = Job(id='generation', name='fixture', model_id=model, mode='protein_binder', status='completed',
                  output_dir=str(output), params={}, provenance={'core_protein_scientific_contract': 1})
        db.add(job); await db.flush()
        await (pp.publish_generation_results if model == 'ppiflow' else bg.ingest)(job, output, db, commit=False)
        await db.commit()
        full = await owner.read_published_generation_results(job, db, limit=100)
    opened = []
    regular = bc._regular
    def observe(root, name, **kwargs):
        path, raw = regular(root, name, **kwargs)
        opened.append((name, len(raw), kwargs.get('byte_range')))
        return path, raw
    monkeypatch.setattr(bc, '_regular', observe)
    app = FastAPI(); app.include_router(jobs.router, prefix='/api/jobs')
    async def dependency():
        async with core() as db:
            yield db
    app.dependency_overrides[get_session] = dependency
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        for offset in [0, 1]:
            opened.clear()
            response = await client.get(f'/api/jobs/generation/generation-results?offset={offset}&limit=1')
            assert response.status_code == 200, response.text
            page = response.json()
            assert page['total'] == count and page['offset'] == offset and page['limit'] == 1
            assert page['records'] == full['records'][offset:offset + 1]
            assert len(opened) <= (4 if model == 'ppiflow' else 4)
            assert all(name != 'samples.jsonl' or span is not None for name, _, span in opened)
            assert all(name != 'filter_summary.json' or span is not None for name, _, span in opened)
            print('POLICY_COUNTER', json.dumps({'model': model, 'campaign_count': count, 'offset': offset,
                  'opens': len(opened), 'body_bytes': sum(n for _, n, _ in opened)}))
        if count > 1:
            async with core() as db:
                job = await db.get(Job, 'generation')
                publication = job.provenance['ppiflow_generation_publication' if model == 'ppiflow' else 'boltzgen_generation_publication']
                sibling = publication['candidates'][-1]['structures'][0]['path']
                campaign = output / publication['campaign_root']
                (campaign / sibling).unlink()
                # Unrelated missing bytes do not refuse a valid page; explicit audit remains full.
                assert (await client.get('/api/jobs/generation/generation-results?limit=1')).status_code == 200
                with pytest.raises((ValueError, RuntimeError, OSError)):
                    await owner.read_published_generation_results(job, db)
                first = publication['candidates'][0]['structures'][0]['path']
                (campaign / first).write_text('corrupt')
                assert (await client.get('/api/jobs/generation/generation-results?limit=1')).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['ppiflow', 'boltzgen'])
@pytest.mark.parametrize('mutation', ['attempt', 'foreign_root', 'pairing', 'registry'])
async def test_native_page_identity_negatives(stores, model, mutation):
    root, _, core = stores
    output = root / 'results/generation'; output.mkdir(parents=True)
    (pp_fixture if model == 'ppiflow' else boltz_output)(output, 2)
    owner = pp if model == 'ppiflow' else bg
    async with core() as db:
        job = Job(id='generation', name='fixture', model_id=model, mode='protein_binder', output_dir=str(output),
                  params={}, provenance={'core_protein_scientific_contract': 1})
        db.add(job); await db.flush()
        await (pp.publish_generation_results if model == 'ppiflow' else bg.ingest)(job, output, db, commit=False)
        await db.commit()
        key = 'ppiflow_generation_publication' if model == 'ppiflow' else 'boltzgen_generation_publication'
        publication = copy.deepcopy(job.provenance[key])
        if mutation == 'attempt':
            job.retry_count = 1
        elif mutation == 'foreign_root':
            publication['root'] = str(root)
            job.provenance = {**job.provenance, key: publication}
        elif mutation == 'pairing':
            publication['candidates'][0]['structures'][0]['sha256'] = 'a' * 64
            job.provenance = {**job.provenance, key: publication}
        else:
            from database import JobArtifact
            row = await db.get(JobArtifact, publication['candidates'][0]['structures'][0]['artifact_id'])
            row.attempt = 1
        await db.commit()
        with pytest.raises(ValueError):
            await owner.read_published_generation_page(job, db, limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['ppiflow', 'boltzgen'])
async def test_historical_unindexed_publication_remains_readable_without_rewrite(stores, model):
    root, _, core = stores
    output = root / 'results/generation'; output.mkdir(parents=True)
    (pp_fixture if model == 'ppiflow' else boltz_output)(output, 2)
    owner = pp if model == 'ppiflow' else bg
    async with core() as db:
        job = Job(id='generation', name='fixture', model_id=model, mode='protein_binder', output_dir=str(output),
                  params={}, provenance={'core_protein_scientific_contract': 1})
        db.add(job); await db.flush()
        await (pp.publish_generation_results if model == 'ppiflow' else bg.ingest)(job, output, db, commit=False)
        key = 'ppiflow_generation_publication' if model == 'ppiflow' else 'boltzgen_generation_publication'
        old = copy.deepcopy(job.provenance[key]); old.pop('record_index'); old.pop('native_receipt', None)
        job.provenance = {**job.provenance, key: old}; await db.commit()
        page = await owner.read_published_generation_page(job, db, offset=1, limit=1)
        assert page['total'] == 2 and len(page['records']) == 1 and job.provenance[key] == old
        if model == 'boltzgen':
            provenance = copy.deepcopy(job.provenance); provenance.pop(key)
            job.provenance = provenance; await db.commit()
            page = await owner.read_published_generation_page(job, db, limit=1)
            assert page['total'] == 2 and key not in job.provenance


@pytest.mark.asyncio
async def test_bc2_selected_custody_ignores_unrelated_campaign_bytes(stores):
    root, _, core = stores
    output = root / 'results/bc'; real_campaign(output)
    async with core() as db:
        job = Job(id='bc', name='BC2', model_id='bindcraft2', mode='campaign', output_dir=str(output), params={})
        db.add(job); await db.flush(); await bc.publish_native_results(job, output, db); await db.commit()
    async with core() as db:
        job = await db.get(Job, 'bc')
        design = (await db.scalars(select(Design).where(Design.job_id == job.id))).one()
        receipt = job.provenance['bindcraft2_native_publication']
        selected = {s['logical_path'].removeprefix('bindcraft2/native/') for s in receipt['candidates'][0]['structures']}
        unrelated = next(name for name in receipt['files'] if name not in selected)
        (output / unrelated).unlink()
        await jobs._validate_selected_design_owners(db, job, None, [design])
        from services.binder_continuation import resolve_selection, snapshot_selection, model_request, individual_model_requests
        source, lineage, selected_designs = await resolve_selection(db, job.id, [design.id])
        retained = snapshot_selection(source, lineage, selected_designs)
        manifest = json.loads((retained / 'selection_manifest.json').read_text())
        snapshot = Path(manifest['designs'][0]['native_source_structure_path'])
        import hashlib
        primary = next(s for s in receipt['candidates'][0]['structures'] if s['primary'])
        assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == primary['sha256']
        for operation in ['fampnn', 'proteinmpnn', 'predict_boltz2', 'predict_protenix', 'refine']:
            base = model_request(operation=operation, params={'binder_chains': 'B', 'target_chains': 'A'},
                                 source=source, root=lineage, selection_dir=retained, execution_target_id=None)
            children = individual_model_requests(base, operation, retained)
            assert children and all(c.params['iteration_source_design_ids'] == [design.id] for c in children)
        with pytest.raises((ValueError, RuntimeError, OSError)):
            await bc.read_published_native_results(job, db)
        job.retry_count = 1
        with pytest.raises(ValueError, match='attempt'):
            await jobs._validate_selected_design_owners(db, job, None, [design])
        job.retry_count = 0
        (output / primary['logical_path'].removeprefix('bindcraft2/native/')).write_text('bad selected bytes')
        with pytest.raises(ValueError, match='bytes'):
            await jobs._validate_selected_design_owners(db, job, None, [design])


@pytest.mark.asyncio
async def test_boltz_explicit_read_consolidates_same_request_body_observations(stores, monkeypatch):
    root, _, core = stores
    output = root / 'results/consolidation'
    output.mkdir(parents=True)
    boltz_output(output, 2)
    async with core() as db:
        job = Job(id='consolidation', name='consolidation', model_id='boltzgen', mode='protein_binder',
                  params={}, provenance={}, output_dir=str(output))
        db.add(job); await db.flush()
        await bg.ingest(job, output, db, commit=False); await db.commit()
        from services import core_protein_result_contract as contract
        observed = []
        actual = contract._artifact
        def count_artifact(base, name, *args):
            observed.append(name)
            return actual(base, name, *args)
        monkeypatch.setattr(bg, '_artifact', count_artifact)
        monkeypatch.setattr(contract, '_artifact', count_artifact)
        full = await bg.read_published_generation_results(job, db)
        assert full['total'] == 2
        assert len(observed) == len(set(observed)) == len(full['publication']['files'])
        print('POLICY_OBSERVATION_COUNTER', json.dumps({'native_file_count': len(observed),
              'max_reads_per_file': max(observed.count(name) for name in observed)}))


@pytest.mark.asyncio
@pytest.mark.parametrize('value,state', [(0., 'ok'), (None, 'unavailable')])
async def test_paged_boltz_native_scalar_pairing_and_missingness(stores, value, state):
    import numpy as np
    from test_boltzgen_native_scalars import publish, observed_source
    root, _, core = stores
    output = root / 'results/generation'; output.mkdir(parents=True)
    identity, _ = observed_source(output)
    values = {'affinity_probability_binary1': np.array([0.])}
    if value is not None:
        values['design_ptm'] = np.array([value])
    publish(output, identity=identity, values=values)
    async with core() as db:
        job = Job(id='generation', name='native scalar fixture', model_id='boltzgen', mode='protein_binder',
                  output_dir=str(output), params={}, provenance={'core_protein_scientific_contract': 1})
        db.add(job); await db.flush(); await bg.ingest(job, output, db); await db.commit()
        page = await bg.read_published_generation_page(job, db, limit=1)
        block = page['records'][0]['native_metrics']
        assert block['metrics']['design_ptm']['state'] == state
        assert block['metrics']['design_ptm']['value'] == value
        assert block['metrics']['filter_rmsd']['value'] is None
        publication = job.provenance['boltzgen_generation_publication']
        native = next(name for name in publication['files'] if name.endswith('.npz'))
        path = output / publication['campaign_root'] / native
        path.write_bytes(path.read_bytes()[:-1] + b'x')
        with pytest.raises(ValueError, match='bytes'):
            await bg.read_published_generation_page(job, db, limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize('model,expected', [('esmfold2', 'AG'), ('protenix', 'A:G:V')])
async def test_round_prediction_summary_multichain_binder_preserves_exact_components(stores, tmp_path, model, expected):
    from services.binder_round_inputs import prediction_request
    from schemas import BinderRoundRequest
    from database import Job, Design
    candidate = tmp_path / 'candidate.pdb'
    candidate.write_text('ATOM      1  CA  ALA B   1       1.000   2.000   3.000  1.00 80.00           C\n'
                         'ATOM      2  CA  GLY C   1       4.000   5.000   6.000  1.00 80.00           C\nEND\n')
    target = tmp_path / 'target.pdb'
    target.write_text('ATOM      1  CA  VAL T   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
    job = Job(id='root', name='root', model_id='boltzgen', mode='protein_binder', params={}, provenance={}, execution_target_id=None)
    design = Design(id='candidate', job_id='root', name='candidate', pdb_path=str(candidate), provenance={})
    settings = BinderRoundRequest.model_validate({'sequence_design': {'model_id': 'fampnn'}, 'prediction': {'model_id': model, 'params': {'seed': 7}}})
    request, binding = prediction_request(job, job, design, settings, ['B', 'C'], ['T'], {'target_path': str(target), 'chains': ['T']})
    assert request.params['sequence'] == expected
    assert [c['sequence'] for c in request.params['complex_components']] == ['A', 'G', 'V']
    assert [c['role'] for c in binding['input_components']] == ['binder', 'binder', 'target']
    assert all('msa' not in c for c in request.params['complex_components'])
    assert request.mode == ('predict' if model == 'esmfold2' else 'complex')


def test_native_byte_index_preserves_unicode_and_nested_decoys():
    records = [{'id': 'α', 'nested': {'dispositions': []}}, {'id': 'β'}]
    raw = json.dumps({'decoy': {'dispositions': []}, 'dispositions': records}, ensure_ascii=False).encode()
    index = native_record_index(raw, member='dispositions')
    assert [json.loads(raw[i['offset']:i['offset'] + i['bytes']]) for i in index] == records
