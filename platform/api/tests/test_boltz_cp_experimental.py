from __future__ import annotations

import sys
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
    assert any(param.name == "shard_plan_id" for param in model.params)
    assert any(param.name == "gpu_ids" for param in model.params)
    assert any(param.name == "backend" for param in model.params)
    assert any(param.name == "triattn_backend" for param in model.params)
    assert any(param.name == "context_store_mode" for param in model.params)
    assert any(param.name == "context_store_root" for param in model.params)
    assert not any(param.name == "size_cp" for param in model.params)


def test_template_registry_loads_boltz_cp_experimental() -> None:
    registry = TemplateRegistry(API_ROOT / "config" / "templates")

    template = registry.get_template("boltz_cp_experimental")

    assert template is not None
    assert template.name == "Fold-CP Experimental"
    assert template.experimental is True
    assert template.preset_params["template_model_id"] == "boltz_cp_experimental"
    assert template.preset_params["template_mode_id"] == "design"
    assert template.preset_params["structure_launch_variant"] == "boltz_cp_experimental"
    assert template.preset_params["bcp_context_store_mode"] == "evidence-only"
    assert template.preset_params["bcp_context_query_tile_tokens"] == 512
    assert template.preset_params["bcp_shard_plan_id"] == "2x2"
    assert template.preset_params["bcp_backend"] == "true-distributed-context-parallel"
    assert template.preset_params["bcp_triattn_backend"] == "reference"
    assert not any(param.name == "input_path" for param in template.user_params)
    assert not any(param.name == "gpu_ids" for param in template.user_params)
    assert not any(param.name == "size_cp" for param in template.user_params)


def test_boltz_cp_launch_copy_identifies_true_distributed_default_without_hiding_legacy_boundary() -> None:
    model_text = (API_ROOT / "config" / "models" / "boltz_cp_experimental.yaml").read_text(encoding="utf-8")
    template_text = (API_ROOT / "config" / "templates" / "boltz_cp_experimental.yaml").read_text(encoding="utf-8")
    combined = f"{model_text}\n{template_text}"

    assert "true-distributed-context-parallel" in combined
    assert "DTensor context-parallel Boltz-2 prediction data-plane" in combined
    assert "shared-cache-serial-output-tiling" in combined
    assert "legacy control-plane/output-tiling fallback" in combined
    assert "triattn_backend" in combined
    assert "context_store_mode" in combined
    assert "evidence-only" in combined
    assert "rank-local-dram-spill-layer" in combined
    assert "rank-local-dram-spill-op" in combined
    assert "does not claim tensor streaming, spill execution, or memory reduction" in combined
    assert "default: reference" in combined
    assert "trifast" in combined
    assert "fail closed" in combined
    assert "current large-protein coordinator is control-plane" not in combined
    assert "one single full Boltz prediction then output tiling" not in combined
    assert "true multi-GPU single-fold" not in combined


