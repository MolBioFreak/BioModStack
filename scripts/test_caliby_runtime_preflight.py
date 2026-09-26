from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from caliby_runtime import preflight_caliby_runtime


def _clear_caliby_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MODEL_PARAMS_DIR",
        "HF_HOME",
        "XDG_CACHE_HOME",
        "TRITON_CACHE_DIR",
        "CALIBY_ALLOW_DOWNLOAD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_preflight_fails_before_import_when_model_params_dir_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_caliby_env(monkeypatch)

    with pytest.raises(RuntimeError, match="MODEL_PARAMS_DIR"):
        preflight_caliby_runtime(task="sequence_design", model_name="soluble_caliby_v1")

    assert "caliby" not in sys.modules


def test_preflight_accepts_installed_soluble_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_caliby_env(monkeypatch)
    model_root = tmp_path / "model_params"
    checkpoint = model_root / "caliby" / "soluble_caliby_v1.ckpt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"test checkpoint")
    monkeypatch.setenv("MODEL_PARAMS_DIR", str(model_root))

    result = preflight_caliby_runtime(task="sequence_design", model_name="soluble_caliby_v1")

    assert result["checkpoint_path"] == str(checkpoint.resolve())
    assert result["allow_download"] is False


def test_preflight_rejects_missing_packer_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_caliby_env(monkeypatch)
    model_root = tmp_path / "model_params"
    soluble = model_root / "caliby" / "soluble_caliby_v1.ckpt"
    soluble.parent.mkdir(parents=True)
    soluble.write_bytes(b"test checkpoint")
    monkeypatch.setenv("MODEL_PARAMS_DIR", str(model_root))

    with pytest.raises(RuntimeError, match="caliby_packer_010.ckpt"):
        preflight_caliby_runtime(
            task="sidechain_pack",
            model_name="soluble_caliby_v1",
            packer_model_name="caliby_packer_010",
        )


def test_preflight_rejects_cache_environment_pointing_to_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_caliby_env(monkeypatch)
    model_root = tmp_path / "model_params"
    checkpoint = model_root / "caliby" / "soluble_caliby_v1.ckpt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"test checkpoint")
    monkeypatch.setenv("MODEL_PARAMS_DIR", str(model_root))
    invalid_cache = tmp_path / "not-a-directory"
    invalid_cache.write_text("x", encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(invalid_cache))

    with pytest.raises(RuntimeError, match="HF_HOME"):
        preflight_caliby_runtime(task="sequence_design", model_name="soluble_caliby_v1")


def test_all_caliby_entrypoints_call_preflight_before_any_caliby_import_or_model_load() -> None:
    for script_name in ("run_caliby_sequence_design.py", "run_caliby_experimental.py"):
        source = (SCRIPTS_ROOT / script_name).read_text(encoding="utf-8")
        assert "preflight_caliby_runtime(" in source
        assert source.index("preflight_caliby_runtime(") < source.index("load_caliby_model(")
        if "maybe_clean_inputs(" in source:
            assert source.index("preflight_caliby_runtime(") < source.index("maybe_clean_inputs(")
        if "from caliby." in source:
            assert source.index("preflight_caliby_runtime(") < source.index("from caliby.")


def test_recipe_pins_installed_native_runtime_and_existing_bind_transport() -> None:
    root = SCRIPTS_ROOT.parent
    recipe = (root / "apptainer/caliby.def").read_text()
    assert "git checkout --detach ddb368cbe239ddc2bd731a963f10ffe81452bb1f" in recipe
    assert "uv/0.11.7/install.sh" in recipe
    lock = recipe.split("<<'CALIBY_RUNTIME_LOCK'\n", 1)[1].split("\nCALIBY_RUNTIME_LOCK", 1)[0]
    assert "torch==2.8.0+cu128" in lock
    assert "gemmi==0.7.5" in lock
    assert "atomworks-caliby.git@ea2c998a593af05a47dd972ad6e72b89f87d4e14" in lock
    assert "protpardelle-1c.git@7962da091a335251fa8e5ddef5d2c937fbd9d9ae" in lock
    assert all("==" in line or " @ git+" in line or line == "-e file:///opt/caliby"
               for line in lock.splitlines())
    assert "uv pip install --no-deps -r /opt/caliby/runtime.lock" in recipe
    config = (root / "nextflow.config").read_text().split("withLabel: Caliby {", 1)[1].split("withLabel:", 1)[0]
    assert '--env MODEL_PARAMS_DIR=/weights/caliby/model_params' in config
    assert '--bind ${params.cache_root}:/cache' in config
    assert 'weightBind(params.weights_root, "/weights")' in config
    for name in ("HF_HOME=/cache/huggingface", "XDG_CACHE_HOME=/cache/general", "TRITON_CACHE_DIR=/cache/triton"):
        assert name in config
