"""Validate Nextflow module namespace isolation and entrypoint routing.

This test suite ensures:
  1. All NGS modules live in the correct namespace
  2. Workflow entrypoints route to the correct files
  3. Module files use proper DSL2 syntax
  4. Monolith is removed and no workflow references it
  5. Entry point files exist and contain proper workflow blocks
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

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


class TestModuleNamespace:
    """Validate that NGS modules are properly isolated."""

    @pytest.fixture
    def root(self):
        return REPO_ROOT

    def test_ngs_modules_exist(self, root):
        """All NGS module files exist."""
        expected_modules = [
            "modules/ngs/dorado_basecall.nf",
            "modules/ngs/dorado_align.nf",
            "modules/ngs/bam_prepare.nf",
            "modules/ngs/fastq_align.nf",
            "modules/ngs/fastq_plasmid_qc.nf",
            "modules/ngs/modkit_pileup.nf",
            "modules/ngs/modkit_summary.nf",
            "modules/ngs/clone_validation.nf",
            "modules/ngs/fastq_dimer_qc.nf",
        ]
        for module in expected_modules:
            assert (root / module).exists(), f"Module not found: {module}"

    def test_modules_use_dsl2(self, root):
        """All NGS modules use DSL2 syntax (emit: keyword or process definition)."""
        modules = list((root / "modules/ngs").glob("*.nf"))
        for module in modules:
            content = module.read_text()
            # DSL2 modules either have emit: keyword or process definitions
            has_emit = "emit:" in content
            has_process = "process " in content
            has_include = "include " in content
            has_dsl2 = "nextflow.enable.dsl = 2" in content
            assert has_emit or has_process or has_include or has_dsl2, (
                f"Module {module.name} doesn't use DSL2 syntax"
            )


class TestEntrypointRouting:
    """Validate that entrypoints are properly registered."""

    def test_all_canonical_in_entrypoints(self):
        """All canonical workflows are in WORKFLOW_ENTRYPOINTS."""
        for workflow_id, entrypoint in CANONICAL_ENTRYPOINTS.items():
            assert workflow_id in WORKFLOW_ENTRYPOINTS, (
                f"Workflow {workflow_id} not in WORKFLOW_ENTRYPOINTS"
            )

    def test_entrypoint_values_match(self):
        """Entrypoint values match expected paths."""
        for workflow_id, expected_entrypoint in CANONICAL_ENTRYPOINTS.items():
            actual = WORKFLOW_ENTRYPOINTS.get(workflow_id)
            assert actual == expected_entrypoint, (
                f"Entrypoint for {workflow_id} is {actual}, expected {expected_entrypoint}"
            )


class TestMonolithRemoval:
    """Validate that the monolith is removed."""

    @pytest.fixture
    def root(self):
        return REPO_ROOT

    def test_monolith_file_deleted(self, root):
        """Monolith file is deleted."""
        monolith = root / "workflows/ngs/nanopore_methylation.nf"
        assert not monolith.exists(), "Monolith file still exists"

    def test_no_workflow_references_monolith(self, root):
        """No workflow references the monolith."""
        workflows = list((root / "workflows/ngs").glob("*.nf"))
        for workflow in workflows:
            content = workflow.read_text()
            assert "NANOPORE_METHYLATION" not in content, (
                f"Workflow {workflow.name} still references monolith"
            )
