"""Exercise a populated retained v22 database through the real v23 startup."""
import hashlib
import sqlite3

import pytest

import experiment_migrations as migrations
from experiment_database import create_experiment_engine, create_experiment_session_factory
from services.global_experiments import workflow_setups
from test_project_workflow_setups import _project


@pytest.mark.asyncio
async def test_populated_v22_upgrade_and_reopen(tmp_path):
    path = tmp_path / 'retained.db'
    migrations.run_all(path)
    engine = create_experiment_engine(f'sqlite+aiosqlite:///{path}')
    try:
        async with create_experiment_session_factory(engine)() as session:
            project = await _project(session)
            project_id = project.id
            setup = await workflow_setups.create_workflow_setup(
                session, project_id=project_id, relationship_kind='primary',
                global_experiment_id=None, experiment_name='Retained v22',
                experiment_objective='Reopen unchanged', domain_kind='protein_in_silico',
                capability_id='protein.de_novo.rfd3', idempotency_key='create-v22')
            await workflow_setups.save_workflow_setup_draft(
                session, project_id=project_id, setup_context_id=setup['setup_context_id'],
                expected_generation=0, idempotency_key='save-v22',
                draft={'editor_state': {'zero': 0, 'false': False, 'empty': ''}})
            await session.commit()
    finally:
        await engine.dispose()
    db = migrations._connect(path)
    try:
        original = db.execute('SELECT * FROM workflow_setup_contexts').fetchall()
        db.execute('ALTER TABLE workflow_setup_contexts RENAME TO setup_copy')
        for name in ('ix_experiment_workflow_setups_project_updated', 'ix_experiment_workflow_setups_experiment'):
            db.execute(f'DROP INDEX {name}')
        for name in ('trg_experiment_workflow_setup_contract_immutable', 'trg_experiment_workflow_setup_terminal_immutable'):
            db.execute(f'DROP TRIGGER {name}')
        db.executescript(migrations._V22_SETUP_SQL)
        db.execute('INSERT INTO workflow_setup_contexts SELECT * FROM setup_copy')
        db.execute('DROP TABLE setup_copy')
        db.execute('DELETE FROM experiment_schema_migrations WHERE version = 23')
        db.commit()
        ledger = db.execute('SELECT version, checksum FROM experiment_schema_migrations ORDER BY version').fetchall()
    finally:
        db.close()
    migrations.run_all(path)
    migrations.run_all(path)
    db = migrations._connect(path)
    try:
        assert db.execute('SELECT * FROM workflow_setup_contexts').fetchall() == original
        contract, contract_hash, draft, draft_hash = db.execute(
            'SELECT capability_contract_json, capability_contract_sha256, draft_json, draft_sha256 FROM workflow_setup_contexts'
        ).fetchone()
        assert hashlib.sha256(contract.encode()).hexdigest() == contract_hash
        assert hashlib.sha256(draft.encode()).hexdigest() == draft_hash
        assert db.execute('SELECT version, checksum FROM experiment_schema_migrations WHERE version <= 22 ORDER BY version').fetchall() == ledger
        assert migrations.attest_schema(db)['ok']
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute("UPDATE workflow_setup_contexts SET capability_contract_json = '{}' ")
    finally:
        db.close()
    engine = create_experiment_engine(f'sqlite+aiosqlite:///{path}')
    try:
        async with create_experiment_session_factory(engine)() as session:
            reopened = await workflow_setups.get_workflow_setup(session, project_id=project_id, setup_context_id=setup['setup_context_id'])
            assert reopened['generation'] == 1
            assert reopened['draft'] == {'zero': 0, 'false': False, 'empty': ''}
            assert reopened['setup_destination'] == setup['setup_destination']
    finally:
        await engine.dispose()
