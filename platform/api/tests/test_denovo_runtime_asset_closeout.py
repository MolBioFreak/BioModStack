"""Cold selected closure and task-private allocation; no scientific execution."""
import ast
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


def complexa_weights(**changes):
    from model_registry import native_checkpoint_dependencies
    request = dict(task='protein_binder', task_name='fixture', operation='generate')
    request.update(changes)
    deps, unresolved = native_checkpoint_dependencies('RunComplexa', {'complexa_request': request})
    assert not unresolved
    return {d.relative_path for d in deps if d.kind == 'weights'}


@pytest.mark.parametrize('reward,expected', [('profile', {'alphafold/params'}), (None, set()),
    ({'rf3folding': {}}, {'foundry/checkpoints/rf3_foundry_01_24_latest_remapped.ckpt'})])
def test_complexa_cold_reward_closure_exact(reward, expected):
    assert complexa_weights(reward=reward) == {'complexa/complexa.ckpt', 'complexa/complexa_ae.ckpt'} | expected


def test_complexa_native_evaluation_closure_not_generation_rewards():
    selected = complexa_weights(operation='evaluate', evaluation_slots=['retained'], reward=None,
        evaluation={'compute_binder_metrics': False, 'compute_esm_metrics': False,
                    'compute_designability': False, 'compute_codesignability': True})
    assert selected == {'complexa/complexa.ckpt', 'complexa/complexa_ae.ckpt',
                        'huggingface/models--facebook--esmfold_v1'}
    selected = complexa_weights(operation='evaluate', evaluation_slots=['retained'], reward=None,
        evaluation={'compute_binder_metrics': False, 'compute_esm_metrics': False,
                    'compute_designability': False, 'compute_codesignability': False})
    assert selected == {'complexa/complexa.ckpt', 'complexa/complexa_ae.ckpt'}


@pytest.mark.parametrize('design,codesign,overrides,expected', [
    (False, False, {}, set()),
    (True, False, {}, {'laproteina-evaluators/ProteinMPNN/vanilla_model_weights/v_48_020.pt', 'huggingface/models--facebook--esmfold_v1'}),
    (False, True, {}, {'huggingface/models--facebook--esmfold_v1'}),
    (True, True, {'compute_designability': False, 'compute_codesignability': False}, set()),
])
def test_la_retained_config_selects_exact_native_evaluation_assets(tmp_path, design, codesign, overrides, expected):
    from model_registry import native_checkpoint_dependencies
    import yaml
    (tmp_path / 'retained.yaml').write_text(yaml.safe_dump({'generation': {'metric': {
        'compute_designability': design, 'compute_codesignability': codesign,
        'designability_folding_models': ['esmfold'], 'codesignability_folding_models': ['esmfold']}}}))
    (tmp_path / 'request.json').write_text(json.dumps({'config_path': 'retained.yaml'}))
    dependencies, blockers = native_checkpoint_dependencies('EvaluateLaProteina', {
        'laproteina_evaluation_input_closure': str(tmp_path),
        'laproteina_evaluation_request': {'metrics': overrides}})
    assert not blockers
    assert {d.relative_path for d in dependencies} == expected
    assert all('esmfold2' not in d.relative_path for d in dependencies)