def test_boltz_cp_workflow_banner_prints_true_distributed_default_and_legacy_boundary() -> None:
    workflow_text = (API_ROOT.parents[1] / "workflows" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "* Backend: ${params.get('bcp_backend', 'true-distributed-context-parallel')}" in workflow_text
    assert "* Data-plane: true-distributed-context-parallel launches torch.distributed DTensor CP prediction; shared-cache backends remain legacy output-tiling only" in workflow_text


def test_main_boltz_cp_banner_prints_backend_and_serial_legacy_boundary() -> None:
    main_text = (API_ROOT.parents[1] / "main.nf").read_text(encoding="utf-8")

    assert "def bcpBackend = params.get('bcp_backend', 'true-distributed-context-parallel')" in main_text
    assert "* Backend: ${bcpBackend}" in main_text
    assert "shared-cache backends remain legacy serial full-prediction/output-tiling only" in main_text
    assert "params.bcp_input_path" not in main_text
    assert "params.bcp_gpu_ids" not in main_text
    assert "params.bcp_size_cp" not in main_text
    assert "params.bcp_sampling_steps" not in main_text


def test_boltz_cp_workflow_uses_warning_safe_optional_param_access() -> None:
    workflow_text = (API_ROOT.parents[1] / "workflows" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "params.bcp_shard_plan_id" not in workflow_text
    assert "params.bcp_backend" not in workflow_text
    assert "params.bcp_sampling_steps" not in workflow_text


def test_boltz_cp_module_uses_warning_safe_bcp_param_access() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "params.bcp_" not in module_text


def test_boltz_cp_module_null_optional_bcp_values_do_not_become_literal_null_flags() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "def seedValue = (params.get('bcp_seed', '') ?: '').toString().trim()" in module_text
    assert "params.get('bcp_seed', '').toString().trim()" not in module_text
    assert "def gpuIdsParam = params.get('bcp_gpu_ids', null)" in module_text
    assert "gpuIdsParam = params.get('gpu_id', '')" in module_text
    assert "def gpuIdsValue = (gpuIdsParam == null ? '' : gpuIdsParam.toString())" in module_text
    assert "params.get('bcp_gpu_ids', params.get('gpu_id', '')) ?: params.get('gpu_id', '')" not in module_text


def test_boltz_cp_model_rejects_single_sampling_step_before_runtime() -> None:
    registry = ModelRegistry()

    errors = registry.validate_job_params(
        "boltz_cp_experimental",
        "design",
        {
            "input_path": "/tmp/complex_input.yaml",
            "sampling_steps": 1,
        },
    )

    assert "sampling_steps must be >= 2.0" in errors


def test_boltz_cp_model_accepts_rank_local_dram_spill_context_store_modes() -> None:
    registry = ModelRegistry()

    for mode in ("rank-local-dram-spill-layer", "rank-local-dram-spill-op"):
        errors = registry.validate_job_params(
            "boltz_cp_experimental",
            "design",
            {
                "input_path": "/tmp/complex_input.yaml",
                "sampling_steps": 200,
                "context_store_mode": mode,
            },
        )

        assert errors == []


def test_boltz_cp_validation_normalizes_triattn_alias_before_enum_check() -> None:
    registry = ModelRegistry()
    validation_params = jobs._normalize_boltz_cp_params_for_validation(
        "boltz_cp_experimental",
        {
            "input_path": "/tmp/complex_input.yaml",
            "bcp_triattn_backend": "not-a-real-triattn-backend",
        },
    )

    assert validation_params["triattn_backend"] == "not-a-real-triattn-backend"
    assert registry.validate_job_params("boltz_cp_experimental", "design", validation_params) == [
        "Invalid value for triattn_backend: must be one of ['reference', 'trifast', 'cueq']"
    ]


def test_boltz_cp_module_true_cp_preflights_sampling_steps_before_torchrun() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    preflight_index = module_text.index("runtime-parameter-preflight")
    torchrun_index = module_text.index("torch.distributed.run")

    assert preflight_index < torchrun_index
    assert "InvalidBoltzSamplingSteps" in module_text
    assert "BCP_SAMPLING_STEPS" in module_text
    assert "Need at least 2 sampling steps" in module_text


def test_boltz_cp_module_true_cp_preflights_triangle_backend_before_torchrun() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    preflight_index = module_text.index("triattn-backend-preflight")
    torchrun_index = module_text.index("torch.distributed.run")

    assert preflight_index < torchrun_index
    assert "MissingTriangleAttentionBackend" in module_text
    assert "UnsupportedTriangleAttentionBackend" in module_text
    assert "supported_backends = {\"reference\", \"trifast\", \"cueq\"}" in module_text
    assert "importlib.util.find_spec" in module_text
    assert "is_true_distributed_context_parallel" in module_text
    assert "BCP_TRIATTN_BACKEND" in module_text


def test_build_nextflow_command_maps_boltz_cp_experimental_params() -> None:
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "input_path": "/tmp/complex_input.yaml",
            "shard_plan_id": "4x4",
            "gpu_ids": "0,1,2,3",
            "input_format": "config_files",
            "output_format": "mmcif",
            "write_full_pae": True,
            "recycling_steps": 5,
            "sampling_steps": 150,
            "diffusion_samples": 2,
            "seed": 17,
            "backend": "dram-context-spill-workhorse",
            "triattn_backend": "reference",
            "context_store_mode": "off",
            "context_store_root": "/tmp/predictor-owned-cp-store",
            "context_query_tile_tokens": 256,
        },
        "/tmp/out",
        job_id="job-bcp-1",
    )

    joined = " ".join(cmd)

    assert cmd[:4] == ["nextflow", "run", "main.nf", "-profile"]
    assert "boltz_cp_experimental,workstation_ryzen7960x" in cmd
    assert "--bcp_input_path /tmp/complex_input.yaml" in joined
    assert "--bcp_shard_plan_id 4x4" in joined
    assert "--bcp_gpu_ids 0,1,2,3" in joined
    assert "--bcp_size_cp 4" in joined
    assert "--bcp_input_format config_files" in joined
    assert "--bcp_output_format mmcif" in joined
    assert "--bcp_write_full_pae true" in joined
    assert "--bcp_recycling_steps 5" in joined
    assert "--bcp_sampling_steps 150" in joined
    assert "--bcp_diffusion_samples 2" in joined
    assert "--bcp_seed 17" in joined
    assert "--bcp_backend dram-context-spill-workhorse" in joined
    assert "--bcp_triattn_backend reference" in joined
    assert "--bcp_context_store_mode off" in joined
    assert "--bcp_context_store_root /tmp/predictor-owned-cp-store" in joined
    assert "--bcp_context_query_tile_tokens 256" in joined
    assert "--rfd_mode boltz_cp_experimental" in joined
    assert "--input_path /tmp/complex_input.yaml" not in joined
    assert "--gpu_ids 0,1,2,3" not in joined


