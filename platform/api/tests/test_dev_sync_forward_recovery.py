import json
import sqlite3
import pytest
from test_dev_sync_manual_deploy import deployment


def failed(deployment):
    sync, root, state, db, installed, current, calls = deployment
    current['restart_failures'] = 2
    with pytest.raises(sync.DeploymentRollbackFailedError):
        sync.sync_once(root, state, deploy_now=True, allow_active_work=True)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE jobs SET status='completed',queue_status=NULL")
    current['live'] = None
    return deployment


def test_forward_recovery_preserves_pause_history_and_normal_publication(deployment):
    sync, root, state, db, installed, current, calls = failed(deployment)
    prior = sync._read_sync_refresh_required(state)
    result = sync.recover_failed_rollback(root, state, forward_revision=current['remote'])
    assert result['decision'] == 'recovered-forward'
    assert current['head'] == current['live'] == current['remote']
    assert calls.count(('restart',)) == 3
    assert sync._read_sync_refresh_required(state) is None
    assert sync._read_deployment_paused(state)
    assert json.loads((state/'dev-sync-prior-failed-rollback.json').read_text()) == prior
    with sqlite3.connect(db) as c:
        assert c.execute('select id,status,remote_attempt_id from jobs').fetchall() == [('job','completed','original-attempt')]


@pytest.mark.parametrize('revision', ['invalid', 'c'*40])
def test_forward_recovery_rejects_wrong_exact_revision_without_restarting(deployment, revision):
    sync, root, state, db, installed, current, calls = failed(deployment)
    prior = sync._read_sync_refresh_required(state)
    with pytest.raises(RuntimeError):
        sync.recover_failed_rollback(root, state, forward_revision=revision)
    assert sync._read_sync_refresh_required(state) == prior
    assert calls.count(('restart',)) == 2


def test_forward_recovery_keeps_ordinary_rollback_on_new_failure(deployment):
    sync, root, state, db, installed, current, calls = failed(deployment)
    current['restart_failures'] = 1
    with pytest.raises(sync.DeploymentRolledBackError):
        sync.recover_failed_rollback(root, state, forward_revision=current['remote'])
    assert current['head'] == current['live'] == 'a'*40
    assert sync._read_deployment_paused(state)
