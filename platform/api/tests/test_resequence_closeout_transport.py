"""CPU fixtures at real Groovy/prep/runner/publication boundaries; no inference."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / 'workflows/protein_local_redesign.nf'


def pdb(domain):
    return ''.join(f'ATOM  {i:5d}  CA  ALA {c}{n:4d}{ins:1s}   {float(i):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C\n'
                   for i, (c, n, ins) in enumerate(domain, 1)) + 'END\n'


def groovy(tmp_path, body):
    jar = os.environ['BMS_TEST_NEXTFLOW_JAR']
    script = tmp_path / 'receive.groovy'
    script.write_text(body)
    result = subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def process_script(name):
    return WORKFLOW.read_text().split('process ' + name + ' {', 1)[1].split('\nprocess ', 1)[0].split('    script:', 1)[1].split('"""', 2)[1]


@pytest.mark.parametrize('selection', ['keep', 'change', 'neither', 'insertions'])
def test_mpnn_real_arguments_and_preparation(tmp_path, selection):
    domains = [[('A', 10, ''), ('A', 10, 'A'), ('A', 11, ''), ('A', 12, 'B'), ('B', 5, '')],
               [('A', 110, ''), ('A', 112, ''), ('B', 21, '')]]
    for i, domain in enumerate(domains):
        (tmp_path / f'{i}_source.pdb').write_text(pdb(domain))
    params = dict(plr_design_chains='A', plr_fixed_positions='A10,A110' if selection == 'keep' else '',
                  plr_designable_positions=('A10A-12B' if selection == 'insertions' else 'A11,A112') if selection in {'change', 'insertions'} else '',
                  plr_region_manifest='', code_root=str(ROOT))
    source = WORKFLOW.read_text()
    construction = 'def manifestArg =' + source.split('    def manifestArg =', 1)[1].split('\n\n    if (resumeFromBackbones)', 1)[0]
    args = groovy(tmp_path, '''class Channel { static def value(v) { v } }
def shellQuote(v) { "'" + v.toString().replace("'", "'\\\"'\\\"'") + "'" }
''' + 'def params = new groovy.json.JsonSlurper().parseText(' + json.dumps(json.dumps(params)) + ')\n' + construction + '\nprintln mpnnDesignableArgs\n')
    # Evaluate actual process interpolation, then run both real prep scripts.
    shell = groovy(tmp_path, 'def params = [code_root:' + json.dumps(str(ROOT)) + ']\ndef designable_args = ' + json.dumps(args) + '\nprint """' + process_script('PrepProteinLocalMPNN') + '"""')
    binary = tmp_path / 'bin'; binary.mkdir()
    stub = binary / 'micromamba'; stub.write_text('#!/bin/sh\nexit 0\n'); stub.chmod(0o755)
    result = subprocess.run(['bash', '-eu', '-c', shell], cwd=tmp_path, env={**os.environ, 'PATH': str(binary) + ':' + str(Path(sys.executable).parent) + ':' + os.environ['PATH']}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    for i, domain in enumerate(domains):
        text = (tmp_path / 'mpnn_fixed' / f'{i}_source.pdb').read_text()
        role = json.loads(text.splitlines()[0].removeprefix('REMARK BMS_MPNN_ROLES '))
        free = ([('A', 10, 'A'), ('A', 11, ''), ('A', 12, 'B')] if i == 0 else []) if selection == 'insertions' else ([('A', 11, '')] if i == 0 else [('A', 112, '')]) if selection == 'change' else [r for r in domain if r[0] == 'A' and (selection != 'keep' or r[1] not in {10, 110} or r[2])]
        assert role['designed_chains'] == ['A']
        assert role['fixed_chains'] == ['B']
        assert role['source_residues'] == [list(r) for r in domain]
        assert role['fixed_positions'] == [list(r) for r in domain if r not in free]
        assert ''.join(line + '\n' for line in text.splitlines() if line.startswith('ATOM')) == pdb(domain).removesuffix('END\n')


@pytest.mark.parametrize('engine', ['ligandmpnn', 'caliby'])
@pytest.mark.parametrize('selection', ['keep', 'change', 'neither', 'insertions'])
def test_template_to_native_request_per_input(tmp_path, monkeypatch, engine, selection):
    import run_resequence_designer as runner
    from services.resequence_designers import build_template
    template = build_template(engine, mode=None, design_chains='A', settings={})
    monkeypatch.setattr(runner, 'run', lambda command: None)  # Native inference only.
    for index, domain in enumerate([[('A', 10, ''), ('A', 10, 'A'), ('A', 11, ''), ('A', 12, 'B'), ('B', 5, '')], [('A', 110, ''), ('B', 21, '')]]):
        source = tmp_path / f'{index}.pdb'; source.write_text(pdb(domain))
        work = tmp_path / f'w{index}'; work.mkdir()
        keep = 'A10,A110' if selection == 'keep' else ''
        change = 'A10A-12B,B5' if selection == 'insertions' else 'A11,A110,B21' if selection == 'change' else ''
        runner.RUNNERS[engine](template, source, work, keep, change, SimpleNamespace())
        request = json.loads((work / ('request.json' if engine == 'ligandmpnn' else 'request/request.json')).read_text())
        if engine == 'ligandmpnn':
            actual = request['options']['fixed_residues'] or []
            assert not request['options'].get('designed_residues')
        else:
            actual = request['effective']['ensembles'][0]['states'][0]['fixed_pos_seq'].split(',')
        free = ({'A10A', 'A11', 'A12B'} if index == 0 else set()) if selection == 'insertions' else {'A11', 'A110'} if selection == 'change' else {'A10A', 'A11', 'A12B'} if selection == 'keep' else {'A10', 'A10A', 'A11', 'A12B', 'A110'}
        assert actual == [f'{c}{n}{ins}' for c, n, ins in domain if f'{c}{n}{ins}' not in free]
        assert 'loop_design_chains' not in request


@pytest.mark.parametrize('engine', ['ligandmpnn', 'caliby', 'protonpottsmpnn'])
def test_runner_retains_native_tree_and_carrier_identity(tmp_path, monkeypatch, engine):
    import run_resequence_designer as runner
    import designer_outputs_to_pdb as converter
    from services.resequence_designers import build_template
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'inputs'; source.mkdir()
    for name in ['001_same.sample', '002_same.sample']:
        (source / (name + '.pdb')).write_text(pdb([('A', 10, ''), ('B', 5, '')]))
    template = build_template(engine, mode=None, design_chains='A', settings={})
    (tmp_path / 'template.json').write_text(json.dumps(template))
    # Explicit fixture native subprocess outputs; real outer runner copies exact bytes.
    def native(command):
        flag = '--output' if engine == 'ligandmpnn' else '--output-dir' if engine == 'caliby' else '--out'
        directory = Path(command[command.index(flag) + 1])
        if engine == 'protonpottsmpnn':
            directory /= 'protonpottsmpnn_design'
        directory.mkdir(parents=True)
        (directory / 'manifest.json').write_text('{"fixture_native_observations": [0, null, false]}\n')
        (directory / 'trajectory.bin').write_bytes(b'fixture-native-trajectory\x00\xff')
    monkeypatch.setattr(runner, 'run', native)
    def convert(directory, *args):
        dest = args[-1] / 'native_sample.pdb'; dest.write_text('fixture carrier\n'); return [dest]
    monkeypatch.setattr(converter, engine if engine != 'protonpottsmpnn' else 'protonpotts', convert)
    monkeypatch.setattr(sys, 'argv', ['runner', engine, '--template', 'template.json', '--input-dir', str(source), '--out-dir', 'designs', '--fixed-positions', 'A10'])
    runner.main()
    for name in ['001_same.sample', '002_same.sample']:
        retained = tmp_path / 'native_results' / name
        assert (retained / 'native/trajectory.bin').read_bytes() == b'fixture-native-trajectory\x00\xff'
        assert json.loads((retained / 'native/manifest.json').read_text()) == {'fixture_native_observations': [0, None, False]}
        association = json.loads((retained / 'loop_conversion.json').read_text())
        assert association['source']['name'] == name + '.pdb'
        assert association['carriers'] == [{'native_converted_name': 'native_sample.pdb', 'candidate': f'{name}__{engine}__native_sample.pdb'}]
        assert (tmp_path / 'designs' / association['carriers'][0]['candidate']).read_text() == 'fixture carrier\n'
        if engine == 'protonpottsmpnn':
            request = json.loads((retained / 'request.json').read_text())
            assert request['options'] == template['options']  # Own-site behavior unchanged.


@pytest.mark.parametrize('pae', [True, False])
def test_boltz_native_publisher_to_actual_export(tmp_path, pae):
    from publish_boltz_samples import publish_samples
    for batch, name in enumerate(['one', 'one_extra']):
        work = tmp_path / str(batch); work.mkdir()
        inputs = work / 'inputs'; inputs.mkdir(); (inputs / (name + '.yaml')).write_text('fixture')
        native = work / 'native' / name; native.mkdir(parents=True)
        (native / f'{name}_model_0.pdb').write_text('fixture coordinates')
        (native / f'confidence_{name}_model_0.json').write_text('{"ptm": 0, "complex_plddt": 0.5}')
        if pae:
            (native / f'pae_{name}_model_0.npz').write_bytes(b'fixture-exact-npz-bytes')
        predictions = work / 'predictions'
        publish_samples(native.parent, predictions, inputs)
        body = 'def sample_dir = ' + json.dumps(str(predictions)) + '\ndef batch_id = ' + str(batch) + '\ndef pdb_files = ' + json.dumps(str(next(predictions.glob('*.pdb')))) + '\ndef json_files = ' + json.dumps(str(next(predictions.glob('*.json')))) + '\nprint """' + process_script('ExportProteinLocalBoltzPredictions') + '"""'
        shell = groovy(work, body)
        result = subprocess.run(['bash', '-eu', '-c', shell], cwd=work, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        published = work / 'published'
        assert (published / f'boltz_samples_{batch}.manifest').read_bytes() == (predictions / 'boltz_samples.manifest').read_bytes()
        for leaf in predictions.glob('*'):
            if leaf.suffix != '.manifest':
                assert (published / leaf.name).read_bytes() == leaf.read_bytes()
        assert len(list(published.glob('*.npz'))) == int(pae)


@pytest.mark.parametrize('scores', [True, False])
def test_checkpoint_retains_optional_validator_files(tmp_path, monkeypatch, scores):
    from open_stage_gate import open_component_gate
    monkeypatch.chdir(tmp_path)
    candidate = tmp_path / 'validation/review_candidates'; candidate.mkdir(parents=True)
    (candidate / 'candidate.pdb').write_text('fixture coordinates')
    expected = ['boltz2/candidate_model_0_boltzpred.json', 'protenix_v2/candidate/seed_42/candidate_summary_confidence_sample_0.json', 'esmfold2/candidate/sample.metrics.json']
    if scores:
        for path in expected:
            target = candidate.parent / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text('{"ptm": 0}')
    runtime = SimpleNamespace(root_job_id='fixture', artifact_root=tmp_path, root_state=lambda: {}, checkpoint=lambda checkpoint_id, **kwargs: kwargs)
    result = open_component_gate(runtime, job_id='fixture', stage='post_structure_validation', payload={'framework_type': 'protein_local_redesign'}, directories={'candidate': candidate, 'raw': candidate.parent})
    refs = [r for r in result['artifacts'] if r.schema == 'bms.stage-review.validator-scores.v1']
    assert sorted(r.relative_path.split('/validator-scores/')[1] for r in refs) == (sorted(expected) if scores else [])
    for ref in refs:
        assert ref.resolve(tmp_path).read_text() == '{"ptm": 0}'


def nextflow(tmp_path, source):
    script = tmp_path / 'fixture.nf'; script.write_text(source)
    config = tmp_path / 'fixture.config'; config.write_text("process.executor = 'local'\n")
    env = {**os.environ, 'NXF_HOME': str(tmp_path / 'nxf-home'), 'NXF_OFFLINE': 'true', 'NXF_DISABLE_CHECK_LATEST': 'true'}
    result = subprocess.run(['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], '-C', str(config), 'run', str(script),
                             '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false', '-offline'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('process,engine', [('RunLigandMPNNResequence', 'ligandmpnn'), ('RunCalibyResequence', 'caliby'), ('RunProtonPottsResequence', 'protonpottsmpnn')])
def test_actual_nextflow_designer_output_publication(tmp_path, process, engine):
    # Native engine is a fixture shell; actual process inputs, outputs, publication
    # and receiving channels are unmodified. Outer runner fidelity is tested above.
    source = WORKFLOW.read_text()
    block = 'process ' + process + ' {' + source.split('process ' + process + ' {', 1)[1].split('    script:', 1)[0]
    block += '    script:\n    """\n    mkdir -p designs native_results/source/native\n    printf carrier > designs/candidate.pdb\n    printf native-observation > native_results/source/native/manifest.json\n    """\n}\n'
    (tmp_path / 'input.pdb').write_text('fixture input')
    (tmp_path / 'template.json').write_text('{}')
    nf = "params.out_dir = 'retained'\n" + block + f"\nworkflow {{ {process}(Channel.value(tuple(file('input.pdb'), [])), Channel.value(file('template.json')), Channel.value('')) }}\n"
    nextflow(tmp_path, nf)
    assert (tmp_path / 'retained' / 'run' / engine / 'candidate.pdb').read_text() == 'carrier'
    assert (tmp_path / 'retained' / 'run' / engine / 'native_results/source/native/manifest.json').read_text() == 'native-observation'


def test_actual_nextflow_boltz_export_receiving(tmp_path):
    source = WORKFLOW.read_text()
    block = 'process ExportProteinLocalBoltzPredictions {' + source.split('process ExportProteinLocalBoltzPredictions {', 1)[1].split('// Sequence designers beyond', 1)[0]
    predictions = tmp_path / 'predictions'; predictions.mkdir()
    for name, data in {'source.pdb': b'fixture-pdb', 'source.json': b'{"ptm":0}', 'source.pae.npz': b'fixture-pae', 'boltz_samples.manifest': b'{"samples":[]}'}.items():
        (predictions / name).write_bytes(data)
    # Choose the old input dialect on baseline, rather than failing on arity.
    value = "tuple(3, file('predictions'))" if 'path(sample_dir)' in block else "tuple(file('predictions/source.pdb'), file('predictions/source.json'))"
    helper = "def proteinlocalredesignPublishMode(a,b) { 'copy' }\n"
    nextflow(tmp_path, "params.out_dir = 'retained'\n" + helper + block + f"\nworkflow {{ ExportProteinLocalBoltzPredictions(Channel.value({value})) }}\n")
    published = tmp_path / 'retained/validation/boltz2'
    assert (published / 'source.pae.npz').read_bytes() == b'fixture-pae'
    assert (published / 'boltz_samples_3.manifest').read_bytes() == b'{"samples":[]}'
