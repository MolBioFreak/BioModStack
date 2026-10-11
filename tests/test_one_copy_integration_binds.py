"""Real Groovy/Nextflow bind serialization; no scientific container execution."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[1]


def _jar():
    configured = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    jars = [Path(configured)] if configured else sorted((Path.home() / '.nextflow/framework').glob('*/nextflow-*-one.jar'))
    if not jars or not jars[-1].is_file() or not shutil.which('java'):
        pytest.skip('existing Nextflow JAR and Java required; no test downloads')
    return jars[-1]


@pytest.mark.parametrize('backend', ['apptainer', 'udocker', None])
@pytest.mark.parametrize('guest', ['/native/weights', None])
def test_weight_bind_serializes_readonly_native_and_original_names(tmp_path, backend, guest):
    from tools.bms_container import parse_exec
    config = (REPO / 'nextflow.config').read_text()
    method = config[config.index('def weightBind('):config.index('def gpuPrefix(')]
    source = str(tmp_path / 'weights')
    call = 'weightBind(' + json.dumps(source) + ', ' + json.dumps(guest) + ')'
    script = method + '\nprintln(groovy.json.JsonOutput.toJson(' + call + '))'
    env = dict(os.environ)
    env.pop('BMS_SHARED_WEIGHTS_ROOT', None)
    if backend:
        env['BMS_CONTAINER_BACKEND'] = backend
    else:
        env.pop('BMS_CONTAINER_BACKEND', None)
    result = subprocess.run(['java', '-cp', str(_jar()), 'groovy.ui.GroovyMain', '-e', script],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    bind = json.loads(result.stdout.strip())
    invocation = parse_exec(['--bind', bind, '/fixture.sif', 'true'])
    expected = [(source, guest or source, 'ro')]
    if guest:
        expected.append((source, source, 'ro'))
    assert invocation.binds == expected


def test_actual_nextflow_config_keeps_weight_modes_and_environment_values_separate(tmp_path):
    weights = tmp_path / 'weights'
    weights.mkdir()
    env = dict(os.environ, BMS_HOME=str(REPO), BMS_WEIGHTS=str(weights),
               BMS_DATA=str(tmp_path / 'data'), XDG_CACHE_HOME=str(tmp_path / 'cache'),
               NXF_HOME=str(tmp_path / 'nextflow'), NXF_OFFLINE='true', NXF_ANSI_LOG='false')
    env.pop('BMS_SHARED_WEIGHTS_ROOT', None)
    env.pop('BMS_CONTAINER_BACKEND', None)
    result = subprocess.run(['java', '-jar', str(_jar()), '-log', str(tmp_path / 'nextflow.log'),
                             '-C', str(REPO / 'nextflow.config'), 'config', '-flat'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'{weights}/protenix:/protenix_weights:ro,{weights}/protenix:{weights}/protenix:ro' in result.stdout
    assert f'BMS_HOST_WEIGHTS={weights} ' in result.stdout
    assert f'BMS_WEIGHTS={weights} ' in result.stdout
    assert f'BMS_HOST_WEIGHTS={weights}:' not in result.stdout
    assert f'BMS_WEIGHTS={weights}:' not in result.stdout
    config = (REPO / 'nextflow.config').read_text()
    # More-specific protection follows ordinary writable code/data/cache mounts.
    for body in re.findall(r'"""(.*?)"""', config, re.S):
        if 'weightBind(' not in body:
            continue
        lines = body.splitlines()
        weight_indices = [i for i, line in enumerate(lines) if 'weightBind(' in line]
        writable_indices = [i for i, line in enumerate(lines)
                            if '--bind ${params.code_root}' in line or '--bind ${params.data_root' in line]
        assert not writable_indices or min(weight_indices) > max(writable_indices)


def _pinned_layout(tmp_path, model="protenix"):
    import hashlib
    weights = tmp_path / 'weights'
    layout = weights / 'layouts' / model / ('a' * 64)
    layout.mkdir(parents=True)
    objects = {}
    for name in ('checkpoint/model.pt', 'common/components.cif'):
        body = ('inert selected ' + name).encode()
        digest = hashlib.sha256(body).hexdigest()
        source = weights / 'objects/sha256' / digest / 'runtime.sif'
        source.parent.mkdir(parents=True)
        source.write_bytes(body)
        source.chmod(0o400)
        member = layout / name
        member.parent.mkdir(exist_ok=True)
        member.symlink_to(os.path.relpath(source, member.parent))
        objects[name] = source
    (weights / model).symlink_to(os.path.relpath(layout, weights), target_is_directory=True)
    unused = weights / 'objects/sha256' / ('f' * 64) / 'runtime.sif'
    unused.parent.mkdir()
    unused.write_bytes(b'not selected')
    return weights, layout, objects, unused


def _emitted_bind(tmp_path, source, guest):
    config = (REPO / 'nextflow.config').read_text()
    method = config[config.index('def weightBind('):config.index('def gpuPrefix(')]
    script = method + '\nprintln(groovy.json.JsonOutput.toJson(weightBind(' + json.dumps(str(source)) + ', ' + json.dumps(guest) + ')))'
    result = subprocess.run(['java', '-cp', str(_jar()), 'groovy.ui.GroovyMain', '-e', script],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize('guest', ['/protenix_weights', '/nested/native/model/weights', None])
def test_pinned_layout_projects_only_selected_referents_at_guest_depth(tmp_path, guest):
    import posixpath
    from tools.bms_container import parse_exec
    weights, layout, objects, unused = _pinned_layout(tmp_path)
    source = weights / 'protenix'
    binds = parse_exec(['--bind', _emitted_bind(tmp_path, source, guest), '/fixture.sif', 'true']).binds
    assert (str(layout), guest or str(source), 'ro') in binds
    assert (str(layout), str(source), 'ro') in binds
    assert (str(layout), str(layout), 'ro') in binds
    for native in objects.values():
        projected = posixpath.normpath(posixpath.join(guest or str(source), os.path.relpath(native, layout)))
        assert (str(native), projected, 'ro') in binds
        assert (str(native), str(native), 'ro') in binds
        assert native.stat().st_nlink == 1 and native.stat().st_mode & 0o777 == 0o400
    assert not any(str(unused) == source or source == str(weights / 'objects') for source, _, _ in binds)
    assert all(mode == 'ro' for _, _, mode in binds)


@pytest.mark.parametrize('selected', ['protenix', 'dorado'])
def test_actual_apptainer_selected_weight_aliases_readonly_with_writable_cache(tmp_path, selected):
    """Genuine kernel mounts with tiny payloads; no scientific execution."""
    image = os.environ.get('BMS_TEST_READONLY_SIF')
    runtime = shutil.which('apptainer')
    if not image or not Path(image).is_file() or runtime is None:
        pytest.skip('explicit existing readonly SIF and Apptainer required')
    weights, layout, objects, _ = _pinned_layout(tmp_path, selected)
    cache = tmp_path / 'native-cache'
    cache.mkdir()
    control = tmp_path / 'writable-control'
    control.write_bytes(b'control')
    control.chmod(0o400)
    guest = '/weights/dorado/2.1.2' if selected == 'dorado' else '/protenix_weights'
    binding = _emitted_bind(tmp_path, weights / selected, guest)
    pod5 = tmp_path / 'pod5-generation'
    pod5.mkdir()
    sentinel = pod5 / 'mount-sentinel'  # Byte visibility only, not POD5 format evidence.
    sentinel.write_bytes(objects['checkpoint/model.pt'].read_bytes())
    sentinel.chmod(0o444)
    pod5_alias = tmp_path / 'pod5-selection'
    pod5_alias.symlink_to(pod5, target_is_directory=True)
    if selected == 'dorado':
        # Evaluate the actual GPU label's options; execute only their emitted
        # mounts in this CPU fixture, not GPU/scientific/scheduling flags.
        config = (REPO / 'nextflow.config').read_text()
        label = re.search(r"withLabel:\s*['\"]?dorado_gpu['\"]?\s*\{", config)
        assert label
        template = re.search(r'containerOptions = \{ (""".*?""") \}', config[label.end():], re.S).group(1)
        methods = config[config.index('def weightBind('):config.index('def firstConfiguredGpuIds(')]
        params = dict(gpu_id='', dorado_runtime_sif=image, code_root=str(REPO),
                      pod5_dir=str(pod5_alias), dorado_model_root=str(layout), weights_root=str(weights))
        script = methods + '\ndef params = new groovy.json.JsonSlurper().parseText(' + json.dumps(json.dumps(params)) + ')\n'
        script += 'println(groovy.json.JsonOutput.toJson(' + template + '))'
        emitted = subprocess.run(['java', '-cp', str(_jar()), 'groovy.ui.GroovyMain', '-e', script],
            cwd=tmp_path, capture_output=True, text=True, timeout=60)
        assert emitted.returncode == 0, emitted.stderr
        import shlex
        from tools.bms_container import parse_exec
        binds = parse_exec([*shlex.split(json.loads(emitted.stdout)), image, 'true']).binds
        assert (str(layout), guest, 'ro') in binds
        assert (str(pod5), str(pod5), 'ro') in binds
        binding = ','.join(':'.join(item) for item in binds)
    before = {str(p): (p.read_bytes(), p.stat().st_ino, p.stat().st_mode, p.stat().st_mtime_ns)
              for p in objects.values()}
    names = [str(objects['checkpoint/model.pt']), str(layout / 'checkpoint/model.pt'),
             str(weights / selected / 'checkpoint/model.pt'), guest + '/checkpoint/model.pt']
    if selected == 'dorado':
        names.extend([str(sentinel), str(pod5_alias / sentinel.name)])
    code = '''set -euo pipefail
expected="$1"; cache="$2"; control="$3"; shift 3
# Prove the broad mount is writable despite the original source mode.
chmod 0600 "$control"
printf '%s' 'writable broad control' > "$control"
printf '%s' 'private native cache' > "$cache/generated"
for name in "$@"; do
    [[ "$(< "$name")" == "$expected" ]]
    if chmod 0600 "$name" 2>/dev/null; then
        printf 'selected alias permits chmod: %s\\n' "$name" >&2; exit 1
    fi
    if (printf '%s' 'forbidden mutation' > "$name") 2>/dev/null; then
        printf 'selected alias permits write: %s\\n' "$name" >&2; exit 1
    fi
    [[ "$(< "$name")" == "$expected" ]]
done
printf '%s\\n' ACTUAL_APPTAINER_SELECTED_ALIAS_READONLY
'''
    env = dict(os.environ, APPTAINER_CACHEDIR=str(tmp_path / 'apptainer-cache'),
               APPTAINER_TMPDIR=str(tmp_path / 'apptainer-tmp'))
    Path(env['APPTAINER_TMPDIR']).mkdir()
    result = subprocess.run(['unshare', '--user', '--map-root-user', '--net', runtime, 'exec',
        '--containall', '--no-home', '--bind', f'{tmp_path}:{tmp_path}', '--bind', binding,
        image, '/bin/bash', '-c', code, 'fixture', objects['checkpoint/model.pt'].read_text(),
        str(cache), str(control), *names], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ACTUAL_APPTAINER_SELECTED_ALIAS_READONLY' in result.stdout
    assert (cache / 'generated').read_text() == 'private native cache'
    assert control.read_bytes() == b'writable broad control'
    assert {str(p): (p.read_bytes(), p.stat().st_ino, p.stat().st_mode, p.stat().st_mtime_ns)
            for p in objects.values()} == before
