"""Actual retained owners, tiny SQLite/filesystem fixtures; no native inference."""
import json
import shutil
from pathlib import Path

import pytest
from database import Design, Job
from routers import jobs
from services.binder_continuation import snapshot_selection
from test_binder_continuation import selected, PDB
from test_project_workflow_setups import setup_store
from test_one_copy_actions_bc2 import native, put, snapshot
from services import bindcraft2_runtime as runtime


def payload(source):
    return {'source_job_id': source.id, 'design_ids': ['d1', 'd0'], 'operation': 'refine',
            'params': {'binder_chains': 'A', 'target_chains': 'B',
                       'maturation_repack_enabled': True, 'maturation_anchors_enabled': False,
                       'maturation_flow_enabled': False}}


@pytest.mark.asyncio
async def test_failed_setup_two_consumers_delete_retry_exact_roots(selected, monkeypatch):
    client, session, root, source, tmp_path = selected
    request = payload(source)
    # Actual destination/setup owner rejects a missing setup, after retaining inputs.
    failed = await client.post('/api/binder-continuation/selected',
                               json={**request, 'launch_context_id': 'missing-setup'})
    assert failed.status_code == 404, failed.text
    directories = list((tmp_path / 'inputs/design_selections/binder').iterdir())
    assert len(directories) == 1
    directory = directories[0]
    original = {p.relative_to(directory): (p.stat().st_ino, p.read_bytes())
                for p in directory.rglob('*') if p.is_file()}
    responses = [await client.post('/api/binder-continuation/selected', json=request) for _ in range(2)]
    assert all(response.status_code == 201 for response in responses), [x.text for x in responses]
    children = [await session.get(Job, x.json()['launched_jobs'][0]['id']) for x in responses]
    assert children[0].id != children[1].id
    for child in children:
        assert child.params['selected_input_dir'] == str(directory)
        assert child.params['selected_input_manifest'] == str(directory / 'selection_manifest.json')
        assert child.params['source_identity_json'] == str(directory / 'source_identity.json')
        assert child.params['iteration_source_design_ids'] == ['d1', 'd0']
        assert child.lineage_root_job_id == root.id
        assert child.selection_source_job_id == source.id
        assert child.params['binder_chains'] == 'A' and child.params['target_chains'] == 'B'
        child.status = 'failed'
        child.queue_status = 'failed'
        child.output_dir = str(tmp_path / 'results' / child.id)
        Path(child.output_dir).mkdir(parents=True)
        (Path(child.output_dir) / 'failed-setup.txt').write_text('setup failure')
    await session.commit()
    removed_id = children[0].id
    deleted = await jobs.delete_job_permanently(removed_id, session)
    assert deleted['job_id'] == removed_id
    assert not Path(children[0].output_dir).exists()
    assert await session.get(Job, children[1].id) is not None
    retry = await client.post('/api/binder-continuation/selected', json=request)
    assert retry.status_code == 201, retry.text
    retried = await session.get(Job, retry.json()['launched_jobs'][0]['id'])
    assert retried.id not in {removed_id, children[1].id}
    assert retried.params['selected_input_dir'] == str(directory)
    assert {p.relative_to(directory): (p.stat().st_ino, p.read_bytes())
            for p in directory.rglob('*') if p.is_file()} == original
    # Deleting producer bytes cannot invalidate either retained consumer.
    for name in ('state-0.pdb', 'state-1.pdb'):
        (tmp_path / name).unlink()
    for row in json.loads((directory / 'source_identity.json').read_text()):
        assert Path(row['source_path']).read_text() == PDB
    assert len(list((tmp_path / 'inputs/design_selections/binder').iterdir())) == 1


