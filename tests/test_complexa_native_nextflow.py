"""Real Nextflow staging/argv only; the scientific executable is an inert shim."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('source_name', ['source.pdb', 'source.cif.zst'])
def test_complexa_source_format_and_evaluation_staging(tmp_path, source_name):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('Already cached Nextflow JAR required')
    closure = tmp_path / 'closure'
    closure.mkdir()
    (closure / 'request.json').write_text('{}')
    (closure / source_name).write_bytes(b'INERT SOURCE BYTES')
    if source_name != 'source.pdb':
        (closure / 'source-path.txt').write_text(source_name + '\n')
    (closure / 'evaluation').mkdir()
    (closure / 'evaluation/0000.pdb').write_text('INERT EVALUATION BYTES')
    code = tmp_path / 'inert-code/scripts'
    code.mkdir(parents=True)
    (code / 'run_complexa_inference.py').write_text('''import json, pathlib, sys
args=sys.argv[1:]
source=pathlib.Path(args[args.index('--source')+1])
evaluation=pathlib.Path(args[args.index('--evaluation-directory')+1])
assert source.read_bytes() == b'INERT SOURCE BYTES'
assert (evaluation/'0000.pdb').read_text() == 'INERT EVALUATION BYTES'
out=pathlib.Path('results');out.mkdir()
(out/'argv.json').write_text(json.dumps({'inert':True,'argv':args}))
''')
    driver = tmp_path / 'driver.nf'
    driver.write_text(f"include {{ RunComplexa }} from '{ROOT / 'modules/complexa.nf'}'\nworkflow {{ RunComplexa(file(params.closure)) }}\n")
    config = tmp_path / 'offline.config'
    config.write_text("process.executor='local'\nprocess.cpus=1\nprocess.memory='256 MB'\nexecutor.queueSize=1\n")
    params = tmp_path / 'params.json'
    params.write_text(json.dumps({'closure':str(closure),'code_root':str(code.parent),
        'out_dir':str(tmp_path/'published'),'complexa_weights_dir':str(tmp_path/'not-loaded'),
        'complexa_autoguidance_checkpoint':None}))
    env = {k:v for k,v in os.environ.items() if not k.startswith('NXF_')}
    env.update(NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_HOME=str(tmp_path/'nxf'),
               TMPDIR=str(tmp_path))
    command=['java', *['--add-opens=java.base/'+n+'=ALL-UNNAMED' for n in ('java.lang','java.util','java.nio')],
        '-Djava.io.tmpdir='+str(tmp_path),'-jar',jar,'-C',str(config),'run',str(driver),
        '-params-file',str(params),'-work-dir',str(tmp_path/'work'),'-ansi-log','false']
    result=subprocess.run(command,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=60)
    (tmp_path/'nextflow.log').write_text(result.stdout+result.stderr)
    assert result.returncode == 0, result.stdout+result.stderr
    captures=list((tmp_path/'work').rglob('results/argv.json'))
    assert len(captures)==1
    capture=json.loads(captures[0].read_text())
    assert capture['inert'] is True
    assert capture['argv'][capture['argv'].index('--source')+1] == 'closure/'+source_name
