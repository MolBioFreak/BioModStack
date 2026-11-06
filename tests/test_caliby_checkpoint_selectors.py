"""Compare every advertised selector with the installed native resolver (no loads)."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_all_checkpoint_selectors_match_installed_native_source(tmp_path):
    image = Path(os.environ.get("BMS_TEST_CALIBY_IMAGE", "/mnt/BioModStack/apptainer/caliby.sif"))
    if not image.is_file() or not shutil.which("apptainer"):
        pytest.skip("Installed Caliby image required; no downloads")
    native = json.loads(subprocess.check_output([
        "apptainer", "exec", str(image), "python", "-c",
        "import json; from caliby.weights import MODEL_REGISTRY; print(json.dumps(MODEL_REGISTRY))",
    ], text=True))
    sys.path.insert(0, str(ROOT))
    from scripts.caliby_runtime import _CALIBY_CHECKPOINTS, preflight_caliby_runtime
    spec = importlib.util.spec_from_file_location("selector_contract", ROOT / "platform/api/services/caliby_native.py")
    assert spec is not None and spec.loader is not None
    contract = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contract
    spec.loader.exec_module(contract)
    from typing import get_args
    sequences = get_args(contract.EnsembleDesign.model_fields["model_name"].annotation)
    packers = get_args(contract.SidechainPack.model_fields["packer_model_name"].annotation)
    assert set(native) == set(sequences) | set(packers) == set(_CALIBY_CHECKPOINTS)
    assert {key: str(path) for key, path in _CALIBY_CHECKPOINTS.items()} == native
    # Isolated empty native names exercise selection only, not weight acceptance.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MODEL_PARAMS_DIR", str(tmp_path))
        for name, relative in native.items():
            checkpoint = tmp_path / relative
            checkpoint.parent.mkdir(exist_ok=True)
            checkpoint.write_text("INERT selector fixture; never loaded")
            tasks = ("sequence_design", "ensemble_design") if name in sequences else ("sidechain_pack",)
            for task in tasks:
                runtime = preflight_caliby_runtime(task=task, model_name=name,
                    packer_model_name=name if task == "sidechain_pack" else None, allow_download=False)
                assert runtime["checkpoint_path"] == str(checkpoint)
                assert runtime["model_name"] == name
                if task != "sequence_design":
                    key = "packer_model_name" if task == "sidechain_pack" else "model_name"
                    assert contract.selected_assets(task, {key: name})["checkpoint"] == relative
