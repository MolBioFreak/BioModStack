"""Local consumer configuration transport; synthetic bytes, no model/service launch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import biomodstack_services as manager
from biomodstack_runtime_profile import get_install_profile_path
from services import ngs_alignment_sessions as service


@pytest.fixture
def installation(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('BMS_') or key == 'DATABASE_URL':
            monkeypatch.delenv(key)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    profile = {'data_root': str(tmp_path / 'data'), 'container_dir': str(tmp_path / 'images')}
    path = get_install_profile_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(profile))
    return Path(profile['container_dir'])


@pytest.mark.parametrize('override', ['profile', 'container', 'store'])
def test_ngs_store_uses_installation_transport(installation, tmp_path, monkeypatch, override):
    expected = installation / '.image-store'
    if override == 'container':
        monkeypatch.setenv('BMS_CONTAINER_DIR', str(tmp_path / 'other images'))
        expected = tmp_path / 'other images' / '.image-store'
    if override == 'store':
        expected = tmp_path / 'explicit store'
        monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(expected))
    for source in (tmp_path / 'lane-a/dorado.sif', tmp_path / 'lane-b/dorado.sif',
                   expected / 'objects/sha256/digest/runtime.sif'):
        assert service._runtime_image_store(source) == expected
    assert not expected.exists()  # Resolution never publishes or touches an image.


@pytest.mark.parametrize('mode,lane,units', [
    ('dev', 'development', [manager.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE, manager.API_SERVICE]),
    ('container', 'production', [manager.PRODUCTION_WORKFLOW_ADAPTER_SERVICE]),
])
@pytest.mark.parametrize('explicit', [False, True])
def test_rendered_lane_reference_transport(installation, tmp_path, monkeypatch, mode, lane, units, explicit):
    store = installation / '.image-store'
    if explicit:
        store = tmp_path / 'shared $literal % images'
        monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    rendered = manager.render_user_units(ROOT, runtime_mode=mode)
    for unit in units:
        text = rendered[unit]
        envfiles = [line.strip().split('=', 1)[1] for line in text.splitlines()
                    if line.strip().startswith('EnvironmentFile=')]
        decoded = [shlex.split(value)[0].replace('%%', '%') for value in envfiles]
        assert '-' + str(store / 'references' / f'{lane}.env') in decoded
        assert not any(f"/{'production' if lane == 'development' else 'development'}.env" in p for p in decoded)
        assert manager.systemd_value(f'BMS_RUNTIME_IMAGE_STORE={store}') in text
        # Feed the rendered transport directives to systemd's parser, without
        # starting/installing any service or reading a live lane reference.
        transport = '\n'.join(line.strip() for line in text.splitlines()
                              if line.strip().startswith(('Environment=', 'EnvironmentFile=')))
        probe = tmp_path / unit
        probe.write_text('[Service]\n' + transport + '\nExecStart=/bin/true\n')
        check = subprocess.run(['systemd-analyze', 'verify', str(probe)],
                               capture_output=True, text=True, timeout=30)
        assert check.returncode == 0, check.stdout + check.stderr
        assert 'Invalid' not in check.stderr and 'Failed to parse' not in check.stderr
    assert not store.exists()  # Rendering neither publishes nor promotes a lane.


def test_relative_store_fails_closed(installation, monkeypatch):
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', 'relative/store')
    with pytest.raises(service.AlignmentSessionError, match='invalid'):
        service._runtime_image_store(Path('/source/runtime.sif'))
    with pytest.raises(manager.ServiceManagerError, match='absolute'):
        manager.render_user_units(ROOT, runtime_mode='dev')


def test_nextflow_real_config_transport(tmp_path, monkeypatch):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('BMS_TEST_NEXTFLOW_JAR required for actual Nextflow config parser')
    for key in tuple(os.environ):
        if key.startswith('BMS_'):
            monkeypatch.delenv(key)
    images = tmp_path / 'images'
    monkeypatch.setenv('BMS_CONTAINER_DIR', str(images))
    monkeypatch.setenv('BMS_HOME', str(ROOT))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setenv('NXF_HOME', str(tmp_path / 'nextflow'))
    monkeypatch.setenv('NXF_OFFLINE', 'true')
    runner = tmp_path / 'config.groovy'
    runner.write_text('''
import nextflow.config.ConfigBuilder
import java.nio.file.Paths

def root = Paths.get(args[0])
def config = new ConfigBuilder().setBaseDir(root).setCurrentDir(root)
    .setHomeDir(Paths.get(args[1])).setProfile('workstation_ryzen7960x')
    .setCliParams([dorado_runtime_sif: args[2], runtime_image_store: args[3]])
    .buildConfigObject()
assert config.params.dorado_runtime_sif.toString() == args[2]
assert config.params.runtime_image_store.toString() == args[3]
['dorado_gpu', 'dorado_cpu', 'fastq_qc_cpu', 'pooled_assignment_cpu'].each { label ->
    def selected = config.process['withLabel:' + label].container
    assert selected.call().toString() == args[2]
}
['dorado_gpu', 'dorado_cpu'].each { label ->
    assert config.process['withLabel:' + label].containerOptions.call().contains(args[2] + ':/runtime/dorado.sif:ro')
}
''')
    image = tmp_path / 'store/objects/sha256/pinned/runtime.sif'
    result = subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(runner),
                             str(ROOT), str(tmp_path), str(image), str(tmp_path / 'store')],
                            cwd=tmp_path, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    # Exercise environment/default evaluation too, through the actual CLI parser.
    result = subprocess.run(['java', '-jar', jar, 'config', str(ROOT), '-profile',
                             'workstation_ryzen7960x', '-flat'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"params.runtime_image_store = '{images}/.image-store'" in result.stdout
    assert f"params.dorado_runtime_sif = '{images}/dorado.sif'" in result.stdout
    monkeypatch.setenv('BMS_NGS_RUNTIME_SIF', str(image))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'store'))
    selected = subprocess.run(['java', '-jar', jar, 'config', str(ROOT), '-profile',
                               'workstation_ryzen7960x', '-flat'], cwd=tmp_path,
                              capture_output=True, text=True, timeout=90)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert f"params.dorado_runtime_sif = '{image}'" in selected.stdout
    assert f"params.runtime_image_store = '{tmp_path}/store'" in selected.stdout


@pytest.mark.parametrize('module,process', [
    ('clone_validation.nf', 'CloneValidationAdapter'),
    ('construct_verify.nf', 'ConstructVerify'),
])
def test_ngs_module_render_has_no_nested_runtime_fallback(tmp_path, module, process):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('BMS_TEST_NEXTFLOW_JAR required for real Groovy rendering')
    source = (ROOT / 'modules/ngs' / module).read_text()
    block = source.split('process ' + process + ' {', 1)[1].split('\nprocess ', 1)[0]
    assert "label 'fastq_qc_cpu'" in block
    body = block.split('    script:', 1)[1].rsplit('}', 1)[0]
    helper = source.split('\nprocess ', 1)[0]
    bindings = {
        'params': {'code_root': str(ROOT)}, 'projectDir': str(ROOT),
        **{key: 'fixture' for key in ('reference', 'aligned_bam', 'aligned_bai',
            'verification_input', 'per_base_support', 'alignment_stats',
            'dimer_breakpoint_call', 'dimer_secondary_summary', 'result_root',
            'runtime_provenance')},
    }
    (tmp_path / 'bindings.json').write_text(json.dumps(bindings))
    script = tmp_path / 'module.groovy'
    script.write_text(helper + '\ndef rendered = {\n' + body + '\n}.call()\nprint rendered\n')
    runner = tmp_path / 'render.groovy'
    runner.write_text('''
def values = new groovy.json.JsonSlurper().parse(new File(args[0]))
new GroovyShell(new Binding(values)).evaluate(new File(args[1]))
''')
    rendered = subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(runner),
                               str(tmp_path / 'bindings.json'), str(script)],
                              capture_output=True, text=True, timeout=60)
    assert rendered.returncode == 0, rendered.stderr
    assert 'apptainer exec' not in rendered.stdout
    assert 'dorado.sif' not in rendered.stdout
    # Exercise only the actual runtime preflight, with Python available but no
    # samtools. No scientific helper or container is executed.
    prefix = rendered.stdout.split('    mkdir -p verification', 1)[0] if process == 'ConstructVerify' else rendered.stdout.split('    "${SAMTOOLS_CMD[@]}" fastq', 1)[0]
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'python3').symlink_to(sys.executable)
    result = subprocess.run(['/bin/bash', '-c', prefix], env={'PATH': str(bin_dir)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 127
    assert 'samtools missing from the selected NGS runtime' in result.stderr
