from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import build_nextflow_command


NGS_WORKFLOW = REPO_ROOT / "workflows" / "ngs" / "nanopore_methylation.nf"
NGS_METHYLATION_ENTRYPOINT = REPO_ROOT / "workflows" / "ngs" / "ont_methylation_analysis.nf"
NGS_MODULE_ROOT = REPO_ROOT / "modules" / "ngs"


def test_ngs_entrypoint_files_exist() -> None:
    assert (REPO_ROOT / "ngs.nf").exists()
    assert NGS_WORKFLOW.exists()
    assert NGS_METHYLATION_ENTRYPOINT.exists()


def test_ngs_workflow_lives_under_ngs_workflows_namespace() -> None:
    assert NGS_WORKFLOW.exists()
    assert not (REPO_ROOT / "workflows" / "nanopore_methylation.nf").exists()
    assert "./workflows/ngs/nanopore_methylation.nf" in (REPO_ROOT / "ngs.nf").read_text(encoding="utf-8")


def test_ngs_process_modules_live_under_ngs_module_namespace() -> None:
    expected_modules = (
        "dorado_basecall.nf",
        "dorado_align.nf",
        "bam_prepare.nf",
        "fastq_align.nf",
        "fastq_plasmid_qc.nf",
        "fastq_dimer_qc.nf",
        "modkit_pileup.nf",
        "modkit_summary.nf",
        "clone_validation.nf",
    )
    for module_name in expected_modules:
        assert (NGS_MODULE_ROOT / module_name).exists(), module_name


def test_nanopore_workflow_uses_ngs_module_namespace() -> None:
    workflow = NGS_WORKFLOW.read_text(encoding="utf-8")
    expected_includes = (
        "../../modules/ngs/dorado_basecall.nf",
        "../../modules/ngs/dorado_align.nf",
        "../../modules/ngs/bam_prepare.nf",
        "../../modules/ngs/fastq_align.nf",
        "../../modules/ngs/fastq_plasmid_qc.nf",
        "../../modules/ngs/modkit_pileup.nf",
        "../../modules/ngs/modkit_summary.nf",
        "../../modules/ngs/clone_validation.nf",
    )
    for include_path in expected_includes:
        assert include_path in workflow, include_path
    assert "../modules/dorado.nf" not in workflow
    assert "../../modules/dorado.nf" not in workflow


def test_ngs_is_explicitly_isolated_from_main_entrypoint() -> None:
    main_nf = (REPO_ROOT / "main.nf").read_text(encoding="utf-8").lower()
    ngs_nf = (REPO_ROOT / "ngs.nf").read_text(encoding="utf-8").lower()

    forbidden_main_terms = (
        "nanopore",
        "dorado",
        "modkit",
        "methylation",
        "clone_validation",
        "fastq",
        "bam_path",
        "reference_fasta",
        "ngs.nf",
    )
    assert not any(term in main_nf for term in forbidden_main_terms)

    assert "main.nf" not in ngs_nf
    assert "include { nanopore_methylation }" in ngs_nf


def test_build_nextflow_command_routes_nanopore_to_ngs_entrypoint() -> None:
    cmd = build_nextflow_command(
        "nanopore",
        "methylation_analysis",
        {
            "pod5_dir": "/tmp/pod5",
            "reference_fasta": "/tmp/reference.fasta",
            "dorado_model": "sup",
            "run_modkit": True,
            "run_fastq_qc": True,
        },
        "/tmp/out",
        job_id="job-ngs-1",
    )

    joined = " ".join(cmd)

    assert cmd[:4] == ["nextflow", "run", "workflows/ngs/ont_methylation_analysis.nf", "-profile"]
    assert "ont_methylation_analysis,workstation_ryzen7960x" in cmd
    assert "--pod5_dir /tmp/pod5" in joined
    assert "--reference_fasta /tmp/reference.fasta" in joined
    assert "--dorado_model sup" in joined
    assert "--run_modkit true" in joined
    assert "--run_fastq_qc true" in joined


