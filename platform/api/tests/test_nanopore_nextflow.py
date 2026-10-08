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
