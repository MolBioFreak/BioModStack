"""Tiny CPU publication/custody fixtures, not native inference qualification."""
import csv
import errno
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


@pytest.mark.parametrize('model', ['caliby_native', 'ppiflow_generation', 'ligandmpnn_design', 'protonpottsmpnn_design'])
def test_prepared_custody_two_new_owners_survive_source_and_sibling_deletion(tmp_path, model):
    native = importlib.import_module('services.' + model)
    source = tmp_path / 'external.pdb'
    source.write_text('REMARK INERT SOURCE\nEND\n')
    if model == 'caliby_native':
        mode = 'sidechain_pack'
        science = {'structures': [{'state_id': 'same-id', 'path': str(source)}]}
        field, relative = 'caliby_request_dir', 'structures/state_000000.pdb'
        prepare = native.prepare_for_job
        read = lambda p: native.read_prepared_request(mode, science, p[field])
    elif model == 'ppiflow_generation':
        mode = 'protein_binder'
        science = {'target_pdb': str(source), 'binder_chain': 'B', 'samples_per_target': 2}
        field, relative = 'ppiflow_generation_request', 'inputs/target_pdb.pdb'
        prepare = native.prepare_ppiflow_generation_request
        read = lambda p: native.read_prepared_ppiflow_generation_request(mode, science, p[field])
    else:
        mode = 'ligand_aware' if model == 'ligandmpnn_design' else 'redesign'
        science = {'target_pdb': str(source)}
        if model == 'protonpottsmpnn_design':
            science['binder_chain'] = 'A'
        field = 'ligandmpnn_design_input' if model == 'ligandmpnn_design' else 'protonpottsmpnn_design_input'
        relative = 'source.pdb'
        prepare = native.prepare_for_job
        read = lambda p: native.read_prepared_request(mode, {**science, **p})
    first = prepare(mode, science, tmp_path / 'first', allowed_roots=[tmp_path])
    leaf = tmp_path / 'first' / relative
    assert not source.samefile(leaf), 'external mutable acquisition must stay private'
    expected = read(first)
    source.write_text('REPLACED EXTERNAL SOURCE\n')
    second = prepare(mode, {**science, **first}, tmp_path / 'second', allowed_roots=[tmp_path])
    third = prepare(mode, {**science, **first}, tmp_path / 'third', allowed_roots=[tmp_path])
    leaves = [tmp_path / name / relative for name in ['first', 'second', 'third']]
    assert len({(p.stat().st_dev, p.stat().st_ino) for p in leaves}) == 1
    assert leaves[0].stat().st_blocks == leaves[2].stat().st_blocks
    assert not (tmp_path / 'first/request.json').samefile(tmp_path / 'third/request.json')
    shutil.rmtree(tmp_path / 'first')
    shutil.rmtree(tmp_path / 'second')
    source.unlink()
    assert read(third) == expected
    assert leaves[2].read_text() == 'REMARK INERT SOURCE\nEND\n'
    assert third != second


@pytest.mark.parametrize('module', ['caliby_runtime', 'prep_mpnn_designs', 'filter_best_designs', 'filter_boltzgen', 'screen_rfantibody_backbones'])
def test_closed_publish_atomic_retry_failure_and_cross_device(tmp_path, monkeypatch, module):
    owner = importlib.import_module(module)
    source, sibling, destination = [tmp_path / n for n in ['source.pdb', 'sibling.pdb', 'result.pdb']]
    source.write_bytes(b'old closed payload')
    os.link(source, sibling)
    owner._retain_closed_result(source, destination)
    assert source.samefile(destination)
    incoming = tmp_path / 'incoming.pdb'
    incoming.write_bytes(b'new closed payload')
    original_replace = os.replace
    def fail_replace(*args):
        raise OSError(errno.EIO, 'injected before publication rename')
    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError):
        owner._retain_closed_result(incoming, destination)
    assert destination.read_bytes() == sibling.read_bytes() == b'old closed payload'
    assert not list(tmp_path.glob('.result-*'))
    monkeypatch.setattr(os, 'replace', original_replace)
    owner._retain_closed_result(incoming, destination)
    assert destination.samefile(incoming)
    assert sibling.read_bytes() == b'old closed payload'
    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, 'injected filesystem boundary')
    monkeypatch.setattr(os, 'link', cross_device)
    owner._retain_closed_result(source, destination)
    assert not source.samefile(destination)
    source.unlink()
    assert destination.read_bytes() == b'old closed payload'


