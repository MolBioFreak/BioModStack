from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


from antibody_pipeline_contract import (  # noqa: E402
    ANTIBODY_DENOVO_PIPELINE,
    ANTIBODY_REFINEMENT_PIPELINE,
)
from services.nextflow import (  # noqa: E402
    COMPLEX_PREDICTION_ENTRYPOINT,
    DEFAULT_WORKFLOW_ENTRYPOINT,
    LEGACY_MAIN_ENTRYPOINT,
    MODEL_MODE_WORKFLOW_ENTRYPOINTS,
    STRUCTURE_PREDICTION_ENTRYPOINT,
    WORKFLOW_ENTRYPOINTS,
    build_nextflow_command,
    resolve_nextflow_entrypoint,
)


EXPECTED_PROFILE_ENTRYPOINTS = {
    "oligo_design": "workflows/oligo_design.nf",
    "ont_basecall_dna": "workflows/ngs/ont_basecall_dna.nf",
    "ont_basecall_rna": "workflows/ngs/ont_basecall_rna.nf",
    "ont_plasmid_qc": "workflows/ngs/ont_plasmid_qc.nf",
    "ont_construct_screening": "workflows/ngs/ont_construct_screening.nf",
    "ont_methylation_analysis": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_fastq_qc": "workflows/ngs/ont_fastq_qc.nf",
    "wf_clone_validation": "workflows/ngs/wf_clone_validation.nf",
    "protein_local_redesign": "workflows/protein_local_redesign.nf",
    "protein_cad_experimental": "workflows/protein_cad_experimental.nf",


    "boltz_cp_experimental": "workflows/boltz_cp_experimental.nf",
    "confornets_experimental": "workflows/confornets_experimental.nf",
    "conformational_mapping": "workflows/conformational_mapping.nf",
    "molecular_dynamics": "workflows/experimental/molecular_dynamics/orchestrator.nf",

    "ppiflow_generator": "workflows/ppiflow_generator_design.nf",
    "antibody_child": "workflows/antibody_child.nf",
    "antibody_backbone": "workflows/rfantibody_backbone.nf",
    "maturation_child": "workflows/maturation_child.nf",

    "docking": "workflows/docking.nf",
    "unidock": "workflows/docking.nf",
    "dual_docking": "workflows/docking.nf",
}

