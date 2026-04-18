from __future__ import annotations

import sys
from pathlib import Path

import yaml


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import ModelRegistry
from services.nextflow import build_nextflow_command
from template_registry import TemplateRegistry


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_model_registry_loads_boltz_cp_experimental() -> None:
    registry = ModelRegistry()

    model = registry.get_model("boltz_cp_experimental")

    assert model is not None
    assert model.name == "Boltz-CP Experimental"
    assert model.experimental is True
    assert any(mode.id == "design" for mode in model.modes)
    assert any(param.name == "input_path" for param in model.params)
    assert any(param.name == "gpu_ids" for param in model.params)
    assert any(param.name == "size_cp" for param in model.params)


def test_template_registry_loads_boltz_cp_experimental() -> None:
    registry = TemplateRegistry(API_ROOT / "config" / "templates")

    template = registry.get_template("boltz_cp_experimental")

    assert template is not None
    assert template.name == "Boltz-CP Experimental"
    assert template.experimental is True
    assert template.preset_params["template_model_id"] == "boltz_cp_experimental"
    assert template.preset_params["template_mode_id"] == "design"
    assert template.preset_params["structure_launch_variant"] == "boltz_cp_experimental"
    assert template.preset_params["bcp_repo_path"] == "/home/dalab/tmp/boltz-cp"
    assert not any(param.name == "input_path" for param in template.user_params)
    assert not any(param.name == "gpu_ids" for param in template.user_params)
    assert not any(param.name == "size_cp" for param in template.user_params)


def test_build_nextflow_command_maps_boltz_cp_experimental_params() -> None:
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "input_path": "/tmp/complex_input.yaml",
            "gpu_ids": "0,1,2,3",
            "size_cp": 4,
            "input_format": "config_files",
            "output_format": "mmcif",
            "write_full_pae": True,
            "recycling_steps": 5,
            "sampling_steps": 150,
            "diffusion_samples": 2,
            "seed": 17,
        },
        "/tmp/out",
        job_id="job-bcp-1",
    )

    joined = " ".join(cmd)

    assert cmd[:4] == ["nextflow", "run", "main.nf", "-profile"]
    assert "boltz_cp_experimental,workstation_ryzen7960x" in cmd
    assert "--bcp_input_path /tmp/complex_input.yaml" in joined
    assert "--bcp_gpu_ids 0,1,2,3" in joined
    assert "--bcp_size_cp 4" in joined
    assert "--bcp_input_format config_files" in joined
    assert "--bcp_output_format mmcif" in joined
    assert "--bcp_write_full_pae true" in joined
    assert "--bcp_recycling_steps 5" in joined
    assert "--bcp_sampling_steps 150" in joined
    assert "--bcp_diffusion_samples 2" in joined
    assert "--bcp_seed 17" in joined
    assert "--rfd_mode boltz_cp_experimental" in joined
    assert "--input_path /tmp/complex_input.yaml" not in joined
    assert "--gpu_ids 0,1,2,3" not in joined


def test_build_nextflow_command_stages_boltz_cp_yaml_from_sequence_inputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "bcp_sequence"
    sequence = "MKTIIALSYIFCLVFADYKDDDDA"
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "sequence": sequence,
            "sequence_name": "cp_sequence_case",
            "boltz_use_msa": False,
            "boltz_recycling_steps": 6,
            "boltz_sampling_steps": 200,
            "boltz_num_samples": 2,
            "pinned_gpus": [0, 1, 2, 3],
        },
        str(output_dir),
        job_id="job-bcp-seq",
    )

    joined = " ".join(cmd)
    input_path = Path(_flag_value(cmd, "--bcp_input_path"))
    payload = yaml.safe_load(input_path.read_text())

    assert input_path.exists()
    assert payload == {
        "version": 1,
        "sequences": [
            {
                "protein": {
                    "id": ["A"],
                    "sequence": sequence,
                    "msa": "empty",
                }
            }
        ],
    }
    assert "--bcp_gpu_ids 0,1,2,3" in joined
    assert "--bcp_size_cp 4" in joined
    assert "--bcp_recycling_steps 6" in joined
    assert "--bcp_sampling_steps 200" in joined
    assert "--bcp_diffusion_samples 2" in joined
    assert "--bcp_input_format config_files" in joined
    assert "--sequence_input" not in joined
    assert "--complex_json_path" not in joined


def test_build_nextflow_command_stages_boltz_cp_yaml_from_complex_components(tmp_path: Path) -> None:
    output_dir = tmp_path / "bcp_complex"
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_complex_case",
            "complex_components": [
                {"type": "protein", "id": "A", "sequence": "MKTIIALSYIFCLVFADYKDDDDA"},
                {"type": "protein", "id": "B", "sequence": "GGGGGGGGGG"},
                {"type": "ligand", "id": "L", "smiles": "CCO", "name": "ethanol"},
            ],
            "pinned_gpus": [0, 1, 2, 3],
        },
        str(output_dir),
        job_id="job-bcp-complex",
    )

    joined = " ".join(cmd)
    input_path = Path(_flag_value(cmd, "--bcp_input_path"))
    payload = yaml.safe_load(input_path.read_text())

    assert input_path.exists()
    assert payload["version"] == 1
    assert payload["sequences"][0]["protein"]["id"] == ["A"]
    assert payload["sequences"][0]["protein"]["msa"] == "empty"
    assert payload["sequences"][1]["protein"]["id"] == ["B"]
    assert payload["sequences"][1]["protein"]["msa"] == "empty"
    assert payload["sequences"][2]["ligand"]["id"] == ["L"]
    assert payload["sequences"][2]["ligand"]["smiles"] == "CCO"
    assert "--bcp_gpu_ids 0,1,2,3" in joined
    assert "--bcp_size_cp 4" in joined
    assert "--complex_json_path" not in joined
