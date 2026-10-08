"""Validate that each canonical ONT workflow has a real entrypoint file,
acceptable default parameters, and a buildable CLI command.

This test verifies structural correctness — that each workflow:
  1. Has a corresponding entrypoint file
  2. Contains required workflow directives
  3. Has proper module includes (not monolith)
  4. Can be built as a CLI command
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Any

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.ont_ngs_contract import (  # noqa: E402
    CANONICAL_ONT_WORKFLOW_IDS,
    CANONICAL_ONT_WORKFLOWS,
)
from services.nextflow import WORKFLOW_ENTRYPOINTS  # noqa: E402

CANONICAL_ENTRYPOINTS = {
    "ont_basecall_dna": "workflows/ngs/ont_basecall_dna.nf",
    "ont_basecall_rna": "workflows/ngs/ont_basecall_rna.nf",
    "ont_plasmid_qc": "workflows/ngs/ont_plasmid_qc.nf",
    "ont_construct_screening": "workflows/ngs/ont_construct_screening.nf",
    "ont_methylation_analysis": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_fastq_qc": "workflows/ngs/ont_fastq_qc.nf",
    "wf_clone_validation": "workflows/ngs/wf_clone_validation.nf",
}


class TestDirectEntrypoints:
    """Validate that entrypoints are direct workflow files."""

    @pytest.fixture
    def root(self):
        return REPO_ROOT

    @pytest.mark.parametrize("workflow_id, entrypoint", CANONICAL_ENTRYPOINTS.items())
    def test_entrypoint_exists(self, root, workflow_id, entrypoint):
        """Each entrypoint file exists."""
        entrypoint_path = root / entrypoint
        assert entrypoint_path.exists(), f"Entrypoint not found: {entrypoint_path}"
        assert entrypoint_path.is_file(), f"Entrypoint is not a file: {entrypoint_path}"

    @pytest.mark.parametrize("workflow_id, entrypoint", CANONICAL_ENTRYPOINTS.items())
    def test_entrypoint_contains_workflow_directive(self, root, workflow_id, entrypoint):
        """Each entrypoint contains a workflow {} block."""
        content = (root / entrypoint).read_text()
        assert "workflow" in content, (
            f"Entrypoint {entrypoint} doesn't contain 'workflow' directive"
        )

    @pytest.mark.parametrize("workflow_id, entrypoint", CANONICAL_ENTRYPOINTS.items())
    def test_entrypoint_is_not_monolith(self, root, workflow_id, entrypoint):
        """No entrypoint references the monolith."""
        content = (root / entrypoint).read_text()
        assert "NANOPORE_METHYLATION" not in content, (
            f"Entrypoint {entrypoint} still references monolith"
        )

    @pytest.mark.parametrize("workflow_id, entrypoint", CANONICAL_ENTRYPOINTS.items())
    def test_entrypoint_is_dsl2(self, root, workflow_id, entrypoint):
        """Each entrypoint uses DSL2."""
        content = (root / entrypoint).read_text()
        assert "nextflow.enable.dsl = 2" in content, (
            f"Entrypoint {entrypoint} doesn't declare DSL2"
        )


class TestDefaultParameters:
    """Validate that default parameters are correct."""

    @pytest.fixture
    def root(self):
        return REPO_ROOT

    def test_plasmid_qc_includes_fastq_plasmid_qc(self, root):
        """Plasmid QC includes FastqPlasmidQC module."""
        content = (root / "workflows/ngs/ont_plasmid_qc.nf").read_text()
        assert "FastqPlasmidQC" in content

    def test_fastq_qc_includes_fastq_plasmid_qc(self, root):
        """FASTQ QC includes FastqPlasmidQC module."""
        content = (root / "workflows/ngs/ont_fastq_qc.nf").read_text()
        assert "FastqPlasmidQC" in content

    def test_basecall_dna_includes_dorado(self, root):
        """DNA basecalling includes DoradoBasecall."""
        content = (root / "workflows/ngs/ont_basecall_dna.nf").read_text()
        assert "DoradoBasecall" in content

    def test_basecall_rna_includes_dorado(self, root):
        """RNA basecalling includes DoradoBasecall."""
        content = (root / "workflows/ngs/ont_basecall_rna.nf").read_text()
        assert "DoradoBasecall" in content


class TestModuleIncludes:
    """Validate that workflows include the right modules."""

    @pytest.fixture
    def root(self):
        return REPO_ROOT

    def test_wf_clone_includes_clone_validation(self, root):
        """wf_clone_validation includes RunCloneValidation."""
        content = (root / "workflows/ngs/wf_clone_validation.nf").read_text()
        assert "RunCloneValidation" in content

    def test_construct_screening_includes_clone_validation(self, root):
        """Construct screening includes RunCloneValidation."""
        content = (root / "workflows/ngs/ont_construct_screening.nf").read_text()
        assert "RunCloneValidation" in content

    def test_methylation_includes_modkit(self, root):
        """Methylation analysis includes ModkitPileup."""
        content = (root / "workflows/ngs/ont_methylation_analysis.nf").read_text()
        assert "ModkitPileup" in content
        assert "ModkitSummary" in content

    @pytest.mark.parametrize(
        "workflow_path",
        [
            "workflows/ngs/ont_fastq_qc.nf",
            "workflows/ngs/ont_plasmid_qc.nf",
            "workflows/ngs/wf_clone_validation.nf",
        ],
    )
    def test_core_plasmid_workflows_include_dimer_qc(self, root, workflow_path):
        """Core plasmid QC workflows must execute FastqDimerQC, not leave it unused."""
        content = (root / workflow_path).read_text()
        assert "FastqDimerAnalysis" in content
        assert "fastq_dimer_qc.nf" in content

    def test_plasmid_qc_has_no_two_argument_fastq_plasmid_qc_calls(self, root):
        """Alignment-backed POD5/BAM paths must not call 3-input FastqPlasmidQC with 2 args."""
        content = (root / "workflows/ngs/ont_plasmid_qc.nf").read_text()
        bad_calls = [
            "FastqPlasmidQC(DoradoAlign.out.aligned, Channel.of(reference_file))",
            "FastqPlasmidQC(analysis_bam, Channel.of(reference_file))",
        ]
        for call in bad_calls:
            assert call not in content


class TestCommandBuilding:
    """Validate that CLI commands can be built."""

    def test_entrypoint_in_nextflow_service(self):
        """All canonical entrypoints are registered in nextflow service."""
        for workflow_id, entrypoint in CANONICAL_ENTRYPOINTS.items():
            assert workflow_id in WORKFLOW_ENTRYPOINTS, (
                f"Workflow {workflow_id} not in WORKFLOW_ENTRYPOINTS"
            )

    def test_monolith_removed_from_entrypoints(self):
        """Monolith entrypoint is removed."""
        assert "nanopore_methylation" not in WORKFLOW_ENTRYPOINTS, (
            "Monolith entrypoint still exists"
        )
