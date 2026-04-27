from __future__ import annotations

import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import ModelRegistry
from services.nextflow import build_nextflow_command
from template_registry import TemplateRegistry


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def _flag_absent(cmd: list[str], flag: str) -> bool:
    return flag not in cmd


def test_model_registry_loads_confornets_experimental_as_monomer_workflow() -> None:
    registry = ModelRegistry()

    model = registry.get_model("confornets_experimental")

    assert model is not None
    assert model.name == "ConforNets Experimental"
    assert model.experimental is True
    assert model.category == "structure_prediction"
    assert {mode.id for mode in model.modes} == {"design"}

    params = {param.name: param for param in model.params}
    for expected in (
        "task",
        "sequence",
        "chain_id",
        "checkpoint_path",
        "confornets_repo_path",
        "num_runs",
        "k_confornets",
        "num_samples",
        "max_steps",
        "save_steps",
        "reference_pdb_1",
        "reference_pdb_2",
        "confornet_path",
        "mse_dir",
    ):
        assert expected in params

    assert params["task"].enum == ["diversity", "mse", "transfer"]
    assert "complex" not in " ".join(params["task"].enum)
    assert "ligand" not in " ".join(params["task"].enum)
    assert "nucleic" not in " ".join(params["task"].enum)
    assert params["sequence"].required is True
    assert params["k_confornets"].minimum == 2
    assert params["num_samples"].minimum == 1
    assert params["num_samples"].maximum == 512

    assert registry.validate_job_params(
        "confornets_experimental",
        "design",
        {
            "task": "diversity",
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "checkpoint_path": "/weights/openfold3/of3-p2-155k.pt",
            "confornets_repo_path": "/opt/confornets",
            "num_runs": 2,
            "k_confornets": 2,
            "num_samples": 5,
        },
    ) == []


def test_template_registry_loads_confornets_experimental_card_with_monomer_only_contract() -> None:
    registry = TemplateRegistry(API_ROOT / "config" / "templates")

    template = registry.get_template("confornets_experimental")

    assert template is not None
    assert template.name == "ConforNets Experimental"
    assert template.experimental is True
    assert template.preset_params["template_model_id"] == "confornets_experimental"
    assert template.preset_params["template_mode_id"] == "design"
    assert template.preset_params["cn_task"] == "diversity"

    card_text = " ".join(
        [
            template.description or "",
            template.goal or "",
            template.status or "",
            " ".join(param.description for param in template.user_params),
        ]
    ).lower()
    assert "monomer" in card_text
    assert "single-chain" in card_text
    assert "of3p2" in card_text or "openfold3" in card_text
    assert "two reference" in card_text or "2 reference" in card_text

    user_params = {param.name: param for param in template.user_params}
    assert user_params["task"].enum == ["diversity", "mse", "transfer"]
    assert user_params["chain_id"].type == "enum"
    assert user_params["save_steps"].type == "enum"
    assert user_params["source_test_cases"].type == "enum"
    assert user_params["skip_msa"].type == "boolean"
    assert user_params["compute_confidence"].type == "boolean"
    assert user_params["save_full_confidence"].type == "boolean"
    assert [param.name for param in template.user_params if param.type in {"string", "text"}] == ["sequence"]
    for numeric_param in (
        "num_runs",
        "k_confornets",
        "num_samples",
        "max_steps",
        "num_recycles",
        "num_diffusion_steps",
    ):
        assert user_params[numeric_param].ui_control == "slider"
        assert user_params[numeric_param].minimum is not None
        assert user_params[numeric_param].maximum is not None
        assert user_params[numeric_param].step is not None
    for forbidden in ("complex", "ligand_binder", "nucleic_binder", "multimer"):
        assert forbidden not in str(user_params["task"].enum)
    assert not any(param.name.startswith("cn_") for param in template.user_params)


