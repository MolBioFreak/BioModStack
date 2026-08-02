from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

for candidate in (str(API_ROOT), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from model_registry import ModelRegistry  # noqa: E402
from services.nextflow import (  # noqa: E402
    build_nextflow_command,
    resolve_nextflow_entrypoint,
)
from services.result_contracts import resolve_result_contract  # noqa: E402
from scripts.bms_md import spawn_replicas as spawn_replicas_module  # noqa: E402


def _flag_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_registry_exposes_engine_neutral_molecular_dynamics_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_FEATURE_MOLECULAR_DYNAMICS", "1")
    model = ModelRegistry().get_model("molecular_dynamics")

    assert model is not None
    assert model.category == "molecular_dynamics"
    assert model.container == "gromacs-md-2025.3.sif"
    assert model.engine_containers == {
        "gromacs": "gromacs-md-2025.3.sif",
        "openmm": "openmm-md-8.5.2.sif",
    }
    assert model.capabilities["accelerator"] == {
        "vendor": "nvidia",
        "api": "cuda",
        "count_per_replica": 1,
    }
    assert "amber_system_xml" not in model.capabilities["input_formats"]
    assert [mode.id for mode in model.modes] == ["simulate", "replica", "analyze"]
    params = {parameter.name: parameter for parameter in model.params}
    assert params["md_job_spec"].required is True
    assert params["md_job_spec"].type == "object"
    assert params["md_job_config"].required is False
    assert params["md_job_config"].file_type == "json"
    assert params["md_analysis_work_item"].required is False
    assert params["md_analysis_work_item"].file_type == "json"


def test_api_routes_md_job_to_bounded_experimental_workflow() -> None:
    entrypoint = "workflows/experimental/molecular_dynamics/orchestrator.nf"
    assert resolve_nextflow_entrypoint(
        effective_profile="molecular_dynamics", model_id="molecular_dynamics", mode="simulate", params={}
    ) == entrypoint
    command = build_nextflow_command(
        model_id="molecular_dynamics", mode="simulate",
        params={"md_job_config": "/tmp/md-job.json", "gpu_id": 2},
        output_dir="/tmp/md-results", job_id="job-md-1",
    )
    assert command[command.index("run") + 1] == entrypoint
    assert _flag_value(command, "-profile") == "molecular_dynamics_coordinator,workstation_ryzen7960x"
    assert _flag_value(command, "--md_job_config") == "/tmp/md-job.json"
    assert _flag_value(command, "--md_input_root") == "/tmp"
    assert _flag_value(command, "--gpu_id") == "2"
    assert _flag_value(command, "--job_id") == "job-md-1"

    analysis = build_nextflow_command(
        model_id="molecular_dynamics",
        mode="analyze",
        params={
            "md_analysis_work_item": "/tmp/parent/orchestration/analysis_work_items/replica_0.json",
            "md_analysis_sif_sha256": "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68",
        },
        output_dir="/tmp/analysis-child",
        job_id="job-md-analysis-1",
    )
    assert analysis[analysis.index("run") + 1] == "workflows/experimental/molecular_dynamics/analyze.nf"
    assert _flag_value(analysis, "-profile") == "molecular_dynamics_analysis,workstation_ryzen7960x"
    assert _flag_value(analysis, "--md_analysis_work_item").endswith("replica_0.json")
    assert "--gpu_id" not in analysis

    with pytest.raises(ValueError, match="CPU-only"):
        build_nextflow_command(
            model_id="molecular_dynamics",
            mode="analyze",
            params={"md_analysis_work_item": "/tmp/item.json", "gpu_id": 0},
            output_dir="/tmp/analysis-child",
            job_id="job-md-analysis-invalid",
        )


def test_md_workflow_uses_bounded_singleton_entrypoints() -> None:
    workflow_root = REPO_ROOT / "workflows" / "experimental" / "molecular_dynamics"
    module_root = REPO_ROOT / "modules" / "experimental" / "molecular_dynamics"
    for name in ("prepare.nf", "replica.nf", "finalize.nf", "workflow.nf", "orchestrator.nf"):
        assert (workflow_root / name).is_file()
    for name in ("prepare.nf", "gromacs_replica.nf", "openmm_replica.nf", "finalize.nf"):
        assert (module_root / name).is_file()

    prepare_entrypoint = (workflow_root / "prepare.nf").read_text(encoding="utf-8")
    assert "MD_PREPARE_CONFIG" in prepare_entrypoint
    assert "params.gpu_id" not in prepare_entrypoint

    replica_entrypoint = (workflow_root / "replica.nf").read_text(encoding="utf-8")
    assert "params.md_replica_index" in replica_entrypoint
    assert "params.md_preparation_bundle" in replica_entrypoint
    assert "params.gpu_id" in replica_entrypoint
    assert "tuple(params.md_replica_index as int, config, bundle)" in replica_entrypoint
    assert ".flatten" not in replica_entrypoint

    orchestrator = (workflow_root / "orchestrator.nf").read_text(encoding="utf-8")
    assert "scripts.bms_md.spawn_replicas" in orchestrator
    assert "scripts/wait_for_children.py" in orchestrator
    assert orchestrator.count("--expected_children ${spawn_result}") == 2
    assert "--batch_name" not in orchestrator
    assert "scripts.bms_md.aggregate_children" in orchestrator
    assert "MD_ASSERT_REPLICA_OUTCOME" in orchestrator
    assert "MD_GROMACS_REPLICA" not in orchestrator
    assert "MD_ANALYZE_REPLICA" not in orchestrator
    ordered_tokens = [
        "scripts.bms_md.spawn_replicas",
        "--stage md_replica",
        "scripts.bms_md.aggregate_children",
        "MD_ASSERT_REPLICA_OUTCOME",
        "scripts.bms_md.spawn_analysis",
        "--stage md_analysis",
        "scripts.bms_md.collect_analysis",
        "MD_ASSERT_ANALYSIS_OUTCOME",
        "MD_COMPLETION_BARRIER",
    ]
    offsets = [orchestrator.index(token) for token in ordered_tokens]
    assert offsets == sorted(offsets)

    analyze_entrypoint = (workflow_root / "analyze.nf").read_text(encoding="utf-8")
    assert "params.md_analysis_work_item" in analyze_entrypoint
    assert "Channel.fromPath(params.md_analysis_work_item" in analyze_entrypoint
    assert ".flatMap" not in analyze_entrypoint
    assert "params.gpu_id" not in analyze_entrypoint

    gromacs_module = (module_root / "gromacs_replica.nf").read_text(encoding="utf-8")
    openmm_module = (module_root / "openmm_replica.nf").read_text(encoding="utf-8")
    assert "process MD_GROMACS_REPLICA" in gromacs_module
    assert "process MD_OPENMM_REPLICA" in openmm_module
    assert "label 'MolecularDynamicsGromacs'" in gromacs_module
    assert "label 'MolecularDynamicsOpenMM'" in openmm_module
    assert "python3 -m scripts.bms_md.cli run" in gromacs_module
    assert "python3 -m scripts.bms_md.cli run" in openmm_module
    for replica_module in (gromacs_module, openmm_module):
        assert "python3 -m scripts.bms_md.cli validate" in replica_module
        assert "--gpu-id 0" in replica_module
        assert '--scheduler-gpu-id "${params.gpu_id}"' in replica_module
        assert "--config runtime_config.json" in replica_module
    assert "preparation_manifest.json" in gromacs_module
    assert '["preparation"]["gromacs_gpu_offload"]' in gromacs_module
    assert '--gpu-offload "\\${runtime_gpu_offload}"' in gromacs_module


def test_md_replica_spawn_pins_the_scheduler_to_the_requested_physical_gpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "normalized_config.json"
    config.write_text(
        json.dumps(
            {
                "engine": "gromacs",
                "engine_runtime": {"sif_sha256": "a" * 64},
                "chemistry": {"profile_id": "accepted"},
                "protocol": {},
                "input": {"structure_sha256": "b" * 64},
                "random_seed": 20260801,
                "execution": {"gpu_id": "2"},
            }
        ),
        encoding="utf-8",
    )
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"replicas": 1, "engine": "gromacs"}), encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    posted: list[dict[str, object]] = []

    class Response:
        ok = True
        status_code = 201
        text = ""

        @staticmethod
        def json() -> dict[str, str]:
            return {"id": "child", "name": "replica", "status": "queued"}

    def fake_post(_url: str, *, json: dict[str, object], timeout: int) -> Response:
        assert timeout == 30
        posted.append(json)
        return Response()

    monkeypatch.setattr(spawn_replicas_module.requests, "post", fake_post)
    spawn_replicas_module.spawn_replicas(
        parent_job_id="parent",
        parent_name="md",
        normalized_config=config,
        metadata_path=metadata,
        preparation_bundle=bundle,
        api_url="http://api",
    )

    assert posted[0]["pinned_gpu"] == 2


