"""Audited-source fixture, software publisher and SQLite only; no model runs."""
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select
from database import Design, Job
from test_core_protein_candidates import setup
from test_boltzgen_candidate_accounting import make_job
from services import boltzgen_candidate_publication as publication

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from lib.filtering import evidence
from filter_boltzgen import run_strict_filter


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))


def observed_source(tmp_path):
    from lib import boltzgen_native
    root = tmp_path / 'installed'
    with zipfile.ZipFile(SCRIPTS.parent / 'tests/fixtures/boltzgen_617e549/installed_source.zip') as z:
        z.extractall(root)
    return boltzgen_native.observe_source(root / 'opt/venv/bin/boltzgen', root=root), root


def publish(tmp_path, *, values=None, identity=None):
    native = tmp_path / 'native'
    native.mkdir()
    designs = tmp_path / 'designs'
    designs.mkdir()
    np.savez(native / 'a.npz', **(values if values is not None else {
        'design_ptm': np.array([0.]), 'affinity_probability_binary1': np.array([0.])}))
    evidence.npz_metadata(native, designs, {'a'}, producer_identity=identity)
    pdb = designs / 'a.pdb'
    pdb.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
    out = tmp_path / 'collected/boltzgen_filtered'
    out.mkdir(parents=True)
    run_strict_filter(SimpleNamespace(pdbs=[str(pdb)], jsons=[str(designs / 'confidence_a.json')],
        out_dir=str(out), filter_biased='false', metrics_override='design_ptm=none affinity_probability=none filter_rmsd=none',
        additional_filters=None, size_buckets=None, boltzgen_min_plddt=None, boltzgen_min_conf_score=None,
        boltzgen_max_rmsd=None, budget=1, alpha=0))
    return out


def test_native_metadata_retains_source_and_observed_identity(tmp_path):
    native = tmp_path / 'native'; native.mkdir()
    out = tmp_path / 'out'; out.mkdir()
    np.savez(native / 'a.npz', design_ptm=np.array([0.]))
    evidence.npz_metadata(native, out, {'a'})
    payload = json.loads((out / 'confidence_a.json').read_text())
    assert 'native_scalar_source' in payload, 'source-bound scalar publication is absent'


