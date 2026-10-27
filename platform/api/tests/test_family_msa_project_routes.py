"""Route authority and forward-only setup table migration."""
import sqlite3

import pytest

import experiment_migrations as migrations
from services.workflow_adapter_registry import is_project_native_destination_registered


@pytest.mark.parametrize('model', ['family_msa_protein', 'family_msa_nucleic'])
def test_alignment_owner_requires_exact_destination(model):
    owner = f'{model}:family_msa'
    route = f'/alignments?model={model}&mode=family_msa'
    assert is_project_native_destination_registered(owner, route)
    assert not is_project_native_destination_registered(owner, route + '&x=1')
    assert not is_project_native_destination_registered(owner, route.replace('/alignments', '/submit'))
    assert not is_project_native_destination_registered(owner, '/alignments?mode=family_msa&model=' + model)
    assert not is_project_native_destination_registered('other:family_msa', route)


def test_v23_preserves_prior_rows_indexes_and_triggers():
    db = sqlite3.connect(':memory:')
    db.executescript(migrations._V22_SETUP_SQL)
    insert = '''INSERT INTO workflow_setup_contexts
        (setup_context_id, project_id, global_experiment_id, domain_experiment_id,
         workflow_id, relationship_kind, capability_id, capability_contract_json,
         capability_contract_sha256, setup_destination, draft_sha256, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'primary', 'old', '{}', ?, ?, ?, 'now', 'now')'''
    digest = '0' * 64
    old = ('setup', 'project', 'global', 'domain', 'workflow', digest, '/submit?template=old', digest)
    db.execute(insert, old)
    before = db.execute('SELECT * FROM workflow_setup_contexts').fetchall()
    db.executescript(migrations.MIGRATION_V23_SQL)
    assert db.execute('SELECT * FROM workflow_setup_contexts').fetchall() == before
    owners = db.execute("SELECT type, name FROM sqlite_master WHERE tbl_name='workflow_setup_contexts' AND type IN ('index', 'trigger') AND name NOT LIKE 'sqlite_%'").fetchall()
    assert len(owners) == 4
    for i, model in enumerate(('family_msa_protein', 'family_msa_nucleic')):
        db.execute(insert, (f'setup{i}', f'project{i}', 'global', 'domain', f'workflow{i}', digest, f'/alignments?model={model}&mode=family_msa', digest))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, ('bad', 'bad', 'global', 'domain', 'bad', digest, '/alignments?model=other&mode=family_msa', digest))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE workflow_setup_contexts SET setup_destination='/submit?template=new' WHERE setup_context_id='setup'")


def test_fresh_migration_and_repeat(tmp_path):
    path = tmp_path / 'project.db'
    migrations.run_all(path)
    migrations.run_all(path)
    with sqlite3.connect(path) as db:
        assert migrations.attest_schema(db)['ok']
        assert db.execute('SELECT max(version) FROM experiment_schema_migrations').fetchone() == (23,)
