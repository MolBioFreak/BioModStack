"""Coverage survives the existing score-field projection and SQLite JSON storage."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from database import Design, ScientificArtifactReceipt
from routers.designs import _design_to_response

import pytest

from services.result_ingester import _apply_ppiflow_score_fields


def comparison():
    return {
        'domain': 'whole_binder', 'value': None, 'reason': 'incomplete_correspondence',
        'expected_reference_count': 2, 'expected_candidate_count': 3,
        'matched_count': 1, 'reference_coverage': 0.5, 'candidate_coverage': 1 / 3,
        'unmatched_reference': [{'identity': ['H', 2, 'A'], 'reason': 'unmapped_identity'}],
        'unmatched_candidate': [{'identity': ['B', 9, ''], 'reason': 'missing_coordinates'},
                                {'identity': ['B', 10, ''], 'reason': 'unmapped_identity'}],
        'subset': {'name': 'explicit_matched_core', 'value': 1.25, 'matched_count': 1, 'unit': 'angstrom'},
        'unit': 'angstrom', 'frame': 'input_coordinates_no_superposition',
        'formula': 'sqrt(mean_squared_CA_distance)',
    }


def test_native_data_chain_to_scorer_sqlite_and_response(tmp_path, monkeypatch):
    """Synthetic native parser/export files; only PyRosetta physics injected."""
    import json
    import os
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root/'tests'))
    monkeypatch.syspath_prepend(str(root/'scripts'))
    import test_g09_native_transport as fixture
    import test_maturation_regressions as physical_fixture
    import score_maturation as scorer
    fixture.test_real_native_parsers_and_fampnn_export_preserve_insertion_identity(tmp_path)
    reference, candidate, output = tmp_path/'ref.pdb', tmp_path/'native.pdb', tmp_path/'score.json'
    def parse(path):
        lines = [line for line in Path(path).read_text().splitlines() if line.startswith('ATOM  ') and line[12:16].strip() == 'CA']
        return physical_fixture.Pose([(line[21], int(line[22:26]), line[26].strip()) for line in lines],
                                     [float(line[30:38]) for line in lines], [-1.] * len(lines))
    monkeypatch.setitem(sys.modules, 'pyrosetta', SimpleNamespace(init=lambda _: None, pose_from_pdb=parse, get_fa_scorefxn=lambda: lambda pose: None))
    monkeypatch.setattr(scorer, 'pair_energy_total', lambda fxn, pose, a, b: -1.)
    monkeypatch.setattr(scorer, 'calculate_rosetta_interface_analyzer_metrics', lambda *args: {})
    monkeypatch.setattr(sys, 'argv', ['score', '--core-protein-scientific-contract', '1', '--original_pdb', str(reference),
        '--matured_pdb', str(candidate), '--comparison-request', str(candidate)+'.comparison.json',
        '--selected_positions', 'H100A', '--objective_mode', 'selected_interface', '--output', str(output)])
    scorer.main()
    score = json.loads(output.read_text())
    assert score['comparisons']['whole_binder']['value'] == 0.
    assert score['comparisons']['whole_binder']['matched_count'] == 2
    assert score['comparisons']['selected']['matched_count'] == 1
    design = Design(id='g09-native-candidate', job_id='g09-synthetic', name='native', pdb_path=str(candidate), confidence_metrics={}, review_profile_id='ppiflow_maturation_v1')
    assert _apply_ppiflow_score_fields(design, score)
    assert design.maturation_rmsd == 0., score['comparisons']['whole_binder']
    engine = create_engine(f"sqlite:///{tmp_path/'native.sqlite'}")
    Design.__table__.create(engine)
    ScientificArtifactReceipt.__table__.create(engine)
    with Session(engine) as db:
        db.add(design)
        db.commit()
        db.expunge_all()
        from sqlalchemy.orm import undefer
        # Match the detail route's explicit loading of deferred score columns.
        reloaded = db.get(Design, 'g09-native-candidate', options=[undefer(Design.maturation_rmsd)])
        response = _design_to_response(reloaded).model_dump(mode='json')
        assert response['confidence_metrics']['maturation_comparisons'] == score['comparisons']
        assert response['maturation_rmsd'] == 0.
    engine.dispose()
    if os.environ.get('BMS_G09_FRONTEND_FIXTURE'):
        Path(os.environ['BMS_G09_FRONTEND_FIXTURE']).write_text(json.dumps({'synthetic': True, 'response': response}, sort_keys=True))


def test_existing_ingress_preserves_both_domains_and_subset_without_promoting_it(tmp_path):
    from services.scientific_artifacts import artifact_root

    # Exercise the suite fixture, not a test-local override that masks its absence.
    assert artifact_root() == tmp_path / 'scientific_artifacts'
    design = Design(maturation_rmsd=None, confidence_metrics={'unrelated': {'value': 0.8}})
    score = {'comparisons': {'whole_binder': comparison()}, 'rmsd_backbone': None,
             'objective_score': -2.0, 'objective_mode': 'selected_interface',
             'comparison_request_sha256': 'a' * 64}
    assert _apply_ppiflow_score_fields(design, score)
    assert design.maturation_rmsd is None
    assert design.ppiflow_objective_score == -2.0
    assert design.confidence_metrics['maturation_comparisons'] == score['comparisons']
    assert design.confidence_metrics['unrelated'] == {'value': 0.8}
    engine = create_engine(f"sqlite:///{tmp_path / 'coverage.sqlite'}")
    Design.__table__.create(engine)
    ScientificArtifactReceipt.__table__.create(engine)
    design.id, design.job_id, design.name, design.pdb_path = 'candidate-id', 'job-id', 'candidate', str(tmp_path / 'absent.pdb')
    with Session(engine, expire_on_commit=False) as db:
        db.add(design)
        db.commit()
        receipts = db.query(ScientificArtifactReceipt).all()
        assert receipts, 'the real ORM externalizer must publish an artifact'
        for receipt in receipts:
            artifact = artifact_root() / receipt.relative_path
            assert artifact.resolve().is_relative_to(tmp_path.resolve())
            assert artifact.is_file()
            assert artifact.stat().st_size == receipt.size_bytes
        db.expunge_all()
        stored = db.get(Design, 'candidate-id')
        reloaded = _design_to_response(stored).model_dump(mode='json')['confidence_metrics']
    engine.dispose()
    assert reloaded['maturation_comparisons']['whole_binder'] == comparison()
    score['comparisons']['whole_binder']['matched_count'] = 999
    assert reloaded['maturation_comparisons']['whole_binder']['matched_count'] == 1


@pytest.mark.parametrize('field,domain', [('maturation_rmsd', 'whole_binder'),
                                         ('maturation_selected_rmsd', 'selected'),
                                         ('maturation_nonselected_rmsd', 'nonselected')])
def test_incomplete_comparison_cannot_be_overridden_by_bare_rmsd(field, domain):
    design = Design(maturation_rmsd=99, maturation_selected_rmsd=99,
                             maturation_nonselected_rmsd=99, confidence_metrics={})
    score = {'comparisons': {domain: comparison()}, 'rmsd_backbone': 99,
             'selected_rmsd_backbone': 99, 'nonselected_rmsd_backbone': 99}
    _apply_ppiflow_score_fields(design, score)
    assert getattr(design, field) is None


def test_complete_comparison_owns_scalar_not_stale_alias():
    record = comparison()
    record.update(value=0.0, reason=None, matched_count=2, expected_candidate_count=2,
                  reference_coverage=1.0, candidate_coverage=1.0,
                  unmatched_reference=[], unmatched_candidate=[])
    record.pop('subset')
    design = Design(maturation_rmsd=99, maturation_selected_rmsd=99,
                             maturation_nonselected_rmsd=99, confidence_metrics={})
    _apply_ppiflow_score_fields(design, {'comparisons': {'whole_binder': record}, 'rmsd_backbone': 99})
    assert design.maturation_rmsd == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['valid', 'changed_candidate', 'changed_reference', 'foreign_request', 'missing_request', 'missing_comparisons'])
@pytest.mark.parametrize('marked', [True, False])
async def test_actual_ingestion_checks_comparison_binding_before_credit(tmp_path, case, marked):
    import hashlib
    import json
    from copy import deepcopy
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import undefer
    from database import Base, Job
    from services.result_ingester import ingest_maturation_data
    from test_ppiflow_rank_ingress import STRUCTURE

    candidate, reference = tmp_path / 'candidate.pdb', tmp_path / 'reference.pdb'
    candidate.write_bytes(STRUCTURE)
    reference.write_bytes(STRUCTURE + b'REMARK reference\n')
    digest = lambda data: hashlib.sha256(data).hexdigest()
    request = {'candidate_sha256': digest(candidate.read_bytes()),
               'reference_sha256': digest(reference.read_bytes()),
               'roles': {role: {'binder': ['H'], 'target': ['A']} for role in ('reference', 'candidate')},
               'domains': {'whole_binder': {'reference': [['H', 1, '']],
                                           'candidate': [['H', 1, '']],
                                           'pairs': [[['H', 1, ''], ['H', 1, '']]]}}}
    request_path = tmp_path / 'candidate.pdb.comparison.json'
    request_path.write_text(json.dumps(request))
    record = comparison()
    record.update(value=0.0, reason=None, matched_count=1, expected_reference_count=1,
                  expected_candidate_count=1, reference_coverage=1.0, candidate_coverage=1.0,
                  unmatched_reference=[], unmatched_candidate=[])
    record.pop('subset')
    score = {'candidate_sha256': request['candidate_sha256'],
             'reference_sha256': request['reference_sha256'],
             'comparison_request_sha256': digest(request_path.read_bytes()),
             'comparisons': {'whole_binder': record}, 'rmsd_backbone': 99}
    if case == 'changed_candidate':
        candidate.write_bytes(STRUCTURE + b'REMARK changed\n')
    elif case == 'changed_reference':
        reference.write_bytes(STRUCTURE)
    elif case == 'foreign_request':
        score['comparison_request_sha256'] = digest(b'other request')
    elif case == 'missing_request':
        request_path.unlink()
    elif case == 'missing_comparisons':
        score.pop('comparisons')
    (tmp_path / 'candidate_maturation_score.json').write_text(json.dumps(score))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingress.sqlite'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            job = Job(id='job', name='job', model_id='maturation_child', mode='maturation',
                      status='completed', params={},
                      provenance={'core_protein_scientific_contract': 1} if marked else {})
            design = Design(id='candidate', job_id=job.id, name='candidate',
                            pdb_path=str(candidate), source_pdb_path=str(reference),
                            confidence_metrics={'keep': 'existing'}, maturation_rmsd=None)
            session.add_all([job, design])
            await session.commit()
            before = deepcopy(design.confidence_metrics)
            if marked and case != 'valid':
                with pytest.raises(ValueError, match='maturation comparison'):
                    await ingest_maturation_data(job.id, tmp_path, session)
                assert design.confidence_metrics == before
                assert design.maturation_rmsd is None
                assert not session.dirty
            else:
                assert await ingest_maturation_data(job.id, tmp_path, session) == 1
        async with sessions() as session:
            reloaded = await session.get(Design, 'candidate', options=[undefer(Design.maturation_rmsd)])
            if marked and case != 'valid':
                assert reloaded.maturation_rmsd is None
                assert reloaded.confidence_metrics == {'keep': 'existing'}
            elif case == 'missing_comparisons':
                assert reloaded.maturation_rmsd == 99
                assert 'maturation_comparisons' not in reloaded.confidence_metrics
                assert reloaded.confidence_metrics['keep'] == 'existing'
            else:
                assert reloaded.maturation_rmsd == 0.0
                assert reloaded.confidence_metrics['maturation_comparisons'] == score['comparisons']
                assert reloaded.confidence_metrics['keep'] == 'existing'
    finally:
        await engine.dispose()
