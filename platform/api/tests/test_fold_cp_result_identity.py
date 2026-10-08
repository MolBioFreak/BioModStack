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
