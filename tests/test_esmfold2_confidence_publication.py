"""Native Nextflow publication of inert ESMFold2 output; no model execution."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('sidecar', [False, True])
def test_native_module_publishes_optional_confidence(tmp_path, sidecar):
    jar = Path('/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')
    if not jar.is_file():
        pytest.skip('pinned offline Nextflow distribution not installed')
    seed = tmp_path / 'inert-producer-output'
    seed.mkdir()
    for name in ('fixture.cif', 'fixture.metrics.json', 'manifest.json', 'summary.tsv'):
        (seed / name).write_text('INERT PUBLICATION FIXTURE; NOT MODEL OUTPUT\n')
    if sidecar:
        spec = importlib.util.spec_from_file_location('confidence_writer', ROOT / 'scripts/run_esmfold2_inference.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        runner.retain_native_confidence(SimpleNamespace(plddt=np.array([.3, .7]), pae=np.array([[0., 2.], [3., 0.]])), seed, 'fixture')
    fixture = tmp_path / 'fixture.nf'
    fixture.write_text(f'''nextflow.enable.dsl=2
include {{ ESMFold2MSAPredict }} from '{ROOT}/modules/esmfold2_experimental.nf'
workflow {{
    ESMFold2MSAPredict(Channel.of(tuple([id:'fixture'], [core_protein_scientific_contract:1, esmf_sequence:'AC', esmf_sequence_name:'fixture'], [], [])))
}}
''')
    config = tmp_path / 'fixture.config'
    config.write_text(f'''process.executor='local'
process.cpus=1
process.memory='256 MB'
process.ext.scripts_root='{ROOT}/scripts'
docker.enabled=false
singularity.enabled=false
params.out_dir='{tmp_path}/published'
''')
    copier = tmp_path / 'inert.py'
    copier.write_text(f'''import shutil
from pathlib import Path
out=Path('esmfold2_results')
out.mkdir(exist_ok=True)
for p in Path({str(seed)!r}).iterdir(): shutil.copyfile(p, out/p.name)
''')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    shim = bindir / 'python3'
    shim.write_text(f'''#!/bin/sh
if [ "$1" = {shlex.quote(str(ROOT / 'scripts/run_esmfold2_inference.py'))} ]; then
    exec {shlex.quote(sys.executable)} {shlex.quote(str(copier))}
fi
exec {shlex.quote(sys.executable)} "$@"
''')
    shim.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH'], NXF_OFFLINE='true',
        NXF_HOME=str(tmp_path / 'nxf-home'), NXF_ASSETS=str(tmp_path / 'assets'),
        NXF_TEMP=str(tmp_path / 'nxf-temp'), HOME=str(tmp_path), JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2',
        PYTHONDONTWRITEBYTECODE='1', BMS_NVIDIA_SMI='/bin/false')
    result = subprocess.run(['java', '-jar', str(jar), '-C', str(config), 'run', str(fixture),
        '-work-dir', str(tmp_path / 'work')], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    destinations = list((tmp_path / 'published' / 'final' / 'esmfold2').rglob('manifest.json'))
    assert len(destinations) == 1
    publication = destinations[0].parent
    for path in seed.iterdir():
        assert (publication / path.name).read_bytes() == path.read_bytes()
    assert len(list(publication.glob('*.confidence.npz'))) == int(sidecar)
