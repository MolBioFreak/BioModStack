"""CPU-only request/admission/compiler/Groovy/preparation composition.

Disposable ORM only; source resolution and geometry are fixture boundaries.
No scheduler task, native inference, live API, model output or GPU is used.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from database import Job
from routers import jobs
from test_core_protein_scientific_admission import admission
from services.fampnn_policy_admission import compile_declaration, guard_cached_declaration

ROOT = Path(__file__).resolve().parents[3]


def pdb(domain):
    return ''.join(f'ATOM  {i:5d}  CA  ALA {c}{n:4d}{ins:1s}   {float(i):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C\n'
                   for i, (c, n, ins) in enumerate(domain, 1)) + 'END\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('selection', ['keep', 'change', 'neither'])
@pytest.mark.parametrize('sidechains', [False, True])
async def test_request_to_compiled_receiving_and_per_input_masks(admission, tmp_path, monkeypatch, selection, sidechains):
    import routers.files as files
    import services.binder_source_materialization as bsm
    import paths
    from services.nextflow import compile_job_nextflow_invocation
    from fampnn_policy_resolution import prep_receipt, resolve_declaration

    monkeypatch.setattr(paths, 'get_code_root', lambda: ROOT)
    monkeypatch.setenv('BMS_HOME', str(ROOT))
    selected = tmp_path / 'selection'; selected.mkdir()
    sources = []
    domains = [[('A', 10, ''), ('A', 12, ''), ('B', 21, '')],
               [('A', 110, ''), ('A', 112, ''), ('B', 221, '')]]
    for i, domain in enumerate(domains):
        source = tmp_path / f'source{i}.pdb'; source.write_text(pdb(domain)); sources.append(source)
    monkeypatch.setattr(jobs, '_create_generic_selection_dir', lambda *a: selected)
    monkeypatch.setattr(files, 'is_path_allowed', lambda p: True)
    monkeypatch.setattr(jobs, 'resolve_allowed_path', lambda p: Path(p))
    async def resolve(request, session):
        return Path(request.path), {}
    monkeypatch.setattr(bsm, 'resolve_structure_source', resolve)
    # Transport source bytes unchanged, without claiming a geometry engine run.
    def materialize(raw, suffix, dest, **kwargs):
        output = Path(dest) / 'materialized.pdb'
        output.write_bytes(raw)
        return {'path': str(output)}
    monkeypatch.setattr(bsm, 'materialize_source_bytes', materialize)
    async def create(request, background, session):
        request.params['fix_fixed_sidechains'] = sidechains
        return await jobs._create_job(request, background, session)
    monkeypatch.setattr(jobs, 'create_job', create)
    selectors = ({'fixed_positions': 'A10,A110'} if selection == 'keep' else
                 {'designable_positions': 'A12,A112'} if selection == 'change' else {})
    request = jobs.ResequenceLaunchRequest(sources=[{'path': str(p)} for p in sources],
        design_chains='A', seq_method='fampnn', **selectors)
    response = await jobs.launch_resequence_from_structures(request, BackgroundTasks(), admission)
    admission.expire_all()
    row = await admission.get(Job, response.id)
    declaration = row.provenance['fampnn_analysis_declaration']
    assert row.params['fampnn_analysis_declaration'] == declaration
    assert declaration['input_domain'] is None
    assert len(declaration['inputs']) == 2
    invocation = compile_job_nextflow_invocation(row, row.params, str(tmp_path / 'compiled'))
    # Qualify the selected worker source closure, not just host imports.
    plan = invocation.execution_plan
    assert plan is not None
    prep = next(component for component in plan.metadata.static_components
                if component.authority == 'workflows/protein_local_redesign.nf:PrepProteinLocalFAMPNN')
    helpers = {'scripts/prep_fampnn_constraints_from_spec.py', 'scripts/prep_fampnn_designs.py',
               'scripts/fampnn_policy_resolution.py', 'scripts/antibody_fampnn_provenance.py'}
    assert {'support_tool:' + path for path in helpers} <= set(prep.dependency_ids)
    assert any(role.native_declaration == 'val fixed_args' and role.format == 'native_value'
               for role in plan.metadata.artifact_roles if role.role_id in prep.input_role_ids)
    from services.remote_execution import bundle
    monkeypatch.setattr(bundle, 'get_code_root', lambda: ROOT)
    assert bundle._runtime_assets(row.model_id, row.mode, invocation.native_parameters,
        selected_plan=plan, only_kinds=frozenset({'support_tool'})) == []
    invocation.materialize_inputs(tmp_path / 'compiled')
    command = list(invocation.command)
    transport = Path(command[command.index('--fampnn_analysis_declaration_path') + 1])
    assert json.loads(transport.read_text()) == declaration
    assert command[command.index('--fampnn_analysis_declaration_sha256') + 1] == hashlib.sha256(transport.read_bytes()).hexdigest()
    native_params = {command[i][2:]: command[i+1] for i in range(len(command)-1) if command[i].startswith('--')}
    native_params['core_protein_scientific_contract'] = 1
    params_file = tmp_path / 'params.json'; params_file.write_text(json.dumps(native_params))

    # Execute the actual workflow argument construction and policy receiver.
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file(), 'BMS_TEST_NEXTFLOW_JAR required for real Groovy receiving'
    workflow = (ROOT / 'workflows/protein_local_redesign.nf').read_text()
    construction = workflow.split('    def manifestArg =', 1)[1].split('    def mpnnDesignableArgs', 1)[0]
    harness = tmp_path / 'receiving.groovy'
    harness.write_text('''
class Channel { static def value(v) { v } }
def shellQuote(v) { "'" + v.toString().replace("'", "'\\\"'\\\"'") + "'" }
def params = new groovy.json.JsonSlurper().parse(new File(args[0]))
assert FampnnAnalysisPolicy.forWorkflow(params, 'protein_local_redesign', 'sequence_redesign_positions_spec').declaration.inputs.size() == 2
''' + 'def manifestArg =' + construction + '\nprintln fampnnFixedArgs\n')
    result = subprocess.run(['java', '-cp', jar + os.pathsep + str(ROOT / 'lib'),
        'groovy.ui.GroovyMain', str(harness), str(params_file)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

    prepared = tmp_path / 'prepared'; prepared.mkdir()
    receipts = {}
    for i, source in enumerate(sorted(selected.glob('*.pdb'))):
        # Deliberately different source/prepared numbering. Pairs stand for
        # residue-object provenance, not a computed alignment.
        output_domain = [('A', 1+i*10, ''), ('A', 2+i*10, ''), ('B', 3+i*10, '')]
        output = prepared / source.name; output.write_text(pdb(output_domain))
        source_ids = declaration['inputs'][source.stem]['input_domain']
        target_ids = [f'{c}:{n}:{ins}' for c, n, ins in output_domain]
        receipt = prep_receipt(source, output, list(zip(source_ids, target_ids)))
        output.with_suffix('.fampnn_prep.json').write_text(json.dumps(receipt))
        receipts[source.stem] = receipt
    import shlex
    csv_path = tmp_path / 'native.csv'
    subprocess.run([sys.executable, str(ROOT / 'scripts/prep_fampnn_constraints_from_spec.py'),
        '--input_dir', str(prepared), '--out_csv', str(csv_path), *shlex.split(result.stdout.strip()),
        *(['--fix_sidechains'] if sidechains else [])], check=True)
    rows = list(csv.reader(csv_path.open()))
    assert len(rows) == 2 and all(len(r) == 3 for r in rows)
    scopes = resolve_declaration(declaration, receipts, prepared)
    for i, (name, sequence, sidechain) in enumerate(rows):
        expected = f'A{1+i*10},B{3+i*10}' if selection != 'neither' else f'B{3+i*10}'
        assert sequence == expected
        assert sidechain == (expected if sidechains else '')
        entry = scopes['inputs'][name]
        expected_region = [f'A:{2+i*10}:'] if selection != 'neither' else [f'A:{1+i*10}:', f'A:{2+i*10}:']
        assert entry['summary'] == entry['sequence_design'] == expected_region
        assert entry['summary_override'] is None
        assert entry['mutation_override'] == expected_region
        assert 'producer_candidate_ids' not in entry['artifact_binding']


@pytest.mark.parametrize('value', [None, []])
def test_collection_overrides_child_and_cached_dependencies(tmp_path, value):
    source = tmp_path / 'source.pdb'; source.write_text(pdb([('A', 10, ''), ('B', 21, '')]))
    params = dict(backbone_input_pdbs=str(tmp_path), design_chains='A', seq_method='fampnn')
    declaration = compile_declaration('protein_modification_experimental', 'region_redesign', params,
                                     {'summary': value, 'mutation': value})
    concrete = declaration['inputs']['source']
    assert concrete['summary_override'] == concrete['mutation_override'] == value
    from fampnn_policy_resolution import prep_receipt, resolve_declaration
    receipt = prep_receipt(source, source, [('A:10:', 'A:10:'), ('B:21:', 'B:21:')])
    # Optional upstream metadata must not redefine this selected PDB's domain.
    receipt['local_redesign_source_mapping'] = {'atoms': []}
    entry = resolve_declaration(declaration, {'source': receipt}, tmp_path)['inputs']['source']
    assert entry['summary_override'] == value
    assert entry['mutation_override'] == (['A:10:'] if value is None else [])
    parent = SimpleNamespace(provenance={'fampnn_analysis_declaration': declaration})
    child = compile_declaration('fampnn_child', 'sequence_design', {}, {'summary': [], 'mutation': []}, parent=parent)
    assert child['inputs']['source']['summary_override'] == child['inputs']['source']['mutation_override'] == []
    guard_cached_declaration(declaration, params, {'backbone_input_pdbs': str(tmp_path)})
    # Sidechain sampling is not sequence/summary declaration authority. Preserve
    # its existing independent retry control, without adding a new refusal.
    guard_cached_declaration(declaration, params, {'fix_fixed_sidechains': True})
    guard_cached_declaration(declaration, params, {'plr_fix_fixed_sidechains': True})
    for key in ('backbone_input_pdbs', 'designable_positions', 'fixed_positions', 'design_chains'):
        for prefix in ('', 'plr_'):
            with pytest.raises(ValueError, match='fresh admission'):
                guard_cached_declaration(declaration, params, {prefix + key: 'changed'})


def test_sequence_only_continuation_retains_no_fampnn_declaration():
    assert compile_declaration('protein_modification_experimental', 'region_redesign',
        {'sequence_input_pdbs': '/unused', 'seq_method': 'fampnn'}) is None


def test_absent_selector_and_insertions_are_not_aligned(tmp_path):
    from prep_fampnn_constraints_from_spec import sequence_scope
    domain = ['A:10:', 'A:10:A', 'A:112:', 'B:21:']
    assert sequence_scope(domain, 'A', '', 'A12') == []
    assert sequence_scope(domain, 'A', 'A10A', '') == ['A:10:', 'A:112:']
    assert sequence_scope(domain, 'A', '', 'A:10A') == ['A:10:A']