EXPECTED_MODEL_MODE_ENTRYPOINTS = {
    ("boltz2", "predict"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("rf3", "predict"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("protenix", "predict"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("esmfold2", "predict"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("esmfold2", "complex"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("esmfold2_experimental", "predict"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("esmfold2_experimental", "complex"): STRUCTURE_PREDICTION_ENTRYPOINT,
    ("boltz2", "complex"): COMPLEX_PREDICTION_ENTRYPOINT,
    ("protenix", "complex"): COMPLEX_PREDICTION_ENTRYPOINT,

    ("ppiflow", "generator_backbone_refine"): "workflows/ppiflow_generator_design.nf",

    ("diffdock", "dock"): "workflows/docking.nf",
    ("diffdock", "ntp_dock"): "workflows/docking.nf",
    ("unidock", "dock"): "workflows/docking.nf",
    ("unidock", "ntp_dock"): "workflows/docking.nf",
    ("docking", "compare"): "workflows/docking.nf",
    ("docking", "consensus"): "workflows/docking.nf",
    ("antibody_denovo", ANTIBODY_DENOVO_PIPELINE): "workflows/antibody_denovo.nf",
    ("antibody_denovo", ANTIBODY_REFINEMENT_PIPELINE): "workflows/antibody_denovo.nf",
    ("antibody_denovo", "default"): "workflows/antibody_denovo.nf",
    ("template_antibody_denovo", ANTIBODY_DENOVO_PIPELINE): "workflows/antibody_denovo.nf",
    ("template_antibody_denovo", ANTIBODY_REFINEMENT_PIPELINE): "workflows/antibody_denovo.nf",
    ("template_antibody_denovo", "default"): "workflows/antibody_denovo.nf",
    ("template_antibody_denovo", "maturation_child"): "workflows/maturation_child.nf",
    ("antibody_child", "validation_batch"): "workflows/antibody_child.nf",
    ("rfantibody_child", "antibody_backbone"): "workflows/rfantibody_backbone.nf",
    ("fampnn_child", "sequence_design"): "workflows/fampnn_child.nf",
    ("protein_modification_experimental", "de_novo_design"): "workflows/protein_cad_experimental.nf",
    ("protein_modification_experimental", "shape_blueprint"): "workflows/shape_blueprint_design.nf",
    ("protein_modification_experimental", "region_redesign"): "workflows/protein_local_redesign.nf",
    ("molecular_dynamics", "simulate"): "workflows/experimental/molecular_dynamics/orchestrator.nf",
    ("molecular_dynamics", "replica"): "workflows/experimental/molecular_dynamics/replica.nf",
    ("molecular_dynamics", "analyze"): "workflows/experimental/molecular_dynamics/analyze.nf",
    ("conformational_mapping", "map"): "workflows/conformational_mapping.nf",
}


def _flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def _nextflow_config_profiles() -> set[str]:
    config_text = (REPO_ROOT / "nextflow.config").read_text(encoding="utf-8", errors="ignore")
    profiles_start = config_text.index("profiles {")
    process_start = config_text.index("\nprocess {", profiles_start)
    profiles_block = config_text[profiles_start:process_start]
    return set(re.findall(r"(?m)^    ([A-Za-z_]\w*)\s*\{", profiles_block))


def _assert_command_profiles_exist(cases: list[tuple[str, str, dict[str, object]]]) -> None:
    defined_profiles = _nextflow_config_profiles()
    missing_profiles: list[str] = []
    for model_id, mode, params in cases:
        cmd = build_nextflow_command(model_id, mode, dict(params), "/tmp/out", job_id=f"job-{model_id}-{mode}")
        for profile_name in _flag_value(cmd, "-profile").split(","):
            if profile_name not in defined_profiles:
                missing_profiles.append(f"{model_id}/{mode} emitted undefined profile {profile_name!r}")
    assert missing_profiles == []


def test_entrypoint_registry_is_explicit_and_current_files_exist() -> None:
    assert LEGACY_MAIN_ENTRYPOINT == "main.nf"
    assert DEFAULT_WORKFLOW_ENTRYPOINT == "workflows/protein_design.nf"
    assert STRUCTURE_PREDICTION_ENTRYPOINT == "workflows/structure_prediction.nf"
    assert COMPLEX_PREDICTION_ENTRYPOINT == "workflows/complex_prediction.nf"
    assert WORKFLOW_ENTRYPOINTS == EXPECTED_PROFILE_ENTRYPOINTS
    assert MODEL_MODE_WORKFLOW_ENTRYPOINTS == EXPECTED_MODEL_MODE_ENTRYPOINTS

    for workflow_id, rel_path in {**WORKFLOW_ENTRYPOINTS, **dict(MODEL_MODE_WORKFLOW_ENTRYPOINTS)}.items():
        assert (REPO_ROOT / rel_path).exists(), f"{workflow_id} -> {rel_path}"
    assert (REPO_ROOT / DEFAULT_WORKFLOW_ENTRYPOINT).exists()
    assert (REPO_ROOT / LEGACY_MAIN_ENTRYPOINT).exists()


def test_boltzgen_modes_require_the_parent_de_novo_workflow() -> None:
    for mode in ("nanobody_binder", "ligand_binder", "peptide_binder", "ntp_binder"):
        with pytest.raises(ValueError, match="internal de-novo engine"):
            resolve_nextflow_entrypoint(
                effective_profile="boltzgen",
                model_id="boltzgen",
                mode=mode,
            )


def test_api_generated_profiles_are_defined_in_nextflow_config() -> None:
    _assert_command_profiles_exist(
        [
            ("ppiflow", "generator_backbone_refine", {"ppiflow_seed_complex_path": "/tmp/seed.pdb"}),

            ("diffdock", "dock", {"protein_pdb": "/tmp/receptor.pdb", "ligand_smiles": "CCO"}),
            ("unidock", "dock", {"protein_pdb": "/tmp/receptor.pdb", "ligand_smiles": "CCO"}),
            ("docking", "compare", {"protein_pdb": "/tmp/receptor.pdb", "ligand_smiles": "CCO"}),
            ("antibody_child", "validation_batch", {"pdb_paths": "/tmp/a.pdb"}),
            ("rfantibody_child", "antibody_backbone", {"target_pdb": "/tmp/target.pdb"}),
            ("fampnn_child", "sequence_design", {"pdb_paths": "/tmp/a.pdb"}),
            ("antibody_child", "maturation_child", {"pdb_paths": "/tmp/a.pdb"}),
        ]
    )


def test_missing_complex_components_is_not_warned_for_sequence_only_launches(caplog) -> None:
    caplog.set_level("WARNING", logger="services.nextflow")
    build_nextflow_command(
        "boltz2",
        "predict",
        {"sequence": "MQIFVKTLTGKTITLEVEPSDTI", "sequence_name": "sequence_only_smoke"},
        "/tmp/out",
        job_id="job-sequence-only-smoke",
    )

    assert not any("complex_components NOT in params" in record.getMessage() for record in caplog.records)


def test_main_entrypoint_is_thin_compatibility_wrapper() -> None:
    main_text = (REPO_ROOT / LEGACY_MAIN_ENTRYPOINT).read_text(encoding="utf-8")
    assert len(main_text.splitlines()) <= 12
    assert "include { PROTEIN_DESIGN } from './workflows/protein_design.nf'" in main_text
    assert "params.rfd_mode" not in main_text
    assert "RunBoltz" not in main_text
    assert "RFANTIBODY" not in main_text


def test_resolver_keeps_profile_migrated_workflows_off_main() -> None:
    for workflow_id, rel_path in EXPECTED_PROFILE_ENTRYPOINTS.items():
        assert resolve_nextflow_entrypoint(effective_profile=workflow_id) == rel_path


def test_resolver_preserves_legacy_ont_profile_aliases() -> None:
    expected = "workflows/ngs/ont_methylation_analysis.nf"
    assert resolve_nextflow_entrypoint(effective_profile="nanopore_methylation") == expected
    assert resolve_nextflow_entrypoint(effective_profile="methylation_analysis") == expected


def test_resolver_fallback_is_core_protein_design_not_main() -> None:
    assert resolve_nextflow_entrypoint(effective_profile="binder_denovo") == DEFAULT_WORKFLOW_ENTRYPOINT
    assert resolve_nextflow_entrypoint(effective_profile="protenix") == DEFAULT_WORKFLOW_ENTRYPOINT
    assert resolve_nextflow_entrypoint(effective_profile="unknown_legacy") == DEFAULT_WORKFLOW_ENTRYPOINT


def test_shared_engine_profiles_do_not_implicitly_select_product_entrypoints() -> None:
    # A bare shared engine profile is not enough to select a product workflow.
    assert resolve_nextflow_entrypoint(effective_profile="boltz") == DEFAULT_WORKFLOW_ENTRYPOINT
    assert resolve_nextflow_entrypoint(effective_profile="protenix") == DEFAULT_WORKFLOW_ENTRYPOINT

    # Product/model intent selects the direct workflow even when the resolved profile is shared.
    assert (
        resolve_nextflow_entrypoint(
            effective_profile="boltz",
            model_id="ppiflow",
            mode="generator_backbone_refine",
            params={},
        )
        == "workflows/ppiflow_generator_design.nf"
    )


def test_standalone_structure_prediction_routes_direct_without_hijacking_complexes() -> None:
    for model_id, profile, pred_method in [
        ("boltz2", "boltz", "boltz"),
        ("rf3", "rf3", "rf3"),
        ("protenix", "protenix", "protenix"),
    ]:
        cmd = build_nextflow_command(
            model_id,
            "predict",
            {"sequence": "MQIFVKTLTGKTITLEVEPSDTI", "sequence_name": f"{model_id}_smoke"},
            "/tmp/out",
            job_id=f"job-{model_id}",
        )
        assert cmd[1:4] == ["run", STRUCTURE_PREDICTION_ENTRYPOINT, "-profile"]
        assert _flag_value(cmd, "-profile") == f"{profile},workstation_ryzen7960x"
        assert _flag_value(cmd, "--sequence_input") == "MQIFVKTLTGKTITLEVEPSDTI"
        assert pred_method in _flag_value(cmd, "-profile") or model_id == "boltz2"

    complex_cmd = build_nextflow_command(
        "boltz2",
        "predict",
        {
            "complex_components": [
                {"type": "protein", "id": "A", "sequence": "MQIFVKTLTGKT"},
                {"type": "ligand", "id": "L", "ccd": "ATP"},
            ],
            "sequence_batch_entries": [{"name": "variant", "sequence": "MQIFVKTLTGKT"}],
        },
        "/tmp/out",
        job_id="job-boltz-complex",
    )
    assert complex_cmd[1:4] == ["run", COMPLEX_PREDICTION_ENTRYPOINT, "-profile"]
    assert "--complex_json_path" in complex_cmd or "--complex_batch_dir" in complex_cmd


def test_remaining_parent_and_child_workflows_route_direct_for_fresh_and_resume() -> None:
    cases = [
        ("ppiflow", "generator_backbone_refine", {"ppiflow_seed_complex_path": "/tmp/seed.pdb"}, "workflows/ppiflow_generator_design.nf"),

        ("diffdock", "dock", {"protein_pdb": "/tmp/receptor.pdb", "ligand_smiles": "CCO"}, "workflows/docking.nf"),
        ("unidock", "dock", {"protein_pdb": "/tmp/receptor.pdb", "ligand_smiles": "CCO"}, "workflows/docking.nf"),
        ("docking", "compare", {"protein_pdb": "/tmp/receptor.pdb", "ligand_smiles": "CCO"}, "workflows/docking.nf"),
        ("antibody_child", "validation_batch", {"pdb_paths": "/tmp/a.pdb"}, "workflows/antibody_child.nf"),
        ("rfantibody_child", "antibody_backbone", {"target_pdb": "/tmp/target.pdb"}, "workflows/rfantibody_backbone.nf"),
        ("fampnn_child", "sequence_design", {"pdb_paths": "/tmp/a.pdb"}, "workflows/fampnn_child.nf"),

    ]

    for model_id, mode, params, rel_path in cases:
        fresh_cmd = build_nextflow_command(model_id, mode, dict(params), "/tmp/out", job_id=f"job-{model_id}-{mode}")
        resume_params = dict(params)
        resume_params["resume_work_dir"] = "/tmp/nxf-work"
        resume_cmd = build_nextflow_command(model_id, mode, resume_params, "/tmp/out", job_id=f"job-{model_id}-{mode}-resume")
        assert fresh_cmd[1:4] == ["run", rel_path, "-profile"], (model_id, mode, fresh_cmd[:4])
        assert resume_cmd[1:4] == ["run", rel_path, "-profile"], (model_id, mode, resume_cmd[:4])
        assert "-resume" not in fresh_cmd
        assert "-resume" in resume_cmd


def test_oligo_design_fresh_and_resume_launches_are_direct_entrypoint() -> None:
    fresh_cmd = build_nextflow_command(
        "oligo_design",
        "oligo_design",
        {"rfdpoly_contigs": "10", "rfdpoly_polymer_chains": "A"},
        "/tmp/out",
        job_id="job-oligo-fresh",
    )
    resume_cmd = build_nextflow_command(
        "oligo_design",
        "oligo_design",
        {
            "rfdpoly_contigs": "10",
            "rfdpoly_polymer_chains": "A",
            "resume_work_dir": "/tmp/nxf-work",
        },
        "/tmp/out",
        job_id="job-oligo-resume",
    )

    assert fresh_cmd[1:4] == ["run", "workflows/oligo_design.nf", "-profile"]
    assert resume_cmd[1:4] == ["run", "workflows/oligo_design.nf", "-profile"]
    assert "-resume" not in fresh_cmd
    assert "-resume" in resume_cmd
