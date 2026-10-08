"""Real native closeout processes -> canonical ingestion -> existing result readers.

Scientific structures are explicitly fixture inputs. Only HTTP callbacks are
replaced by a no-op recorder: no fake child result, compiler plan or native report.
The real Nextflow publisher creates the reports and validation artifact layout.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('validation', [True, False])
@pytest.mark.parametrize('model', ['antibody_denovo', 'template_antibody_denovo'])
def test_native_full_root_publication_and_readback(tmp_path, monkeypatch, validation, model):
    output = tmp_path / 'published'
    source = output / 'collected/rfantibody' if not validation else tmp_path / 'prediction'
    source.mkdir(parents=True)
    pdb = source / ('candidate_boltzpred.pdb' if validation else 'candidate.pdb')
    pdb.write_bytes((ROOT / 'platform/api/tests/fixtures/md/1AKI.pdb').read_bytes())
    expected = hashlib.sha256(pdb.read_bytes()).hexdigest()
    scores = source / pdb.with_suffix('.json').name
    scores.write_text(json.dumps({'plddt': 85.0, 'ptm': 0.8}))
    manifest = tmp_path / 'input.json'
    manifest.write_text(json.dumps({'pdbs': [str(pdb)], 'scores': [str(scores)]}))
    terminal = tmp_path / 'terminal.list'
    terminal.write_text(str(pdb) + '\n')
    process = 'FinalizeSequentialValidationOutputs' if validation else 'FinalizeTerminalAntibodyOutputs'
    harness = tmp_path / 'closeout.nf'
    harness.write_text(f"include {{ {process} }} from '{ROOT}/modules/antibody_output_finalization'\n"
                       f"workflow {{ {process}(Channel.value(file(params.fixture))) }}\n")
    params = tmp_path / 'params.json'
    params.write_text(json.dumps({'out_dir': str(output), 'code_root': str(ROOT),
        'job_id': 'root', 'job_name': 'root', 'batch_name': 'root',
        'api_url': 'http://callback.invalid', 'fixture': str(manifest if validation else terminal)}))
    config = tmp_path / 'local.config'
    config.write_text("process.executor = 'local'\nprocess.shell = ['/bin/bash', '-euo', 'pipefail']\n")
    shim_dir = tmp_path / 'bin'; shim_dir.mkdir()
    shim = shim_dir / 'python3'
    # Inline Python is the actual producer. Callback transport only is replaced;
    # the same returned bytes are ingested below using the actual API service.
    shim.write_text(f'#!/bin/bash\ncase "$1" in\n"{ROOT}/scripts/result_ingester.py"|"{ROOT}/scripts/stage_reporter.py") exit 0;;\nesac\nexec "{sys.executable}" "$@"\n')
    shim.chmod(0o755)
    run = subprocess.run(['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], 'run', str(harness),
        '-c', str(config), '-params-file', str(params), '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false'],
        cwd=tmp_path, env=dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
            PATH=str(shim_dir) + os.pathsep + os.environ['PATH']), capture_output=True, text=True, timeout=150)
    assert run.returncode == 0, run.stdout + run.stderr
    report_name = 'aggregation_report.json' if validation else 'terminal_closeout_report.json'
    report_path = output / report_name
    raw = report_path.read_bytes()
    report = json.loads(raw)
    assert report['total_validated_designs' if validation else 'total_terminal_designs'] == 1
    assert report['parent_job_id' if validation else 'job_id'] == 'root'
    assert 'Submitted process > ' + process in run.stdout
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path))

    async def verify():
        from database import Base, Job, Design, get_session
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from services.result_ingester import ingest_job_results
        from services.result_contracts import result_contract_for_design, antibody_pipeline_closeout
        from routers.designs import router
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "results.sqlite"}')
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                job = Job(id='root', name='root', model_id=model, mode='antibody_refinement_pipeline',
                    params={'run_structure_validation': validation, 'run_frustrampnn': False},
                    status='running', queue_status='running', output_dir=str(output), provenance={})
                db.add(job); await db.commit()
                from services.result_state_integrity import finalize_successful_job
                finalized = await finalize_successful_job(job, str(output), db)
                assert finalized.completed, finalized
                job = await db.get(Job, 'root')
                assert job.status == 'completed'
                rows = list((await db.scalars(select(Design).where(Design.job_id == job.id))).all())
                assert rows
                assert job.provenance['antibody_pipeline_result']['sha256'] == hashlib.sha256(raw).hexdigest()
                assert job.provenance['antibody_pipeline_result']['contract_id'] == 'antibody_pipeline_v1'
                for row in rows:
                    # Root aggregate cannot claim PAE/sequence/backbone evidence
                    # on behalf of a scientific candidate.
                    assert result_contract_for_design(row).analysis_contract_id != 'antibody_pipeline_v1'
                row_id = rows[0].id
            # Fresh DB session and actual existing HTTP result reader.
            app = FastAPI(); app.include_router(router, prefix='/designs')
            async def sessions():
                async with factory() as db:
                    yield db
            app.dependency_overrides[get_session] = sessions
            async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
                response = await client.get(f'/designs/{row_id}/pdb')
                assert response.status_code == 200, response.text
                assert hashlib.sha256(response.content).hexdigest() == expected
            async with factory() as db:
                job = await db.get(Job, 'root')
                before = dict(job.provenance)
                for changes in ({'status': 'failed'},
                                {('parent_job_id' if validation else 'job_id'): 'foreign'},
                                {('total_validated_designs' if validation else 'total_terminal_designs'): True}):
                    report_path.write_text(json.dumps({**report, **changes}))
                    with pytest.raises(ValueError, match='closeout'):
                        await ingest_job_results('root', str(output), db)
                    job = await db.get(Job, 'root')
                    assert job.provenance == before
                    assert list((await db.scalars(select(Design.id))).all()) == [row_id]
                report_path.write_bytes(raw)
                assert antibody_pipeline_closeout(job, output)['sha256'] == hashlib.sha256(raw).hexdigest()
                report_path.unlink()
                assert antibody_pipeline_closeout(job, output) is None  # interactive gate is not terminal success
                report_path.symlink_to(params)
                with pytest.raises(ValueError, match='regular'):
                    antibody_pipeline_closeout(job, output)
                report_path.unlink()
                job.status = job.queue_status = 'running'
                job.awaiting_input = True
                await db.commit()
                gate = await finalize_successful_job(job, str(output), db)
                assert not gate.completed and gate.integrity_state == 'awaiting_input'
                job.awaiting_input = False
                await db.commit()
                failed = await finalize_successful_job(job, str(output), db)
                assert not failed.completed
                job = await db.get(Job, 'root')
                assert job.status == 'failed'
                assert 'native closeout' in job.provenance['result_integrity']['error']
                # Earlier real candidate rows survive, but cannot certify a root
                # missing its own completion evidence.
                assert list((await db.scalars(select(Design.id))).all()) == [row_id]
                for scenario in ('count_mismatch', 'missing_structure'):
                    report_path.write_bytes(raw)
                    saved_pdbs = {p: p.read_bytes() for p in output.rglob('*.pdb')}
                    if scenario == 'count_mismatch':
                        report_path.write_text(json.dumps({**report,
                            ('total_validated_designs' if validation else 'total_terminal_designs'): 2}))
                    else:
                        for p in saved_pdbs:
                            p.unlink()
                    job.status = job.queue_status = 'running'
                    job.params = {**job.params, 'result_integrity_requires_designs': False}
                    await db.commit()
                    failed = await finalize_successful_job(job, str(output), db)
                    assert not failed.completed, scenario
                    job = await db.get(Job, 'root')
                    assert job.status == 'failed'
                    for p, content in saved_pdbs.items():
                        p.write_bytes(content)
        finally:
            await engine.dispose()
    asyncio.run(verify())
