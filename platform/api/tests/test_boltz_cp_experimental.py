from __future__ import annotations

import sys
from pathlib import Path

import yaml


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import ModelRegistry
import routers.jobs as jobs
from services.nextflow import build_nextflow_command
from template_registry import TemplateRegistry


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_model_registry_loads_boltz_cp_experimental() -> None:
    registry = ModelRegistry()

    model = registry.get_model("boltz_cp_experimental")

    assert model is not None
    assert model.name == "Fold-CP Experimental"
    assert model.experimental is True
    assert any(mode.id == "design" for mode in model.modes)
    assert any(param.name == "input_path" for param in model.params)
    assert any(param.name == "gpu_ids" for param in model.params)
    assert any(param.name == "size_cp" for param in model.params)


def test_template_registry_loads_boltz_cp_experimental() -> None:
    registry = TemplateRegistry(API_ROOT / "config" / "templates")

    template = registry.get_template("boltz_cp_experimental")

    assert template is not None
    assert template.name == "Fold-CP Experimental"
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


def test_boltz_cp_structure_launcher_aliases_pass_registry_validation_without_explicit_input_path() -> None:
    registry = ModelRegistry()
    validation_params = jobs._normalize_boltz_cp_params_for_validation(
        "boltz_cp_experimental",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_sequence_case",
            "structure_launch_variant": "boltz_cp_experimental",
            "bcp_input_format": "config_files",
            "bcp_output_format": "mmcif",
            "bcp_write_full_pae": False,
            "bcp_gpu_ids": "0,1,2,3",
            "bcp_size_cp": 4,
            "boltz_recycling_steps": 6,
            "boltz_sampling_steps": 200,
            "boltz_num_samples": 2,
            "pinned_gpus": [0, 1, 2, 3],
        },
    )

    assert validation_params["input_path"] == jobs.BOLTZ_CP_STRUCTURE_LAUNCHER_INPUT_SENTINEL
    assert validation_params["gpu_ids"] == "0,1,2,3"
    assert validation_params["size_cp"] == 4
    assert validation_params["input_format"] == "config_files"
    assert validation_params["output_format"] == "mmcif"
    assert validation_params["recycling_steps"] == 6
    assert validation_params["sampling_steps"] == 200
    assert validation_params["diffusion_samples"] == 2
    assert registry.validate_job_params("boltz_cp_experimental", "design", validation_params) == []



def test_boltz_cp_structure_launcher_defaults_size_cp_to_largest_square_divisor() -> None:
    registry = ModelRegistry()
    validation_params = jobs._normalize_boltz_cp_params_for_validation(
        "boltz_cp_experimental",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_square_divisor_case",
            "structure_launch_variant": "boltz_cp_experimental",
            "bcp_input_format": "config_files",
            "bcp_output_format": "mmcif",
            "bcp_write_full_pae": False,
            "pinned_gpus": [2, 3],
            "boltz_use_msa": True,
            "msa_provider": "colabfold_api",
        },
    )

    assert validation_params["gpu_ids"] == "2,3"
    assert validation_params["size_cp"] == 1
    assert registry.validate_job_params("boltz_cp_experimental", "design", validation_params) == []



def test_boltz_cp_structure_launcher_clamps_requested_size_cp_to_valid_square_divisor() -> None:
    registry = ModelRegistry()
    validation_params = jobs._normalize_boltz_cp_params_for_validation(
        "boltz_cp_experimental",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_square_divisor_requested_case",
            "structure_launch_variant": "boltz_cp_experimental",
            "bcp_input_format": "config_files",
            "bcp_output_format": "mmcif",
            "bcp_write_full_pae": False,
            "bcp_gpu_ids": "2,3",
            "bcp_size_cp": 4,
        },
    )

    assert validation_params["gpu_ids"] == "2,3"
    assert validation_params["size_cp"] == 1
    assert registry.validate_job_params("boltz_cp_experimental", "design", validation_params) == []



