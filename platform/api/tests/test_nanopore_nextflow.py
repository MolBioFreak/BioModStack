from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import build_nextflow_command


def test_ngs_entrypoint_files_exist() -> None:
    assert (REPO_ROOT / "ngs.nf").exists()
    assert (REPO_ROOT / "workflows" / "nanopore_methylation.nf").exists()


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

    assert cmd[:4] == ["nextflow", "run", "ngs.nf", "-profile"]
    assert "nanopore_methylation,workstation_ryzen7960x" in cmd
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

    assert cmd[:4] == ["nextflow", "run", "ngs.nf", "-profile"]
    assert "-w /tmp/work-ngs" in joined
    assert "-resume" in cmd
    assert "--bam_path /tmp/input.bam" in joined


def test_fastq_plasmid_qc_declares_manifest_and_per_base_support_outputs() -> None:
    dorado_module = (REPO_ROOT / "modules" / "dorado.nf").read_text(encoding="utf-8")
    fastq_block = dorado_module.split("process FastqPlasmidQC", 1)[1].split("process FastqMultimerQC", 1)[0]

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
    workflow = (REPO_ROOT / "workflows" / "nanopore_methylation.nf").read_text(encoding="utf-8")
    dorado_module = (REPO_ROOT / "modules" / "dorado.nf").read_text(encoding="utf-8")
    nanopore_yaml = (REPO_ROOT / "platform" / "api" / "config" / "models" / "nanopore.yaml").read_text(encoding="utf-8")

    assert "fastq_minimap2_preset = 'map-ont'" in nextflow_config
    assert "job_id = null" in nextflow_config
    assert "params.fastq_minimap2_preset ?: 'map-ont'" in workflow
    assert "params.fastq_minimap2_preset ?: 'map-ont'" in dorado_module
    assert "default: map-ont" in nanopore_yaml
    assert "Unsupported --fastq_minimap2_preset" in workflow
    assert "'lr:hq'" not in nanopore_yaml
    assert "?: 'lr:hq'" not in workflow
    assert "?: 'lr:hq'" not in dorado_module
