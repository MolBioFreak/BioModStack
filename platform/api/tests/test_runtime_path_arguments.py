from pathlib import Path
import shlex
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import biomodstack_services as manager
import biomodstack_runtime_profile as profile


@pytest.mark.parametrize('mode', ['dev', 'container'])
def test_unit_paths_preserve_spaces_and_literal_percent(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    for key in tuple(__import__('os').environ):
        if key.startswith('BMS_'):
            monkeypatch.delenv(key)
    root = tmp_path / 'code 20% root'
    root.mkdir()
    data = tmp_path / 'data 50% root'
    profile.save_install_profile({'data_root': str(data), 'dev_data_root': str(data / 'dev')})
    monkeypatch.setattr(manager, 'git_build_identity', lambda _: {'revision':'a'*40, 'build_id':'test', 'build_time':'test'})
    units = manager.render_user_units(root, runtime_mode=mode)
    for unit in units.values():
        for line in unit.splitlines():
            if line.startswith(('ExecStart=', 'ExecStartPre=', 'ExecStop=')):
                value = line.split('=', 1)[1]
                args = shlex.split(value.replace('%%', '%'))
                if str(root) in value.replace('%%', '%'):
                    assert any(arg == str(root) or arg.startswith(str(root) + '/') for arg in args), line
                    assert '20%%' in value, line
            if line.startswith('Environment=BMS_HOME=') or line.startswith('Environment="BMS_HOME='):
                assert shlex.split(line.split('=',1)[1].replace('%%','%')) == ['BMS_HOME=' + str(root)]


def test_tailnet_dropins_preserve_path_argv(tmp_path, monkeypatch):
    import biomodstack_tailnet as tailnet
    root = tmp_path / 'code 20% root'
    revision = 'a' * 40
    monkeypatch.setattr(tailnet, '_git_revision', lambda _: revision)
    monkeypatch.setattr(tailnet, 'git_build_identity', lambda _: {'revision': revision, 'build_id':'test', 'build_time':'test'})
    monkeypatch.setattr(tailnet, '_tailnet_owner_login', lambda: 'operator@example.org')
    monkeypatch.setattr(tailnet, '_host_user_systemd_dir', lambda: tmp_path / 'units')
    monkeypatch.setattr(tailnet, 'daemon_reload', lambda **_: None)
    monkeypatch.setattr(tailnet, 'render_user_units', lambda **_: {manager.FRONTEND_SERVICE:
        'Environment=' + manager.systemd_value('BMS_HOME=' + str(root)) + '\nEnvironment=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:18002\nEnvironment=VITE_BMS_BUILD_SHA=' + revision + '\nEnvironment=VITE_BMS_BUILD_ID=test\nEnvironment=VITE_BMS_BUILD_TIME=test\n'})
    monkeypatch.setattr(tailnet, 'runtime_api_port', lambda *a, **kw: 18002)
    captured = []
    monkeypatch.setattr(tailnet, '_atomic_write', lambda path, content: captured.append(content))
    tailnet._install_operator_development_frontend(root)
    tailnet._install_adapter_control_policy(root, revision)
    for content in captured:
        for line in content.splitlines():
            if line.startswith(('ExecStart=', 'ExecStartPre=')) and str(root) in line.replace('%%', '%'):
                assert any(arg.startswith(str(root) + '/') for arg in shlex.split(line.split('=', 1)[1].replace('%%', '%'))), line
                assert '20%%' in line


def test_desktop_path_argument_identity():
    path = '/tmp/code 20% root/biomodstack_panel.py'
    assert hasattr(manager, 'desktop_exec_arg')
    assert shlex.split(manager.desktop_exec_arg(path).replace('%%', '%')) == [path]
