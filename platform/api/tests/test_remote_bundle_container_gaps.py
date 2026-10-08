"""Focused real config evaluation and opt-in no-home import-only acceptance."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from services.remote_execution import bundle


@pytest.mark.skipif(not os.getenv('BMS_RELOCATION_TEST_NEXTFLOW_JAR'), reason='explicit installed Nextflow parser probe opt-in')
@pytest.mark.parametrize('remote,backend,use_msa,offline', [(True, 'colabfold_api', True, False), (True, 'local', True, True), (False, 'colabfold_api', True, True), (True, 'local', False, False)])
def test_protenix_bind_contract(tmp_path, remote, backend, use_msa, offline):
    config = Path(__file__).resolve().parents[3] / 'nextflow.config'
    source = config.read_text()
    block = source.split('    withLabel: Protenix {', 1)[1].split('\n    withLabel:', 1)[0]
    minimal = tmp_path/'nextflow.config'
    minimal.write_text('params {\ncontainer_dir="/containers"\nprotenix_weights="/weights/protenix"\n'
                       'msa_local_db="/offline-absent"\nmsa_cache_dir="/remote/cache"\n'
                       f'protenix_msa_backend="{backend}"\nprotenix_use_msa={str(use_msa).lower()}\n'
                       'cm_api_runtime_dir="/remote/support-python"\ncode_root="/source"\n}\n'
                       'process {\nwithLabel: Protenix {' + block + '\n}\n')
    env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_HOME=str(tmp_path/'nxf-home'), HOME=str(tmp_path))
    env.pop('BMS_REMOTE_EXECUTION', None)
    if remote:
        env['BMS_REMOTE_EXECUTION'] = '1'
    jar = Path(os.environ['BMS_RELOCATION_TEST_NEXTFLOW_JAR'])
    assert jar.is_file(), 'An installed Nextflow JAR is required; never bootstrap over the network'
    result = subprocess.run(['java', '-jar', str(jar), 'config', '-flat', str(tmp_path)],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ('--bind /offline-absent:/offline-absent' in result.stdout) is offline
    assert '--bind /remote/cache:/remote/cache' in result.stdout
    assert '--bind /remote/support-python:/remote/support-python:ro' in result.stdout


@pytest.mark.skipif(not os.getenv('BMS_RELOCATION_TEST_RUNTIME'), reason='explicit readonly managed-runtime probe opt-in')
def test_live_managed_runtime_imports_in_no_home_container(tmp_path):
    source = Path(os.environ['BMS_RELOCATION_TEST_RUNTIME']).resolve()
    image = Path(os.environ['BMS_RELOCATION_TEST_IMAGE'])
    assert source.is_dir() and image.is_file()
    destination = tmp_path/'remote/support-python'
    bundle._relocate_python_runtime(source, destination, str(destination))
    # Digest every relocated file/link before executing; no live source changes.
    records = bundle._records_for_source(destination, 'runtime/support-python', 'runtime')
    assert len(records) > 100
    interpreter = destination/'venv/bin/python'
    script = ('import json,ssl,sqlite3,packaging,sys,pathlib; '
              f'assert not pathlib.Path({str(source)!r}).exists(); '
              'print(json.dumps({"base":sys.base_prefix,"prefix":sys.prefix,"dependency":packaging.__version__}))')
    result = subprocess.run(['apptainer', 'exec', '--containall', '--no-home', '--cleanenv',
                             '--bind', f'{destination}:{destination}:ro', str(image),
                             str(interpreter), '-I', '-B', '-c', script],
                            cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                            text=True, capture_output=True, check=True, timeout=60)
    observed = json.loads(result.stdout)
    assert observed['base'].startswith(str(destination))
    assert observed['prefix'].startswith(str(destination))
    print('REAL_NO_HOME', json.dumps(observed), f'digest_bound_files={len(records)}', 'original_inaccessible=True')
