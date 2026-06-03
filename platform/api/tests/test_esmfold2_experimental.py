from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Design, Job  # noqa: E402
from model_registry import ModelRegistry  # noqa: E402
from routers.models import router as models_router  # noqa: E402
from routers.templates import router as templates_router  # noqa: E402
from services.nextflow import WORKFLOW_ENTRYPOINTS, build_nextflow_command, resolve_nextflow_entrypoint  # noqa: E402
from services.result_ingester import ingest_job_results  # noqa: E402
from template_registry import TemplateRegistry  # noqa: E402


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


async def _build_session_factory(tmp_path: Path) -> tuple[sessionmaker, object]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'esmfold2_ingest.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


def _write_minimal_esmfold2_cif(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "data_rnaseh_000\n"
        "#\n"
        "loop_\n"
        "_atom_site.group_PDB\n"
        "_atom_site.id\n"
        "_atom_site.type_symbol\n"
        "_atom_site.label_atom_id\n"
        "_atom_site.label_comp_id\n"
        "_atom_site.label_asym_id\n"
        "_atom_site.label_seq_id\n"
        "_atom_site.Cartn_x\n"
        "_atom_site.Cartn_y\n"
        "_atom_site.Cartn_z\n"
        "_atom_site.B_iso_or_equiv\n"
        "ATOM 1 C CA MET A 1 0.0 0.0 0.0 71.0\n"
        "ATOM 2 C CA GLY A 2 1.0 0.0 0.0 72.0\n"
        "ATOM 3 P P A B 1 2.0 0.0 0.0 26.0\n"
        "#\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_esmfold2_result_ingestion_uses_manifest_metrics_without_fampnn_synthesis(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    output_root = tmp_path / "rnaseh_esmfold2_full"
    final_root = output_root / "final" / "esmfold2"
    cif_path = final_root / "rnaseh_000.cif"
    metrics_path = final_root / "rnaseh_000.metrics.json"
    manifest_path = final_root / "manifest.json"
    _write_minimal_esmfold2_cif(cif_path)
    metrics_payload = {
        "sample_id": "rnaseh_000",
        "sequence_name": "rnaseh",
        "sequence_length": 539,
        "total_polymer_residues": 677,
        "component_count": 2,
        "components": [
            {"type": "protein", "id": "A", "sequence": "MG", "name": "protein"},
            {"type": "rna", "id": "B", "sequence": "ACGU", "name": "ncRNA"},
        ],
        "model_variant": "full",
        "model_id_or_path": "biohub/ESMFold2",
        "local_files_only": True,
        "num_loops": 5,
        "num_sampling_steps": 100,
        "num_diffusion_samples": 1,
        "plddt_mean": 0.6226916909217834,
        "ptm": 0.74803227186203,
        "iptm": 0.15889352560043335,
        "cif": "rnaseh_000.cif",
    }
    metrics_path.write_text(json.dumps(metrics_payload), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workflow": "esmfold2_experimental",
                "sample_count": 1,
                "sequence_name": "rnaseh",
                "model_variant": "full",
                "model_id_or_path": "biohub/ESMFold2",
                "local_files_only": True,
                "samples": [{"sample_id": "rnaseh_000", "cif": "rnaseh_000.cif", "metrics": "rnaseh_000.metrics.json"}],
            }
        ),
        encoding="utf-8",
    )

    async with session_factory() as session:
        session.add(
            Job(
                id="job-esmf2-ingest",
                name="rnaseh-esmfold2-full",
                model_id="esmfold2_experimental",
                mode="predict",
                params={"model_variant": "full", "sequence_name": "rnaseh"},
                output_dir=str(output_root),
                status="completed",
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        created = await ingest_job_results("job-esmf2-ingest", str(output_root), session)
        assert created == 1

        designs = (await session.execute(select(Design).where(Design.job_id == "job-esmf2-ingest"))).scalars().all()
        assert len(designs) == 1
        design = designs[0]
        assert design.name == "rnaseh_000"
        assert Path(design.pdb_path) == cif_path
        assert design.json_path == str(metrics_path)
        assert design.stage_family == "esmfold2"
        assert design.stage_mode == "predict"
        assert design.provenance["artifact_group"] == "esmfold2"
        assert design.provenance["model_id"] == "esmfold2_experimental"
        assert design.provenance["model_variant"] == "full"
        assert design.provenance["model_id_or_path"] == "biohub/ESMFold2"
        assert design.confidence_metrics is not None
        assert "esmfold2" in design.confidence_metrics
        assert "fampnn" not in design.confidence_metrics
        assert design.fampnn_psce is None
        assert design.mpnn_score is None
        assert design.plddt_overall == pytest.approx(62.26916909217834)
        assert design.ptm == pytest.approx(0.74803227186203)
        assert design.iptm == pytest.approx(0.15889352560043335)

    await engine.dispose()


@pytest.mark.asyncio
async def test_esmfold2_ingestion_does_not_fall_through_to_loose_fampnn_synthesis(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    output_root = tmp_path / "partial_esmfold2"
    loose_structure = output_root / "pdb_files" / "partial_000.cif"
    _write_minimal_esmfold2_cif(loose_structure)

    async with session_factory() as session:
        session.add(
            Job(
                id="job-esmf2-partial",
                name="partial-esmfold2-output",
                model_id="esmfold2_experimental",
                mode="predict",
                params={"model_variant": "fast"},
                output_dir=str(output_root),
                status="completed",
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        created = await ingest_job_results("job-esmf2-partial", str(output_root), session)
        assert created == 0
        designs = (await session.execute(select(Design).where(Design.job_id == "job-esmf2-partial"))).scalars().all()
        assert designs == []

    await engine.dispose()


def test_model_registry_loads_standalone_esmfold2_experimental() -> None:
    registry = ModelRegistry()

    model = registry.get_model("esmfold2_experimental")

    assert model is not None
    assert model.name == "ESMFold2 Experimental"
    assert model.category == "structure_prediction"
    assert model.experimental is True
    assert model.container == "esmfold2.sif"
    assert any(mode.id == "predict" for mode in model.modes)
    assert any(param.name == "sequence" and param.required is False for param in model.params)
    assert any(param.name == "model_variant" and param.default == "fast" for param in model.params)
    assert any(param.name == "local_files_only" and param.default is True for param in model.params)
    assert any(param.name == "quality_preset" and param.default == "standard" for param in model.params)
    model_path_param = next(param for param in model.params if param.name == "model_id_or_path")
    assert model_path_param.default in {None, ""}
    assert any(param.name == "device" and param.default == "auto" for param in model.params)
    for required_gap_param in {
        "pdb_sequence_path",
        "pdb_chain_ids",
        "msa_path",
        "msa_format",
        "dna_sequence",
        "rna_sequence",
        "ligand_smiles",
        "ligand_ccd",
        "complex_components_json",
    }:
        assert any(param.name == required_gap_param for param in model.params), required_gap_param

    assert registry.validate_job_params("esmfold2_experimental", "predict", {"sequence": "MQIFVKTLTGKT"}) == []
    assert registry.validate_job_params("esmfold2_experimental", "predict", {}) == []
    assert registry.validate_job_params(
        "esmfold2_experimental",
        "predict",
        {"sequence": "MQIFVKTLTGKT", "model_variant": "msa"},
    ) == ["Invalid value for model_variant: must be one of ['fast', 'full']"]


def test_template_registry_loads_structure_launcher_esmf2_variant_without_generic_user_params() -> None:
    registry = TemplateRegistry(API_ROOT / "config" / "templates")

    template = registry.get_template("esmfold2_experimental")

    assert template is not None
    assert template.name == "ESMFold2 Experimental"
    assert template.experimental is True
    assert template.preset_params == {
        "template_model_id": "esmfold2_experimental",
        "template_mode_id": "predict",
        "structure_launch_variant": "esmfold2_experimental",
        "model_variant": "fast",
    }
    assert template.user_params == [], (
        "ESMFold2 must reuse StructurePredictionTemplate inputs instead of rendering a duplicate generic form"
    )
    assert not any(key.startswith("esmf_") for key in template.preset_params), (
        "prefixed ESMFold2 defaults in preset_params shadow canonical structure-launcher controls"
    )


def test_esmfold2_model_config_keeps_runtime_contract_while_template_delegates_ui_to_structure_launcher() -> None:
    model_registry = ModelRegistry()
    template_registry = TemplateRegistry(API_ROOT / "config" / "templates")
    model = model_registry.get_model("esmfold2_experimental")
    template = template_registry.get_template("esmfold2_experimental")
    assert model is not None
    assert template is not None

    model_param_names = {param.name for param in model.params}
    for required_runtime_param in {
        "sequence",
        "sequence_name",
        "chain_id",
        "pdb_sequence_path",
        "pdb_chain_ids",
        "dna_sequence",
        "rna_sequence",
        "ligand_smiles",
        "complex_components_json",
        "model_variant",
        "local_files_only",
        "num_loops",
        "num_sampling_steps",
        "num_diffusion_samples",
        "device",
    }:
        assert required_runtime_param in model_param_names, required_runtime_param

    assert [param.name for param in template.user_params] == []
    assert template.preset_params["structure_launch_variant"] == "esmfold2_experimental"


def test_esmfold2_experimental_routes_as_direct_standalone_entrypoint(tmp_path) -> None:
    assert WORKFLOW_ENTRYPOINTS["esmfold2_experimental"] == "workflows/esmfold2_experimental.nf"
    assert resolve_nextflow_entrypoint(effective_profile="esmfold2_experimental") == "workflows/esmfold2_experimental.nf"

    cmd = build_nextflow_command(
        "esmfold2_experimental",
        "predict",
        {
            "sequence": "MQIFVKTLTGKTITLEVEPSDTI",
            "sequence_name": "ubiquitin_fragment",
            "model_variant": "fast",
            "num_loops": 3,
            "num_sampling_steps": 50,
            "num_diffusion_samples": 1,
            "local_files_only": True,
        },
        str(tmp_path / "single"),
        job_id="job-esmf2",
    )

    assert cmd[:4] == ["nextflow", "run", "workflows/esmfold2_experimental.nf", "-profile"]
    assert _flag_value(cmd, "-profile") == "esmfold2_experimental,workstation_ryzen7960x"
    assert _flag_value(cmd, "--esmf_sequence") == "MQIFVKTLTGKTITLEVEPSDTI"
    assert _flag_value(cmd, "--esmf_sequence_name") == "ubiquitin_fragment"
    assert _flag_value(cmd, "--esmf_model_variant") == "fast"
    assert _flag_value(cmd, "--esmf_model_id_or_path") == "biohub/ESMFold2-Fast"
    assert _flag_value(cmd, "--esmf_num_loops") == "3"
    assert _flag_value(cmd, "--esmf_num_sampling_steps") == "50"
    assert _flag_value(cmd, "--esmf_num_diffusion_samples") == "1"
    assert _flag_value(cmd, "--esmf_local_files_only") == "true"
    assert _flag_value(cmd, "--esmf_device") == "auto"
    assert "--sequence_input" not in cmd

    complex_cmd = build_nextflow_command(
        "esmfold2_experimental",
        "predict",
        {
            "sequence": "",
            "sequence_name": "complex_from_ui",
            "pdb_sequence_path": "/inputs/4h8k.pdb",
            "pdb_chain_ids": "A,C,D",
            "msa_path": "/inputs/ubiquitin.a3m",
            "msa_format": "a3m",
            "msa_max_sequences": 512,
            "msa_remove_insertions": True,
            "dna_sequence": "GGAATCAGGTGTCG",
            "dna_chain_id": "D",
            "rna_sequence": "CGACACCUGAUUCC",
            "rna_chain_id": "C",
            "ligand_smiles": "CCO",
            "ligand_chain_id": "L",
            "complex_components_json": '[{"type":"protein","id":"B","sequence":"MQIFVK"}]',
        },
        str(tmp_path / "complex_json"),
        job_id="job-esmf2-complex",
    )

    assert _flag_value(complex_cmd, "--esmf_pdb_sequence_path") == "/inputs/4h8k.pdb"
    assert _flag_value(complex_cmd, "--esmf_pdb_chain_ids") == "A,C,D"
    assert _flag_value(complex_cmd, "--esmf_msa_path") == "/inputs/ubiquitin.a3m"
    assert _flag_value(complex_cmd, "--esmf_msa_format") == "a3m"
    assert _flag_value(complex_cmd, "--esmf_msa_max_sequences") == "512"
    assert _flag_value(complex_cmd, "--esmf_msa_remove_insertions") == "true"
    assert _flag_value(complex_cmd, "--esmf_dna_sequence") == "GGAATCAGGTGTCG"
    assert _flag_value(complex_cmd, "--esmf_rna_sequence") == "CGACACCUGAUUCC"
    assert _flag_value(complex_cmd, "--esmf_ligand_smiles") == "CCO"
    assert _flag_value(complex_cmd, "--esmf_complex_components_json").startswith('[{"type":"protein"')
    assert "--sequence_input" not in complex_cmd

    canonical_complex_cmd = build_nextflow_command(
        "esmfold2_experimental",
        "predict",
        {
            "sequence": "MQIFVKTLTGKTITLEVEPSDTI",
            "sequence_name": "canonical_complex",
            "pred_method": "esmfold2",
            "primary_chain_id": "A",
            "target_chains": "A",
            "binder_chains": "B",
            "model_variant": "full",
            "complex_components": [
                {"type": "protein", "id": "A", "sequence": "MQIFVKTLTGKTITLEVEPSDTI", "name": "primary"},
                {"type": "protein", "id": "B", "sequence": "GSGSGS", "name": "binder"},
                {"type": "dna", "id": "C", "sequence": "GGAATCAGGTGTCG"},
                {"type": "ligand", "id": "L", "smiles": "CCO"},
            ],
        },
        str(tmp_path / "canonical_complex"),
        job_id="job-esmf2-canonical",
    )

    components_path = Path(_flag_value(canonical_complex_cmd, "--esmf_complex_components_file"))
    assert components_path.exists()
    components_payload = components_path.read_text(encoding="utf-8")
    assert '"id": "A"' in components_payload
    assert '"id": "B"' in components_payload
    assert _flag_value(canonical_complex_cmd, "--esmf_model_variant") == "full"
    assert _flag_value(canonical_complex_cmd, "--esmf_model_id_or_path") == "biohub/ESMFold2"
    assert "--esmf_sequence" not in canonical_complex_cmd
    assert "--complex_json_path" not in canonical_complex_cmd
    assert "--pred_method" not in canonical_complex_cmd
    assert "--primary_chain_id" not in canonical_complex_cmd
    assert "--target_chains" not in canonical_complex_cmd
    assert "--binder_chains" not in canonical_complex_cmd


def test_esmfold2_visible_ui_params_override_legacy_prefixed_defaults() -> None:
    cmd = build_nextflow_command(
        "esmfold2_experimental",
        "predict",
        {
            "sequence": "MQIFVKTLTGKTITLEVEPSDTI",
            "sequence_name": "ui_override",
            "model_variant": "full",
            "model_id_or_path": "",
            "local_files_only": False,
            "num_loops": 5,
            "num_sampling_steps": 75,
            "num_diffusion_samples": 2,
            "seed": 123,
            "device": "cuda",
            # Legacy/stale prefixed defaults that used to shadow the visible UI controls.
            "esmf_model_variant": "fast",
            "esmf_model_id_or_path": "biohub/ESMFold2-Fast",
            "esmf_local_files_only": True,
        },
        "/tmp/bms-esmfold2-out",
        job_id="job-esmf2-ui",
    )

    assert _flag_value(cmd, "--esmf_model_variant") == "full"
    assert _flag_value(cmd, "--esmf_model_id_or_path") == "biohub/ESMFold2"
    assert _flag_value(cmd, "--esmf_local_files_only") == "false"
    assert _flag_value(cmd, "--esmf_num_loops") == "5"
    assert _flag_value(cmd, "--esmf_num_sampling_steps") == "75"
    assert _flag_value(cmd, "--esmf_num_diffusion_samples") == "2"
    assert _flag_value(cmd, "--esmf_seed") == "123"
    assert _flag_value(cmd, "--esmf_device") == "cuda"

    cloned_legacy_cmd = build_nextflow_command(
        "esmfold2_experimental",
        "predict",
        {
            "sequence": "MQIFVKTLTGKTITLEVEPSDTI",
            "model_variant": "full",
            # A pre-fix cloned/template payload may carry the old Fast prefixed default
            # without a blank model_id_or_path UI field. Variant selection still wins.
            "esmf_model_id_or_path": "biohub/ESMFold2-Fast",
        },
        "/tmp/bms-esmfold2-out",
        job_id="job-esmf2-ui-legacy",
    )
    assert _flag_value(cloned_legacy_cmd, "--esmf_model_variant") == "full"
    assert _flag_value(cloned_legacy_cmd, "--esmf_model_id_or_path") == "biohub/ESMFold2"

    preset_cmd = build_nextflow_command(
        "esmfold2_experimental",
        "predict",
        {
            "sequence": "MQIFVKTLTGKTITLEVEPSDTI",
            "quality_preset": "thorough",
        },
        "/tmp/bms-esmfold2-out",
        job_id="job-esmf2-ui-preset",
    )
    assert _flag_value(preset_cmd, "--esmf_num_loops") == "5"
    assert _flag_value(preset_cmd, "--esmf_num_sampling_steps") == "100"
    assert _flag_value(preset_cmd, "--esmf_num_diffusion_samples") == "2"


def test_esmfold2_experimental_static_runtime_contract() -> None:
    workflow_text = (REPO_ROOT / "workflows" / "esmfold2_experimental.nf").read_text(encoding="utf-8")
    module_text = (REPO_ROOT / "modules" / "esmfold2_experimental.nf").read_text(encoding="utf-8")
    nextflow_config = (REPO_ROOT / "nextflow.config").read_text(encoding="utf-8")
    runner_text = (REPO_ROOT / "scripts" / "run_esmfold2_inference.py").read_text(encoding="utf-8")
    apptainer_def = (REPO_ROOT / "apptainer" / "esmfold2.def").read_text(encoding="utf-8")
    main_text = (REPO_ROOT / "main.nf").read_text(encoding="utf-8")

    assert "workflow ESMFOLD2_EXPERIMENTAL" in workflow_text
    assert "workflow {" in workflow_text
    assert "ESMFold2 Experimental Workflow" in workflow_text
    assert "params.esmf_" not in workflow_text
    assert "label 'ESMFold2'" in module_text
    assert "run_esmfold2_inference.py" in module_text
    assert "bms_gpu_run_telemetry.py" in module_text
    assert "bms_run_telemetry_RunESMFold2Experimental.json" in module_text
    assert "${params.out_dir}/run/telemetry" in module_text
    assert "path 'bms_run_telemetry_*.json'" in module_text
    assert "--label RunESMFold2Experimental" in module_text
    assert "-- python3 /scripts/run_esmfold2_inference.py" in module_text
    assert "cp -R esmfold2_results/." in module_text
    assert "pdb_files" in module_text
    assert "params.esmf_" not in module_text
    assert "esmfold2_experimental {" in nextflow_config
    assert "withLabel: ESMFold2" in nextflow_config
    assert "${params.container_dir}/esmfold2.sif" in nextflow_config
    assert "HF_HOME=/weights/esmfold2/hf_home" in nextflow_config
    assert "HF_HUB_CACHE=/weights/esmfold2/hf_home/hub" in nextflow_config
    assert "TRANSFORMERS_CACHE=/weights/esmfold2/hf_home/hub" in nextflow_config
    assert "BMS_ESMFOLD2_MODEL" in nextflow_config
    assert "ESMFold2Model.from_pretrained" in runner_text
    assert "local_files_only=args.local_files_only" in runner_text
    assert "StructurePredictionInput" in runner_text
    assert "DNAInput" in runner_text
    assert "RNAInput" in runner_text
    assert "LigandInput" in runner_text
    assert "from_a3m" in runner_text
    assert "parse_pdb_polymer_components" in runner_text
    assert "--complex-components-json" in runner_text
    assert "--pdb-sequence-path" in module_text
    assert "--msa-path" in module_text
    assert "biohub.ai/developer-console" not in workflow_text + module_text + runner_text
    assert "ESM_API_KEY" not in workflow_text + module_text + runner_text
    assert "Biohub/esm.git@c94ed8d763bbd7088b296949e5b401e8ea12073a" in apptainer_def
    assert "BiohubTransformersCommit 3a8956fb4d4ea16b0ec8e71deef2c2909b6a5cbf" in apptainer_def
    assert "HF_HOME=/weights/esmfold2/hf_home" in apptainer_def
    assert "esmfold2_experimental" not in main_text


def test_esmfold2_experimental_live_api_contract_if_routers_are_loaded() -> None:
    app = FastAPI()
    app.include_router(models_router, prefix="/api/models")
    app.include_router(templates_router, prefix="/api/templates")
    client = TestClient(app)

    models_payload = client.get("/api/models", params={"include_experimental": "true"}).json()
    templates_payload = client.get("/api/templates").json()

    models = models_payload.get("data", models_payload) if isinstance(models_payload, dict) else models_payload
    templates = templates_payload.get("data", templates_payload) if isinstance(templates_payload, dict) else templates_payload
    model = next((item for item in models if item["id"] == "esmfold2_experimental"), None)
    template = next((item for item in templates if item["id"] == "esmfold2_experimental"), None)

    assert model is not None
    assert model["experimental"] is True
    assert template is not None
    assert template["experimental"] is True

    detail = client.get("/api/templates/esmfold2_experimental").json()
    assert detail["preset_params"] == {
        "template_model_id": "esmfold2_experimental",
        "template_mode_id": "predict",
        "structure_launch_variant": "esmfold2_experimental",
        "model_variant": "fast",
    }
    assert detail["user_params"] == []


def test_esmfold2_runner_parses_pdb_and_component_json_without_runtime_imports(tmp_path) -> None:
    import importlib.util

    runner_path = REPO_ROOT / "scripts" / "run_esmfold2_inference.py"
    spec = importlib.util.spec_from_file_location("bms_esmfold2_runner_for_test", runner_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pdb_path = tmp_path / "rnaseh_fragment.pdb"
    pdb_path.write_text(
        "\n".join([
            "ATOM      1  N   MET A   1      11.104  13.207   2.100  1.00 20.00           N",
            "ATOM      2  CA  MET A   1      12.000  13.800   2.500  1.00 20.00           C",
            "ATOM      3  N   GLY A   2      13.104  14.207   3.100  1.00 20.00           N",
            "ATOM      4  P     G D   1      15.104  14.207   3.100  1.00 20.00           P",
            "ATOM      5  P     T D   2      16.104  15.207   3.100  1.00 20.00           P",
            "ATOM      6  P     C C   1      17.104  16.207   3.100  1.00 20.00           P",
            "ATOM      7  P     U C   2      18.104  17.207   3.100  1.00 20.00           P",
        ]) + "\n",
        encoding="utf-8",
    )

    components = module.parse_pdb_polymer_components(pdb_path, chain_ids="A,C,D")
    assert components == [
        {"type": "protein", "id": "A", "sequence": "MG", "source": str(pdb_path)},
        {"type": "rna", "id": "C", "sequence": "CU", "source": str(pdb_path)},
        {"type": "dna", "id": "D", "sequence": "GT", "source": str(pdb_path)},
    ]

    parsed = module.parse_components_json('{"components":[{"type":"protein","id":"B","sequence":"MQIF"},{"type":"ligand","id":"L","smiles":"CCO"}]}')
    assert parsed[0]["type"] == "protein"
    assert parsed[1]["smiles"] == "CCO"
    assert module.normalize_sequence(" mqifvkt ") == "MQIFVKT"
    assert module.normalize_component_sequence("acgu", "rna") == "ACGU"
    assert module.normalize_component_sequence("acgu", "dna") == "ACGT"
    assert module.sanitize_mmcif_data_block_id("RCSB: 3KTQ") == "RCSB_3KTQ"
    assert module.sanitize_mmcif_data_block_id("  weird label / with spaces ") == "weird_label_with_spaces"
    unsafe_cif = "data_RCSB: 3KTQ\n#\nloop_\n_atom_site.id\n1\n"
    safe_cif = module.ensure_safe_mmcif_data_block(unsafe_cif, "RCSB: 3KTQ_000")
    assert safe_cif.splitlines()[0] == "data_RCSB_3KTQ_000"
    assert "data_RCSB: 3KTQ" not in safe_cif
