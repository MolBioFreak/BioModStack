"""CPU-only antibody declaration tests; no image/model execution."""
from types import SimpleNamespace
import pytest
from services.fampnn_policy_admission import compile_declaration
from test_core_protein_scientific_admission import admission


@pytest.fixture(autouse=True)
def isolated_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))


def test_antibody_admission_defers_physical_identity_and_inherits_child():
    declaration = compile_declaration('antibody_denovo', 'antibody_denovo_pipeline', {
        'backbone_method': 'rfantibody', 'seq_method': 'fampnn',
        'framework_type': 'nanobody', 'antibody_design_mode': 'framework_allowed',
    })
    assert declaration is not None
    assert declaration['owner'] == 'antibody_denovo'
    assert declaration['allow_summary_override'] is False
    assert declaration['materialization']['summary'] == 'authorized_antibody_domain'
    assert declaration['materialization']['mutation'] == 'resolved_cdrs'
    assert declaration['input_domain'] is None
    assert 'artifact_binding' not in str(declaration)
    child = compile_declaration('fampnn_child', 'sequence_design', {},
        parent=SimpleNamespace(provenance={'fampnn_analysis_declaration': declaration}))
    assert child == declaration


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', [None, [], [{'chain_id':'H', 'author_number':2}]])
@pytest.mark.parametrize('attack', [None, 'foreign_parent', 'swapped_roles', 'resealed_foreign'])
async def test_antibody_request_prepared_child_analyzer(admission, monkeypatch, tmp_path, mutation, attack):
    import json, sys, subprocess, pickle
    import numpy as np
    from pathlib import Path
    from fastapi import BackgroundTasks
    from database import Job
    from routers import jobs
    from schemas import JobCreate
    from services import core_protein_scientific_contract as contract
    from services.nextflow import build_nextflow_command
    from antibody_fampnn_provenance import native_export, NATIVE_SOURCES
    from fampnn_policy_resolution import prep_receipt, resolve_declaration, bind_native_candidates
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('antibody_denovo','antibody_denovo_pipeline'), ('fampnn_child','sequence_design')}))
    target = tmp_path/'target.pdb'
    target.write_text('ATOM      1  CA  ALA T   1       0.000   0.000   0.000  1.00 20.00           C\n')
    framework = tmp_path/'framework.pdb'
    framework.write_text('ATOM      1  CA  ALA H   1       0.000   0.000   0.000  1.00 20.00           C\n')
    request = JobCreate(name='antibody', model_id='antibody_denovo', mode='antibody_denovo_pipeline',
        params={'target_pdb':str(target), 'epitope_residues':'T1', 'framework_pdb':str(framework),
                'antibody_design_mode':'framework_allowed', 'framework_type':'nanobody',
                'seq_design_fampnn':True, 'protect_vhh_tetrad':False},
        fampnn_analysis_overrides={'mutation':mutation})
    response = await jobs._create_job(request, BackgroundTasks(), admission)
    admission.expire_all()
    parent = await admission.get(Job, response.id)
    declaration = parent.provenance['fampnn_analysis_declaration']
    prepared = Path(parent.output_dir) / 'prep' / 'fampnn'
    prepared.mkdir(parents=True)
    pdb = prepared / 'design.pdb'
    lines = [f'ATOM  {i:5d}  CA  ALA {chain}{i:4d}    {0:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C'
             for i, chain in enumerate(['H','H','T'], 1)]
    from antibody_fampnn_provenance import input_sources
    from normalize_target_pdb import normalize_pdb
    normalized_target = tmp_path/'normalized_target.pdb'
    normalize_pdb(target, normalized_target, set(), True)
    assert normalized_target.read_bytes() != target.read_bytes()
    sources = input_sources(framework, normalized_target)
    assert sources['framework']['sha256'] != sources['target']['sha256']
    pdb.write_text('\n'.join(native_export(lines, ['H','H','T'], {'H1':[2]}, NATIVE_SOURCES, sources)))
    native_parent = Path(parent.output_dir)/'collected'/'rfantibody_raw'/'design.pdb'
    native_parent.parent.mkdir(parents=True)
    native_parent.write_bytes(pdb.read_bytes())
    if attack is None:
        from antibody_fampnn_provenance import carry_export
        intermediate = tmp_path/'intermediate.pdb'
        intermediate.write_text('\n'.join(line.replace('H   1', 'H  11').replace('H   2', 'H  12') for line in lines))
        carry_export(native_parent, intermediate, [('H:1:','H:11:'), ('H:2:','H:12:'), ('T:3:','T:3:')])
        pdb.write_text('\n'.join(lines))
        carry_export(intermediate, pdb, [('H:11:','H:1:'), ('H:12:','H:2:'), ('T:3:','T:3:')])
    if attack == 'foreign_parent':
        prepared = tmp_path/'foreign-parent'/'prep'/'fampnn'
        prepared.mkdir(parents=True)
        pdb = prepared/'design.pdb'
        pdb.write_bytes(native_parent.read_bytes())
    elif attack in {'swapped_roles', 'resealed_foreign'}:
        if attack == 'swapped_roles':
            sources = input_sources(target, framework)
        else:
            foreign = tmp_path/'foreign-target.pdb'
            foreign.write_text(target.read_text().replace('ALA', 'GLY'))
            sources = input_sources(framework, foreign)
        pdb.write_text('\n'.join(native_export(lines, ['H','H','T'], {'H1':[2]}, NATIVE_SOURCES, sources)))
        if attack == 'resealed_foreign':
            native_parent.write_bytes(pdb.read_bytes())
    receipt = prep_receipt(pdb, pdb, [(v,v) for v in ['H:1:', 'H:2:', 'T:3:']])
    pdb.with_suffix('.fampnn_prep.json').write_text(json.dumps(receipt))
    cmd = [sys.executable, str(root/'scripts/prep_antibody_constraints.py'), '--input_dir', str(prepared),
           '--out_fampnn', str(tmp_path/'fixed.csv'), '--out_mpnn', str(tmp_path/'fixed.json'),
           '--design_mode','framework_allowed', '--protect_tetrad','false',
           '--require_role_provenance', '--prepared_dir',str(prepared), '--cdr_positions','H99']
    result = subprocess.run(cmd, capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(pdb.with_suffix('.fampnn_prep.json').read_text())
    from copy import deepcopy
    bad_declaration = deepcopy(declaration)
    bad_declaration['materialization']['source'] = 'unadmitted-source'
    with pytest.raises(ValueError, match='unsupported'):
        resolve_declaration(bad_declaration, {'design':receipt}, prepared)
    import spawn_fampnn_children as spawn
    captured = []
    monkeypatch.setattr(spawn, 'check_existing_children', lambda *a, **kw: (False, [], {}))
    def post(url, json, **kwargs):
        captured.append(json)
        return SimpleNamespace(ok=True, json=lambda: {'id':'captured'})
    monkeypatch.setattr(spawn.requests, 'post', post)
    spawn.spawn_fampnn_jobs(parent.id, str(prepared), 1, 1, 'antibody',
        params_json=json.dumps({'core_protein_scientific_contract':1,
                               'fampnn_analysis_declaration':declaration}))
    child_request = JobCreate.model_validate(captured[0])
    from fastapi import HTTPException
    if attack:
        from sqlalchemy import select
        before_jobs = len(list((await admission.execute(select(Job))).scalars()))
        before_dirs = set(Path(parent.output_dir).parent.iterdir())
        with pytest.raises(HTTPException) as exc:
            await jobs._create_job(child_request, BackgroundTasks(), admission)
        assert exc.value.status_code == 422
        assert len(list((await admission.execute(select(Job))).scalars())) == before_jobs
        assert set(Path(parent.output_dir).parent.iterdir()) == before_dirs
        # Fresh retries must re-resolve the same original scientific parent.
        from fastapi import Request, Response
        failed = Job(id='foreign-retry', name='foreign-retry', model_id='fampnn_child',
            mode='sequence_design', status='failed', params=child_request.params,
            parent_job_id=parent.id, child_stage='fampnn',
            provenance={'core_protein_scientific_contract':1,
                        'fampnn_analysis_declaration':declaration},
            output_dir=str(tmp_path/'old-child'))
        admission.add(failed)
        await admission.commit()
        before_jobs += 1
        http = Request({'type':'http','method':'POST','scheme':'http','path':'/','headers':[]})
        with pytest.raises(HTTPException) as exc:
            await jobs.resubmit_job(failed.id, http, Response(), admission)
        assert exc.value.status_code == 422
        assert len(list((await admission.execute(select(Job))).scalars())) == before_jobs
        assert set(Path(parent.output_dir).parent.iterdir()) == before_dirs
        return
    from services.fampnn_policy_admission import FampnnAnalysisOverrides
    forbidden = child_request.model_copy(deep=True)
    forbidden.fampnn_analysis_overrides = FampnnAnalysisOverrides.model_validate({'mutation':[{'chain_id':'H','author_number':1}]})
    with pytest.raises(HTTPException) as exc:
        await jobs._create_job(forbidden, BackgroundTasks(), admission)
    assert exc.value.status_code == 422
    forbidden.fampnn_analysis_overrides = FampnnAnalysisOverrides.model_validate({'summary':[]})
    with pytest.raises(HTTPException) as exc:
        await jobs._create_job(forbidden, BackgroundTasks(), admission)
    assert exc.value.status_code == 422
    child_request.fampnn_analysis_overrides = FampnnAnalysisOverrides.model_validate({'mutation': [] if mutation == [] else [{'chain_id':'H','author_number':2}]})
    child_response = await jobs._create_job(child_request, BackgroundTasks(), admission)
    child = await admission.get(Job, child_response.id)
    expected_child = dict(declaration, mutation_override=[] if mutation == [] else ['H:2:'])
    import hashlib
    expected_child['materialization'] = dict(declaration['materialization'],
        origins=[hashlib.sha256(native_parent.read_bytes()).hexdigest()])
    assert child.provenance['fampnn_analysis_declaration'] == expected_child
    command = build_nextflow_command(child.model_id, child.mode, child.params, str(tmp_path/'command'), job_id=child.id)
    assert command[command.index('--fampnn_constraint_mode')+1] == 'antibody'
    assert command[command.index('--antibody_design_mode')+1] == 'framework_allowed'
    transport = json.loads(Path(command[command.index('--fampnn_analysis_declaration_path')+1]).read_text())
    scopes = resolve_declaration(transport, {'design':receipt}, prepared)
    assert scopes['inputs']['design']['summary'] == ['H:1:', 'H:2:']
    assert scopes['inputs']['design']['mutation_override'] == ([] if mutation == [] else ['H:2:'])
    native = tmp_path/'native'; native.mkdir()
    (native/'design_sample0.pdb').write_bytes(pdb.read_bytes())
    policy = bind_native_candidates(scopes, native)
    policy_path = tmp_path/'policy.json'; policy_path.write_text(json.dumps(policy))
    pkls = tmp_path/'pkls'; pkls.mkdir()
    (pkls/'design_sample0.pkl').write_bytes(pickle.dumps(dict(
        seq_probs=np.tile(np.eye(21)[1]*0.2 + np.eye(21)[2]*0.8, (3,1)),
        pred_aatype=np.array([1,1,1]), seq_mask=np.ones(3), aatype_override_mask=np.array([0,0,1]),
        chain_index=np.array([0,0,1]), residue_index=np.array([1,2,3]))))
    output = tmp_path/'analysis.jsonl'
    result = subprocess.run([sys.executable,str(root/'scripts/analyse_fampnn_seq_probs.py'),
        '--sample-pkl-dir',str(pkls),'--out-jsonl',str(output),'--out-csv',str(tmp_path/'analysis.csv'),
        '--core-protein-scientific-contract','1','--analysis-policy',str(policy_path),
        '--source-pdb-dir',str(prepared),'--candidate-pdb-dir',str(native)],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    row = json.loads(output.read_text())
    assert row['analysis_policy'] == policy
    assert parent.provenance['fampnn_analysis_declaration'] == declaration



@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'framework_pdb':'foreign.pdb'}, {'framework_type':'custom'},
    {'target_pdb':'foreign.pdb'}, {'antibody_design_mode':'full_design'},
    {'antibody_design_loops':'H3'}, {'protect_vhh_tetrad':False},
    {'lock_antibody_framework':False}, {'lock_target_chains':False},
    {'pdb_paths':'foreign.pdb'}, {'antigen_chains':'B'},
    {'rfantibody_loop_length_ranges':'[H3:20]'},
    {'manual_mutation_fixed_positions_json':'foreign.json'},
    {'fampnn_constraint_mode':'generic'},
    *[{key: 'foreign-cohort'} for key in (
        'selected_input_dir', 'iteration_selection_dir', 'rfantibody_input_pdbs',
        'fampnn_collected_pdbs', 'selected_input_manifest', 'source_selection_manifest_path',
        'selected_input_source_job_id', 'source_stage_job_id', 'selection_source_job_id',
        'iteration_source_job_id', 'selected_input_stage_family', 'source_stage_family',
        'selected_input_stage_mode', 'source_stage_mode', 'iteration_source_design_ids',
        'source_selection_count', 'selected_loop_scope', 'skip_rfantibody')],
])
@pytest.mark.parametrize('model', ['antibody_denovo', 'fampnn_child'])
async def test_cached_antibody_resume_rejects_biology_before_writes(admission, tmp_path, changes, model):
    from fastapi import Request, Response, HTTPException
    from database import Job
    from routers import jobs
    from sqlalchemy import select
    target = tmp_path/'target.pdb'
    framework = tmp_path/'framework.pdb'
    target.write_text('ATOM      1  CA  ALA T   1       0.000   0.000   0.000  1.00 20.00           C\n')
    framework.write_text('ATOM      1  CA  ALA H   1       0.000   0.000   0.000  1.00 20.00           C\n')
    params = {'target_pdb':str(target), 'framework_pdb':str(framework),
              'epitope_residues':'T1', 'framework_type':'nanobody', 'seq_design_fampnn':True}
    cohort_keys = {'selected_input_dir', 'iteration_selection_dir',
                   'rfantibody_input_pdbs', 'fampnn_collected_pdbs'}
    if cohort_keys.intersection(changes):
        foreign = tmp_path/'foreign-cohort'
        foreign.mkdir()
        (foreign/'foreign.pdb').write_bytes(framework.read_bytes())
        changes = {key:str(foreign) for key in changes}
    declaration = compile_declaration('antibody_denovo', 'antibody_denovo_pipeline', params)
    original = Job(id='resume-antibody', name='antibody', model_id=model,
        mode='antibody_denovo_pipeline' if model == 'antibody_denovo' else 'sequence_design', status='failed', params=params,
        provenance={'core_protein_scientific_contract':1, 'fampnn_analysis_declaration':declaration},
        output_dir=str(tmp_path/'original'))
    admission.add(original)
    await admission.commit()
    request = Request({'type':'http','method':'POST','scheme':'http','path':'/','headers':[]})
    with pytest.raises(HTTPException) as exc:
        await jobs.resume_job(original.id, request, Response(),
            request=jobs.ResumeJobRequest(param_overrides=changes), session=admission)
    assert exc.value.status_code == 422
    assert len(list((await admission.execute(select(Job))).scalars())) == 1
    assert not (tmp_path/'original').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['antibody_denovo', 'fampnn_child'])
