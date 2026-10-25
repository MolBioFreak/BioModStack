"""Pinned Nextflow transport fixture: native executable is explicitly inert."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def execute_inert_evaluation(closure, output, tmp_path, native_exit=0):
    bindir = tmp_path/'bin'; bindir.mkdir()
    shim = bindir/'python3'
    shim.write_text('#!'+sys.executable+'\n'+f'import sys; sys.path.insert(0, {str(ROOT / "scripts")!r})\n'+'''import json, pathlib, shutil, os
from run_laproteina_inference import publish_evaluation
args = sys.argv[1:]
assert '--evaluate' in args
assert args[0].endswith('/scripts/run_laproteina_inference.py')
owner = pathlib.Path(args[args.index('--request')+1])
request = json.loads(owner.read_text())
source = owner.parent/request['source_directory']
config = owner.parent/request['config_path']
assert (source/'motif_info.csv').read_text() == 'name,ancestry\\noriginal,retained\\n'
out = pathlib.Path(args[args.index('--output-dir')+1]); attempt = out/'evaluation_inert'
attempt.mkdir(parents=True)
shutil.copytree(source, attempt/'samples')
shutil.copyfile(config, attempt/'original_config.yaml')
(attempt/'results.csv').write_text('pdb_path\\n')
exit_code = int(os.environ['BMS_LA_INERT_EXIT'])
receipt = {'execution_status': 'failed' if exit_code else 'completed', 'sample_count': 0, 'native_execution': False,
           'qualification': 'inert Nextflow argv/retained-byte transport only', 'requested': request}
(attempt/'evaluation.json').write_text(json.dumps(receipt))
publish_evaluation(out, attempt, receipt)
sys.exit(exit_code)
''')
    shim.chmod(0o755)
    config = tmp_path/'fixture.config'
    config.write_text("process.executor = 'local'\nsingularity.enabled = false\n"+'\n'.join(
        'params.'+key+' = '+json.dumps(str(value)) for key, value in {
            'laproteina_evaluation_input_closure': closure, 'out_dir': output,
            'container_dir': tmp_path/'inert-images', 'weights_root': tmp_path/'weights', 'code_root': ROOT, 'gpu_id': 0}.items()))
    jar = Path(os.environ.get('BMS_TEST_NEXTFLOW_JAR', '/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'))
    env = {**os.environ, 'PATH': str(bindir)+':'+os.environ['PATH'], 'NXF_OFFLINE': 'true',
           'NXF_HOME': str(tmp_path/'nxf-home'), 'NXF_DISABLE_CHECK_LATEST': 'true', 'BMS_LA_INERT_EXIT': str(native_exit)}
    command = ['java', '-jar', str(jar), '-C', str(config), 'run', str(ROOT/'workflows/laproteina_evaluation.nf'), '-work-dir', str(tmp_path/'work')]
    done = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180)
    (tmp_path/'nextflow.log').write_text(done.stdout+done.stderr)
    assert (done.returncode == 0) == (native_exit == 0), done.stdout+done.stderr
    statuses = list((tmp_path/'work').glob('*/*/.exitcode'))
    assert len(statuses) == 1 and int(statuses[0].read_text()) == native_exit
    return output/'collected/laproteina_evaluation'
