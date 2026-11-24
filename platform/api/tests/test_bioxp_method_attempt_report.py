"""Same-run recovery results must distinguish execution history from resolution."""
from types import SimpleNamespace

from services.bioxp.method_report import occurrence_outcomes


def report(*results, completed=()):
    stage = SimpleNamespace(current_action_id=None, completed_actions=completed, status='completed')
    state = SimpleNamespace(action_results=list(results), stage_states={'stage': stage})
    snapshot = {'compilation': {'provenance': [{'occurrence_id': 'occ', 'native_action_ids': ['step']}]}}
    return occurrence_outcomes(snapshot, state)[0]


def test_retry_can_complete_without_overwriting_failed_partial_attempt():
    failed = {'action_id': 'step', 'attempt': 1, 'ok': False, 'status': 'failed',
              'child_outcomes': [{'status': 'ambiguous', 'partial_effects': True}]}
    ok = {'action_id': 'step', 'attempt': 2, 'ok': True, 'status': 'completed'}
    row = report(failed, ok, completed=['step'])
    assert row['status'] == 'completed'
    child = row['children'][0]
    assert child['results'] == [failed, ok]
    assert [(a['attempt'], a['status']) for a in child['attempts']] == [(1, 'failed'), (2, 'completed')]
    assert child['child_outcomes'] == failed['child_outcomes']


def test_old_completion_cannot_certify_repeat_with_missing_evidence():
    row = report({'action_id': 'step', 'attempt': 1, 'status': 'completed', 'ok': True},
                 {'action_id': 'step', 'attempt': 2}, completed=['step'])
    assert row['status'] == 'unknown'


def test_operator_resolution_does_not_turn_physical_failure_into_success():
    for resolution in ('operator_completed', 'skipped'):
        row = report({'action_id': 'step', 'attempt': 1, 'ok': False, 'resolution': resolution})
        assert row['status'] == resolution
        assert row['children'][0]['attempts'][0]['status'] == 'failed'


def test_legacy_failure_still_takes_precedence_over_completed_cursor():
    row = report({'action_id': 'step', 'ok': False}, completed=['step'])
    assert row['status'] == 'failed' and 'attempts' not in row['children'][0]
