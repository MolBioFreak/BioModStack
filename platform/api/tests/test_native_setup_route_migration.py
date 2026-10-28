"""Upgrade an authentic v21 setup table without rewriting retained authority."""
import json
import sqlite3

import pytest

import experiment_migrations as migrations
from experiment_database import create_experiment_engine, create_experiment_session_factory
from services.global_experiments import workflow_setups
from test_project_workflow_setups import _project


@pytest.mark.asyncio
async def test_v22_preserves_v21_rows_and_immutable_contract(tmp_path, monkeypatch):
    path = tmp_path / 'upgrade.db'
    migrations.run_all(path)
    engine = create_experiment_engine(f'sqlite+aiosqlite:///{path}')
    sessions = create_experiment_session_factory(engine)
    try:
        async with sessions() as session:
            project = await _project(session)
            created = await workflow_setups.create_workflow_setup(session, project_id=project.id,
                relationship_kind='primary', global_experiment_id=None, experiment_name='Retained',
                experiment_objective='Upgrade without rewriting', domain_kind='protein_in_silico',
                capability_id='protein.de_novo.rfd3', idempotency_key='create')
            await workflow_setups.save_workflow_setup_draft(session, project_id=project.id,
                setup_context_id=created['setup_context_id'], expected_generation=0, idempotency_key='save',
                draft={'editor_state': {'zero': 0, 'false': False, 'null': None, 'empty': ''}})
            await session.commit()
    finally:
        await engine.dispose()
    # Restore the exact historical DDL and ledger, not an approximation with
    # disabled constraints. No table elsewhere references workflow_setup_contexts.
    connection = migrations._connect(path)
    try:
        original = connection.execute('SELECT * FROM workflow_setup_contexts').fetchall()
        connection.execute('ALTER TABLE workflow_setup_contexts RENAME TO setup_copy')
        for name in ('ix_experiment_workflow_setups_project_updated', 'ix_experiment_workflow_setups_experiment'):
            connection.execute(f'DROP INDEX {name}')
        for name in ('trg_experiment_workflow_setup_contract_immutable', 'trg_experiment_workflow_setup_terminal_immutable'):
            connection.execute(f'DROP TRIGGER {name}')
        connection.executescript(migrations.MIGRATION_V21_SQL)
        connection.execute('INSERT INTO workflow_setup_contexts SELECT * FROM setup_copy')
        connection.execute('DROP TABLE setup_copy')
        connection.execute('DELETE FROM experiment_schema_migrations WHERE version >= 22')
        connection.commit()
        old_ledger = connection.execute('SELECT version, checksum FROM experiment_schema_migrations ORDER BY version').fetchall()
    finally:
        connection.close()
    audits = []
    attest = migrations.attest_schema

    def record_attestation(connection, **kwargs):
        audits.append(kwargs["verify_data"])
        return attest(connection, **kwargs)

    monkeypatch.setattr(migrations, "attest_schema", record_attestation)
    migrations.run_all(path, verify_data=False)
    migrations.run_all(path, verify_data=False)  # startup replay is harmless
    assert audits == [True, False]
    monkeypatch.setattr(migrations, "attest_schema", attest)
    connection = migrations._connect(path)
    try:
        assert connection.execute('SELECT * FROM workflow_setup_contexts').fetchall() == original
        assert connection.execute('SELECT version, checksum FROM experiment_schema_migrations WHERE version <= 21 ORDER BY version').fetchall() == old_ledger
        assert migrations.attest_schema(connection)['ok']
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            connection.execute("UPDATE workflow_setup_contexts SET capability_contract_json = '{}' ")
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            connection.execute("UPDATE workflow_setup_contexts SET setup_destination = '/submit?model=complexa&mode=generate'")
    finally:
        connection.close()
