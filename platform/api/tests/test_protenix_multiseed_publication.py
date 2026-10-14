"""Reader software fixtures: synthetic native bytes, no inference or live stores."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from httpx import ASGITransport, AsyncClient

from database import Base, Design, Job
from services import protenix_scientific_consumer as consumer
from services.result_ingester import ingest_job_results
from scripts import write_structure_producer_manifest as producer
from test_protenix_native_confidence import app_for, mixed_bytes


def publication(tmp_path, *, flat=False):
    if flat:
        return historical_publication(tmp_path)
    source, full, summary = mixed_bytes()
    root = tmp_path / 'returned'
    expected = {}
    for task_index, task in enumerate(('alpha', 'beta')):
        native = tmp_path / task / 'native'
        for seed in (42, 43, 44):
            for sample in (0, 1):
                # Same filename/rank across both seeds and task namespaces.
                key = f'{task}/seed_{seed}/predictions/complex_sample_{sample}.cif'
                path = native / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(source)
                names = producer.protenix_confidence_names(path.name)
                metrics = json.loads(summary)
                metrics['plddt'] = seed + sample / 10 + task_index
                path.with_name(names['metrics']).write_text(json.dumps(metrics))
                path.with_name(names['pae']).write_bytes(full)
                expected[key] = metrics['plddt']
        manifest = producer.build_manifest(predictions_root=native, producer_method='protenix',
                                          producer_sample=task, formats=['mmcif'])
        raw = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
        stage = tmp_path / task / 'structure_publication'
        producer.write_publication(manifest_bytes=raw, native_candidates=manifest['candidates'],
            manifest=manifest, predictions_root=native, publication_dir=root/'run/protenix/producer',
            published_structure_root='pdb_files/predictions', structure_publication_dir=stage)
        shutil.copytree(stage, root/'pdb_files/predictions', dirs_exist_ok=True)
        shutil.rmtree(native)
        shutil.rmtree(stage)
    return root, expected


def historical_publication(tmp_path):
    """Emulate the existing flat descriptor; the new writer has no legacy flag."""
    native = tmp_path / 'native'
    source, full, summary = mixed_bytes()
    seeds, samples = [42], [0]
    expected = {}
    for seed in seeds:
        for sample in samples:
            key = f'complex/seed_{seed}/predictions/complex_sample_{sample}.cif'
            path = native / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source)
            names = producer.protenix_confidence_names(path.name)
            metrics = json.loads(summary)
            metrics['plddt'] = seed + sample / 10
            path.with_name(names['metrics']).write_text(json.dumps(metrics))
            path.with_name(names['pae']).write_bytes(full)
            expected[key] = metrics['plddt']
    manifest = producer.build_manifest(predictions_root=native, producer_method='protenix',
                                      producer_sample='complex', formats=['mmcif'])
    root = tmp_path / 'returned'
    owner = root / 'run/protenix/producer/complex'
    owner.mkdir(parents=True)
    raw = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    manifest_path = owner / 'producer_manifest.json'
    manifest_path.write_bytes(raw)
    bindings = []
    for candidate in manifest['candidates']:
        key = candidate['producer_output_key']
        relative = Path('pdb_files/predictions') / Path(key).name
        source_path = native / key
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        confidence = {}
        for kind, name in producer.protenix_confidence_names(source_path.name).items():
            original = source_path.with_name(name)
            dest = target.with_name(name)
            shutil.copyfile(original, dest)
            confidence[kind] = dict(published_relative_path=dest.relative_to(root).as_posix(),
                sha256=hashlib.sha256(dest.read_bytes()).hexdigest(), size_bytes=dest.stat().st_size)
        bindings.append(dict(producer_output_key=key, published_relative_path=relative.as_posix(),
            sha256=candidate['producer_artifact_sha256'], size_bytes=target.stat().st_size,
            source_format='mmcif', confidence=confidence))
    # Exact existing descriptor schema; only its transport destination differs.
    document = dict(schema_name='structure_producer_publication', schema_version=1,
        producer_manifest=dict(relative_path=manifest_path.relative_to(root).as_posix(),
                               sha256=hashlib.sha256(raw).hexdigest()), bindings=bindings)
    (owner / 'publication.json').write_text(json.dumps(document))
    shutil.rmtree(native)
    return root, expected


@pytest.mark.asyncio
@pytest.mark.parametrize('flat', [False, True], ids=['multiseed-preserved', 'historical-flat'])
async def test_public_ingestion_preserves_exact_seed_sample_and_confidence(tmp_path, flat):
    root, expected = publication(tmp_path, flat=flat)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "store.sqlite"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='multiseed', name='fixture', model_id='protenix', mode='complex',
                      params={}, provenance={'core_protein_scientific_contract': 1},
                      output_dir=str(root), status='completed')
            session.add(job)
            await session.commit()
            assert await ingest_job_results(job.id, str(root), session) == len(expected)
        identities = {}
        async with factory() as session:
            rows = list((await session.scalars(select(Design).where(Design.job_id == 'multiseed'))).all())
            assert len(rows) == len(expected)
            assert len({row.name for row in rows}) == len(expected)
            assert len({row.pdb_path for row in rows}) == len(expected)
            if flat:
                assert rows[0].name == 'complex_sample_0'
            for row in rows:
                selected = await consumer.verified_native_design(row, session)
                key = row.provenance['native_producer']['producer_output_key']
                assert selected['block']['candidate_id'] == key
                assert selected['block']['document_id'] == key
                assert json.loads(selected['snapshots']['metrics'])['plddt'] == expected[key]
                names = producer.protenix_confidence_names(Path(key).name)
                assert row.json_path == str(Path(row.pdb_path).with_name(names['metrics']))
                assert selected['artifacts']['pae']['path'] == str(Path(row.pdb_path).with_name(names['pae']))
                async with AsyncClient(transport=ASGITransport(app=app_for(session)), base_url='http://test') as client:
                    response = await client.get(f'/designs/{row.id}/residue-metrics')
                    assert response.status_code == 200 and response.json()['status'] == 'ok'
                    response = await client.get(f'/designs/{row.id}/pae')
                    assert response.status_code == 200 and response.json()['native_shape'] == [6, 6]
                identities[key] = (row.id, row.name, deepcopy(row.provenance), deepcopy(row.confidence_metrics))
            assert set(identities) == set(expected)
            assert await ingest_job_results('multiseed', str(root), session) == 0
        async with factory() as session:
            rows = list((await session.scalars(select(Design).where(Design.job_id == 'multiseed'))).all())
            assert {row.provenance['native_producer']['producer_output_key']:
                    (row.id, row.name, row.provenance, row.confidence_metrics) for row in rows} == identities
            for row in rows:
                assert await consumer.verified_native_design(row, session)
            if not flat:
                # A same-basename sibling cannot replace missing selected evidence.
                selected = rows[0]
                sibling = next(row for row in rows[1:] if Path(row.json_path).name == Path(selected.json_path).name)
                assert Path(selected.json_path).name == Path(sibling.json_path).name
                Path(selected.json_path).unlink()
                with pytest.raises((ValueError, KeyError, OSError)):
                    await consumer.verified_native_design(selected, session)
                assert (await consumer.compute_persisted_pae(selected, {}, session))[0]['status'] == 'unavailable'
                assert await consumer.scientific_document(selected, session) is not None
                assert await consumer.verified_native_design(sibling, session)
    finally:
        await engine.dispose()
