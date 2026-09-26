"""Packaging contract only: no native sampling or GPU execution."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("disco_wrapper_packaging", ROOT / "scripts/run_disco_inference.py")
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


@pytest.mark.parametrize("effort,attention,seeds", [("fast", False, None), ("max", True, [4, 9])])
@pytest.mark.parametrize("cache_mode", ["unset", "nextflow", "explicit"])
def test_writable_cwd_selected_cache_native_fallback_and_unchanged_options(tmp_path, monkeypatch, effort, attention, seeds, cache_mode):
    source = tmp_path / "native"
    source.mkdir()
    checkpoint = tmp_path / "selected" / "DISCO.pt"
    inputs = tmp_path / "inputs.json"
    inputs.write_text("[]")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"job_name": "packaging", "task": "unconditional", "disco": {
        "input_json_path": str(inputs), "checkpoint_path": str(checkpoint),
        "effort": effort, "use_deepspeed_evo_attention": attention, "seeds": seeds,
    }}))
    output = tmp_path / "output"
    monkeypatch.setenv("DISCO_REPO", str(source))
    monkeypatch.setenv("HF_HOME", "/unrelated-user-cache")
    shared = tmp_path / "shared"
    supplied = {}
    for key in ("XDG_CACHE_HOME", "TRITON_CACHE_DIR", "TORCH_EXTENSIONS_DIR"):
        monkeypatch.delenv(key, raising=False)
        if cache_mode == "explicit" or (cache_mode == "nextflow" and key != "TORCH_EXTENSIONS_DIR"):
            supplied[key] = str(shared / key.lower())
            monkeypatch.setenv(key, supplied[key])
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        if attention:
            monkeypatch.setenv(key, "1")  # Explicit qualification policy is preserved.
        else:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(wrapper, "bind_native_document", lambda doc, *args, **kwargs: doc)
    monkeypatch.setattr(sys, "argv", ["wrapper", "--request", str(request), "--input-dir", str(tmp_path), "--output-dir", str(output)])
    seen = []

    def run(cmd, **kwargs):
        seen.append(cmd)
        assert cmd[1] == str(source / "runner/inference.py")
        assert f"effort={effort}" in cmd
        assert "experiment=designable" in cmd
        assert f"use_deepspeed_evo_attention={str(attention).lower()}" in cmd
        assert f"load_checkpoint_path={checkpoint}" in cmd
        assert ("seeds=[4,9]" if seeds else "num_inference_seeds=8") in cmd
        assert kwargs["cwd"] == output / "disco_work"
        (kwargs["cwd"] / "native-write-probe").write_text("ok")
        env = kwargs["env"]
        assert env["HF_HUB_CACHE"] == str(checkpoint.parent / "huggingface/hub")
        for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            assert env.get(key) == ("1" if attention else None)
        for key in ("XDG_CACHE_HOME", "TRITON_CACHE_DIR", "TORCH_EXTENSIONS_DIR"):
            assert Path(env[key]).is_dir()
            assert Path(env[key]).is_relative_to(output if cache_mode == "unset" else shared)
            if key in supplied:
                assert env[key] == supplied[key]

    monkeypatch.setattr(wrapper.subprocess, "run", run)
    wrapper.main()
    assert len(seen) == 1
    assert inputs.read_text() == "[]"
    assert not list(source.iterdir())
    assert json.loads((output / "design_manifest.json").read_text()) == []


def test_recipe_pins_native_and_cuda_stack():
    recipe = (ROOT / "apptainer/disco.def").read_text()
    assert "82b594f838eb61dd8c78ae3a403ccb8f00cf7abf" in recipe
    assert "torch==2.7.1+cu128" in recipe
    assert "triton==3.3.1" in recipe
    assert "cu124" not in recipe
    assert "HF_HUB_OFFLINE=" not in recipe
    assert "TRANSFORMERS_OFFLINE=" not in recipe
    assert "git checkout --detach" in recipe