def test_caliby_normalization_retains_native_and_display_with_private_metadata(tmp_path):
    from caliby_runtime import normalize_sampling_results
    source = tmp_path / 'native.pdb'
    source.write_text('REMARK INERT CALIBY\nEND\n')
    out = tmp_path / 'results'
    rows = normalize_sampling_results(results={'example_id': ['native-key'], 'out_pdb': [str(source)], 'seq': ['A']},
        output_pdb_dir=out, output_meta_dir=out, prefix='sample', source='caliby', stage_mode='design')
    assert len(rows) == 1
    display = out / 'sample_0001.pdb'
    retained = out / 'native_outputs/sample_0001/native.pdb'
    assert source.samefile(display) and source.samefile(retained)
    metadata = json.loads((out / 'generator_sample_0001.json').read_text())
    assert metadata['design_id'] == 'sample_0001'
    assert metadata['native_output']['example_id'] == 'native-key'
    source.unlink()
    assert retained.read_bytes() == display.read_bytes()


def test_caliby_new_transform_does_not_overwrite_old_link(tmp_path, monkeypatch):
    import caliby_runtime as owner
    old = tmp_path / 'native.pdb'; old.write_text('old PDB')
    owner.publish_structure(old, tmp_path / 'out', 'candidate')
    new = tmp_path / 'native.cif'; new.write_text('inert CIF transport')
    # Exercise the output syscall boundary without importing/running a numerical engine.
    monkeypatch.setattr(owner, '_convert_cif_to_pdb', lambda src, dst: dst.write_text('distinct conversion'))
    owner.publish_structure(new, tmp_path / 'out', 'candidate')
    assert old.read_text() == 'old PDB'
    assert (tmp_path / 'out/candidate.pdb').read_text() == 'distinct conversion'


def test_mpnn_canonical_native_identity_and_private_metadata(tmp_path):
    from prep_mpnn_designs import canonical_results
    source = tmp_path / 'native'; source.mkdir()
    pdb = source / 'native_id.pdb'; pdb.write_text('REMARK INERT MPNN\nEND\n')
    meta = source / 'unexpected_prefix.json'
    meta.write_text(json.dumps({'design': 'native_id', 'sequence': 'A', 'score': 0.25}))
    canonical_results(source, tmp_path / 'canonical')
    retained = tmp_path / 'canonical/native_id.pdb'
    assert retained.samefile(pdb)
    assert not (tmp_path / 'canonical/native_id.json').samefile(meta)
    shutil.rmtree(source)
    assert retained.read_text() == 'REMARK INERT MPNN\nEND\n'


def test_boltzgen_filter_payload_shared_metadata_transformed(tmp_path):
    from tests.test_boltzgen_accounting_publication import publish
    out = publish(tmp_path)
    assert (out / 'a.pdb').samefile(tmp_path / 'native/a.pdb')
    assert not (out / 'confidence_a.json').samefile(tmp_path / 'native/confidence_a.json')
    receipt = json.loads((out / 'filter_summary.json').read_text())
    assert list(receipt['publication']) == ['a']
    shutil.rmtree(tmp_path / 'native')
    assert (out / 'a.pdb').read_text().endswith('END\n')


@pytest.mark.parametrize('script', ['filter_best_designs', 'screen_rfantibody_backbones'])
def test_real_filter_cli_retains_accepted_payloads(tmp_path, script):
    source = tmp_path / 'native'; source.mkdir()
    pdb = source / 'fold_1_seq_1.pdb'
    pdb.write_text('ATOM      1  CA  ALA H   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
    trb = pdb.with_suffix('.trb'); trb.write_bytes(b'inert retained trb bytes')
    output = tmp_path / 'filtered'
    args = ['--pdb-dir', str(source), '--output-dir', str(output)]
    if script == 'filter_best_designs':
        table = tmp_path / 'input.csv'; table.write_text('fold_id,seq_id,score\n1,1,0.25\n2,1,0.5\n')
        args += ['--csv', str(table), '--output-csv', str(tmp_path / 'best.csv')]
    else:
        args += ['--summary-json', str(tmp_path / 'screen.json')]
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / (script + '.py')), *args],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    retained = output / pdb.name
    assert retained.samefile(pdb)
    if script == 'screen_rfantibody_backbones':
        assert (output / trb.name).samefile(trb)
        assert json.loads((tmp_path / 'screen.json').read_text())['screening_applied'] is False
    shutil.rmtree(source)
    assert retained.read_text().endswith('END\n')