@pytest.mark.parametrize('revision', [None, 1])
async def test_unchanged_antibody_resume_retains_authority_with_resource_changes(admission, tmp_path, model, revision):
    from copy import deepcopy
    from fastapi import Request, Response
    from database import Job
    from routers import jobs
    from services.core_protein_scientific_contract import revision_for_job
    params = {'antibody_design_mode':'framework_allowed', 'pdb_paths':'unchanged.pdb'}
    declaration = compile_declaration('antibody_denovo', 'antibody_denovo_pipeline', params)
    provenance = {'fampnn_analysis_declaration':declaration}
    if revision is not None:
        provenance['core_protein_scientific_contract'] = revision
    original = Job(id='unchanged-resume', name='antibody', model_id=model,
        mode='antibody_denovo_pipeline' if model == 'antibody_denovo' else 'sequence_design',
        status='failed', params=params, provenance=provenance, output_dir=str(tmp_path/'original'))
    admission.add(original)
    await admission.commit()
    before = deepcopy((original.params, original.provenance))
    request = Request({'type':'http','method':'POST','scheme':'http','path':'/','headers':[]})
    result = await jobs.resume_job(original.id, request, Response(),
        request=jobs.ResumeJobRequest(param_overrides={**params, 'pinned_gpu':2}), session=admission)
    resumed = await admission.get(Job, result['new_job_id'])
    assert revision_for_job(resumed) == revision
    assert resumed.provenance['fampnn_analysis_declaration'] == declaration
    assert resumed.params['pinned_gpu'] == 2
    assert (original.params, original.provenance) == before


def test_antibody_summary_override_rejected_before_materialization():
    with pytest.raises(ValueError, match='summary override forbidden'):
        compile_declaration('antibody_denovo', 'antibody_denovo_pipeline', {}, {'summary': []})


@pytest.mark.parametrize('owner,mode', [('rfdiffusion','binder_design'), ('rfdiffusion','design')])
def test_held_legacy_generated_callers_cannot_compile(owner, mode):
    with pytest.raises(ValueError, match='held|unadmitted'):
        compile_declaration(owner, mode, {})


def test_unsupported_parent_declaration_rejected():
    with pytest.raises(ValueError, match='unsupported'):
        compile_declaration('fampnn_child', 'sequence_design', {},
            parent=SimpleNamespace(provenance={'fampnn_analysis_declaration': {'owner':'unknown'}}))
