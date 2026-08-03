from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "config" / "shape_blueprint" / "rfd3_profiles.json"
RUNTIME_PATH = ROOT / "scripts" / "shape_blueprint" / "run_shape_rfd3.py"


def _load_runtime_module():
    assert RUNTIME_PATH.is_file(), "RFD3 runtime wrapper must exist"
    spec = importlib.util.spec_from_file_location("shape_rfd3_runtime_under_test", RUNTIME_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_registry_has_only_truthful_active_rfd3_profiles() -> None:
    assert REGISTRY_PATH.is_file(), "canonical RFD3 profile registry is required"
    registry = json.loads(REGISTRY_PATH.read_text())
    assert registry["schema"] == "bms_rfd3_profile_registry_v1"
    profiles = registry["profiles"]
    assert set(profiles) == {
        "rfd3_unguided_control_v1",
        "rfd3_ca_shape_transfer_control_v1",
        "rfd3_ca_shape_validity_v1",
    }
    assert "paper_like_rfd3_v1" not in profiles
    assert profiles["rfd3_ca_shape_transfer_control_v1"]["source_generator"] == "classic_rf_diffusion"
    assert profiles["rfd3_ca_shape_validity_v1"]["status"] == "calibration_only"


def test_runtime_loads_the_same_registry_and_rejects_retired_profile() -> None:
    runtime = _load_runtime_module()
    assert hasattr(runtime, "PROFILE_REGISTRY_SHA256"), "runtime must bind the canonical profile registry"
    assert hasattr(runtime, "_profile_for_request"), "runtime must resolve profiles through the registry"
    registry = json.loads(REGISTRY_PATH.read_text())
    expected_registry_sha256 = hashlib.sha256(
        json.dumps(registry, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert runtime.PROFILE_REGISTRY_SHA256 == expected_registry_sha256
    assert runtime._profile_for_request({
        "guidance_profile": "rfd3_ca_shape_transfer_control_v1",
        "guidance_profile_registry_sha256": expected_registry_sha256,
    })["id"] == "rfd3_ca_shape_transfer_control_v1"
    with pytest.raises(ValueError, match="unrecognized|retired|profile"):
        runtime._profile_for_request({
            "guidance_profile": "paper_like_rfd3_v1",
            "guidance_profile_registry_sha256": expected_registry_sha256,
        })


def test_runtime_requires_registry_binding_for_active_profile() -> None:
    runtime = _load_runtime_module()
    assert hasattr(runtime, "validate_request_v2"), "runtime must validate the request-v2 contract"
    with pytest.raises(ValueError, match="registry"):
        runtime.validate_request_v2(
            {
                "schema": "bms_shape_design_request_v2",
                "request_sha256": "0" * 64,
                "generator": "rfd3",
                "guidance_profile": "rfd3_ca_shape_transfer_control_v1",
                "guidance_profile_registry_sha256": "0" * 64,
                "length_policy": {"mode": "fixed", "min": 120, "max": 120},
                "num_backbones": 1,
                "seed": 0,
            },
            {},
        )
