"""Non-science contracts for selected native-operation composition and identity."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ppiflow_sample_identity import collect, instrument, publish_sample
from prep_caliby_binder_constraints import build_constraints
from publish_binder_refinement import publish


def atom(chain, number, serial=1):
    return f'ATOM  {serial:5d}  CA  ALA {chain}{number:4d}    {0:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C\n'


def test_caliby_constraints_keep_exact_chain_and_native_columns(tmp_path):
    (tmp_path / 'selected.pdb').write_text(atom('a', 1) + atom('a', 2, 2) + atom('2', 1, 3))
    row, = build_constraints(tmp_path, 'a', '2', 'a2', fixed_pos_override_seq='a2:G',
                             pos_restrict_aatype='a2:AG', symmetry_pos='a1,a2')
    assert row['fixed_pos_seq'] == '21-1,a1-1'
    assert row['fixed_pos_override_seq'] == 'a2:G'
    assert row['pos_restrict_aatype'] == 'a2:AG'
    assert row['symmetry_pos'] == 'a1,a2'


def test_ppiflow_identity_comes_from_producer_not_glob_order(tmp_path):
    native = tmp_path / 'native'
    native.mkdir()
    a, z = native / 'a.pdb', native / 'z.pdb'
    a.write_text('native sample 19')
    z.write_text('native sample 2')
    (native / 'unassociated.pdb').write_text('not a producer-associated sample')
    publish_sample(a, 19)
    publish_sample(z, 2)
    rows = collect(native, tmp_path / 'out', 'candidate', tmp_path / 'manifest.json')
    assert {r['sample_index'] for r in rows} == {2, 19}
    assert (tmp_path / 'out/candidate_ppiflow_sample19.pdb').read_text() == 'native sample 19'
    assert (tmp_path / 'out/candidate_ppiflow_sample2.pdb').read_text() == 'native sample 2'
    assert collect(tmp_path / 'empty', tmp_path / 'zero', 'x', tmp_path / 'zero.json') == []


def test_ppiflow_export_hook_leaves_native_body_and_result_unchanged(tmp_path):
    namespace = {'Path': Path}
    # Synthetic native producer, not scientific sampling. Index is deliberately
    # unrelated to output filename to disallow attribution by naming convention.
    source = '''class FlowModule:
    def test_step(self, batch, batch_idx):
        pdb_path = batch
        Path(pdb_path).write_text('unchanged native bytes')
'''
    exec(instrument(source, 'fixture_native.py'), namespace)
    output = tmp_path / 'unrelated.pdb'
    namespace['FlowModule']().test_step(str(output), 42)
    assert output.read_text() == 'unchanged native bytes'
    assert json.loads(Path(str(output) + '.sample.json').read_text())['sample_index'] == 42


def test_generic_redesign_reuses_global_masks_without_cdr_inference(tmp_path):
    from prep_binder_fampnn_constraints import prepare
    selected, prepared = tmp_path / 'selected', tmp_path / 'prepared'
    selected.mkdir()
    prepared.mkdir()
    text = atom('A', 1) + atom('A', 2, 2) + atom('B', 1, 3)
    (selected / 'candidate.pdb').write_text(text)
    (prepared / 'candidate.pdb').write_text(text)
    anchors = tmp_path / 'anchors.json'
    anchors.write_text(json.dumps({'anchors': [], 'analysis_status': 'not_run'}))
    request = dict(sequence_design_mode='binder_design', design_chain='A', target_chain='B',
                   fixed_positions='A:2', fampnn_fix_target_sidechains=False)
    rows = prepare(selected, prepared, tmp_path / 'constraints.csv', request, anchors)
    assert rows == [('candidate', 'A2,B1', '')]
    request['fampnn_fix_target_sidechains'] = True
    assert prepare(selected, prepared, tmp_path / 'constraints.csv', request, anchors) == [('candidate', 'A2,B1', 'B1')]


def test_refinement_publication_does_not_promote_parent_validation(tmp_path):
    pdb = tmp_path / 'descendant.pdb'
    pdb.write_text('non-science structure fixture')
    meta = dict(id='child', parent_id='parent', validation_status='unvalidated',
                terminal_producer='fampnn', source_meta={'id': 'parent', 'plddt': 99, 'validation_status': 'validated'})
    record = publish(pdb, meta, tmp_path / 'published')
    assert record['validation_status'] == 'unvalidated'
    assert 'plddt' not in record
    assert record['source_meta']['plddt'] == 99
    assert (tmp_path / 'published/descendant.pdb').read_bytes() == pdb.read_bytes()


def test_frozen_selected_model_contracts_and_off_defaults():
    for model, mode, workflow in [('binder_refinement', 'refine', 'binder_refinement.nf'), ('caliby_binder', 'design', 'caliby_binder.nf')]:
        data = yaml.safe_load((ROOT / f'platform/api/config/models/{model}.yaml').read_text())
        assert data['modes'][0]['id'] == mode
        keys = {p['name'] for p in data['params']}
        assert {'pdb_paths', 'source_identity_json', 'binder_chains', 'target_chains'} <= keys
        assert (ROOT / 'workflows' / workflow).is_file()
    refinement = yaml.safe_load((ROOT / 'platform/api/config/models/binder_refinement.yaml').read_text())
    assert all(p['default'] is False for p in refinement['params'] if p['name'] in {
        'maturation_repack_enabled', 'maturation_anchors_enabled', 'maturation_flow_enabled', 'maturation_redesign_enabled'})
    workflow = (ROOT / 'workflows/binder_refinement.nf').read_text()
    assert 'FilterByMaturation' not in workflow
    assert 'ScorePartialFlowImprovement' not in workflow
    assert 'if (repack || anchors)' in workflow
    assert 'if (flow)' in workflow and 'if (redesign)' in workflow


def test_iggm_final_producer_and_freshness_are_not_revalidation_gate():
    text = (ROOT / 'workflows/antibody_denovo.nf').read_text()
    block = text[text.index('final_designs = matured_designs.flatMap'):text.index('if (params.run_frustrampnn == true)', text.index('final_designs = matured_designs.flatMap'))]
    assert "validation_status: 'unvalidated'" in block
    assert "source_meta: new LinkedHashMap(meta)" in block
    assert "? 'iggm_affinity_maturation'" in block
    assert "? 'iggm'" in block
    assert 'error(' not in block


def test_regions_need_no_rosetta_or_anchor_analysis(tmp_path):
    pdb = tmp_path / 'selected.pdb'
    pdb.write_text(atom('A', 1) + atom('A', 2, 2) + atom('B', 1, 3))
    subprocess.run([sys.executable, str(SCRIPTS / 'prepare_maturation_regions.py'), '--pdb', str(pdb),
                    '--prefix', 'mask', '--chains', 'A', '--region', 'all_antibody'], cwd=tmp_path, check=True)
    assert (tmp_path / 'mask_ppiflow_positions.txt').read_text().strip() == 'A1-2'
    assert json.loads((tmp_path / 'mask_anchors.json').read_text())['analysis_status'] == 'not_run'


@pytest.fixture
def nextflow(tmp_path):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('set BMS_TEST_NEXTFLOW_JAR for pinned non-science Nextflow execution')
    config = tmp_path / 'clean.config'
    config.write_text('process.executor = "local"\napptainer.enabled = false\ndocker.enabled = false\n')
    env = {**os.environ, 'NXF_HOME': str(tmp_path / 'nxf'), 'NXF_OFFLINE': 'true'}
    def run(workflow, params, name='run'):
        params_file = tmp_path / (name + '.json')
        params_file.write_text(json.dumps(params))
        command = ['java', '-jar', jar, '-C', str(config), '-log', str(tmp_path / (name + '.log')),
                   'run', str(workflow), '-ansi-log', 'false', '-params-file', str(params_file),
                   '-work-dir', str(tmp_path / (name + '-work')), '-with-trace', str(tmp_path / (name + '.trace'))]
        completed = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return (tmp_path / (name + '.trace')).read_text()
    return run


def test_nextflow_all_off_only_publishes_selected_bytes(nextflow, tmp_path):
    pdb = tmp_path / 'selected.pdb'
    pdb.write_text(atom('A', 1) + atom('B', 1, 2))
    out = tmp_path / 'out'
    trace = nextflow(ROOT / 'workflows/binder_refinement.nf', dict(pdb_paths=str(pdb), code_root=str(ROOT),
                      out_dir=str(out), binder_chains='A', target_chains='B'))
    assert 'PublishBinderRefinement' in trace
    assert 'IdentifyAnchorResidues' not in trace and 'RunPartialFlow' not in trace and 'RunMaturationFAMPNN' not in trace
    copies = list(out.rglob('selected.pdb'))
    assert len(copies) == 1
    assert copies[0].read_bytes() == pdb.read_bytes()


# Non-science process fixtures: only copy input bytes and emit transport records.
# The real selected workflow is compiled/executed unchanged except its include
# path; native process compilation is qualified separately with pinned inspect.
FIXTURE_PRODUCERS = r'''
process IdentifyAnchorResidues {
 input:
 tuple val(meta), path(pdb)
 output:
 tuple val(meta), path(pdb), path("${meta.id}_enriched_complex.pdb"), path('anchors.json'), path('positions.txt'), path('cdr.txt'), path('loops.json'), emit: anchor_inputs
 script:
 """
 cp '${pdb}' '${meta.id}_enriched_complex.pdb'
 printf '{"anchors":[]}' > anchors.json
 printf A1-2 > positions.txt
 printf A1-2 > cdr.txt
 printf '{}' > loops.json
 """
}
process RunPartialFlow {
 input:
 tuple val(meta), path(original), path(pdb), path(a), path(p), path(c), path(l)
 output:
 tuple val(meta), path('backbones'), path('manifest.json'), emit: backbones
 script:
 """
 mkdir backbones
 cp '${pdb}' backbones/fixture_flow.pdb
 python3 -c 'import json;from pathlib import Path;Path("manifest.json").write_text(json.dumps([dict(path=str(Path("backbones/fixture_flow.pdb").resolve()),sample_index=23)]))'
 """
}
process PrepareBinderRedesign {
 input:
 tuple val(meta), path(pdb), path(a)
 output:
 tuple val(meta), path(pdb), path('fampnn.csv'), path('transport'), emit: prep
 script:
 """
 touch fampnn.csv
 mkdir transport
 """
}
process RunMaturationFAMPNN {
 input:
 tuple val(meta), path(pdb), path(csv), path(transport)
 output:
 tuple val(meta), path('fixture_redesign.pdb'), path('fixture_redesign.json'), emit: redesigned
 script:
 """
 cp '${pdb}' fixture_redesign.pdb
 printf '{}' > fixture_redesign.json
 """
}
'''


@pytest.mark.parametrize('enabled', [('repack',), ('anchors',), ('flow',), ('redesign',), ('repack', 'anchors', 'flow', 'redesign')])
def test_nextflow_independent_composition_transport(nextflow, tmp_path, enabled):
    fixture = tmp_path / 'fixture_producers.nf'
    fixture.write_text(FIXTURE_PRODUCERS)
    workflow = tmp_path / 'composition.nf'
    source_workflow = (ROOT / 'workflows/binder_refinement.nf').read_text().replace('../modules/ppiflow.nf', str(fixture))
    first = source_workflow.index('process PrepareBinderRedesign {')
    last = source_workflow.index('process PublishBinderRefinement {')
    source_workflow = source_workflow[:first] + source_workflow[last:]
    source_workflow = source_workflow.replace('RunPartialFlow; RunMaturationFAMPNN', 'RunPartialFlow; RunMaturationFAMPNN; PrepareBinderRedesign')
    workflow.write_text(source_workflow)
    pdb = tmp_path / 'selected.pdb'
    pdb.write_text(atom('A', 1) + atom('A', 2, 2) + atom('B', 1, 3))
    source = tmp_path / 'source.json'
    source.write_text(json.dumps([dict(staged_name=pdb.name, source_meta=dict(id='parent-design', validation_status='validated', plddt=98))]))
    out = tmp_path / 'out'
    params = dict(pdb_paths=str(pdb), source_identity_json=str(source), code_root=str(ROOT), out_dir=str(out),
                  binder_chains='A', target_chains='B', ppiflow_region_mode='all_antibody')
    params.update({f'maturation_{name}_enabled': name in enabled for name in ['repack', 'anchors', 'flow', 'redesign']})
    trace = nextflow(workflow, params)
    assert ('IdentifyAnchorResidues' in trace) == bool(set(enabled) & {'repack', 'anchors'})
    assert ('RunPartialFlow' in trace) == ('flow' in enabled)
    assert ('RunMaturationFAMPNN' in trace) == ('redesign' in enabled)
    records = list(out.rglob('generator_*.json'))
    assert len(records) == 1
    data = json.loads(records[0].read_text())
    assert data['source_document_id'] == 'parent-design'
    assert data['source_meta']['plddt'] == 98 and 'plddt' not in data
    if set(enabled) & {'repack', 'flow', 'redesign'}:
        assert data['validation_status'] == 'unvalidated'
    if 'flow' in enabled:
        assert data['sample_index'] == 23


def test_nextflow_caliby_invokes_real_module_and_transports_typed_settings(nextflow, tmp_path):
    code = tmp_path / 'fixture_code/scripts'
    code.mkdir(parents=True)
    # Only native science is replaced by an argument-recording fixture script.
    # Real RunCalibyBinder performs staging, constraint preparation and publication.
    for name in ['prep_caliby_binder_constraints.py', 'prep_antibody_constraints.py']:
        (code / name).write_text((SCRIPTS / name).read_text())
    (code / 'run_caliby_sequence_design.py').write_text('''import json, sys
from pathlib import Path
Path('results').mkdir(exist_ok=True)
Path('results/fixture.pdb').write_bytes(next(Path('.').glob('*.pdb')).read_bytes())
Path('results/generator_fixture.json').write_text(json.dumps({'fixture_argv':sys.argv[1:]}))
Path('results/caliby_metadata.jsonl').write_text('{}\\n')
''')
    pdb = tmp_path / 'selected.pdb'
    pdb.write_text(atom('A', 1) + atom('B', 1, 2))
    out = tmp_path / 'out'
    trace = nextflow(ROOT / 'workflows/caliby_binder.nf', dict(pdb_paths=str(pdb), code_root=str(code.parent),
        out_dir=str(out), binder_chains='A', target_chains='B', caliby_gaussian_n_conformers=0,
        caliby_gaussian_noise_std=0, caliby_potts_rejection_step=False, caliby_scn_num_steps=17,
        caliby_temperature=0.2, caliby_omit_aas='', caliby_fixed_pos_override_seq='A1:G'))
    assert 'RunCalibyBinder' in trace
    record = json.loads(next(out.rglob('generator_fixture.json')).read_text())
    argv = record['fixture_argv']
    options = dict(zip(argv[::2], argv[1::2]))
    assert options['--temperature'] == '0.2' and options['--omit-aas'] == ''
    native = json.loads(options['--sampling-overrides-json'])
    assert native['gaussian_conformers_cfg'] == {'n_conformers': 0, 'noise_std': 0}
    assert native['potts_sampling_cfg']['rejection_step'] is False
    assert native['scn_packing_cfg']['num_steps'] == 17
    assert 'A1:G' in next(out.rglob('caliby_constraints.csv')).read_text()