def test_build_nextflow_command_defaults_boltz_cp_query_tiling_to_reference_triangle_attention() -> None:
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "input_path": "/tmp/complex_input.yaml",
            "shard_plan_id": "2x2",
        },
        "/tmp/out",
        job_id="job-bcp-query-tiling-default",
    )

    joined = " ".join(cmd)

    assert "--bcp_triattn_backend reference" in joined
    assert "--bcp_context_query_tile_tokens 512" in joined


def test_build_nextflow_command_defaults_boltz_cp_gpu_bridge_to_scheduler_gpu_not_local_four_gpu_rig(tmp_path: Path) -> None:
    output_dir = tmp_path / "bcp_scheduler_gpu_default"
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "input_path": "/tmp/complex_input.yaml",
            "shard_plan_id": "4x4",
            "gpu_id": 2,
        },
        str(output_dir),
        job_id="job-bcp-scheduler-gpu",
    )

    joined = " ".join(cmd)

    assert "--bcp_gpu_ids 2" in joined
    assert "--bcp_size_cp 1" in joined
    assert "0,1,2,3" not in joined



def test_boltz_cp_structure_launcher_aliases_pass_registry_validation_without_explicit_input_path() -> None:
    registry = ModelRegistry()
    validation_params = jobs._normalize_boltz_cp_params_for_validation(
        "boltz_cp_experimental",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_sequence_case",
            "structure_launch_variant": "boltz_cp_experimental",
            "bcp_shard_plan_id": "4x4",
            "bcp_input_format": "config_files",
            "bcp_output_format": "mmcif",
            "bcp_write_full_pae": False,
            "bcp_gpu_ids": "0,1,2,3",
            "bcp_backend": "dram-context-spill-workhorse",
            "boltz_recycling_steps": 6,
            "boltz_sampling_steps": 200,
            "boltz_num_samples": 2,
            "pinned_gpus": [0, 1, 2, 3],
        },
    )

    assert validation_params["input_path"] == jobs.BOLTZ_CP_STRUCTURE_LAUNCHER_INPUT_SENTINEL
    assert validation_params["shard_plan_id"] == "4x4"
    assert validation_params["gpu_ids"] == "0,1,2,3"
    assert validation_params["backend"] == "dram-context-spill-workhorse"
    assert "size_cp" not in validation_params
    assert validation_params["input_format"] == "config_files"
    assert validation_params["output_format"] == "mmcif"
    assert validation_params["recycling_steps"] == 6
    assert validation_params["sampling_steps"] == 200
    assert validation_params["diffusion_samples"] == 2
    assert registry.validate_job_params("boltz_cp_experimental", "design", validation_params) == []



def test_boltz_cp_structure_launcher_defaults_gpu_bridge_to_largest_square_divisor_without_exposing_size_cp() -> None:
    registry = ModelRegistry()
    validation_params = jobs._normalize_boltz_cp_params_for_validation(
        "boltz_cp_experimental",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_square_divisor_case",
            "structure_launch_variant": "boltz_cp_experimental",
            "bcp_shard_plan_id": "4x4",
            "bcp_input_format": "config_files",
            "bcp_output_format": "mmcif",
            "bcp_write_full_pae": False,
            "pinned_gpus": [2, 3],
            "boltz_use_msa": True,
            "msa_provider": "colabfold_api",
        },
    )

    assert validation_params["shard_plan_id"] == "4x4"
    assert validation_params["gpu_ids"] == "2,3"
    assert "size_cp" not in validation_params
    assert registry.validate_job_params("boltz_cp_experimental", "design", validation_params) == []



