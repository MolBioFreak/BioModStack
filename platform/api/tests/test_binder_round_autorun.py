"""Exercise existing post-publication analysis scheduling, without inference."""
from contextlib import asynccontextmanager

import pytest

from database import Design, Job
from services import analysis_autorun as autorun
from test_core_protein_scientific_admission import admission


def prediction(**overrides):
    values = dict(id='prediction', name='prediction', model_id='protenix', mode='complex',
                  params={}, status='completed', parent_job_id='generation',
                  provenance={'binder_round_step': {
                      'schema_version': 1, 'stage': 'prediction',
                      'root_job_id': 'generation', 'source_design_id': 'designed',
                      'backbone_design_id': 'backbone'}})
    values.update(overrides)
    return Job(**values)


def design():
    return Design(id='predicted', job_id='prediction', name='native-output',
                  pdb_path='native.cif', aligned_error_path=None, aligned_error_format=None)


@pytest.mark.parametrize('model', ['protenix', 'boltz2'])
def test_native_prediction_uses_native_confidence_without_legacy_path(model):
    types = list(autorun._iter_unique(autorun._viewer_minimum_analysis_types(
        prediction(model_id=model), design())))
    assert types == ['structure_summary', 'chain_metrics', 'pae_matrix',
                     'ipsae_interface', 'binder_pose_comparison']


def test_sequence_design_and_historical_children_keep_existing_analysis_selection():
    historical = prediction(provenance={})
    assert autorun._viewer_minimum_analysis_types(historical, design()) == [
        'structure_summary', 'chain_metrics']
    seq = prediction(model_id='fampnn', provenance={'binder_round_step': {
        'schema_version': 1, 'stage': 'sequence_design'}})
    assert autorun._viewer_minimum_analysis_types(seq, design()) == ['structure_summary', 'chain_metrics']


def test_pae_not_invented_for_predictor_without_native_matrix_contract():
    types = autorun._viewer_minimum_analysis_types(prediction(model_id='esmfold2'), design())
    assert 'pae_matrix' not in types and 'ipsae_interface' not in types
    assert 'binder_pose_comparison' in types


@pytest.mark.asyncio
@pytest.mark.parametrize('publication_state', ['completed', 'running'])
async def test_analysis_follows_primary_completion_and_failure_does_not_reject_prediction(
        admission, monkeypatch, publication_state):
    parent = Job(id='generation', name='generation', model_id='ppiflow', mode='protein_binder',
                 params={}, status='completed')
    child = prediction(status=publication_state)
    admission.add_all([parent, child, design()])
    await admission.commit()
    requested = []

    @asynccontextmanager
    async def store():
        yield admission

    async def request(session, row, analysis_type, **kwargs):
        assert session is admission
        requested.append((row.id, analysis_type, kwargs['requested_by']))
        if analysis_type == 'ipsae_interface':
            raise RuntimeError('explicit unavailable-analysis fixture')
        return object(), False

    monkeypatch.setattr(autorun, 'async_session', store)
    monkeypatch.setattr(autorun, 'request_design_analysis', request)
    result = await autorun.ensure_viewer_minimum_analyses_for_job('prediction')
    if publication_state == 'running':
        assert requested == [] and result['designs'] == 0
    else:
        assert [item[1] for item in requested] == [
            'structure_summary', 'chain_metrics', 'pae_matrix', 'ipsae_interface',
            'binder_pose_comparison']
        assert result['queued'] == 4
        assert child.status == parent.status == 'completed'
        assert child.error_message is None and parent.error_message is None
    assert all(item[2] == 'system:auto' for item in requested)


@pytest.mark.asyncio
async def test_round_analysis_covers_all_samples_above_generic_viewer_cap(admission, monkeypatch):
    admission.add_all([prediction(), design(), Design(id='sample2', job_id='prediction',
                       name='sample2', pdb_path='native2.cif')])
    await admission.commit()
    monkeypatch.setenv('BMS_ANALYSIS_AUTORUN_MAX_DESIGNS', '1')
    requested = []

    @asynccontextmanager
    async def store():
        yield admission

    async def request(session, row, analysis_type, **kwargs):
        requested.append((row.id, analysis_type))
        return object(), False

    monkeypatch.setattr(autorun, 'async_session', store)
    monkeypatch.setattr(autorun, 'request_design_analysis', request)
    result = await autorun.ensure_viewer_minimum_analyses_for_job('prediction')
    assert result == {'designs': 2, 'queued': 10, 'reused': 0, 'skipped': 0}
    assert len(requested) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'missing', 'failed', 'version', 'params'])
async def test_round_recovery_only_requests_missing_current_defaults(admission, monkeypatch, fault):
    from database import AnalysisRun
    child, sample = prediction(), design()
    admission.add_all([child, sample])
    for analysis_type in autorun._viewer_minimum_analysis_types(child, sample):
        if fault == 'missing' and analysis_type == 'pae_matrix':
            continue
        definition, params = autorun.normalize_analysis_params(analysis_type, None)
        changed = analysis_type == 'pae_matrix'
        admission.add(AnalysisRun(id=analysis_type, subject_kind='design', subject_id=sample.id,
            analysis_type=analysis_type, params_json=params,
            params_hash='different' if changed and fault == 'params' else autorun.stable_json_hash(params),
            code_version='old' if changed and fault == 'version' else definition.version,
            cache_key=analysis_type, input_signature='fixture-immutable-publication', resource_class=definition.resource_class,
            status='failed' if changed and fault == 'failed' else 'completed'))
    await admission.commit()
    requested = []

    @asynccontextmanager
    async def store():
        yield admission

    async def request(session, row, analysis_type, **kwargs):
        requested.append(analysis_type)
        return object(), False

    monkeypatch.setattr(autorun, 'async_session', store)
    monkeypatch.setattr(autorun, 'request_design_analysis', request)
    result = await autorun.ensure_viewer_minimum_analyses_for_job('prediction')
    assert requested == ([] if fault is None else ['pae_matrix'])
    assert result['reused'] == (5 if fault is None else 4)
    assert child.status == 'completed'