def nextflow(tmp_path, workflow, params, resume=False):
    jar = Path(os.environ.get('BMS_TEST_NEXTFLOW_JAR') or os.environ.get('ONE_COPY_NEXTFLOW_JAR') or Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')
    assert jar.is_file()
    script = tmp_path / 'fixture.nf'
    config = tmp_path / 'fixture.config'
    settings = tmp_path / 'params.json'
    if not resume:
        script.write_text(workflow)
        config.write_text("process.executor='local'\nprocess.cpus=2\napptainer.enabled=false\ndocker.enabled=false\n")
        settings.write_text(json.dumps(params))
    trace = tmp_path / ('resume.tsv' if resume else 'trace.tsv')
    env = {**os.environ, 'NXF_OFFLINE': 'true', 'NXF_DISABLE_CHECK_LATEST': 'true',
           'JAVA_TOOL_OPTIONS': '-XX:ActiveProcessorCount=2 --add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED',
           'PATH': str(Path(sys.executable).parent) + ':' + os.environ['PATH']}
    command = ['java', '-jar', str(jar), '-C', str(config), 'run', str(script), '-params-file', str(settings),
               '-work-dir', str(tmp_path / 'work'), '-with-trace', str(trace)]
    if resume:
        command.append('-resume')
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    with trace.open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def test_all_owned_modules_parse_without_native_execution(tmp_path):
    owners = {
        'structure_prediction': 'BoltzFromSequenceTask', 'esmfold2_experimental': 'ESMFold2MSAPredict',
        'boltzgen': 'CollectBoltzGenOutputs', 'caliby': 'RunCaliby', 'proteinmpnn': 'RunMPNN',
        'fampnn': 'RunFAMPNN', 'ppiflow': 'RunMaturationFAMPNN', 'compress': 'Compress',
        'boltz_cp_experimental': 'RunBoltzCPExperimental', 'binder_blind_pose': 'BinderBlindPoseESMFold2',
    }
    includes = '\n'.join(f"include {{ {process} }} from '{ROOT}/modules/{module}.nf'" for module, process in owners.items())
    assert nextflow(tmp_path, includes + '\nworkflow {}\n', {'out_dir': str(tmp_path / 'output')}) == []


def test_real_export_task_cached_republish_and_work_deletion(tmp_path):
    source = tmp_path / 'candidate.pdb'; source.write_text('REMARK INERT EXPORT\nEND\n')
    meta = tmp_path / 'candidate.json'; meta.write_text('{"identity":"retained"}\n')
    workflow = f"""include {{ ExportProteinLocalMPNNResults }} from '{ROOT}/workflows/protein_local_redesign.nf'
workflow {{ ExportProteinLocalMPNNResults(Channel.of(tuple(file('{source}'), file('{meta}')))) }}
"""
    output = tmp_path / 'results'
    rows = nextflow(tmp_path, workflow, {'out_dir': str(output), 'code_root': str(ROOT)})
    display = output / 'pdb_files/candidate.pdb'
    raw = output / 'run/mpnn/results/candidate.pdb'
    assert display.samefile(raw) and display.samefile(source)
    shutil.rmtree(output)
    rows = nextflow(tmp_path, workflow, {}, resume=True)
    assert len(rows) == 1 and rows[0]['status'] == 'CACHED'
    assert display.samefile(raw)
    shutil.rmtree(tmp_path / 'work')
    source.unlink(); meta.unlink()
    assert display.read_text() == 'REMARK INERT EXPORT\nEND\n'
    assert json.loads((output / 'pdb_files/candidate.json').read_text())['identity'] == 'retained'


def test_nextflow_retry_uses_new_native_work_before_link_publication(tmp_path):
    text = (ROOT / 'modules/caliby.nf').read_text()
    helper = re.search(r'def calibyPublishMode\(workDir, outputDir\) \{.*?^\}', text, re.S | re.M).group()
    workflow = helper + """
process ClosedRetryFixture {
    errorStrategy 'retry'
    maxRetries 1
    publishDir "${params.out_dir}/retained", mode: "${calibyPublishMode(workflow.workDir, params.out_dir)}", pattern: 'candidate.pdb'
    output:
    path 'candidate.pdb'
    script:
    \"\"\"
    printf 'INERT ATTEMPT ${task.attempt}' > candidate.pdb
    ${task.attempt == 1 ? 'exit 1' : ':'}
    \"\"\"
}
workflow { ClosedRetryFixture() }
"""
    output = tmp_path / 'results'
    rows = nextflow(tmp_path, workflow, {'out_dir': str(output)})
    assert len(rows) == 2 and {r['status'] for r in rows} == {'FAILED', 'COMPLETED'}
    attempts = list((tmp_path / 'work').glob('*/*/candidate.pdb'))
    assert len(attempts) == 2 and not attempts[0].samefile(attempts[1])
    retained = output / 'retained/candidate.pdb'
    assert retained.read_text() == 'INERT ATTEMPT 2'
    assert sum(p.samefile(retained) for p in attempts) == 1
    shutil.rmtree(tmp_path / 'work')
    assert retained.read_text() == 'INERT ATTEMPT 2'


def test_fold_cp_declared_alignment_projection_and_private_yaml(tmp_path):
    text = (ROOT / 'modules/boltz_cp_experimental.nf').read_text()
    code = text.split("python3 - ${inputConfigPath} ${shellQuote(params.get('boltz_prepared_msa_sha256') ?: '')} <<'PY'\n", 1)[1].split('\nPY', 1)[0]
    source = tmp_path / 'prepared'; source.mkdir()
    msa = source / 'chain.a3m'; msa.write_text('>query\nAG\n')
    config = source / 'input.yaml'; config.write_text('sequences: []\n')
    (source / 'msa-inputs.json').write_text(json.dumps({'configs': [{'path': 'input.yaml', 'chains': [{'alignment': {'mode': 'file', 'path': 'chain.a3m'}}]}]}))
    task = tmp_path / 'task'; task.mkdir(); (task / 'staged_input').mkdir()
    run = subprocess.run([sys.executable, '-c', code, str(source), 'reviewed'], cwd=task, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    retained = task / 'staged_input/input_bundle'
    assert (retained / 'chain.a3m').samefile(msa)
    assert not (retained / 'input.yaml').samefile(config)
    (retained / 'input.yaml').write_text('private binding rewrite\n')
    assert config.read_text() == 'sequences: []\n'
    shutil.rmtree(source)
    assert (retained / 'chain.a3m').read_text() == '>query\nAG\n'



def test_blind_pose_regular_projection_reaches_actual_sequence_reader(tmp_path):
    from tests.test_binder_blind_pose import sources
    from run_binder_blind_pose import compile_selected_inputs
    manifest, _, target = sources(tmp_path)
    source = tmp_path / 'candidate.pdb'
    text = (ROOT / 'modules/binder_blind_pose.nf').read_text()
    code = text.split("python3 - ${candidate_pdbs} <<'PY'\n", 1)[1].split('\nPY', 1)[0]
    task = tmp_path / 'task'; task.mkdir()
    result = subprocess.run([sys.executable, '-c', code, str(source)], cwd=task, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    staged = task / 'staged_candidates'
    assert (staged / source.name).samefile(source)
    source.unlink()
    compiled = compile_selected_inputs(manifest, staged, target)
    assert [(c['id'], c['sequence']) for c in compiled[0]['components']] == [('B', 'AG'), ('T', 'Y')]


def test_real_compress_task_keeps_portable_archive_without_view_copies(tmp_path):
    import tarfile
    source = tmp_path / 'candidate.pdb'; source.write_text('REMARK INERT ARCHIVE\nEND\n')
    output = tmp_path / 'results'
    workflow = f"""include {{ Compress }} from '{ROOT}/modules/compress.nf'
workflow {{ Compress('fixture', file('{source}')) }}
"""
    nextflow(tmp_path, workflow, {'out_dir': str(output)})
    views = list((tmp_path / 'work').glob('*/*/fixture_results/candidate.pdb'))
    assert len(views) == 1 and views[0].is_symlink() and views[0].samefile(source)
    archive = output / 'run/fixture/fixture_results.tar.gz'
    source.unlink(); shutil.rmtree(tmp_path / 'work')
    with tarfile.open(archive) as stream:
        member = stream.getmember('fixture_results/candidate.pdb')
        assert member.isfile() and not member.issym()
        assert stream.extractfile(member).read() == b'REMARK INERT ARCHIVE\nEND\n'


@pytest.mark.parametrize('module,process,tree,outputs,published', [
    ('structure_prediction', 'BoltzFromSequenceTask', 'predictions', "path 'predictions/*'", ['scientific/boltz/retained/predictions/candidate.pdb', 'pdb_files/predictions/candidate.pdb']),
    ('esmfold2_experimental', 'ESMFold2MSAPredict', 'esmfold2_results', "path 'esmfold2_results/*'", ['final/esmfold2/fixture/esmfold2_results/candidate.cif', 'pdb_files/esmfold2_results/candidate.cif']),
    ('boltzgen', 'RunBoltzGen', 'output/designs', "path 'output/designs/*'", ['pdb_files/candidate.pdb', 'collected/boltzgen_raw/candidate.pdb']),
    ('caliby', 'RunCaliby', 'results', "path 'results/*'", ['pdb_files/candidate.pdb', 'collected/caliby_raw/candidate.pdb']),
    ('proteinmpnn', 'RunMPNN', 'mpnn_canonical', "path 'mpnn_canonical/*'\n    path 'mpnn_native_output'", ['pdb_files/candidate.pdb', 'run/mpnn/raw/candidate.pdb']),
    ('fampnn', 'RunFAMPNN', 'results', "path 'results/*'\n    path 'fampnn_output'", ['pdb_files/candidate.pdb', 'run/fampnn/results/candidate.pdb', 'run/fampnn/raw/samples/candidate.pdb']),
])
def test_literal_final_publishers_shared_payload_and_cached_republish(tmp_path, module, process, tree, outputs, published):
    # Execute the exact controller publisher declarations, without running a
    # numerical engine or representing these inert bytes as native output.
    text = (ROOT / f'modules/{module}.nf').read_text()
    name = module.replace('_', '') + 'PublishMode'
    helper = re.search(r'def ' + name + r'\(workDir, outputDir\) \{.*?^\}', text, re.S | re.M).group()
    block = text.split('process ' + process + ' {', 1)[1].split('    input:', 1)[0]
    directives = '\n'.join(line for line in block.splitlines() if 'publishDir ' in line and name in line)
    extension = 'cif' if module == 'esmfold2_experimental' else 'pdb'
    body = f"mkdir -p {tree}; printf 'INERT PUBLICATION FIXTURE' > {tree}/candidate.{extension}"
    if module == 'proteinmpnn':
        body += '; cp -a -l mpnn_canonical mpnn_native_output'
    if module == 'fampnn':
        body += '; mkdir -p fampnn_output/samples; cp -l results/candidate.pdb fampnn_output/samples/'
    workflow = helper + f"""
process PublicationFixture {{
{directives}
    input:
    val producer_meta
    val request
    val sequence_name
    output:
    {outputs}
    script:
    \"\"\"{body}\"\"\"
}}
workflow {{ PublicationFixture([producer_artifact_id:'retained'], [esmf_sequence_name:'fixture'], 'fixture') }}
"""
    output = tmp_path / 'published'
    params = {'out_dir': str(output), 'protein_science_contract_revision': 1}
    nextflow(tmp_path, workflow, params)
    paths = [output / value for value in published]
    assert all(p.is_file() and not p.is_symlink() for p in paths), list(output.rglob('*'))
    assert len({(p.stat().st_dev, p.stat().st_ino) for p in paths}) == 1
    shutil.rmtree(output)
    rows = nextflow(tmp_path, workflow, {}, resume=True)
    assert len(rows) == 1 and rows[0]['status'] == 'CACHED'
    assert len({(p.stat().st_dev, p.stat().st_ino) for p in paths}) == 1
    shutil.rmtree(tmp_path / 'work')
    assert all(p.read_bytes() == b'INERT PUBLICATION FIXTURE' for p in paths)