def test_boltz_cp_single_job_supports_colabfold_api_msa_provider() -> None:
    assert jobs._supports_colabfold_api_single_job("boltz_cp_experimental", "design") is True



def test_boltz_cp_structure_launcher_clamps_gpu_bridge_to_valid_square_divisor_without_exposing_size_cp() -> None:
    registry = ModelRegistry()
    validation_params = jobs._normalize_boltz_cp_params_for_validation(
        "boltz_cp_experimental",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_square_divisor_requested_case",
            "structure_launch_variant": "boltz_cp_experimental",
            "bcp_shard_plan_id": "4x4",
            "bcp_input_format": "config_files",
            "bcp_output_format": "mmcif",
            "bcp_write_full_pae": False,
            "bcp_gpu_ids": "2,3",
        },
    )

    assert validation_params["shard_plan_id"] == "4x4"
    assert validation_params["gpu_ids"] == "2,3"
    assert "size_cp" not in validation_params
    assert registry.validate_job_params("boltz_cp_experimental", "design", validation_params) == []



def test_boltz_cp_shard_plan_catalog_exposes_valid_logical_plans(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "_get_boltz_cp_catalog_physical_gpu_count", lambda: 4)
    catalog = jobs.list_boltz_cp_shard_plans()
    payload = catalog.model_dump() if hasattr(catalog, "model_dump") else catalog
    plans_by_id = {plan["id"]: plan for plan in payload["plans"]}

    assert payload["default_plan_id"] == "2x2"
    assert set(plans_by_id) == {"1x1", "2x2", "4x4"}
    assert plans_by_id["1x1"]["logical_size_cp"] == 1
    assert plans_by_id["2x2"]["logical_size_cp"] == 4
    assert plans_by_id["4x4"]["logical_size_cp"] == 16
    assert "does not change with GPU count" in plans_by_id["2x2"]["description"]
    assert "does not change with GPU count" in plans_by_id["4x4"]["description"]
    assert plans_by_id["4x4"]["physical_gpu_resolutions"] == [
        {"gpu_count": 1, "launch_size_cp": 1},
        {"gpu_count": 2, "launch_size_cp": 1},
        {"gpu_count": 3, "launch_size_cp": 1},
        {"gpu_count": 4, "launch_size_cp": 4},
    ]


def test_boltz_cp_shard_plan_catalog_route_uses_documented_jobs_prefix(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "_get_boltz_cp_catalog_physical_gpu_count", lambda: 4)
    app = FastAPI()
    app.include_router(jobs.router, prefix="/api/jobs")

    with TestClient(app) as client:
        response = client.get("/api/jobs/boltz-cp/shard-plans")
        accidental_double_prefix = client.get("/api/jobs/api/jobs/boltz-cp/shard-plans")

    assert response.status_code == 200
    assert response.json()["default_plan_id"] == "2x2"
    assert accidental_double_prefix.status_code == 404



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


def test_boltz_cp_production_sources_do_not_embed_local_four_gpu_default() -> None:
    repo_root = API_ROOT.parents[1]
    production_files = [
        repo_root / "main.nf",
        repo_root / "nextflow.config",
        repo_root / "modules" / "boltz_cp_experimental.nf",
        repo_root / "workflows" / "boltz_cp_experimental.nf",
        API_ROOT / "services" / "nextflow.py",
        API_ROOT / "services" / "boltz_cp_shard_plans.py",
        API_ROOT / "routers" / "jobs.py",
        API_ROOT / "config" / "models" / "boltz_cp_experimental.yaml",
    ]

    for source_path in production_files:
        source = source_path.read_text(encoding="utf-8")
        assert "0,1,2,3" not in source, f"{source_path} should not assume the DALAB 4-GPU ordinals"
    assert "max_physical_gpu_count=4" not in (API_ROOT / "routers" / "jobs.py").read_text(encoding="utf-8")



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



def test_boltz_cp_module_reuses_msa_paths_for_duplicate_sequences() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "msa_by_sequence: dict[str, str] = {}" in module_text
    assert "canonical_msa = msa_by_sequence.get(sequence)" in module_text
    assert "msa_by_sequence[sequence] = canonical_msa" in module_text
    assert "protein[\"msa\"] = canonical_msa" in module_text
    assert "if canonical_msa:" in module_text