@pytest.mark.asyncio
async def test_irrelevant_metadata_no_scan_relevant_metadata_shared_bodies(selected, monkeypatch):
    _, session, root, source, tmp_path = selected
    designs = [await session.get(Design, 'd1'), await session.get(Design, 'd0')]
    first = snapshot_selection(source, root, designs)
    before = snapshot(first)
    source.params = {**source.params, 'irrelevant_progress': 12}
    source.provenance = {'unrelated': 'publication metadata'}
    root.params = {'unrelated': 'display preferences'}
    designs[0].plddt = 42.0
    original_glob = Path.glob
    def no_scan(path, pattern):
        assert pattern != '*/selection_manifest.json', 'unbounded selection scan'
        return original_glob(path, pattern)
    monkeypatch.setattr(Path, 'glob', no_scan)
    monkeypatch.setattr(jobs, '_materialize_antibody_selection', lambda *a, **k: pytest.fail('body recopied'))
    assert snapshot_selection(source, root, designs) == first
    # Relevant emitted provenance gets a new immutable metadata revision, NOT new bodies.
    designs[0].name = 'renamed'
    designs[0].provenance = {**designs[0].provenance, 'annotation': 'new',
                             'historical_input': str(first / '001_d1.pdb')}
    second = snapshot_selection(source, root, designs)
    assert second != first
    assert snapshot_selection(source, root, designs) == second
    one = json.loads((first / 'selection_manifest.json').read_text())
    two = json.loads((second / 'selection_manifest.json').read_text())
    assert one['designs'][0]['design_name'] == 'same-display-name'
    assert two['designs'][0]['design_name'] == 'renamed'
    assert two['designs'][0]['source_design_provenance'] == designs[0].provenance
    for a, b in zip(one['designs'], two['designs']):
        assert Path(a['selection_pdb_path']).stat().st_ino == Path(b['selection_pdb_path']).stat().st_ino
    identities = json.loads((second / 'source_identity.json').read_text())
    assert identities[0]['source_meta']['source_design_provenance']['annotation'] == 'new'
    assert snapshot(first) == before
    shutil.rmtree(first)
    assert all(Path(row['selection_pdb_path']).read_text() == PDB for row in two['designs'])


@pytest.mark.asyncio
async def test_native_selection_metadata_revision_retains_every_sidecar(selected, monkeypatch):
    from services.binder_continuation import retained_selection
    _, session, root, source, tmp_path = selected
    design = await session.get(Design, 'd1')
    cif = tmp_path / 'native.cif'
    cif.write_text('data_native\n')
    cif.with_suffix('.json').write_text('{"confidence": 0.7}')
    cif.with_suffix('.npz').write_bytes(b'npz-fixture')
    design.pdb_path = str(cif)
    def prepare():
        return retained_selection(source, root, [design], kind='native',
            create=lambda: jobs._materialize_protein_local_selection(root, source, [design], 'sequence_design'))
    first = prepare()
    assert sorted(p.name for p in first.iterdir()) == ['native.cif', 'native.json', 'native.npz', 'selection_manifest.json']
    monkeypatch.setattr(jobs, '_materialize_protein_local_selection', lambda *a: pytest.fail('native payload recopied'))
    design.review_role_map = {'binder': ['A'], 'target': ['B']}
    second = prepare()
    assert second != first and prepare() == second
    for name in ('native.cif', 'native.json', 'native.npz'):
        assert (first / name).stat().st_ino == (second / name).stat().st_ino
    manifest = json.loads((second / 'selection_manifest.json').read_text())
    assert manifest['designs'][0]['source_review_role_map'] == design.review_role_map
    assert manifest['designs'][0]['selection_pdb_path'] == str(second / 'native.cif')
    shutil.rmtree(first)
    assert (second / 'native.cif').read_bytes() == cif.read_bytes()
    assert (second / 'native.json').read_bytes() == cif.with_suffix('.json').read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize('transported', [False, True])
