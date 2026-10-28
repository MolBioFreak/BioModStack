from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from services import ont_ngs_contract

REPO_ROOT = Path(__file__).resolve().parents[3]


def _preflight_module():
    spec = importlib.util.spec_from_file_location("dorado_p4_preflight_cpu", REPO_ROOT / "scripts/dorado_p4_preflight.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_installed_device_defaults_to_cuda_and_accepts_cpu(monkeypatch) -> None:
    monkeypatch.delenv(ont_ngs_contract.DORADO_DEVICE_ENV, raising=False)
    assert ont_ngs_contract.installed_dorado_device() == "cuda:0"
    monkeypatch.setenv(ont_ngs_contract.DORADO_DEVICE_ENV, "cpu")
    assert ont_ngs_contract.installed_dorado_device() == "cpu"
    monkeypatch.setenv(ont_ngs_contract.DORADO_DEVICE_ENV, "metal")
    with pytest.raises(ValueError):
        ont_ngs_contract.installed_dorado_device()


def test_preflight_device_pattern_accepts_cpu_and_cuda_only() -> None:
    pattern = _preflight_module().DEVICE
    for ok in ("cpu", "cuda:0", "cuda:all", "cuda:0,1"):
        assert pattern.fullmatch(ok)
    for bad in ("auto", "cuda", "gpu", "cpu,cuda:0"):
        assert not pattern.fullmatch(bad)


def test_basecall_module_skips_gpu_index_checks_for_cpu() -> None:
    text = (REPO_ROOT / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    assert '"\\${device}" == cpu ||' in text
    assert 'if [[ "\\${device}" != cpu && "\\${device_indices}" != all ]]' in text


def test_operator_device_choice_is_kept_and_validated(monkeypatch) -> None:
    from services.ont_submission_trust import ONT_SERVER_CONTROLLED_RUNTIME_PARAMS
    assert "dorado_device" not in ONT_SERVER_CONTROLLED_RUNTIME_PARAMS
    monkeypatch.setenv(ont_ngs_contract.DORADO_DEVICE_ENV, "cpu")
    base = {"pod5_dir": "/x", "dorado_quality_mode": "fast"}
    assert ont_ngs_contract.normalize_ont_launch_params("ont_basecall_dna", dict(base))["dorado_device"] == "cpu"
    chosen = ont_ngs_contract.normalize_ont_launch_params("ont_basecall_dna", dict(base, dorado_device="cuda:0"))
    assert chosen["dorado_device"] == "cuda:0"
    with pytest.raises(ValueError):
        ont_ngs_contract.normalize_ont_launch_params("ont_basecall_dna", dict(base, dorado_device="gpu"))


def test_device_options_offer_cuda_only_when_present(monkeypatch) -> None:
    import shutil
    monkeypatch.setenv(ont_ngs_contract.DORADO_DEVICE_ENV, "cpu")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(ont_ngs_contract.Path, "exists", lambda self: False)
    cpu_only = ont_ngs_contract.basecall_device_options()
    assert cpu_only["default"] == "cpu" and [o["value"] for o in cpu_only["options"]] == ["cpu"]
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    values = [o["value"] for o in ont_ngs_contract.basecall_device_options()["options"]]
    assert values == ["cpu", "cuda:0", "cuda:all"]
