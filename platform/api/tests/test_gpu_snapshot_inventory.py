"""DASH-03: retain control semantics while reusing native absence discovery."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import gpu
from services import nvidia_inventory


@pytest.fixture(autouse=True)
def isolated_controls(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu, "GPU_POWER_STATE_PATH", tmp_path / "power.json")
    monkeypatch.setattr(gpu, "GPU_FAN_STATE_PATH", tmp_path / "fan.json")
    monkeypatch.setattr(gpu, "_power_control_cache", None)
    monkeypatch.setattr(gpu, "_fan_control_cache", None)
    monkeypatch.setattr(gpu, "_fan_backend_auto_cache", None)
    monkeypatch.setattr(gpu, "_current_limits", {})
    monkeypatch.setattr(gpu, "_saved_limits", {})
    monkeypatch.setattr(gpu, "_fan_profiles", {})
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {})
    monkeypatch.setattr(gpu, "_gpu_proxy_enabled", lambda: False)


@pytest.mark.parametrize("backend", ["coolercontrol", "nvidia-settings"])
def test_complete_absence_reuses_inventory_without_driver_probes(monkeypatch, backend):
    monkeypatch.setenv("BMS_FAN_CONTROL_BACKEND", backend)
    probes = []
    monkeypatch.setattr(nvidia_inventory, "nvidia_gpu_present", lambda: probes.append("inventory") or False)
    def forbidden(*args, **kwargs):
        pytest.fail(f"absent hardware spawned {args}")
    monkeypatch.setattr(gpu.subprocess, "run", forbidden)
    fan = gpu._get_fan_control_snapshot(force_refresh=True)
    power = gpu._get_power_control_payload(force_refresh=True)
    assert fan["supported"] is False
    assert fan["gpus"] == {}
    assert power["limits"] == power["hardware_limits"] == {}
    assert probes == ["inventory", "inventory"]


@pytest.mark.parametrize("presence", [True, None])
def test_present_or_unknown_keeps_full_identity_and_native_power_parser(monkeypatch, presence):
    monkeypatch.setattr(nvidia_inventory, "nvidia_gpu_present", lambda: presence)
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {2: {"min": 100, "max": 400, "default": 300}, 7: {"min": 50, "max": 200, "default": 150}})
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        text = "2, GPU-two, NVIDIA Two, 00000000:01:00.0\n7, GPU-seven, NVIDIA Seven, with comma, 00000000:02:00.0\n" if "uuid" in argv[1] else "2, 245.6\n7, 900\n99, 150\ninvalid\n"
        return SimpleNamespace(returncode=0, stdout=text)
    monkeypatch.setattr(gpu.subprocess, "run", run)
    assert gpu._query_smi_gpu_map() == {
        2: {"uuid": "GPU-two", "name": "NVIDIA Two", "pci_bus_id": "00000000:01:00.0"},
        7: {"uuid": "GPU-seven", "name": "NVIDIA Seven, with comma", "pci_bus_id": "00000000:02:00.0"},
    }
    assert gpu._read_live_power_limits() == {2: 246, 7: 200}
    assert [call[0] for call in calls] == [
        ["nvidia-smi", "--query-gpu=index,uuid,name,pci.bus_id", "--format=csv,noheader"],
        ["nvidia-smi", "--query-gpu=index,power.limit", "--format=csv,noheader,nounits"],
    ]
    assert all(call[1] == {"capture_output": True, "text": True, "timeout": 5} for call in calls)


@pytest.mark.parametrize("reader", ["_read_live_power_limits", "_query_smi_gpu_map"])
def test_inventory_is_rechecked_without_new_absence_cache(monkeypatch, reader):
    presence = [False, None, True]
    monkeypatch.setattr(nvidia_inventory, "nvidia_gpu_present", lambda: presence.pop(0))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="7, GPU-seven, NVIDIA Seven, 00000000:02:00.0\n" if "uuid" in argv[1] else "7, 180\n")
    monkeypatch.setattr(gpu.subprocess, "run", run)
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {7: {"min": 100, "max": 300, "default": 250}})
    read = getattr(gpu, reader)
    assert read() == {}
    assert set(read()) == {7}
    assert set(read()) == {7}
    assert len(calls) == 2
    assert presence == []


@pytest.mark.parametrize("reader", ["_read_live_power_limits", "_query_smi_gpu_map"])
@pytest.mark.parametrize("failure", ["exit", "exception"])
def test_unknown_inventory_preserves_driver_failure_behavior(monkeypatch, reader, failure):
    monkeypatch.setattr(nvidia_inventory, "nvidia_gpu_present", lambda: None)
    def run(*args, **kwargs):
        if failure == "exception":
            raise OSError("driver unavailable")
        return SimpleNamespace(returncode=1, stdout="not success")
    monkeypatch.setattr(gpu.subprocess, "run", run)
    assert getattr(gpu, reader)() == {}


@pytest.mark.parametrize("control,ttl", [("fan", 5.0), ("power", 2.0)])
def test_exact_existing_ttl_copy_force_and_invalidation(monkeypatch, control, ttl):
    clock = [100.0]
    monkeypatch.setattr(gpu.time, "monotonic", lambda: clock[0])
    observations = []
    if control == "fan":
        def sample():
            observations.append(clock[0])
            return {"supported": True, "gpus": {"7": {"uuid": "GPU-seven", "current_percent": len(observations)}}}
        monkeypatch.setattr(gpu, "_fan_control_snapshot", sample)
        read, invalidate = gpu._get_fan_control_snapshot, gpu._invalidate_fan_control_cache
        field = "gpus"
    else:
        def sample_power():
            observations.append(clock[0])
            gpu._current_limits[7] = len(observations)
        monkeypatch.setattr(gpu, "_refresh_power_state_from_hardware", sample_power)
        read, invalidate = gpu._get_power_control_payload, gpu._invalidate_power_control_cache
        field = "limits"
    first = read()
    first[field].clear()
    clock[0] += ttl - 0.001
    cached = read()
    assert cached[field]
    cached[field].clear()
    assert read()[field]
    assert len(observations) == 1
    clock[0] = 100 + ttl
    assert read()[field]
    assert len(observations) == 2
    read(force_refresh=True)
    assert len(observations) == 3
    invalidate()
    read()
    assert len(observations) == 4


@pytest.mark.parametrize("control", ["fan", "power"])
def test_refresh_exception_not_cached_as_success(monkeypatch, control):
    boundary = "_fan_control_snapshot" if control == "fan" else "_refresh_power_state_from_hardware"
    read = gpu._get_fan_control_snapshot if control == "fan" else gpu._get_power_control_payload
    calls = []
    def fail():
        calls.append(1)
        raise RuntimeError("provider failed")
    monkeypatch.setattr(gpu, boundary, fail)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="provider failed"):
            read()
    assert calls == [1, 1]
    assert getattr(gpu, f"_{control}_control_cache") is None


@pytest.mark.parametrize("presence", [None, False])
def test_power_mutation_retains_immediate_driver_readback_and_admission(monkeypatch, presence):
    monkeypatch.setattr(nvidia_inventory, "nvidia_gpu_present", lambda: presence)
    limits = {7: {"min": 100, "max": 300, "default": 250, "eco": 180}}
    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", limits)
    monkeypatch.setattr(gpu, "_current_limits", {7: 250})
    monkeypatch.setattr(gpu, "_saved_limits", {7: 180})
    live = [250]
    queries, writes = [], []
    def run(argv, **kwargs):
        queries.append(argv)
        return SimpleNamespace(returncode=0, stdout=f"7, {live[0]}\n")
    def write(index, watts):
        writes.append((index, watts))
        live[0] = watts
        return True
    monkeypatch.setattr(gpu.subprocess, "run", run)
    monkeypatch.setattr(gpu, "set_gpu_power_limit", write)
    assert gpu._get_power_control_payload()["limits"] == {7: 250}
    response = gpu._set_power_control_sync(gpu.PowerControlRequest(gpu_index=7, limit_watts=190))
    assert response["success"] is True
    assert response["limits"] == {7: 190}
    assert gpu._get_power_control_payload()["limits"] == {7: 190}
    assert writes == [(7, 190)]
    assert len(queries) == (0 if presence is False else 4)  # no new mutation admission gate
    with pytest.raises(HTTPException, match="Unknown GPU index"):
        gpu._set_power_control_sync(gpu.PowerControlRequest(gpu_index=99, limit_watts=190))
    assert writes == [(7, 190)]


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["fan", "power"])
async def test_proxy_read_and_mutation_keep_exact_receiving_owner(monkeypatch, control):
    monkeypatch.setattr(gpu, "_gpu_proxy_enabled", lambda: True)
    calls = []
    async def proxy(method, path, payload=None):
        calls.append((method, path, payload))
        return {"owner": "adapter", "sequence": len(calls)}
    monkeypatch.setattr(gpu, "_gpu_proxy_request_async", proxy)
    def forbidden(*args, **kwargs):
        pytest.fail("proxy request ran local discovery or control")
    monkeypatch.setattr(nvidia_inventory, "nvidia_gpu_present", forbidden)
    monkeypatch.setattr(gpu.subprocess, "run", forbidden)
    read = gpu.get_fan_control if control == "fan" else gpu.get_power_control
    assert (await read())["sequence"] == 1
    assert (await read())["sequence"] == 2
    if control == "fan":
        await gpu.set_fan_control(gpu.FanControlRequest(gpu_index=7, mode="manual", target_percent=58))
        payload = {"gpu_index": 7, "mode": "manual", "target_percent": 58}
    else:
        await gpu.set_power_control(gpu.PowerControlRequest(gpu_index=7, limit_watts=190))
        payload = {"gpu_index": 7, "limit_watts": 190}
    assert calls == [("GET", f"/{control}-control", None), ("GET", f"/{control}-control", None), ("POST", f"/{control}-control", payload)]


@pytest.mark.parametrize("backend", [gpu.FAN_BACKEND_NVIDIA_SETTINGS, gpu.FAN_BACKEND_COOLERCONTROL])
@pytest.mark.parametrize("applies", [True, False])
def test_fan_mutation_real_cache_forces_post_write_observation(monkeypatch, backend, applies):
    state = {"mode": "auto", "target_percent": 30}
    samples, writes = [], []
    def snapshot():
        samples.append(dict(state))
        return {"supported": True, "backend": backend, "gpus": {"7": {
            **state, "uuid": "GPU-seven", "writable": True, "settings_gpu_target": 3,
            "fan_targets": [4], "min_percent": 30, "max_percent": 100,
            "coolercontrol_device_uid": "device-seven", "coolercontrol_channels": ["fan1"],
        }}}
    monkeypatch.setattr(gpu, "_fan_control_snapshot", snapshot)
    def mode(target, value):
        writes.append((target, value))
        if applies:
            state["mode"] = value
        return True, "ok"
    def percent(target, value):
        writes.append((target, value))
        if applies:
            state["target_percent"] = value
        return True, "ok"
    def cooler(**kwargs):
        writes.append(kwargs)
        if applies:
            state.update(mode=kwargs["desired_mode"], target_percent=kwargs["desired_target"])
        return True, "ok"
    monkeypatch.setattr(gpu, "_apply_gpu_fan_mode", mode)
    monkeypatch.setattr(gpu, "_apply_fan_target_percent", percent)
    monkeypatch.setattr(gpu, "_apply_coolercontrol_gpu_setting", cooler)
    gpu._get_fan_control_snapshot()
    response = gpu._set_fan_control_sync(gpu.FanControlRequest(gpu_index=7, mode="manual", target_percent=58))
    assert response["success"] is applies
    assert response["fan_control"]["gpus"]["7"]["uuid"] == "GPU-seven"
    assert response["fan_control"]["gpus"]["7"]["target_percent"] == (58 if applies else 30)
    assert gpu._get_fan_control_snapshot() == response["fan_control"]
    assert len(samples) == 3  # initial cache, forced admission observation, forced readback
    assert len(writes) == (1 if backend == gpu.FAN_BACKEND_COOLERCONTROL else 2)
    assert bool(gpu._fan_profiles) is applies