def test_build_nextflow_command_injects_boltz_cp_compat_container_default(tmp_path: Path) -> None:
    output_dir = tmp_path / "bcp_compat_container"
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_container_case",
            "container_dir": "/srv/apptainer",
            "pinned_gpus": [0, 1, 2, 3],
        },
        str(output_dir),
        job_id="job-bcp-container",
    )

    joined = " ".join(cmd)

    assert "--bcp_container_path /srv/apptainer/boltz2-pre-community-20260417-211613.sif" in joined



def test_boltz_cp_nextflow_config_uses_explicit_compat_container_override() -> None:
    config_text = (API_ROOT.parents[1] / "nextflow.config").read_text(encoding="utf-8")
    boltz_cp_section = config_text.split("withLabel: BoltzCP {", 1)[1].split("withLabel:", 1)[0]

    assert "params.bcp_container_path" in boltz_cp_section
    assert "boltz2-pre-community-20260417-211613.sif" in boltz_cp_section



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


def test_build_nextflow_command_stages_boltz_cp_yaml_without_empty_msa_when_enabled(tmp_path: Path) -> None:
    output_dir = tmp_path / "bcp_sequence_with_msa"
    sequence = "MKTIIALSYIFCLVFADYKDDDDA"
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "sequence": sequence,
            "sequence_name": "cp_sequence_with_msa",
            "boltz_use_msa": True,
            "msa_provider": "colabfold_api",
            "pinned_gpus": [0, 1, 2, 3],
        },
        str(output_dir),
        job_id="job-bcp-seq-msa",
    )

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
                }
            }
        ],
    }


def test_build_nextflow_command_threads_boltz_cp_msa_submission_params(tmp_path: Path) -> None:
    output_dir = tmp_path / "bcp_msa_params"
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_msa_params",
            "boltz_use_msa": True,
            "msa_provider": "colabfold_api",
            "msa_preset": "balanced",
            "colabfold_api_host": "https://api.colabfold.com",
            "colabfold_api_min_interval": 9,
            "colabfold_api_poll_interval": 11,
            "msa_num_iterations": 2,
            "msa_use_env": True,
            "msa_use_expand": False,
            "msa_min_seq_id": 0.25,
            "msa_min_coverage": 0.5,
            "msa_taxon_list": "9606,10090",
            "pinned_gpus": [2, 3],
        },
        str(output_dir),
        job_id="job-bcp-msa-flags",
    )

    joined = " ".join(cmd)

    assert "--boltz_use_msa true" in joined
    assert "--msa_provider colabfold_api" in joined
    assert "--msa_preset balanced" in joined
    assert "--colabfold_api_host https://api.colabfold.com" in joined
    assert "--colabfold_api_min_interval 9" in joined
    assert "--colabfold_api_poll_interval 11" in joined
    assert "--msa_num_iterations 2" in joined
    assert "--msa_use_env true" in joined
    assert "--msa_use_expand false" in joined
    assert "--msa_min_seq_id 0.25" in joined
    assert "--msa_min_coverage 0.5" in joined
    assert "--msa_taxon_list 9606,10090" in joined
    assert "--bcp_gpu_ids 2,3" in joined
    assert "--bcp_size_cp 1" in joined


def test_boltz_cp_module_materializes_msa_inputs_with_run_local_msa() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "def shellQuote(value)" in module_text
    assert "run_local_msa.py" in module_text
    assert '"--msa-provider"' in module_text
    assert 'os.environ.get("MSA_PROVIDER", "local")' in module_text
    assert "materializing msa-enabled boltz-cp input bundles" in module_text.lower()
    assert "REPO_PATH=${repoPath}" in module_text
    assert 'MSA_TAXON_LIST=${quotedMsaTaxonList}' in module_text
    assert 'REPO_PATH="${params.bcp_repo_path ?: \'\'}"' not in module_text
    assert 'MSA_TAXON_LIST="${params.msa_taxon_list ?: \'\'}"' not in module_text


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
