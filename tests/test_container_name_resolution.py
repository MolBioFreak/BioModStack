"""Name resolution must work inside an execution container.

udocker runs with --nosysdirs, so the container's /etc/hosts and /etc/resolv.conf
are empty placeholders. torch.distributed.run --standalone rendezvouses on
localhost, fails with `gai error: -3 Temporary failure in name resolution`, and
no rank is ever started — the GPUs stay idle while the workflow burns its budget.
"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'name_resolution_driver', Path(__file__).parents[1] / 'platform/api/tools/bms_container.py')
driver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = driver
spec.loader.exec_module(driver)


def test_container_gets_a_generated_loopback_hosts_file(tmp_path):
    bindings = driver.name_resolution_bindings(tmp_path)
    assert bindings[0][1] == '/etc/hosts'
    source = Path(bindings[0][0])
    # Generated for this execution, never the host's own hosts file.
    assert source.parent == tmp_path
    assert source.read_text() == driver.LOOPBACK_HOSTS
    lines = source.read_text().splitlines()
    assert lines[0] == '127.0.0.1 localhost'
    assert lines[1].startswith('::1 localhost')
    assert source.stat().st_mode & 0o222 == 0


def test_host_resolver_is_bound_only_when_configured(tmp_path):
    # One private directory per execution, as in production.
    resolver = tmp_path / 'resolv.conf'
    resolver.write_text('')
    empty_private = tmp_path / 'empty' / 'private'
    empty_private.mkdir(parents=True)
    assert [t for _, t in driver.name_resolution_bindings(empty_private, resolver)] == ['/etc/hosts']
    resolver.write_text('nameserver 127.0.0.53\n')
    configured_private = tmp_path / 'configured' / 'private'
    configured_private.mkdir(parents=True)
    assert [t for _, t in driver.name_resolution_bindings(configured_private, resolver)] == [
        '/etc/hosts', '/etc/resolv.conf']


def test_system_bindings_keep_kernel_interfaces_and_add_name_resolution(tmp_path):
    targets = [t for _, t in driver.host_system_bindings(tmp_path)]
    assert '/etc/hosts' in targets
    for interface in ('/proc', '/sys', '/dev'):
        if Path(interface).exists():
            assert interface in targets
