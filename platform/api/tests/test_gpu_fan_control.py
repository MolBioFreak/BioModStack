from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import gpu


def test_run_nvidia_settings_assign_rejects_permission_denied(monkeypatch) -> None:
    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="",
            stderr=(
                "ERROR: The current user does not have permission for operation\n"
                "ERROR: Operation not permitted for the current user.\n"
            ),
        )

    monkeypatch.setattr(gpu.subprocess, "run", _fake_run)

    ok, output = gpu._run_nvidia_settings_assign(["-a", "[gpu:1]/GPUFanControlState=1"])

    assert ok is False
    assert "permission" in output.lower()


def test_fan_control_backend_auto_prefers_coolercontrol(monkeypatch) -> None:
    monkeypatch.delenv("BMS_FAN_CONTROL_BACKEND", raising=False)
    monkeypatch.setattr(gpu, "_fan_backend_auto_cache", None)
    monkeypatch.setattr(gpu, "_fan_backend_auto_cache_time", 0.0)
    monkeypatch.setattr(gpu, "_coolercontrol_login_cookie", lambda force_refresh=False: ("cc=session", ""))

    assert gpu._fan_control_backend() == gpu.FAN_BACKEND_COOLERCONTROL


def test_set_fan_control_requires_post_write_verification(monkeypatch) -> None:
    request = gpu.FanControlRequest(gpu_index=0, mode="manual", target_percent=55)
    snapshots = iter(
        [
            {
                "supported": True,
                "backend": gpu.FAN_BACKEND_NVIDIA_SETTINGS,
                "gpus": {
                    "0": {
                        "settings_gpu_target": 1,
                        "fan_targets": [1],
                        "profile_mode": "auto",
                        "mode": "auto",
                        "target_percent": 30,
                        "min_percent": 30,
                        "max_percent": 100,
                    }
                },
            },
            {
                "supported": True,
                "backend": gpu.FAN_BACKEND_NVIDIA_SETTINGS,
                "gpus": {
                    "0": {
                        "settings_gpu_target": 1,
                        "fan_targets": [1],
                        "profile_mode": "auto",
                        "mode": "auto",
                        "target_percent": 30,
                        "min_percent": 30,
                        "max_percent": 100,
                    }
                },
            },
        ]
    )

    monkeypatch.setattr(gpu, "HARDWARE_LIMITS", {0: {"min": 0, "max": 0, "default": 0}})
    monkeypatch.setattr(gpu, "_get_fan_control_snapshot", lambda force_refresh=False: next(snapshots))
    monkeypatch.setattr(gpu, "_apply_gpu_fan_mode", lambda *_args, **_kwargs: (True, "ok"))
    monkeypatch.setattr(gpu, "_apply_fan_target_percent", lambda *_args, **_kwargs: (True, "ok"))
    monkeypatch.setattr(gpu, "_invalidate_fan_control_cache", lambda: None)
    monkeypatch.setattr(gpu, "_save_fan_state", lambda: None)
    monkeypatch.setattr(gpu, "_fan_profiles", {})

    response = gpu._set_fan_control_sync(request)

    assert response["success"] is False
    assert "verification failed" in response["message"].lower()
