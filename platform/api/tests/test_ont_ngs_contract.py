"""Validate ONT/NGS canonical workflow contract, aliases, and manifest schema."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.ont_ngs_contract import CANONICAL_ONT_WORKFLOWS, normalize_ont_launch_params

EXPECTED_CANONICAL = {
    "ont_basecall_dna",
    "ont_basecall_rna",
    "ont_plasmid_qc",
    "ont_construct_screening",
    "ont_methylation_analysis",
    "ont_fastq_qc",
    "ont_pooled_reference_assignment",
    "wf_clone_validation",
}


class TestCanonicalWorkflows:
    """Validate that all canonical workflows are properly defined."""


    def test_methylation_input_modes_match_workflow_support(self):
        """Methylation workflow currently supports only POD5/BAM with MM/ML-capable BAMs."""
        spec = CANONICAL_ONT_WORKFLOWS.get("ont_methylation_analysis")
        assert spec is not None
        assert spec.input_modes == ("pod5", "bam")

    def test_fast5_is_not_advertised_until_conversion_and_model_qualification_exist(self):
        for spec in CANONICAL_ONT_WORKFLOWS.values():
            assert "fast5" not in spec.input_modes

        model_config = (REPO_ROOT / "platform/api/config/models/nanopore.yaml").read_text(
            encoding="utf-8"
        )
        assert "POD5/FAST5" not in model_config

        dorado_module = (REPO_ROOT / "modules/ngs/dorado_basecall.nf").read_text(
            encoding="utf-8"
        )
        assert "scripts/dorado_supports_option.sh" in dorado_module
        assert "--emit-summary" in dorado_module
        assert "basecaller --help 2>&1 | grep" not in dorado_module

        for relative_path in (
            "workflows/ngs/ont_basecall_dna.nf",
            "workflows/ngs/ont_basecall_rna.nf",
            "workflows/ngs/ont_plasmid_qc.nf",
            "workflows/ngs/ont_construct_screening.nf",
            "workflows/ngs/ont_methylation_analysis.nf",
            "workflows/ngs/wf_clone_validation.nf",
        ):
            workflow = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            assert '"${params.out_dir}/basecall/sequencing_summary.tsv"' not in workflow

    def test_rna_launch_defaults_to_rna_dorado_model(self):
        """RNA workflow normalization must select an RNA model, not generic 'sup'."""
        normalized = normalize_ont_launch_params("ont_basecall_rna", {})
        assert normalized["ont_molecule_type"] == "rna"
        assert normalized["dorado_model"].startswith("rna")


class TestCloneAndDimerNormalization:
    def test_clone_uses_its_own_quality_default_and_fractional_thresholds(self):
        normalized = normalize_ont_launch_params(
            "wf_clone_validation",
            {"wf_clone_expected_coverage": 92.5, "wf_clone_expected_identity": 98.25},
        )
        assert normalized["wf_clone_min_quality"] == 9
        assert normalized["wf_clone_primer_mismatch"] == 2
        assert normalized["wf_clone_expected_coverage"] == 92.5
        assert normalized["wf_clone_expected_identity"] == 98.25
        assert "wf_clone_analyse_unclassified" not in normalized

    @pytest.mark.parametrize("key, value", [("wf_clone_min_quality", 61), ("wf_clone_primer_mismatch", -1), ("wf_clone_expected_identity", 100.1)])
    def test_clone_rejects_out_of_policy_vendor_values(self, key, value):
        with pytest.raises(ValueError):
            normalize_ont_launch_params("wf_clone_validation", {key: value})

    def test_plasmid_qc_normalizes_only_bounded_dimer_controls(self):
        normalized = normalize_ont_launch_params(
            "ont_plasmid_qc",
            {"rotation_scan_step_bp": 5, "single_ref_split_min_mapq": 30},
        )
        assert normalized["enable_rotating_reference_frames"] is True
        assert normalized["rotation_scan_step_bp"] == 5
        assert normalized["single_ref_split_min_mapq"] == 30
        with pytest.raises(ValueError, match="single_ref_split_min_mapq"):
            normalize_ont_launch_params("ont_plasmid_qc", {"single_ref_split_min_mapq": 61})
