from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import ModelRegistry
from services.nextflow import build_nextflow_command


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_protenix_v2_model_is_live_and_selectable() -> None:
    model = ModelRegistry().get_model("protenix")

    assert model is not None
    assert model.enabled is True
    assert model.experimental is False
    assert model.version == "2.0.0"
    assert model.container == "protenix.sif"
    assert {mode.id for mode in model.modes} == {"predict", "complex"}

    weights_param = next(param for param in model.params if param.name == "protenix_model_weights")
    assert weights_param.default == "protenix-v2"
    assert weights_param.enum == ["protenix-v2"]


def test_protenix_standalone_command_is_v2_and_not_boltz() -> None:
    cmd = build_nextflow_command(
        model_id="protenix",
        mode="predict",
        params={"sequence": "MKTIIALSYIFCLVFADYKDDDDA", "sequence_name": "v2_smoke"},
        output_dir="/tmp/protenix-v2-standalone",
    )

    assert cmd[2] == "workflows/structure_prediction.nf"
    assert _flag_value(cmd, "-profile").startswith("protenix,")
    assert _flag_value(cmd, "--pred_method") == "protenix"
    assert _flag_value(cmd, "--protenix_model_weights") == "protenix-v2"
    assert _flag_value(cmd, "--sequence_input") == "MKTIIALSYIFCLVFADYKDDDDA"


def test_protenix_complex_command_is_v2_and_routes_to_complex_workflow() -> None:
    cmd = build_nextflow_command(
        model_id="protenix",
        mode="complex",
        params={
            "sequence_name": "v2_complex_smoke",
            "complex_components": [
                {"type": "protein", "id": "A", "sequence": "MKTIIALSYIFCLVFADYKDDDDA"},
                {"type": "protein", "id": "B", "sequence": "GIVEQCCTSICSLYQLENYCN"},
            ],
        },
        output_dir="/tmp/protenix-v2-complex",
    )

    assert cmd[2] == "workflows/complex_prediction.nf"
    assert _flag_value(cmd, "-profile").startswith("protenix,")
    assert _flag_value(cmd, "--pred_method") == "protenix"
    assert _flag_value(cmd, "--protenix_model_weights") == "protenix-v2"
    assert "--complex_json_path" in cmd


def test_protenix_gpu_label_keeps_runtime_image_and_weight_bind() -> None:
    config = (API_ROOT.parent.parent / "nextflow.config").read_text(encoding="utf-8")
    start = config.index("    withLabel: Protenix {")
    end = config.index("\n    }", start)
    block = config[start:end]

    assert 'container = "${params.container_dir}/protenix.sif"' in block
    assert "ext.containerOptions =" in block
    assert "--bind ${params.protenix_weights}:/protenix_weights" in block
