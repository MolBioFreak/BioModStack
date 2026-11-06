"""Current systemd removes CPUAccounting; actual cgroup counters remain."""
import os
from types import SimpleNamespace

import pytest
from services import resource_usage_evidence as resources


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    properties = SimpleNamespace(
        control_group='/fixture', invocation_id='invocation',
        main_pid=str(os.getpid()), cpu_accounting='', memory_accounting='yes',
        tasks_accounting='yes', cpu_quota_per_sec_usec='2s',
        memory_max='1073741824', device_policy='closed', device_allow='',
    )
    monkeypatch.setattr(resources, 'show_unit_properties', lambda *_: properties)
    monkeypatch.setattr(resources, '_current_unified_cgroup', lambda: '/fixture')
    monkeypatch.setattr(resources, '_cgroup_directory', lambda _: tmp_path)
    monkeypatch.setattr(resources, '_cgroup_pids', lambda _: {os.getpid()})
    monkeypatch.setenv('INVOCATION_ID', 'invocation')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '')
    monkeypatch.setattr(resources.threading, 'Thread', lambda **_: SimpleNamespace(start=lambda: None))
    (tmp_path / 'cpu.stat').write_text(
        'usage_usec 123\nuser_usec 100\nsystem_usec 23\nnr_periods 2\nnr_throttled 0\nthrottled_usec 0\n')
    for name, value in [('memory.current', 64), ('memory.peak', 128), ('pids.peak', 1)]:
        (tmp_path / name).write_text(str(value))
    item = resources.WorkflowResourceMonitor(
        job_id='installation-check', lane='development', generation=1, attempt=1,
        unit_name='fixture.service', owner_nonce='owner', expected_invocation_id='invocation',
        handoff={'cpu_threads': 2, 'dram_bytes': 1073741824},
        dispatch_authority={}, gpu_index=None, gpu_uuid=None,
    )
    checkpoints = []
    monkeypatch.setattr(item, '_write_checkpoint', lambda data: checkpoints.append(data))
    return item, properties, tmp_path, checkpoints


def test_missing_cpu_accounting_toggle_uses_actual_cgroup_observation(monitor):
    item, properties, _, checkpoints = monitor
    item.start()
    assert properties.cpu_accounting == ''
    assert item._enforcement['cpu_accounting'] is True
    assert item._enforcement['cpu_quota_per_sec_usec'] == 2000000
    assert item._enforcement['memory_max_bytes'] == 1073741824
    assert item._last_accounting['cpu']['usage_usec'] == 123
    assert checkpoints[0]['cpu']['system_usec'] == 23
    assert item._monitor_failures == 0


def test_explicit_disabled_cpu_accounting_keeps_existing_refusal(monitor):
    item, properties, _, checkpoints = monitor
    properties.cpu_accounting = 'no'
    with pytest.raises(resources.ResourceUsageEvidenceError, match='CPUAccounting'):
        item.start()
    assert checkpoints == []
    assert item._enforcement['cpu_accounting'] is False


def test_missing_toggle_does_not_fake_unavailable_cgroup_counters(monitor):
    item, _, root, checkpoints = monitor
    (root / 'cpu.stat').unlink()
    with pytest.raises(resources.ResourceUsageEvidenceError, match='accounting is unavailable'):
        item.start()
    assert item._enforcement['cpu_accounting'] is False
    assert checkpoints == []


def test_missing_toggle_preserves_cpu_quota_verification(monitor):
    item, properties, _, checkpoints = monitor
    properties.cpu_quota_per_sec_usec = '1s'
    with pytest.raises(resources.ResourceUsageEvidenceError, match='CPUQuotaPerSecUSec'):
        item.start()
    assert checkpoints == []
