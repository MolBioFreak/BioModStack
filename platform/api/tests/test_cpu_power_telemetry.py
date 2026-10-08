from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import gpu


class _DeniedEnergyPath:
    def __init__(self, path: str) -> None:
        self.path = path

    def read_text(self) -> str:
        raise PermissionError(self.path)

    def __str__(self) -> str:
        return self.path


def test_cpu_power_sampler_reports_unreadable_rapl_source(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu,
        "_get_rapl_package_sources",
        lambda: [
            {
                "domain_name": "package-0",
                "energy_path": _DeniedEnergyPath("/sys/class/powercap/intel-rapl:0/energy_uj"),
                "max_energy_uj": 65532610987.0,
                "readable": False,
            }
        ],
    )

    watts, status = gpu._sample_cpu_package_power()

    assert watts is None
    assert status["available"] is False
    assert status["status"] == "unreadable"
    assert status["discovered_sources"] == 1
    assert status["readable_sources"] == 0
    assert "RAPL" in status["message"]
    assert status["setup_hint"]


def test_cpu_power_sampler_prefers_configured_collector(monkeypatch) -> None:
    monkeypatch.setenv("BMS_CPU_POWER_COLLECTOR_URL", "http://127.0.0.1:8797/power")
    monkeypatch.setattr(gpu, "_sample_cpu_power_from_collector", lambda url: (142.6, {
        "source": "rapl_collector",
        "available": True,
        "status": "ok",
        "message": "CPU package power sampled by host RAPL collector.",
        "discovered_sources": 1,
        "readable_sources": 1,
        "setup_hint": None,
    }))

    watts, status = gpu._sample_cpu_package_power()

    assert watts == 142.6
    assert status["source"] == "rapl_collector"
    assert status["available"] is True
    assert status["status"] == "ok"


def test_cpu_power_sampler_falls_back_when_configured_collector_errors(monkeypatch) -> None:
    monkeypatch.setenv("BMS_CPU_POWER_COLLECTOR_URL", "http://127.0.0.1:8797/power")
    monkeypatch.setattr(gpu, "_sample_cpu_power_from_collector", lambda url: (None, {
        "source": "rapl_collector",
        "available": False,
        "status": "collector_error",
        "message": "collector unavailable",
        "discovered_sources": 0,
        "readable_sources": 0,
        "setup_hint": "collector down",
    }))
    monkeypatch.setattr(gpu, "_get_rapl_package_sources", lambda: [])

    watts, status = gpu._sample_cpu_package_power()

    assert watts is None
    assert status["source"] == "rapl"
    assert status["status"] == "no_sources"


def test_cpu_status_exposes_power_telemetry_diagnostics(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "_sample_cpu_package_power", lambda: (None, {
        "source": "rapl",
        "available": False,
        "status": "unreadable",
        "message": "RAPL energy counters are not readable by this service user",
        "discovered_sources": 1,
        "readable_sources": 0,
        "setup_hint": "Grant read access to RAPL energy_uj or run a privileged collector.",
    }))
    monkeypatch.setattr(gpu, "_read_cpu_frequency_from_sysfs", lambda: (3200.0, 5500.0))
    monkeypatch.setattr(gpu, "_read_cpu_frequency_from_proc", lambda: 3200.0)
    monkeypatch.setattr(gpu.psutil, "cpu_freq", lambda: None)
    monkeypatch.setattr(gpu.psutil, "cpu_percent", lambda interval=None, percpu=False: [0.0, 0.0] if percpu else 0.0)
    monkeypatch.setattr(gpu.psutil, "cpu_count", lambda logical=True: 2 if logical else 1)
    monkeypatch.setattr(gpu.psutil, "sensors_temperatures", lambda: {})

    cpu = gpu.get_cpu_stats()

    assert cpu.power_watts is None
    assert cpu.power_telemetry.status == "unreadable"
    assert cpu.power_telemetry.readable_sources == 0