def test_build_nextflow_command_maps_confornets_params_to_cn_namespace(tmp_path: Path) -> None:
    output_dir = tmp_path / "confornets_command"
    cmd = build_nextflow_command(
        "confornets_experimental",
        "design",
        {
            "task": "diversity",
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "chain_id": "A",
            "benchmark_name": "bms_confornets",
            "test_case_name": "monomer_case",
            "checkpoint_path": "/weights/openfold3/of3-p2-155k.pt",
            "confornets_repo_path": "/opt/confornets",
            "num_runs": 3,
            "k_confornets": 2,
            "num_samples": 7,
            "max_steps": 21,
            "save_steps": "5,10,15,20",
            "num_recycles": 0,
            "num_diffusion_steps": 200,
            "skip_msa": True,
            "compute_confidence": False,
            "reference_pdb_1": "/tmp/ref_open.pdb",
            "reference_name_1": "open_ref",
            "reference_pdb_2": "/tmp/ref_closed.cif",
            "reference_name_2": "closed_ref",
        },
        str(output_dir),
        job_id="job-cn-1",
    )

    assert cmd[:4] == ["nextflow", "run", "main.nf", "-profile"]
    assert "confornets_experimental,workstation_ryzen7960x" in cmd
    assert _flag_value(cmd, "--rfd_mode") == "confornets_experimental"
    assert _flag_value(cmd, "--cn_task") == "diversity"
    assert _flag_value(cmd, "--cn_sequence") == "MKTIIALSYIFCLVFADYKDDDDA"
    assert _flag_value(cmd, "--cn_chain_id") == "A"
    assert _flag_value(cmd, "--cn_checkpoint_path") == "/weights/openfold3/of3-p2-155k.pt"
    assert _flag_value(cmd, "--cn_confornets_repo_path") == "/opt/confornets"
    assert _flag_value(cmd, "--cn_num_runs") == "3"
    assert _flag_value(cmd, "--cn_k_confornets") == "2"
    assert _flag_value(cmd, "--cn_num_samples") == "7"
    assert _flag_value(cmd, "--cn_save_steps") == "5,10,15,20"
    assert _flag_value(cmd, "--cn_skip_msa") == "true"
    assert _flag_value(cmd, "--cn_reference_pdb_1") == "/tmp/ref_open.pdb"
    assert _flag_value(cmd, "--cn_reference_name_2") == "closed_ref"
    assert _flag_value(cmd, "--rfd_num_designs") == "7"

    for raw_flag in (
        "--task",
        "--sequence",
        "--chain_id",
        "--checkpoint_path",
        "--confornets_repo_path",
        "--num_samples",
        "--skip_msa",
    ):
        assert _flag_absent(cmd, raw_flag)


def test_confornets_nextflow_static_contract_is_wired_without_stub_outputs() -> None:
    main_text = (REPO_ROOT / "main.nf").read_text(encoding="utf-8")
    config_text = (REPO_ROOT / "nextflow.config").read_text(encoding="utf-8")
    workflow_path = REPO_ROOT / "workflows" / "confornets_experimental.nf"
    module_path = REPO_ROOT / "modules" / "confornets_experimental.nf"

    assert "include { CONFORNETS_EXPERIMENTAL } from './workflows/confornets_experimental.nf'" in main_text
    assert "params.rfd_mode == 'confornets_experimental'" in main_text
    assert "CONFORNETS_EXPERIMENTAL()" in main_text

    assert "confornets_experimental {" in config_text
    assert "rfd_mode = 'confornets_experimental'" in config_text
    assert "withLabel: ConforNets" in config_text
    assert "apptainer.pullTimeout" in config_text
    assert "apptainer.cacheDir" in config_text

    workflow_text = workflow_path.read_text(encoding="utf-8")
    module_text = module_path.read_text(encoding="utf-8")
    assert "workflow CONFORNETS_EXPERIMENTAL" in workflow_text
    assert "PrepConforNetsRequest" in module_text
    assert "RunConforNets" in module_text
    assert "FinalizeConforNetsOutputs" in module_text
    assert "process PrepConforNetsRequest {\n    label 'local_cpu'" in module_text
    assert "python3 ${params.code_root}/scripts/prep_confornets_request.py" in module_text
    assert "prep_confornets_request.py" in module_text
    assert "run_confornets_inference.py" in module_text
    assert "stub:" not in module_text

    assert (REPO_ROOT / "scripts" / "prep_confornets_request.py").exists()
    assert (REPO_ROOT / "scripts" / "run_confornets_inference.py").exists()


def test_prep_confornets_rejects_single_confornet_diversity(tmp_path: Path) -> None:
    output_path = tmp_path / "request.json"
    assets_dir = tmp_path / "assets"

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "prep_confornets_request.py"),
            "--task",
            "diversity",
            "--sequence",
            "MKTIIALSYIFCLVFADYKDDDDA",
            "--checkpoint-path",
            "/weights/openfold3/of3-p2-155k.pt",
            "--confornets-repo-path",
            "/opt/confornets",
            "--k-confornets",
            "1",
            "--assets-dir",
            str(assets_dir),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "requires at least 2 ConforNets" in result.stderr
    assert not output_path.exists()
