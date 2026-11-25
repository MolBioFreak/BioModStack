"""Admitted structure selection through compilation and consumed native channels."""
import os
from pathlib import Path
import subprocess
import sys

from fastapi import HTTPException
import pytest

from schemas import JobCreate
from services.job_requests import normalize_job_request
from services.nextflow import build_nextflow_command

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = 'template_structure_prediction'


def compile_request(tmp_path, model, mode, method, complex_input=False):
    params = dict(sequence='ACDE', sequence_name='sample', pred_method=method,
                  boltz_use_msa=False, protenix_use_msa=False)
    if complex_input:
        params['complex_components'] = [{'id': 'A', 'type': 'protein', 'sequence': 'ACDE'}]
    if method == 'fold_cp':
        params.update(pinned_gpus=[0], size_cp=1)
    job = normalize_job_request(JobCreate(name='sample', model_id=model, mode=mode, params=params))
    cmd = build_nextflow_command(job.model_id, job.mode, job.params, str(tmp_path))
    return job, cmd


@pytest.mark.parametrize('method,owner,mode,entry', [
    ('boltz', 'boltz2', 'predict', 'structure_prediction'),
    ('protenix', 'protenix', 'complex', 'complex_prediction'),
    ('esmfold2', 'esmfold2', 'predict', 'structure_prediction'),
    ('both', 'boltz2', 'complex', 'complex_prediction'),
    ('fold_cp', 'boltz_cp_experimental', 'design', 'boltz_cp_experimental'),
])
def test_raw_template_dispatch(tmp_path, method, owner, mode, entry):
    job, cmd = compile_request(tmp_path, TEMPLATE, 'structure_prediction', method, mode == 'complex')
    assert (job.model_id, job.mode) == (owner, mode)
    assert f'workflows/{entry}.nf' in cmd


@pytest.mark.parametrize('method', ['rf3', 'boltz_api', 'unknown'])
def test_raw_template_cannot_launch_unsupported_or_paid(tmp_path, method):
    with pytest.raises(HTTPException) as exc:
        compile_request(tmp_path, TEMPLATE, 'structure_prediction', method)
    assert exc.value.status_code == 422
    assert method in str(exc.value.detail)


def test_esm_route_does_not_replace_an_explicit_ensemble(tmp_path):
    with pytest.raises(HTTPException, match='cannot execute boltz_protenix'):
        compile_request(tmp_path, 'esmfold2', 'predict', 'both')


def inert_module(tmp_path, module, scripts):
    # Keep the workflow, input/output declarations, manifest readers and mixing intact.
    source = (ROOT / 'modules' / module).read_text()
    for name, script in scripts.items():
        start = source.index('    script:', source.index(f'process {name} {{'))
        closing = '\n    """\n}'
        end = source.index(closing, start) + len(closing) - 2
        source = source[:start] + '    script:\n    """\n' + script + '\n    """' + source[end:]
    (tmp_path / module).write_text(source)


@pytest.mark.parametrize('model,mode,method,expected', [
    ('protenix', 'predict', 'both', ['boltz', 'protenix']),
    ('protenix', 'complex', 'all', ['boltz', 'protenix']),
    ('boltz2', 'complex', 'both', ['boltz', 'protenix']),
    (TEMPLATE, 'structure_prediction', 'boltz_protenix', ['boltz', 'protenix']),
    ('protenix', 'complex', 'protenix', ['protenix']),
    ('boltz2', 'predict', 'boltz', ['boltz']),
])
def test_consumed_producers(tmp_path, model, mode, method, expected):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('requires cached offline Nextflow jar')
    job, cmd = compile_request(tmp_path, model, mode, method, mode == 'complex')
    complex_input = 'workflows/complex_prediction.nf' in cmd
    assert complex_input == (mode == 'complex')
    writer = ROOT / 'scripts/write_structure_producer_manifest.py'
    metadata = "'${groovy.json.JsonOutput.toJson(producer_meta).bytes.encodeBase64()}'"
    boltz = 'mkdir -p predictions; echo boltz > predictions/sample_model_0.pdb; touch fixture.log boltz_task_binding.json\n'
    protenix = 'mkdir -p predictions/native; echo protenix > predictions/native/sample_sample_0.cif\n'
    protenix += f'python3 {writer} --producer-method protenix --predictions-root predictions --format mmcif --output producer_candidates.json --publication-dir producer_publication --published-structure-root pdb_files/predictions '
    inert_module(tmp_path, 'structure_prediction.nf', {
        'BoltzFromSequenceTask': boltz + f'python3 {ROOT}/scripts/write_sequence_producer_manifest.py --metadata-base64 {metadata} --producer-method boltz --predictions-dir predictions --output producer_candidates.json',
        'BoltzFromComplex': boltz + f'python3 {writer} --producer-sample ${{complex_name}} --producer-method boltz --predictions-root predictions --format pdb --output producer_candidates.json',
        'PrepareComplexWithMSA': 'mkdir -p yamls msa; cp ${complex_json} yamls/${complex_name}.yaml; touch prep.log',
    })
    inert_module(tmp_path, 'protenix.nf', {
        'ProtenixPredict': protenix + f'--producer-sample ${{sequence_name}} --sequence-metadata-base64 {metadata}',
        'ProtenixFromComplex': protenix + '--producer-sample ${input_sample}',
    })
    (tmp_path / 'esmfold2.nf').symlink_to(ROOT / 'modules/esmfold2.nf')
    if complex_input:
        (tmp_path / 'NO_MSA').touch()
        wf, channel = 'complex_prediction_wf', 'canonical_candidates'
        inputs = "tuple('sample', file(params.complex_json_path), file('NO_MSA'))"
    else:
        wf, channel = 'structure_prediction_wf', 'canonical_structures'
        inputs = "tuple(params.sequence_input, 'sample')"
    main = tmp_path / 'main.nf'
    main.write_text(f"include {{ {wf} }} from './structure_prediction'\nworkflow {{\n"
                    f"{wf}(Channel.of({inputs}))\n{wf}.out.{channel}.view {{ m, p -> 'CONSUMED=' + m.producer_method + ':' + p.text.trim() }}\n}}")
    config = tmp_path / 'offline.config'
    config.write_text("process.executor='local'\nprocess.cpus=1\nprocess.memory='128 MB'\n")
    flags = ['--' + key for key in ('pred_method', 'sequence_input', 'complex_json_path')]
    args = [arg for flag in flags if flag in cmd for arg in [flag, cmd[cmd.index(flag) + 1]]]
    result = subprocess.run(['java', '-jar', jar, '-C', str(config), 'run', str(main), *args,
        '--api_python', sys.executable, '--code_root', str(ROOT), '--out_dir', str(tmp_path/'out'),
        '--boltz_use_msa', 'false', '--protenix_use_msa', 'false'], cwd=tmp_path, capture_output=True,
        text=True, timeout=120, env=dict(os.environ, NXF_OFFLINE='true', NXF_ANSI_LOG='false', NXF_HOME=str(tmp_path/'nxf')))
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(line for line in result.stdout.splitlines() if line.startswith('CONSUMED=')) == [
        f'CONSUMED={producer}:{producer}' for producer in expected]
