"""The assembly report runs without an expected sequence; the other workflows still need one."""
import pytest

from routers.ont_runs import _validate_ont_input_contract
from services.job_readback import _planned_nanopore_stages


@pytest.mark.parametrize("key,mode", [("fastq_path", "fastq"), ("bam_path", "bam"), ("pod5_dir", "pod5")])
def test_assembly_report_accepts_reads_without_a_reference(key, mode):
    assert _validate_ont_input_contract("wf_clone_validation", {key: "/reads"}) == (mode, "/reads")


@pytest.mark.parametrize("workflow", ["ont_plasmid_qc", "ont_construct_screening", "ont_methylation_analysis", "ont_fastq_qc"])
def test_the_other_reference_workflows_still_require_one(workflow):
    key = "fastq_path" if workflow == "ont_fastq_qc" else "bam_path"
    with pytest.raises(ValueError, match="requires reference_fasta"):
        _validate_ont_input_contract(workflow, {key: "/reads"})


def test_stage_plan_without_a_reference_assembles_then_aligns_to_the_assembly():
    stages = _planned_nanopore_stages({"fastq_path": "/reads", "run_assembly": True, "run_fastq_qc": False}, mode="clone_validation")
    assert stages == ["wf_clone_validation", "dorado_align"]
    stages = _planned_nanopore_stages({"pod5_dir": "/reads", "run_assembly": True, "run_modkit": False}, mode="clone_validation")
    assert stages == ["dorado_basecall", "wf_clone_validation", "dorado_align"]


def test_stage_plan_with_a_reference_is_unchanged():
    stages = _planned_nanopore_stages(
        {"bam_path": "/reads", "reference_fasta": "/ref", "run_assembly": True, "run_modkit": False}, mode="clone_validation")
    assert stages == ["bam_prepare", "wf_clone_validation"]