def test_boltz_cp_workflow_branches_between_child_and_coordinator_paths() -> None:
    workflow_text = (API_ROOT.parents[1] / "workflows" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "BuildBoltzCPPlanManifest" in workflow_text
    assert "SpawnBoltzCPChildren" in workflow_text
    assert "WaitForBoltzCPChildren" in workflow_text
    assert "FinalizeBoltzCPExperimentalChildren" in workflow_text
    assert "def bcpRole = params.get('bcp_role', 'coordinator').toString()" in workflow_text
    assert "def requestedBackend = params.get('bcp_backend', 'true-distributed-context-parallel').toString()" in workflow_text
    assert "def useTrueDistributed = requestedBackend == 'true-distributed-context-parallel'" in workflow_text
    assert "def requiresPlanRuntime = requestedBackend in ['dram-context-spill-workhorse', 'shared-cache-serial-output-tiling', 'metadata-only']" in workflow_text
    assert "def useCoordinator = bcpRole != 'child' && !useTrueDistributed && (logicalSizeCp > 1 || requiresPlanRuntime)" in workflow_text
    assert "logicalSizeCp = ['1x1': 1, '2x2': 4, '4x4': 16]" in workflow_text
    assert "BuildBoltzCPPlanManifest.out.plan_store" in workflow_text
    assert "FinalizeBoltzCPExperimentalChildren(WaitForBoltzCPChildren.out.result, BuildBoltzCPPlanManifest.out.plan_store)" in workflow_text


def test_build_nextflow_command_defaults_boltz_cp_to_true_distributed_data_plane(tmp_path: Path) -> None:
    output_dir = tmp_path / "bcp_true_distributed_default"
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "input_path": "/tmp/complex_input.yaml",
            "shard_plan_id": "2x2",
            "gpu_ids": "0,2,3,1",
            "input_format": "preprocessed",
            "recycling_steps": 1,
            "sampling_steps": 20,
            "diffusion_samples": 1,
        },
        str(output_dir),
        job_id="job-bcp-true-default",
    )

    joined = " ".join(cmd)

    assert "--bcp_backend true-distributed-context-parallel" in joined
    assert "--bcp_confidence_prediction false" in joined
    assert "--bcp_input_format preprocessed" in joined
    assert "--bcp_size_cp 4" in joined


def test_boltz_cp_module_true_distributed_path_uses_venv_torchrun_and_diagnostics() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert '"\\$BOLTZ_PYTHON" -m torch.distributed.run' in module_text
    assert 'CUDA_VISIBLE_DEVICES="\\$GPU_IDS_RAW"' in module_text
    assert "export CUDA_DEVICE_ORDER=PCI_BUS_ID" in module_text
    assert "BCP_DATA_PLANE_SEMANTICS=torch.distributed_dtensor_context_parallel" in module_text
    assert "BCP_CONTEXT_STORE_MODE" in module_text
    assert "BCP_CONTEXT_STORE_ROOT" in module_text
    assert "--context_store_root \"\\$BCP_CONTEXT_STORE_ROOT\"" in module_text
    assert "--context_store_mode \"\\$BCP_CONTEXT_STORE_MODE\"" in module_text
    assert '"context_store_mode": os.environ.get("BCP_CONTEXT_STORE_MODE", "")' in module_text
    assert '"context_store_root": os.environ.get("BCP_CONTEXT_STORE_ROOT", "")' in module_text
    assert "torch.distributed_dtensor_pairformer_context_store_evidence" in module_text
    assert "export BCP_CONFIDENCE_PREDICTION BCP_MAX_MSA_SEQS BCP_MAX_PARALLEL_SAMPLES BCP_PRECISION" in module_text
    assert "true_cp_launch_manifest.json" in module_text
    assert "true_cp_failure_diagnostics.json" in module_text
    assert "--no_confidence_prediction" in module_text
    assert "BOLTZ_CACHE_DIR=${cachePath}" in module_text
    assert 'export BOLTZ_CACHE="\\$BOLTZ_CACHE_DIR"' in module_text
    assert '--cache "\\$BOLTZ_CACHE_DIR"' in module_text
    assert "params.get('bcp_triattn_backend', 'reference')" in module_text
    assert "params.get('bcp_triattn_backend', 'trifast')" not in module_text



