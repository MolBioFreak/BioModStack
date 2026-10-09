"""Opt-in, route-free native control for ONT retained execution.

Uses a task-owned two-revision Git checkout and a deliberately bounded version
probe in the ONT entrypoint, NOT an ONT inference result. The real historical
compiler, real Nextflow cache and real Dorado SIF execute. No daemon/job/DB is used.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'platform/api'), str(ROOT)]


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, message):
    git(repo, 'add', 'workflows/ngs/ont_basecall_dna.nf', 'nextflow.config')
    git(repo, '-c', 'user.name=Native replay control', '-c', 'user.email=replay@example.invalid', 'commit', '-qm', message)


@pytest.mark.skipif(not os.environ.get('BMS_TEST_ONT_RESUME_ASSETS'), reason='explicit native ONT assets required')
def test_retained_source_runtime_and_nextflow_cache(tmp_path, monkeypatch):
    assets = Path(os.environ['BMS_TEST_ONT_RESUME_ASSETS'])
    sif = assets / 'dorado-2.1.2.sif'
    assert sif.is_file()
    engine = assets / 'nextflow'
    assert engine.is_file()
    apptainer = shutil.which('apptainer')
    assert apptainer
    repo = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '--quiet', '--shared', str(ROOT), str(repo)], check=True)
    for key, name in {'HOME':'home', 'XDG_CONFIG_HOME':'xdg', 'XDG_CACHE_HOME':'cache',
            'BMS_DATA':'data', 'BMS_INPUTS':'inputs', 'BMS_RESULTS_DIR':'results',
            'BMS_RESULTS_ROOT':'results', 'BMS_WORK':'work', 'BMS_WEIGHTS':'weights',
            'BMS_CONTAINER_DIR':'containers', 'BMS_MOLBIO_NGS_REFERENCE_ROOT':'references',
            'BMS_STATE_DIR':'state', 'NXF_HOME':'nxf-home', 'TMPDIR':'tmp'}.items():
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        monkeypatch.setenv(key, str(path))
    for key, name in {'DATABASE_URL':'main', 'BMS_EXPERIMENT_DATABASE_URL':'experiment',
                      'BMS_MOLBIO_NGS_DATABASE_URL':'molbio'}.items():
        monkeypatch.setenv(key, 'sqlite+aiosqlite:///' + str(tmp_path / (name + '.db')))
    monkeypatch.setenv('BMS_HOME', str(repo))
    monkeypatch.setenv('BMS_DB_PATH', str(tmp_path / 'main.db'))
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', str(engine))
    monkeypatch.setenv('NXF_VER', '25.10.0')
    monkeypatch.setenv('NXF_OFFLINE', 'true')
    monkeypatch.setenv('NXF_ANSI_LOG', 'false')
    monkeypatch.setenv('NXF_CACHE_DIR', str(tmp_path / 'results/.nextflow'))
    shutil.copytree(assets / 'nxf-home/framework', tmp_path / 'nxf-home/framework')
    # No source path appears in the task command, so relocation does not change
    # this bounded native task hash. Source-dependent scripts may rerun normally.
    workflow = '''nextflow.enable.dsl=2
process RetainedRuntimeControl {
  cpus 1
  memory '512 MB'
  output:
  path 'version.txt'
  script:
  """
  APPTAINER_EXEC exec '${params.dorado_runtime_sif}' dorado --version > version.txt 2>&1
  printf 'SOURCE_MARKER\\n' >> version.txt
  """
}
workflow { RetainedRuntimeControl() }
'''.replace('APPTAINER_EXEC', apptainer)
    entry = repo / 'workflows/ngs/ont_basecall_dna.nf'
    entry.write_text(workflow.replace('SOURCE_MARKER', 'retained-source'))
    (repo / 'nextflow.config').write_text('profiles {\n ont_basecall_dna { process.executor = "local" }\n workstation_ryzen7960x { process.maxForks = 1 }\n}\n')
    commit(repo, 'bounded retained native control')
    from component_runtime import SourceIdentity
    from services import nextflow
    old = SourceIdentity.from_checkout(repo)
    params = dict(ont_workflow_id='ont_basecall_dna', ont_input_mode='pod5',
                  pod5_dir=str(tmp_path / 'inputs'), dorado_runtime_sif=str(sif),
                  work_dir=str(repo / 'work'),
                  run_alignment=False, run_assembly=False, run_modifications=False)
    job = SimpleNamespace(id='cached-native-control', model_id='nanopore', mode='basecall_dna',
        params=dict(params), provenance={}, execution_source_revision=old.revision,
        execution_source_tree=old.tree, output_dir=str(tmp_path / 'results'))
    class Session:
        async def commit(self):
            pass
    def compile_launch():
        return asyncio.run(nextflow._compile_launch_nextflow_invocation(Session(), job, job.params, job.output_dir))
    def run(invocation, label):
        env = dict(os.environ)
        source = nextflow._local_nextflow_source_root(invocation, job.params, env)
        command = list(invocation.command) + ['-with-trace', str(tmp_path / (label + '.tsv'))]
        result = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True, timeout=180)
        (tmp_path / (label + '.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        rows = (tmp_path / (label + '.tsv')).read_text().splitlines()
        assert len(rows) == 2
        return dict(zip(rows[0].split('\t'), rows[1].split('\t'))), command
    baseline = compile_launch()
    first, first_cmd = run(baseline, 'baseline')
    assert first['status'] == 'COMPLETED'
    # The installation now contains different executable source. Its current
    # runtime selector is intentionally invalid, exposing any retained overwrite.
    entry.write_text(workflow.replace('SOURCE_MARKER', 'current-source'))
    commit(repo, 'bounded current native control upgrade')
    current = SourceIdentity.from_checkout(repo)
    assert current.tree != old.tree
    monkeypatch.setenv('BMS_NGS_RUNTIME_SIF', str(tmp_path / 'unavailable-current-runtime.sif'))
    job.params.update(resume_job_id=job.id, resume_work_dir='work',
                      resume_source_dir=job.output_dir)
    resumed = compile_launch()
    second, second_cmd = run(resumed, 'resumed')
    assert resumed.source_identity == old
    assert resumed.native_parameters['dorado_runtime_sif'] == str(sif)
    assert second['status'] == 'CACHED'
    assert second['hash'] == first['hash']
    assert resumed.native_parameters['resume_work_dir'] == str(repo / 'work')
    products = list((repo / 'work').glob('*/*/version.txt'))
    assert len(products) == 1
    assert 'retained-source' in products[0].read_text()
    assert '2.1.2' in products[0].read_text()
    # An actually corrupt selected runtime is a native error, not a cache hit
    # or a silent fallback to today's installation/runtime.
    corrupt_sif = tmp_path / 'corrupt.sif'
    corrupt_sif.write_bytes(b'not an Apptainer image')
    negative_command = list(resumed.command)
    negative_command[negative_command.index('--dorado_runtime_sif') + 1] = str(corrupt_sif)
    negative = subprocess.run(negative_command, cwd=resumed.native_parameters['code_root'],
        env=dict(os.environ), capture_output=True, text=True, timeout=180)
    (tmp_path / 'corrupt-runtime.log').write_text(negative.stdout + negative.stderr)
    assert negative.returncode != 0
    assert 'Error executing process >' in negative.stdout + negative.stderr
    # Fresh work selects the current source and an independent output/cache.
    job.id = 'fresh-native-control'
    job.execution_source_revision, job.execution_source_tree = current.revision, current.tree
    job.params = dict(params, work_dir=str(tmp_path / 'fresh-work'))
    job.output_dir = str(tmp_path / 'fresh-results')
    monkeypatch.setenv('NXF_CACHE_DIR', str(tmp_path / 'fresh-results/.nextflow'))
    fresh = compile_launch()
    third, third_cmd = run(fresh, 'fresh')
    assert fresh.source_identity == current
    assert '-resume' not in fresh.command
    assert third['status'] == 'COMPLETED'
    fresh_product, = (tmp_path / 'fresh-work').glob('*/*/version.txt')
    assert 'current-source' in fresh_product.read_text()
    report = dict(scope='native bounded Dorado version/cache control; no ONT inference',
        old=old.__dict__, current=current.__dict__, sif=str(sif),
        sif_sha256=hashlib.file_digest(sif.open('rb'), 'sha256').hexdigest(),
        baseline=first, resumed=second, fresh=third,
        corrupt_runtime_exit_code=negative.returncode,
        commands=[first_cmd, second_cmd, third_cmd],
        outputs=[products[0].read_text(), fresh_product.read_text()])
    (tmp_path / 'native-proof.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
