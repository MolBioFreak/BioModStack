"""Explicit round identity through real SQLite ingestion, not model inference."""
import pytest
from sqlalchemy import select

from database import Design, Job
from services.result_ingester import (
    _design_lineage_fields, _job_stage_context, _resolve_parent_design_lineage,
    ingest_job_results,
)
from test_core_protein_scientific_admission import admission
from test_generic_sequence_results import native_fixture


def step(stage='sequence_design', **updates):
    return {
        'schema_version': 1, 'stage': stage, 'root_job_id': 'generation',
        'source_job_id': 'generation', 'source_design_id': 'candidate-explicit',
        'backbone_design_id': 'candidate-explicit', 'candidate_key': 'producer:sample:17',
        'target_state': 'independently-declared', 'binder_chains': ['B'],
        'target_chains': ['T'], **updates,
    }


async def source(session, tmp_path):
    session.add(Job(id='generation', name='generation', model_id='ppiflow',
                    mode='protein_binder', status='completed', params={}))
    parent = Design(id='candidate-explicit', name='unrelated-source-name',
                    job_id='generation', pdb_path=str(tmp_path / 'backbone.pdb'),
                    artifact_class='binder_backbone', lineage_root_job_id='generation',
                    plddt_overall=99.0, iptm=0.97)
    session.add(parent)
    await session.commit()
    return parent


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['proteinmpnn', 'fampnn'])
async def test_native_sequence_samples_link_exact_round_source(admission, tmp_path, engine):
    await source(admission, tmp_path)
    output, _ = native_fixture(tmp_path, engine, [0, 1])
    handoff = step()
    child = Job(id='designer', name='designer', model_id=engine, mode='design',
                status='completed', params={}, output_dir=str(output),
                provenance={'binder_round_step': handoff})
    admission.add(child)
    await admission.commit()
    assert await ingest_job_results('designer', output, admission) == 2
    rows = list((await admission.scalars(select(Design).where(Design.job_id == 'designer'))).all())
    assert len(rows) == 2
    for row in rows:
        assert row.parent_design_id == 'candidate-explicit'
        assert row.source_stage_job_id == 'generation'
        assert row.origin_design_id == row.origin_backbone_design_id == 'candidate-explicit'
        assert row.origin_job_id == row.lineage_root_job_id == 'generation'
        assert row.provenance['binder_round_step'] == handoff
        assert row.plddt_overall is None and row.iptm is None
        assert row.review_role_map is None  # input roles are not native output role proof
    before = {row.id: row.provenance for row in rows}
    assert await ingest_job_results('designer', output, admission) == 0
    admission.expire_all()
    after = list((await admission.scalars(select(Design).where(Design.job_id == 'designer'))).all())
    assert {row.id: row.provenance for row in after} == before


@pytest.mark.asyncio
async def test_prediction_parent_is_designed_child_not_backbone(admission, tmp_path):
    await source(admission, tmp_path)
    admission.add(Job(id='designer', name='designer', model_id='fampnn', mode='design',
                      status='completed', params={}))
    designed = Design(id='designed-sample', job_id='designer', name='designed',
                      pdb_path=str(tmp_path / 'designed.pdb'), parent_design_id='candidate-explicit',
                      origin_design_id='candidate-explicit', origin_backbone_design_id='candidate-explicit',
                      origin_job_id='generation', lineage_root_job_id='generation')
    admission.add(designed)
    await admission.commit()
    prediction = Job(id='prediction', name='prediction', model_id='protenix', mode='complex',
                     params={}, provenance={'binder_round_step': step(
                         'prediction', source_design_id='designed-sample', source_job_id='designer')})
    context = _job_stage_context(prediction)
    lineage = await _resolve_parent_design_lineage(admission, context, 'arbitrary-native-output')
    fields = _design_lineage_fields(context, lineage)
    assert fields['parent_design_id'] == 'designed-sample'
    assert fields['source_stage_job_id'] == 'designer'
    assert fields['origin_design_id'] == fields['origin_backbone_design_id'] == 'candidate-explicit'
    assert fields['origin_job_id'] == fields['lineage_root_job_id'] == 'generation'


@pytest.mark.asyncio
async def test_missing_round_association_never_falls_back_to_output_name(admission, tmp_path):
    await source(admission, tmp_path)
    child = Job(id='designer', name='designer', model_id='fampnn', mode='design', params={},
                provenance={'binder_round_step': step(source_design_id=None)})
    context = _job_stage_context(child)
    context['selection_index'] = {'candidate-explicit': {
        'design_id': 'candidate-explicit', 'design_job_id': 'generation'}}
    lineage = await _resolve_parent_design_lineage(admission, context, 'candidate-explicit')
    assert lineage['parent_design_id'] is None
    assert context['provenance']['binder_round_step']['source_design_id'] is None


@pytest.mark.asyncio
async def test_round_input_keeps_existing_source_owner_check(admission, tmp_path):
    await source(admission, tmp_path)
    child = Job(id='designer', name='designer', model_id='fampnn', mode='design', params={},
                provenance={'binder_round_step': step(source_job_id='foreign-job')})
    with pytest.raises(ValueError, match='different source job'):
        await _resolve_parent_design_lineage(admission, _job_stage_context(child), 'unused')


@pytest.mark.asyncio
async def test_conflicting_native_source_is_not_silently_reparented(admission, tmp_path):
    await source(admission, tmp_path)
    child = Job(id='designer', name='designer', model_id='fampnn', mode='design', params={},
                provenance={'binder_round_step': step()})
    with pytest.raises(ValueError, match='differs from its binder round input'):
        await _resolve_parent_design_lineage(admission, _job_stage_context(child), 'unused',
                                            source_identity={'source_design_id': 'other'})


def test_historical_job_has_no_round_marker():
    job = Job(id='old', name='old', model_id='fampnn', mode='design', params={})
    context = _job_stage_context(job)
    assert context['binder_round_step'] is None
    assert 'binder_round_step' not in context['provenance']
