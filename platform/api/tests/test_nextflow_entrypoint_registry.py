from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import (  # noqa: E402
    DEFAULT_WORKFLOW_ENTRYPOINT,
    WORKFLOW_ENTRYPOINTS,
    build_nextflow_command,
    resolve_nextflow_entrypoint,
)


EXPECTED_MIGRATED_ENTRYPOINTS = {
    "nanopore_methylation": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_basecall_dna": "workflows/ngs/ont_basecall_dna.nf",
    "ont_basecall_rna": "workflows/ngs/ont_basecall_rna.nf",
    "ont_plasmid_qc": "workflows/ngs/ont_plasmid_qc.nf",
    "ont_construct_screening": "workflows/ngs/ont_construct_screening.nf",
    "ont_methylation_analysis": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_fastq_qc": "workflows/ngs/ont_fastq_qc.nf",
    "protein_local_redesign": "workflows/protein_local_redesign.nf",
    "protein_cad_experimental": "workflows/protein_cad_experimental.nf",
    "caliby_experimental": "workflows/caliby_experimental.nf",
    "protein_hunter_experimental": "workflows/protein_hunter_experimental.nf",
    "boltz_cp_experimental": "workflows/boltz_cp_experimental.nf",
    "confornets_experimental": "workflows/confornets_experimental.nf",
}


def test_entrypoint_registry_is_explicit_and_current_files_exist() -> None:
    assert DEFAULT_WORKFLOW_ENTRYPOINT == "main.nf"
    assert WORKFLOW_ENTRYPOINTS == EXPECTED_MIGRATED_ENTRYPOINTS

    for workflow_id, rel_path in WORKFLOW_ENTRYPOINTS.items():
        assert (REPO_ROOT / rel_path).exists(), f"{workflow_id} -> {rel_path}"


def test_resolver_keeps_current_migrated_workflows_off_main() -> None:
    for workflow_id, rel_path in EXPECTED_MIGRATED_ENTRYPOINTS.items():
        assert resolve_nextflow_entrypoint(effective_profile=workflow_id) == rel_path


def test_resolver_fallback_is_explicit_for_unmigrated_profiles() -> None:
    assert resolve_nextflow_entrypoint(effective_profile="oligo_design") == "main.nf"
    assert resolve_nextflow_entrypoint(effective_profile="binder_denovo") == "main.nf"
    assert resolve_nextflow_entrypoint(effective_profile="protenix") == "main.nf"


def test_shared_engine_profiles_do_not_implicitly_select_product_entrypoints() -> None:
    # Boltz-family profiles are shared engines. Until a product workflow is migrated,
    # the resolver must not silently hijack all boltz/protenix uses into standalone
    # structure prediction just because those models share backend profiles.
    assert (
        resolve_nextflow_entrypoint(
            effective_profile="boltz",
            model_id="boltz2",
            mode="predict",
            params={"pred_method": "boltz"},
        )
        == "main.nf"
    )
    assert (
        resolve_nextflow_entrypoint(
            effective_profile="boltz",
            model_id="antibody_denovo",
            mode="antibody_denovo_pipeline",
            params={"structure_validator": "boltz2"},
        )
        == "main.nf"
    )
    assert (
        resolve_nextflow_entrypoint(
            effective_profile="boltz",
            model_id="ppiflow",
            mode="generator_backbone_refine",
            params={},
        )
        == "main.nf"
    )


def test_build_nextflow_command_uses_registry_for_fresh_and_resume_launches() -> None:
    fresh_cmd = build_nextflow_command(
        "protein_local_redesign",
        "local_redesign",
        {"input_pdb": "/tmp/input.pdb", "design_chains": "A", "redesign_ranges": "1-5"},
        "/tmp/out",
        job_id="job-plr-fresh",
    )
    resume_cmd = build_nextflow_command(
        "protein_local_redesign",
        "local_redesign",
        {
            "input_pdb": "/tmp/input.pdb",
            "design_chains": "A",
            "redesign_ranges": "1-5",
            "resume_work_dir": "/tmp/nxf-work",
        },
        "/tmp/out",
        job_id="job-plr-resume",
    )

    assert fresh_cmd[:4] == ["nextflow", "run", WORKFLOW_ENTRYPOINTS["protein_local_redesign"], "-profile"]
    assert resume_cmd[:4] == ["nextflow", "run", WORKFLOW_ENTRYPOINTS["protein_local_redesign"], "-profile"]
    assert "-resume" in resume_cmd