def test_md_nextflow_profile_propagates_the_feature_gate_into_containers() -> None:
    config = (REPO_ROOT / "nextflow.config").read_text(encoding="utf-8")
    assert "def mdFeatureFlag = System.getenv('BMS_FEATURE_MOLECULAR_DYNAMICS') ?: '0'" in config
    assert config.count("--env BMS_FEATURE_MOLECULAR_DYNAMICS=${mdFeatureFlag}") == 5
    assert "molecular_dynamics_experimental" in config
    assert "withLabel: MolecularDynamicsGromacs" in config
    assert "withLabel: MolecularDynamicsOpenMM" in config
    assert "ext.accelerator_count = 1" in config


def test_openmm_image_definition_pins_a_cuda_capable_exact_runtime() -> None:
    dockerfile = (REPO_ROOT / "containers" / "openmm-md" / "Dockerfile").read_text(encoding="utf-8")
    assert "OPENMM_VERSION=8.5.2" in dockerfile
    assert "MAMBA_VERSION=2.5.0" in dockerfile
    assert '"cuda-version=13.0"' in dockerfile
    assert '"gromacs=2025.3"' in dockerfile
    assert "OPENMM_GROMACS_INCLUDE_DIR=/opt/conda/share/gromacs/top" in dockerfile
    assert "OPENMM_DEFAULT_PLATFORM=CUDA" in dockerfile
    openmm_pipeline = (REPO_ROOT / "scripts" / "bms_md" / "openmm_pipeline.py").read_text(encoding="utf-8")
    assert "OPENMM_GROMACS_INCLUDE_DIR" in openmm_pipeline
    assert "includeDir=include_dir" in openmm_pipeline


def test_md_is_not_advertised_as_a_design_result_contract() -> None:
    contract = resolve_result_contract(provenance={"model_id": "molecular_dynamics"})

    assert contract.analysis_contract_id is None
    assert contract.supported_analyzers == []
    assert contract.viewer_capabilities == []