def test_boltz_cp_true_distributed_path_persists_physical_rank_probe_before_prediction() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    rank_probe_script_marker = 'cat > "\\$TASK_ROOT/true_cp_rank_probe.py"'
    rank_probe_script_index = module_text.index(rank_probe_script_marker)
    rank_probe_launch_index = module_text.index(
        '"\\$TASK_ROOT/true_cp_rank_probe.py"',
        rank_probe_script_index + len(rank_probe_script_marker),
    )
    predict_launch_index = module_text.index("src/boltz/distributed/main.py predict")

    assert rank_probe_script_index < rank_probe_launch_index < predict_launch_index
    assert "true_cp_rank_probe.jsonl" in module_text
    assert "rank_probe_path" in module_text
    assert 'path \'true_cp_rank_probe.jsonl\', emit: rank_probe, optional: true' in module_text
    assert 'path \'true_cp_context_store\', emit: context_store, optional: true' in module_text
    assert 'pattern: \'true_cp_*\'' in module_text
    assert '"rank": os.environ.get("RANK", "")' in module_text
    assert '"local_rank": os.environ.get("LOCAL_RANK", "")' in module_text
    assert '"world_size": os.environ.get("WORLD_SIZE", "")' in module_text
    assert '"cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", "")' in module_text
    assert 'handle.write(json.dumps(payload, sort_keys=True) + "\\\\n")' in module_text



def test_boltz_cp_module_exposes_plan_manifest_and_child_aggregation_processes() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "process BuildBoltzCPPlanManifest" in module_text
    assert "large-protein init-plan" in module_text
    assert "boltz_cp_plan_store.json" in module_text
    assert "physical_launch_size_cp" in module_text
    assert "process SpawnBoltzCPChildren" in module_text
    assert "scripts/spawn_boltz_cp_children.py" in module_text
    assert "bcp_store_root" in module_text
    assert "large-protein run-bundle" in module_text
    assert 'CUDA_VISIBLE_DEVICES="$assigned_gpu"' not in module_text
    assert '--stage "boltz_cp_bundle"' in module_text
    assert "process FinalizeBoltzCPExperimentalChildren" in module_text
    assert "large-protein finalize" in module_text
    assert "bundle_manifests" in module_text
    assert "parent_shard_plan_id" in module_text



def test_boltz_cp_finalize_children_mirrors_store_root_published_artifacts_for_viewer() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "store_published = store_root / 'published'" in module_text
    assert "boltz_cp_result_0" in module_text
    assert "confidence_boltz_cp_result_0.json" in module_text
    assert "published_original" in module_text
    assert "path 'published/*.cif', emit: cifs" in module_text
    assert "path 'published/*.json', emit: jsons" in module_text
    assert "pattern: 'published/*.cif'" in module_text
    assert "pattern: 'published/*.json'" in module_text



def test_boltz_cp_plan_manifest_exports_context_tile_params_without_truncation_artifacts() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "cont..." not in module_text
    assert "BCP_CONTEXT_TILE_TOKENS=${contextTileTokens}" in module_text
    assert "BCP_CONTEXT_KEY_TILE_TOKENS=${contextKeyTileTokens}" in module_text
    assert "BCP_CONTEXT_QUERY_TILE_TOKENS=${contextQueryTileTokens}" in module_text


def test_boltz_cp_true_distributed_predict_threads_triangle_attention_query_tiling_flag() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")
    run_start = module_text.index("process RunBoltzCPExperimental {")
    plan_start = module_text.index("process BuildBoltzCPPlanManifest {")
    run_block = module_text[run_start:plan_start]

    assert "def contextQueryTileTokensValue = (params.get('bcp_context_query_tile_tokens', '512') ?: '').toString().trim()" in run_block
    assert "def contextQueryTileTokens = shellQuote(contextQueryTileTokensValue)" in run_block
    assert "BCP_CONTEXT_QUERY_TILE_TOKENS=${contextQueryTileTokens}" in run_block
    assert "context_triangle_query_tile_enabled = bool(os.environ.get(\"BCP_CONTEXT_QUERY_TILE_TOKENS\", \"\").strip())" in run_block
    assert '"triangle_attention_query_tile_tokens": os.environ.get("BCP_CONTEXT_QUERY_TILE_TOKENS", "").strip()' in run_block
    assert 'triangle_query_tile_flag=(--context_store_triangle_attention_query_tile_tokens "\\$BCP_CONTEXT_QUERY_TILE_TOKENS")' in run_block
    assert '"\\${triangle_query_tile_flag[@]}"' in run_block


