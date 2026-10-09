"""Retained-source Dorado terminal publication with real SQL/readback owners.

Product bytes are the existing explicitly inert terminal fixture, not a claim
of Dorado inference. The native cached-engine/runtime control is separate.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from component_runtime import SourceIdentity
from database import Base, Job
from routers import jobs, ont_runs
from services import nextflow, ont_ngs_completion, ont_ngs_contract, alignment_access
from services.result_state_integrity import finalize_successful_job
from test_ont_retained_execution import upgraded_source
from test_ont_ngs_capability_lifecycle import _write_terminal_product_tree, _request


@pytest.fixture
def historical_dorado_source(upgraded_source):
    repo, _, _ = upgraded_source
    retained = os.environ.get('BMS_TEST_RETAINED_ONT_REVISION')
    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    if retained:
        # Opt-in qualification can use the genuine pre-upgrade revision already
        # present in Git; ordinary regression runs construct two private releases.
        old = SourceIdentity(git('rev-parse', retained), git('rev-parse', retained + '^{tree}'))
    else:
        contract = repo / 'platform/api/services/ont_ngs_contract.py'
        current_bytes = contract.read_text()
        assert 'dorado_v2.1.2.lock.json' in current_bytes
        contract.write_text(current_bytes.replace('dorado_v2.1.2.lock.json', 'dorado_v1.3.1.lock.json'))
        git('add', str(contract))
        git('-c', 'user.name=Replay test', '-c', 'user.email=replay@example.invalid', 'commit', '-qm', 'historical lock selection fixture')
        old = SourceIdentity.from_checkout(repo)
        contract.write_text(current_bytes)
        git('add', str(contract))
        git('-c', 'user.name=Replay test', '-c', 'user.email=replay@example.invalid', 'commit', '-qm', 'current lock selection fixture')
    return repo, old


def historical_job(tmp_path, source):
    output = tmp_path / 'results/retained'
    manifest, params = _write_terminal_product_tree(output)
    params.update(ont_workflow_id='ont_basecall_dna', ont_input_mode='pod5',
                  resume_job_id='original', resume_work_dir='work',
                  resume_source_dir=str(output), pod5_dir=str(tmp_path / 'inputs'))
    return manifest, Job(id='historical-completion', name='historical terminal control',
        model_id='nanopore', mode='basecall_dna', params=params, output_dir=str(output),
        execution_source_revision=source.revision, execution_source_tree=source.tree,
        status='running', queue_status='running', awaiting_input=False,
        completed_stages=[], stage_outputs={}, provenance={})


@pytest.mark.asyncio
async def test_historical_compilation_terminal_publication_and_fresh_readback(historical_dorado_source, tmp_path):
    repo, old = historical_dorado_source
    manifest, job = historical_job(tmp_path, old)
    declared = job.params['dorado_lock_sha256']
    current = hashlib.sha256(ont_ngs_contract.DORADO_LOCK_PATH.read_bytes()).hexdigest()
    assert declared != current
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    assert invocation.source_identity == old
    assert invocation.native_parameters['dorado_lock_sha256'] == declared
    assert invocation.native_parameters['resume_work_dir'] == str(repo / 'work')
    source_root = Path(invocation.native_parameters['code_root'])
    assert hashlib.sha256((source_root / 'config/ngs/dorado_v1.3.1.lock.json').read_bytes()).hexdigest() == declared
    stage_token = 'historical-stage-control-0123456789'
    access_token = 'historical-read-control-0123456789'
    job.provenance = {
        'workflow_stage_report_token_sha256': hashlib.sha256(stage_token.encode()).hexdigest(),
        alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256(access_token),
        alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
    }
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'completion.db'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            session.add(job)
            await session.commit()
            await jobs.report_stage_complete(job.id, _request('/stage-complete', stage_token),
                'dorado_demux', [str(manifest)], session)
            await session.refresh(job)
            assert job.provenance['ont_dorado_terminal_products']['identities']['lock_sha256'] == declared
            outcome = await finalize_successful_job(job, job.output_dir, session)
            assert outcome.completed, outcome
        async with sessions() as session:
            loaded = await session.get(Job, 'historical-completion')
            assert loaded.status == loaded.queue_status == 'completed'
            assert loaded.execution_source_revision == old.revision
            assert loaded.execution_source_tree == old.tree
            assert loaded.params['dorado_lock_sha256'] == declared
            anchor = loaded.provenance['ont_dorado_terminal_products']
            assert anchor['identities']['lock_sha256'] == declared != current
            assert 'ont_dorado_products_unavailable' not in loaded.provenance
            result = await ont_runs.ont_list_barcode_units(loaded.id,
                _request(f'/api/jobs/{loaded.id}/barcode-units', access_token), session)
            unit, = result['units']
            assert unit['unit_id'] == 'barcode01'
            assert Path(unit['bam_path']).read_bytes() == b'anchored-barcode-bam'
            print(json.dumps({'source': old.__dict__, 'historical_lock': declared,
                'current_lock': current, 'status': loaded.status,
                'anchor': anchor, 'readback_unit': unit['unit_id']}, sort_keys=True))
    finally:
        await engine.dispose()


@pytest.mark.parametrize('fault', ['tree', 'declared_lock', 'runtime', 'bam', 'fresh'])
def test_historical_completion_never_blesses_conflicting_identity(historical_dorado_source, tmp_path, fault):
    _, old = historical_dorado_source
    _, job = historical_job(tmp_path, old)
    if fault == 'tree':
        job.execution_source_tree = '0' * 40
    elif fault == 'declared_lock':
        job.params['dorado_lock_sha256'] = hashlib.sha256(ont_ngs_contract.DORADO_LOCK_PATH.read_bytes()).hexdigest()
    elif fault == 'runtime':
        path = Path(job.output_dir) / 'basecall/dorado_runtime_provenance.json'
        payload = json.loads(path.read_bytes())
        payload['runtime_sha256'] = 'f' * 64
        path.write_text(json.dumps(payload))
    elif fault == 'bam':
        (Path(job.output_dir) / 'demux/demux/units/barcode01.bam').write_bytes(b'replaced')
    else:
        job.params.pop('resume_work_dir')
    with pytest.raises(ont_ngs_completion.DoradoProductsUnavailable):
        ont_ngs_completion.prepare_dorado_demux_products(job,
            read_root=Path(job.output_dir), persisted_root=Path(job.output_dir))


def test_missing_source_observation_does_not_add_a_completion_gate(tmp_path):
    output = tmp_path / 'current'
    _, params = _write_terminal_product_tree(output, lock_path=ont_ngs_contract.DORADO_LOCK_PATH)
    params.update(resume_job_id='legacy', resume_work_dir='work')
    job = Job(id='legacy-no-source', model_id='nanopore', mode='basecall_dna',
              params=params, output_dir=str(output))
    anchor = ont_ngs_completion.prepare_dorado_demux_products(job,
        read_root=output, persisted_root=output)
    assert anchor['identities']['lock_sha256'] == params['dorado_lock_sha256']