def test_cold_selected_layout_and_helper_closure(tmp_path):
    from model_registry import selected_execution_metadata
    from tools.bms_artifact_cache import Cache
    import sys
    request = dict(task='protein_binder', task_name='fixture', operation='generate', reward='profile')
    metadata = selected_execution_metadata('complexa', 'generate', {'complexa_request': request}, 'workflows/complexa.nf')
    weights = {row.relative_path for row in metadata.dependencies if row.kind == 'weights'}
    assert weights == {'complexa/complexa.ckpt', 'complexa/complexa_ae.ckpt', 'alphafold/params'}
    helpers = {row.relative_path for row in metadata.dependencies if row.kind == 'support_tool'}
    required = {'scripts/lib/complexa_contract.py', 'scripts/bms_complexa/config.py', 'scripts/bms_complexa/profiles.json',
                'scripts/bms_complexa/hf_cache.py', 'scripts/bms_complexa/assets.json'}
    assert required <= helpers
    cold = tmp_path / 'cold-source'
    for member in helpers:
        dest = cold / member
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / member).read_bytes())
    env = {**os.environ, 'PYTHONPATH': str(cold)}
    # No warm source tree is on the subprocess's module path.
    probe = 'from scripts.bms_complexa.config import selected_dependencies; from scripts.lib.complexa_contract import Request; print(selected_dependencies(Request(task="protein_binder",task_name="fixture")))'
    run = subprocess.run([sys.executable, '-B', '-c', probe], cwd=cold, env=env, capture_output=True, text=True, check=True)
    assert 'af2' in run.stdout
    storage = Cache(tmp_path / 'worker-cache')
    entries = []
    for member in sorted(weights):
        name = member if member.endswith('.ckpt') else member + '/fixture-params.bin'
        body = ('inert fixture: ' + name).encode()
        item = dict(sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))
        incoming = Path(storage.root) / 'incoming' / item['sha256']
        incoming.write_bytes(body)
        storage.ingest(item, incoming)
        entries.append(dict(item, name=name, mode=0o444))
    installed = storage.weights(entries, install=True)
    layout = Path(installed['root'])
    assert {p.relative_to(layout).as_posix() for p in layout.rglob('*') if p.is_file() and p.name != '.bms-weights.json'} == {e['name'] for e in entries}
    inodes = {e['name']: (layout / e['name']).stat().st_ino for e in entries}
    assert storage.weights(entries, install=True) == installed
    assert inodes == {e['name']: (layout / e['name']).stat().st_ino for e in entries}
    assert not (layout / 'foundry').exists() and not (layout / 'huggingface').exists()


def _layout(tmp_path):
    shared = tmp_path / 'worker/cache/artifacts/v1/weights/selected'
    shared.mkdir(parents=True)
    rows = []
    for name, data in [('selected/a.bin', b'selected weight'), ('unused/b.bin', b'UNUSED' * 1000)]:
        member = shared / name
        member.parent.mkdir()
        member.write_bytes(data)
        member.chmod(0o444)
        digest = hashlib.sha256(data).hexdigest()
        obj = shared.parent.parent / 'objects/sha256' / digest[:2] / digest
        obj.parent.mkdir(parents=True, exist_ok=True)
        os.link(member, obj)
        rows.append(dict(name=name, sha256=digest, mode=0o444, size_bytes=len(data)))
    (shared / '.bms-weights.json').write_text(json.dumps(rows))
    return shared, rows


def _no_reflink(*args):
    raise OSError(errno.EOPNOTSUPP, 'forced unsupported FICLONE fixture')


def test_unbound_job_weights_are_not_copied_and_remain_shadowed(tmp_path, monkeypatch):
    from tools import bms_container as c
    shared, rows = _layout(tmp_path)
    before = c.snapshot(shared)
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    copied = []
    real = c.views.allocate_private_file
    def record(fd, out, info):
        copied.append(info.st_size)
        return real(fd, out, info)
    monkeypatch.setattr(c.views.fcntl, 'ioctl', _no_reflink)
    monkeypatch.setattr(c.views, 'allocate_private_file', record)
    requested = [(str(tmp_path / 'worker'), '/broad', 'rw'),
                 (str(shared / 'selected'), '/weights/selected', 'ro')]
    volumes, guards = c.project_bindings(requested, inputs, [], shared=shared)
    assert sum(copied) == len(b'selected weight'), copied
    mapped = {guest: Path(host) for host, guest in volumes}
    assert not (mapped[str(shared)] / 'unused').exists()
    assert not (mapped['/broad/cache/artifacts/v1/weights/selected'] / 'unused').exists()
    original = shared / 'selected/a.bin'
    private = mapped['/weights/selected'] / 'a.bin'
    assert not private.samefile(original)
    private.write_bytes(b'private write')
    assert original.read_bytes() == b'selected weight'
    assert c.snapshot(shared) == before
    for row in rows:
        obj_dir = shared.parent.parent / 'objects/sha256' / row['sha256'][:2]
        alias = mapped[str(obj_dir)] / row['sha256']
        if row['name'].startswith('selected/'):
            assert alias.samefile(private)
        else:
            assert not alias.exists()


