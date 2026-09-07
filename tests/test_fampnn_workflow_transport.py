"""CPU-only real Nextflow module transport: producer and pSCE are test doubles.
The seq-prob analyzer itself executes unchanged against producer-shaped PKLs.
"""
import json
import hashlib
import os
import pickle
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
DIALECT = 'fampnn-18363df253dbeb7b2cb963daf7a732fbaa25157d'


@pytest.mark.parametrize('deferred', [False, True])
def test_real_module_transports_marked_policy_and_runs_analyzer(tmp_path, deferred):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file(), 'Set BMS_TEST_NEXTFLOW_JAR to a native Nextflow 25.10.1 jar (no Docker wrapper)'
    launcher = ['java', '-jar', jar]
    source = tmp_path / 'input.pdb'
    source.write_text('ATOM      1  CA  ALA Z  10       0.000   0.000   0.000  1.00 20.00           C\n')
    csv = tmp_path / 'fixed.csv'
    csv.write_text('pdb,fixed_pos\ninput,\n')
    sample = tmp_path / 'sample.pkl'
    sample.write_bytes(pickle.dumps(dict(seq_probs=np.eye(21)[[1]]*0.2 + np.eye(21)[[2]]*0.8, pred_aatype=np.array([1]),
        seq_mask=np.ones(1), aatype_override_mask=np.zeros(1), chain_index=np.zeros(1), residue_index=np.array([10]))))
    policy = dict(schema_version=1, owner='protein_design', version=1,
        declaration='declared_protein_inputs', dialect=DIALECT,
        require_full_coverage=False, allow_summary_override=True,
        inputs={'input': dict(input_domain=['Z:10:'], sequence_design=['Z:10:'],
            summary=['Z:10:'], summary_override=None, mutation_override=None,
            artifact_binding=dict(producer_input_id='input',
                source_pdb_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                producer_candidate_ids=['input_sample0']))})
    params = tmp_path / 'params.json'
    params.write_text(json.dumps(dict(core_protein_scientific_contract=1, fampnn_analysis_policy=policy,
        out_dir=str(tmp_path / 'out'), fampnn_mutation_top_n=0)))
    staged = f"file('{source}')"
    extra_args = []
    if deferred:
        sys.path.insert(0, str(ROOT / 'scripts'))
        from fampnn_policy_resolution import prep_receipt
        receipt = tmp_path / 'input.fampnn_prep.json'
        receipt.write_text(json.dumps(prep_receipt(source, source, [('Z:10:', 'Z:10:')])))
        declaration = {key: value for key, value in policy.items() if key not in {'inputs', 'dialect'}}
        declaration.update({key: value for key, value in policy['inputs']['input'].items() if key != 'artifact_binding'})
        declaration['fixed'] = []
        params.write_text(json.dumps(dict(core_protein_scientific_contract=1,
            out_dir=str(tmp_path / 'out'), fampnn_mutation_top_n=0)))
        declaration_path = tmp_path / 'declaration.json'
        declaration_path.write_text(json.dumps(declaration))
        extra_args = ['--fampnn_analysis_declaration_path', str(declaration_path),
                      '--fampnn_analysis_declaration_sha256', hashlib.sha256(declaration_path.read_bytes()).hexdigest()]
        staged = f"[file('{source}'), file('{receipt}')]"
    harness = tmp_path / 'main.nf'
    harness.write_text(f"""
nextflow.enable.dsl=2
include {{ RunFAMPNN }} from '{ROOT}/modules/fampnn.nf'
workflow {{
    def analysis = FampnnAnalysisPolicy.forWorkflow(params, 'protein_design', 'declared_protein_inputs')
    RunFAMPNN(Channel.of(tuple(0, {staged}, file('{csv}'), 0)), 'all_chains', analysis)
}}
""")
    config = tmp_path / 'minimal.config'
    config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="128 MB"\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    python = bin_dir / 'python'
    python.write_text(f'''#!{sys.executable}
import os, sys, shutil
from pathlib import Path
args = sys.argv[1:]
if args[0] == '/app/fampnn/fampnn/inference/seq_design.py':
    Path('fampnn_output/sample_pkls').mkdir(parents=True)
    Path('fampnn_output/samples').mkdir()
    shutil.copy({str(sample)!r}, 'fampnn_output/sample_pkls/input_sample0.pkl')
    shutil.copy('input.pdb', 'fampnn_output/samples/input_sample0.pdb')
elif args[0] == '/scripts/analyse_fampnn.py':
    Path('results/input_seq_0.json').write_text('{{}}')
elif args[0] == '/scripts/metadata_converter.py':
    Path(args[args.index('--output_file')+1]).write_text('{{}}\\n')
else:
    if args[0] == '/scripts/analyse_fampnn_seq_probs.py':
        args[0] = {str(ROOT / 'scripts/analyse_fampnn_seq_probs.py')!r}
    if args[0] == '/scripts/fampnn_policy_resolution.py':
        args[0] = {str(ROOT / 'scripts/fampnn_policy_resolution.py')!r}
    os.execv({sys.executable!r}, [{sys.executable!r}] + args)
''')
    python.chmod(0o755)
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'], NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', BMS_SCIENTIFIC_ARTIFACT_ROOT=str(tmp_path / 'artifacts'))
    result = subprocess.run(launcher + ['-C', str(config), 'run', str(harness), '-lib', str(ROOT/'lib'),
        '-params-file', str(params), '-work-dir', str(tmp_path/'work')] + extra_args, cwd=tmp_path,
        env=env, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    metrics = list((tmp_path/'out/run/fampnn/seq_prob_metrics').glob('*.jsonl'))
    assert len(metrics) == 1
    row = json.loads(metrics[0].read_text())
    assert row['core_protein_scientific_contract'] == 1
    assert row['analysis_policy'] == policy
    assert row['artifact_binding']['source_pdb']['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row['artifact_binding']['sample_pkl']['sha256'] == hashlib.sha256(sample.read_bytes()).hexdigest()
    assert row['artifact_binding']['candidate_pdb']['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row['residue_evidence'][0]['aa'] == 'R'
    assert row['residue_evidence'][0]['identity'] == 'Z:10:'
    assert row['fampnn_mutation_opportunity_count'] == 1
    assert row['fampnn_top_model_favored_mutations'] == []


def test_workflow_and_child_authority_gates(tmp_path):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file(), 'Set BMS_TEST_NEXTFLOW_JAR for Groovy authority-gate acceptance'
    script = tmp_path / 'authority.groovy'
    script.write_text('''
assert FampnnAnalysisPolicy.forWorkflow([:], 'protein_design', 'declared_protein_inputs') == [:]
assert FampnnAnalysisPolicy.forChild([:]) == [:]
assert FampnnAnalysisPolicy.stagePrepared([:], ['legacy.pdb']) == ['legacy.pdb']
boolean missingPrepRejected = false
try { FampnnAnalysisPolicy.stagePrepared([fampnn_analysis_declaration: [:]], [java.nio.file.Path.of('missing.pdb')]) }
catch (IllegalArgumentException expected) { missingPrepRejected = true }
assert missingPrepRejected
def policy = [owner: 'antibody_denovo', declaration: 'authorized_sequence_design_region',
              schema_version: 1, version: 1, inputs: [input: [:]]]
def params = [core_protein_scientific_contract: 1, fampnn_analysis_policy: policy]
assert FampnnAnalysisPolicy.forChild(params).policy == policy
for (bad in [[core_protein_scientific_contract: 1],
             [core_protein_scientific_contract: 1, fampnn_analysis_policy: policy + [owner: 'fampnn_child']],
             [core_protein_scientific_contract: 2, fampnn_analysis_policy: policy]]) {
    boolean rejected = false
    try { FampnnAnalysisPolicy.forChild(bad) }
    catch (IllegalArgumentException expected) { rejected = true }
    assert rejected
}
boolean rejected = false
try { FampnnAnalysisPolicy.forWorkflow(params, 'protein_design', 'binder_role_residues') }
catch (IllegalArgumentException expected) { rejected = true }
assert rejected
println 'authority gates passed'
''')
    result = subprocess.run(['java', '-cp', jar + os.pathsep + str(ROOT/'lib'), 'groovy.ui.GroovyMain', str(script)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'authority gates passed' in result.stdout


def test_antibody_policy_transport_is_shell_safe():
    import base64
    text = (ROOT/'workflows/antibody_denovo.nf').read_text()
    block = text.split('process SpawnFAMPNNJobs {', 1)[1].split('process ', 1)[0]
    assert 'params_json_base64' in block
    line = next(line for line in block.splitlines() if '--params_json ' in line)
    argument = line.split('--params_json ', 1)[1].strip().rstrip('\\').rstrip()
    value = json.dumps({'identity': "':10:", 'literal': '$(exit 99)'})
    argument = argument.replace('${params_json_base64}', base64.b64encode(value.encode()).decode()).replace('\\$', '$')
    result = subprocess.run(['bash', '-c', 'take() { printf "%s" "$2"; }; take --params_json ' + argument], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == value


def test_real_antibody_prep_publishes_parent_owned_chain(tmp_path):
    """Real Nextflow prep/publication; only the geometry engine is doubled."""
    sys.path.insert(0, str(ROOT/'scripts'))
    from antibody_fampnn_provenance import native_export, NATIVE_SOURCES, input_sources
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file()
    source = tmp_path/'native.pdb'
    atom = 'ATOM      1  CA  ALA H   1       0.000   0.000   0.000  1.00 20.00           C'
    framework = tmp_path/'framework.pdb'; framework.write_text(atom)
    target = tmp_path/'target.pdb'; target.write_text(atom.replace(' H ', ' T '))
    source.write_text('\n'.join(native_export([atom], ['H'], {'H1':[1]}, NATIVE_SOURCES, input_sources(framework, target))))
    metadata = tmp_path/'metadata.json'; metadata.write_text('{}')
    harness = tmp_path/'main.nf'
    harness.write_text(f"""
nextflow.enable.dsl=2
include {{ PrepFAMPNN }} from '{ROOT}/modules/fampnn.nf'
workflow {{ PrepFAMPNN(Channel.of(tuple(file('{source}'), file('{metadata}')))) }}
""")
    config = tmp_path/'minimal.config'
    config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="128 MB"\n')
    params = tmp_path/'params.json'
    params.write_text(json.dumps(dict(out_dir=str(tmp_path/'parent'), core_protein_scientific_contract=1,
        fampnn_constraint_mode='antibody', antibody_design_mode='framework_allowed', protect_vhh_tetrad=False)))
    bin_dir = tmp_path/'bin'; bin_dir.mkdir()
    micromamba = bin_dir/'micromamba'
    micromamba.write_text('#!/bin/sh\nexit 0\n'); micromamba.chmod(0o755)
    python = bin_dir/'python'
    python.write_text(f'''#!{sys.executable}
import os, sys, json
from pathlib import Path
sys.path.insert(0, {str(ROOT/'scripts')!r})
args = sys.argv[1:]
if args[0] == '/scripts/prep_fampnn_designs.py':
    from antibody_fampnn_provenance import carry_export, domain
    from fampnn_policy_resolution import prep_receipt
    out = Path('fampnn_input'); out.mkdir()
    for src in Path('.').glob('*.pdb'):
        dst = out/src.name
        dst.write_bytes(src.read_bytes())
        pairs = [(v,v) for v in domain(src.read_text().splitlines())]
        carry_export(src, dst, pairs)
        dst.with_suffix('.fampnn_prep.json').write_text(json.dumps(prep_receipt(src,dst,pairs)))
else:
    args[0] = str(Path({str(ROOT/'scripts')!r})/Path(args[0]).name)
    os.execv({sys.executable!r}, [{sys.executable!r}]+args)
''')
    python.chmod(0o755)
    env = dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
        NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', BMS_SCIENTIFIC_ARTIFACT_ROOT=str(tmp_path/'artifacts'))
    result = subprocess.run(['java','-jar',jar,'-C',str(config),'run',str(harness),
        '-params-file',str(params),'-work-dir',str(tmp_path/'work')], cwd=tmp_path,
        env=env,text=True,capture_output=True,timeout=120)
    assert result.returncode == 0, result.stdout+result.stderr
    published = tmp_path/'parent'/'prep'/'fampnn'
    assert (published/'native'/'native.pdb').read_bytes() == source.read_bytes()
    receipt = json.loads((published/'native.fampnn_prep.json').read_text())
    assert receipt['source_pdb_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert receipt['prepared_pdb_sha256'] == hashlib.sha256((published/'native.pdb').read_bytes()).hexdigest()
    assert receipt['antibody_constraints']['mutation'] == ['H:1:']


def test_each_real_owner_passes_policy_without_psce_scope_reuse():
    cases = {
        'workflows/protein_design.nf': "'protein_design'",
        'workflows/protein_local_redesign.nf': "'protein_local_redesign'",
        'workflows/fampnn_child.nf': 'FampnnAnalysisPolicy.forChild(params)',
        'workflows/antibody_denovo.nf': "'antibody_denovo'",
    }
    for path, owner in cases.items():
        text = (ROOT/path).read_text()
        assert 'FampnnAnalysisPolicy.' in text, path
        assert owner in text, path
    antibody = (ROOT/'workflows/antibody_denovo.nf').read_text()
    assert 'core_protein_scientific_contract: analysisContract.core_protein_scientific_contract' in antibody
    assert 'fampnn_analysis_declaration: analysisContract.declaration' in antibody
