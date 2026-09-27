"""Fold-CP native shard publication and returned-confidence ingestion."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from services.result_ingester import ingest_loose_files

REPO = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / 'fixtures' / 'fold_cp'


def test_native_finalizer_keeps_duplicate_names_and_publishes_nested_paths(tmp_path):
    module = (REPO / 'modules/boltz_cp_experimental.nf').read_text()
    finalizer = module.split('process FinalizeBoltzCPExperimental {', 1)[1]
    for suffix in ('pdb', 'cif', 'json', 'npz'):
        assert f"path 'published/**/*.{suffix}'" in finalizer
        assert f"pattern: 'published/**/*.{suffix}'" in finalizer
    assert "filename.replaceFirst('^published/', '')" in finalizer
    root = tmp_path / 'cp_results'
    for shard, content in [('predictions_dp0_cp0', 'one'), ('predictions_dp1_cp0', 'two')]:
        sample = root / shard / 'sample'
        sample.mkdir(parents=True)
        (sample / 'same_model_0.cif').write_text(content)
        (root / shard / 'confidence_same_model_0.json').write_text(json.dumps({'shard': content}))
    script = finalizer.split("python3 - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    script = script.replace('${results_dir}', str(root))
    subprocess.run(['python3', '-c', script], cwd=tmp_path, check=True)
    assert [(tmp_path / 'published' / s / 'sample' / 'same_model_0.cif').read_text()
            for s in ('predictions_dp0_cp0', 'predictions_dp1_cp0')] == ['one', 'two']
    assert len(list((tmp_path / 'published').rglob('confidence_same_model_0.json'))) == 2
    workflow = (REPO / 'workflows/boltz_cp_experimental.nf').read_text()
    assert 'producer_candidate_key: "fold_cp/${relativePath}"' in workflow
    assert "relativePath.getBytes('UTF-8')" in workflow


@pytest.mark.asyncio
async def test_returned_fold_cp_json_cif_persist_metrics_and_shard_identity(tmp_path):
    output = tmp_path / 'output'
    json_root = output / 'json_files' / 'predictions'
    cif_root = output / 'cif_files' / 'predictions'
    for shard, ptm in [(None, 0.9572844505310059), ('predictions_dp0_cp0', 0.81),
                       ('predictions_dp1_cp0', 0.72)]:
        json_dir = json_root / shard if shard else json_root
        cif_dir = cif_root / shard / 'sample' if shard else cif_root
        json_dir.mkdir(parents=True)
        cif_dir.mkdir(parents=True)
        source = FIXTURE / 'confidence_boltz_cp_input_model_0.json'
        payload = json.loads(source.read_text())
        payload['ptm'] = ptm
        (json_dir / source.name).write_text(json.dumps(payload))
        shutil.copy2(FIXTURE / 'boltz_cp_input_model_0.cif', cif_dir / 'boltz_cp_input_model_0.cif')
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "foldcp.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='fold-cp-return', name='Fold-CP return', model_id='boltz_cp_experimental',
                      mode='design', params={}, output_dir=str(output))
            session.add(job)
            await session.commit()
            assert await ingest_loose_files(job.id, output, session, current_job=job) == 3
        async with factory() as session:
            rows = (await session.execute(select(Design).where(Design.job_id == 'fold-cp-return'))).scalars().all()
            assert len(rows) == 3
            assert len({row.name for row in rows}) == 3
            flat = next(row for row in rows if row.name == 'boltz_cp_input_model_0')
            assert flat.ptm == pytest.approx(0.9572844505310059)
            assert flat.iptm == pytest.approx(0.9093641042709351)
            assert flat.conf_score == pytest.approx(0.8668479323387146)
            assert flat.ligand_iptm == pytest.approx(0.9549866914749146)
            assert flat.plddt_overall == pytest.approx(85.62188744544983)
            assert Path(flat.pdb_path) == cif_root / 'boltz_cp_input_model_0.cif'
            assert Path(flat.json_path) == json_root / 'confidence_boltz_cp_input_model_0.json'
            for row in rows:
                assert row.confidence_metrics['ptm'] == pytest.approx(row.ptm)
                assert Path(row.pdb_path).exists()
                assert Path(row.json_path).exists()
                if row is not flat:
                    relative = Path(row.pdb_path).relative_to(cif_root).as_posix()
                    stem = re.sub(r'[^A-Za-z0-9._-]', '_', str(Path(relative).with_suffix('')))[:100]
                    assert row.name == f'foldcp_{stem}_{hashlib.sha256(relative.encode()).hexdigest()[:12]}'
            assert sorted(row.ptm for row in rows) == pytest.approx([0.72, 0.81, 0.9572844505310059])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('commit', [False, True])
async def test_raw_then_confidence_enriches_same_row_and_respects_transaction(tmp_path, nested, commit):
    output = tmp_path / 'output'
    cif_root = output / 'cif_files' / 'predictions'
    json_root = output / 'json_files' / 'predictions'
    relative = Path('predictions_dp0_cp0/sample') if nested else Path('.')
    cif_dir = cif_root / relative
    cif_dir.mkdir(parents=True)
    structure = cif_dir / 'boltz_cp_input_model_0.cif'
    shutil.copy2(FIXTURE / structure.name, structure)
    # Intermediate structures must never enter the published result roster.
    intermediate = output / 'run' / 'intermediate' / structure.name
    intermediate.parent.mkdir(parents=True)
    shutil.copy2(structure, intermediate)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "foldcp.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='fold-cp-return', name='Fold-CP return', model_id='boltz_cp_experimental',
                      mode='design', params={}, output_dir=str(output))
            session.add(job)
            await session.commit()
            assert await ingest_loose_files(job.id, output, session, current_job=job) == 1
            row = (await session.execute(select(Design).where(Design.job_id == job.id))).scalar_one()
            assert row.producer_model_id == 'boltz_cp_experimental'
            assert row.ptm is None
            assert Path(row.pdb_path) == structure
            row_id, created = row.id, row.created_at
            row.is_favorite = True
            row.notes = 'operator annotation'
            row.provenance = {'operator': 'preserve'}
            if nested:
                # A retained legacy name differs from today's shard-local name.
                row.name = 'legacy_basename'
            await session.commit()
            assert await ingest_loose_files(job.id, output, session, current_job=job) == 0

            sidecar_dir = json_root / ('predictions_dp0_cp0' if nested else '')
            sidecar_dir.mkdir(parents=True)
            sidecar = sidecar_dir / 'confidence_boltz_cp_input_model_0.json'
            shutil.copy2(FIXTURE / sidecar.name, sidecar)
            assert await ingest_loose_files(job.id, output, session, current_job=job, commit=commit) == 0
            if not commit:
                async with factory() as separate:
                    uncommitted = (await separate.execute(select(Design).where(Design.job_id == job.id))).scalar_one()
                    assert uncommitted.json_path is None
                await session.commit()
        async with factory() as session:
            rows = (await session.execute(select(Design).where(Design.job_id == 'fold-cp-return'))).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert (row.id, row.created_at) == (row_id, created)
            assert (row.is_favorite, row.notes) == (True, 'operator annotation')
            if nested:
                assert row.name == 'legacy_basename'
            assert row.provenance['operator'] == 'preserve'
            assert row.json_path == str(sidecar)
            assert row.ptm == pytest.approx(0.9572844505310059)
            assert row.iptm == pytest.approx(0.9093641042709351)
            assert row.producer_model_id == 'boltz_cp_experimental'
            assert await ingest_loose_files(job.id, output, session, current_job=job) == 0
            assert (await session.execute(select(Design).where(Design.job_id == job.id))).scalars().all() == [row]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_partial_confidence_does_not_hide_other_published_structures(tmp_path):
    output = tmp_path / 'output'
    cif_root = output / 'cif_files' / 'predictions'
    json_root = output / 'json_files' / 'predictions'
    for shard in ('predictions_dp0_cp0', 'predictions_dp1_cp0'):
        directory = cif_root / shard / 'sample'
        directory.mkdir(parents=True)
        shutil.copy2(FIXTURE / 'boltz_cp_input_model_0.cif', directory / 'boltz_cp_input_model_0.cif')
    json_dir = json_root / 'predictions_dp0_cp0'
    json_dir.mkdir(parents=True)
    shutil.copy2(FIXTURE / 'confidence_boltz_cp_input_model_0.json',
                 json_dir / 'confidence_boltz_cp_input_model_0.json')
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "foldcp.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='partial', name='partial', model_id='boltz_cp_experimental',
                      mode='design', params={}, output_dir=str(output))
            session.add(job)
            await session.commit()
            assert await ingest_loose_files(job.id, output, session, current_job=job) == 2
            rows = (await session.execute(select(Design).where(Design.job_id == job.id))).scalars().all()
            assert len({row.name for row in rows}) == 2
            assert {row.ptm is None for row in rows} == {True, False}
            assert {row.producer_model_id for row in rows} == {'boltz_cp_experimental'}
            assert await ingest_loose_files(job.id, output, session, current_job=job) == 0
    finally:
        await engine.dispose()
