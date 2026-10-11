"""Real Nextflow module shells; tiny inert CPU engine transport only."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
JAR = Path('/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')


@pytest.mark.parametrize('engine,exit_code', [('gromacs', 0), ('openmm', 0), ('gromacs', 17), ('openmm', 17)])
def test_real_nextflow_preview_shell_durable_output_and_cache(tmp_path, engine, exit_code, record_property):
    assert JAR.is_file(), 'qualification requires the assigned real Nextflow JAR'
    runtime = tmp_path / 'inert-engine.sif'
    runtime.write_bytes(b'not an image: inert shell-boundary identity')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    wrapper = binaries / 'python3'
    wrapper.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['-m', 'scripts.bms_md.cli']:
    if args[2] == 'validate':
        config = json.loads(Path(args[args.index('--config') + 1]).read_text())
        config['engine_runtime'] = {{'sif_sha256': '{hashlib.sha256(runtime.read_bytes()).hexdigest()}'}}
        Path(args[args.index('--output') + 1]).write_text(json.dumps(config))
    elif args[2] == 'run':
        sys.path.insert(0, {str(ROOT / 'platform/api/tests')!r})
        from test_one_copy_pub_md_storage import emit_native
        emit_native(Path(args[args.index('--output-dir') + 1]), engine={engine!r},
                    status={'completed' if exit_code == 0 else 'failed'!r})
        with open({str(tmp_path / 'writer-calls')!r}, 'a') as log:
            log.write('native-writer\\n')
        sys.exit({exit_code})
    else:
        raise AssertionError(args)
else:
    os.execv({sys.executable!r}, [{sys.executable!r}, *args])
''')
    wrapper.chmod(0o755)
    checksum = binaries / 'sha256sum'
    checksum.write_text(f'''#!{sys.executable}
import subprocess, sys
payload = sys.stdin.read().replace('/opt/bms-md-engine-runtime.sif', {str(runtime)!r})
sys.exit(subprocess.run([{shutil.which('sha256sum')!r}, *sys.argv[1:]], input=payload, text=True).returncode)
''')
    checksum.chmod(0o755)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'schema': 'bms.md.job.v1', 'engine': engine,
                                 'execution': {'gpu_offload': 'none'}}))
    preparation = tmp_path / 'preparation'
    preparation.mkdir()
    (preparation / 'inert.txt').write_text('staged input must remain a reference')
    pipeline = tmp_path / 'main.nf'
    name = f'MD_{engine.upper()}_REPLICA'
    module = ROOT / f'modules/experimental/molecular_dynamics/{engine}_replica'
    pipeline.write_text(f'''nextflow.enable.dsl=2
include {{ {name} }} from '{module}'
params.md_resume_checkpoint = null
params.md_resume_checkpoint_sha256 = null
workflow {{
    {name}(Channel.of(tuple(0, file('{config}'), file('{preparation}'))))
}}
''')
    (tmp_path / 'nextflow.config').write_text('trace.overwrite = true')
    durable = tmp_path / 'result/native'
    work = tmp_path / 'work'
    invocation = ['java', '--add-opens=java.base/java.lang=ALL-UNNAMED',
        '--add-opens=java.base/java.util=ALL-UNNAMED', '--add-opens=java.base/java.nio=ALL-UNNAMED',
        '-jar', str(JAR), '-log', str(tmp_path / 'nextflow.log'), 'run',
        str(pipeline), '-with-trace', str(tmp_path / 'trace.txt'), '-ansi-log', 'false', '-w', str(work),
        '--out_dir', str(durable.parent), '--md_resume_output_dir', str(durable),
        '--code_root', str(ROOT), '--gpu_id', '0']
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
               NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true')
    preview = subprocess.run([*invocation, '-preview'], cwd=tmp_path, env=env,
                             capture_output=True, text=True, timeout=90)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert not durable.exists()
    result = subprocess.run(invocation, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    record_property('nextflow_output', result.stdout + result.stderr)
    command_scripts = list(work.glob('*/*/.command.sh'))
    assert len(command_scripts) == 1
    task = command_scripts[0].parent
    shell = command_scripts[0].read_text()
    assert 'cp -a' not in shell
    assert f"--output-dir '{durable}'" in shell
    assert (task / 'preparation').is_symlink()
    assert (durable / 'production.xtc').is_file()
    assert (durable / '.private-continuation/seed').is_file()
    assert (tmp_path / 'writer-calls').read_text() == 'native-writer\n'
    # The worker's real terminal inventory sees native/ exactly once, with no
    # task alias in the returned tree. This is local transport, never SSH.
    from tools.bms_remote_worker import build_result_manifest, ENVELOPE_FILE
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    envelope = {'output_directory': str(durable.parent), 'attempt_id': 'inert-attempt',
                'job_id': 'storage-parent', 'source_revision': 'inert', 'source_tree': 'inert'}
    (attempt / ENVELOPE_FILE).write_text(json.dumps(envelope))
    wire = build_result_manifest(attempt, envelope, exit_code)
    paths = [row['relative_path'] for row in wire['artifacts']]
    assert [name for name in paths if name.endswith('.xtc')] == ['native/production.xtc']
    assert 'native/.private-continuation/seed' in paths
    for row in wire['artifacts']:
        path = durable.parent / row['relative_path']
        assert path.stat().st_size == row['size_bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
    record_property('remote_manifest_paths', json.dumps(paths))
    if exit_code:
        assert result.returncode != 0
        assert int((task / '.exitcode').read_text()) == exit_code
        assert not (task / 'replica_0').exists()
        assert not list(durable.parent.glob('replicas/**/*_manifest.json'))
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert (task / 'replica_0').is_symlink()
    assert (task / 'replica_0').resolve() == durable
    assert list(durable.parent.glob('replicas/**/*_manifest.json'))
    assert not list(durable.parent.glob('replicas/**/*.xtc'))
    manifest_paths = list(durable.parent.rglob('manifest.json'))
    assert manifest_paths == [durable / 'manifest.json']
    before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_ino)
              for p in durable.iterdir() if p.is_file()}
    resumed = subprocess.run([*invocation, '-resume'], cwd=tmp_path, env=env,
                             capture_output=True, text=True, timeout=90)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert 'Cached process' in resumed.stdout
    assert (tmp_path / 'writer-calls').read_text() == 'native-writer\n'
    assert before == {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_ino)
                      for p in durable.iterdir() if p.is_file()}
    # Original work is retained: no terminal cleanup and no payload allocation there.
    assert command_scripts[0].is_file()
    record_property('durable_trajectory_bytes', (durable / 'production.xtc').stat().st_size)
    record_property('task_trajectory_is_reference', True)
