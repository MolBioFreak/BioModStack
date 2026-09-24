"""Actual BMS materialization layout, hand-authored outputs; NOT native execution."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, JobArtifact
from services import bindcraft2_launch as launch
from services.bindcraft2_native import compile_for_native, _canonical
from services.bindcraft2_native_results import native_result_page, read_native_publication
from services.bindcraft2_publication import (
    PublicationError, native_workbench_page, publish_native_results, read_published_native_results,
)
from services.result_ingester import ingest_job_results
from test_bindcraft2_publication import campaign, table


def materialize(tmp_path, monkeypatch, *, sweep=False):
    monkeypatch.setattr(launch, 'get_results_dir', lambda: tmp_path)
    monkeypatch.setattr(launch, 'get_allowed_roots', lambda: {'bms_results': tmp_path})
    source = tmp_path / 'target.fasta'
    source.write_text('>fixture\nAAAAAAAA\n')
    settings = {'max_trajectories': 4, 'modality': ['binder'],
                'targets': [{'name': 'fixture', 'target_path': str(source)}]}
    if sweep:
        settings['parameter_sweep'] = {'axes': ['weights_interface_contacts'], 'levels': [0.5]}

    def compiler(request, root):
        # Identity settings/arm resolver only: no inference, native claim or GPU use.
        compiled = compile_for_native(request, root, lambda native: dict(native),
                                      lambda _: (('arm_alpha', {}), ('arm_beta', {})))
        compiled['requested_settings'] = request
        compiled['request_sha256'] = hashlib.sha256(_canonical(request)).hexdigest()
        return compiled

    preview = launch.preview_campaign(settings, compiler=compiler)
    root = tmp_path / 'job'
    params = launch.materialize_campaign(settings, root, preview_digest=preview['preview_digest'], compiler=compiler)
    receipt = json.loads(Path(params['bc2_compilation']).read_text())
    native = Path(receipt['native_request']['project_folder'])
    assert native == root / 'bindcraft2/campaign'
    return root, native, params


def ancillary(root):
    files = {
        '.redesigned_sequences.txt': 'CCC\n', 'summary.csv': 'scope,metric,samples\ncampaign,trajectories,2\n',
        'native_score.txt': 'fixture stdout, not scientific evidence\n',
        'ranked_by_i_pTM.csv': 'design,rank\nlegacy,7\n',
        'campaign_metadata_abc.json': '{"resume":true}',
        'workers/campaign_settings.json': '{"worker":0}', 'workers/worker_00_gpu_0.log': 'fixture log\n',
        '1_Trajectories/t/t_losses.csv': 'phase,round\ndesign,1\n',
        '1_Trajectories/t/t_sequences.npz': 'fixture-not-an-npz',
        '1_Trajectories/t/t_losses.png': 'fixture-not-a-png',
        '1_Trajectories/t/t_trajectory.html': '<p>fixture</p>',
        '1_Trajectories/t.zip': 'fixture-not-a-zip',
        '2_Refolded/filtered.csv': 'design\nt_candidate2\n',
        '3_Ranked/ranked_by_i_pTM.csv': 'design,rank\nt_seq0,7\n',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (root / '1_Trajectories/ignored.partial').write_text('unfinished')
    (root / 'workers/ignored.lock').write_text('coordination')
    return files


@pytest.mark.asyncio
@pytest.mark.parametrize('sweep', [False, True])
@pytest.mark.parametrize('zero', [False, True])
async def test_materialized_layout_sweep_zero_return_and_replay(tmp_path, monkeypatch, sweep, zero):
    root, native, params = materialize(tmp_path, monkeypatch, sweep=sweep)
    assert params['bc2_sweep_budget']['arms'] == (2 if sweep else 0)
    arms = ('arm_alpha', 'arm_beta') if sweep else (None,)
    extras = {}
    for arm in arms:
        folder = native / arm if arm else native
        campaign(folder, zero=zero)
        extras[arm] = ancillary(folder)
    if sweep:
        table(native / 'sweep.csv', [{'arm': name, 'rank': str(i + 1)} for i, name in enumerate(arms)])
        (native / 'best_settings.json').write_text('{"resolved":false}')
        (native / '.redesigned_sequences.txt').write_text('sweep-shared-state\n')
    returned = tmp_path / 'returned'
    shutil.copytree(root, returned)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr('paths.get_allowed_roots', lambda: {'bms_results': tmp_path})
    try:
        async with factory() as session:
            receipts, publications = [], []
            for job_root in (root, returned):
                job = Job(id=job_root.name, name='fixture', status='completed', model_id='bindcraft2', mode='campaign',
                          params={**params, 'bc2_campaign_dir': str(job_root / 'bindcraft2')}, output_dir=str(job_root))
                session.add(job)
                await session.commit()
                assert await ingest_job_results(job.id, str(job_root), session) == (0 if zero else len(arms))
                publication, receipt = await read_published_native_results(job, session)
                assert receipt['root'] == str(job_root)
                assert receipt['campaign_root'] == 'bindcraft2/campaign'
                assert receipt['inventory_version'] == 2
                for arm in arms:
                    prefix = f'{arm}/' if arm else ''
                    assert {prefix + name for name in extras[arm]} <= set(receipt['files'])
                assert not any(name.endswith(('.lock', '.partial')) for name in receipt['files'])
                for artifact in (await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all():
                    name = artifact.logical_path.removeprefix('bindcraft2/native/')
                    assert artifact.storage_path == str(job_root / 'bindcraft2/campaign' / name)
                    assert artifact.sha256 == hashlib.sha256(Path(artifact.storage_path).read_bytes()).hexdigest()
                designs = (await session.scalars(select(Design).where(Design.job_id == job.id))).all()
                assert all('/bindcraft2/campaign/' in design.pdb_path for design in designs)
                from services.bindcraft2_result_readback import read_bindcraft2_result_page
                page = await read_bindcraft2_result_page(job, session, arm=arms[0], stage='retained')
                assert all(item['download_url'].startswith('/api/files/download/bms_results/') for item in page['artifacts'])
                if not zero:
                    assert page['rows'][0]['rank'] == 1
                    assert page['rows'][0]['design_id'] in {design.id for design in designs}
                    assert {s['target_state'] for s in page['rows'][0]['structures']} == {'stateA', 'stateB'}
                assert await ingest_job_results(job.id, str(job_root), session) == len(designs)
                assert (await read_published_native_results(job, session))[1] == receipt
                publications.append(publication)
                receipts.append(receipt)
            assert publications[0] == publications[1]
            assert receipts[0]['files'] == receipts[1]['files']
            changed = returned / 'bindcraft2/campaign' / ('arm_alpha' if sweep else '') / '.redesigned_sequences.txt'
            changed.write_text('changed durable state')
            with pytest.raises(PublicationError, match='bytes'):
                await read_published_native_results(job, session)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_flat_historical_receipt_reopens_without_inventory_migration(tmp_path):
    from services.bindcraft2_publication import _inventory
    root = tmp_path / 'old'
    campaign(root)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='old', name='old', model_id='bindcraft2', mode='campaign', params={}, output_dir=str(root))
            session.add(job)
            await session.flush()
            await publish_native_results(job, root, session)
            publication, receipt = await read_published_native_results(job, session)
            old = {key: value for key, value in receipt.items() if key not in ('campaign_root', 'inventory_version')}
            assert old['files'] == _inventory(root, publication, legacy=True)
            job.provenance = {'bindcraft2_native_publication': old}
            # Files which were never registered by the old publisher must not
            # rewrite or invalidate its sealed historical receipt on readback.
            ancillary(root)
            await session.commit()
            assert (await read_published_native_results(job, session))[1] == old
            assert await publish_native_results(job, root, session) == 1
            assert job.provenance['bindcraft2_native_publication'] == old
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_campaign_root_preserves_owned_root_and_containment(tmp_path):
    root = tmp_path / 'job'
    native = root / 'bindcraft2/campaign'
    campaign(native, zero=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='explicit', name='explicit', model_id='bindcraft2', mode='campaign', params={}, output_dir=str(root))
            session.add(job)
            await session.flush()
            with pytest.raises(PublicationError, match='persisted job output'):
                await publish_native_results(job, native, session)
            with pytest.raises(PublicationError, match='escapes'):
                await publish_native_results(job, root, session, campaign_root=tmp_path)
            (root / 'linked').symlink_to(native, target_is_directory=True)
            with pytest.raises(PublicationError, match='unsafe'):
                await publish_native_results(job, root, session, campaign_root=root / 'linked')
            assert await publish_native_results(job, root, session, campaign_root=Path('bindcraft2/campaign')) == 0
            assert (await read_published_native_results(job, session))[1]['campaign_root'] == 'bindcraft2/campaign'
            with pytest.raises(PublicationError, match='replay changed|execution evidence'):
                await publish_native_results(job, root, session, campaign_root=root)
    finally:
        await engine.dispose()


def test_missing_draw_and_unassociated_stamped_structure_are_observations(tmp_path):
    campaign(tmp_path)
    (tmp_path / '2_Refolded/!_Refolded.csv').unlink()
    root = tmp_path / '2_Refolded/Complexes'
    root.mkdir()
    (root / 'unknown.cif').write_text('data_model\n_bindcraft.design not_retained\n_bindcraft.bms_scored_candidate 1\n_bindcraft.bms_target_state explicit_state\n')
    publication = read_native_publication(tmp_path)
    assert publication.arms[0].retained[0].scored_design is None
    unknown = next(doc for doc in publication.arms[0].documents if doc.path.endswith('unknown.cif'))
    assert unknown.retained_design is None
    assert unknown.target_state == 'explicit_state'


def test_native_rank_and_relaxed_state_are_preserved(tmp_path):
    campaign(tmp_path)
    table_path = tmp_path / '3_Ranked/!_Ranked.csv'
    table_path.write_text(table_path.read_text().replace(',1,2,', ',7,2,'))
    native = tmp_path / '3_Ranked/t_seq0_stateA.cif'
    relaxed = tmp_path / '3_Ranked/relaxed/t_seq0_stateA.cif'
    relaxed.parent.mkdir()
    relaxed.write_text(native.read_text().replace('bms_structure_variant native', 'bms_structure_variant relaxed'))
    result = read_native_publication(tmp_path)
    assert result.arms[0].retained[0].rank == 7
    assert next(doc for doc in result.arms[0].documents if '/relaxed/' in doc.path).structure_variant == 'relaxed'


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [False, True])
async def test_native_lifecycle_preserves_source_root_without_inheriting_validation(tmp_path, changed):
    parent_root = tmp_path / 'parent'
    campaign(parent_root / 'bindcraft2/campaign')
    child_root = tmp_path / 'child'
    shutil.copytree(parent_root, child_root)
    if changed:
        # Different bytes with identical names must NOT claim an unchanged source.
        structure = child_root / 'bindcraft2/campaign/3_Ranked/t_seq0_stateA.cif'
        structure.write_text(structure.read_text() + '# changed coordinates fixture\n')
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            parent = Job(id='parent', name='parent', model_id='bindcraft2', mode='campaign',
                         params={'bc2_campaign_dir': str(parent_root / 'bindcraft2')},
                         lineage_root_job_id='scientific-root', output_dir=str(parent_root))
            session.add(parent)
            await session.flush()
            await publish_native_results(parent, parent_root, session)
            _, parent_receipt = await read_published_native_results(parent, session)
            original = parent_receipt['candidates'][0]
            source_design = await session.get(Design, original['design_id'])
            source_design.conf_score = 0.9  # unrelated prior analysis must not be inherited
            child = Job(id='child', name='child', model_id='bindcraft2', mode='resume' if changed else 'rank',
                        params={'bc2_source_job_id': parent.id, 'bc2_action_options': {},
                                'bc2_campaign_dir': str(child_root / 'bindcraft2')}, output_dir=str(child_root))
            session.add(child)
            await session.flush()
            assert await publish_native_results(child, child_root, session) == 1
            _, receipt = await read_published_native_results(child, session)
            projected = await session.get(Design, receipt['candidates'][0]['design_id'])
            assert projected.lineage_root_job_id == 'scientific-root'
            assert projected.source_stage_job_id == parent.id
            assert projected.parent_design_id == (None if changed else original['design_id'])
            assert projected.origin_job_id == ('child' if changed else 'parent')
            assert projected.conf_score is None
            assert projected.provenance['source_job_id'] == parent.id
            assert parent.provenance['bindcraft2_native_publication'] == parent_receipt
            assert await publish_native_results(child, child_root, session) == 1
            await session.commit()
        # Parent files / original worker need not be available to reopen a child.
        parent_root.rename(tmp_path / 'offline-parent')
        async with factory() as session:
            child = await session.get(Job, 'child')
            assert (await read_published_native_results(child, session))[1] == receipt
    finally:
        await engine.dispose()
