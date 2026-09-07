from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from services import nvidia_inventory
from routers import gpu


def pci_device(root, name, vendor, device_class):
    device = root / name
    device.mkdir(parents=True)
    (device / 'vendor').write_text(vendor)
    (device / 'class').write_text(device_class)


@pytest.mark.parametrize('vendor,device_class,expected', [
    ('0x1002', '0x030000', False),
    ('0x10de', '0x030000', True),
    ('0x10de', '0x030200', True),
    ('0x10de', '0x120000', True),
    ('0x10de', '0x040300', False),
])
def test_complete_inventory(tmp_path, vendor, device_class, expected):
    pci = tmp_path / 'pci'; dev = tmp_path / 'dev'; dev.mkdir()
    pci_device(pci, '0', vendor, device_class)
    assert nvidia_inventory.nvidia_gpu_present(pci, dev) is expected


@pytest.mark.parametrize('case', ['missing', 'empty', 'unreadable', 'device_node'])
def test_unknown_inventory_never_reports_absence(tmp_path, case):
    pci = tmp_path / 'pci'; dev = tmp_path / 'dev'; dev.mkdir()
    if case != 'missing':
        pci.mkdir()
    if case in ('unreadable', 'device_node'):
        pci_device(pci, '0', '0x1002', '0x030000')
    if case == 'unreadable':
        (pci / '0' / 'vendor').unlink()
    if case == 'device_node':
        (dev / 'nvidia0').touch()
    assert nvidia_inventory.nvidia_gpu_present(pci, dev) is None


def test_absence_skips_nvml_and_smi(monkeypatch):
    monkeypatch.setattr(nvidia_inventory, 'nvidia_gpu_present', lambda: False)
    def forbidden(*args, **kwargs):
        raise AssertionError('Driver probe must not run on confirmed absent hardware')
    monkeypatch.setitem(sys.modules, 'pynvml', SimpleNamespace(nvmlInit=forbidden))
    monkeypatch.setattr(gpu.subprocess, 'run', forbidden)
    assert gpu._collect_gpu_stats() == ([], None)


@pytest.mark.parametrize('presence', [True, None])
def test_present_or_unknown_preserves_driver_error(monkeypatch, presence):
    monkeypatch.setattr(nvidia_inventory, 'nvidia_gpu_present', lambda: presence)
    def failing():
        raise RuntimeError('fixture NVML driver failure')
    monkeypatch.setitem(sys.modules, 'pynvml', SimpleNamespace(nvmlInit=failing))
    calls = []
    def smi(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout='', stderr='fixture driver unavailable')
    monkeypatch.setattr(gpu.subprocess, 'run', smi)
    assert gpu._collect_gpu_stats() == ([], 'fixture driver unavailable')
    assert len(calls) == 1 and calls[0][0] == 'nvidia-smi'
