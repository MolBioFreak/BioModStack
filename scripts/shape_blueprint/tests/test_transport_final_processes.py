"""Offline real Nextflow publication: no inference or external endpoints."""
import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

from test_one_copy_shape_followup import nextflow

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'platform/api/tests'))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('NXF_HOME', str(tmp_path / 'nxf-home'))
    (tmp_path / 'lib').symlink_to(ROOT / 'lib', target_is_directory=True)


def test_completed_workflow_preview_publication_and_actual_receiving(tmp_path):
    from test_local_gate_review_rows import PDB
    from test_shape_result_ingestion import _session_factory
    from services import nextflow as compiler
    from routers.jobs import _execution_plan_preview
    from schemas import JobCreate
    from services.result_ingester import ingest_loose_files
    from database import Job, Design
    from sqlalchemy import select
    source = tmp_path / 'selected'
    source.mkdir()
    (source / 'one.pdb').write_text(PDB)
    params = dict(modification_mode='region_redesign', plr_validation_input_pdbs=str(source),
                  plr_final_candidate_dir=str(source), interactive_gate_continue=True,
                  interactive_gating=False)
    request = JobCreate(name='inert-selected-final', model_id='protein_modification_experimental',
                        mode='region_redesign', params=params)
    preview = _execution_plan_preview(request)
    assert preview['admissible'], preview['blockers']
    invocation = compiler.compile_nextflow_invocation(request.model_id, request.mode, params,
                                                      str(tmp_path / 'published'), job_id='final')
    assert invocation.native_parameters['plr_validation_input_pdbs'] == str(source)
    workflow = f"include {{ PROTEIN_LOCAL_REDESIGN }} from '{ROOT}/workflows/protein_local_redesign'\nworkflow {{ PROTEIN_LOCAL_REDESIGN() }}"
    run = nextflow(tmp_path, workflow)
    args = ('--plr_validation_input_pdbs', str(source), '--interactive_gating', 'false')
    rows = run(*args)
    assert len(rows) == 1 and 'FinalizeProteinLocalSelectedResults' in rows[0]['name']
    assert (tmp_path / 'published/final_candidates/one.pdb').read_text() == PDB
    shutil.rmtree(tmp_path / 'published')
    assert {x['status'] for x in run(*args, '-resume')} == {'CACHED'}
    shutil.rmtree(source)
    shutil.rmtree(tmp_path / 'work')
    async def receive():
        factory, engine = await _session_factory(tmp_path)
        try:
            async with factory() as session:
                job = Job(id='final', name=request.name, model_id=request.model_id, mode=request.mode,
                    params=params, status='completed', output_dir=str(tmp_path / 'published'))
                session.add(job)
                await session.commit()
                assert await ingest_loose_files(job.id, Path(job.output_dir), session, current_job=job) == 1
                await session.commit()
            async with factory() as session:
                design = (await session.scalars(select(Design))).one()
                assert Path(design.pdb_path).read_text() == PDB
                assert Path(design.pdb_path).is_relative_to(tmp_path / 'published/final_candidates')
        finally:
            await engine.dispose()
    asyncio.run(receive())


@pytest.mark.parametrize('scores', [False, True])
def test_gate_receives_closed_channels_not_async_publishdir(tmp_path, scores):
    from test_local_gate_review_rows import PDB
    from component_runtime import ComponentRuntime
    from scripts.open_stage_gate import open_component_gate
    (tmp_path / 'one.pdb').write_text(PDB)
    (tmp_path / 'receipt.json').write_text('{"state":"complete"}')
    (tmp_path / 'complete').touch()
    score = tmp_path / 'one_summary_confidence_sample_0.json'
    score.write_text('{"ptm":0.0,"plddt":0.5,"ranking_score":0.0,"has_clash":false}')
    score_input = 'OptionalScores.out.scores.collect().ifEmpty { [] }'
    score_script = f"sleep 1; cp '{score}' one_summary_confidence_sample_0.json" if scores else 'exit 1'
    # Drive either installed native signature so the baseline observes the
    # publishDir race, rather than failing merely on a new process input arity.
    from native_components import PROCESS_CONTRACTS
    receives_scores = len(PROCESS_CONTRACTS['workflows/protein_local_redesign.nf:StageProteinLocalValidatedCandidates'][1]) > 3
    if receives_scores:
        # Use the workflow's real optional producer expression, including empty
        # channel handling, rather than a test-only replacement for its semantics.
        source_text = (ROOT / 'workflows/protein_local_redesign.nf').read_text()
        binding = next(line for line in source_text.splitlines()
                       if "selectedValidators.contains('protenix_v2') ?" in line)
        score_input = binding.split(' ? ', 1)[1].split(' : ', 1)[0].replace(
            'ProtenixFromComplex.out.confidence', 'OptionalScores.out.scores')
    extra = f", [], [], {score_input}" if receives_scores else ''
    score_output = (f"StageProteinLocalValidatedCandidates.out.scores.map {{ it.toString() }}.collectFile(name:'score-path.txt', storeDir:'{tmp_path}/paths', newLine:true)"
                    if receives_scores else '')
    workflow = f"""include {{ StageProteinLocalValidatedCandidates }} from '{ROOT}/workflows/protein_local_redesign'
process OptionalScores {{
 errorStrategy 'ignore'
 output:
 path 'one_summary_confidence_sample_0.json', emit: scores, optional: true
 script:
 "{score_script}"
}}
workflow {{
 OptionalScores()
 StageProteinLocalValidatedCandidates([file('{tmp_path}/one.pdb')], file('{tmp_path}/receipt.json'), file('{tmp_path}/complete'){extra})
 StageProteinLocalValidatedCandidates.out.candidates.map {{ files -> (files instanceof Collection ? files[0] : files).parent.toString() }}.collectFile(name:'candidate-path.txt', storeDir:'{tmp_path}/paths', newLine:true)
 {score_output}
}}
"""
    nextflow(tmp_path, workflow)()
    # Remove published names. Gate opens exclusively from closed task outputs.
    shutil.rmtree(tmp_path / 'published')
    candidates = Path((tmp_path / 'paths/candidate-path.txt').read_text().strip())
    if receives_scores:
        raw = Path((tmp_path / 'paths/score-path.txt').read_text().strip())
    else:
        raw = tmp_path / 'published/validation'
        raw.mkdir(parents=True)  # publisher has not populated scores yet
    (tmp_path / 'artifacts').mkdir()
    runtime = ComponentRuntime(tmp_path / 'ledger.sqlite', artifact_root=tmp_path / 'artifacts',
        attempt_id='attempt', root_job_id='root', target_id='worker', lease_id='lease',
        source_identity=dict(revision='a'*40, tree='b'*40))
    open_component_gate(runtime, job_id='root', stage='post_structure_validation',
        payload={'framework_type':'protein_local_redesign'}, directories={'candidate':candidates, 'raw':raw})
    checkpoint = runtime.pending_checkpoints()[0]['checkpoint']
    retained = [r for r in checkpoint['artifacts'] if r['schema'] == 'bms.stage-review.validator-scores.v1']
    assert bool(retained) is scores
    assert any(r['schema'] == 'bms.stage-review.candidate.v1' for r in checkpoint['artifacts'])