def test_real_resource_setup_scopes_broad_weight_bind_and_reports_fallback(tmp_path, monkeypatch):
    from model_registry import native_checkpoint_dependencies
    from native_components import native_resource_policy
    from scripts.lib.component_adapter import native_resource_config
    from tools import bms_container as c
    shared, rows = _layout(tmp_path)
    component = dict(authority='modules/laproteina_evaluation.nf:EvaluateLaProteina',
        selection_json={}, resources_json=json.loads(native_resource_policy({})), dependency_ids=['weights:selected'])
    plan = {'metadata': {'static_components': [component], 'dependencies': [
        {'logical_id': 'weights:selected', 'kind': 'weights', 'relative_path': 'selected'}]}}
    config = native_resource_config(plan, {'required': {'cpus': 1, 'memory_bytes': 1024}},
                                    str(tmp_path / 'lock'), ROOT)
    setup = ast.literal_eval(re.search(r'return (.+?) \+ \(nativeSetup', config)[1])
    subprocess.run(['bash', '-euc', setup], cwd=tmp_path, check=True)
    assert json.loads((tmp_path / '.bms-task-weights').read_text()) == ['selected']
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    allocation = {}
    monkeypatch.setattr(c.views.fcntl, 'ioctl', _no_reflink)
    volumes, guards = c.project_bindings([(str(shared), '/weights', 'ro')], inputs, [], shared=shared,
        weight_paths=c.task_weight_paths(tmp_path), allocation=allocation)
    view = {guest: Path(host) for host, guest in volumes}['/weights']
    assert sorted(p.relative_to(view).as_posix() for p in view.rglob('*') if p.is_file()) == ['selected/a.bin']
    assert allocation == {'copy_bytes': len(b'selected weight')}
    for source, before, private, strict in guards:
        assert c.snapshot(source) == before
        if strict is not None:
            assert c.snapshot(private) == strict


def test_scratch_estimate_counts_selected_private_bodies_not_job_union():
    from types import SimpleNamespace as NS
    from services.remote_execution.bundle import _private_weight_copy_upper_bound
    metadata = NS(dependencies=[NS(logical_id=name, relative_path=name, kind='weights') for name in ('a', 'b')],
        static_components=[NS(authority=name, dependency_ids=[name]) for name in ('a', 'b')], dynamic_templates=[])
    entries = [dict(name='a/one', sha256='1' * 64, mode=0o444, size_bytes=10),
               dict(name='a/alias', sha256='1' * 64, mode=0o444, size_bytes=10),
               dict(name='b/two', sha256='2' * 64, mode=0o444, size_bytes=20)]
    assert _private_weight_copy_upper_bound(metadata, entries) == 20
    metadata.static_components[1].authority = 'a'
    assert _private_weight_copy_upper_bound(metadata, entries) == 30


