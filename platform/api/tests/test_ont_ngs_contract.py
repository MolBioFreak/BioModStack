"""Validate ONT/NGS canonical workflow contract, aliases, and manifest schema."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.ont_ngs_contract import (  # noqa: E402
    CANONICAL_ONT_WORKFLOW_IDS,
    CANONICAL_ONT_WORKFLOWS,
    ONT_WORKFLOW_ALIASES,
    ONT_SEQUENCE_QC_MANIFEST_CONTRACT,
    resolve_ont_workflow_alias,
)
from services.sequence_qc_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    SCHEMA_BY_KIND,
)

EXPECTED_CANONICAL = {
    "ont_basecall_dna",
    "ont_basecall_rna",
    "ont_plasmid_qc",
    "ont_construct_screening",
    "ont_methylation_analysis",
    "ont_fastq_qc",
    "wf_clone_validation",
}


class TestCanonicalWorkflows:
    """Validate that all canonical workflows are properly defined."""

    def test_canonical_ids_match_registry(self):
        assert set(CANONICAL_ONT_WORKFLOW_IDS) == EXPECTED_CANONICAL, (
            f"Canonical IDs mismatch: {set(CANONICAL_ONT_WORKFLOW_IDS) ^ EXPECTED_CANONICAL}"
        )

    def test_all_have_ids(self):
        for spec in CANONICAL_ONT_WORKFLOWS.values():
            assert spec.workflow_id in EXPECTED_CANONICAL, (
                f"Workflow {spec.workflow_id} not in canonical set"
            )

    def test_lifecycle_values(self):
        allowed = {"seed", "planned", "deprecated"}
        for spec in CANONICAL_ONT_WORKFLOWS.values():
            assert spec.lifecycle in allowed, (
                f"Invalid lifecycle {spec.lifecycle} for {spec.workflow_id}"
            )

    def test_seed_workflows_have_input_modes(self):
        for spec in CANONICAL_ONT_WORKFLOWS.values():
            if spec.lifecycle == "seed":
                assert len(spec.input_modes) > 0, (
                    f"Seed workflow {spec.workflow_id} has no input_modes"
                )

    def test_seed_workflows_have_artifact_kinds(self):
        for spec in CANONICAL_ONT_WORKFLOWS.values():
            if spec.lifecycle == "seed":
                assert len(spec.artifact_kinds) > 0, (
                    f"Seed workflow {spec.workflow_id} has no artifact_kinds"
                )

    def test_wf_clone_validation_exists(self):
        """Verify wf_clone_validation is properly registered."""
        spec = CANONICAL_ONT_WORKFLOWS.get("wf_clone_validation")
        assert spec is not None
        assert spec.lifecycle == "seed"
        assert "pod5" in spec.input_modes
        assert "bam" in spec.input_modes
        assert "fastq" in spec.input_modes
        assert "clone_validation_assembly" in spec.artifact_kinds
        assert "construct_screening_summary" in spec.artifact_kinds

    def test_ont_basecall_rna_seed(self):
        """Verify ont_basecall_rna is seed lifecycle."""
        spec = CANONICAL_ONT_WORKFLOWS.get("ont_basecall_rna")
        assert spec is not None
        assert spec.lifecycle == "seed"

    def test_ont_plasmid_qc_input_modes(self):
        """Verify ont_plasmid_qc accepts all input modes."""
        spec = CANONICAL_ONT_WORKFLOWS.get("ont_plasmid_qc")
        assert spec is not None
        assert "pod5" in spec.input_modes
        assert "bam" in spec.input_modes
        assert "fastq" in spec.input_modes


class TestWorkflowAliases:
    """Validate that aliases resolve correctly."""

    def test_alias_resolution(self):
        for alias, canonical in ONT_WORKFLOW_ALIASES.items():
            assert canonical in EXPECTED_CANONICAL, (
                f"Alias '{alias}' -> '{canonical}' not in canonical set"
            )

    def test_resolve_workflow_id(self):
        for wf_id in EXPECTED_CANONICAL:
            result = resolve_ont_workflow_alias(wf_id)
            assert result == wf_id, f"resolve_ont_workflow_alias('{wf_id}') returned {result}"

    def test_find_by_alias(self):
        for alias, canonical in ONT_WORKFLOW_ALIASES.items():
            result = resolve_ont_workflow_alias(alias)
            assert result == canonical, (
                f"resolve_ont_workflow_alias('{alias}') returned {result}, expected {canonical}"
            )

    def test_wf_clone_aliases(self):
        """Verify wf_clone aliases resolve correctly."""
        assert resolve_ont_workflow_alias("wf_clone_validation") == "wf_clone_validation"
        assert resolve_ont_workflow_alias("wf_clone") == "wf_clone_validation"
        assert resolve_ont_workflow_alias("clone_validation") == "wf_clone_validation"
        assert resolve_ont_workflow_alias("construct_screening") == "ont_construct_screening"

    def test_no_monolith_alias(self):
        """Verify nanopore_methylation alias is removed."""
        assert "nanopore_methylation" not in ONT_WORKFLOW_ALIASES


class TestManifestContract:
    """Validate that manifest contract matches our schema."""

    def test_artifact_kinds_contain_common(self):
        common = {
            "raw_reads", "basecall_reads", "alignment_bam", "alignment_bai",
            "reference", "reference_index", "read_qc_summary",
            "per_base_support", "consensus",
        }
        for kind in common:
            assert kind in ONT_SEQUENCE_QC_MANIFEST_CONTRACT["artifact_kinds"], (
                f"Missing common artifact kind: {kind}"
            )

    def test_artifact_kinds_contain_ont_specific(self):
        ont_specific = {
            "modified_bases", "modkit_summary", "methylation_bed",
            "plasmid_qc_summary", "construct_screening_summary",
            "clone_validation_assembly", "clone_validation_report",
            "igv_track_config", "igv_report", "igv_track",
        }
        for kind in ont_specific:
            assert kind in ONT_SEQUENCE_QC_MANIFEST_CONTRACT["artifact_kinds"], (
                f"Missing ONT artifact kind: {kind}"
            )

    def test_schema_format(self):
        assert ONT_SEQUENCE_QC_MANIFEST_CONTRACT["schema"].startswith("sequence_qc.")

    def test_required_fields(self):
        required = ONT_SEQUENCE_QC_MANIFEST_CONTRACT["required_top_level_fields"]
        assert "artifact_schema_version" in required
        assert "workflow_id" in required
        assert "job_id" in required
        assert "input_mode" in required
        assert "analysis_status" in required
        assert "artifacts" in required

    def test_schema_by_kind_clone_validation(self):
        """Verify clone validation artifact kinds are in SCHEMA_BY_KIND."""
        kinds = {
            "clone_validation_assembly", "clone_validation_report",
        }
        for kind in kinds:
            assert kind in SCHEMA_BY_KIND, f"Missing schema for artifact kind: {kind}"

    def test_path_policy(self):
        assert ONT_SEQUENCE_QC_MANIFEST_CONTRACT["path_policy"] == "manifest_relative_only"

    def test_unavailable_policy(self):
        assert ONT_SEQUENCE_QC_MANIFEST_CONTRACT["unavailable_policy"] == "optional_artifacts_use_state_without_fake_paths"


class TestManifestFilename:
    """Validate manifest filename."""

    def test_manifest_filename(self):
        assert MANIFEST_FILENAME == "qc_manifest.json"


class TestManifestSchemaVersion:
    """Validate manifest schema version."""

    def test_schema_version_type(self):
        assert isinstance(MANIFEST_SCHEMA_VERSION, int)
        assert MANIFEST_SCHEMA_VERSION >= 1
