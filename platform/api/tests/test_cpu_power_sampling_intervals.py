from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from routers import gpu
from tools import cpu_power_collector as collector


@pytest.fixture(params=["direct", "collector"])
def sampler(request, monkeypatch, tmp_path):
    energy = tmp_path / "energy_uj"
    clock = [100.0]
    sources = [{"domain_name": "package-0", "energy_path": energy,
                "max_energy_uj": 1_000_000_000.0, "readable": True}]
    state = {}
    module = gpu if request.param == "direct" else collector
    monkeypatch.delenv("BMS_CPU_POWER_COLLECTOR_URL", raising=False)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    if request.param == "direct":
        monkeypatch.setattr(gpu, "_rapl_sample_state", state)
        monkeypatch.setattr(gpu, "_get_rapl_package_sources", lambda: sources)
        read = lambda: gpu._sample_cpu_package_power()[0]
    else:
        monkeypatch.setattr(collector, "_sample_state", state)
        monkeypatch.setattr(collector, "_discover_sources", lambda: sources)
        read = lambda: collector._sample_once(sources)[0]

    def point(seconds, joules):
        clock[0] = 100.0 + seconds
        energy.write_text(str(joules * 1_000_000))

    return SimpleNamespace(kind=request.param, point=point, read=read, state=state,
                           key=str(energy), energy=energy, sources=sources)


def test_fast_initial_reads_do_not_keep_repriming_the_baseline(sampler):
    for seconds in [0, 0.003, 0.006, 0.009]:
        sampler.point(seconds, seconds * 100)
        assert sampler.read() is None
        assert sampler.state[sampler.key]["time_s"] == 100.0
    sampler.point(0.012, 1.2)
    assert sampler.read() == pytest.approx(100)


def test_fast_reads_reuse_only_the_current_interval_and_next_delta_is_complete(sampler):
    sampler.point(0, 0)
    assert sampler.read() is None
    sampler.point(1, 100)
    assert sampler.read() == pytest.approx(100)
    for seconds in [1.001, 1.004, 1.008]:
        sampler.point(seconds, 100 + (seconds - 1) * 200)
        assert sampler.read() == pytest.approx(100)
        assert sampler.state[sampler.key]["time_s"] == 101.0
    sampler.point(1.02, 104)
    assert sampler.read() == pytest.approx(200)
    assert sampler.state[sampler.key]["time_s"] == 101.02


def test_concurrent_readers_do_not_destroy_a_valid_sample(sampler):
    sampler.point(0, 0)
    assert sampler.read() is None
    sampler.point(1, 100)
    assert sampler.read() == pytest.approx(100)
    sampler.point(1.005, 100.5)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: sampler.read(), range(32)))
    assert values == pytest.approx([100] * 32)
    assert sampler.state[sampler.key]["time_s"] == 101.0


@pytest.mark.parametrize("failure", ["missing", "malformed", "permission"])
def test_failed_reads_do_not_hide_behind_last_valid_watts(sampler, monkeypatch, failure):
    sampler.point(0, 0)
    sampler.read()
    sampler.point(1, 100)
    assert sampler.read() == pytest.approx(100)
    sampler.point(1.005, 100.5)
    if failure == "missing":
        sampler.energy.unlink()
    elif failure == "malformed":
        sampler.energy.write_text("not an energy counter")
    else:
        original = type(sampler.energy).read_text

        def denied(path, *args, **kwargs):
            if path == sampler.energy:
                raise PermissionError(str(path))
            return original(path, *args, **kwargs)

        monkeypatch.setattr(type(sampler.energy), "read_text", denied)
    assert sampler.read() is None


def test_zero_power_and_counter_wrap_remain_real_measurements(sampler):
    sampler.point(0, 900)
    assert sampler.read() is None
    sampler.point(1, 50)  # 1000 J counter range: 150 J elapsed.
    assert sampler.read() == pytest.approx(150)
    sampler.point(2, 50)
    assert sampler.read() == 0
    sampler.point(2.005, 50)
    assert sampler.read() == 0


def test_a_new_sampler_generation_still_requires_a_real_energy_delta(sampler):
    sampler.point(0, 0)
    sampler.read()
    sampler.point(1, 100)
    assert sampler.read() == pytest.approx(100)
    sampler.state.clear()
    sampler.point(2, 200)
    assert sampler.read() is None


def test_public_collector_serves_fast_valid_reads_without_priming_sleep(monkeypatch, tmp_path):
    energy = tmp_path / "energy_uj"
    energy.write_text("100500000")
    monkeypatch.setattr(collector, "_discover_sources", lambda: [{"energy_path": energy}])
    monkeypatch.setattr(collector, "_sample_state", {str(energy): {"energy_uj": 100_000_000.0, "time_s": 101.0, "power_watts": 100.0}})
    # No sleep API: a wrongly restarted priming branch fails this test.
    monkeypatch.setattr(collector, "time", SimpleNamespace(monotonic=lambda: 101.005))
    assert collector.sample_power() == {
        "source": "rapl_collector", "available": True, "status": "ok",
        "message": "CPU package power sampled by host RAPL collector.",
        "discovered_sources": 1, "readable_sources": 1, "setup_hint": None,
        "power_watts": 100.0,
    }


def test_cpu_and_status_routes_share_a_valid_rapl_interval(monkeypatch, tmp_path):
    energy = tmp_path / "energy_uj"
    energy.write_text("100500000")
    monkeypatch.delenv("BMS_CPU_POWER_COLLECTOR_URL", raising=False)
    monkeypatch.setattr(gpu, "_gpu_proxy_enabled", lambda: False)
    monkeypatch.setattr(gpu, "_get_rapl_package_sources", lambda: [{"energy_path": energy, "readable": True}])
    monkeypatch.setattr(gpu, "_rapl_sample_state", {str(energy): {"energy_uj": 100_000_000.0, "time_s": 101.0, "power_watts": 100.0}})
    monkeypatch.setattr(gpu, "time", SimpleNamespace(monotonic=lambda: 101.005))
    monkeypatch.setattr(gpu, "_read_cpu_frequency_from_sysfs", lambda: (3200.0, 5500.0))
    monkeypatch.setattr(gpu, "_read_cpu_frequency_from_proc", lambda: 3200.0)
    monkeypatch.setattr(gpu.psutil, "cpu_freq", lambda: None)
    monkeypatch.setattr(gpu.psutil, "cpu_percent", lambda **kwargs: [1.0, 2.0])
    monkeypatch.setattr(gpu.psutil, "cpu_count", lambda **kwargs: 2)
    monkeypatch.setattr(gpu.psutil, "sensors_temperatures", lambda: {})
    monkeypatch.setattr(gpu, "get_gpu_stats_with_error", lambda: ([], None))
    monkeypatch.setattr(gpu, "get_ram_stats", lambda: gpu.RAMStatus(total_gb=32, used_gb=8, available_gb=24, utilization=25, swap_total_gb=0, swap_used_gb=0, swap_percent=0))
    app = FastAPI()
    app.include_router(gpu.router, prefix="/gpu")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            responses = await asyncio.gather(*(client.get("/gpu/cpu" if index % 2 else "/gpu/status") for index in range(16)))
        for response in responses:
            assert response.status_code == 200
            cpu = response.json()["cpu"]
            assert cpu["power_watts"] == 100.0
            assert cpu["power_telemetry"]["status"] == "ok"

    asyncio.run(exercise())