def test_nextflow_writes_selection_before_container_launch(tmp_path):
    from scripts.lib.component_adapter import native_resource_config
    from native_components import native_resource_policy
    import sys
    component = dict(authority='modules/laproteina_evaluation.nf:EvaluateLaProteina',
        selection_json={}, resources_json=json.loads(native_resource_policy({})), dependency_ids=['weights:selected'])
    plan = {'metadata': {'static_components': [component], 'dependencies': [
        {'logical_id': 'weights:selected', 'kind': 'weights', 'relative_path': 'selected'}]}}
    configuration = native_resource_config(plan, {'required': {'cpus': 1, 'memory_bytes': 1024 ** 3}},
        str(tmp_path / 'compute.lock'), ROOT)
    image = tmp_path / 'fixture.sif'
    image.write_bytes(b'inert container argv fixture')
    (tmp_path / 'fixture.config').write_text(configuration + f"\nprocess.container = '{image}'\napptainer.enabled=true\n")
    (tmp_path / 'main.nf').write_text('''nextflow.enable.dsl = 2
process EvaluateLaProteina {
 output:
 path 'observed.txt'
 script:
 "echo fixture > observed.txt"
}
workflow { EvaluateLaProteina() }
''')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    launcher = binaries / 'apptainer'
    launcher.write_text('#!' + sys.executable + '''
import json, os, pathlib, sys
if '--version' in sys.argv:
 print('apptainer version 1.4.2'); raise SystemExit(0)
selection = pathlib.Path('.bms-task-weights').read_text()
pathlib.Path(OUTPUT_PATH).write_text(selection)
index = sys.argv.index('/bin/bash')
os.execv('/bin/bash', sys.argv[index:])
'''.replace('OUTPUT_PATH', repr(str(tmp_path / 'observed-selection.json'))))
    launcher.chmod(0o755)
    env = {**os.environ, 'PATH': str(binaries) + ':' + os.environ['PATH'],
           'NXF_HOME': str(tmp_path / 'nxf-home'), 'NXF_OFFLINE': 'true',
           'NXF_APPTAINER_CACHEDIR': str(tmp_path / 'images'), 'LAUNCH_SELECTION': str(tmp_path / 'observed-selection.json')}
    # The transport recorder is not a container/GPU qualification. It reads
    # the compiler hint at the exact scientific-launcher boundary.
    result = subprocess.run(['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], 'run', str(tmp_path / 'main.nf'),
        '-c', str(tmp_path / 'fixture.config'), '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false'],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'observed-selection.json').read_text()) == ['selected']


@pytest.mark.parametrize('module', ['ligandmpnn_design', 'protonpottsmpnn'])
@pytest.mark.parametrize('backend', ['apptainer', 'udocker'])
def test_explicit_launcher_emitted_argv(tmp_path, module, backend):
    jar = os.environ['BMS_TEST_NEXTFLOW_JAR']
    text = (ROOT / f'modules/{module}.nf').read_text().split('script:', 1)[1]
    end = text.rfind('"""') + 3
    script = tmp_path / 'render.groovy'
    script.write_text("def params = [container_dir:'/images', code_root:'/code', gpu_id:'2']; "
        "def task = [cpus:3]; def request='prepared_request.json'; def source='source/input.pdb';\n" +
        'def result = {\n' + text[:end] + '\n}.call(); print(result)\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    recorder = '#!/bin/bash\nprintf "%s\\n" "$0" "$@" > "${RECORD}"\n'
    (bin_dir / 'apptainer').write_text(recorder)
    (bin_dir / 'apptainer').chmod(0o755)
    selected = bin_dir / 'bms-container'
    selected.write_text(recorder)
    selected.chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH'], 'RECORD': str(tmp_path / 'argv')}
    env.pop('BMS_CONTAINER_EXECUTABLE', None)
    if backend == 'udocker':
        env['BMS_CONTAINER_EXECUTABLE'] = str(selected)
    rendered = subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(script)],
                              env=env, text=True, capture_output=True, check=True)
    subprocess.run(['bash', '-euc', rendered.stdout], cwd=tmp_path, env=env, check=True)
    argv = (tmp_path / 'argv').read_text().splitlines()
    assert argv[0] == str(selected if backend == 'udocker' else bin_dir / 'apptainer')
    assert argv[1] == 'exec'
    assert '/images/' + ('foundry.sif' if module == 'ligandmpnn_design' else 'protonpottsmpnn.sif') in argv
    assert '--request' in argv and 'prepared_request.json' in argv
