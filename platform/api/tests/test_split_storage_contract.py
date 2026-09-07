import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import biomodstack_runtime_profile as profile

ROOT = Path(__file__).resolve().parents[3]

@pytest.fixture
def split(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    values = {key: str(tmp_path / ('separate ' + key + ' 20%')) for key in
              ('data_root', 'inputs_dir', 'results_dir', 'weights_root', 'colabfold_db',
               'msa_cache_dir', 'sabdab_cache_dir', 'work_dir', 'analysis_cache_dir', 'container_dir')}
    values['db_path'] = str(tmp_path / 'database 20%' / 'custom.db')
    return values


@pytest.mark.runtime_integration
def test_core_launcher_environment_ignores_development_storage(split, monkeypatch):
    profile.save_install_profile(split)
    Path(split['data_root']).mkdir()
    env = dict(os.environ, BMS_DATA='/wrong/dev', BMS_DB_PATH='/wrong/dev.sqlite',
               BMS_STATE_DIR='/wrong/dev', BMS_WORKFLOW_ADAPTER_LANE='production', BMS_HOME=str(ROOT))
    # Exercise the actual bootstrap before the root-owned hardware GID gate.
    # Full wrapper config is intentionally blocked on this host by that gate;
    # actual offline Compose resolution is exercised independently below.
    bootstrap = (ROOT / 'scripts/run_biomodstack_core_runtime.sh').read_text().split('\nload_root_owned_mk1d_recovery_gid\n')[0]
    result = subprocess.run(['bash', '-c', bootstrap + "\n/usr/bin/env -0"],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    exported = dict(item.split('=', 1) for item in result.stdout.split('\0') if '=' in item)
    assert exported['BMS_DB_PATH'] == split['db_path']
    assert exported['BMS_STATE_DIR'] == split['data_root']
    assert not Path(split['work_dir']).exists()


@pytest.mark.runtime_integration
@pytest.mark.parametrize("custom_results", [None, "/srv/separate-results"])
def test_split_profile_exports_compose_mounts_and_adapter_identity(split, custom_results):
    profile.save_install_profile(split)
    assert profile.load_install_profile()['work_dir'] == split['work_dir']
    env = {k: v for k, v in os.environ.items() if not k.startswith('BMS_')}
    if custom_results:
        env['BMS_RESULTS_CONTAINER_PATH'] = custom_results
    rendered = subprocess.run(['docker', 'compose', '--env-file', str(profile.get_core_runtime_env_path()),
                               '-f', str(ROOT / 'compose.core-runtime.yml'), 'config', '--format', 'json'],
                              env=env, capture_output=True, text=True, check=True)
    api = json.loads(rendered.stdout)['services']['bms-api']
    mounts = api['volumes']
    resolved = profile.resolve_runtime_paths(profile=split, environ={'BMS_RESULTS_CONTAINER_PATH': custom_results} if custom_results else {})
    expected = profile.core_runtime_storage_mounts(resolved)
    assert {(m['source'], m['target']) for m in mounts} == {(m['source'], m['target']) for m in expected}
    assert all(m.get('bind', {}).get('create_host_path', False) is False for m in mounts)
    import yaml
    declared = yaml.safe_load((ROOT / 'compose.core-runtime.yml').read_text())
    assert all(m['bind']['create_host_path'] is False for m in declared['services']['bms-api']['volumes'])
    assert api['environment']['BMS_LOCAL_CPU_THREADS']
    for key, var in [('inputs_dir','BMS_INPUTS'), ('results_dir','BMS_RESULTS_DIR'),
                     ('weights_root','BMS_WEIGHTS'), ('work_dir','BMS_WORK'),
                     ('analysis_cache_dir','BMS_ANALYSIS_CACHE')]:
        target = api['environment'][var]
        assert any(m['source'] == split[key] and m['target'] == target for m in mounts)
    from services import workflow_adapter
    old = dict(os.environ)
    try:
        os.environ.update(api['environment'])
        assert workflow_adapter._container_to_host_path(api['environment']['BMS_INPUTS'] + '/sample.fa') == split['inputs_dir'] + '/sample.fa'
        assert workflow_adapter._container_to_host_path(api['environment']['BMS_RESULTS_DIR'] + '/job/out.pdb') == split['results_dir'] + '/job/out.pdb'
        assert workflow_adapter._container_to_host_path(api['environment']['BMS_DB_PATH']) == split['db_path']
        import paths
        internal_result = Path(api['environment']['BMS_RESULTS_DIR']) / 'job/out.pdb'
        # No host or container filesystem mutation: mapping must be lexical and
        # independent of the file existing in this test process.
        assert paths.resolve_runtime_data_path(split['results_dir'] + '/job/out.pdb') == internal_result
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert not Path(split['work_dir']).exists()


@pytest.mark.parametrize("unit", ["biomodstack-api.service", "biomodstack-development-workflow-adapter.service"])
def test_development_analysis_cache_ignores_production_setting(split, tmp_path, monkeypatch, unit):
    import shlex
    import biomodstack_services as manager
    import paths
    split["dev_data_root"] = str(tmp_path / "dev data 20%")
    profile.save_install_profile(split)
    rendered = manager.render_user_units(ROOT, runtime_mode="dev")[unit]
    for line in rendered.splitlines():
        if line.strip().startswith("Environment="):
            for item in shlex.split(line.strip().split("=", 1)[1]):
                key, value = item.split("=", 1)
                monkeypatch.setenv(key, value.replace("%%", "%"))
    assert paths.get_analysis_cache_dir() == Path(split["dev_data_root"]) / "analysis_cache"
    assert not Path(split["analysis_cache_dir"]).exists()


def test_split_sources_fail_closed_without_creation(split):
    resolved = profile.resolve_runtime_paths(profile=split, environ={})
    with pytest.raises(ValueError, match='directory'):
        profile.validate_core_runtime_storage(resolved)
    assert not Path(split['data_root']).exists()
