from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.nextflow import WORKFLOW_ENTRYPOINTS, build_nextflow_command, resolve_nextflow_entrypoint  # noqa: E402
from services.ont_ngs_contract import CANONICAL_ONT_WORKFLOW_IDS  # noqa: E402


EXPECTED_ONT_ENTRYPOINTS = {
    "ont_basecall_dna": "workflows/ngs/ont_basecall_dna.nf",
    "ont_basecall_rna": "workflows/ngs/ont_basecall_rna.nf",
    "ont_plasmid_qc": "workflows/ngs/ont_plasmid_qc.nf",
    "ont_construct_screening": "workflows/ngs/ont_construct_screening.nf",
    "ont_methylation_analysis": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_fastq_qc": "workflows/ngs/ont_fastq_qc.nf",
}


def test_all_canonical_ont_products_have_direct_entrypoints() -> None:
    assert set(CANONICAL_ONT_WORKFLOW_IDS) == set(EXPECTED_ONT_ENTRYPOINTS)

    for workflow_id, rel_path in EXPECTED_ONT_ENTRYPOINTS.items():
        assert WORKFLOW_ENTRYPOINTS[workflow_id] == rel_path
        assert resolve_nextflow_entrypoint(effective_profile=workflow_id) == rel_path
        workflow_text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert "workflow {" in workflow_text
        assert "NANOPORE_METHYLATION" in workflow_text
        assert f"params.ont_workflow_id = params.ont_workflow_id ?: '{workflow_id}'" in workflow_text
        assert "params.manifest_contract = params.manifest_contract ?: 'sequence_qc.manifest.v1'" in workflow_text


def test_direct_ont_entrypoints_bind_product_specific_cli_defaults() -> None:
    expected_defaults = {
        "ont_basecall_dna": {
            "params.ont_molecule_type = params.ont_molecule_type ?: 'dna'",
            "params.run_modkit = params.run_modkit != null ? params.run_modkit : false",
            "params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : false",
            "params.modified_bases = params.modified_bases ?: 'none'",
        },
        "ont_basecall_rna": {
            "params.ont_molecule_type = params.ont_molecule_type ?: 'rna'",
            "params.run_modkit = params.run_modkit != null ? params.run_modkit : false",
            "params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : false",
            "params.modified_bases = params.modified_bases ?: 'none'",
        },
        "ont_plasmid_qc": {
            "params.run_modkit = params.run_modkit != null ? params.run_modkit : false",
            "params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : true",
            "params.fastq_minimap2_preset = params.fastq_minimap2_preset ?: 'map-ont'",
            "params.modified_bases = params.modified_bases ?: 'none'",
        },
        "ont_construct_screening": {
            "params.run_modkit = params.run_modkit != null ? params.run_modkit : false",
            "params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : true",
            "params.fastq_minimap2_preset = params.fastq_minimap2_preset ?: 'map-ont'",
            "params.modified_bases = params.modified_bases ?: 'none'",
        },
        "ont_methylation_analysis": {
            "params.run_modkit = params.run_modkit != null ? params.run_modkit : true",
            "params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : true",
            "params.modified_bases = params.modified_bases ?: '6mA 4mC_5mC'",
        },
        "ont_fastq_qc": {
            "params.run_modkit = params.run_modkit != null ? params.run_modkit : false",
            "params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : true",
            "params.fastq_minimap2_preset = params.fastq_minimap2_preset ?: 'map-ont'",
            "params.modified_bases = params.modified_bases ?: 'none'",
        },
    }

    for workflow_id, expected_lines in expected_defaults.items():
        workflow_text = (REPO_ROOT / EXPECTED_ONT_ENTRYPOINTS[workflow_id]).read_text(encoding="utf-8")
        for expected_line in expected_lines:
            assert expected_line in workflow_text


def test_legacy_nanopore_methylation_profile_routes_to_canonical_direct_entrypoint() -> None:
    assert WORKFLOW_ENTRYPOINTS["nanopore_methylation"] == EXPECTED_ONT_ENTRYPOINTS["ont_methylation_analysis"]
    assert resolve_nextflow_entrypoint(effective_profile="nanopore_methylation") == "workflows/ngs/ont_methylation_analysis.nf"


def test_build_nextflow_command_routes_each_ont_product_to_its_direct_entrypoint() -> None:
    cases = [
        ("basecall_dna", {"pod5_dir": "/tmp/pod5"}, "ont_basecall_dna"),
        ("basecall_rna", {"pod5_dir": "/tmp/pod5"}, "ont_basecall_rna"),
        ("plasmid_qc", {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"}, "ont_plasmid_qc"),
        ("construct_screening", {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"}, "ont_construct_screening"),
        ("methylation_analysis", {"bam_path": "/tmp/aligned.bam"}, "ont_methylation_analysis"),
        ("fastq_qc", {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"}, "ont_fastq_qc"),
    ]

    for mode, params, workflow_id in cases:
        cmd = build_nextflow_command("nanopore", mode, params, "/tmp/out", job_id=f"job-{workflow_id}")
        joined = " ".join(cmd)

        assert cmd[:4] == ["nextflow", "run", EXPECTED_ONT_ENTRYPOINTS[workflow_id], "-profile"]
        assert f"{workflow_id},workstation_ryzen7960x" in cmd
        assert f"--ont_workflow_id {workflow_id}" in joined
        assert f"--job_id job-{workflow_id}" in joined


def test_resume_launch_uses_same_direct_ont_entrypoint() -> None:
    cmd = build_nextflow_command(
        "nanopore",
        "fastq_qc",
        {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa", "resume_work_dir": "/tmp/nxf-work"},
        "/tmp/out",
        job_id="job-fastq-resume",
    )

    assert cmd[:4] == ["nextflow", "run", "workflows/ngs/ont_fastq_qc.nf", "-profile"]
    assert "-resume" in cmd
    assert "-w" in cmd
    assert "/tmp/nxf-work" in cmd
