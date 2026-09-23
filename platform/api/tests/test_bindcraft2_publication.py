"""Hand-authored publication fixtures; no native GPU acceptance claim."""
import csv
import hashlib
import json
import shutil

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, JobArtifact
from services.bindcraft2_publication import PublicationError, read_published_native_results
from services.bindcraft2_result_readback import read_bindcraft2_result_page
from services.result_ingester import ingest_job_results


def table(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def campaign(root, *, zero=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / '.campaign_state.json').write_text('{"trajectories":2}')
    attempt = {'schema_version': 1, 'design': 't', 'trajectory': 1, 'recipe_hash': 'h',
               'effective_settings': {'temperature': 0.4}, 'drawn': {'binder_length': 80}}
    raw = json.dumps(attempt, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = root / '1_Trajectories/!_BMS_Attempts/t.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw + b'\n')
    table(root / '1_Trajectories/!_Trajectories.csv', [
        {'design': 't', 'hash': 'h', 'trajectory': '1', 'bms_attempt_sha256': digest}])
    if zero:
        return
    table(root / '2_Refolded/!_Refolded.csv', [
        {'design': 't_candidate1', 'hash': 'h', 'outcome': 'rejected', 'Binder_Sequence': 'AAA', 'i_pTM': '0.1'},
        {'design': 't_candidate2', 'hash': 'h', 'outcome': 'passed', 'Binder_Sequence': 'CCC', 'i_pTM': '0.9'}])
    table(root / '3_Ranked/!_Ranked.csv', [
        {'design': 't_seq0', 'hash': 'h', 'rank': '1', 'bms_scored_candidate': '2',
         'bms_attempt_sha256': digest, 'Binder_Sequence': 'CCC'}])
    for state in ('stateB', 'stateA'):
        (root / '3_Ranked' / f't_seq0_{state}.cif').write_text(
            f'data_model\n_bindcraft.design t_seq0\n_bindcraft.bms_scored_candidate 2\n'
            f'_bindcraft.bms_attempt_sha256 {digest}\n_atom_site.id 1\n')


@pytest.mark.asyncio
@pytest.mark.parametrize('zero', [False, True])
async def test_ingester_idempotent_readback_and_accounting(tmp_path, zero):
    root = tmp_path / 'local'
    campaign(root, zero=zero)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='bc', name='BC2', status='completed', model_id='bindcraft2',
                      mode='campaign', params={}, output_dir=str(root))
            session.add(job)
            await session.commit()
            assert await ingest_job_results(job.id, str(root), session) == 0
            publication, receipt = await read_published_native_results(job, session)
            page = await read_bindcraft2_result_page(job, session, stage='retained', limit=1)
            assert page['schema'] == 'bindcraft2.native-readback.v1'
            assert page['total'] == (0 if zero else 1)
            assert 'selection' not in page
            if not zero:
                assert page['rows'][0]['scored_design'] == 't_candidate2'
            arm = publication.arms[0]
            assert arm.accounting == {'claimed_attempts': 2, 'emitted_trajectories': 1,
                                      'scored_draws': 0 if zero else 2, 'passing_draws': 0 if zero else 1,
                                      'rejected_draws': 0 if zero else 1, 'retained_sequences': 0 if zero else 1,
                                      'unresolved_retained_draw_joins': 0}
            assert receipt['arms'][0]['verified_attempts'] == 1
            assert 'selection' not in receipt
            if not zero:
                assert arm.retained[0].scored_design == 't_candidate2'
                assert arm.draws[0].values['i_pTM'] == '0.1'
                assert {doc.retained_design for doc in arm.documents} == {'t_seq0'}
                assert receipt['arms'][0]['qualified_documents'] == 2
            count = len((await session.scalars(select(JobArtifact))).all())
            assert count == len(receipt['files'])
            assert await ingest_job_results(job.id, str(root), session) == 0
            assert len((await session.scalars(select(JobArtifact))).all()) == count
            assert not (await session.scalars(select(Design).where(Design.job_id == job.id))).all()
            await session.commit()
        async with factory() as session:
            job = await session.get(Job, 'bc')
            assert (await read_published_native_results(job, session))[1] == receipt
            if not zero:
                structure = root / '3_Ranked/t_seq0_stateA.cif'
                structure.write_text(structure.read_text() + '# tampered\n')
                with pytest.raises(PublicationError, match='bytes'):
                    await read_published_native_results(job, session)
                with pytest.raises(PublicationError, match='bytes'):
                    await read_bindcraft2_result_page(job, session, stage='document')
                with pytest.raises(PublicationError, match='replay changed'):
                    await ingest_job_results(job.id, str(root), session)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_campaign_is_not_published(tmp_path):
    root = tmp_path / 'empty'
    root.mkdir()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='empty', name='empty', status='completed', model_id='bindcraft2',
                      mode='campaign', params={}, output_dir=str(root))
            session.add(job)
            await session.flush()
            with pytest.raises(PublicationError, match='execution evidence'):
                await ingest_job_results(job.id, str(root), session, commit=False)
            assert not (await session.scalars(select(JobArtifact))).all()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_returned_tree_has_same_scientific_readback(tmp_path):
    local, returned = tmp_path / 'local', tmp_path / 'returned'
    campaign(local)
    shutil.copytree(local, returned)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            pages = {}
            for name, root in (('local', local), ('remote', returned)):
                job = Job(id=name, name=name, status='completed', model_id='bindcraft2',
                          mode='campaign', params={}, output_dir=str(root),
                          remote_attempt_id='remote-attempt' if name == 'remote' else None)
                session.add(job)
                await session.flush()
                assert await ingest_job_results(job.id, str(root), session, commit=False) == 0
                pages[name] = await read_bindcraft2_result_page(job, session, stage='document')
                if name == 'local':
                    local_publication, local_receipt = await read_published_native_results(job, session)
                else:
                    remote_publication, remote_receipt = await read_published_native_results(job, session)
            assert pages['local'] == pages['remote']
            assert local_publication == remote_publication
            assert local_receipt['arms'] == remote_receipt['arms']
            assert local_receipt['files'] == remote_receipt['files']
            await session.rollback()
            assert not (await session.scalars(select(JobArtifact))).all()
    finally:
        await engine.dispose()
