"""Registered all-emitted sequence records use real private DB/artifact owners."""
import json
from pathlib import Path

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database import Base, Job, Design, JobArtifact
from services.binder_retained_publication import publish_sequence_index, publish_retained_documents
from services.binder_native_selection import read_native_sequence_candidates, resolve_native_source
from scripts.sequence_design_results import build_result_index, load_result_index, RESULT_PATH


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', [False, True])
async def test_native_sequence_artifact_only_membership_and_replay(tmp_path, selected, monkeypatch):
    output = tmp_path / 'output'; output.mkdir()
    raw = output / 'pdb_files'; raw.mkdir()
    filtered = output / 'collected/mpnn_filtered'; filtered.mkdir(parents=True)
    pdb = b'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C  \nEND\n'
    for name in ['emitted-a', 'emitted-b']:
        (raw / f'{name}.pdb').write_bytes(pdb)
        (raw / f'{name}.json').write_text(json.dumps({'design': name, 'sequence': 'A', 'score': 0.5}))
    if selected:
        for p in raw.glob('emitted-a.*'): (filtered / p.name).write_bytes(p.read_bytes())
    source = tmp_path / 'source.pdb'; source.write_bytes(pdb)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'sequence_design_engine': 'proteinmpnn', 'sequence_design_mode': 'design'}))
    index = build_result_index(engine='proteinmpnn', mode='design', source=source, settings=settings,
                               unfiltered_dir=raw, filtered_dir=filtered)
    (output / 'results').mkdir()
    manifest = output / RESULT_PATH; manifest.write_text(json.dumps(index))
    validated = load_result_index(output, 'proteinmpnn', 'design')
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'private.db'))
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            job = Job(id='sequence-job', name='Native sequence specimen', model_id='proteinmpnn', mode='design',
                      params={}, status='completed', output_dir=str(output))
            session.add(job); await session.flush()
            artifact = await publish_sequence_index(session, job, output, validated)
            assert (await publish_sequence_index(session, job, output, validated)).id == artifact.id
            await session.commit()
            assert await session.scalar(select(func.count()).select_from(Design)) == 0
            assert await session.scalar(select(func.count()).select_from(JobArtifact)) == 1
            opened = []
            original = Path.read_bytes
            def read(path):
                opened.append(path)
                return original(path)
            monkeypatch.setattr(Path, 'read_bytes', read)
            from sqlalchemy import event
            queries = []
            def observed_sql(connection, cursor, statement, parameters, context, executemany):
                if statement.lstrip().upper().startswith('SELECT'):
                    queries.append(statement)
            event.listen(engine.sync_engine, 'before_cursor_execute', observed_sql)
            # Two scalar page requests: no manifest/metric/coordinate file opens.
            for _ in range(2):
                registered, records = await read_native_sequence_candidates(session, job)
                assert len(records) == 2
                assert sum(r['selected'] for r in records) == int(selected)
                assert all(r['candidate_ref']['publication_ref'] == artifact.id for r in records)
                assert records[1]['sequence'] == 'A' and not records[1]['selected']
            assert opened == [] and len(queries) == 2
            await read_native_sequence_candidates(session, job, verify_bytes=True)
            assert opened == [manifest]
            manifest.write_text('{}')
            with pytest.raises(ValueError, match='bytes changed'):
                await read_native_sequence_candidates(session, job, verify_bytes=True)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retained_sidecar_is_read_once_and_nonbinder_source_is_owned(tmp_path, monkeypatch):
    output = tmp_path / 'output'; raw = output / 'pdb_files'; raw.mkdir(parents=True)
    (raw / 'candidate.pdb').write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C  \nEND\n')
    sidecar = raw / 'generator_candidate.json'
    sidecar.write_text(json.dumps({'native_output_id': 'exact-emitted', 'target_state': 'state-b'}))
    import services.binder_retained_publication as publication
    regular = publication._regular
    opened = []
    def tracked(root, name):
        opened.append(name)
        return regular(root, name)
    monkeypatch.setattr(publication, '_regular', tracked)
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'private.db'))
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            job = Job(id='producer', name='Specimen', model_id='proteinmpnn', mode='design', params={},
                      status='completed', output_dir=str(output), lineage_root_job_id='historical-root')
            session.add(job); await session.flush()
            await publish_retained_documents(session, job, output)
            assert opened.count('pdb_files/generator_candidate.json') == 1
            artifact = await session.scalar(select(JobArtifact).where(JobArtifact.logical_path.endswith('candidate.pdb')))
            path, identity = await resolve_native_source(session, job.id, artifact.id)
            assert path == raw / 'candidate.pdb'
            assert identity['target_state'] == 'state-b'
            assert identity['producer_document']['native_output_id'] == 'exact-emitted'
            assert identity['lineage_root_job_id'] == 'historical-root'
    finally:
        await engine.dispose()
