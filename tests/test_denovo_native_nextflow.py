"""Real pinned Nextflow module; only the native executable is an inert fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_rfd4_relocated_native_closure(tmp_path):
    launcher = Path(os.environ['BMS_TEST_NEXTFLOW_LAUNCHER'])
    closure = tmp_path / 'retained'
    for name, value in [('0000/same.cif', 'first original'), ('0001/same.cif', 'second original')]:
        path = closure / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)
    (closure / 'request.json').write_text(json.dumps({'fixture': 'transport only'}))
    (closure / 'bindings.json').write_text(json.dumps({'left': '0000/same.cif', 'right': '0001/same.cif'}))
    bindir = tmp_path / 'bin'; bindir.mkdir()
    shim = bindir / 'apptainer'
    shim.write_text('#!' + sys.executable + '\n' + '''import json, pathlib, sys
args = sys.argv[1:]
assert args[args.index('-m') + 1] == 'bms_rfd4.run'
root = pathlib.Path('rfd4_closure')
bindings = json.loads((root / 'bindings.json').read_text())
assert (root / bindings['left']).read_text() == 'first original'
assert (root / bindings['right']).read_text() == 'second original'
out = pathlib.Path('rfd4_proteina'); out.mkdir()
(out / 'inert-transport-receipt.json').write_text(json.dumps({'native_execution': False, 'argv': args, 'bindings': bindings}))
''')
    shim.chmod(0o755)
    relocated = tmp_path / 'relocated'; (relocated / 'modules').mkdir(parents=True)
    (relocated / 'workflows').mkdir()
    shutil.copyfile(ROOT / 'modules/rfd4_proteina.nf', relocated / 'modules/rfd4_proteina.nf')
    shutil.copyfile(ROOT / 'workflows/rfd4_proteina.nf', relocated / 'workflows/rfd4_proteina.nf')
    output = tmp_path / 'published'
    config = tmp_path / 'fixture.config'
    config.write_text("process.executor = 'local'\nsingularity.enabled = false\n" +
        '\n'.join('params.' + key + ' = ' + json.dumps(str(value)) for key, value in {
            'rfd4_input_closure': closure, 'out_dir': output, 'container_dir': tmp_path / 'images',
            'weights_root': tmp_path / 'weights', 'code_root': ROOT, 'gpu_id': '0',
            'rfd4_proteina_bundle': tmp_path / 'weights/rfd4-proteina',
            'rfd4_proteina_container_path': tmp_path / 'images/rfd4_proteina.sif'}.items()))
    env = {**os.environ, 'PATH': str(bindir) + ':' + os.environ['PATH'],
           'NXF_OFFLINE': 'true', 'NXF_HOME': str(tmp_path / 'nxf-home')}
    completed = subprocess.run([str(launcher), '-C', str(config), 'run',
        str(relocated / 'workflows/rfd4_proteina.nf'), '-work-dir', str(tmp_path / 'work')],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads((output / 'collected/rfd4_proteina/inert-transport-receipt.json').read_text())
    assert receipt['native_execution'] is False
    assert receipt['bindings'] == {'left': '0000/same.cif', 'right': '0001/same.cif'}
