"""Durable scheduling authority, resume membership, and branch controls."""
import pytest
from database import Job
from test_core_protein_candidates import setup
from services import rf_filter_task_roster as roster


def owner(tmp_path, **params):
    return Job(id='roster', name='roster', model_id='protein_local_redesign', mode='local_redesign',
               params=params, output_dir=str(tmp_path), status='running',
               provenance={'core_protein_scientific_contract': 1})


@pytest.mark.asyncio
async def test_tasks_survive_reload_resume_and_cannot_be_erased(tmp_path):
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = owner(tmp_path); db.add(job); await db.commit()
            assert roster.begin(job, job.params)
            await db.commit()
            assert roster.observe(job, '[ab/123456] Submitted process > PROTEIN_LOCAL_REDESIGN:FilterRFD3 (1)')
            await db.commit()
            roster.finish(job, 1); await db.commit()
        async with factory() as db:
            job = await db.get(Job, 'roster')
            with pytest.raises(ValueError, match='incomplete'):
                roster.authority(job, 'protein_local_redesign')
            roster.begin(job, job.params)
            roster.finish(job, 0)
            with pytest.raises(ValueError, match='incomplete'):
                roster.authority(job, 'protein_local_redesign')
            roster.observe(job, '[ab/123456] Cached process > PROTEIN_LOCAL_REDESIGN:FilterRFD3 (1)')
            roster.finish(job, 0); await db.commit()
        async with factory() as db:
            job = await db.get(Job, 'roster')
            record = roster.authority(job, 'protein_local_redesign')
            assert set(record['stages']['rfd3_backbone_filter']['tasks']) == {'1'}
            assert record['attempt'] == 2
            with pytest.raises(ValueError, match='changed'):
                roster.begin(job, {'rfd_min_rog': 1})
    finally:
        await engine.dispose()


@pytest.mark.parametrize('params,role', [({}, 'upstream'), ({'rfd3_request_path': '/native.json'}, 'skipped'),
    ({'plr_backbone_input_pdbs': ['/a.pdb']}, 'skipped'), ({'plr_sequence_input_pdbs': ['/a.pdb']}, 'skipped'),
    ({'plr_validation_input_pdbs': ['/a.pdb']}, 'skipped')])
def test_zero_task_and_skipped_roles_come_from_launch(tmp_path, params, role):
    job = owner(tmp_path, **params)
    roster.begin(job, params); roster.finish(job, 0)
    stage = roster.authority(job, 'protein_local_redesign')['stages']['rfd3_backbone_filter']
    assert stage == {'role': role, 'tasks': {}}
    with pytest.raises(ValueError, match='outside'):
        roster.observe(job, '[ab/123456] Submitted process > PROTEIN_LOCAL_REDESIGN:FilterRFD3 (1)')


@pytest.mark.parametrize('line', ['[ab/123456] Submitted process > FilterRFD3 (bad)',
    '[ab/123456] Cached process > FilterRFD3 (0)'])
def test_malformed_scheduling_events_fail_closed(tmp_path, line):
    job = owner(tmp_path); roster.begin(job, {})
    with pytest.raises(ValueError, match='decoded'): roster.observe(job, line)


@pytest.mark.asyncio
async def test_compiled_launch_settings_persist_before_spawn(tmp_path):
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = owner(tmp_path); db.add(job); await db.commit()
            await roster.begin_command(db, job, ['nextflow', 'run', 'workflows/protein_local_redesign.nf',
                '--rfd_min_rog', '0', '--skip_rfd', 'false'])
        async with factory() as db:
            job = await db.get(Job, 'roster')
            record = job.provenance[roster.KEY]
            assert record['settings']['rfd_min_rog'] == 0
            assert record['complete'] is False
            assert record['stages']['rfd3_backbone_filter']['role'] == 'upstream'
    finally:
        await engine.dispose()