def test_build_nextflow_command_routes_nanopore_resume_to_ngs_entrypoint() -> None:
    cmd = build_nextflow_command(
        "nanopore",
        "methylation_analysis",
        {
            "bam_path": "/tmp/input.bam",
            "resume_work_dir": "/tmp/work-ngs",
        },
        "/tmp/out",
        job_id="job-ngs-resume",
    )

    joined = " ".join(cmd)

    assert cmd[:4] == ["nextflow", "run", "workflows/ngs/ont_methylation_analysis.nf", "-profile"]
    assert "-w /tmp/work-ngs" in joined
    assert "-resume" in cmd
    assert "--bam_path /tmp/input.bam" in joined


def test_fastq_plasmid_qc_declares_manifest_and_per_base_support_outputs() -> None:
    fastq_module = (NGS_MODULE_ROOT / "fastq_plasmid_qc.nf").read_text(encoding="utf-8")
    fastq_block = fastq_module.split("process FastqPlasmidQC", 1)[1]

    assert 'path "per_base_support.tsv", emit: per_base_support' in fastq_block
    assert 'path "qc_manifest.json", emit: qc_manifest' in fastq_block
    assert 'path "aligned.bam", emit: alignment_bam' in fastq_block
    assert 'path "aligned.bam.bai", emit: alignment_bai' in fastq_block
    assert '--alignment-bam "\\${bam_local}"' in fastq_block
    assert '--alignment-bai "\\${bai_local}"' in fastq_block
    assert 'scripts/build_fastq_support_tables.py' in fastq_block
    assert 'scripts/build_sequence_qc_manifest.py' in fastq_block


def test_nanopore_model_declares_sequence_qc_contract_outputs() -> None:
    nanopore_yaml = (REPO_ROOT / "platform" / "api" / "config" / "models" / "nanopore.yaml").read_text(encoding="utf-8")

    assert "json" in nanopore_yaml
    assert "per_base_support.tsv" in nanopore_yaml
    assert "qc_manifest.json" in nanopore_yaml


def test_jobs_list_route_exposes_model_and_mode_filters_for_ngs_polling() -> None:
    jobs_router = (REPO_ROOT / "platform" / "api" / "routers" / "jobs.py").read_text(encoding="utf-8")

    list_jobs_block = jobs_router.split('@router.get("", response_model=JobList)', 1)[1].split('@router.get("/{job_id}"', 1)[0]
    assert "model_id: Optional[str]" in list_jobs_block
    assert "mode: Optional[str]" in list_jobs_block
    assert "query = query.where(Job.model_id == model_id)" in list_jobs_block
    assert "count_query = count_query.where(Job.model_id == model_id)" in list_jobs_block


def test_fastq_minimap2_default_is_bundled_runtime_compatible() -> None:
    nextflow_config = (REPO_ROOT / "nextflow.config").read_text(encoding="utf-8")
    workflow = NGS_WORKFLOW.read_text(encoding="utf-8")
    fastq_align_module = (NGS_MODULE_ROOT / "fastq_align.nf").read_text(encoding="utf-8")
    nanopore_yaml = (REPO_ROOT / "platform" / "api" / "config" / "models" / "nanopore.yaml").read_text(encoding="utf-8")

    assert "fastq_minimap2_preset = 'map-ont'" in nextflow_config
    assert "job_id = null" in nextflow_config
    assert "params.fastq_minimap2_preset ?: 'map-ont'" in workflow
    assert "params.fastq_minimap2_preset ?: 'map-ont'" in fastq_align_module
    assert "default: map-ont" in nanopore_yaml
    assert "Unsupported --fastq_minimap2_preset" in workflow
    assert "'lr:hq'" not in nanopore_yaml
    assert "?: 'lr:hq'" not in workflow
    assert "?: 'lr:hq'" not in fastq_align_module