@pytest.mark.asyncio
@pytest.mark.parametrize('value,state', [(0., 'ok'), (None, 'unavailable'), (float('nan'), 'invalid'), (-1., 'invalid')])
async def test_publisher_sqlite_reload_verified_canonical_reader(tmp_path, value, state):
    identity, _ = observed_source(tmp_path)
    assert identity['state'] == 'ok'
    values = {'affinity_probability_binary1': np.array([0.])}
    if value is not None:
        values['design_ptm'] = np.array([value])
    publish(tmp_path, identity=identity, values=values)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = make_job(tmp_path); db.add(job); await db.commit()
            assert await publication.ingest(job, tmp_path, db) == 1
        async with factory() as db:
            row = await db.scalar(select(Design))
            result = await publication.verified_boltzgen_design(db, row)
            metrics = result['block']['metrics']
            assert metrics['design_ptm']['state'] == state
            assert metrics['design_ptm']['value'] == (0 if state == 'ok' else None)
            assert metrics['affinity_probability']['value'] == 0
            assert metrics['filter_rmsd']['state'] == 'unavailable'
            assert metrics['filter_rmsd']['value'] is None
            assert metrics['design_ptm']['source']['candidate_id'] == row.id
            assert metrics['design_ptm']['source']['document_id'] == 'primary'
            assert metrics['design_ptm']['producer_version'].startswith('boltzgen:0.2.0:sha256:')
            assert result['block'] == row.confidence_metrics['core_protein_scientific']
            assert all(m['source']['artifact_sha256'] == result['artifacts']['native']['sha256'] for m in metrics.values())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_native_source_uses_published_metadata_identity(tmp_path):
    from test_boltzgen_candidate_accounting import published
    published(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = make_job(tmp_path); db.add(job); await db.commit()
            await publication.ingest(job, tmp_path, db)
        async with factory() as db:
            row = await db.scalar(select(Design))
            result = await publication.verified_boltzgen_design(db, row)
            for metric in result['block']['metrics'].values():
                assert metric['state'] == 'unavailable' and metric['value'] is None
                assert metric['source']['artifact_sha256'] == result['artifacts']['metrics']['sha256']
    finally:
        await engine.dispose()


@pytest.mark.parametrize('damage', ['source', 'cli', 'inventory', 'missing'])
def test_observed_source_rejects_changes(tmp_path, damage):
    from lib.boltzgen_native import observe_source
    _, root = observed_source(tmp_path)
    package = root / 'opt/venv/lib/python3.11/site-packages/boltzgen'
    if damage == 'source':
        (package / 'model/layers/confidence_utils.py').write_text('# changed')
    elif damage == 'cli':
        (root / 'opt/venv/bin/boltzgen').write_text('# changed')
    elif damage == 'inventory':
        (package / 'extra.py').write_text('# changed')
    else:
        (package / 'cli/boltzgen.py').unlink()
    assert observe_source(root / 'opt/venv/bin/boltzgen', root=root)['state'] == 'unavailable'


@pytest.mark.parametrize('value,state,reason', [
    (0., 'ok', None), (None, 'unavailable', 'missing_native_metric'),
    (-.1, 'invalid', 'outside_domain'), (1.1, 'invalid', 'outside_domain'),
    (float('nan'), 'invalid', 'nonfinite'), (float('inf'), 'invalid', 'nonfinite'),
    (True, 'invalid', 'not_scalar_real'), ([.2, .8], 'invalid', 'not_scalar_real'),
])
def test_native_scalar_domains_do_not_use_metadata_means(tmp_path, value, state, reason):
    import io
    from lib.boltzgen_native import metric_records
    identity, _ = observed_source(tmp_path)
    stream = io.BytesIO()
    np.savez(stream, **({} if value is None else {'design_ptm': np.asarray(value)}))
    record = metric_records({'producer_identity': identity, 'dialect': 'npz'}, stream.getvalue(), candidate_id='owned')['design_ptm']
    assert record['state'] == state
    assert record['reason_code'] == reason
    assert record['value'] == (value if state == 'ok' else None)


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['native', 'metrics', 'manifest', 'copied_block', 'extra_row', 'missing_row', 'unmarked', 'no_receipt', 'foreign_object'])
async def test_reader_rejects_changed_source_and_candidate_authority(tmp_path, damage):
    import copy
    identity, _ = observed_source(tmp_path)
    out = publish(tmp_path, identity=identity)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = make_job(tmp_path); db.add(job); await db.commit()
            await publication.ingest(job, tmp_path, db)
        async with factory() as db:
            row = await db.scalar(select(Design))
            job = await db.get(Job, row.job_id)
            if damage in {'native', 'metrics', 'manifest'}:
                name = {'native': 'native_a.npz', 'metrics': 'confidence_a.json', 'manifest': 'filter_summary.json'}[damage]
                with (out / name).open('ab') as f: f.write(b' ')
            elif damage == 'copied_block':
                payload = copy.deepcopy(row.confidence_metrics)
                payload['core_protein_scientific']['metrics']['design_ptm']['source']['candidate_id'] = 'foreign'
                row.confidence_metrics = payload
            elif damage == 'extra_row':
                db.add(Design(id='extra', job_id=job.id, name='foreign', confidence_metrics=copy.deepcopy(row.confidence_metrics), pdb_path=row.pdb_path, json_path=row.json_path))
            elif damage == 'missing_row':
                await db.delete(row)
            elif damage == 'unmarked':
                job.provenance = {'core_protein_candidate_publication': job.provenance['core_protein_candidate_publication']}
            elif damage == 'no_receipt':
                job.provenance = {'core_protein_scientific_contract': 1}
            await db.commit()
            if damage == 'foreign_object':
                row = SimpleNamespace(id='copied', job_id=row.job_id, name=row.name, pdb_path=row.pdb_path, json_path=row.json_path, confidence_metrics=row.confidence_metrics)
            with pytest.raises((ValueError, RuntimeError)):
                await publication.verified_boltzgen_design(db, row)
    finally:
        await engine.dispose()


def test_wrapper_captures_before_after_source_not_expected_literal(tmp_path, monkeypatch):
    import run_boltzgen_wrapper as wrapper
    from lib import boltzgen_native
    identity, _ = observed_source(tmp_path)
    calls = []
    def observe():
        calls.append('observe')
        return identity
    monkeypatch.setattr(boltzgen_native, 'observe_source', observe)
    monkeypatch.setattr(wrapper.os, 'system', lambda command: calls.append(command) or 0)
    assert hasattr(wrapper, 'run_with_native_identity'), 'wrapper never captures observed source'
    code, receipt = wrapper.run_with_native_identity('software-fixture-not-model')
    assert code == 0 and receipt == identity
    assert calls == ['observe', 'software-fixture-not-model', 'observe']


def test_native_csv_filter_scope_requires_observed_invocation(tmp_path):
    from lib.boltzgen_native import metric_records
    identity, _ = observed_source(tmp_path)
    raw = b'id,design_ptm,affinity_probability_binary1,filter_rmsd\na,0,0,0\n'
    source = {'dialect': 'csv', 'native_id': 'a', 'producer_identity': identity}
    records = metric_records(source, raw, candidate_id='owned')
    assert records['filter_rmsd']['state'] == 'unavailable'
    source['filter_from_inverse_folded'] = True
    records = metric_records(source, raw, candidate_id='owned')
    assert records['filter_rmsd']['value'] == 0
    assert records['filter_rmsd']['scope'] == 'native_refolded_complex_backbone'