def test_boltz_cp_true_distributed_cache_defaults_to_writable_boltz_models_with_task_local_fallback() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "def cachePath = shellQuote(params.get('bcp_cache_path', null) ?: params.get('boltz_models', '') ?: '')" in module_text
    assert 'BOLTZ_CACHE_DIR="\\$TASK_ROOT/boltz_cache"' in module_text
    assert 'mkdir -p "\\$BOLTZ_CACHE_DIR"' in module_text
    assert "def cachePath = shellQuote('/boltzcache')" not in module_text



def test_boltz_cp_true_distributed_cache_falls_back_to_mounted_boltzcache_before_task_local() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert 'if [ -d "/boltzcache" ] && [ -w "/boltzcache" ]; then' in module_text
    assert 'BOLTZ_CACHE_DIR="/boltzcache"' in module_text
    assert "using_mounted_boltzcache_fallback" in module_text
    assert 'else\n            BOLTZ_CACHE_DIR="\\$TASK_ROOT/boltz_cache"' in module_text



def test_boltz_cp_true_distributed_cache_falls_back_when_configured_path_cannot_be_created() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert 'if ! mkdir -p "\\$BOLTZ_CACHE_DIR" 2> "\\$TASK_ROOT/boltz_cache_mkdir.err"; then' in module_text
    assert "configured_boltz_cache_unavailable" in module_text
    assert 'BOLTZ_CACHE_DIR="\\$TASK_ROOT/boltz_cache"' in module_text
    assert 'rm -f "\\$TASK_ROOT/boltz_cache_mkdir.err"' in module_text



def test_boltz_cp_spawn_script_uses_parent_child_job_contract() -> None:
    script_text = (API_ROOT.parents[1] / "scripts" / "spawn_boltz_cp_children.py").read_text(encoding="utf-8")

    assert 'CHILD_STAGE = "boltz_cp_bundle"' in script_text
    assert '"model_id": "boltz_cp_experimental"' in script_text
    assert '"bcp_role": "child"' in script_text
    assert '"bcp_store_root"' in script_text
    assert '"bcp_backend"' in script_text
    assert '"bcp_plan_manifest_path"' in script_text
    assert '"bcp_bundle_id"' in script_text
    assert '"bcp_assigned_gpu"' in script_text
    assert '"batch_index": bundle_index' in script_text
    assert 'apply_child_resume_params' in script_text



def test_boltz_cp_coordinator_does_not_force_disk_store_root_as_configured_ram_root() -> None:
    module_text = (API_ROOT.parents[1] / "modules" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert 'BCP_CONFIGURED_RAM_ROOT' in module_text
    assert '--configured-ram-root "\\$BCP_CONFIGURED_RAM_ROOT"' in module_text
    assert '--configured-ram-root "\\$BCP_STORE_ROOT"' not in module_text



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



def test_build_nextflow_command_normalizes_ion_components_to_boltz_cp_ligands(tmp_path: Path) -> None:
    output_dir = tmp_path / "bcp_complex_with_ions"
    cmd = build_nextflow_command(
        "boltz_cp_experimental",
        "design",
        {
            "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
            "sequence_name": "cp_complex_ions_case",
            "complex_components": [
                {"type": "protein", "id": "A", "sequence": "MKTIIALSYIFCLVFADYKDDDDA"},
                {"type": "ligand", "id": "D", "ccd": "ATP", "name": "ATP"},
                {"type": "ion", "id": "E", "ccd": "MG", "name": "Magnesium 1"},
                {"type": "ion", "id": "F", "ccd": "MG", "name": "Magnesium 2"},
            ],
            "pinned_gpus": [0, 1, 2, 3],
        },
        str(output_dir),
        job_id="job-bcp-complex-ions",
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
                    "sequence": "MKTIIALSYIFCLVFADYKDDDDA",
                    "msa": "empty",
                }
            },
            {
                "ligand": {
                    "id": ["D"],
                    "ccd": "ATP",
                    "name": "ATP",
                }
            },
            {
                "ligand": {
                    "id": ["E"],
                    "ccd": "MG",
                    "name": "Magnesium 1",
                }
            },
            {
                "ligand": {
                    "id": ["F"],
                    "ccd": "MG",
                    "name": "Magnesium 2",
                }
            },
        ],
    }