async def test_real_bc2_publication_donates_only_completed_exact_attempt(tmp_path, monkeypatch, transported):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from database import Base
    from services.bindcraft2_publication import published_campaign_immutable, read_published_native_results
    from services.result_ingester import ingest_job_results
    from services import bindcraft2_launch as launch
    from test_bindcraft2_lifecycle import setup, compiler
    from test_bindcraft2_publication import campaign as publish_fixture
    campaign, _ = setup(tmp_path, monkeypatch)
    publish_fixture(campaign)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "publication.sqlite"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='published-source', name='source', model_id='bindcraft2', mode='campaign',
                      status='running', params={'bc2_campaign_dir': str(campaign.parent)},
                      output_dir=str(tmp_path / 'parent'), retry_count=0)
            session.add(job)
            await session.commit()
            assert not published_campaign_immutable(job)
            await ingest_job_results(job.id, job.output_dir, session)
            assert not published_campaign_immutable(job)  # publication alone is not producer completion
            job.status = 'completed'
            await session.commit()
            _, publication = await read_published_native_results(job, session)
            assert publication['campaign_root'] == 'bindcraft2/campaign'
            assert published_campaign_immutable(job)
            for field, value in [('status', 'failed'), ('retry_count', 1), ('remote_attempt_id', 'other'),
                                 ('awaiting_input', True), ('paused', True)]:
                previous = getattr(job, field)
                setattr(job, field, value)
                assert not published_campaign_immutable(job)
                setattr(job, field, previous)
            for key in ('resume_source_dir', 'resume_job_id'):
                job.params = {**job.params, key: 'retained-ancestor'}
                assert not published_campaign_immutable(job)
                job.params = {k: v for k, v in job.params.items() if k != key}
            before = snapshot(campaign)
            result = launch.materialize_native_action(Path(job.output_dir), tmp_path / 'child',
                operation='rank', source_job_id=job.id, compiler=compiler,
                source_campaign_immutable=published_campaign_immutable(job))
            owned = Path(result['bc2_campaign_dir'])
            compiled = json.loads((owned / 'compilation.json').read_text())
            if transported:
                worker = tmp_path / 'worker'
                runtime.materialize_runtime_compilation(compiled, owned, worker)
                owned = worker
            for name in publication['files']:
                if name.endswith('.cif'):
                    assert (owned / 'campaign' / name).stat().st_ino == (campaign / name).stat().st_ino
            assert compiled['native_action']['source_job_id'] == job.id
            assert snapshot(campaign) == before
            shutil.rmtree(tmp_path / 'parent')
            for name in publication['files']:
                assert (owned / 'campaign' / name).read_bytes() == before[name]
    finally:
        await engine.dispose()


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('operation', ['campaign_output', 'rank', 'filter'])
def test_exact_native_existing_table_partial_collision(tmp_path, native, legacy, operation):
    source = tmp_path / 'source'
    campaign = source / 'campaign'
    ranked = 'ranked.csv' if legacy else '3_Ranked/!_Ranked.csv'
    folder = 'accepted' if legacy else '3_Ranked'
    put(campaign, ranked, 'design,i_pDAE\ndesign,0.7\n')
    body = put(campaign, folder + '/design.cif', 'structure\n')
    put(campaign, ranked + '.partial', 'interrupted')
    put(campaign, 'summary.csv', 'old summary')
    put(campaign, 'summary.csv.partial', 'interrupted')
    for name in ('ranked_by_i_pDAE.csv', 'filtered.csv', 'rejected.csv'):
        put(campaign, folder + '/' + name, 'old')
        put(campaign, folder + '/' + name + '.partial', 'interrupted')
    before = snapshot(source)
    child = tmp_path / 'child'
    runtime.materialize_action_tree(source, child, {'operation': operation})
    if operation == 'campaign_output':
        native.campaign_output.main([str(child / 'campaign')])
    elif operation == 'rank':
        native.rank.main([str(child / 'campaign'), '--rescore', '--on', 'i_pDAE'])
    else:
        native.campaign_filter.main([str(child / 'campaign'), '--table', 'accepted', '--where', 'i_pDAE>0.9',
                                     '--rejected', str(child / 'campaign' / folder / 'rejected.csv')])
    assert snapshot(source) == before
    assert (child / body.relative_to(source)).stat().st_ino == body.stat().st_ino
    shutil.rmtree(source)
    assert (child / body.relative_to(source)).read_text() == 'structure\n'
